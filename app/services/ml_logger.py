"""
ML 학습용 매칭 로그 기록 서비스 (v5.0 신규)

매칭 시점에 추천된 강사 각각에 대해 MLTrainingLog 1행씩 생성하고,
이후 사용자 피드백 (select / feedback) 으로 라벨이 채워진다.
"""
from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.ml_training_log import MLTrainingLog
from app.services.feature_encoder import build_feature_snapshot


def log_match_candidates(
    request: EducationRequest,
    scored_items: list[dict],
    engine_version: str = 'rule_based_v4',
    commit: bool = True,
) -> list[MLTrainingLog]:
    """
    추천된 강사 후보 각각에 대해 MLTrainingLog 1행씩 생성.

    Parameters:
      request        : 교육 요청
      scored_items   : calculate_match_score() 결과 리스트
                      (각 item 은 'instructor', 'total_score', 'breakdown' 포함)
      engine_version : 사용 매칭 엔진 식별자 (A/B 비교용)
      commit         : DB 즉시 커밋 여부

    재호출 시 같은 request_id 의 기존 로그를 삭제하고 새로 작성 (재매칭 대응).
    """
    # 기존 로그 정리 (재매칭으로 추천 후보가 바뀐 경우)
    MLTrainingLog.query.filter_by(request_id=request.id).delete()

    created: list[MLTrainingLog] = []
    for item in scored_items:
        inst = item['instructor']
        snapshot = build_feature_snapshot(
            inst, request, item.get('breakdown', {}),
        )
        log = MLTrainingLog(
            request_id=request.id,
            instructor_id=inst.id,
            was_selected=False,
            was_conducted=False,
            final_satisfaction=None,
            match_score=item.get('total_score'),
            engine_version=engine_version,
            feature_snapshot=snapshot,
        )
        db.session.add(log)
        created.append(log)

    if commit:
        db.session.commit()
    return created


def mark_selection(
    request_id: int,
    selected_instructor_id: int,
    not_selected_reasons: dict[int, str] | None = None,
) -> dict:
    """
    수요처가 강사를 최종 선택했을 때 호출.
    - 선택된 강사의 로그에 was_selected=True
    - 선택되지 않은 강사들의 로그에 not_selected_reason 기록 (제공된 경우)

    반환:
      {'selected': MLTrainingLog | None, 'not_selected_count': int}
    """
    logs = MLTrainingLog.query.filter_by(request_id=request_id).all()
    selected_log = None
    not_selected_count = 0
    reasons = not_selected_reasons or {}

    for log in logs:
        if log.instructor_id == selected_instructor_id:
            log.was_selected = True
            selected_log = log
        else:
            log.was_selected = False
            not_selected_count += 1
            reason = reasons.get(log.instructor_id)
            if reason:
                log.not_selected_reason = reason

    db.session.commit()
    return {
        'selected': selected_log,
        'not_selected_count': not_selected_count,
    }


def record_feedback(
    request_id: int,
    instructor_id: int,
    satisfaction: float,
    was_conducted: bool = True,
) -> MLTrainingLog | None:
    """
    강의 완료 후 만족도 피드백 기록.
    매칭 로그 1행을 갱신 (선택된 강사의 행).
    """
    log = MLTrainingLog.query.filter_by(
        request_id=request_id, instructor_id=instructor_id,
    ).first()
    if not log:
        return None
    log.was_conducted = was_conducted
    log.final_satisfaction = satisfaction
    db.session.commit()
    return log


# ─────────────────────────────────────────────────────────────────────
# 검증 이슈 #6 수정: Match status 변경 시 ml_training_logs 자동 동기화
# ─────────────────────────────────────────────────────────────────────

# Match status → ml_logs 라벨 매핑 정책
#   '수락'      → was_selected=True
#   '최종확정'  → was_selected=True + was_conducted=True
#   '거절'      → was_selected=False + not_selected_reason 기록
#   '매칭제안'  → 변경 없음
MATCH_STATUS_TO_LABEL = {
    '수락':     {'was_selected': True,  'was_conducted': False},
    '최종확정': {'was_selected': True,  'was_conducted': True},
    '거절':     {'was_selected': False, 'was_conducted': False},
}


def sync_label_for_match(match, not_selected_reason: str | None = None) -> MLTrainingLog | None:
    """
    단일 매칭의 status 를 기반으로 해당 ml_training_logs 행을 자동 동기화.
    상태 전이 API(accept/reject/expire/select/feedback) 직후 호출하면 됨.
    """
    log = MLTrainingLog.query.filter_by(
        request_id=match.request_id, instructor_id=match.instructor_id,
    ).first()
    if not log:
        return None

    rule = MATCH_STATUS_TO_LABEL.get(match.status)
    if rule is None:
        return log  # '매칭제안' 등은 변경 없음

    log.was_selected = rule['was_selected']
    log.was_conducted = rule['was_conducted']
    if match.satisfaction_score is not None and log.final_satisfaction is None:
        log.final_satisfaction = match.satisfaction_score
    if not rule['was_selected'] and not_selected_reason and not log.not_selected_reason:
        log.not_selected_reason = not_selected_reason
    db.session.commit()
    return log


def backfill_labels_from_matches() -> dict:
    """
    기존 matches 테이블의 status 를 기반으로 ml_training_logs 일괄 백필.
    부분 라벨링된 기존 데이터를 한 번에 동기화할 때 사용.

    반환: {'updated': N, 'skipped_no_log': M}
    """
    # 순환 import 회피: 지연 import
    from app.models.match import Match

    updated = 0
    skipped = 0
    for m in Match.query.all():
        rule = MATCH_STATUS_TO_LABEL.get(m.status)
        if rule is None:
            continue
        log = MLTrainingLog.query.filter_by(
            request_id=m.request_id, instructor_id=m.instructor_id,
        ).first()
        if not log:
            skipped += 1
            continue
        # 이미 라벨이 더 명확히 설정된 경우는 덮어쓰지 않음
        changed = False
        if log.was_selected != rule['was_selected']:
            log.was_selected = rule['was_selected']
            changed = True
        if log.was_conducted != rule['was_conducted'] and rule['was_conducted']:
            log.was_conducted = True
            changed = True
        if m.satisfaction_score is not None and log.final_satisfaction is None:
            log.final_satisfaction = m.satisfaction_score
            changed = True
        if (not rule['was_selected']
                and not log.not_selected_reason
                and m.status == '거절'):
            log.not_selected_reason = '수요처 거절(백필)'
            changed = True
        if changed:
            updated += 1
    db.session.commit()
    return {'updated': updated, 'skipped_no_log': skipped}

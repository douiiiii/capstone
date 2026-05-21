"""
ML 학습용 매칭 로그 기록 서비스 (v5.0 신규)

매칭 시점에 추천된 강사 각각에 대해 MLTrainingLog 1행씩 생성하고,
이후 사용자 피드백 (select / feedback) 으로 라벨이 채워진다.
"""
from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
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

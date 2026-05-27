from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.match import Match
from app.routes._errors import (
    coerce_int,
    error_response,
    handle_api_errors,
    require_fields,
)
from app.services.class_session_service import (
    create_sessions_for_match,
    mark_match_sessions_completed,
    recalculate_total_classes,
)
from app.services.matching_engine import run_matching
from app.services.matching_service import find_top_matches
from app.services.ml_logger import (
    mark_selection,
    record_feedback,
    sync_label_for_match,
)

matches_bp = Blueprint('matches', __name__)


@matches_bp.route('/match', methods=['POST'])
@handle_api_errors  # 검증 이슈 #5 수정: 일관된 4xx/5xx 응답
def create_match():
    """
    교육 요청 기반 강사 매칭 실행 (상위 5명 반환)

    Request Body (JSON):
      { "request_id": 1 }

    재매칭 시 기존 결과를 삭제하고 새 결과로 교체.
    응답에 match_mode(정상/인접권역추천/유사분야확장/조건완화추천/최선추천)와
    score_detail(평점 보너스·활동일 패널티 포함 상세 점수)이 포함됩니다.
    """
    data = request.get_json(silent=True)
    err = require_fields(data, ['request_id'])
    if err:
        return err
    # 검증 이슈 #5 수정: int 캐스팅 가드 (잘못된 타입 → 400)
    request_id, err = coerce_int(data['request_id'], 'request_id')
    if err:
        return err

    edu_request = db.session.get(EducationRequest, request_id)
    if not edu_request:
        return error_response(
            f'교육 요청 ID {request_id}를 찾을 수 없습니다.',
            'NOT_FOUND', 404,
        )

    result = find_top_matches(request_id)

    if result is None:
        return error_response(
            '매칭 중 오류가 발생했습니다.', 'MATCHING_FAILED', 500,
        )

    matches = result['matches']
    match_mode = result['match_mode']
    match_mode_reason = result['match_mode_reason']

    # 조건 완화 추천 또는 최선 추천일 때 응답에 명시적으로 표시
    message = f'{match_mode} - 상위 {len(matches)}명의 강사가 매칭되었습니다.'
    if match_mode in ('조건완화추천', '최선추천'):
        message = f'[{match_mode}] {match_mode_reason} (총 {len(matches)}명)'

    return jsonify({
        'success': True,
        'request_id': request_id,
        'org_name': edu_request.organization.name if edu_request.organization else None,
        'specialty_needed': edu_request.specialty_needed,
        'match_mode': match_mode,
        'match_mode_reason': match_mode_reason,
        'message': message,
        'total_count': result['total_count'],
        'auto_excluded': result.get('auto_excluded', []),
        'data': matches,
    })


@matches_bp.route('/matches/<int:request_id>', methods=['GET'])
@handle_api_errors  # 검증 이슈 #5 수정
def get_matches(request_id: int):
    """특정 교육 요청의 매칭 결과 조회"""
    edu_request = db.session.get(EducationRequest, request_id)
    if not edu_request:
        return error_response(
            f'교육 요청 ID {request_id}를 찾을 수 없습니다.',
            'NOT_FOUND', 404,
        )

    matches = (
        Match.query
        .filter_by(request_id=request_id)
        .order_by(Match.match_score.desc())
        .all()
    )

    return jsonify({
        'success': True,
        'request_id': request_id,
        'request_info': edu_request.to_dict(),
        'count': len(matches),
        'data': [m.to_dict() for m in matches],
    })


# ─────────────────────────────────────────────────────────────────────
# v5.0: ML 학습 데이터 수집용 피드백 엔드포인트
# ─────────────────────────────────────────────────────────────────────

@matches_bp.route('/match/select', methods=['POST'])
@handle_api_errors  # 검증 이슈 #5 수정
def select_match():
    """
    수요처가 최종 강사 선택 시 호출.
    매칭 로그(MLTrainingLog) 에 was_selected 표시 + 선택 안 된 강사들의 사유 저장.

    Request Body (JSON):
      {
        "request_id": 1,
        "instructor_id": 7,
        "not_selected_reasons": {  # 선택사항. 키는 강사 id (문자열도 허용)
          "3": "거리가 멀어서",
          "5": "시간대 안 맞음"
        }
      }
    """
    data = request.get_json(silent=True) or {}
    err = require_fields(data, ['request_id', 'instructor_id'])
    if err:
        return err
    request_id, err = coerce_int(data['request_id'], 'request_id')
    if err:
        return err
    instructor_id, err = coerce_int(data['instructor_id'], 'instructor_id')
    if err:
        return err

    # 키가 문자열이면 int 변환 (JSON 객체 키는 문자열로 직렬화되는 경우가 많음)
    raw_reasons = data.get('not_selected_reasons') or {}
    reasons = {int(k): v for k, v in raw_reasons.items()}

    # Match 테이블에도 선택 상태 반영 (status='최종확정')
    # DB CHECK 제약: matches.status ∈ {'매칭제안','수락','거절','최종확정'}
    selected_match = Match.query.filter_by(
        request_id=request_id, instructor_id=instructor_id,
    ).first()
    if selected_match:
        selected_match.status = '최종확정'
        # 다른 매칭은 거절로 표시
        rejected_matches = Match.query.filter(
            Match.request_id == request_id,
            Match.id != selected_match.id,
        ).all()
        for rm in rejected_matches:
            rm.status = '거절'
        db.session.commit()
        # 검증 이슈 #6 수정: status 변경에 따라 ml_logs 동기화
        sync_label_for_match(selected_match)
        for rm in rejected_matches:
            sync_label_for_match(rm, not_selected_reason='다른 강사 선택됨')
        # v5.1: 확정 매칭에 대한 강의 세션 자동 생성
        #   - 1회성 강의 → 1개
        #   - 정기 강의   → frequency/기간에 맞춰 N개
        create_sessions_for_match(selected_match)

    result = mark_selection(request_id, instructor_id, reasons)
    return jsonify({
        'success': True,
        'request_id': request_id,
        'instructor_id': instructor_id,
        'not_selected_count': result['not_selected_count'],
        'message': '선택이 기록되었습니다.',
    })


@matches_bp.route('/match/feedback', methods=['POST'])
@handle_api_errors  # 검증 이슈 #5 수정
def submit_feedback():
    """
    강의 완료 후 만족도 평가 제출.
    MLTrainingLog 의 was_conducted/final_satisfaction 갱신 + Match 테이블에도 반영.

    Request Body (JSON):
      {
        "request_id": 1,
        "instructor_id": 7,
        "satisfaction_score": 4.5,
        "was_conducted": true  # 선택사항 기본 true
      }
    """
    data = request.get_json(silent=True) or {}
    err = require_fields(data, ['request_id', 'instructor_id', 'satisfaction_score'])
    if err:
        return err
    request_id, err = coerce_int(data['request_id'], 'request_id')
    if err:
        return err
    instructor_id, err = coerce_int(data['instructor_id'], 'instructor_id')
    if err:
        return err
    try:
        satisfaction = float(data['satisfaction_score'])
    except (TypeError, ValueError):
        return error_response(
            'satisfaction_score 는 0.0 ~ 5.0 범위 숫자여야 합니다.',
            'INVALID_TYPE', 400,
        )
    if not (0.0 <= satisfaction <= 5.0):
        return error_response(
            'satisfaction_score 는 0.0 ~ 5.0 범위여야 합니다.',
            'OUT_OF_RANGE', 400,
        )

    was_conducted = bool(data.get('was_conducted', True))

    # Match 테이블에도 만족도 반영 (학습 데이터 일관성)
    selected_match = Match.query.filter_by(
        request_id=request_id, instructor_id=instructor_id,
    ).first()
    if selected_match:
        selected_match.satisfaction_score = satisfaction
        if was_conducted:
            # DB CHECK 제약상 '완료' 불가 → '최종확정' 으로 통일
            selected_match.status = '최종확정'
        db.session.commit()
        # v5.1: 강의 완료 시 세션도 '완료' 로 갱신하고 누적 강의 횟수 재계산
        if was_conducted and selected_match.request is not None:
            # 확정 절차 없이 곧바로 피드백이 들어오는 경우를 대비해 세션을 보강 생성
            create_sessions_for_match(selected_match, commit=False)
            mark_match_sessions_completed(selected_match, commit=False)
            if selected_match.instructor:
                recalculate_total_classes(selected_match.instructor, commit=False)
            db.session.commit()

    log = record_feedback(request_id, instructor_id, satisfaction, was_conducted)
    if not log:
        return error_response(
            '해당 매칭 로그를 찾을 수 없습니다.', 'NOT_FOUND', 404,
        )

    return jsonify({
        'success': True,
        'request_id': request_id,
        'instructor_id': instructor_id,
        'satisfaction_score': satisfaction,
        'was_conducted': was_conducted,
        'message': '피드백이 기록되었습니다.',
    })


# ─────────────────────────────────────────────────────────────────────
# 검증 이슈 #2 수정: 매칭 status 흐름 보완
#   - POST /api/match/<id>/accept : 매칭제안 → 수락
#   - POST /api/match/<id>/reject : 매칭제안 → 거절 + 다음 후보 자동 추천
#   - POST /api/matches/expire-stale : 30일 이상 매칭제안 자동 만료
# ─────────────────────────────────────────────────────────────────────

from datetime import datetime, timedelta  # noqa: E402

# 매칭제안 자동 만료 기준일 (30일)
MATCH_PROPOSAL_TTL_DAYS = 30


@matches_bp.route('/match/<int:match_id>/accept', methods=['POST'])
@handle_api_errors
def accept_match(match_id: int):
    """
    수요처가 강사를 수락하는 API. 매칭제안 → 수락 전이.

    Request Body (JSON, 선택):
      { "note": "메모" }
    """
    m = db.session.get(Match, match_id)
    if not m:
        return error_response(
            f'매칭 ID {match_id} 를 찾을 수 없습니다.', 'NOT_FOUND', 404,
        )
    if m.status not in ('매칭제안',):
        return error_response(
            f'현재 status={m.status} → 수락 불가 (매칭제안 상태에서만 가능)',
            'INVALID_TRANSITION', 409,
        )
    m.status = '수락'
    db.session.commit()
    # ML 로그 동기화 — 검증 이슈 #6 의 자동 라벨링과 같은 정책
    from app.services.ml_logger import sync_label_for_match
    sync_label_for_match(m)
    return jsonify({
        'success': True,
        'match_id': m.id,
        'status': m.status,
        'message': '매칭이 수락되었습니다.',
    })


@matches_bp.route('/match/<int:match_id>/reject', methods=['POST'])
@handle_api_errors
def reject_match(match_id: int):
    """
    수요처가 강사를 거절하는 API. 매칭제안 → 거절 전이 + 다음 후보 자동 추천.

    Request Body (JSON, 선택):
      { "reason": "거리 멀어서" }

    응답에 다음 후보 강사 1명을 추가로 반환 (가능한 경우).
    """
    m = db.session.get(Match, match_id)
    if not m:
        return error_response(
            f'매칭 ID {match_id} 를 찾을 수 없습니다.', 'NOT_FOUND', 404,
        )
    if m.status not in ('매칭제안', '수락'):
        return error_response(
            f'현재 status={m.status} → 거절 불가',
            'INVALID_TRANSITION', 409,
        )

    data = request.get_json(silent=True) or {}
    reason = data.get('reason', '수요처 거절')
    m.status = '거절'
    db.session.commit()

    # ML 로그 동기화
    from app.services.ml_logger import sync_label_for_match
    sync_label_for_match(m, not_selected_reason=reason)

    # 다음 후보 추천: 현 요청의 매칭제안 중 점수가 가장 높은 1명을 노출
    next_candidate = (
        Match.query.filter_by(request_id=m.request_id, status='매칭제안')
        .order_by(Match.match_score.desc())
        .first()
    )
    next_info = next_candidate.to_dict() if next_candidate else None

    return jsonify({
        'success': True,
        'match_id': m.id,
        'status': m.status,
        'reason': reason,
        'next_candidate': next_info,
        'message': '매칭이 거절되었습니다.',
    })


@matches_bp.route('/matches/expire-stale', methods=['POST'])
@handle_api_errors
def expire_stale_matches():
    """
    매칭제안 상태로 MATCH_PROPOSAL_TTL_DAYS(30일) 이상 방치된 매칭을 자동 만료.

    응답: 만료된 매칭 수.
    """
    cutoff = datetime.utcnow() - timedelta(days=MATCH_PROPOSAL_TTL_DAYS)
    stale = Match.query.filter(
        Match.status == '매칭제안',
        Match.created_at < cutoff,
    ).all()
    for m in stale:
        m.status = '거절'  # DB CHECK 제약: 매칭제안/수락/거절/최종확정 만 허용
    db.session.commit()
    # ML 로그 동기화
    from app.services.ml_logger import sync_label_for_match
    for m in stale:
        sync_label_for_match(m, not_selected_reason='30일 무응답 자동 만료')
    return jsonify({
        'success': True,
        'expired_count': len(stale),
        'cutoff_date': cutoff.isoformat(),
        'message': f'{len(stale)}건의 매칭제안이 자동 만료되었습니다.',
    })

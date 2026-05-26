from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.match import Match
from app.services.class_session_service import (
    create_sessions_for_match,
    mark_match_sessions_completed,
    recalculate_total_classes,
)
from app.services.matching_engine import run_matching
from app.services.matching_service import find_top_matches
from app.services.ml_logger import mark_selection, record_feedback

matches_bp = Blueprint('matches', __name__)


@matches_bp.route('/match', methods=['POST'])
def create_match():
    """
    교육 요청 기반 강사 매칭 실행 (상위 5명 반환)

    Request Body (JSON):
      { "request_id": 1 }

    재매칭 시 기존 결과를 삭제하고 새 결과로 교체.
    응답에 match_mode(정상/인접권역추천/유사분야확장/조건완화추천/최선추천)와
    score_detail(평점 보너스·활동일 패널티 포함 상세 점수)이 포함됩니다.
    """
    data = request.get_json()

    if not data or 'request_id' not in data:
        return jsonify({
            'success': False,
            'message': 'request_id 필드가 필요합니다.',
        }), 400

    request_id = data['request_id']

    edu_request = db.session.get(EducationRequest, request_id)
    if not edu_request:
        return jsonify({
            'success': False,
            'message': f'교육 요청 ID {request_id}를 찾을 수 없습니다.',
        }), 404

    result = find_top_matches(request_id)

    if result is None:
        return jsonify({
            'success': False,
            'message': '매칭 중 오류가 발생했습니다.',
        }), 500

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
def get_matches(request_id: int):
    """특정 교육 요청의 매칭 결과 조회"""
    edu_request = db.session.get(EducationRequest, request_id)
    if not edu_request:
        return jsonify({
            'success': False,
            'message': f'교육 요청 ID {request_id}를 찾을 수 없습니다.',
        }), 404

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
    data = request.get_json() or {}
    request_id = data.get('request_id')
    instructor_id = data.get('instructor_id')
    if not request_id or not instructor_id:
        return jsonify({
            'success': False,
            'message': 'request_id 와 instructor_id 가 필요합니다.',
        }), 400

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
        Match.query.filter(
            Match.request_id == request_id,
            Match.id != selected_match.id,
        ).update({'status': '거절'})
        db.session.commit()
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
    data = request.get_json() or {}
    request_id = data.get('request_id')
    instructor_id = data.get('instructor_id')
    satisfaction = data.get('satisfaction_score')

    if not request_id or not instructor_id or satisfaction is None:
        return jsonify({
            'success': False,
            'message': 'request_id, instructor_id, satisfaction_score 가 필요합니다.',
        }), 400
    if not (0.0 <= satisfaction <= 5.0):
        return jsonify({
            'success': False,
            'message': 'satisfaction_score 는 0.0 ~ 5.0 범위여야 합니다.',
        }), 400

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
        return jsonify({
            'success': False,
            'message': '해당 매칭 로그를 찾을 수 없습니다.',
        }), 404

    return jsonify({
        'success': True,
        'request_id': request_id,
        'instructor_id': instructor_id,
        'satisfaction_score': satisfaction,
        'was_conducted': was_conducted,
        'message': '피드백이 기록되었습니다.',
    })

from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.match import Match
from app.services.matching_service import find_top_matches

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

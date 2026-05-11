from flask import Blueprint, jsonify, request

from app.models.instructor import Instructor

instructors_bp = Blueprint('instructors', __name__)


@instructors_bp.route('/instructors', methods=['GET'])
def get_instructors():
    """
    전체 강사 목록 조회
    쿼리 파라미터:
      - region    : 권역 필터 (예: 동부권)
      - specialty : 전문 분야 필터 (예: AI기초)
      - is_active : 활동 여부 (기본값: true)
    """
    region = request.args.get('region')
    specialty = request.args.get('specialty')
    is_active_param = request.args.get('is_active', 'true').lower()
    is_active = is_active_param != 'false'

    query = Instructor.query.filter_by(is_active=is_active)

    if region:
        query = query.filter_by(region=region)

    instructors = query.order_by(Instructor.avg_rating.desc()).all()

    # 전문 분야 필터 (JSON 배열은 Python 레벨에서 처리)
    if specialty:
        instructors = [i for i in instructors if specialty in (i.specialties or [])]

    return jsonify({
        'success': True,
        'count': len(instructors),
        'data': [i.to_dict() for i in instructors],
    })

from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models.instructor import Instructor
from app.services.matching_service import (
    MAX_CLASSES_MONTH_MAX,
    MAX_CLASSES_MONTH_MIN,
)

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


@instructors_bp.route(
    '/instructors/<int:instructor_id>/max-classes', methods=['PATCH'],
)
def update_max_classes(instructor_id: int):
    """
    강사의 월 최대 강의 횟수 수정 (v5.1 신규)

    Request Body (JSON):
      { "max_classes_month": 25 }

    유효 범위: MAX_CLASSES_MONTH_MIN(10) ~ MAX_CLASSES_MONTH_MAX(40)
    """
    inst = db.session.get(Instructor, instructor_id)
    if not inst:
        return jsonify({
            'success': False,
            'message': f'강사 ID {instructor_id} 를 찾을 수 없습니다.',
        }), 404

    data = request.get_json() or {}
    value = data.get('max_classes_month')
    if value is None:
        return jsonify({
            'success': False,
            'message': 'max_classes_month 가 필요합니다.',
        }), 400

    try:
        value = int(value)
    except (TypeError, ValueError):
        return jsonify({
            'success': False,
            'message': 'max_classes_month 는 정수여야 합니다.',
        }), 400

    if not (MAX_CLASSES_MONTH_MIN <= value <= MAX_CLASSES_MONTH_MAX):
        return jsonify({
            'success': False,
            'message': (
                f'max_classes_month 는 {MAX_CLASSES_MONTH_MIN}~{MAX_CLASSES_MONTH_MAX}'
                ' 범위여야 합니다.'
            ),
        }), 400

    inst.max_classes_month = value
    db.session.commit()
    return jsonify({
        'success': True,
        'instructor_id': inst.id,
        'max_classes_month': inst.max_classes_month,
    })

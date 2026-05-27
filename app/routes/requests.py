from flask import Blueprint, jsonify, request
from sqlalchemy.orm import joinedload

from app.models.education_request import EducationRequest
from app.routes._errors import handle_api_errors

requests_bp = Blueprint('requests', __name__)


@requests_bp.route('/requests', methods=['GET'])
@handle_api_errors  # 검증 이슈 #5 수정
def get_requests():
    """
    전체 교육 요청 목록 조회
    쿼리 파라미터:
      - status : 요청 상태 필터 (대기중 / 매칭완료 / 진행중 / 완료)
    """
    status = request.args.get('status')

    # N+1 회피: to_dict() 가 organization.name/region 을 참조하므로 eager load
    query = EducationRequest.query.options(
        joinedload(EducationRequest.organization)
    )
    if status:
        query = query.filter_by(status=status)

    edu_requests = query.order_by(EducationRequest.id.desc()).all()

    return jsonify({
        'success': True,
        'count': len(edu_requests),
        'data': [r.to_dict() for r in edu_requests],
    })

"""
ML 준비 데이터 관련 API (v5.0 신규)

엔드포인트:
  GET /api/ml/features/<request_id>  : 특정 요청의 정규화된 피처 벡터
  GET /api/ml/data-quality           : 데이터 품질 리포트
  GET /api/ml/status                 : 학습 가능 데이터 현황 / 목표 달성도
"""
from flask import Blueprint, jsonify

from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
from app.routes._errors import error_response, handle_api_errors
from app.services.data_quality import build_ml_status, build_quality_report
from app.services.feature_encoder import (
    encode_instructor,
    encode_request,
)

ml_bp = Blueprint('ml', __name__)


@ml_bp.route('/ml/features/<int:request_id>', methods=['GET'])
@handle_api_errors  # 검증 이슈 #5 수정
def get_features(request_id: int):
    """
    특정 교육 요청에 대한 정규화된 피처.

    응답 구조:
      {
        "success": true,
        "request_id": 1,
        "request_features": {...},
        "instructor_features": [{...}, ...]   # 현재 활동 강사 전체
      }
    """
    request = db.session.get(EducationRequest, request_id)
    if not request:
        return error_response(
            f'요청 ID {request_id} 를 찾을 수 없습니다.',
            'NOT_FOUND', 404,
        )

    instructors = Instructor.query.filter_by(is_active=True).all()

    return jsonify({
        'success': True,
        'request_id': request_id,
        'request_features': encode_request(request),
        'instructor_features': [encode_instructor(i) for i in instructors],
    })


@ml_bp.route('/ml/data-quality', methods=['GET'])
@handle_api_errors  # 검증 이슈 #5 수정
def get_data_quality():
    """결측값/이상값 현황 + 품질 점수"""
    return jsonify({
        'success': True,
        'data': build_quality_report(),
    })


@ml_bp.route('/ml/status', methods=['GET'])
@handle_api_errors  # 검증 이슈 #5 수정
def get_ml_status():
    """학습 데이터 진척도 + 목표(500건) 달성 여부"""
    return jsonify({
        'success': True,
        'data': build_ml_status(),
    })

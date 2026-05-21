"""
화성시 AI 시민리더 허브 - 관리자 전용 API (v4.0 신규)

엔드포인트:
  GET  /api/admin/instructors    : 등급 정보 포함 전체 강사 조회
  GET  /api/admin/growth         : 승급 대상(80% 이상 또는 충족) 강사 목록
  GET  /api/admin/grade-history  : 등급 변경 이력
  POST /api/admin/grade-upgrade  : 승급 가능 강사 일괄 자동 업그레이드

인증:
  - 환경변수 ADMIN_TOKEN 과 일치하는 X-Admin-Token 헤더가 필요
  - 누락/불일치 시 401 응답
  - ADMIN_TOKEN 미설정 시 모든 요청 차단 (운영 보안)
"""
import os
from functools import wraps

from flask import Blueprint, jsonify, request

from app.models.grade_history import GradeHistory
from app.models.instructor import Instructor
from app.services.grade_service import (
    bulk_upgrade_all,
    check_eligibility,
    list_growth_candidates,
)

admin_bp = Blueprint('admin', __name__)


# ─────────────────────────────────────────────────────────────────────
# 토큰 인증 데코레이터
# ─────────────────────────────────────────────────────────────────────

def require_admin_token(view_func):
    """
    X-Admin-Token 헤더가 환경변수 ADMIN_TOKEN 과 일치할 때만 통과.

    - 환경변수 미설정: 503 (서버 설정 문제)
    - 헤더 누락 / 불일치: 401
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        expected = os.environ.get('ADMIN_TOKEN')
        if not expected:
            return jsonify({
                'success': False,
                'message': 'ADMIN_TOKEN 환경변수가 설정되지 않아 관리자 API가 비활성화되어 있습니다.',
            }), 503
        provided = request.headers.get('X-Admin-Token')
        if not provided or provided != expected:
            return jsonify({
                'success': False,
                'message': '관리자 인증 실패 (X-Admin-Token 헤더 확인 필요)',
            }), 401
        return view_func(*args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────────────────────────────
# 1) 전체 강사 조회 (등급 정보 포함)
# ─────────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/instructors', methods=['GET'])
@require_admin_token
def admin_list_instructors():
    """등급 정보 포함 전체 강사 목록 (활동/비활동 모두)"""
    instructors = Instructor.query.order_by(Instructor.id.asc()).all()
    return jsonify({
        'success': True,
        'count': len(instructors),
        'data': [i.to_dict(include_grade_info=True) for i in instructors],
    })


# ─────────────────────────────────────────────────────────────────────
# 2) 승급 대상 강사 목록
# ─────────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/growth', methods=['GET'])
@require_admin_token
def admin_growth_candidates():
    """승급 80% 이상 / 100% 충족한 강사 목록"""
    data = list_growth_candidates()
    return jsonify({
        'success': True,
        'count': len(data),
        'data': data,
    })


# ─────────────────────────────────────────────────────────────────────
# 3) 등급 변경 이력
# ─────────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/grade-history', methods=['GET'])
@require_admin_token
def admin_grade_history():
    """등급 변경 이력 (최신순)"""
    rows = GradeHistory.query.order_by(GradeHistory.changed_at.desc()).all()
    return jsonify({
        'success': True,
        'count': len(rows),
        'data': [r.to_dict() for r in rows],
    })


# ─────────────────────────────────────────────────────────────────────
# 4) 일괄 자동 업그레이드
# ─────────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/grade-upgrade', methods=['POST'])
@require_admin_token
def admin_bulk_upgrade():
    """
    승급 조건을 충족한 모든 활동 강사를 자동으로 한 단계 승급.
    응답에는 이번 호출에서 승급된 강사 목록과 이력이 포함됨.
    """
    upgraded = bulk_upgrade_all()
    return jsonify({
        'success': True,
        'upgraded_count': len(upgraded),
        'data': [h.to_dict() for h in upgraded],
    })

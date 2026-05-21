"""
강사 등급 자동 업그레이드 서비스 (v4.0 신규)

승급 기준:
  기초  → 중급   : 강의 10회 이상 + 평점 4.0 이상
  중급  → 전문가 : 강의 30회 이상 + 평점 4.5 이상

핵심 함수:
  - check_eligibility(instructor)
      다음 등급 승급 가능 여부와 진척률 반환 (mutation 없음)
  - upgrade_instructor(instructor)
      eligibility 충족 시 cert_level 갱신 + GradeHistory 1건 생성
  - bulk_upgrade_all()
      관리자 API 에서 호출. 활동 강사 전체에 대해 upgrade_instructor 실행
"""
from datetime import datetime

from app.extensions import db
from app.models.grade_history import GradeHistory
from app.models.instructor import Instructor
from app.services.matching_service import (
    GRADE_UPGRADE_RULES,
    GROWTH_PROGRESS_THRESHOLD,
    _calc_grade_progress,
)


def check_eligibility(instructor: Instructor) -> dict:
    """
    승급 가능 여부 및 진척률 계산.

    반환:
      {
        'instructor_id': int,
        'current_grade': str | None,
        'next_grade': str | None,
        'progress': 0.0~1.0,        # 다음 등급 충족도
        'is_eligible': bool,        # 100% 도달
        'is_growing': bool,         # 80% 이상 (성장 중)
        'requirement': {'min_classes': int, 'min_rating': float} | None,
        'current_stats': {'total_classes': int, 'avg_rating': float},
      }
    """
    progress, rule = _calc_grade_progress(instructor)
    return {
        'instructor_id': instructor.id,
        'instructor_name': instructor.name,
        'current_grade': instructor.cert_level,
        'next_grade': rule['next'] if rule else None,
        'progress': round(progress, 3),
        'is_eligible': progress >= 1.0 and rule is not None,
        'is_growing': (
            progress >= GROWTH_PROGRESS_THRESHOLD and progress < 1.0 and rule is not None
        ),
        'requirement': (
            {'min_classes': rule['min_classes'], 'min_rating': rule['min_rating']}
            if rule else None
        ),
        'current_stats': {
            'total_classes': instructor.total_classes or 0,
            'avg_rating': instructor.avg_rating or 0.0,
        },
    }


def upgrade_instructor(instructor: Instructor, commit: bool = True) -> GradeHistory | None:
    """
    승급 조건 충족 시 등급을 한 단계 올리고 GradeHistory 1건 생성.

    한 번에 한 단계만 승급함. (예: 기초 → 중급 만, 그 다음 호출에서 중급 → 전문가)
    충족하지 않으면 None 반환.
    commit=False 일 경우 호출자가 일괄 commit.
    """
    rule = GRADE_UPGRADE_RULES.get(instructor.cert_level or '')
    if not rule:
        return None

    classes = instructor.total_classes or 0
    rating = instructor.avg_rating or 0.0
    if classes < rule['min_classes'] or rating < rule['min_rating']:
        return None

    from_grade = instructor.cert_level
    to_grade = rule['next']
    instructor.cert_level = to_grade
    instructor.cert_level_updated_at = datetime.utcnow()

    history = GradeHistory(
        instructor_id=instructor.id,
        from_grade=from_grade,
        to_grade=to_grade,
        reason=(
            f'강의 {classes}회 (≥{rule["min_classes"]}) '
            f'+ 평점 {rating:.2f} (≥{rule["min_rating"]})'
        ),
        changed_at=datetime.utcnow(),
    )
    db.session.add(history)
    if commit:
        db.session.commit()
    return history


def bulk_upgrade_all() -> list[GradeHistory]:
    """
    활동 중인 모든 강사에 대해 승급 가능 여부를 확인하고, 가능한 강사를 일괄 승급.

    반환: 이번 호출에서 생성된 GradeHistory 리스트
    (한 강사가 두 단계 승급 가능해도 한 단계만 승급 — 다음 호출에서 추가 승급)
    """
    upgraded: list[GradeHistory] = []
    instructors = Instructor.query.filter_by(is_active=True).all()
    for inst in instructors:
        history = upgrade_instructor(inst, commit=False)
        if history:
            upgraded.append(history)
    if upgraded:
        db.session.commit()
    return upgraded


def list_growth_candidates() -> list[dict]:
    """
    승급 80% 달성한 (또는 이미 100% 충족) 강사 목록 반환.
    관리자 GET /api/admin/growth 응답에 사용.
    """
    candidates: list[dict] = []
    instructors = Instructor.query.filter_by(is_active=True).all()
    for inst in instructors:
        info = check_eligibility(inst)
        if info['is_eligible'] or info['is_growing']:
            candidates.append(info)
    # 진척률 높은 순
    candidates.sort(key=lambda x: -x['progress'])
    return candidates

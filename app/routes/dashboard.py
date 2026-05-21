"""
화성시 AI 시민리더 허브 - 대시보드 API

프론트엔드 팀과 협의 후 수정하기 쉽도록 최대한 단순한 구조로 작성.
각 엔드포인트는 독립적으로 동작하며, 다른 API에 의존하지 않습니다.

엔드포인트:
  GET /api/dashboard/summary   : 전체 요약 지표
  GET /api/dashboard/region    : 권역별 통계
  GET /api/dashboard/specialty : 전문분야별 통계
"""
from collections import Counter

from flask import Blueprint, jsonify

from app.models.education_request import EducationRequest
from app.models.instructor import Instructor

dashboard_bp = Blueprint('dashboard', __name__)


# ─────────────────────────────────────────────────────────────────────
# 매칭 완료로 간주할 EducationRequest.status 값
# 정책 변경 시 이 상수만 수정하면 됨 (예: '확정', '완료' 만 포함 등)
# ─────────────────────────────────────────────────────────────────────
MATCHED_STATUSES = ('매칭완료', '진행중', '완료')


@dashboard_bp.route('/dashboard/summary', methods=['GET'])
def get_summary():
    """
    전체 요약 지표

    응답 예시:
      {
        "success": true,
        "data": {
          "total_instructors": 13,
          "active_instructors": 12,
          "total_requests": 25,
          "matched_requests": 18,
          "match_rate": 72.0   // 단위: %
        }
      }
    """
    # 강사 통계
    total_instructors = Instructor.query.count()
    active_instructors = Instructor.query.filter_by(is_active=True).count()

    # 교육 요청 통계
    total_requests = EducationRequest.query.count()
    matched_requests = EducationRequest.query.filter(
        EducationRequest.status.in_(MATCHED_STATUSES)
    ).count()

    # 매칭 성공률 (소수점 1자리, 분모 0 보호)
    match_rate = (
        round(matched_requests / total_requests * 100, 1)
        if total_requests > 0
        else 0.0
    )

    return jsonify({
        'success': True,
        'data': {
            # 항목 추가 시 여기에 키만 더 넣으면 됨
            'total_instructors': total_instructors,
            'active_instructors': active_instructors,
            'total_requests': total_requests,
            'matched_requests': matched_requests,
            'match_rate': match_rate,
        },
    })


@dashboard_bp.route('/dashboard/region', methods=['GET'])
def get_region_stats():
    """
    권역별 강사 수 / 교육 요청 수

    - 강사 수는 활동 중인 강사(is_active=True)만 집계
    - 권역이 없는 데이터는 '미지정'으로 묶음
    - 응답은 권역명 사전 순으로 정렬

    응답 예시:
      {
        "success": true,
        "data": [
          {"region": "남부권", "instructor_count": 2, "request_count": 3},
          {"region": "동부권", "instructor_count": 4, "request_count": 7},
          ...
        ]
      }
    """
    # 권역별 강사 수 (활동 중인 강사만)
    instructor_rows = Instructor.query.filter_by(is_active=True).all()
    instructor_counts = Counter(
        (i.region or '미지정') for i in instructor_rows
    )

    # 권역별 교육 요청 수 (organization.region 기준)
    request_rows = EducationRequest.query.all()
    request_counts = Counter(
        ((r.organization.region if r.organization else None) or '미지정')
        for r in request_rows
    )

    # 두 Counter의 키 합집합으로 권역 목록 구성
    regions = sorted(set(instructor_counts) | set(request_counts))

    data = [
        {
            'region': region,
            'instructor_count': instructor_counts.get(region, 0),
            'request_count': request_counts.get(region, 0),
        }
        for region in regions
    ]

    return jsonify({
        'success': True,
        'count': len(data),
        'data': data,
    })


@dashboard_bp.route('/dashboard/specialty', methods=['GET'])
def get_specialty_stats():
    """
    전문분야별 강사 수 / 인기 전문분야 Top5

    - 강사의 specialties는 JSON 배열이므로 1명이 N개 분야에 카운트될 수 있음
    - 인기 전문분야는 EducationRequest.specialty_needed 기준
    - top 개수는 TOP_N 상수로 조정 가능 (현재 5)

    응답 예시:
      {
        "success": true,
        "data": {
          "instructor_by_specialty": [
            {"specialty": "AI기초", "count": 5},
            ...
          ],
          "top_requested": [
            {"specialty": "AI기초", "count": 8},
            ...
          ]
        }
      }
    """
    TOP_N = 5  # 인기 전문분야 노출 개수 (조정 필요 시 이 값만 변경)

    # 강사별 전문분야 카운트 (활동 중인 강사만, JSON 배열을 풀어서 집계)
    instructor_rows = Instructor.query.filter_by(is_active=True).all()
    specialty_counter = Counter()
    for inst in instructor_rows:
        for spec in (inst.specialties or []):
            specialty_counter[spec] += 1

    instructor_by_specialty = [
        {'specialty': spec, 'count': cnt}
        for spec, cnt in specialty_counter.most_common()
    ]

    # 교육 요청에서 인기 전문분야 Top N
    request_rows = EducationRequest.query.all()
    request_counter = Counter(
        r.specialty_needed for r in request_rows if r.specialty_needed
    )
    top_requested = [
        {'specialty': spec, 'count': cnt}
        for spec, cnt in request_counter.most_common(TOP_N)
    ]

    return jsonify({
        'success': True,
        'data': {
            'instructor_by_specialty': instructor_by_specialty,
            'top_requested': top_requested,
        },
    })

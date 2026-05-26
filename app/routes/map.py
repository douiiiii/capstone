"""
화성시 AI 시민리더 허브 - 지도 히트맵 시각화 API

프론트엔드 팀과 협의 후 수정하기 쉽도록 최대한 단순한 구조로 작성.
Kakao Map / Google Map / Leaflet 등 어떤 지도 라이브러리든 쓸 수 있게
범용 lat/lng 키 기반의 평탄한 JSON 응답을 사용합니다.

엔드포인트:
  GET /api/map/regions     : 권역별 중심 좌표 + 강사/요청/매칭 수
  GET /api/map/heatmap     : 히트맵용 (lat, lng, intensity)
  GET /api/map/instructors : 강사별 위치 (소속 권역 중심 좌표 기준)
"""
from flask import Blueprint, jsonify

from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
from app.models.match import Match

map_bp = Blueprint('map', __name__)


# ─────────────────────────────────────────────────────────────────────
# 화성시 권역별 중심 좌표 (수정이 필요할 경우 이 상수만 변경)
# 키: 권역명 / 값: { lat, lng, areas(설명용 세부 지역 라벨) }
# ─────────────────────────────────────────────────────────────────────
REGION_COORDINATES = {
    '동부권': {'lat': 37.20, 'lng': 127.07, 'areas': ['동탄1', '동탄2']},
    '서부권': {'lat': 37.07, 'lng': 126.82, 'areas': ['향남', '팔탄']},
    '북부권': {'lat': 37.22, 'lng': 126.92, 'areas': ['봉담', '기안']},
    '남부권': {'lat': 37.00, 'lng': 126.83, 'areas': ['우정', '장안']},
    '중부권': {'lat': 37.20, 'lng': 126.83, 'areas': ['화성시청']},
}

# 매칭 완료로 간주할 Match.status 값 (정책 변경 시 이 값만 수정)
# DB CHECK 제약: matches.status ∈ {'매칭제안','수락','거절','최종확정'}
# 기존 '확정'/'완료' 를 '최종확정' 으로 통일.
MATCHED_MATCH_STATUSES = ('최종확정',)


def _empty_region_counter():
    """모든 권역 키를 0으로 초기화한 dict 반환 (응답 누락 방지)"""
    return {region: 0 for region in REGION_COORDINATES}


@map_bp.route('/map/regions', methods=['GET'])
def get_map_regions():
    """
    권역별 중심 좌표 + 강사 수 / 교육 요청 수 / 매칭 완료 수

    - 강사 수는 활동 중(is_active=True) 강사만 집계
    - 매칭 완료 수는 Match.status ∈ MATCHED_MATCH_STATUSES 기준

    응답 예시:
      {
        "success": true,
        "count": 5,
        "data": [
          {
            "region": "동부권",
            "lat": 37.20, "lng": 127.07,
            "areas": ["동탄1", "동탄2"],
            "instructor_count": 4,
            "request_count": 7,
            "matched_count": 3
          },
          ...
        ]
      }
    """
    # 강사 수 (활동 강사만, 소속 region 기준)
    instructor_counts = _empty_region_counter()
    for inst in Instructor.query.filter_by(is_active=True).all():
        if inst.region in instructor_counts:
            instructor_counts[inst.region] += 1

    # 교육 요청 수 (요청을 낸 기관의 region 기준)
    request_counts = _empty_region_counter()
    matched_counts = _empty_region_counter()
    for req in EducationRequest.query.all():
        org_region = req.organization.region if req.organization else None
        if org_region in request_counts:
            request_counts[org_region] += 1
            # 해당 요청에 매칭 완료된 강사가 1명 이상이면 matched 1 카운트
            has_matched = any(
                m.status in MATCHED_MATCH_STATUSES for m in (req.matches or [])
            )
            if has_matched:
                matched_counts[org_region] += 1

    data = [
        {
            'region': region,
            'lat': coord['lat'],
            'lng': coord['lng'],
            'areas': coord['areas'],
            'instructor_count': instructor_counts[region],
            'request_count': request_counts[region],
            'matched_count': matched_counts[region],
        }
        for region, coord in REGION_COORDINATES.items()
    ]

    return jsonify({
        'success': True,
        'count': len(data),
        'data': data,
    })


@map_bp.route('/map/heatmap', methods=['GET'])
def get_map_heatmap():
    """
    히트맵용 데이터 (lat, lng, intensity)

    - intensity = 해당 권역의 교육 요청 수
    - 요청 0건인 권역은 응답에서 제외하여 프론트에서 빈 점 그리는 것을 방지
    - 프론트는 Leaflet.heat / Google heatmap / Kakao 사용자 정의 모두 호환

    응답 예시:
      {
        "success": true,
        "count": 3,
        "data": [
          {"region": "동부권", "lat": 37.20, "lng": 127.07, "intensity": 7},
          ...
        ]
      }
    """
    intensity_by_region = _empty_region_counter()
    for req in EducationRequest.query.all():
        org_region = req.organization.region if req.organization else None
        if org_region in intensity_by_region:
            intensity_by_region[org_region] += 1

    data = [
        {
            'region': region,
            'lat': REGION_COORDINATES[region]['lat'],
            'lng': REGION_COORDINATES[region]['lng'],
            'intensity': count,
        }
        for region, count in intensity_by_region.items()
        if count > 0  # 0인 권역은 제외
    ]

    return jsonify({
        'success': True,
        'count': len(data),
        'data': data,
    })


@map_bp.route('/map/instructors', methods=['GET'])
def get_map_instructors():
    """
    강사별 위치 데이터 (소속 권역의 중심 좌표 기준)

    - 활동 중(is_active=True) 강사만 반환
    - 좌표는 강사 개인 좌표가 아닌 소속 권역 중심 좌표 (개인정보 보호)
    - 권역이 좌표 테이블에 없는 강사는 응답에서 제외

    응답 예시:
      {
        "success": true,
        "count": 12,
        "data": [
          {
            "id": 1, "name": "김지현",
            "region": "동부권", "lat": 37.20, "lng": 127.07,
            "specialties": ["AI기초", "머신러닝"],
            "avg_rating": 4.8,
            "cert_level": 3        // 1=기초, 2=중급, 3=전문가
          },
          ...
        ]
      }
    """
    instructors = Instructor.query.filter_by(is_active=True).all()

    data = []
    for inst in instructors:
        coord = REGION_COORDINATES.get(inst.region)
        if not coord:
            continue  # 좌표 정보가 없는 권역은 스킵
        data.append({
            'id': inst.id,
            'name': inst.name,
            'region': inst.region,
            'lat': coord['lat'],
            'lng': coord['lng'],
            'specialties': inst.specialties or [],
            'avg_rating': inst.avg_rating,
            'cert_level': inst.cert_level,
        })

    return jsonify({
        'success': True,
        'count': len(data),
        'data': data,
    })

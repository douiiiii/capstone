from datetime import datetime

from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
from app.models.match import Match
from app.services.region_service import are_adjacent

# 전문 분야 유사도 그룹 (같은 그룹 = 유사 분야로 인정)
SPECIALTY_GROUPS = {
    'AI·디지털': ['AI기초', '머신러닝', '데이터분석', '인공지능활용'],
    '코딩·프로그래밍': ['코딩교육', '파이썬', '앱개발', '웹개발'],
    '미디어·콘텐츠': ['영상편집', 'SNS활용', '유튜브제작', '디지털마케팅'],
    '업무자동화': ['엑셀', '오피스활용', '업무자동화', 'RPA'],
    '생활디지털': ['스마트폰활용', '인터넷뱅킹', '키오스크', '모바일앱'],
}


def _get_specialty_group(specialty: str) -> str | None:
    """전문 분야가 속한 유사도 그룹 반환"""
    for group, specialties in SPECIALTY_GROUPS.items():
        if specialty in specialties:
            return group
    return None


def _calc_region_score(instructor: Instructor, request_region: str) -> float:
    """
    권역 점수 계산 (최대 40점)
      - 같은 권역     : 40점
      - 인접 권역     : 20점
      - 이동가능 범위 내 : 10점
    """
    if not request_region:
        return 0.0

    travel_range = instructor.travel_range or []

    if instructor.region == request_region:
        return 40.0
    if are_adjacent(instructor.region, request_region):
        return 20.0
    if request_region in travel_range:
        return 10.0
    return 0.0


def _calc_specialty_score(instructor: Instructor, specialty_needed: str) -> float:
    """
    전문분야 점수 계산 (최대 40점)
      - 완전 일치       : 40점
      - 유사 분야(동일 그룹) : 20점
    """
    if not specialty_needed:
        return 0.0

    instructor_specialties = instructor.specialties or []

    # 완전 일치
    if specialty_needed in instructor_specialties:
        return 40.0

    # 유사 분야 확인 (같은 그룹에 속하는 전문 분야 보유 여부)
    needed_group = _get_specialty_group(specialty_needed)
    if needed_group:
        for spec in instructor_specialties:
            if _get_specialty_group(spec) == needed_group:
                return 20.0

    return 0.0


def _calc_time_score(instructor: Instructor, preferred_times: list) -> float:
    """
    시간대 점수 계산 (최대 20점)
      - 완전 일치(요청 시간대 전부 가능) : 20점
      - 부분 일치(일부 시간대 가능)     : 10점
    """
    if not preferred_times:
        return 0.0

    available = set(instructor.available_times or [])
    preferred = set(preferred_times)

    if preferred.issubset(available):
        return 20.0
    if available & preferred:
        return 10.0
    return 0.0


def calculate_match_score(instructor: Instructor, request: EducationRequest) -> dict:
    """강사와 교육 요청 사이의 종합 매칭 점수 계산"""
    request_region = request.organization.region if request.organization else None

    region_score = _calc_region_score(instructor, request_region)
    specialty_score = _calc_specialty_score(instructor, request.specialty_needed)
    time_score = _calc_time_score(instructor, request.preferred_times)

    return {
        'instructor': instructor,
        'total_score': region_score + specialty_score + time_score,
        'region_score': region_score,
        'specialty_score': specialty_score,
        'time_score': time_score,
    }


def find_top_matches(request_id: int, top_n: int = 3) -> list | None:
    """
    교육 요청 ID를 받아 상위 N명 강사 매칭 후 matches 테이블에 저장.
    재매칭 시 기존 결과를 삭제하고 새 결과로 교체.
    """
    request = db.session.get(EducationRequest, request_id)
    if not request:
        return None

    # 활성 강사 전체 조회
    instructors = Instructor.query.filter_by(is_active=True).all()
    if not instructors:
        return []

    # 전체 강사에 대해 점수 계산 후 내림차순 정렬
    scored = sorted(
        [calculate_match_score(inst, request) for inst in instructors],
        key=lambda x: x['total_score'],
        reverse=True,
    )

    top = scored[:top_n]

    # 기존 매칭 결과 삭제 (재매칭 처리)
    Match.query.filter_by(request_id=request_id).delete()

    # 새 매칭 결과 저장
    saved_matches = []
    for item in top:
        match = Match(
            request_id=request_id,
            instructor_id=item['instructor'].id,
            match_score=item['total_score'],
            region_score=item['region_score'],
            specialty_score=item['specialty_score'],
            time_score=item['time_score'],
            status='추천',
            created_at=datetime.utcnow(),
        )
        db.session.add(match)
        saved_matches.append(match)

    # 교육 요청 상태 업데이트
    request.status = '매칭완료'
    db.session.commit()

    return [m.to_dict() for m in saved_matches]

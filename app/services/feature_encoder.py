"""
ML 피처 인코더 (v5.0 신규)

카테고리 데이터를 정수 코드로 변환하여 ML 모델 입력에 바로 사용 가능한 형태로 만든다.
모든 인코딩 테이블은 모듈 상단 상수로 분리 → 추가/수정 시 한 곳만 수정.

결측값 처리 (imputation):
  - 평점 미설정 / 0  → DEFAULT_AVG_RATING (4.0, 신규 강사 가정)
  - last_active 없음 → 오늘 (등록일 정보 부재 시 안전 기본값)
  - 카테고리 미설정 → UNKNOWN_CODE (=0)
"""
from datetime import date

from app.models.education_request import EducationRequest
from app.models.instructor import Instructor


# ─────────────────────────────────────────────────────────────────────
# 인코딩 테이블 (수정 시 ML 학습 시점과 호환되도록 신중히 변경)
# 0 = 미지정 / 알 수 없음
# ─────────────────────────────────────────────────────────────────────
UNKNOWN_CODE = 0

REGION_CODES = {
    '동부권': 1,
    '서부권': 2,
    '북부권': 3,
    '남부권': 4,
    '중부권': 5,
}

TIME_CODES = {
    '오전': 1,
    '오후': 2,
    '저녁': 3,
    '야간': 4,
}

# 전문분야 코드 — SPECIALTY_GROUPS와 일치하는 순서로 부여
SPECIALTY_CODES = {
    # AI·디지털
    'AI기초': 11, '머신러닝': 12, '데이터분석': 13, '인공지능활용': 14, '챗GPT': 15,
    # 코딩·프로그래밍
    '코딩교육': 21, '파이썬': 22, '앱개발': 23, '웹개발': 24,
    # 미디어·콘텐츠
    '영상편집': 31, 'SNS활용': 32, '유튜브제작': 33, '디지털마케팅': 34,
    # 업무자동화
    '엑셀': 41, '오피스활용': 42, '업무자동화': 43, 'RPA': 44,
    # 생활디지털
    '스마트폰활용': 51, '인터넷뱅킹': 52, '키오스크': 53, '모바일앱': 54,
}

# 전문분야 → 카테고리 (그룹) 코드
SPECIALTY_CATEGORY_CODES = {
    'AI·디지털': 1,
    '코딩·프로그래밍': 2,
    '미디어·콘텐츠': 3,
    '업무자동화': 4,
    '생활디지털': 5,
}

ORG_TYPE_CODES = {
    '복지관': 1,
    '도서관': 2,
    '주민센터': 3,
    '학교': 4,
    '기업': 5,
    '평생학습관': 6,
    '청소년관': 7,
    '문화원': 8,
    '교육원': 9,
}

# cert_level 정수화: DB/모델이 이미 1/2/3 이므로 identity 매핑.
# 호환 위해 기존 문자열 입력도 받아준다 (마이그레이션 안전망).
CERT_LEVEL_CODES = {
    1: 1, 2: 2, 3: 3,
    '기초': 1, '중급': 2, '전문가': 3,
}

TARGET_AUDIENCE_CODES = {
    '시니어': 1,
    '성인': 2,
    '청소년': 3,
    '어린이': 4,
}

# 결측값 imputation 기본값
DEFAULT_AVG_RATING = 4.0      # 평점 미설정 강사 (신규 강사 가정)
DEFAULT_TOTAL_CLASSES = 0
RATING_VALID_RANGE = (0.0, 5.0)  # 유효 평점 범위
MATCH_SCORE_VALID_RANGE = (-50.0, 200.0)  # 유효 매칭 점수 범위 (이상값 탐지용)


# ─────────────────────────────────────────────────────────────────────
# 단일 값 인코딩 헬퍼
# ─────────────────────────────────────────────────────────────────────

def encode_region(region: str | None) -> int:
    """권역명 → 코드 (미지정 0)"""
    return REGION_CODES.get(region or '', UNKNOWN_CODE)


def encode_time(time_slot: str | None) -> int:
    return TIME_CODES.get(time_slot or '', UNKNOWN_CODE)


def encode_times(times: list | None) -> list[int]:
    """시간대 리스트 → 코드 리스트"""
    return [encode_time(t) for t in (times or [])]


def encode_specialty(specialty: str | None) -> int:
    return SPECIALTY_CODES.get(specialty or '', UNKNOWN_CODE)


def encode_specialties(specs: list | None) -> list[int]:
    return [encode_specialty(s) for s in (specs or [])]


def encode_specialty_category(specialty: str | None) -> int:
    """전문분야 → 그룹 카테고리 코드 (그룹 미존재 시 0)"""
    from app.services.matching_service import _get_specialty_group
    group = _get_specialty_group(specialty or '')
    return SPECIALTY_CATEGORY_CODES.get(group or '', UNKNOWN_CODE)


def encode_org_type(org_type: str | None) -> int:
    """기관 유형 문자열 → 코드. 키워드 매칭 사용 (예: '초등학교' → 학교)"""
    if not org_type:
        return UNKNOWN_CODE
    # 정규화 (matching_service의 정규화 로직 재사용)
    from app.services.matching_service import _normalize_org_type
    normalized = _normalize_org_type(org_type) or ''
    return ORG_TYPE_CODES.get(normalized, UNKNOWN_CODE)


def encode_cert_level(cert) -> int:
    """cert_level 인코딩.
    int(1/2/3) 또는 legacy 문자열('기초'/'중급'/'전문가') 모두 허용."""
    if cert is None:
        return UNKNOWN_CODE
    return CERT_LEVEL_CODES.get(cert, UNKNOWN_CODE)


def encode_audience(audience: str | None) -> int:
    return TARGET_AUDIENCE_CODES.get(audience or '', UNKNOWN_CODE)


# ─────────────────────────────────────────────────────────────────────
# 결측값 imputation
# ─────────────────────────────────────────────────────────────────────

def impute_avg_rating(rating: float | None) -> float:
    """평점 미설정/0 → 신규 강사 기본값 4.0"""
    if rating is None or rating <= 0:
        return DEFAULT_AVG_RATING
    return rating


def impute_last_active(last_active: date | None) -> date:
    """활동일 미설정 → 오늘 (신규 등록 강사 가정)"""
    return last_active or date.today()


# ─────────────────────────────────────────────────────────────────────
# 풀 피처 벡터 빌더
# ─────────────────────────────────────────────────────────────────────

def encode_instructor(instructor: Instructor) -> dict:
    """강사 객체 → ML 입력용 정규화 dict"""
    return {
        'instructor_id': instructor.id,
        'region_code': encode_region(instructor.region),
        'travel_range_codes': [encode_region(r) for r in (instructor.travel_range or [])],
        'specialty_codes': encode_specialties(instructor.specialties),
        'cert_level_code': encode_cert_level(instructor.cert_level),
        'available_time_codes': encode_times(instructor.available_times),
        'target_audience_codes': [encode_audience(a) for a in (instructor.target_audience or [])],
        'total_classes': instructor.total_classes or DEFAULT_TOTAL_CLASSES,
        'avg_rating': impute_avg_rating(instructor.avg_rating),
        'max_classes_month': instructor.max_classes_month or 0,
        'is_active': bool(instructor.is_active),
    }


def encode_request(request: EducationRequest) -> dict:
    """교육 요청 → ML 입력용 정규화 dict"""
    org = request.organization
    return {
        'request_id': request.id,
        'org_region_code': encode_region(org.region if org else None),
        'org_type_code': encode_org_type(org.type if org else None),
        'specialty_code': encode_specialty(request.specialty_needed),
        'specialty_category_code': encode_specialty_category(request.specialty_needed),
        'target_audience_code': encode_audience(request.target_audience),
        'preferred_time_codes': encode_times(request.preferred_times),
        'expected_students': request.expected_students or 0,
        'is_regular': '정기' in (request.frequency or ''),
    }


def build_feature_snapshot(
    instructor: Instructor,
    request: EducationRequest,
    score_breakdown: dict,
) -> dict:
    """
    매칭 시점에 ML 학습용으로 저장할 풀 피처 스냅샷.
    score_breakdown 은 calculate_match_score 의 breakdown 그대로.
    """
    return {
        'instructor': encode_instructor(instructor),
        'request': encode_request(request),
        'score_breakdown': score_breakdown,
        # 학습/추론 시 호환성 깨질 때 빠르게 식별할 수 있도록 버전 명시
        'schema_version': '1.0',
    }

"""
화성시 AI 시민리더 허브 - 매칭 알고리즘 서비스 v4.0

v3.0 대비 추가 사항:
  V4-A) 강사-수요처 상성 시스템
        · 기관 유형별 과거 평균 평점 4.5+ → +15점
        · 강사 선호 기관 유형 일치 +10 / 비선호 -5
  V4-C) 강사 성장 추적
        · 성장 중인 강사 (다음 등급 80% 달성) +10점
        · 등급 자동 업그레이드 + 이력 저장은 grade_service 에서 담당
  V4-D) 매칭 실패 원인 분석
        · 결과 5명 미만일 때 _analyze_failure_reasons 로 구체 사유 산출
"""
from datetime import datetime, date

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
from app.models.match import Match
from app.services.region_service import are_adjacent, normalize_region

# ─────────────────────────────────────────────────────────
# 전문 분야 유사도 그룹 (같은 그룹 = 유사 분야로 인정)
# ─────────────────────────────────────────────────────────
SPECIALTY_GROUPS = {
    'AI·디지털': ['AI기초', '머신러닝', '데이터분석', '인공지능활용', '챗GPT'],
    '코딩·프로그래밍': ['코딩교육', '파이썬', '앱개발', '웹개발'],
    '미디어·콘텐츠': ['영상편집', 'SNS활용', '유튜브제작', '디지털마케팅'],
    '업무자동화': ['엑셀', '오피스활용', '업무자동화', 'RPA'],
    '생활디지털': ['스마트폰활용', '인터넷뱅킹', '키오스크', '모바일앱'],
}

# ─────────────────────────────────────────────────────────
# 인증 등급별 강의 가능 전문 분야
#   - 3(전문가) 또는 미설정: 제한 없음 (모든 분야 가능)
#   - 2(중급): 기초 분야 + 챗GPT, 데이터분석, 코딩교육
#   - 1(기초): AI기초, 스마트폰활용만 가능
# cert_level 은 정수(1/2/3)로 통일 — 키 타입을 int 로 사용.
# ─────────────────────────────────────────────────────────
CERT_ALLOWED_SPECIALTIES: dict[int, set[str]] = {
    1: {'AI기초', '스마트폰활용'},
    2: {'AI기초', '스마트폰활용', '챗GPT', '데이터분석', '코딩교육'},
}

# ─────────────────────────────────────────────────────────
# 확정으로 간주할 매칭 상태
# (이번 달 강의 횟수·일정 충돌 계산 시 사용)
# DB CHECK 제약: matches.status ∈ {'매칭제안','수락','거절','최종확정'}
# 기존 코드의 '확정'/'완료' 를 DB 표준값 '최종확정' 으로 통일.
# ─────────────────────────────────────────────────────────
CONFIRMED_MATCH_STATUSES = ('수락', '최종확정')

# ─────────────────────────────────────────────────────────
# 신규 강사 기준 (누적 강의 횟수)
# v5.1: 5회 → 10회로 상향
# ─────────────────────────────────────────────────────────
NEW_INSTRUCTOR_THRESHOLD = 10

# ─────────────────────────────────────────────────────────
# 강사가 직접 설정할 수 있는 월 최대 강의 횟수 범위 (v5.1)
# 기본값은 Instructor 모델의 default(30) 를 사용한다.
# ─────────────────────────────────────────────────────────
MAX_CLASSES_MONTH_MIN = 10
MAX_CLASSES_MONTH_MAX = 40

# ─────────────────────────────────────────────────────────
# 권역 0점 가드 (시나리오 이슈 #2)
# 권역 점수가 0이면 base_score 에 패널티를 더해 후순위로 밀어낸다.
# - 1지망(권역 일치/인접/이동가능) 후보가 충분하면 자연스럽게 제외됨
# - 후보가 부족할 때만 fallback 으로 노출되도록 보호
# ─────────────────────────────────────────────────────────
REGION_GUARD_PENALTY = -20.0

# ─────────────────────────────────────────────────────────
# 요일 검증 (검증 이슈 #1 수정)
# preferred_dates 에서 요일을 추출 후 강사의 available_days 와 비교.
# 정책: 평일/주말 카테고리 OR 구체 요일 둘 다 지원.
#   - '평일' / '주말' 카테고리 매칭
#   - '월','화'... 구체 요일 매칭
# 검증 실패 시 매칭 후보에서 완전 제외 (강의 진행 자체가 불가능하므로)
# ─────────────────────────────────────────────────────────
WEEKDAY_KO = ['월', '화', '수', '목', '금', '토', '일']
WEEKDAY_LABELS = {True: '주말', False: '평일'}


# ───────────────────────────── 보조 유틸 ──────────────────────────────

def _get_specialty_group(specialty: str) -> str | None:
    """전문 분야가 속한 유사도 그룹명 반환"""
    for group, items in SPECIALTY_GROUPS.items():
        if specialty in items:
            return group
    return None


def _get_group_specialties(specialty: str) -> list[str]:
    """동일 유사도 그룹에 속하는 모든 전문 분야 목록 반환"""
    for items in SPECIALTY_GROUPS.values():
        if specialty in items:
            return items
    return []


def _months_since(target: date) -> int:
    """특정 날짜로부터 현재까지 경과 개월 수 (연·월 기준)"""
    today = date.today()
    return (today.year - target.year) * 12 + (today.month - target.month)


def _is_new_instructor(instructor: Instructor) -> bool:
    """누적 강의 NEW_INSTRUCTOR_THRESHOLD 미만이면 신규 강사 (v5.1: 10회 미만)"""
    return (instructor.total_classes or 0) < NEW_INSTRUCTOR_THRESHOLD


def _is_regular_request(request: EducationRequest) -> bool:
    """정기 강의 요청 여부 판정 (frequency 에 '정기' 키워드 포함)"""
    return '정기' in (request.frequency or '')


# ───────────────────────── 인증 등급 필터 ─────────────────────────────

def _is_cert_eligible(instructor: Instructor, specialty: str) -> bool:
    """
    인증 등급이 해당 전문분야 강의를 허용하는지 확인.
    '전문가' 또는 미설정이면 모든 분야 가능.
    """
    cert = instructor.cert_level
    if not cert or cert not in CERT_ALLOWED_SPECIALTIES:
        return True  # 전문가 또는 미설정 → 제한 없음
    return specialty in CERT_ALLOWED_SPECIALTIES[cert]


def _is_cert_eligible_for_similar(instructor: Instructor, specialty: str) -> bool:
    """
    조건 완화 시 사용.
    유사 분야 중 인증 등급이 허용하고 강사도 가르칠 수 있는 분야가 하나라도 있는지 확인.
    """
    cert = instructor.cert_level
    if not cert or cert not in CERT_ALLOWED_SPECIALTIES:
        return True

    allowed = CERT_ALLOWED_SPECIALTIES[cert]
    group_specs = set(_get_group_specialties(specialty))
    inst_specs = set(instructor.specialties or [])
    return bool(allowed & group_specs & inst_specs)


# ─────────────────────── 요일 추출/검증 (이슈 #1) ─────────────────────

def _extract_requested_weekdays(preferred_dates) -> list[str]:
    """
    preferred_dates 에서 한국어 요일 리스트를 추출한다.

    지원 입력 형식:
      - ['2026-06-01', '2026-06-08']  (list of ISO date)
      - '2026-06-01 ~ 2026-06-30'     (str, 범위 → 양 끝점만 사용)
      - '2026-06-01'                    (str, 단일)

    파싱 실패 시 빈 리스트를 반환. 빈 리스트면 요일 검증을 건너뛴다.
    """
    if not preferred_dates:
        return []
    raw = preferred_dates if isinstance(preferred_dates, list) else [preferred_dates]
    weekdays: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        # 'YYYY-MM-DD ~ YYYY-MM-DD' 범위는 시작/종료 둘 다 사용
        candidates = [s.strip() for s in item.split('~')] if '~' in item else [item.strip()]
        for s in candidates:
            try:
                d = date.fromisoformat(s)
            except Exception:
                continue
            weekdays.append(WEEKDAY_KO[d.weekday()])
    return weekdays


def _is_weekday_compatible(
    instructor: Instructor, requested_weekdays: list[str],
) -> tuple[bool, str]:
    """
    강사 available_days 와 요청된 요일이 호환되는지 검사.

    available_days 가 '평일'/'주말' 카테고리, 구체 요일('월','화'...), 둘 다 섞여 있을 수 있어
    두 표기를 모두 지원한다.

    반환: (호환여부, 사유)
      - 요청 요일이 비어있으면 (True, '요일 정보 없음 — 검증 생략')
      - 강사 가능 요일이 비어있으면 (True, '강사 가능 요일 정보 없음 — 검증 생략')
      - 요청된 요일 중 하나라도 강사가 가능하면 True
    """
    if not requested_weekdays:
        return True, '요일 정보 없음 — 검증 생략'
    avail = list(instructor.available_days or [])
    if not avail:
        return True, '강사 가능 요일 정보 없음 — 검증 생략'

    # 카테고리(평일/주말) → 해당하는 구체 요일로 확장
    expanded: set[str] = set()
    for d in avail:
        if d == '평일':
            expanded.update(['월', '화', '수', '목', '금'])
        elif d == '주말':
            expanded.update(['토', '일'])
        else:
            expanded.add(d)

    # 요청된 요일 중 하나라도 강사가 가능하면 OK
    matched = [w for w in requested_weekdays if w in expanded]
    if matched:
        return True, f'요일 {matched} 가능'
    needs = sorted(set(requested_weekdays))
    return False, f'요청 요일 {needs} 불가 (강사 가능: {avail})'


# ─────────────────────── 기본 점수 계산 함수 ──────────────────────────

def _calc_region_score(instructor: Instructor, request_region: str | None) -> float:
    """권역 점수 (최대 40점)

    검증 이슈 #3 수정: 외부에서 '동탄1' 같은 세부 지역명이 들어와도
    normalize_region 으로 권역명을 정규화한 뒤 비교한다.
    """
    if not request_region:
        return 0.0
    # 요청 권역명 정규화 (예: '동탄1' → '동부권')
    request_region = normalize_region(request_region)
    inst_region = normalize_region(instructor.region)
    travel_range = [normalize_region(r) for r in (instructor.travel_range or [])]
    if inst_region == request_region:
        return 40.0
    if are_adjacent(inst_region, request_region):
        return 20.0
    if request_region in travel_range:
        return 10.0
    return 0.0


def _calc_specialty_score(instructor: Instructor, specialty_needed: str | None) -> float:
    """전문분야 점수 (최대 40점)"""
    if not specialty_needed:
        return 0.0
    inst_specs = instructor.specialties or []
    if specialty_needed in inst_specs:
        return 40.0
    needed_group = _get_specialty_group(specialty_needed)
    if needed_group:
        for spec in inst_specs:
            if _get_specialty_group(spec) == needed_group:
                return 20.0
    return 0.0


def _calc_time_score(instructor: Instructor, preferred_times: list | None) -> float:
    """시간대 점수 (최대 20점)"""
    if not preferred_times:
        return 0.0
    available = set(instructor.available_times or [])
    preferred = set(preferred_times)
    if preferred.issubset(available):
        return 20.0
    if available & preferred:
        return 10.0
    return 0.0


# ───────────────────── 기존 보너스 / 패널티 (A 1단계) ─────────────────

def _calc_rating_bonus(instructor: Instructor) -> tuple[float, str]:
    """
    평점 보너스 계산
    - 4.8 이상  : +10점
    - 4.5 ~ 4.7 : +5점
    - 4.5 미만  : 0점
    """
    rating = instructor.avg_rating or 0.0
    if rating >= 4.8:
        return 10.0, f'평점 {rating:.1f} (4.8 이상 → +10점)'
    elif rating >= 4.5:
        return 5.0, f'평점 {rating:.1f} (4.5~4.7 → +5점)'
    else:
        return 0.0, f'평점 {rating:.1f} (4.5 미만 → 보너스 없음)'


def _calc_activity_penalty(instructor: Instructor) -> tuple[float, str]:
    """
    최근 활동일 패널티 계산
    - 3개월 이내  : 0점
    - 3 ~ 6개월  : -5점
    - 6개월 초과 : -10점
    - 이력 없음  : -5점
    """
    if not instructor.last_active:
        return 5.0, '활동 이력 없음 (→ -5점 패널티)'
    months = _months_since(instructor.last_active)
    if months <= 3:
        return 0.0, f'최근 {months}개월 전 활동 (3개월 이내 → 패널티 없음)'
    elif months <= 6:
        return 5.0, f'최근 {months}개월 전 활동 (3~6개월 → -5점 패널티)'
    else:
        return 10.0, f'최근 {months}개월 전 활동 (6개월 초과 → -10점 패널티)'


# ──────────────── A항: 피드백 반영 시스템 ─────────────────────────────

def _calc_satisfaction_bonus(
    instructor: Instructor, context: dict | None = None,
) -> tuple[float, str]:
    """
    수요처 만족도 평가 점수 반영 (A-1)
    - 평균 만족도 4.5 이상 : +10점
    - 평균 만족도 3.0 미만 : -10점 (패널티)
    - 그 외 / 이력 없음    : 0점
    반환값은 (점수, 사유) — 점수는 부호 그대로 (양수=보너스, 음수=패널티)
    """
    # N+1 회피: 사전 캐싱된 만족도 점수 사용
    if context and 'satisfaction_by_inst' in context:
        scores = context['satisfaction_by_inst'].get(instructor.id, [])
    else:
        scores = [
            m.satisfaction_score for m in (instructor.matches or [])
            if m.satisfaction_score is not None
        ]
    if not scores:
        return 0.0, '만족도 평가 이력 없음'
    avg = sum(scores) / len(scores)
    if avg >= 4.5:
        return 10.0, f'만족도 평균 {avg:.2f} (4.5 이상 → +10점)'
    if avg < 3.0:
        return -10.0, f'만족도 평균 {avg:.2f} (3.0 미만 → -10점 패널티)'
    return 0.0, f'만족도 평균 {avg:.2f} (3.0~4.5 → 보너스 없음)'


def _calc_rerequest_bonus(
    instructor: Instructor, request: EducationRequest,
    context: dict | None = None,
) -> tuple[float, str]:
    """
    같은 강사 재요청 횟수 점수화 (A-2)
    - 같은 기관에서 이전에 매칭된 횟수 3회 이상: +15점
    - 같은 기관에서 이전에 매칭된 횟수 1~2회 : +7점
    """
    if not request.org_id:
        return 0.0, ''
    # N+1 회피: 사전 캐싱된 (강사→기관→요청id) 인덱스 사용
    if context and 'rerequest_data' in context:
        req_ids = (
            context['rerequest_data'].get(instructor.id, {}).get(request.org_id, [])
        )
        count = sum(1 for rid in req_ids if rid != request.id)
    else:
        count = (
            Match.query
            .join(EducationRequest, Match.request_id == EducationRequest.id)
            .filter(
                Match.instructor_id == instructor.id,
                EducationRequest.org_id == request.org_id,
                EducationRequest.id != request.id,
            )
            .count()
        )
    if count >= 3:
        return 15.0, f'같은 기관 재요청 {count}회 (3회 이상 → +15점)'
    if count >= 1:
        return 7.0, f'같은 기관 재요청 {count}회 (1~2회 → +7점)'
    return 0.0, ''


def _calc_bad_rating_penalty(
    instructor: Instructor, context: dict | None = None,
) -> tuple[float, str]:
    """
    누적 나쁜 평가 패널티 (A-3)
    - 3.0 미만 평가가 3회 이상 누적된 강사: 자동 후순위 (-30점)
    """
    # N+1 회피: 사전 카운팅된 나쁜 평가 수 사용
    if context and 'bad_rating_counts' in context:
        bad_count = context['bad_rating_counts'].get(instructor.id, 0)
    else:
        bad_count = sum(
            1 for m in (instructor.matches or [])
            if m.satisfaction_score is not None and m.satisfaction_score < 3.0
        )
    if bad_count >= 3:
        return -30.0, f'나쁜 평가 {bad_count}회 누적 (자동 후순위 -30점)'
    return 0.0, ''


# ─────────────── B항: 수요처 맞춤 추천 ────────────────────────────────

def _calc_org_type_bonus(
    instructor: Instructor, organization,
) -> tuple[float, str]:
    """
    기관 유형별 가중치 (B-1)
    - 학교  → 누적 강의 30회 이상 강사 +10점
    - 기업  → 전문가 인증 등급 강사    +10점
    - 복지관 → 시니어 대상 경험 강사   +10점
    """
    if not organization:
        return 0.0, ''
    org_type = organization.type or ''

    if '학교' in org_type:
        if (instructor.total_classes or 0) >= 30:
            return 10.0, (
                f'기관유형=학교 + 누적 강의 {instructor.total_classes}회 → +10점'
            )
        return 0.0, f'기관유형=학교지만 누적 강의 부족({instructor.total_classes or 0}회)'

    if '기업' in org_type or '회사' in org_type:
        # cert_level 정수화: 3(전문가) 비교
        if instructor.cert_level == 3:
            return 10.0, '기관유형=기업 + 전문가 인증 → +10점'
        return 0.0, f'기관유형=기업이지만 인증 등급 부족({instructor.cert_level})'

    if '복지관' in org_type:
        targets = instructor.target_audience or []
        if '시니어' in targets:
            return 10.0, '기관유형=복지관 + 시니어 대상 경험 → +10점'
        return 0.0, '기관유형=복지관이지만 시니어 대상 경험 없음'

    return 0.0, ''


def _calc_prior_match_bonus(
    instructor: Instructor, request: EducationRequest,
    context: dict | None = None,
) -> tuple[float, str]:
    """
    수요처 과거 매칭 이력 기반 보너스 (B-2)
    - 같은 기관에서 이전에 매칭된 적이 있으면 +5점
    """
    if not request.org_id:
        return 0.0, ''
    # N+1 회피: 사전 캐싱된 (강사→기관→요청id) 인덱스 사용
    if context and 'rerequest_data' in context:
        req_ids = (
            context['rerequest_data'].get(instructor.id, {}).get(request.org_id, [])
        )
        exists = any(rid != request.id for rid in req_ids)
    else:
        exists = (
            Match.query
            .join(EducationRequest, Match.request_id == EducationRequest.id)
            .filter(
                Match.instructor_id == instructor.id,
                EducationRequest.org_id == request.org_id,
                EducationRequest.id != request.id,
            )
            .first()
        )
    if exists:
        return 5.0, '이전 매칭 이력 있음 → +5점'
    return 0.0, ''


# ─────────────── C항: 강사 부하 분산 ──────────────────────────────────

def _count_this_month_confirmed(instructor: Instructor, today: date) -> int:
    """
    이번 달 강사가 진행할/진행한 활성 세션 수.

    v5.1: matches 카운트 대신 class_sessions(예정+완료) 카운트.
    정기 강의 1건이어도 실제 세션 수만큼 카운트되어 부하 분산 정확도가 향상됨.
    """
    # 순환 임포트를 피하기 위해 함수 내부에서 import
    from app.services.class_session_service import count_sessions_in_month
    return count_sessions_in_month(instructor.id, today.year, today.month)


def _calc_load_penalty(
    instructor: Instructor, context: dict,
) -> tuple[float, str]:
    """
    이번 달 매칭 80% 도달 패널티 (C-2)
    - 이번 달 확정 매칭 횟수가 max_classes_month의 80% 이상이면 -15점
    """
    max_m = instructor.max_classes_month or 0
    if max_m <= 0:
        return 0.0, ''
    cnt = context['month_match_counts'].get(instructor.id, 0)
    ratio = cnt / max_m
    if ratio >= 0.8:
        return -15.0, (
            f'이번 달 매칭 {cnt}/{max_m} ({ratio*100:.0f}% ≥ 80%) → -15점 패널티'
        )
    return 0.0, ''


def _calc_concentration_penalty(
    instructor: Instructor, context: dict,
) -> tuple[float, str]:
    """
    매칭 쏠림 방지 패널티 (C-3)
    - 이번 달 매칭 횟수가 가장 많은 강사에게만 -10점
    """
    if instructor.id in context['most_matched_ids']:
        cnt = context['month_match_counts'].get(instructor.id, 0)
        return -10.0, f'이번 달 최다 매칭 강사({cnt}회) → -10점 쏠림 패널티'
    return 0.0, ''


# ─────────────── D항: 연속 강의 매칭 ──────────────────────────────────

def _calc_regular_bonus(
    instructor: Instructor, request: EducationRequest,
) -> tuple[float, str]:
    """
    정기 강의 매칭 보너스 (D-1)
    - frequency 가 '정기' 일 때, 월 3회 이상 강의 가능한 강사에게 +10점
    """
    if not _is_regular_request(request):
        return 0.0, ''
    if (instructor.max_classes_month or 0) >= 3:
        return 10.0, (
            f"정기 강의 + 월 {instructor.max_classes_month}회 가능 → +10점"
        )
    return 0.0, '정기 강의지만 월 가능 횟수 3회 미만'


def _has_date_conflict(
    instructor: Instructor, request: EducationRequest,
) -> bool:
    """
    이미 잡혀있는 강의 세션과 (날짜 + 시간대) 가 겹치는지 검사 (D-3 / v5.1).

    v5.1: preferred_dates 단순 비교 → class_sessions(예정/완료) 기반 비교로 강화.
    같은 날짜라도 시간대(오전/오후/저녁)가 다르면 충돌로 보지 않음.
    """
    # 순환 임포트를 피하기 위해 함수 내부에서 import
    from app.services.class_session_service import has_schedule_conflict

    pref_dates = request.preferred_dates or []
    pref_times = request.preferred_times or []
    if not pref_dates or not pref_times:
        return False
    return has_schedule_conflict(
        instructor.id, pref_dates, pref_times,
        # 자기 자신의 매칭(같은 request) 세션은 충돌 대상에서 제외
        exclude_match_id=None,
    )


# ─────────────── E항: 신규 강사 노출 보장 ─────────────────────────────

def _calc_new_instructor_bonus(instructor: Instructor) -> tuple[float, str]:
    """신규 강사(누적 5회 미만) +20점 (E-2)"""
    if _is_new_instructor(instructor):
        return 20.0, (
            f'신규 강사 (누적 강의 {instructor.total_classes or 0}회) → +20점'
        )
    return 0.0, ''


# ─────────────── v4-A: 강사-수요처 상성 시스템 ─────────────────────────

# 기관 유형별 상성 보너스를 받기 위한 최소 평균 평점
ORG_CHEMISTRY_MIN_RATING = 4.5
# 상성 보너스 점수
ORG_CHEMISTRY_BONUS = 15.0
# 강사 선호/비선호 기관 유형 가중치
PREFERENCE_MATCH_BONUS = 10.0
PREFERENCE_DISLIKE_PENALTY = -5.0


def _normalize_org_type(org_type: str | None) -> str | None:
    """
    기관 유형 문자열을 비교 가능한 형태로 정규화.
    예: '복지관/노인복지센터' → '복지관'
    실제 기관 유형이 다양하게 표기될 수 있어 키워드 매칭을 사용.
    """
    if not org_type:
        return None
    # 우선순위가 높은 키워드부터 검사
    for key in ('학교', '기업', '회사', '복지관', '도서관', '주민센터'):
        if key in org_type:
            return '기업' if key == '회사' else key
    return org_type


def _calc_org_chemistry_bonus(
    instructor: Instructor, organization, context: dict | None = None,
) -> tuple[float, str]:
    """
    강사-기관유형 상성 보너스 (v4-A-1)
    - 해당 기관 유형에서 강사의 과거 평균 만족도가 4.5 이상이면 +15점
    - 평가 이력이 없으면 0점 (보너스 없음)
    """
    if not organization:
        return 0.0, ''
    target_type = _normalize_org_type(organization.type)
    if not target_type:
        return 0.0, ''

    # N+1 회피: 사전 그룹화된 (강사→기관유형→만족도 점수 목록) 인덱스 사용
    if context and 'chemistry_by_type' in context:
        scores = (
            context['chemistry_by_type'].get(instructor.id, {}).get(target_type, [])
        )
    else:
        # fallback: 강사 매칭을 순회하며 직접 집계
        scores: list[float] = []
        for m in (instructor.matches or []):
            if m.satisfaction_score is None:
                continue
            req = m.request
            org = req.organization if req else None
            if not org:
                continue
            if _normalize_org_type(org.type) == target_type:
                scores.append(m.satisfaction_score)

    if not scores:
        return 0.0, f'{target_type} 평가 이력 없음 (상성 보너스 없음)'

    avg = sum(scores) / len(scores)
    if avg >= ORG_CHEMISTRY_MIN_RATING:
        return ORG_CHEMISTRY_BONUS, (
            f'{target_type} 평균 만족도 {avg:.2f} (≥{ORG_CHEMISTRY_MIN_RATING}) '
            f'→ +{ORG_CHEMISTRY_BONUS:.0f}점 상성 보너스'
        )
    return 0.0, f'{target_type} 평균 만족도 {avg:.2f} (상성 기준 미달)'


def _calc_preference_bonus(
    instructor: Instructor, organization,
) -> tuple[float, str]:
    """
    강사 선호/비선호 기관 유형 보너스/패널티 (v4-A-2)
    - preferred_org_types 와 일치: +10
    - disliked_org_types 와 일치 : -5
    """
    if not organization:
        return 0.0, ''
    target_type = _normalize_org_type(organization.type)
    if not target_type:
        return 0.0, ''

    preferred = instructor.preferred_org_types or []
    disliked = instructor.disliked_org_types or []

    if target_type in preferred:
        return PREFERENCE_MATCH_BONUS, (
            f'선호 기관 유형({target_type}) 일치 → +{PREFERENCE_MATCH_BONUS:.0f}점'
        )
    if target_type in disliked:
        return PREFERENCE_DISLIKE_PENALTY, (
            f'비선호 기관 유형({target_type}) → {PREFERENCE_DISLIKE_PENALTY:.0f}점'
        )
    return 0.0, ''


# ─────────────── v4-C: 강사 성장 추적 (성장 보너스만 매칭에 반영) ──────

# 등급 자동 업그레이드 기준 (grade_service 와 공유 — 수정 시 동기화)
# v5.1 기준 상향: 기초→중급 10→20회, 중급→전문가 30→60회
# cert_level 정수화에 맞춰 키/next 값을 정수로 변경
# 1=기초, 2=중급, 3=전문가
GRADE_UPGRADE_RULES = {
    1: {  # 기초 → 중급
        'next': 2,
        'min_classes': 20,
        'min_rating': 4.0,
    },
    2: {  # 중급 → 전문가
        'next': 3,
        'min_classes': 60,
        'min_rating': 4.5,
    },
}
# 사용자 표시용 등급명 매핑 (정수 → 한글 명칭)
GRADE_NAMES = {1: '기초', 2: '중급', 3: '전문가'}
# 성장 보너스: 다음 등급 조건의 80% 이상 달성 시 +10
GROWTH_PROGRESS_THRESHOLD = 0.8
GROWTH_BONUS = 10.0


def _calc_grade_progress(instructor: Instructor) -> tuple[float, dict | None]:
    """
    다음 등급까지의 진척률(0.0~1.0)과 기준 dict 반환.
    승급 기준이 없는 등급(전문가/미설정)은 (0.0, None).
    """
    # cert_level 정수화: None 대비 0 fallback (0은 RULES 에 없는 키)
    rule = GRADE_UPGRADE_RULES.get(instructor.cert_level or 0)
    if not rule:
        return 0.0, None
    classes = instructor.total_classes or 0
    rating = instructor.avg_rating or 0.0
    # 강의수/평점 각각의 달성률 중 더 낮은 값으로 진척률 산출
    class_ratio = min(classes / rule['min_classes'], 1.0)
    rating_ratio = min(rating / rule['min_rating'], 1.0)
    return min(class_ratio, rating_ratio), rule


def _calc_growth_bonus(instructor: Instructor) -> tuple[float, str]:
    """
    성장 중인 강사 보너스 (v4-C)
    - 다음 등급 조건의 80% 이상 달성 (단, 아직 승급 전) → +10점
    """
    progress, rule = _calc_grade_progress(instructor)
    if rule is None:
        return 0.0, ''
    if progress >= 1.0:
        # 승급 가능 상태인 강사는 별도 시스템(grade_service)에서 처리
        return 0.0, ''
    if progress >= GROWTH_PROGRESS_THRESHOLD:
        # 표시용은 정수 대신 한글 등급명 사용 (예: 2 → '중급')
        next_name = GRADE_NAMES.get(rule['next'], rule['next'])
        return GROWTH_BONUS, (
            f"{next_name} 승급 {progress*100:.0f}% 달성 (성장 중) → +{GROWTH_BONUS:.0f}점"
        )
    return 0.0, ''


# ─────────────────── 스코어링 컨텍스트 빌더 ────────────────────────────

def _build_scoring_context(active_instructors: list[Instructor]) -> dict:
    """
    매칭 시 반복 조회되는 데이터를 미리 캐싱 (N+1 쿼리 회피).

    캐시 항목:
      - month_match_counts  : 강사별 이번 달 활성 세션 수
      - most_matched_ids    : 이번 달 매칭이 가장 많은 강사 ID 집합
      - satisfaction_by_inst: 강사별 만족도 점수 리스트 (만족도/나쁜평가 패널티 공유)
      - bad_rating_counts   : 강사별 <3.0 평가 누적 횟수
      - rerequest_data      : 강사별 {기관 id → [요청 id 목록]} (재요청·과거매칭 보너스 공유)
      - chemistry_by_type   : 강사별 {기관 유형 → 만족도 점수 리스트} (상성 보너스)
    """
    today = date.today()
    instructor_ids = [i.id for i in active_instructors]

    # ── 배치 1: 이번 달 활성 세션 수 (N개 쿼리 → 1개 쿼리) ─────────
    from app.models.class_session import ClassSession
    from app.services.class_session_service import ACTIVE_SESSION_STATUSES
    counts: dict[int, int] = {iid: 0 for iid in instructor_ids}
    if instructor_ids:
        session_rows = (
            ClassSession.query
            .filter(
                ClassSession.instructor_id.in_(instructor_ids),
                ClassSession.status.in_(ACTIVE_SESSION_STATUSES),
                db.extract('year', ClassSession.session_date) == today.year,
                db.extract('month', ClassSession.session_date) == today.month,
            )
            .all()
        )
        for s in session_rows:
            counts[s.instructor_id] = counts.get(s.instructor_id, 0) + 1

    max_count = max(counts.values()) if counts else 0
    most_matched_ids = (
        {iid for iid, c in counts.items() if c == max_count}
        if max_count > 0 else set()
    )

    # ── 배치 2: 강사별 매칭 통계 (N개 쿼리 → 1개 쿼리, eager loading) ──
    satisfaction_by_inst: dict[int, list[float]] = {iid: [] for iid in instructor_ids}
    bad_rating_counts: dict[int, int] = {iid: 0 for iid in instructor_ids}
    # iid → { org_id: [req_id, ...] }  — 재요청/과거 매칭 보너스 공유
    rerequest_data: dict[int, dict[int, list[int]]] = {iid: {} for iid in instructor_ids}
    # iid → { 기관유형: [만족도 점수, ...] }  — 상성 보너스
    chemistry_by_type: dict[int, dict[str, list[float]]] = {iid: {} for iid in instructor_ids}

    if instructor_ids:
        all_matches = (
            Match.query
            .options(
                joinedload(Match.request).joinedload(EducationRequest.organization)
            )
            .filter(Match.instructor_id.in_(instructor_ids))
            .all()
        )
        for m in all_matches:
            iid = m.instructor_id
            req = m.request
            org = req.organization if req else None

            # 재요청 / 과거 매칭 이력 누적
            if req and req.org_id:
                rerequest_data[iid].setdefault(req.org_id, []).append(req.id)

            # 만족도 통계 (만족도 누적 + 나쁜 평가 + 상성)
            if m.satisfaction_score is not None:
                satisfaction_by_inst[iid].append(m.satisfaction_score)
                if m.satisfaction_score < 3.0:
                    bad_rating_counts[iid] += 1
                if org:
                    org_type = _normalize_org_type(org.type)
                    if org_type:
                        chemistry_by_type[iid].setdefault(org_type, []).append(
                            m.satisfaction_score,
                        )

    return {
        'today': today,
        'month_match_counts': counts,
        'most_matched_ids': most_matched_ids,
        'satisfaction_by_inst': satisfaction_by_inst,
        'bad_rating_counts': bad_rating_counts,
        'rerequest_data': rerequest_data,
        'chemistry_by_type': chemistry_by_type,
    }


# ──────────────────────── 종합 점수 계산 ──────────────────────────────

def calculate_match_score(
    instructor: Instructor,
    request: EducationRequest,
    context: dict | None = None,
) -> dict:
    """
    강사와 교육 요청 사이의 종합 매칭 점수 계산.
    breakdown 형식으로 보너스/패널티 상세를 반환한다.
    """
    if context is None:
        context = _build_scoring_context([instructor])

    request_region = request.organization.region if request.organization else None
    organization = request.organization

    # ── 기본 점수 ─────────────────────────────────────────────────
    region_score = _calc_region_score(instructor, request_region)
    specialty_score = _calc_specialty_score(instructor, request.specialty_needed)
    time_score = _calc_time_score(instructor, request.preferred_times)
    base_score = region_score + specialty_score + time_score

    # ── 보너스 / 패널티 ─────────────────────────────────────────────
    bonuses: list[dict] = []
    penalties: list[dict] = []

    # 권역 0점 가드 (시나리오 이슈 #2)
    # 권역이 일치/인접/이동가능 중 어느 것에도 해당 안 되면 -20점 패널티
    # → 다른 보너스로 0점을 극복할 수 없도록 base_score 단계에서 차감
    if request_region and region_score == 0.0:
        penalties.append({
            '항목': '권역 부적합 가드',
            '점수': REGION_GUARD_PENALTY,
            '사유': f'요청 권역({request_region}) 부적합 → {REGION_GUARD_PENALTY:.0f}점 가드',
        })

    # 검증 이슈 #1 수정: 요일 검증 결과를 breakdown 에 기록 (점수에는 영향 없음 — 자동 제외 처리)
    requested_weekdays = _extract_requested_weekdays(request.preferred_dates)
    weekday_ok, weekday_reason = _is_weekday_compatible(instructor, requested_weekdays)
    weekday_info = {
        '요청_요일': requested_weekdays,
        '강사_가능요일': list(instructor.available_days or []),
        '검증결과': '호환' if weekday_ok else '불일치',
        '사유': weekday_reason,
    }

    def _add(name: str, value: float, reason: str):
        """양수면 bonuses, 음수면 penalties 로 정리. 0/빈사유는 무시."""
        if value > 0:
            bonuses.append({'항목': name, '점수': value, '사유': reason})
        elif value < 0:
            penalties.append({'항목': name, '점수': value, '사유': reason})

    # 평점 보너스
    rating_bonus, rating_reason = _calc_rating_bonus(instructor)
    _add('평점 보너스', rating_bonus, rating_reason)

    # 활동일 패널티 (반환값 양수 → 실제론 음수 처리)
    activity_penalty, activity_reason = _calc_activity_penalty(instructor)
    if activity_penalty > 0:
        penalties.append({
            '항목': '활동일 패널티',
            '점수': -activity_penalty,
            '사유': activity_reason,
        })

    # A-1 만족도
    sat_value, sat_reason = _calc_satisfaction_bonus(instructor, context)
    _add('만족도 보너스/패널티', sat_value, sat_reason)

    # A-2 재요청
    rerequest_value, rerequest_reason = _calc_rerequest_bonus(instructor, request, context)
    _add('재요청 보너스', rerequest_value, rerequest_reason)

    # A-3 나쁜 평가 누적
    bad_value, bad_reason = _calc_bad_rating_penalty(instructor, context)
    _add('누적 나쁜평가 패널티', bad_value, bad_reason)

    # B-1 기관 유형 가중치
    org_value, org_reason = _calc_org_type_bonus(instructor, organization)
    _add('기관 유형 보너스', org_value, org_reason)

    # B-2 과거 매칭 이력
    prior_value, prior_reason = _calc_prior_match_bonus(instructor, request, context)
    _add('과거 매칭 이력 보너스', prior_value, prior_reason)

    # C-2 월 강의 80% 도달
    load_value, load_reason = _calc_load_penalty(instructor, context)
    _add('월 강의 80% 패널티', load_value, load_reason)

    # C-3 매칭 쏠림 패널티
    conc_value, conc_reason = _calc_concentration_penalty(instructor, context)
    _add('매칭 쏠림 패널티', conc_value, conc_reason)

    # D-1 정기 강의 보너스
    reg_value, reg_reason = _calc_regular_bonus(instructor, request)
    _add('정기 강의 보너스', reg_value, reg_reason)

    # E-2 신규 강사 보너스
    new_value, new_reason = _calc_new_instructor_bonus(instructor)
    _add('신규 강사 보너스', new_value, new_reason)

    # v4-A-1 강사-기관유형 상성 보너스
    chem_value, chem_reason = _calc_org_chemistry_bonus(instructor, organization, context)
    _add('상성 보너스', chem_value, chem_reason)

    # v4-A-2 선호/비선호 기관 유형
    pref_value, pref_reason = _calc_preference_bonus(instructor, organization)
    _add('선호 기관 보너스/패널티', pref_value, pref_reason)

    # v4-C 성장 중인 강사 보너스
    growth_value, growth_reason = _calc_growth_bonus(instructor)
    _add('성장 강사 보너스', growth_value, growth_reason)

    # ── 총점 ────────────────────────────────────────────────────
    bonus_sum = sum(b['점수'] for b in bonuses)
    penalty_sum = sum(p['점수'] for p in penalties)  # 이미 음수
    total_score = base_score + bonus_sum + penalty_sum

    # 공식 문자열 (점수 디버깅용)
    parts = [
        f'{region_score}(권역)',
        f'+{specialty_score}(전문분야)',
        f'+{time_score}(시간대)',
    ]
    for b in bonuses:
        parts.append(f"+{b['점수']}({b['항목']})")
    for p in penalties:
        parts.append(f"{p['점수']}({p['항목']})")
    formula = ' '.join(parts) + f' = {total_score}'

    return {
        'instructor': instructor,
        'total_score': total_score,
        'region_score': region_score,
        'specialty_score': specialty_score,
        'time_score': time_score,
        'base_score': base_score,
        # 기존 호환 필드
        'rating_bonus': rating_bonus,
        'rating_bonus_reason': rating_reason,
        'activity_penalty': activity_penalty,
        'activity_penalty_reason': activity_reason,
        # 새 breakdown
        'breakdown': {
            'base': {
                '권역_점수': region_score,
                '전문분야_점수': specialty_score,
                '시간대_점수': time_score,
                '기본_합계': base_score,
            },
            'bonuses': bonuses,
            'penalties': penalties,
            '보너스_합계': bonus_sum,
            '패널티_합계': penalty_sum,
            '최종_총점': total_score,
            '점수_공식': formula,
            # 검증 이슈 #1 수정: 요일 검증 결과 노출
            '요일_검증': weekday_info,
        },
    }


# ──────────────────────── 정렬 키 ─────────────────────────────────────

def _sort_key(item: dict) -> tuple:
    """
    동점자 정렬 기준
    1순위: 총점 내림차순
    2순위: 평점 내림차순
    3순위: 누적 강의 횟수 내림차순
    """
    inst = item['instructor']
    return (
        -item['total_score'],
        -(inst.avg_rating or 0.0),
        -(inst.total_classes or 0),
    )


# ──────────────── 자동 제외 필터 (C-1, D-3) ───────────────────────────

def _is_excluded_by_load(instructor: Instructor, context: dict) -> bool:
    """월 최대 강의 횟수 초과 강사 자동 제외 (C-1)"""
    max_m = instructor.max_classes_month or 0
    if max_m <= 0:
        return False
    cnt = context['month_match_counts'].get(instructor.id, 0)
    return cnt >= max_m


def _is_excluded_by_schedule(
    instructor: Instructor, request: EducationRequest,
) -> bool:
    """정기 강의 요청 시 이미 확정된 일정과 충돌하는 강사 자동 제외 (D-3)"""
    if not _is_regular_request(request):
        return False
    return _has_date_conflict(instructor, request)


# 검증 이슈 #1 수정: 요일 불일치 강사 자동 제외
def _is_excluded_by_weekday(
    instructor: Instructor, request: EducationRequest,
) -> tuple[bool, str]:
    """
    요청 요일과 강사 가능 요일이 호환되지 않으면 매칭 후보에서 완전 제외.

    반환: (제외여부, 사유)
    """
    weekdays = _extract_requested_weekdays(request.preferred_dates)
    ok, reason = _is_weekday_compatible(instructor, weekdays)
    if ok:
        return False, ''
    return True, reason


# ─────────────── v4-D: 매칭 실패 원인 분석 ─────────────────────────────

# 실패 원인 코드 → 사람 친화 메시지
FAILURE_REASON_MESSAGES = {
    'no_region': '해당 권역(인접 권역 포함) 강사 없음',
    'no_specialty': '전문분야 일치 강사 없음',
    'no_cert': '전문분야 강의 가능 인증 등급 강사 없음',
    'no_time': '시간대 조건 맞는 강사 없음',
    'all_overloaded': '모든 강사 이번 달 강의 횟수 초과',
    'no_active': '활동 중인 강사가 없음',
}


def _analyze_failure_reasons(
    request: EducationRequest,
    all_active: list[Instructor],
    candidate_pool: list[Instructor],
    result_count: int,
    top_n: int,
) -> list[dict]:
    """
    매칭 결과가 top_n 미만일 때 구체적인 실패 원인 산출.

    반환: [{"code": str, "message": str}, ...] (중복 없음)
    원인 진단 우선순위:
      1. 활동 강사 0명
      2. 모든 강사 부하초과 → 후보 풀이 비어있고 all_active 는 있음
      3. 권역/분야/인증/시간 각 항목별 매칭 가능 강사 수가 0
    """
    reasons: list[dict] = []

    def _add(code: str, override_msg: str | None = None):
        if any(r['code'] == code for r in reasons):
            return
        reasons.append({
            'code': code,
            'message': override_msg or FAILURE_REASON_MESSAGES.get(code, code),
        })

    if not all_active:
        _add('no_active')
        return reasons

    # 부하초과로 모두 제외된 경우
    if not candidate_pool:
        _add('all_overloaded')
        return reasons

    specialty = request.specialty_needed
    request_region = request.organization.region if request.organization else None
    preferred_times = set(request.preferred_times or [])

    # 권역 조건 (정확 일치 또는 인접)
    if request_region:
        region_ok = [
            i for i in candidate_pool
            if i.region == request_region or are_adjacent(i.region, request_region)
            or (request_region in (i.travel_range or []))
        ]
        if not region_ok:
            _add('no_region')

    # 전문분야 (유사분야 포함)
    if specialty:
        group = set(_get_group_specialties(specialty))
        specialty_ok = [
            i for i in candidate_pool
            if specialty in (i.specialties or [])
            or set(i.specialties or []) & group
        ]
        if not specialty_ok:
            _add('no_specialty')

        # 인증 등급으로 가능한 강사 0명
        cert_ok = [i for i in candidate_pool if _is_cert_eligible(i, specialty)]
        if not cert_ok:
            _add('no_cert')

    # 시간대
    if preferred_times:
        time_ok = [
            i for i in candidate_pool
            if set(i.available_times or []) & preferred_times
        ]
        if not time_ok:
            _add('no_time')

    # 결과는 있지만 부족한 경우 → 일반적 부족 사유 추가
    if not reasons and result_count < top_n:
        _add('no_specialty', f'조건 부합 강사가 {result_count}명으로 {top_n}명 미만')

    return reasons



# ──────────── E항: 신규 강사 노출 보장 ────────────────────────────────

def _pick_newcomer_slot(
    candidate_pool: list[Instructor],
    request: EducationRequest,
    context: dict,
    excluded_ids: set[int],
    score_cache: dict[int, dict] | None = None,
) -> dict | None:
    """
    신규 강사 6번째 슬롯 선정 (시나리오 이슈 #3).

    top 5 매칭과는 별개로 항상 신규 강사 1명을 추가 노출하기 위한 슬롯.
    조건:
      - total_classes < 10 (NEW_INSTRUCTOR_THRESHOLD)
      - base_score > 0 (권역/분야/시간 중 하나 이상 매칭)
      - is_active = True (candidate_pool 이 이미 만족)
      - top 5 에 이미 포함되지 않은 강사

    성능: score_cache 에 이미 계산된 점수가 있으면 재사용하여 중복 계산 회피.

    반환:
      - 가장 점수 높은 신규 강사 후보 dict (calculate_match_score 결과 + reason)
      - 후보 없으면 None
    """
    score_cache = score_cache or {}
    candidates: list[dict] = []
    for inst in candidate_pool:
        if inst.id in excluded_ids:
            continue
        if not _is_new_instructor(inst):
            continue
        # 이미 계산된 점수가 있으면 재사용, 없으면 새로 계산
        scored = score_cache.get(inst.id)
        if scored is None:
            scored = calculate_match_score(inst, request, context)
        # base_score 0 또는 음수 총점은 매칭과 무관 → 제외
        if scored['base_score'] <= 0:
            continue
        if scored['total_score'] <= 0:
            continue
        candidates.append(scored)

    if not candidates:
        return None

    candidates.sort(key=_sort_key)
    best = candidates[0]
    inst = best['instructor']
    best['newcomer_reason'] = (
        f'신규 강사 노출 보장 — 누적 강의 {inst.total_classes or 0}회, '
        f'권역 {best["region_score"]:.0f}+분야 {best["specialty_score"]:.0f}'
        f'+시간 {best["time_score"]:.0f}={best["base_score"]:.0f}점 기본 매칭'
    )
    return best


def _ensure_new_instructor_slot(
    scored_sorted: list[dict], top_n: int,
) -> tuple[list[dict], bool]:
    """
    결과 top_n 중 신규 강사가 최소 1명 포함되도록 보정 (E-1).
    이미 포함되어 있으면 그대로 반환.
    신규 강사 후보가 없거나 매칭 자체와 무관(base_score=0)이면 그대로 반환.
    보정한 경우 가장 점수 낮은 비신규 강사를 제거하고 최고 점수 신규 강사를 추가.
    반환값: (보정된 리스트, 신규강사_삽입_여부)
    """
    top = scored_sorted[:top_n]
    has_new = any(_is_new_instructor(s['instructor']) for s in top)
    if has_new:
        return top, False

    # base_score(권역+분야+시간) 가 0 이면 매칭과 무관한 강사 → +20 보너스만으로
    # 강제 삽입되는 것을 막기 위해 base_score > 0 조건 적용
    new_candidates = [
        s for s in scored_sorted
        if _is_new_instructor(s['instructor'])
        and s['total_score'] > 0
        and s['base_score'] > 0
    ]
    if not new_candidates:
        return top, False

    best_new = new_candidates[0]  # scored_sorted 이미 정렬됨
    # top 의 마지막 자리(점수 가장 낮음) 를 신규 강사로 교체
    if len(top) >= top_n:
        replaced = top[:-1] + [best_new]
    else:
        replaced = top + [best_new]
    replaced.sort(key=_sort_key)
    return replaced, True


# ──────────────────────── 상위 매칭 탐색 ──────────────────────────────

def find_top_matches(request_id: int, top_n: int = 5) -> dict | None:
    """
    교육 요청 ID를 받아 상위 N명 강사를 매칭 후 matches 테이블에 저장.

    반환 구조:
    {
        'matches': [ ...각 매칭 dict (breakdown 포함)... ],
        'match_mode': '정상' | '인접권역추천' | '유사분야확장'
                       | '조건완화추천' | '최선추천' | '강사없음',
        'match_mode_reason': str,
        'total_count': int,
        'auto_excluded': [...],  # 자동 제외된 강사 사유
    }
    """
    request = db.session.get(EducationRequest, request_id)
    if not request:
        return None

    specialty = request.specialty_needed
    request_region = request.organization.region if request.organization else None

    # ── is_active=False 강사 완전 제외 ────────────────────────────
    all_active = Instructor.query.filter_by(is_active=True).all()
    if not all_active:
        failure_reasons = [{
            'code': 'no_active',
            'message': FAILURE_REASON_MESSAGES['no_active'],
        }]
        request.failure_reasons = failure_reasons
        db.session.commit()
        return {
            'matches': [],
            'match_mode': '강사없음',
            'match_mode_reason': '등록된 활성 강사가 없습니다.',
            'total_count': 0,
            'auto_excluded': [],
            'failure_reasons': failure_reasons,
        }

    # ── 스코어링 컨텍스트 빌드 (이번 달 매칭 통계) ─────────────────
    context = _build_scoring_context(all_active)

    # ── 자동 제외 (C-1 월최대 초과, D-3 일정 충돌, 검증 #1 요일 불일치) ──
    auto_excluded: list[dict] = []
    candidate_pool: list[Instructor] = []
    for inst in all_active:
        if _is_excluded_by_load(inst, context):
            cnt = context['month_match_counts'].get(inst.id, 0)
            auto_excluded.append({
                'instructor_id': inst.id,
                'instructor_name': inst.name,
                '사유': (
                    f'이번 달 매칭 {cnt}/{inst.max_classes_month}회 '
                    f'(최대치 도달 → 자동 제외)'
                ),
            })
            continue
        if _is_excluded_by_schedule(inst, request):
            auto_excluded.append({
                'instructor_id': inst.id,
                'instructor_name': inst.name,
                '사유': '정기 강의 일정 충돌 (자동 제외)',
            })
            continue
        # 검증 이슈 #1 수정: 요일 불일치 자동 제외 (강의 진행 자체 불가)
        excl, reason = _is_excluded_by_weekday(inst, request)
        if excl:
            auto_excluded.append({
                'instructor_id': inst.id,
                'instructor_name': inst.name,
                '사유': f'요일 불일치 (자동 제외) — {reason}',
            })
            continue
        candidate_pool.append(inst)

    match_mode = '정상'
    match_mode_reason = '정상 매칭'
    result_scored: list[dict] = []

    # ── 인증 등급 필터 → 점수 계산 → 0점 이하 제외 ─────────────────
    cert_eligible = [i for i in candidate_pool if _is_cert_eligible(i, specialty)]

    # 성능: 강사 id → 점수 dict 캐시. 후속 단계(_pick_newcomer_slot 등) 에서 재사용.
    score_cache: dict[int, dict] = {}
    if cert_eligible:
        scored_all = [calculate_match_score(i, request, context) for i in cert_eligible]
        for s in scored_all:
            score_cache[s['instructor'].id] = s
        scored_positive = [s for s in scored_all if s['total_score'] > 0]
        scored_positive.sort(key=_sort_key)
        # top_n 보다 1명 더 뽑아둠 (E 보정용 풀)
        result_scored = scored_positive[:max(top_n * 2, top_n + 3)]

    # ── 인접 권역 자동 탐색 표시 ────────────────────────────────
    if request_region:
        same_region_count = sum(1 for i in candidate_pool if i.region == request_region)
        if same_region_count == 0 and result_scored:
            match_mode = '인접권역추천'
            match_mode_reason = (
                f'{request_region}에 소속 강사가 없어 인접 권역 강사를 자동 탐색했습니다.'
            )

    # ── 전문분야 강사 0명 → 유사분야 자동 확장 표시 ──────────────
    exact_specialty_count = sum(
        1 for i in cert_eligible if specialty in (i.specialties or [])
    )
    if exact_specialty_count == 0 and result_scored and match_mode == '정상':
        match_mode = '유사분야확장'
        match_mode_reason = (
            f"'{specialty}' 강사가 없어 유사 분야로 자동 확장하여 탐색했습니다."
        )

    # ── 5명 미만 → 전문분야 조건 완화, 유사분야 강사 추가 ───────
    if 0 < len(result_scored) < top_n:
        existing_ids = {s['instructor'].id for s in result_scored}

        relaxed_candidates: list[dict] = []
        for inst in candidate_pool:
            if inst.id in existing_ids:
                continue
            if not _is_cert_eligible_for_similar(inst, specialty):
                continue
            # 이미 계산된 점수 재사용
            s = score_cache.get(inst.id)
            if s is None:
                s = calculate_match_score(inst, request, context)
                score_cache[inst.id] = s
            if s['specialty_score'] >= 20 and s['total_score'] > 0:
                s['match_type_override'] = '조건완화추천'
                relaxed_candidates.append(s)

        relaxed_candidates.sort(key=_sort_key)
        needed = top_n - len(result_scored)
        added = relaxed_candidates[:needed]
        result_scored.extend(added)

        if added:
            match_mode = '조건완화추천'
            match_mode_reason = (
                f'매칭 가능 강사가 {len(result_scored) - len(added)}명으로 '
                f'{top_n}명 미만이어서 유사 분야 강사를 추가 추천합니다.'
            )

    # ── 결과 0명 → 평점 순 3명 '최선 추천' ─────────────────────
    if not result_scored:
        best_3 = sorted(
            candidate_pool,
            key=lambda x: (-(x.avg_rating or 0.0), -(x.total_classes or 0)),
        )[:3]
        result_scored = [
            {**calculate_match_score(i, request, context),
             'match_type_override': '최선추천'}
            for i in best_3
        ]
        match_mode = '최선추천'
        match_mode_reason = (
            '모든 조건에 부합하는 강사가 없어 평점 기준 상위 강사를 추천합니다.'
        )

    # ── E항: 신규 강사 1명 보장 (최선추천 모드 제외) ──────────────
    new_inserted = False
    if match_mode != '최선추천' and len(result_scored) > 0:
        result_scored, new_inserted = _ensure_new_instructor_slot(result_scored, top_n)
    else:
        result_scored = result_scored[:top_n]

    # ── DB 저장 ──────────────────────────────────────────────────
    Match.query.filter_by(request_id=request_id).delete()

    saved_pairs: list[tuple[Match, dict]] = []
    for item in result_scored:
        # 신규 강사 보장으로 삽입된 경우 match_type 표기
        if new_inserted and _is_new_instructor(item['instructor']) and \
                'match_type_override' not in item:
            match_type = '신규강사보장'
        else:
            match_type = item.get('match_type_override', '정상')
        m = Match(
            request_id=request_id,
            instructor_id=item['instructor'].id,
            match_score=item['total_score'],
            region_score=item['region_score'],
            specialty_score=item['specialty_score'],
            time_score=item['time_score'],
            rating_bonus=item['rating_bonus'],
            activity_penalty=item['activity_penalty'],
            match_type=match_type,
            status='매칭제안',
            created_at=datetime.utcnow(),
        )
        db.session.add(m)
        saved_pairs.append((m, item))

    request.status = '완료'

    # v4-D: top_n 미만 결과일 때 실패 원인 저장
    failure_reasons: list[dict] = []
    if len(saved_pairs) < top_n:
        failure_reasons = _analyze_failure_reasons(
            request, all_active, candidate_pool, len(saved_pairs), top_n,
        )
    # 결과가 5명 이상이면 이전에 저장된 실패 원인 초기화
    request.failure_reasons = failure_reasons or None

    db.session.commit()

    # v5.0: ML 학습용 로그 기록 (추천된 후보 전체 + 피처 스냅샷)
    # 로깅 실패가 매칭 자체를 막지 않도록 import를 함수 내부에서 수행
    from app.services.ml_logger import log_match_candidates
    log_match_candidates(request, result_scored, engine_version='rule_based_v4')

    # ── 응답 구성 ────────────────────────────────────────────────
    result_list = []
    for m, item in saved_pairs:
        d = m.to_dict()
        d['breakdown'] = item['breakdown']
        # 기존 score_detail (역호환)
        d['score_detail'] = {
            '권역_점수': item['region_score'],
            '전문분야_점수': item['specialty_score'],
            '시간대_점수': item['time_score'],
            '기본_합계': item['base_score'],
            '평점_보너스': item['rating_bonus'],
            '평점_보너스_사유': item['rating_bonus_reason'],
            '활동일_패널티': -item['activity_penalty'],
            '활동일_패널티_사유': item['activity_penalty_reason'],
            '최종_총점': item['total_score'],
            '점수_공식': item['breakdown']['점수_공식'],
        }
        result_list.append(d)

    # ── 시나리오 이슈 #3: 6번째 슬롯 — 신규 강사 항상 노출 ─────────────
    # top 5 매칭과는 별개로 신규 강사 1명을 추가 노출.
    # top 5 에 이미 포함된 강사는 제외하고, 가장 점수 높은 신규 강사를 선정.
    top_ids = {item['instructor'].id for item in result_scored}
    newcomer_item = _pick_newcomer_slot(
        candidate_pool, request, context, top_ids, score_cache=score_cache,
    )
    newcomer_slot = None
    if newcomer_item is not None:
        inst = newcomer_item['instructor']
        newcomer_slot = {
            'instructor_id': inst.id,
            'instructor_name': inst.name,
            'instructor_region': inst.region,
            'instructor_specialties': inst.specialties,
            'instructor_cert_level': inst.cert_level,
            'instructor_avg_rating': inst.avg_rating,
            'instructor_total_classes': inst.total_classes,
            'match_score': newcomer_item['total_score'],
            'region_score': newcomer_item['region_score'],
            'specialty_score': newcomer_item['specialty_score'],
            'time_score': newcomer_item['time_score'],
            'base_score': newcomer_item['base_score'],
            'breakdown': newcomer_item['breakdown'],
            'exposure_reason': newcomer_item['newcomer_reason'],
            'slot_type': '신규강사슬롯',
        }

    return {
        'matches': result_list,
        'match_mode': match_mode,
        'match_mode_reason': match_mode_reason,
        'total_count': len(result_list),
        'auto_excluded': auto_excluded,
        # v4-D: 5명 미만 매칭 시 실패 원인. 충분히 매칭됐을 땐 빈 리스트.
        'failure_reasons': failure_reasons,
        # 시나리오 이슈 #3: 6번째 슬롯 — 신규 강사 항상 노출 (없으면 None)
        'newcomer_slot': newcomer_slot,
    }

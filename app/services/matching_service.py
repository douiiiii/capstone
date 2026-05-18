"""
화성시 AI 시민리더 허브 - 매칭 알고리즘 서비스 v3.0

v2.0 대비 추가 사항:
  A) 피드백 반영      : 수요처 만족도/재요청/누적 나쁜평가 점수화
  B) 수요처 맞춤 추천 : 기관 유형별 가중치, 과거 매칭 이력 보너스
  C) 강사 부하 분산   : 월 최대 강의 횟수 초과/80% 패널티/쏠림 방지
  D) 연속 강의 매칭   : 정기 강의 우선 배정, 일정 충돌 자동 제외
  E) 신규 강사 노출   : 누적 5회 미만 강사 +20점 보너스 + 결과 1명 보장
"""
from datetime import datetime, date

from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
from app.models.match import Match
from app.services.region_service import are_adjacent

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
#   - '전문가' 또는 미설정: None (모든 분야 가능)
#   - '중급': 기초 분야 + 챗GPT, 데이터분석, 코딩교육
#   - '기초': AI기초, 스마트폰활용만 가능
# ─────────────────────────────────────────────────────────
CERT_ALLOWED_SPECIALTIES: dict[str, set[str]] = {
    '기초': {'AI기초', '스마트폰활용'},
    '중급': {'AI기초', '스마트폰활용', '챗GPT', '데이터분석', '코딩교육'},
}

# ─────────────────────────────────────────────────────────
# 확정으로 간주할 매칭 상태
# (이번 달 강의 횟수·일정 충돌 계산 시 사용)
# ─────────────────────────────────────────────────────────
CONFIRMED_MATCH_STATUSES = ('수락', '확정', '완료')

# ─────────────────────────────────────────────────────────
# 신규 강사 기준 (누적 강의 횟수)
# ─────────────────────────────────────────────────────────
NEW_INSTRUCTOR_THRESHOLD = 5


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
    """누적 강의 5회 미만이면 신규 강사"""
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


# ─────────────────────── 기본 점수 계산 함수 ──────────────────────────

def _calc_region_score(instructor: Instructor, request_region: str | None) -> float:
    """권역 점수 (최대 40점)"""
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

def _calc_satisfaction_bonus(instructor: Instructor) -> tuple[float, str]:
    """
    수요처 만족도 평가 점수 반영 (A-1)
    - 평균 만족도 4.5 이상 : +10점
    - 평균 만족도 3.0 미만 : -10점 (패널티)
    - 그 외 / 이력 없음    : 0점
    반환값은 (점수, 사유) — 점수는 부호 그대로 (양수=보너스, 음수=패널티)
    """
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
) -> tuple[float, str]:
    """
    같은 강사 재요청 횟수 점수화 (A-2)
    - 같은 기관에서 이전에 매칭된 횟수 3회 이상: +15점
    - 같은 기관에서 이전에 매칭된 횟수 1~2회 : +7점
    """
    if not request.org_id:
        return 0.0, ''
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


def _calc_bad_rating_penalty(instructor: Instructor) -> tuple[float, str]:
    """
    누적 나쁜 평가 패널티 (A-3)
    - 3.0 미만 평가가 3회 이상 누적된 강사: 자동 후순위 (-30점)
    """
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
        if instructor.cert_level == '전문가':
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
) -> tuple[float, str]:
    """
    수요처 과거 매칭 이력 기반 보너스 (B-2)
    - 같은 기관에서 이전에 매칭된 적이 있으면 +5점
    """
    if not request.org_id:
        return 0.0, ''
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
    """이번 달 확정/수락 상태 매칭 횟수"""
    return (
        Match.query
        .filter(
            Match.instructor_id == instructor.id,
            Match.status.in_(CONFIRMED_MATCH_STATUSES),
            db.extract('year', Match.created_at) == today.year,
            db.extract('month', Match.created_at) == today.month,
        )
        .count()
    )


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
    이미 확정된 매칭 일정과 충돌하는지 (D-3)
    confirmed 상태인 다른 요청의 preferred_dates 와 교집합이 있으면 True
    """
    req_dates = set(request.preferred_dates or [])
    if not req_dates:
        return False
    confirmed = (
        Match.query
        .filter(
            Match.instructor_id == instructor.id,
            Match.status.in_(CONFIRMED_MATCH_STATUSES),
            Match.request_id != request.id,
        )
        .all()
    )
    for m in confirmed:
        other = m.request
        if other and other.preferred_dates:
            if req_dates & set(other.preferred_dates):
                return True
    return False


# ─────────────── E항: 신규 강사 노출 보장 ─────────────────────────────

def _calc_new_instructor_bonus(instructor: Instructor) -> tuple[float, str]:
    """신규 강사(누적 5회 미만) +20점 (E-2)"""
    if _is_new_instructor(instructor):
        return 20.0, (
            f'신규 강사 (누적 강의 {instructor.total_classes or 0}회) → +20점'
        )
    return 0.0, ''


# ─────────────────── 스코어링 컨텍스트 빌더 ────────────────────────────

def _build_scoring_context(active_instructors: list[Instructor]) -> dict:
    """
    매칭 시 반복 조회되는 데이터를 미리 캐싱.
    - month_match_counts : 강사별 이번 달 확정 매칭 수
    - most_matched_ids   : 이번 달 매칭이 가장 많은 강사 ID 집합
    """
    today = date.today()
    counts: dict[int, int] = {}
    for inst in active_instructors:
        counts[inst.id] = _count_this_month_confirmed(inst, today)

    max_count = max(counts.values()) if counts else 0
    most_matched_ids = (
        {iid for iid, c in counts.items() if c == max_count}
        if max_count > 0 else set()
    )
    return {
        'today': today,
        'month_match_counts': counts,
        'most_matched_ids': most_matched_ids,
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
    sat_value, sat_reason = _calc_satisfaction_bonus(instructor)
    _add('만족도 보너스/패널티', sat_value, sat_reason)

    # A-2 재요청
    rerequest_value, rerequest_reason = _calc_rerequest_bonus(instructor, request)
    _add('재요청 보너스', rerequest_value, rerequest_reason)

    # A-3 나쁜 평가 누적
    bad_value, bad_reason = _calc_bad_rating_penalty(instructor)
    _add('누적 나쁜평가 패널티', bad_value, bad_reason)

    # B-1 기관 유형 가중치
    org_value, org_reason = _calc_org_type_bonus(instructor, organization)
    _add('기관 유형 보너스', org_value, org_reason)

    # B-2 과거 매칭 이력
    prior_value, prior_reason = _calc_prior_match_bonus(instructor, request)
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


# ──────────── E항: 신규 강사 노출 보장 ────────────────────────────────

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
        return {
            'matches': [],
            'match_mode': '강사없음',
            'match_mode_reason': '등록된 활성 강사가 없습니다.',
            'total_count': 0,
            'auto_excluded': [],
        }

    # ── 스코어링 컨텍스트 빌드 (이번 달 매칭 통계) ─────────────────
    context = _build_scoring_context(all_active)

    # ── 자동 제외 (C-1 월최대 초과, D-3 일정 충돌) ────────────────
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
        candidate_pool.append(inst)

    match_mode = '정상'
    match_mode_reason = '정상 매칭'
    result_scored: list[dict] = []

    # ── 인증 등급 필터 → 점수 계산 → 0점 이하 제외 ─────────────────
    cert_eligible = [i for i in candidate_pool if _is_cert_eligible(i, specialty)]

    if cert_eligible:
        scored_all = [calculate_match_score(i, request, context) for i in cert_eligible]
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
            s = calculate_match_score(inst, request, context)
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
    db.session.commit()

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

    return {
        'matches': result_list,
        'match_mode': match_mode,
        'match_mode_reason': match_mode_reason,
        'total_count': len(result_list),
        'auto_excluded': auto_excluded,
    }

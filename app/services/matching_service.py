"""
화성시 AI 시민리더 허브 - 매칭 알고리즘 서비스 v2.0

개선 사항:
  A) 정확도 향상  : 평점 보너스, 인증 등급 제한, 활동일 패널티
  B) 추천 고도화  : 상위 5명, 동점자 처리, 조건 완화 추천
  C) 예외 처리 강화: 인접 권역 탐색, 최선 추천, 유사 분야 자동 확장
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
        return True  # 전문가 → 제한 없음

    allowed = CERT_ALLOWED_SPECIALTIES[cert]
    group_specs = set(_get_group_specialties(specialty))
    inst_specs = set(instructor.specialties or [])

    # 허용된 분야 ∩ 유사분야 그룹 ∩ 강사 보유 분야 가 하나라도 있으면 OK
    return bool(allowed & group_specs & inst_specs)


# ────────────────────────── 점수 계산 함수 ────────────────────────────

def _calc_rating_bonus(instructor: Instructor) -> tuple[float, str]:
    """
    평점 보너스 계산 (A항)
    - 4.8 이상  : +10점
    - 4.5 ~ 4.7 : +5점
    - 4.5 미만  : 0점
    반환값: (보너스 점수, 사유 문자열)
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
    최근 활동일 패널티 계산 (A항)
    - 3개월 이내  : 0점 패널티
    - 3 ~ 6개월  : -5점 패널티
    - 6개월 초과 : -10점 패널티
    반환값: (패널티 점수, 사유 문자열)
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


def _calc_region_score(instructor: Instructor, request_region: str | None) -> float:
    """
    권역 점수 계산 (최대 40점)
    - 같은 권역      : 40점
    - 인접 권역      : 20점
    - 이동 가능 범위 : 10점
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


def _calc_specialty_score(instructor: Instructor, specialty_needed: str | None) -> float:
    """
    전문분야 점수 계산 (최대 40점)
    - 완전 일치          : 40점
    - 유사 분야(동일 그룹) : 20점
    """
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


# ──────────────────────── 종합 점수 계산 ──────────────────────────────

def calculate_match_score(instructor: Instructor, request: EducationRequest) -> dict:
    """
    강사와 교육 요청 사이의 종합 매칭 점수 계산.
    평점 보너스, 활동일 패널티를 포함한 상세 breakdown 반환.
    """
    request_region = request.organization.region if request.organization else None

    region_score = _calc_region_score(instructor, request_region)
    specialty_score = _calc_specialty_score(instructor, request.specialty_needed)
    time_score = _calc_time_score(instructor, request.preferred_times)
    rating_bonus, rating_reason = _calc_rating_bonus(instructor)
    activity_penalty, activity_reason = _calc_activity_penalty(instructor)

    base_score = region_score + specialty_score + time_score
    total_score = base_score + rating_bonus - activity_penalty

    return {
        'instructor': instructor,
        'total_score': total_score,
        'region_score': region_score,
        'specialty_score': specialty_score,
        'time_score': time_score,
        'base_score': base_score,
        'rating_bonus': rating_bonus,
        'rating_bonus_reason': rating_reason,
        'activity_penalty': activity_penalty,
        'activity_penalty_reason': activity_reason,
    }


def _sort_key(item: dict) -> tuple:
    """
    동점자 정렬 기준 (B항)
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


# ──────────────────────── 상위 매칭 탐색 ──────────────────────────────

def find_top_matches(request_id: int, top_n: int = 5) -> dict | None:
    """
    교육 요청 ID를 받아 상위 N명 강사를 매칭 후 matches 테이블에 저장.

    반환 구조:
    {
        'matches': [ ...각 매칭 dict (score_detail 포함)... ],
        'match_mode': '정상' | '인접권역추천' | '유사분야확장' | '조건완화추천' | '최선추천',
        'match_mode_reason': str,
        'total_count': int,
    }
    재매칭 시 기존 결과를 삭제하고 새 결과로 교체.
    """
    request = db.session.get(EducationRequest, request_id)
    if not request:
        return None

    specialty = request.specialty_needed
    request_region = request.organization.region if request.organization else None

    # ── 예외 C-3: is_active=False 강사 완전 제외 ──────────────────────
    all_active = Instructor.query.filter_by(is_active=True).all()
    if not all_active:
        return {
            'matches': [],
            'match_mode': '강사없음',
            'match_mode_reason': '등록된 활성 강사가 없습니다.',
            'total_count': 0,
        }

    match_mode = '정상'
    match_mode_reason = '정상 매칭'
    result_scored: list[dict] = []

    # ── A항: 인증 등급 필터 → 점수 계산 → 0점 이하 제외 ────────────────
    cert_eligible = [i for i in all_active if _is_cert_eligible(i, specialty)]

    if cert_eligible:
        scored = [calculate_match_score(i, request) for i in cert_eligible]
        # B항: 매칭 점수 0점 강사 제외
        scored = [s for s in scored if s['total_score'] > 0]
        # B항: 동점자 정렬 (평점 → 누적 강의 횟수)
        scored.sort(key=_sort_key)
        result_scored = scored[:top_n]

    # ── 예외 C-1: 해당 권역 강사 없을 때 → 인접 권역 자동 탐색 표시 ────
    if request_region:
        same_region_count = sum(1 for i in all_active if i.region == request_region)
        if same_region_count == 0 and result_scored:
            match_mode = '인접권역추천'
            match_mode_reason = (
                f'{request_region}에 소속 강사가 없어 인접 권역 강사를 자동 탐색했습니다.'
            )

    # ── 예외 C-4: 전문분야 강사 0명 → 유사분야 자동 확장 표시 ──────────
    exact_specialty_count = sum(
        1 for i in cert_eligible if specialty in (i.specialties or [])
    )
    if exact_specialty_count == 0 and result_scored:
        # 이미 유사분야 점수(20점)로 매칭된 결과 → 안내
        if match_mode == '정상':
            match_mode = '유사분야확장'
            match_mode_reason = (
                f"'{specialty}' 강사가 없어 유사 분야로 자동 확장하여 탐색했습니다."
            )

    # ── B항: 5명 미만 → 전문분야 조건 완화, 유사분야 강사 추가 ──────────
    if 0 < len(result_scored) < top_n:
        existing_ids = {s['instructor'].id for s in result_scored}

        relaxed_candidates: list[dict] = []
        for inst in all_active:
            if inst.id in existing_ids:
                continue
            # 유사분야에 대해 인증 등급 확인
            if not _is_cert_eligible_for_similar(inst, specialty):
                continue
            s = calculate_match_score(inst, request)
            # 유사분야 점수(20점)가 있어야 의미 있는 후보
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

    # ── 예외 C-2: 결과 0명 → 평점 순 3명 '최선 추천' ──────────────────
    if not result_scored:
        best_3 = sorted(
            all_active,
            key=lambda x: (-(x.avg_rating or 0.0), -(x.total_classes or 0)),
        )[:3]
        result_scored = [
            {**calculate_match_score(i, request), 'match_type_override': '최선추천'}
            for i in best_3
        ]
        match_mode = '최선추천'
        match_mode_reason = (
            '모든 조건에 부합하는 강사가 없어 평점 기준 상위 강사를 추천합니다.'
        )

    # ── DB 저장 ──────────────────────────────────────────────────────
    Match.query.filter_by(request_id=request_id).delete()

    saved_pairs: list[tuple[Match, dict]] = []
    for item in result_scored:
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
            status='추천',
            created_at=datetime.utcnow(),
        )
        db.session.add(m)
        saved_pairs.append((m, item))

    request.status = '매칭완료'
    db.session.commit()

    # ── 응답 구성 (점수 계산 상세 포함) ──────────────────────────────
    result_list = []
    for m, item in saved_pairs:
        d = m.to_dict()
        # 변경된 점수 계산 방식 상세 (응답 JSON에 포함)
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
            '점수_공식': (
                f"{item['region_score']} (권역)"
                f" + {item['specialty_score']} (전문분야)"
                f" + {item['time_score']} (시간대)"
                f" + {item['rating_bonus']} (평점보너스)"
                f" - {item['activity_penalty']} (활동패널티)"
                f" = {item['total_score']}"
            ),
        }
        result_list.append(d)

    return {
        'matches': result_list,
        'match_mode': match_mode,
        'match_mode_reason': match_mode_reason,
        'total_count': len(result_list),
    }

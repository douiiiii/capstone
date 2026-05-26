"""
강의 세션(ClassSession) 관련 서비스 (v5.1 신규)

핵심 책임:
  1. 매칭 확정 시 자동 세션 생성
     - 1회성 강의 → 세션 1개
     - 정기 강의   → frequency 와 duration 에 맞춰 여러 개 생성
  2. 강사별 월 세션 카운트 (부하 분산 계산 용)
  3. 강사별 시간대 충돌 검사 (날짜 + 시간 일치)
  4. 강사 누적 강의 횟수(total_classes) 재계산
"""
from datetime import date, datetime, timedelta
import re

from app.extensions import db
from app.models.class_session import ClassSession
from app.models.education_request import EducationRequest
from app.models.match import Match


# ────────────────────────────────────────────────────────────────────
# 매칭 상태 → 세션 상태 매핑
#   매칭이 '완료' 이면 세션도 '완료', 그 외 활성 상태는 '예정' 으로 생성
#   '거절'/'매칭제안' 등에서는 세션을 생성하지 않음
# ────────────────────────────────────────────────────────────────────
ACTIVE_SESSION_STATUSES = ('예정', '완료')

# 매칭 상태가 다음 집합에 속할 때만 세션을 생성/유지한다.
CONFIRMED_MATCH_STATUSES = ('수락', '확정', '완료')


# ────────────────── frequency 파싱 ─────────────────────────────────

def _parse_frequency(frequency: str | None) -> tuple[int, int]:
    """
    frequency 문자열을 (주당 횟수, 진행 주 수) 로 변환.

    지원 패턴 예:
      - '1회성', '단발'              → (1회, 0주)   ※ 세션 1개
      - '주 1회', '주1회'            → (1, 4)        ※ 1개월 = 4주 기본
      - '주 2회 × 3개월'             → (2, 12)
      - '주 3회 x 3개월'             → (3, 12)
      - '정기' (기간 미지정)          → (1, 4)        ※ 기본 1개월
      - '격주', '격주 1회'            → (1, 4)        ※ 0.5회/주 ≒ 2주에 1회 → 단순화
    """
    if not frequency:
        return 1, 0
    text = frequency.replace(' ', '')

    # 1회성/단발은 세션 1개만
    if '1회성' in text or '단발' in text or '일회성' in text or '1회만' in text:
        return 1, 0

    # 주 N회 패턴
    per_week_match = re.search(r'주(\d+)회', text)
    per_week = int(per_week_match.group(1)) if per_week_match else 1

    # N개월 또는 N주 패턴
    month_match = re.search(r'(\d+)개월', text)
    week_match = re.search(r'(\d+)주', text)
    if month_match:
        total_weeks = int(month_match.group(1)) * 4
    elif week_match:
        total_weeks = int(week_match.group(1))
    else:
        # 기간이 안 적혀있고 '정기' 키워드가 있으면 기본 1개월(4주)
        # 그 외(예: '주 1회')는 1개월(4주)
        total_weeks = 4

    # 격주면 절반으로
    if '격주' in text:
        total_weeks = max(total_weeks // 2, 1)
        per_week = 1

    return per_week, total_weeks


def _parse_date(value) -> date | None:
    """ISO 문자열 또는 date 객체를 date 로 변환. 실패 시 None."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


# ────────────────── 세션 생성 ─────────────────────────────────────

def _initial_session_status(match_status: str) -> str:
    """매칭 상태에 따른 초기 세션 상태"""
    if match_status == '완료':
        return '완료'
    return '예정'


def build_session_schedule(
    request: EducationRequest,
) -> list[tuple[date, str]]:
    """
    교육 요청으로부터 생성할 (날짜, 시간대) 목록 산출.

    규칙:
      - 1회성: 첫 preferred_date × 모든 preferred_times (없으면 1개)
      - 정기: 주 N회 × M주 → 각 주마다 N개의 시간대를 첫 preferred_date 부터 7일씩 증가
    """
    pref_dates_raw = request.preferred_dates or []
    pref_times = list(request.preferred_times or []) or ['오전']

    # 유효한 첫 날짜 (없으면 오늘)
    start_date: date | None = None
    for d in pref_dates_raw:
        parsed = _parse_date(d)
        if parsed:
            start_date = parsed
            break
    if start_date is None:
        start_date = date.today()

    per_week, total_weeks = _parse_frequency(request.frequency)

    # 1회성 (total_weeks=0): preferred_times 각각 1개씩 (기본 시간대 1개)
    if total_weeks <= 0:
        return [(start_date, pref_times[0])]

    schedule: list[tuple[date, str]] = []
    # 정기 강의: 각 주마다 per_week 개 세션 — 시간대는 preferred_times 를 순환 사용
    for week_idx in range(total_weeks):
        week_start = start_date + timedelta(days=7 * week_idx)
        for k in range(per_week):
            # 한 주 내에서는 같은 날짜의 다른 시간대 또는 다른 요일 사용
            #   - per_week 가 시간대 개수보다 많으면 다음 날짜로 분산
            if k < len(pref_times):
                schedule.append((week_start, pref_times[k]))
            else:
                # 시간대가 모자라면 다음 날로 밀고 다시 첫 시간대부터
                day_offset = k - len(pref_times) + 1
                schedule.append((week_start + timedelta(days=day_offset), pref_times[0]))

    return schedule


def create_sessions_for_match(match: Match, commit: bool = True) -> list[ClassSession]:
    """
    매칭 1건에 대해 세션 row 들을 자동 생성한다.

    이미 동일 match 의 세션이 존재하면 중복 생성하지 않고 기존 세션을 그대로 반환.
    match.status 가 CONFIRMED_MATCH_STATUSES 가 아니면 빈 리스트.
    """
    if match.status not in CONFIRMED_MATCH_STATUSES:
        return []

    # 이미 생성된 세션이 있으면 그대로 반환
    existing = ClassSession.query.filter_by(match_id=match.id).all()
    if existing:
        return existing

    request = match.request
    if request is None:
        return []

    schedule = build_session_schedule(request)
    init_status = _initial_session_status(match.status)
    sessions: list[ClassSession] = []
    for sess_date, sess_time in schedule:
        s = ClassSession(
            match_id=match.id,
            instructor_id=match.instructor_id,
            session_date=sess_date,
            session_time=sess_time,
            status=init_status,
        )
        db.session.add(s)
        sessions.append(s)

    if commit:
        db.session.commit()
    return sessions


def mark_match_sessions_completed(match: Match, commit: bool = True) -> int:
    """
    매칭 완료 시 해당 매칭의 '예정' 세션을 모두 '완료' 로 갱신.
    반환값: 갱신된 세션 수.
    """
    updated = 0
    for s in ClassSession.query.filter_by(match_id=match.id).all():
        if s.status == '예정':
            s.status = '완료'
            updated += 1
    if commit:
        db.session.commit()
    return updated


def cancel_match_sessions(match: Match, commit: bool = True) -> int:
    """
    매칭이 거절/취소된 경우 관련 세션을 '취소' 로 변경.
    반환값: 갱신된 세션 수.
    """
    updated = 0
    for s in ClassSession.query.filter_by(match_id=match.id).all():
        if s.status != '완료':
            s.status = '취소'
            updated += 1
    if commit:
        db.session.commit()
    return updated


# ────────────────── 카운트 / 충돌 검사 ────────────────────────────

def count_sessions_in_month(
    instructor_id: int,
    year: int,
    month: int,
    statuses: tuple[str, ...] = ACTIVE_SESSION_STATUSES,
) -> int:
    """강사의 특정 연·월 내 활성 세션 개수 (예정+완료)"""
    return (
        ClassSession.query
        .filter(
            ClassSession.instructor_id == instructor_id,
            ClassSession.status.in_(statuses),
            db.extract('year', ClassSession.session_date) == year,
            db.extract('month', ClassSession.session_date) == month,
        )
        .count()
    )


def has_schedule_conflict(
    instructor_id: int,
    candidate_dates: list,
    candidate_times: list,
    exclude_match_id: int | None = None,
) -> bool:
    """
    이미 잡혀있는 활성 세션과 (날짜, 시간대) 가 하나라도 겹치면 True.
    candidate_dates : ISO 문자열 또는 date 의 리스트
    candidate_times : '오전' / '오후' / '저녁' 등의 리스트
    """
    if not candidate_dates or not candidate_times:
        return False

    parsed_dates = {_parse_date(d) for d in candidate_dates}
    parsed_dates.discard(None)
    if not parsed_dates:
        return False

    times = set(candidate_times)

    query = ClassSession.query.filter(
        ClassSession.instructor_id == instructor_id,
        ClassSession.status.in_(ACTIVE_SESSION_STATUSES),
        ClassSession.session_date.in_(parsed_dates),
        ClassSession.session_time.in_(times),
    )
    if exclude_match_id is not None:
        query = query.filter(ClassSession.match_id != exclude_match_id)
    return db.session.query(query.exists()).scalar()


# ────────────────── total_classes 재계산 ──────────────────────────

def count_completed_sessions(instructor_id: int) -> int:
    """강사의 누적 '완료' 세션 개수"""
    return (
        ClassSession.query
        .filter(
            ClassSession.instructor_id == instructor_id,
            ClassSession.status == '완료',
        )
        .count()
    )


def recalculate_total_classes(instructor, commit: bool = True) -> int:
    """
    강사의 total_classes 컬럼을 완료 세션 수로 재계산.
    세션이 없으면(레거시 데이터) 기존 값 유지.
    반환값: 갱신 후의 total_classes.
    """
    completed = count_completed_sessions(instructor.id)
    # 세션이 한 건도 없을 때는 기존 컬럼 값 보존 (시드 데이터/레거시 호환)
    if completed > 0:
        instructor.total_classes = completed
        if commit:
            db.session.commit()
    return instructor.total_classes or 0

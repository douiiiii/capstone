"""
v5.1 신규 기능 테스트: class_sessions 시스템

검증 항목:
  1) 매칭 확정 시 자동 세션 생성
     · 1회성 강의 → 세션 1개
     · 정기 강의   → 주기/기간에 맞춰 세션 여러 개
  2) 시간대 충돌 검사 (세션 기반)
     · 같은 날짜 같은 시간대 → 충돌
     · 같은 날짜 다른 시간대 → 충돌 아님
  3) total_classes 세션 기반 재계산
  4) 기준치 변경 반영 확인
"""
from datetime import date, datetime, timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models.class_session import ClassSession
from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
from app.models.match import Match
from app.models.organization import Organization
from app.services.class_session_service import (
    build_session_schedule,
    count_completed_sessions,
    count_sessions_in_month,
    create_sessions_for_match,
    has_schedule_conflict,
    mark_match_sessions_completed,
    recalculate_total_classes,
)
from app.services.matching_service import (
    GRADE_UPGRADE_RULES,
    MAX_CLASSES_MONTH_MAX,
    MAX_CLASSES_MONTH_MIN,
    NEW_INSTRUCTOR_THRESHOLD,
    _has_date_conflict,
    _is_new_instructor,
    find_top_matches,
)


# ────────────────────── 픽스처 ──────────────────────────────────────

@pytest.fixture(scope='function')
def app():
    application = create_app('testing')
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def org_east(app):
    org = Organization(name='동부복지관', type='복지관', region='동부권', contact='-')
    db.session.add(org)
    db.session.commit()
    return org


def _make_instructor(
    name='강사', region='동부권', specialties=None,
    cert_level='전문가', total_classes=15, avg_rating=4.5,
    max_classes_month=None,
):
    inst = Instructor(
        name=name, region=region, travel_range=[region],
        specialties=specialties or ['AI기초'],
        cert_level=cert_level, available_days=['월'], available_times=['오전', '오후'],
        max_classes_month=max_classes_month if max_classes_month is not None else 30,
        target_audience=['성인'],
        total_classes=total_classes, avg_rating=avg_rating,
        last_active=date.today(), is_active=True,
    )
    db.session.add(inst)
    db.session.commit()
    return inst


def _make_request(
    org, specialty='AI기초', preferred_dates=None,
    preferred_times=None, frequency='주 1회', status='대기중',
):
    req = EducationRequest(
        org_id=org.id, specialty_needed=specialty, target_audience='성인',
        expected_students=10,
        preferred_dates=preferred_dates or ['2026-06-01'],
        preferred_times=preferred_times or ['오전'],
        frequency=frequency, location_type='대면', status=status,
    )
    db.session.add(req)
    db.session.commit()
    return req


def _make_match(request, instructor, status='수락'):
    """세션 자동 생성 포함 매칭 헬퍼"""
    m = Match(
        request_id=request.id, instructor_id=instructor.id,
        match_score=80.0, region_score=40.0, specialty_score=40.0,
        time_score=0.0, match_type='정상', status=status,
    )
    db.session.add(m)
    db.session.commit()
    create_sessions_for_match(m)
    return m


# ════════════════════════════════════════════════════════════════════
# 1) 자동 세션 생성
# ════════════════════════════════════════════════════════════════════

class TestSessionGeneration:
    """매칭 → class_sessions 자동 생성 검증"""

    def test_1회성_매칭_세션_1개_생성(self, app, org_east):
        inst = _make_instructor()
        req = _make_request(
            org_east, preferred_dates=['2026-07-15'],
            preferred_times=['오전'], frequency='1회성',
        )
        m = _make_match(req, inst, status='수락')
        sessions = ClassSession.query.filter_by(match_id=m.id).all()
        assert len(sessions) == 1
        assert sessions[0].session_date == date(2026, 7, 15)
        assert sessions[0].session_time == '오전'
        assert sessions[0].status == '예정'

    def test_정기_주3회_3개월_36개_세션(self, app, org_east):
        """주 3회 × 3개월 = 36개 세션"""
        inst = _make_instructor()
        req = _make_request(
            org_east, preferred_dates=['2026-07-01'],
            preferred_times=['오전', '오후', '저녁'],
            frequency='정기 주 3회 × 3개월',
        )
        m = _make_match(req, inst, status='수락')
        sessions = ClassSession.query.filter_by(match_id=m.id).all()
        assert len(sessions) == 36
        # 모두 예정 상태로 생성
        assert all(s.status == '예정' for s in sessions)
        # 시간대 분포 확인 (3가지가 골고루 나옴)
        times = {s.session_time for s in sessions}
        assert times == {'오전', '오후', '저녁'}

    def test_정기_주1회_4주_세션_4개(self, app, org_east):
        inst = _make_instructor()
        req = _make_request(
            org_east, preferred_dates=['2026-07-01'],
            preferred_times=['오전'], frequency='주 1회',
        )
        m = _make_match(req, inst, status='수락')
        sessions = (
            ClassSession.query.filter_by(match_id=m.id)
            .order_by(ClassSession.session_date).all()
        )
        assert len(sessions) == 4
        # 7일 간격
        for i in range(1, 4):
            diff = (sessions[i].session_date - sessions[i - 1].session_date).days
            assert diff == 7

    def test_매칭제안_상태는_세션_미생성(self, app, org_east):
        inst = _make_instructor()
        req = _make_request(org_east, frequency='1회성')
        m = _make_match(req, inst, status='매칭제안')
        sessions = ClassSession.query.filter_by(match_id=m.id).all()
        assert sessions == []

    def test_완료_매칭은_완료_세션으로_생성(self, app, org_east):
        inst = _make_instructor()
        req = _make_request(org_east, frequency='1회성')
        m = _make_match(req, inst, status='완료')
        sessions = ClassSession.query.filter_by(match_id=m.id).all()
        assert len(sessions) == 1
        assert sessions[0].status == '완료'

    def test_세션_중복_생성_방지(self, app, org_east):
        inst = _make_instructor()
        req = _make_request(org_east, frequency='1회성')
        m = _make_match(req, inst, status='수락')
        # 두 번 호출해도 세션은 1개만
        create_sessions_for_match(m)
        sessions = ClassSession.query.filter_by(match_id=m.id).all()
        assert len(sessions) == 1

    def test_build_schedule_격주(self, app, org_east):
        """'격주 1회 × 2개월' 같은 frequency 도 파싱"""
        req = _make_request(
            org_east, preferred_dates=['2026-07-01'],
            preferred_times=['오전'], frequency='격주 1회 × 2개월',
        )
        schedule = build_session_schedule(req)
        # 2개월(8주) ÷ 격주 → 4주
        assert len(schedule) == 4

    def test_매칭확정_API_세션_생성(self, app, org_east):
        """POST /api/match/select → 세션 자동 생성"""
        inst = _make_instructor()
        req = _make_request(
            org_east, preferred_dates=['2026-08-01'],
            preferred_times=['오전'], frequency='1회성', status='대기중',
        )
        m = Match(
            request_id=req.id, instructor_id=inst.id,
            match_score=80.0, status='매칭제안',
        )
        db.session.add(m)
        db.session.commit()

        client = app.test_client()
        res = client.post('/api/match/select', json={
            'request_id': req.id, 'instructor_id': inst.id,
        })
        assert res.status_code == 200
        sessions = ClassSession.query.filter_by(match_id=m.id).all()
        assert len(sessions) == 1


# ════════════════════════════════════════════════════════════════════
# 2) 시간대 충돌 검사
# ════════════════════════════════════════════════════════════════════

class TestScheduleConflict:
    """class_sessions 기반 시간대 충돌 검사 (v5.1)"""

    def test_같은_날짜_같은_시간대_충돌(self, app, org_east):
        inst = _make_instructor()
        # 기존 세션: 2026-08-10 오전
        past = _make_request(
            org_east, preferred_dates=['2026-08-10'],
            preferred_times=['오전'], frequency='1회성',
        )
        _make_match(past, inst, status='수락')

        # 새 요청도 2026-08-10 오전 → 충돌
        assert has_schedule_conflict(
            inst.id, ['2026-08-10'], ['오전'],
        ) is True

    def test_같은_날짜_다른_시간대_충돌아님(self, app, org_east):
        inst = _make_instructor()
        past = _make_request(
            org_east, preferred_dates=['2026-08-10'],
            preferred_times=['오전'], frequency='1회성',
        )
        _make_match(past, inst, status='수락')

        # 같은 날짜이지만 시간대 다름 → 충돌 아님
        assert has_schedule_conflict(
            inst.id, ['2026-08-10'], ['저녁'],
        ) is False

    def test_다른_날짜_충돌아님(self, app, org_east):
        inst = _make_instructor()
        past = _make_request(
            org_east, preferred_dates=['2026-08-10'],
            preferred_times=['오전'], frequency='1회성',
        )
        _make_match(past, inst, status='수락')

        assert has_schedule_conflict(
            inst.id, ['2026-08-11'], ['오전'],
        ) is False

    def test_취소된_세션은_충돌_무시(self, app, org_east):
        inst = _make_instructor()
        past = _make_request(
            org_east, preferred_dates=['2026-08-10'],
            preferred_times=['오전'], frequency='1회성',
        )
        m = _make_match(past, inst, status='수락')
        # 세션을 '취소' 로 변경
        for s in ClassSession.query.filter_by(match_id=m.id).all():
            s.status = '취소'
        db.session.commit()

        # 취소된 세션은 충돌 대상에서 제외
        assert has_schedule_conflict(
            inst.id, ['2026-08-10'], ['오전'],
        ) is False

    def test_정기_요청_시간대_충돌_시_자동_제외(self, app, org_east):
        """find_top_matches 흐름에서 시간대 충돌 강사 제외 검증"""
        inst = _make_instructor(name='충돌강사')

        # 과거 정기 매칭: 2026-06-01 오전
        past = _make_request(
            org_east, preferred_dates=['2026-06-01'],
            preferred_times=['오전'], frequency='1회성', status='완료',
        )
        _make_match(past, inst, status='수락')

        # 비교용: 충돌 없는 강사
        free = _make_instructor(name='여유강사')

        # 현재 정기 요청: 2026-06-01 오전 → 충돌
        current = _make_request(
            org_east, preferred_dates=['2026-06-01'],
            preferred_times=['오전'], frequency='정기 주 1회',
            status='대기중',
        )
        result = find_top_matches(current.id)
        ids = [m['instructor_id'] for m in result['matches']]
        assert inst.id not in ids
        assert free.id in ids
        excluded_ids = {e['instructor_id'] for e in result.get('auto_excluded', [])}
        assert inst.id in excluded_ids

    def test_정기_요청_다른_시간대_OK(self, app, org_east):
        """같은 날짜이지만 시간대 다르면 충돌 아니므로 제외 안 됨 (v5.1)"""
        inst = _make_instructor(name='시간다른강사')
        # 과거: 2026-06-01 오전 강의
        past = _make_request(
            org_east, preferred_dates=['2026-06-01'],
            preferred_times=['오전'], frequency='1회성', status='완료',
        )
        _make_match(past, inst, status='수락')

        # 현재 정기 요청: 2026-06-01 저녁 → 충돌 아님
        current = _make_request(
            org_east, preferred_dates=['2026-06-01'],
            preferred_times=['저녁'], frequency='정기 주 1회',
            status='대기중',
        )
        # 직접 검사
        assert _has_date_conflict(inst, current) is False


# ════════════════════════════════════════════════════════════════════
# 3) total_classes 세션 기반 재계산
# ════════════════════════════════════════════════════════════════════

class TestTotalClassesRecalc:
    def test_완료_세션_수로_재계산(self, app, org_east):
        inst = _make_instructor(total_classes=0)
        # 3건 완료 매칭 (frequency=1회성) → 3 완료 세션
        for _ in range(3):
            req = _make_request(org_east, frequency='1회성')
            _make_match(req, inst, status='완료')

        assert count_completed_sessions(inst.id) == 3
        recalculate_total_classes(inst)
        assert inst.total_classes == 3

    def test_예정_세션은_total_불포함(self, app, org_east):
        inst = _make_instructor(total_classes=0)
        # 예정 세션 2개
        req = _make_request(org_east, frequency='주 1회')  # 4개 예정 세션
        _make_match(req, inst, status='수락')
        recalculate_total_classes(inst)
        # 예정 상태이므로 total_classes 는 갱신되지 않음 (완료 0개)
        # recalculate_total_classes 는 완료 세션이 0이면 컬럼 보존
        assert inst.total_classes == 0

    def test_세션_없으면_컬럼값_보존(self, app):
        inst = _make_instructor(total_classes=15)
        # 세션 없는 상태에서 재계산 → 컬럼 값 유지
        recalculate_total_classes(inst)
        assert inst.total_classes == 15

    def test_완료_세션으로_표시되면_총횟수_재계산(self, app, org_east):
        """mark_match_sessions_completed + recalc 흐름 검증"""
        inst = _make_instructor(total_classes=0)
        # 정기 주 1회 (4 세션)
        req = _make_request(
            org_east, preferred_dates=['2026-09-01'],
            preferred_times=['오전'], frequency='주 1회', status='대기중',
        )
        m = _make_match(req, inst, status='수락')

        # 강의 종료 후 세션을 모두 완료 처리
        mark_match_sessions_completed(m)
        recalculate_total_classes(inst)
        # 4 세션 모두 완료로 갱신
        assert inst.total_classes == 4


# ════════════════════════════════════════════════════════════════════
# 4) 기준치 변경 (등급/월최대/신규)
# ════════════════════════════════════════════════════════════════════

class TestThresholdAdjustments:
    def test_등급_승급_기초_중급_기준_20회(self, app):
        rule = GRADE_UPGRADE_RULES['기초']
        assert rule['min_classes'] == 20
        assert rule['min_rating'] == 4.0

    def test_등급_승급_중급_전문가_기준_60회(self, app):
        rule = GRADE_UPGRADE_RULES['중급']
        assert rule['min_classes'] == 60
        assert rule['min_rating'] == 4.5

    def test_신규강사_기준_10회_미만(self, app):
        assert NEW_INSTRUCTOR_THRESHOLD == 10
        inst_a = _make_instructor(name='9회강사', total_classes=9)
        inst_b = _make_instructor(name='10회강사', total_classes=10)
        assert _is_new_instructor(inst_a) is True
        assert _is_new_instructor(inst_b) is False

    def test_월_최대_기본값_30(self, app):
        # Instructor 모델의 default 가 30
        inst = Instructor(
            name='기본강사', region='동부권', travel_range=['동부권'],
            specialties=['AI기초'], cert_level='전문가',
            available_days=['월'], available_times=['오전'],
            target_audience=['성인'], total_classes=0, avg_rating=4.5,
            last_active=date.today(), is_active=True,
        )
        db.session.add(inst)
        db.session.commit()
        assert inst.max_classes_month == 30

    def test_월_최대_설정_범위_10_40(self, app):
        # 강사가 직접 설정할 수 있는 범위 상수가 노출되어 있어야 함
        assert MAX_CLASSES_MONTH_MIN == 10
        assert MAX_CLASSES_MONTH_MAX == 40

    def test_PATCH_max_classes_정상(self, app):
        inst = _make_instructor()
        client = app.test_client()
        res = client.patch(
            f'/api/instructors/{inst.id}/max-classes',
            json={'max_classes_month': 25},
        )
        assert res.status_code == 200
        db.session.refresh(inst)
        assert inst.max_classes_month == 25

    def test_PATCH_max_classes_범위초과_거부(self, app):
        inst = _make_instructor()
        client = app.test_client()
        # 9 (하한 미만)
        res = client.patch(
            f'/api/instructors/{inst.id}/max-classes',
            json={'max_classes_month': 9},
        )
        assert res.status_code == 400
        # 41 (상한 초과)
        res = client.patch(
            f'/api/instructors/{inst.id}/max-classes',
            json={'max_classes_month': 41},
        )
        assert res.status_code == 400


# ════════════════════════════════════════════════════════════════════
# 5) 부하 분산이 세션 기반으로 동작
# ════════════════════════════════════════════════════════════════════

class TestSessionBasedLoad:
    def test_정기_강의_여러_세션이_월카운트에_반영(self, app, org_east):
        """매칭 1건이라도 정기 강의면 실제 세션 수만큼 카운트"""
        inst = _make_instructor(max_classes_month=10)
        today = date.today()
        # 이번 달 첫 주에 정기 주 3회 시작 → 첫 주 3 세션이 이번 달
        req = _make_request(
            org_east, preferred_dates=[today.replace(day=1).isoformat()],
            preferred_times=['오전', '오후', '저녁'],
            frequency='정기 주 3회 × 1개월',
        )
        _make_match(req, inst, status='수락')

        # 이번 달 세션 수: 적어도 3개 이상 (첫 주의 세션)
        cnt = count_sessions_in_month(inst.id, today.year, today.month)
        assert cnt >= 3

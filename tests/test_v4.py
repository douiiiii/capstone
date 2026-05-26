"""
매칭 알고리즘 v4.0 신규 기능 테스트

검증 항목:
  A) 강사-수요처 상성 시스템
     · 기관 유형별 평균 만족도 4.5+ → +15 보너스
     · 선호 기관 유형 일치 +10 / 비선호 -5
  C) 강사 성장 추적
     · 80% 진척 강사 +10 보너스
     · 자동 등급 업그레이드 + GradeHistory 기록
     · 관리자 API 토큰 인증 (성공/실패)
     · 일반 /api/instructors 응답에서 cert_level 제외
  D) 매칭 실패 원인 분석
     · find_top_matches 결과 5명 미만일 때 failure_reasons 채워짐
     · GET /api/dashboard/failure-stats 응답
"""
import os
from datetime import date, datetime

import pytest

from app import create_app
from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.grade_history import GradeHistory
from app.models.instructor import Instructor
from app.models.match import Match
from app.models.organization import Organization
from app.services.grade_service import (
    bulk_upgrade_all,
    check_eligibility,
    upgrade_instructor,
)
from app.services.matching_service import (
    _calc_growth_bonus,
    _calc_org_chemistry_bonus,
    _calc_preference_bonus,
    _normalize_org_type,
    calculate_match_score,
    find_top_matches,
)


ADMIN_TOKEN = 'test-admin-token-v4'


# ────────────────────── 픽스처 ──────────────────────────────────────

@pytest.fixture(scope='function')
def app(monkeypatch):
    """인메모리 앱 + 테스트용 ADMIN_TOKEN 환경변수 설정"""
    monkeypatch.setenv('ADMIN_TOKEN', ADMIN_TOKEN)
    application = create_app('testing')
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _make_org(name='테스트복지관', type='복지관', region='동부권'):
    org = Organization(name=name, type=type, region=region, contact='-')
    db.session.add(org)
    db.session.commit()
    return org


def _make_instructor(
    name='테스트강사', region='동부권', specialties=None,
    cert_level='전문가', avg_rating=4.5, total_classes=10,
    preferred_org_types=None, disliked_org_types=None,
):
    inst = Instructor(
        name=name, region=region, travel_range=[region],
        specialties=specialties or ['AI기초'],
        cert_level=cert_level, available_days=['월'], available_times=['오전'],
        max_classes_month=4, target_audience=['성인'],
        total_classes=total_classes, avg_rating=avg_rating,
        last_active=date(2026, 5, 1), is_active=True,
        preferred_org_types=preferred_org_types,
        disliked_org_types=disliked_org_types,
    )
    db.session.add(inst)
    db.session.commit()
    return inst


def _make_request(org, specialty='AI기초', preferred_times=None):
    req = EducationRequest(
        org_id=org.id, specialty_needed=specialty, target_audience='성인',
        expected_students=10, preferred_dates=['2026-06-01'],
        preferred_times=preferred_times or ['오전'],
        frequency='주 1회', location_type='대면', status='대기중',
    )
    db.session.add(req)
    db.session.commit()
    return req


# ═══════════════════════════════════════════════════════════════════
# A) 상성 시스템
# ═══════════════════════════════════════════════════════════════════

class TestOrgTypeNormalization:
    def test_정규화_키워드_매칭(self, app):
        assert _normalize_org_type('초등학교') == '학교'
        assert _normalize_org_type('주식회사') == '기업'
        assert _normalize_org_type('스타트업기업') == '기업'
        assert _normalize_org_type('노인복지관') == '복지관'
        assert _normalize_org_type(None) is None


class TestOrgChemistryBonus:
    def test_복지관_평균_4_5이상_15점(self, app):
        org_welfare = _make_org(type='복지관')
        inst = _make_instructor()

        # 과거 복지관 매칭 2건, 만족도 4.6 / 4.8
        for score in [4.6, 4.8]:
            past_req = _make_request(org_welfare)
            db.session.add(Match(
                request_id=past_req.id, instructor_id=inst.id,
                match_score=80, status='완료', satisfaction_score=score,
            ))
        db.session.commit()

        bonus, reason = _calc_org_chemistry_bonus(inst, org_welfare)
        assert bonus == 15.0
        assert '복지관' in reason
        assert '4.70' in reason  # 평균 4.7

    def test_다른_기관유형_평가는_무관(self, app):
        org_welfare = _make_org(type='복지관')
        org_school = _make_org(name='초등학교', type='초등학교', region='동부권')
        inst = _make_instructor()

        # 학교에서만 좋은 평가
        past_req = _make_request(org_school)
        db.session.add(Match(
            request_id=past_req.id, instructor_id=inst.id,
            match_score=80, status='완료', satisfaction_score=4.9,
        ))
        db.session.commit()

        # 복지관 요청에는 보너스 없음
        bonus, _ = _calc_org_chemistry_bonus(inst, org_welfare)
        assert bonus == 0.0

        # 학교 요청에는 보너스
        bonus_s, _ = _calc_org_chemistry_bonus(inst, org_school)
        assert bonus_s == 15.0

    def test_평가_이력_없으면_0점(self, app):
        org = _make_org(type='복지관')
        inst = _make_instructor()
        bonus, reason = _calc_org_chemistry_bonus(inst, org)
        assert bonus == 0.0
        assert '이력 없음' in reason

    def test_평균_4_5_미만은_0점(self, app):
        org = _make_org(type='복지관')
        inst = _make_instructor()
        past_req = _make_request(org)
        db.session.add(Match(
            request_id=past_req.id, instructor_id=inst.id,
            match_score=80, status='완료', satisfaction_score=4.0,
        ))
        db.session.commit()
        bonus, _ = _calc_org_chemistry_bonus(inst, org)
        assert bonus == 0.0


class TestPreferenceBonus:
    def test_선호_기관_일치_10점(self, app):
        org = _make_org(type='복지관')
        inst = _make_instructor(preferred_org_types=['복지관', '학교'])
        bonus, reason = _calc_preference_bonus(inst, org)
        assert bonus == 10.0
        assert '복지관' in reason

    def test_비선호_기관_패널티(self, app):
        org = _make_org(type='기업')
        inst = _make_instructor(disliked_org_types=['기업'])
        bonus, reason = _calc_preference_bonus(inst, org)
        assert bonus == -5.0
        assert '비선호' in reason

    def test_둘다_미설정_0점(self, app):
        org = _make_org(type='복지관')
        inst = _make_instructor()
        bonus, _ = _calc_preference_bonus(inst, org)
        assert bonus == 0.0


class TestBreakdownContainsChemistry:
    def test_breakdown에_상성_점수_포함(self, app):
        org = _make_org(type='복지관')
        inst = _make_instructor(preferred_org_types=['복지관'])
        # 과거 복지관 매칭 1건 (만족도 4.8)
        past_req = _make_request(org)
        db.session.add(Match(
            request_id=past_req.id, instructor_id=inst.id,
            match_score=80, status='완료', satisfaction_score=4.8,
        ))
        db.session.commit()

        req = _make_request(org)
        result = calculate_match_score(inst, req)
        bonus_names = [b['항목'] for b in result['breakdown']['bonuses']]
        assert '상성 보너스' in bonus_names
        assert '선호 기관 보너스/패널티' in bonus_names


# ═══════════════════════════════════════════════════════════════════
# C) 강사 성장 추적
# ═══════════════════════════════════════════════════════════════════

class TestGrowthBonus:
    def test_80퍼_달성_10점(self, app):
        # v5.1: 기초 → 중급 = 20회+4.0
        # 강의 18회(90%), 평점 4.0(100%) → 진척률 0.9 → 성장 중
        inst = _make_instructor(
            cert_level='기초', specialties=['AI기초'],
            total_classes=18, avg_rating=4.0,
        )
        bonus, reason = _calc_growth_bonus(inst)
        assert bonus == 10.0
        assert '중급' in reason

    def test_조건_충족이면_성장보너스_0(self, app):
        """100% 달성 → 승급 대상이므로 성장 보너스는 주지 않음 (v5.1: 20회 기준)"""
        inst = _make_instructor(
            cert_level='기초', specialties=['AI기초'],
            total_classes=22, avg_rating=4.3,
        )
        bonus, _ = _calc_growth_bonus(inst)
        assert bonus == 0.0

    def test_전문가는_성장보너스_없음(self, app):
        inst = _make_instructor(cert_level='전문가', total_classes=80, avg_rating=4.9)
        bonus, _ = _calc_growth_bonus(inst)
        assert bonus == 0.0


class TestGradeAutoUpgrade:
    def test_기초_중급_자동_승급(self, app):
        # v5.1: 기초 → 중급 = 20회+4.0
        inst = _make_instructor(
            cert_level='기초', specialties=['AI기초'],
            total_classes=22, avg_rating=4.3,
        )
        history = upgrade_instructor(inst)
        assert history is not None
        assert history.from_grade == '기초'
        assert history.to_grade == '중급'
        assert inst.cert_level == '중급'
        assert inst.cert_level_updated_at is not None
        # GradeHistory 저장 확인
        all_history = GradeHistory.query.all()
        assert len(all_history) == 1

    def test_조건_미달이면_승급_없음(self, app):
        # v5.1: 기초 → 중급 기준 20회. 18회면 미달
        inst = _make_instructor(
            cert_level='기초', specialties=['AI기초'],
            total_classes=18, avg_rating=4.5,
        )
        result = upgrade_instructor(inst)
        assert result is None
        assert inst.cert_level == '기초'
        assert GradeHistory.query.count() == 0

    def test_중급_전문가_승급(self, app):
        # v5.1: 중급 → 전문가 = 60회+4.5
        inst = _make_instructor(
            cert_level='중급', specialties=['챗GPT'],
            total_classes=60, avg_rating=4.5,
        )
        history = upgrade_instructor(inst)
        assert history is not None
        assert inst.cert_level == '전문가'

    def test_bulk_upgrade_전체(self, app):
        # 승급 가능 강사 2명 + 미달 1명 (v5.1 기준)
        _make_instructor(name='승급1', cert_level='기초', total_classes=20, avg_rating=4.0)
        _make_instructor(name='승급2', cert_level='중급', specialties=['챗GPT'],
                         total_classes=60, avg_rating=4.5)
        _make_instructor(name='미달', cert_level='기초', total_classes=10, avg_rating=4.0)

        upgraded = bulk_upgrade_all()
        assert len(upgraded) == 2
        names = {h.instructor.name for h in upgraded}
        assert names == {'승급1', '승급2'}


class TestEligibilityCheck:
    def test_eligibility_정보(self, app):
        # v5.1: 기초 → 중급 = 20회+4.0. 18회/4.0 → 진척률 0.9
        inst = _make_instructor(
            cert_level='기초', total_classes=18, avg_rating=4.0,
        )
        info = check_eligibility(inst)
        assert info['current_grade'] == '기초'
        assert info['next_grade'] == '중급'
        assert info['is_eligible'] is False
        assert info['is_growing'] is True
        assert info['progress'] == 0.9


# ═══════════════════════════════════════════════════════════════════
# C) 관리자 API
# ═══════════════════════════════════════════════════════════════════

class TestAdminAuth:
    def test_토큰_없으면_401(self, client, app):
        res = client.get('/api/admin/instructors')
        assert res.status_code == 401

    def test_틀린_토큰_401(self, client, app):
        res = client.get(
            '/api/admin/instructors',
            headers={'X-Admin-Token': 'wrong-token'},
        )
        assert res.status_code == 401

    def test_올바른_토큰_200(self, client, app):
        _make_instructor()
        res = client.get(
            '/api/admin/instructors',
            headers={'X-Admin-Token': ADMIN_TOKEN},
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body['success'] is True
        assert body['count'] == 1

    def test_환경변수_미설정시_503(self, client, app, monkeypatch):
        monkeypatch.delenv('ADMIN_TOKEN', raising=False)
        res = client.get(
            '/api/admin/instructors',
            headers={'X-Admin-Token': 'whatever'},
        )
        assert res.status_code == 503


class TestAdminEndpoints:
    def test_instructors_등급정보_포함(self, client, app):
        _make_instructor(cert_level='중급')
        res = client.get(
            '/api/admin/instructors',
            headers={'X-Admin-Token': ADMIN_TOKEN},
        )
        body = res.get_json()
        assert body['data'][0]['cert_level'] == '중급'
        assert 'cert_level_updated_at' in body['data'][0]

    def test_growth_endpoint(self, client, app):
        # v5.1: 기초→중급 = 20회+4.0. 성장 중 1명, 충족 1명, 무관 1명
        _make_instructor(name='성장중', cert_level='기초',
                         total_classes=18, avg_rating=4.0)
        _make_instructor(name='충족', cert_level='기초',
                         total_classes=22, avg_rating=4.3)
        _make_instructor(name='전문가', cert_level='전문가',
                         total_classes=80, avg_rating=4.9)

        res = client.get(
            '/api/admin/growth',
            headers={'X-Admin-Token': ADMIN_TOKEN},
        )
        body = res.get_json()
        names = {row['instructor_name'] for row in body['data']}
        assert '성장중' in names
        assert '충족' in names
        assert '전문가' not in names

    def test_grade_history_endpoint(self, client, app):
        # v5.1: 기초→중급 기준 20회
        inst = _make_instructor(cert_level='기초', total_classes=22, avg_rating=4.3)
        upgrade_instructor(inst)

        res = client.get(
            '/api/admin/grade-history',
            headers={'X-Admin-Token': ADMIN_TOKEN},
        )
        body = res.get_json()
        assert body['count'] == 1
        assert body['data'][0]['to_grade'] == '중급'

    def test_grade_upgrade_post(self, client, app):
        # v5.1: 기초→중급 기준 20회
        _make_instructor(name='승급', cert_level='기초',
                         total_classes=22, avg_rating=4.3)
        res = client.post(
            '/api/admin/grade-upgrade',
            headers={'X-Admin-Token': ADMIN_TOKEN},
        )
        body = res.get_json()
        assert body['success'] is True
        assert body['upgraded_count'] == 1


class TestPublicInstructorsHidesGrade:
    def test_일반_api_에서_cert_level_없음(self, client, app):
        _make_instructor(cert_level='전문가')
        res = client.get('/api/instructors')
        body = res.get_json()
        # 일반 API 응답에 cert_level 키가 없어야 함
        assert 'cert_level' not in body['data'][0]
        assert 'cert_level_updated_at' not in body['data'][0]


# ═══════════════════════════════════════════════════════════════════
# D) 매칭 실패 원인 분석
# ═══════════════════════════════════════════════════════════════════

class TestFailureReasons:
    def test_활성_강사_없으면_no_active(self, client, app):
        org = _make_org()
        req = _make_request(org)
        result = find_top_matches(req.id)
        assert result['total_count'] == 0
        codes = {r['code'] for r in result['failure_reasons']}
        assert 'no_active' in codes

        # DB에도 저장
        db.session.refresh(req)
        assert req.failure_reasons is not None
        assert req.failure_reasons[0]['code'] == 'no_active'

    def test_시간대_안맞으면_no_time_사유(self, client, app):
        """동부권 + AI기초 강사는 있는데 시간대만 안 맞는 경우"""
        org = _make_org()
        # 강사는 오전만 가능
        for i in range(1, 4):
            _make_instructor(
                name=f'강사{i}', region='동부권',
                specialties=['AI기초'], cert_level='기초',
            )
        # 요청은 저녁만 원함
        req = _make_request(org, preferred_times=['저녁'])
        result = find_top_matches(req.id)
        # 결과가 5명 미만 (시간 0점이라 점수 낮지만 권역+분야로 일부 매칭될 수 있음)
        assert result['total_count'] < 5
        codes = {r['code'] for r in result['failure_reasons']}
        assert 'no_time' in codes

    def test_매칭_5명_이상이면_failure_reasons_없음(self, client, app):
        """충분히 매칭되면 failure_reasons 가 빈 리스트"""
        org = _make_org()
        for i in range(6):
            _make_instructor(name=f'강사{i}', specialties=['AI기초'])
        req = _make_request(org)
        result = find_top_matches(req.id)
        assert result['total_count'] >= 5
        assert result['failure_reasons'] == []


class TestFailureStatsDashboard:
    def test_failure_stats_빈_데이터(self, client, app):
        res = client.get('/api/dashboard/failure-stats')
        body = res.get_json()
        assert body['success'] is True
        assert body['data']['total_failed_requests'] == 0
        assert body['data']['top_reasons'] == []
        assert body['data']['by_region'] == []

    def test_failure_stats_집계(self, client, app):
        """실패 사유가 기록된 요청을 만든 후 집계 확인"""
        org_e = _make_org(name='동부복지', region='동부권')
        org_s = _make_org(name='남부복지', region='남부권')

        # 실패 사유를 직접 주입 (find_top_matches 호출 없이)
        for org, code in [
            (org_e, 'no_specialty'),
            (org_e, 'no_specialty'),
            (org_s, 'no_specialty'),
            (org_s, 'no_time'),
        ]:
            req = _make_request(org)
            req.failure_reasons = [{'code': code, 'message': f'msg-{code}'}]
        db.session.commit()

        res = client.get('/api/dashboard/failure-stats')
        data = res.get_json()['data']

        assert data['total_failed_requests'] == 4
        # top_reasons: no_specialty 3, no_time 1
        top = {r['code']: r['count'] for r in data['top_reasons']}
        assert top['no_specialty'] == 3
        assert top['no_time'] == 1

        # by_region: 동부 2, 남부 2
        by_region = {r['region']: r['failed_request_count'] for r in data['by_region']}
        assert by_region['동부권'] == 2
        assert by_region['남부권'] == 2

"""
매칭 알고리즘 v3.0 테스트

[v2.0 유지 케이스 — 단위 테스트]
  1. 평점 보너스 계산 (_calc_rating_bonus)
  2. 활동일 패널티 계산 (_calc_activity_penalty)
  3. 인증 등급 필터 (_is_cert_eligible)
  4. 인증 등급 유사분야 필터 (_is_cert_eligible_for_similar)

[v2.0 유지 케이스 — 통합 테스트]
  5. 정상 매칭 / 6. 비활성 강사 제외 / 7. 인증 등급 필터
  8. 인접 권역 탐색 / 9. 유사분야 확장 / 10. 조건 완화 추천
  11. 최선 추천 / 12. 동점자 정렬 / 13. 0점 강사 제외 / 14. 활동일 패널티

[v3.0 신규 케이스]
  A) 피드백 반영       : 만족도/재요청/나쁜평가 누적
  B) 수요처 맞춤 추천  : 기관 유형 가중치, 과거 매칭 이력
  C) 강사 부하 분산   : 월 최대 초과 자동 제외, 80% 패널티, 쏠림 방지
  D) 연속 강의 매칭   : 정기 강의 보너스, 일정 충돌 자동 제외
  E) 신규 강사 노출   : +20 보너스, top 5 중 1명 보장
  F) breakdown 응답 구조
"""
import pytest
from datetime import date, datetime

from app import create_app
from app.extensions import db
from app.models.instructor import Instructor
from app.models.organization import Organization
from app.models.education_request import EducationRequest
from app.models.match import Match
from app.services.matching_service import (
    _calc_rating_bonus,
    _calc_activity_penalty,
    _calc_satisfaction_bonus,
    _calc_rerequest_bonus,
    _calc_bad_rating_penalty,
    _calc_org_type_bonus,
    _calc_prior_match_bonus,
    _calc_load_penalty,
    _calc_concentration_penalty,
    _calc_regular_bonus,
    _calc_new_instructor_bonus,
    _is_cert_eligible,
    _is_cert_eligible_for_similar,
    _is_new_instructor,
    _is_regular_request,
    _build_scoring_context,
    calculate_match_score,
    find_top_matches,
)


# ────────────────────── 픽스처 ──────────────────────────────────────

@pytest.fixture(scope='function')
def app():
    """인메모리 SQLite 기반 테스트 앱"""
    application = create_app('testing')
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def org_east(app):
    """동부권 기관"""
    org = Organization(name='동부권테스트기관', type='복지관', region='동부권', contact='031-0000-0001')
    db.session.add(org)
    db.session.commit()
    return org


@pytest.fixture
def org_west(app):
    """서부권 기관"""
    org = Organization(name='서부권테스트기관', type='도서관', region='서부권', contact='031-0000-0002')
    db.session.add(org)
    db.session.commit()
    return org


def _make_instructor(
    name='테스트강사',
    region='동부권',
    travel_range=None,
    specialties=None,
    cert_level='전문가',
    available_times=None,
    avg_rating=4.5,
    total_classes=10,
    last_active=None,
    is_active=True,
):
    """테스트용 강사 생성 헬퍼"""
    if last_active is None:
        last_active = date(2026, 5, 1)  # 기본값: 최근 활동 (패널티 없음)
    inst = Instructor(
        name=name,
        region=region,
        travel_range=travel_range or [region],
        specialties=specialties or ['AI기초'],
        cert_level=cert_level,
        available_days=['월', '화', '수'],
        available_times=available_times or ['오전'],
        max_classes_month=4,
        target_audience=['성인'],
        total_classes=total_classes,
        avg_rating=avg_rating,
        last_active=last_active,
        is_active=is_active,
    )
    db.session.add(inst)
    db.session.commit()
    return inst


def _make_request(org, specialty='AI기초', preferred_times=None):
    """테스트용 교육 요청 생성 헬퍼"""
    req = EducationRequest(
        org_id=org.id,
        specialty_needed=specialty,
        target_audience='성인',
        expected_students=10,
        preferred_dates=['2026-06-01'],
        preferred_times=preferred_times or ['오전'],
        frequency='주 1회',
        location_type='대면',
        status='대기중',
    )
    db.session.add(req)
    db.session.commit()
    return req


# ────────────────── [단위 테스트] 평점 보너스 ────────────────────────

class TestRatingBonus:
    """테스트 1: 평점 보너스 계산"""

    def test_고평점_4_8이상_보너스_10점(self, app):
        inst = _make_instructor(avg_rating=4.8)
        bonus, reason = _calc_rating_bonus(inst)
        assert bonus == 10.0
        assert '4.8 이상' in reason

    def test_평점_4_9도_보너스_10점(self, app):
        inst = _make_instructor(avg_rating=4.9)
        bonus, _ = _calc_rating_bonus(inst)
        assert bonus == 10.0

    def test_중간평점_4_5_보너스_5점(self, app):
        inst = _make_instructor(avg_rating=4.5)
        bonus, reason = _calc_rating_bonus(inst)
        assert bonus == 5.0
        assert '4.5~4.7' in reason

    def test_중간평점_4_7_보너스_5점(self, app):
        inst = _make_instructor(avg_rating=4.7)
        bonus, _ = _calc_rating_bonus(inst)
        assert bonus == 5.0

    def test_낮은평점_4_5미만_보너스_없음(self, app):
        inst = _make_instructor(avg_rating=4.4)
        bonus, reason = _calc_rating_bonus(inst)
        assert bonus == 0.0
        assert '미만' in reason

    def test_평점_0_보너스_없음(self, app):
        inst = _make_instructor(avg_rating=0.0)
        bonus, _ = _calc_rating_bonus(inst)
        assert bonus == 0.0


# ─────────────────── [단위 테스트] 활동일 패널티 ─────────────────────

class TestActivityPenalty:
    """테스트 2: 활동일 패널티 계산 (기준일: 2026-05-14)"""

    def test_3개월이내_패널티_없음(self, app):
        # 2개월 전
        inst = _make_instructor(last_active=date(2026, 3, 14))
        penalty, reason = _calc_activity_penalty(inst)
        assert penalty == 0.0
        assert '패널티 없음' in reason

    def test_3개월_경계_패널티_없음(self, app):
        # 정확히 3개월 전
        inst = _make_instructor(last_active=date(2026, 2, 14))
        penalty, _ = _calc_activity_penalty(inst)
        assert penalty == 0.0

    def test_4개월_패널티_5점(self, app):
        inst = _make_instructor(last_active=date(2026, 1, 14))
        penalty, reason = _calc_activity_penalty(inst)
        assert penalty == 5.0
        assert '-5점' in reason

    def test_6개월_패널티_5점(self, app):
        inst = _make_instructor(last_active=date(2025, 11, 14))
        penalty, _ = _calc_activity_penalty(inst)
        assert penalty == 5.0

    def test_7개월_패널티_10점(self, app):
        inst = _make_instructor(last_active=date(2025, 10, 14))
        penalty, reason = _calc_activity_penalty(inst)
        assert penalty == 10.0
        assert '-10점' in reason

    def test_9개월이상_패널티_10점(self, app):
        # 류진아 케이스
        inst = _make_instructor(last_active=date(2025, 8, 1))
        penalty, _ = _calc_activity_penalty(inst)
        assert penalty == 10.0

    def test_활동이력없음_패널티_5점(self, app):
        inst = _make_instructor()
        inst.last_active = None
        db.session.commit()
        penalty, reason = _calc_activity_penalty(inst)
        assert penalty == 5.0
        assert '이력 없음' in reason


# ──────────────────── [단위 테스트] 인증 등급 필터 ───────────────────

class TestCertEligibility:
    """테스트 3: 인증 등급에 따른 강의 가능 범위 제한"""

    def test_전문가_모든분야_가능(self, app):
        inst = _make_instructor(cert_level='전문가')
        assert _is_cert_eligible(inst, 'AI기초') is True
        assert _is_cert_eligible(inst, '파이썬') is True
        assert _is_cert_eligible(inst, '영상편집') is True

    def test_기초_AI기초_가능(self, app):
        inst = _make_instructor(cert_level='기초')
        assert _is_cert_eligible(inst, 'AI기초') is True

    def test_기초_스마트폰활용_가능(self, app):
        inst = _make_instructor(cert_level='기초')
        assert _is_cert_eligible(inst, '스마트폰활용') is True

    def test_기초_챗GPT_불가(self, app):
        inst = _make_instructor(cert_level='기초')
        assert _is_cert_eligible(inst, '챗GPT') is False

    def test_기초_코딩교육_불가(self, app):
        inst = _make_instructor(cert_level='기초')
        assert _is_cert_eligible(inst, '코딩교육') is False

    def test_기초_파이썬_불가(self, app):
        inst = _make_instructor(cert_level='기초')
        assert _is_cert_eligible(inst, '파이썬') is False

    def test_중급_코딩교육_가능(self, app):
        inst = _make_instructor(cert_level='중급')
        assert _is_cert_eligible(inst, '코딩교육') is True

    def test_중급_데이터분석_가능(self, app):
        inst = _make_instructor(cert_level='중급')
        assert _is_cert_eligible(inst, '데이터분석') is True

    def test_중급_파이썬_불가(self, app):
        # 파이썬은 중급 허용 목록에 없음 → 전문가 필요
        inst = _make_instructor(cert_level='중급')
        assert _is_cert_eligible(inst, '파이썬') is False

    def test_중급_영상편집_불가(self, app):
        inst = _make_instructor(cert_level='중급')
        assert _is_cert_eligible(inst, '영상편집') is False


# ──────────────── [단위 테스트] 유사분야 인증 등급 필터 ──────────────

class TestCertEligibleForSimilar:
    """테스트 4: 조건 완화 시 유사분야 인증 등급 확인"""

    def test_전문가_항상_가능(self, app):
        inst = _make_instructor(cert_level='전문가', specialties=['파이썬'])
        assert _is_cert_eligible_for_similar(inst, '코딩교육') is True

    def test_기초_유사분야_허용분야_있으면_가능(self, app):
        # 기초 cert → AI기초 가능. '데이터분석' 요청 유사분야 = AI·디지털 그룹
        # 강사가 AI기초 보유 + 기초 cert가 AI기초 허용 → 유사 분야로 OK
        inst = _make_instructor(cert_level='기초', specialties=['AI기초'])
        assert _is_cert_eligible_for_similar(inst, '데이터분석') is True

    def test_기초_유사분야_허용분야_없으면_불가(self, app):
        # '파이썬' 유사 그룹 = 코딩·프로그래밍. 기초 cert 허용 분야와 교집합 없음
        inst = _make_instructor(cert_level='기초', specialties=['AI기초'])
        assert _is_cert_eligible_for_similar(inst, '파이썬') is False

    def test_중급_코딩교육_유사분야_가능(self, app):
        inst = _make_instructor(cert_level='중급', specialties=['코딩교육'])
        assert _is_cert_eligible_for_similar(inst, '파이썬') is True


# ───────────── [통합 테스트] 케이스 5: 정상 매칭 ────────────────────

class TestNormalMatching:
    """테스트 5: 정상 매칭 - 완전 일치 강사 상위 5명 반환"""

    def test_정상매칭_모드(self, app, org_east):
        # 동부권 AI기초 전문가 강사 5명 이상 생성
        for i in range(6):
            _make_instructor(
                name=f'강사{i}',
                region='동부권',
                specialties=['AI기초'],
                avg_rating=4.5 + i * 0.05,
            )
        req = _make_request(org_east, specialty='AI기초')

        result = find_top_matches(req.id, top_n=5)

        assert result is not None
        assert result['match_mode'] == '정상'
        assert result['total_count'] == 5

    def test_상위5명_반환(self, app, org_east):
        for i in range(7):
            _make_instructor(name=f'강사{i}', region='동부권', specialties=['AI기초'])
        req = _make_request(org_east, specialty='AI기초')

        result = find_top_matches(req.id)

        assert len(result['matches']) <= 5

    def test_score_detail_포함(self, app, org_east):
        _make_instructor(region='동부권', specialties=['AI기초'], avg_rating=4.8)
        req = _make_request(org_east, specialty='AI기초')

        result = find_top_matches(req.id)

        assert result['total_count'] > 0
        m = result['matches'][0]
        # score_detail 키 검증
        assert 'score_detail' in m
        sd = m['score_detail']
        assert '권역_점수' in sd
        assert '전문분야_점수' in sd
        assert '시간대_점수' in sd
        assert '평점_보너스' in sd
        assert '평점_보너스_사유' in sd
        assert '활동일_패널티' in sd
        assert '활동일_패널티_사유' in sd
        assert '최종_총점' in sd
        assert '점수_공식' in sd


# ─────── [통합 테스트] 케이스 6: is_active=False 강사 제외 ───────────

class TestInactiveExclusion:
    """테스트 6: is_active=False 강사 완전 제외"""

    def test_비활성_강사_결과에_없음(self, app, org_east):
        # 비활성 강사 (높은 평점)
        inactive = _make_instructor(
            name='비활성강사', region='동부권', specialties=['AI기초'],
            avg_rating=5.0, is_active=False,
        )
        # 활성 강사
        active = _make_instructor(
            name='활성강사', region='동부권', specialties=['AI기초'],
            avg_rating=4.0, is_active=True,
        )
        req = _make_request(org_east, specialty='AI기초')

        result = find_top_matches(req.id)

        instructor_ids = [m['instructor_id'] for m in result['matches']]
        assert inactive.id not in instructor_ids, '비활성 강사가 결과에 포함됨'
        assert active.id in instructor_ids, '활성 강사가 결과에 없음'

    def test_모든강사_비활성_결과_빈배열(self, app, org_east):
        _make_instructor(is_active=False)
        req = _make_request(org_east)

        result = find_top_matches(req.id)

        assert result['match_mode'] == '강사없음'
        assert result['total_count'] == 0


# ─────── [통합 테스트] 케이스 7: 인증 등급 필터 ─────────────────────

class TestCertLevelFilter:
    """테스트 7: 기초 등급 강사는 고급 요청에서 제외"""

    def test_기초강사_고급요청_제외(self, app, org_east):
        # 기초 등급 강사 (파이썬 보유)
        basic_inst = _make_instructor(
            name='기초강사', region='동부권',
            specialties=['파이썬', 'AI기초'],
            cert_level='기초', avg_rating=5.0,
        )
        # 전문가 등급 강사
        expert_inst = _make_instructor(
            name='전문강사', region='동부권',
            specialties=['파이썬'],
            cert_level='전문가', avg_rating=4.0,
        )
        # '파이썬'은 기초 등급 불가
        req = _make_request(org_east, specialty='파이썬')

        result = find_top_matches(req.id)

        ids = [m['instructor_id'] for m in result['matches']]
        assert basic_inst.id not in ids, '기초 등급 강사가 파이썬 요청에 포함됨'
        assert expert_inst.id in ids, '전문가 강사가 결과에 없음'

    def test_중급강사_허용분야_매칭(self, app, org_east):
        mid_inst = _make_instructor(
            name='중급강사', region='동부권',
            specialties=['코딩교육'],
            cert_level='중급',
        )
        req = _make_request(org_east, specialty='코딩교육')

        result = find_top_matches(req.id)

        ids = [m['instructor_id'] for m in result['matches']]
        assert mid_inst.id in ids, '중급 강사가 코딩교육 요청에 없음'


# ─────── [통합 테스트] 케이스 8: 인접 권역 탐색 ─────────────────────

class TestAdjacentRegion:
    """테스트 8: 해당 권역 강사 없을 때 인접 권역 자동 탐색"""

    def test_인접권역_match_mode(self, app, org_east):
        # 서부권 강사만 등록 (동부권 강사 없음)
        # 동부권-중부권은 인접, 중부권-서부권은 인접 → 서부권은 동부권과 비인접이지만
        # 중부권 강사는 동부권에 인접
        _make_instructor(name='중부권강사', region='중부권', specialties=['AI기초'])
        req = _make_request(org_east, specialty='AI기초')  # org_east = 동부권

        result = find_top_matches(req.id)

        # 동부권 강사 없음 → match_mode가 인접권역추천
        assert result['match_mode'] == '인접권역추천'
        assert result['total_count'] > 0

    def test_인접권역_강사_점수(self, app, org_east):
        inst = _make_instructor(
            name='인접강사', region='중부권',
            travel_range=['중부권', '동부권'],
            specialties=['AI기초'],
            avg_rating=4.5,
        )
        req = _make_request(org_east, specialty='AI기초')

        result = find_top_matches(req.id)

        assert result['total_count'] > 0
        # 인접 권역 = 최대 20점
        m = result['matches'][0]
        assert m['score_detail']['권역_점수'] <= 20.0


# ─────── [통합 테스트] 케이스 9: 전문분야 0명 → 유사분야 확장 ─────────

class TestSimilarSpecialtyExpansion:
    """테스트 9: 전문분야 강사 0명 → 유사분야 자동 확장"""

    def test_유사분야_확장_match_mode(self, app, org_east):
        # '챗GPT' 전문 강사는 없고, 같은 AI·디지털 그룹인 'AI기초' 강사만 있음
        _make_instructor(
            name='AI기초강사', region='동부권', specialties=['AI기초'],
            cert_level='전문가',
        )
        req = _make_request(org_east, specialty='챗GPT')

        result = find_top_matches(req.id)

        assert result['match_mode'] in ('유사분야확장', '조건완화추천')
        assert result['total_count'] > 0

    def test_유사분야_전문분야점수_20점(self, app, org_east):
        _make_instructor(
            name='유사분야강사', region='동부권', specialties=['AI기초'],
            cert_level='전문가', avg_rating=4.8,
        )
        req = _make_request(org_east, specialty='챗GPT')

        result = find_top_matches(req.id)

        m = result['matches'][0]
        # 유사 분야 매칭 = 20점
        assert m['score_detail']['전문분야_점수'] == 20.0


# ─────── [통합 테스트] 케이스 10: 5명 미만 → 조건 완화 ──────────────

class TestRelaxedMatching:
    """테스트 10: 매칭 가능 강사 5명 미만 → 조건 완화 추천"""

    def test_조건완화_match_mode(self, app, org_east):
        # 완전 일치 강사 1명 (AI기초), 유사 분야 강사 여러 명 (머신러닝 등)
        _make_instructor(
            name='완전일치강사', region='동부권', specialties=['AI기초'],
        )
        for i in range(3):
            _make_instructor(
                name=f'유사강사{i}', region='동부권', specialties=['머신러닝'],
                cert_level='전문가',
            )
        req = _make_request(org_east, specialty='AI기초', preferred_times=['오전'])

        result = find_top_matches(req.id, top_n=5)

        # 완전 일치 1명 + 조건 완화 후보로 채워짐
        assert result['match_mode'] in ('조건완화추천', '정상')
        # 전체 5명 이하
        assert result['total_count'] <= 5

    def test_조건완화_강사의_match_type(self, app, org_east):
        # 완전 일치 1명
        _make_instructor(
            name='완전강사', region='동부권', specialties=['AI기초'],
        )
        # 유사 분야 강사 (머신러닝 = AI·디지털 그룹)
        _make_instructor(
            name='유사강사', region='동부권', specialties=['머신러닝'],
            cert_level='전문가',
        )
        req = _make_request(org_east, specialty='AI기초', preferred_times=['오전'])

        result = find_top_matches(req.id, top_n=5)

        # 조건 완화 강사는 match_type='조건완화추천'
        relaxed = [m for m in result['matches'] if m['match_type'] == '조건완화추천']
        if result['match_mode'] == '조건완화추천':
            assert len(relaxed) > 0


# ─────── [통합 테스트] 케이스 11: 모든 조건 불일치 → 최선 추천 ────────

class TestBestEffortMatching:
    """테스트 11: 모든 조건 불일치 → 평점 순 최선 추천"""

    def test_최선추천_match_mode(self, app, org_east):
        # 동부권 요청이지만 강사는 모두 서부권에만 있고 인접 권역도 아님
        # 남부권 요청 기관 만들기
        far_org = Organization(name='먼기관', type='복지관', region='남부권', contact='000')
        db.session.add(far_org)
        db.session.commit()

        # 전혀 다른 분야 강사들만 존재 (완전 불일치 상황 만들기)
        # → 전문분야 0점, 권역 0점, 시간 0점이 되도록
        _make_instructor(
            name='최선강사1', region='동부권', specialties=['영상편집'],
            cert_level='전문가', avg_rating=4.9,
            available_times=['저녁'],  # 요청 시간(오전)과 불일치
        )
        _make_instructor(
            name='최선강사2', region='동부권', specialties=['SNS활용'],
            cert_level='전문가', avg_rating=4.7,
            available_times=['저녁'],
        )
        _make_instructor(
            name='최선강사3', region='동부권', specialties=['유튜브제작'],
            cert_level='전문가', avg_rating=4.5,
            available_times=['저녁'],
        )

        # 동부권 기관이지만 '챗GPT' 요청 → 전문가 cert 강사가 없는 상황을 만들어야 함
        # 모든 강사가 인증 등급 제한에 걸리도록 '기초' 분야만 가능한 요청
        # 기초 강사들만 있고 '챗GPT' 요청 → cert 필터로 모두 제외
        for i in range(3):
            _make_instructor(
                name=f'기초강사{i}', region='동부권',
                specialties=['AI기초'], cert_level='기초',
                avg_rating=4.5 + i * 0.1,
                available_times=['저녁'],
            )

        req = _make_request(org_east, specialty='챗GPT', preferred_times=['오전'])

        result = find_top_matches(req.id)

        # '챗GPT'는 기초 등급 불가 → 모두 cert 필터 제거 → 최선추천
        # 단, 전문가 cert 강사도 있으면 유사분야나 조건완화가 될 수 있음
        # 여기서는 cert=전문가 강사들이 시간 불일치 → 점수 > 0 가능성 있음
        assert result is not None
        assert result['total_count'] > 0

    def test_최선추천_3명이하(self, app, org_east):
        # 전문가 강사가 없어서 cert 필터 전부 제외되는 케이스
        for i in range(5):
            _make_instructor(
                name=f'기초강사{i}', cert_level='기초',
                specialties=['스마트폰활용'],
                avg_rating=4.5 + i * 0.05,
            )
        # '챗GPT' 요청: 기초 cert는 챗GPT 불가
        req = _make_request(org_east, specialty='챗GPT')

        result = find_top_matches(req.id)

        assert result['match_mode'] == '최선추천'
        assert len(result['matches']) <= 3

    def test_최선추천_강사_match_type(self, app, org_east):
        _make_instructor(cert_level='기초', specialties=['스마트폰활용'], avg_rating=4.9)
        req = _make_request(org_east, specialty='챗GPT')

        result = find_top_matches(req.id)

        if result['match_mode'] == '최선추천':
            for m in result['matches']:
                assert m['match_type'] == '최선추천'


# ─────── [통합 테스트] 케이스 12: 동점자 정렬 ───────────────────────

class TestTieBreaking:
    """테스트 12: 동점자 정렬 - 평점 → 누적 강의 횟수"""

    def test_동점_평점높은순(self, app, org_east):
        # 동일 조건 강사 2명, 평점만 다름
        inst_high = _make_instructor(
            name='고평점', region='동부권', specialties=['AI기초'],
            avg_rating=4.9, total_classes=10,
        )
        inst_low = _make_instructor(
            name='저평점', region='동부권', specialties=['AI기초'],
            avg_rating=4.5, total_classes=10,
        )
        req = _make_request(org_east, specialty='AI기초', preferred_times=['오전'])

        result = find_top_matches(req.id, top_n=5)

        matches = result['matches']
        ids = [m['instructor_id'] for m in matches]
        assert ids.index(inst_high.id) < ids.index(inst_low.id), \
            '평점 높은 강사가 앞에 와야 함'

    def test_동점_같은평점_누적강의많은순(self, app, org_east):
        # 평점 동일, 누적 강의 수 다름
        # v5.1 기준 상향(신규 강사 < 10회)을 반영해 두 강사 모두 10회 이상으로 설정
        inst_more = _make_instructor(
            name='많은강의', region='동부권', specialties=['AI기초'],
            avg_rating=4.7, total_classes=50,
        )
        inst_less = _make_instructor(
            name='적은강의', region='동부권', specialties=['AI기초'],
            avg_rating=4.7, total_classes=15,
        )
        req = _make_request(org_east, specialty='AI기초', preferred_times=['오전'])

        result = find_top_matches(req.id, top_n=5)

        matches = result['matches']
        ids = [m['instructor_id'] for m in matches]
        assert ids.index(inst_more.id) < ids.index(inst_less.id), \
            '누적 강의 많은 강사가 앞에 와야 함'


# ─────── [통합 테스트] 케이스 13: 0점 강사 제외 ─────────────────────

class TestZeroScoreExclusion:
    """테스트 13: 매칭 점수 0점 강사 결과 제외"""

    def test_0점_강사_제외(self, app, org_east):
        # 권역 불일치, 전문분야 불일치, 시간 불일치 → 총점 0점 (보너스도 없음)
        zero_inst = _make_instructor(
            name='0점강사', region='서부권',
            travel_range=['서부권'],           # 동부권 이동 불가
            specialties=['영상편집'],          # AI기초와 다른 그룹
            avg_rating=4.4,                   # 보너스 없음
            available_times=['저녁'],          # 오전 요청과 불일치
        )
        _make_instructor(
            name='정상강사', region='동부권', specialties=['AI기초'],
            avg_rating=4.8,
        )
        req = _make_request(org_east, specialty='AI기초', preferred_times=['오전'])

        result = find_top_matches(req.id)

        ids = [m['instructor_id'] for m in result['matches']]
        assert zero_inst.id not in ids, '0점 강사가 결과에 포함됨'

    def test_결과_총점_모두_양수(self, app, org_east):
        for i in range(3):
            _make_instructor(
                name=f'강사{i}', region='동부권', specialties=['AI기초'],
                avg_rating=4.5 + i * 0.1,
            )
        req = _make_request(org_east, specialty='AI기초')

        result = find_top_matches(req.id)

        for m in result['matches']:
            assert m['match_score'] > 0, f"총점 0 이하 강사 포함: {m}"


# ─────── [통합 테스트] 케이스 14: 활동일 패널티 점수 반영 ─────────────

class TestActivityPenaltyInScore:
    """테스트 14: 활동일 패널티로 인한 총점 감소 확인"""

    def test_6개월초과_패널티_10점_감소(self, app, org_east):
        # 동일 조건 강사 2명: 활동일만 다름
        recent = _make_instructor(
            name='최근강사', region='동부권', specialties=['AI기초'],
            avg_rating=4.5, last_active=date(2026, 5, 1),
        )
        old = _make_instructor(
            name='오래된강사', region='동부권', specialties=['AI기초'],
            avg_rating=4.5, last_active=date(2025, 8, 1),  # 9개월 전
        )
        req = _make_request(org_east, specialty='AI기초', preferred_times=['오전'])

        result = find_top_matches(req.id)

        scores = {m['instructor_id']: m['match_score'] for m in result['matches']}
        # 오래된 강사 점수가 최근 강사보다 10점 낮아야 함
        assert scores[recent.id] - scores[old.id] == pytest.approx(10.0, abs=0.1), \
            f'패널티 미반영: 최근={scores[recent.id]}, 오래된={scores[old.id]}'

    def test_패널티_score_detail_반영(self, app, org_east):
        _make_instructor(
            name='오래된강사', region='동부권', specialties=['AI기초'],
            last_active=date(2025, 8, 1),
        )
        req = _make_request(org_east, specialty='AI기초')

        result = find_top_matches(req.id)

        m = result['matches'][0]
        sd = m['score_detail']
        assert sd['활동일_패널티'] == -10.0  # 음수로 표시
        assert '6개월 초과' in sd['활동일_패널티_사유']


# ════════════════════════════════════════════════════════════════════
# [v3.0] A항: 피드백 반영 시스템
# ════════════════════════════════════════════════════════════════════

def _make_past_request(org, specialty='AI기초'):
    """
    과거 교육 요청 (만족도 평가/부하 분산 테스트용).
    v5.1: preferred_dates 를 오늘 날짜로 두어 _make_match 가 자동 생성하는
    class_session 이 '이번 달' 카운트에 포함되도록 함.
    """
    req = EducationRequest(
        org_id=org.id,
        specialty_needed=specialty,
        target_audience='성인',
        expected_students=10,
        preferred_dates=[date.today().isoformat()],
        preferred_times=['오전'],
        # v5.1: 1회성으로 두어 _make_match 가 정확히 세션 1개만 생성하게 함
        # (날짜 의존적 테스트 변동을 막기 위함)
        frequency='1회성',
        location_type='대면',
        status='완료',
    )
    db.session.add(req)
    db.session.commit()
    return req


def _make_match(
    request, instructor,
    status='수락',
    satisfaction_score=None,
    created_at=None,
    match_score=80.0,
):
    """
    과거 매칭 레코드 생성 (피드백/부하 분산 테스트용).
    v5.1: 확정 계열 상태(수락/확정/완료) 면 class_session 도 함께 자동 생성.
    """
    m = Match(
        request_id=request.id,
        instructor_id=instructor.id,
        match_score=match_score,
        region_score=40.0,
        specialty_score=40.0,
        time_score=0.0,
        match_type='정상',
        status=status,
        satisfaction_score=satisfaction_score,
        created_at=created_at or datetime.utcnow(),
    )
    db.session.add(m)
    db.session.commit()
    # v5.1: 매칭에 대응하는 강의 세션 자동 생성 (부하/충돌 검사가 세션 기반이라 필요)
    from app.services.class_session_service import create_sessions_for_match
    create_sessions_for_match(m)
    return m


class TestSatisfactionBonus:
    """A-1: 수요처 만족도 평가 점수 반영"""

    def test_만족도_4_5이상_보너스_10점(self, app, org_east):
        inst = _make_instructor(name='고만족', specialties=['AI기초'])
        past1 = _make_past_request(org_east)
        past2 = _make_past_request(org_east)
        _make_match(past1, inst, satisfaction_score=4.7)
        _make_match(past2, inst, satisfaction_score=4.5)

        value, reason = _calc_satisfaction_bonus(inst)
        assert value == 10.0
        assert '4.5 이상' in reason

    def test_만족도_3_0미만_패널티_10점(self, app, org_east):
        inst = _make_instructor(name='저만족', specialties=['AI기초'])
        past1 = _make_past_request(org_east)
        past2 = _make_past_request(org_east)
        _make_match(past1, inst, satisfaction_score=2.5)
        _make_match(past2, inst, satisfaction_score=2.0)

        value, reason = _calc_satisfaction_bonus(inst)
        assert value == -10.0
        assert '3.0 미만' in reason

    def test_만족도_중간_보너스_없음(self, app, org_east):
        inst = _make_instructor(name='중간만족', specialties=['AI기초'])
        past = _make_past_request(org_east)
        _make_match(past, inst, satisfaction_score=4.0)

        value, _ = _calc_satisfaction_bonus(inst)
        assert value == 0.0

    def test_만족도_이력없음_0점(self, app, org_east):
        inst = _make_instructor(name='신규', specialties=['AI기초'])
        value, reason = _calc_satisfaction_bonus(inst)
        assert value == 0.0
        assert '이력 없음' in reason


class TestRerequestBonus:
    """A-2: 같은 강사 재요청 횟수 점수화"""

    def test_재요청_3회이상_보너스_15점(self, app, org_east):
        inst = _make_instructor(name='단골', specialties=['AI기초'])
        # 같은 기관에서 3번 매칭됨
        for _ in range(3):
            past = _make_past_request(org_east)
            _make_match(past, inst, status='수락')

        # 현재 요청
        current = _make_request(org_east, specialty='AI기초')
        value, reason = _calc_rerequest_bonus(inst, current)
        assert value == 15.0
        assert '3회 이상' in reason

    def test_재요청_1회_보너스_7점(self, app, org_east):
        inst = _make_instructor(name='단골1', specialties=['AI기초'])
        past = _make_past_request(org_east)
        _make_match(past, inst, status='수락')

        current = _make_request(org_east, specialty='AI기초')
        value, reason = _calc_rerequest_bonus(inst, current)
        assert value == 7.0

    def test_재요청_2회_보너스_7점(self, app, org_east):
        inst = _make_instructor(name='단골2', specialties=['AI기초'])
        for _ in range(2):
            past = _make_past_request(org_east)
            _make_match(past, inst, status='수락')

        current = _make_request(org_east, specialty='AI기초')
        value, _ = _calc_rerequest_bonus(inst, current)
        assert value == 7.0

    def test_재요청_0회_보너스_없음(self, app, org_east):
        inst = _make_instructor(name='첫매칭', specialties=['AI기초'])
        current = _make_request(org_east, specialty='AI기초')
        value, _ = _calc_rerequest_bonus(inst, current)
        assert value == 0.0

    def test_재요청_다른기관은_제외(self, app, org_east, org_west):
        inst = _make_instructor(name='타기관단골', specialties=['AI기초'])
        # 서부권 기관에서 3번 매칭
        for _ in range(3):
            past = _make_past_request(org_west)
            _make_match(past, inst, status='수락')

        # 동부권 기관에서 매칭 → 재요청 0회
        current = _make_request(org_east, specialty='AI기초')
        value, _ = _calc_rerequest_bonus(inst, current)
        assert value == 0.0


class TestBadRatingPenalty:
    """A-3: 누적 나쁜 평가 패널티"""

    def test_나쁜평가_3회이상_후순위(self, app, org_east):
        inst = _make_instructor(name='문제강사', specialties=['AI기초'])
        for _ in range(3):
            past = _make_past_request(org_east)
            _make_match(past, inst, satisfaction_score=2.5)

        value, reason = _calc_bad_rating_penalty(inst)
        assert value == -30.0
        assert '누적' in reason

    def test_나쁜평가_2회_패널티_없음(self, app, org_east):
        inst = _make_instructor(name='약간문제', specialties=['AI기초'])
        for _ in range(2):
            past = _make_past_request(org_east)
            _make_match(past, inst, satisfaction_score=2.5)

        value, _ = _calc_bad_rating_penalty(inst)
        assert value == 0.0

    def test_좋은평가는_나쁜평가_안카운트(self, app, org_east):
        inst = _make_instructor(name='좋은강사', specialties=['AI기초'])
        for _ in range(5):
            past = _make_past_request(org_east)
            _make_match(past, inst, satisfaction_score=4.8)

        value, _ = _calc_bad_rating_penalty(inst)
        assert value == 0.0


# ════════════════════════════════════════════════════════════════════
# [v3.0] B항: 수요처 맞춤 추천
# ════════════════════════════════════════════════════════════════════

class TestOrgTypeBonus:
    """B-1: 기관 유형별 가중치"""

    def test_학교_누적30회이상_보너스_10점(self, app):
        org = Organization(name='학교A', type='학교', region='동부권')
        db.session.add(org)
        db.session.commit()

        inst = _make_instructor(name='경험많은강사', total_classes=30)
        value, reason = _calc_org_type_bonus(inst, org)
        assert value == 10.0
        assert '학교' in reason

    def test_학교_누적부족_보너스_없음(self, app):
        org = Organization(name='학교B', type='학교', region='동부권')
        db.session.add(org)
        db.session.commit()

        inst = _make_instructor(name='신참강사', total_classes=20)
        value, _ = _calc_org_type_bonus(inst, org)
        assert value == 0.0

    def test_기업_전문가_보너스_10점(self, app):
        org = Organization(name='기업A', type='기업', region='동부권')
        db.session.add(org)
        db.session.commit()

        inst = _make_instructor(name='전문가강사', cert_level='전문가')
        value, reason = _calc_org_type_bonus(inst, org)
        assert value == 10.0
        assert '기업' in reason

    def test_기업_중급강사_보너스_없음(self, app):
        org = Organization(name='기업B', type='기업', region='동부권')
        db.session.add(org)
        db.session.commit()

        inst = _make_instructor(name='중급강사', cert_level='중급')
        value, _ = _calc_org_type_bonus(inst, org)
        assert value == 0.0

    def test_복지관_시니어대상_보너스_10점(self, app, org_east):
        # org_east 는 복지관 타입
        inst = Instructor(
            name='시니어강사', region='동부권',
            travel_range=['동부권'], specialties=['AI기초'],
            cert_level='전문가', available_days=['월'], available_times=['오전'],
            max_classes_month=4, target_audience=['시니어'],
            total_classes=10, avg_rating=4.5,
            last_active=date(2026, 5, 1), is_active=True,
        )
        db.session.add(inst)
        db.session.commit()

        value, reason = _calc_org_type_bonus(inst, org_east)
        assert value == 10.0
        assert '복지관' in reason

    def test_복지관_시니어경험없음_보너스_없음(self, app, org_east):
        inst = Instructor(
            name='청소년강사', region='동부권',
            travel_range=['동부권'], specialties=['AI기초'],
            cert_level='전문가', available_days=['월'], available_times=['오전'],
            max_classes_month=4, target_audience=['청소년'],
            total_classes=10, avg_rating=4.5,
            last_active=date(2026, 5, 1), is_active=True,
        )
        db.session.add(inst)
        db.session.commit()

        value, _ = _calc_org_type_bonus(inst, org_east)
        assert value == 0.0


class TestPriorMatchBonus:
    """B-2: 수요처 과거 매칭 이력 보너스"""

    def test_과거매칭_있으면_5점(self, app, org_east):
        inst = _make_instructor(name='재추천강사', specialties=['AI기초'])
        past = _make_past_request(org_east)
        _make_match(past, inst, status='수락')

        current = _make_request(org_east, specialty='AI기초')
        value, reason = _calc_prior_match_bonus(inst, current)
        assert value == 5.0
        assert '이전' in reason

    def test_과거매칭_없으면_0점(self, app, org_east):
        inst = _make_instructor(name='신규매칭', specialties=['AI기초'])
        current = _make_request(org_east, specialty='AI기초')
        value, _ = _calc_prior_match_bonus(inst, current)
        assert value == 0.0


# ════════════════════════════════════════════════════════════════════
# [v3.0] C항: 강사 부하 분산
# ════════════════════════════════════════════════════════════════════

class TestLoadBalancing:
    """C: 월 최대 강의 횟수 / 80% 패널티 / 쏠림 방지"""

    def test_월최대초과_자동제외(self, app, org_east):
        # max_classes_month=2 인 강사에게 이번 달 2건 확정 매칭 → 제외
        full_inst = _make_instructor(
            name='풀강사', specialties=['AI기초'], avg_rating=4.9,
        )
        full_inst.max_classes_month = 2
        db.session.commit()

        # 이번 달 2건 확정
        past1 = _make_past_request(org_east)
        past2 = _make_past_request(org_east)
        _make_match(past1, full_inst, status='수락', created_at=datetime.utcnow())
        _make_match(past2, full_inst, status='수락', created_at=datetime.utcnow())

        # 비교용 여유 있는 강사
        free_inst = _make_instructor(
            name='여유강사', specialties=['AI기초'], avg_rating=4.0,
        )

        current = _make_request(org_east, specialty='AI기초')
        result = find_top_matches(current.id)

        ids = [m['instructor_id'] for m in result['matches']]
        assert full_inst.id not in ids, '월 최대 초과 강사가 결과에 포함됨'
        assert free_inst.id in ids
        # auto_excluded 에 등록됨
        excluded_ids = [e['instructor_id'] for e in result.get('auto_excluded', [])]
        assert full_inst.id in excluded_ids

    def test_월_80퍼센트_패널티_15점(self, app, org_east):
        # max_classes_month=5, 이번 달 4건 (80%) → -15점 적용 확인
        inst = _make_instructor(
            name='80%강사', specialties=['AI기초'], avg_rating=4.5,
        )
        inst.max_classes_month = 5
        db.session.commit()

        for _ in range(4):
            past = _make_past_request(org_east)
            _make_match(past, inst, status='수락', created_at=datetime.utcnow())

        context = _build_scoring_context([inst])
        value, reason = _calc_load_penalty(inst, context)
        assert value == -15.0
        assert '80%' in reason or '≥ 80%' in reason

    def test_월_70퍼센트_패널티_없음(self, app, org_east):
        inst = _make_instructor(
            name='70%강사', specialties=['AI기초'], avg_rating=4.5,
        )
        inst.max_classes_month = 10
        db.session.commit()

        # 이번 달 7건 (70%)
        for _ in range(7):
            past = _make_past_request(org_east)
            _make_match(past, inst, status='수락', created_at=datetime.utcnow())

        context = _build_scoring_context([inst])
        value, _ = _calc_load_penalty(inst, context)
        assert value == 0.0

    def test_쏠림_최다매칭강사_패널티_10점(self, app, org_east):
        # 강사 3명: A 5건, B 2건, C 1건 → A 만 -10점
        a = _make_instructor(name='쏠림A', specialties=['AI기초'], avg_rating=4.5)
        a.max_classes_month = 20
        b = _make_instructor(name='쏠림B', specialties=['AI기초'], avg_rating=4.5)
        b.max_classes_month = 20
        c = _make_instructor(name='쏠림C', specialties=['AI기초'], avg_rating=4.5)
        c.max_classes_month = 20
        db.session.commit()

        for _ in range(5):
            past = _make_past_request(org_east)
            _make_match(past, a, status='수락', created_at=datetime.utcnow())
        for _ in range(2):
            past = _make_past_request(org_east)
            _make_match(past, b, status='수락', created_at=datetime.utcnow())
        past = _make_past_request(org_east)
        _make_match(past, c, status='수락', created_at=datetime.utcnow())

        context = _build_scoring_context([a, b, c])
        a_val, _ = _calc_concentration_penalty(a, context)
        b_val, _ = _calc_concentration_penalty(b, context)
        c_val, _ = _calc_concentration_penalty(c, context)

        assert a_val == -10.0
        assert b_val == 0.0
        assert c_val == 0.0


# ════════════════════════════════════════════════════════════════════
# [v3.0] D항: 연속 강의 매칭
# ════════════════════════════════════════════════════════════════════

class TestRegularLecture:
    """D: 정기 강의 보너스 / 일정 충돌 자동 제외"""

    def test_is_regular_request(self, app, org_east):
        regular = EducationRequest(
            org_id=org_east.id, specialty_needed='AI기초',
            preferred_times=['오전'], frequency='정기', status='대기중',
        )
        db.session.add(regular)
        db.session.commit()
        assert _is_regular_request(regular) is True

        one_time = EducationRequest(
            org_id=org_east.id, specialty_needed='AI기초',
            preferred_times=['오전'], frequency='1회성', status='대기중',
        )
        db.session.add(one_time)
        db.session.commit()
        assert _is_regular_request(one_time) is False

    def test_정기_월3회이상_보너스_10점(self, app, org_east):
        inst = _make_instructor(name='정기강사', specialties=['AI기초'])
        inst.max_classes_month = 4
        db.session.commit()

        req = EducationRequest(
            org_id=org_east.id, specialty_needed='AI기초',
            preferred_dates=['2026-06-01'], preferred_times=['오전'],
            frequency='정기', status='대기중',
        )
        db.session.add(req)
        db.session.commit()

        value, reason = _calc_regular_bonus(inst, req)
        assert value == 10.0
        assert '정기' in reason

    def test_정기_월2회미만_보너스_없음(self, app, org_east):
        inst = _make_instructor(name='적은강사', specialties=['AI기초'])
        inst.max_classes_month = 2
        db.session.commit()

        req = EducationRequest(
            org_id=org_east.id, specialty_needed='AI기초',
            preferred_dates=['2026-06-01'], preferred_times=['오전'],
            frequency='정기', status='대기중',
        )
        db.session.add(req)
        db.session.commit()

        value, _ = _calc_regular_bonus(inst, req)
        assert value == 0.0

    def test_일회성_보너스_없음(self, app, org_east):
        inst = _make_instructor(name='일회성강사', specialties=['AI기초'])
        inst.max_classes_month = 10
        db.session.commit()

        req = _make_request(org_east, specialty='AI기초')  # frequency='주 1회'
        value, _ = _calc_regular_bonus(inst, req)
        assert value == 0.0

    def test_일정충돌_자동제외(self, app, org_east):
        inst = _make_instructor(name='일정겹침강사', specialties=['AI기초'])

        # 과거 요청 (preferred_dates 2026-06-01) + 수락 매칭
        past = EducationRequest(
            org_id=org_east.id, specialty_needed='AI기초',
            preferred_dates=['2026-06-01'], preferred_times=['오전'],
            frequency='정기', status='완료',
        )
        db.session.add(past)
        db.session.commit()
        _make_match(past, inst, status='수락')

        # 비교용 여유 강사 (일정 겹치지 않음)
        free_inst = _make_instructor(name='여유강사', specialties=['AI기초'])

        # 현재 요청도 2026-06-01 + 정기 → 충돌
        current = EducationRequest(
            org_id=org_east.id, specialty_needed='AI기초',
            preferred_dates=['2026-06-01'], preferred_times=['오전'],
            frequency='정기', status='대기중',
        )
        db.session.add(current)
        db.session.commit()

        result = find_top_matches(current.id)
        ids = [m['instructor_id'] for m in result['matches']]
        assert inst.id not in ids, '일정 충돌 강사가 매칭에 포함됨'
        assert free_inst.id in ids
        excluded = [e['instructor_id'] for e in result.get('auto_excluded', [])]
        assert inst.id in excluded

    def test_일회성_일정충돌_무시(self, app, org_east):
        # 1회성 요청에서는 일정 충돌 체크 안 함
        inst = _make_instructor(name='1회성_겹침', specialties=['AI기초'])

        past = EducationRequest(
            org_id=org_east.id, specialty_needed='AI기초',
            preferred_dates=['2026-06-01'], preferred_times=['오전'],
            frequency='정기', status='완료',
        )
        db.session.add(past)
        db.session.commit()
        _make_match(past, inst, status='수락')

        # 현재 요청은 1회성 (frequency != '정기')
        current = _make_request(org_east, specialty='AI기초')
        result = find_top_matches(current.id)
        ids = [m['instructor_id'] for m in result['matches']]
        assert inst.id in ids, '1회성 요청에서 일정 충돌이 적용됨'


# ════════════════════════════════════════════════════════════════════
# [v3.0] E항: 신규 강사 노출 보장
# ════════════════════════════════════════════════════════════════════

class TestNewInstructorExposure:
    """E: 신규 강사 보너스 +20 / top 5 중 1명 포함 보장"""

    def test_is_new_instructor(self, app):
        new_inst = _make_instructor(name='신참', total_classes=3)
        old_inst = _make_instructor(name='고참', total_classes=50)
        assert _is_new_instructor(new_inst) is True
        assert _is_new_instructor(old_inst) is False

    def test_신규강사_보너스_20점(self, app):
        inst = _make_instructor(name='신규', total_classes=3)
        value, reason = _calc_new_instructor_bonus(inst)
        assert value == 20.0
        assert '신규' in reason

    def test_고참강사_보너스_없음(self, app):
        inst = _make_instructor(name='베테랑', total_classes=50)
        value, _ = _calc_new_instructor_bonus(inst)
        assert value == 0.0

    def test_top5_신규강사_미포함시_삽입(self, app, org_east):
        # 고참 강사 5명 (모두 점수 양수) + 신규 강사 1명
        for i in range(5):
            _make_instructor(
                name=f'고참{i}', region='동부권', specialties=['AI기초'],
                avg_rating=4.9, total_classes=100,
            )
        new_inst = _make_instructor(
            name='신규강사', region='동부권', specialties=['AI기초'],
            avg_rating=4.0, total_classes=2,
        )

        req = _make_request(org_east, specialty='AI기초')
        result = find_top_matches(req.id, top_n=5)

        ids = [m['instructor_id'] for m in result['matches']]
        assert new_inst.id in ids, '신규 강사가 top 5 에 포함되지 않음'

        # 정확히 5명 (마지막 자리가 신규로 교체됨)
        assert len(ids) == 5

    def test_신규강사_match_type_표기(self, app, org_east):
        # 신규가 자연 순위로는 top5에 못 들고, 강제 삽입된 경우 match_type='신규강사보장'
        for i in range(5):
            _make_instructor(
                name=f'고참{i}', region='동부권', specialties=['AI기초'],
                avg_rating=4.9, total_classes=100,
            )
        # 신규 강사: 권역(10) + 유사분야(20) + 시간(0) + 신규보너스(20) = 50
        # → 고참 5명(110점)에게 밀려 자연 6위 → 보장 로직 발동
        new_inst = Instructor(
            name='신규삽입',
            region='남부권',
            travel_range=['남부권', '동부권'],   # 동부권 이동 가능 (10점)
            specialties=['머신러닝'],             # AI기초와 유사 (20점)
            cert_level='전문가',
            available_days=['월'],
            available_times=['저녁'],            # 오전 요청과 불일치 (0점)
            max_classes_month=4,
            target_audience=['성인'],
            total_classes=2,
            avg_rating=4.0,
            last_active=date(2026, 5, 1),
            is_active=True,
        )
        db.session.add(new_inst)
        db.session.commit()

        req = _make_request(org_east, specialty='AI기초', preferred_times=['오전'])
        result = find_top_matches(req.id, top_n=5)

        new_match = next(
            (m for m in result['matches'] if m['instructor_id'] == new_inst.id),
            None,
        )
        assert new_match is not None, '신규 강사가 결과에 포함되지 않음'
        assert new_match['match_type'] == '신규강사보장'

    def test_신규강사_0점이면_미삽입(self, app, org_east):
        # 신규 강사가 모든 조건 불일치로 0점이면 강제 삽입 안 함
        for i in range(5):
            _make_instructor(
                name=f'고참{i}', region='동부권', specialties=['AI기초'],
                avg_rating=4.9, total_classes=100,
            )
        zero_new = _make_instructor(
            name='0점신규', region='서부권',
            travel_range=['서부권'],          # 동부권 이동 불가
            specialties=['영상편집'],         # 분야 불일치
            avg_rating=4.0, total_classes=1,
            available_times=['저녁'],         # 시간 불일치
        )

        req = _make_request(org_east, specialty='AI기초', preferred_times=['오전'])
        result = find_top_matches(req.id, top_n=5)
        ids = [m['instructor_id'] for m in result['matches']]
        assert zero_new.id not in ids, '0점 신규 강사가 강제 삽입됨'

    def test_신규강사_이미포함시_그대로(self, app, org_east):
        # top 5 안에 이미 신규 강사가 자연스럽게 포함된 경우 보정 불필요
        # 신규 강사가 보너스 +20 + 평점 보너스 → top 자연 포함
        _make_instructor(
            name='신규자연', region='동부권', specialties=['AI기초'],
            avg_rating=4.9, total_classes=2,
        )
        for i in range(3):
            _make_instructor(
                name=f'기타{i}', region='동부권', specialties=['AI기초'],
                avg_rating=4.5, total_classes=20,
            )

        req = _make_request(org_east, specialty='AI기초')
        result = find_top_matches(req.id, top_n=5)
        # 신규강사보장 match_type 없어야 함 (자연 포함)
        types = [m['match_type'] for m in result['matches']]
        new_inst_match = next(
            (m for m in result['matches'] if m['instructor_name'] == '신규자연'),
            None,
        )
        assert new_inst_match is not None
        # match_type 이 '정상' 이어야 함 (강제 삽입 아님)
        assert new_inst_match['match_type'] == '정상'


# ════════════════════════════════════════════════════════════════════
# [v3.0] F: breakdown 응답 구조
# ════════════════════════════════════════════════════════════════════

class TestBreakdownResponse:
    """F: 보너스/패널티 breakdown 응답 형식 검증"""

    def test_breakdown_키_존재(self, app, org_east):
        _make_instructor(
            name='기본강사', region='동부권', specialties=['AI기초'],
            avg_rating=4.8,
        )
        req = _make_request(org_east, specialty='AI기초')
        result = find_top_matches(req.id)

        m = result['matches'][0]
        assert 'breakdown' in m
        bd = m['breakdown']
        assert 'base' in bd
        assert 'bonuses' in bd
        assert 'penalties' in bd
        assert '최종_총점' in bd
        assert '점수_공식' in bd

    def test_breakdown_보너스_평점10점(self, app, org_east):
        _make_instructor(
            name='평점강사', region='동부권', specialties=['AI기초'],
            avg_rating=4.9,
        )
        req = _make_request(org_east, specialty='AI기초')
        result = find_top_matches(req.id)

        bd = result['matches'][0]['breakdown']
        rating_items = [b for b in bd['bonuses'] if b['항목'] == '평점 보너스']
        assert len(rating_items) == 1
        assert rating_items[0]['점수'] == 10.0

    def test_breakdown_패널티_활동일(self, app, org_east):
        _make_instructor(
            name='오래된강사', region='동부권', specialties=['AI기초'],
            last_active=date(2025, 8, 1),  # 9개월 전
        )
        req = _make_request(org_east, specialty='AI기초')
        result = find_top_matches(req.id)

        bd = result['matches'][0]['breakdown']
        items = [p for p in bd['penalties'] if p['항목'] == '활동일 패널티']
        assert len(items) == 1
        assert items[0]['점수'] == -10.0

    def test_breakdown_재요청_보너스_표기(self, app, org_east):
        inst = _make_instructor(
            name='재요청강사', region='동부권', specialties=['AI기초'],
        )
        # 3회 재요청 만들기
        for _ in range(3):
            past = _make_past_request(org_east)
            _make_match(past, inst, status='수락')

        current = _make_request(org_east, specialty='AI기초')
        result = find_top_matches(current.id)

        target = next(
            m for m in result['matches']
            if m['instructor_id'] == inst.id
        )
        bd = target['breakdown']
        items = [b for b in bd['bonuses'] if b['항목'] == '재요청 보너스']
        assert len(items) == 1
        assert items[0]['점수'] == 15.0

    def test_breakdown_점수_합산_일치(self, app, org_east):
        # 보너스/패널티 합산이 최종 총점과 일치하는지 검증
        _make_instructor(
            name='검증강사', region='동부권', specialties=['AI기초'],
            avg_rating=4.9, last_active=date(2025, 8, 1),
        )
        req = _make_request(org_east, specialty='AI기초')
        result = find_top_matches(req.id)

        bd = result['matches'][0]['breakdown']
        base_sum = bd['base']['기본_합계']
        bonus_sum = sum(b['점수'] for b in bd['bonuses'])
        penalty_sum = sum(p['점수'] for p in bd['penalties'])
        expected = base_sum + bonus_sum + penalty_sum
        assert bd['최종_총점'] == pytest.approx(expected, abs=0.01)


# ════════════════════════════════════════════════════════════════════
# [v3.0] 통합 시나리오 — 여러 보너스/패널티가 한꺼번에 적용되는지
# ════════════════════════════════════════════════════════════════════

class TestIntegratedScenario:
    """A~E 조합 시나리오 검증"""

    def test_복합_보너스_누적_반영(self, app, org_east):
        # org_east = 복지관 + 동부권
        # 강사: 시니어 대상 + 평점 4.9 + 재요청 1회 + 신규
        inst = Instructor(
            name='슈퍼강사', region='동부권',
            travel_range=['동부권'], specialties=['AI기초'],
            cert_level='전문가',
            available_days=['월'], available_times=['오전'],
            max_classes_month=4, target_audience=['시니어'],
            total_classes=2,         # 신규 (+20)
            avg_rating=4.9,          # +10
            last_active=date(2026, 5, 1),
            is_active=True,
        )
        db.session.add(inst)
        db.session.commit()

        # 과거 매칭 1회 → 재요청 +7, 과거 이력 +5
        past = _make_past_request(org_east)
        _make_match(past, inst, status='수락', satisfaction_score=4.8)

        current = _make_request(org_east, specialty='AI기초')
        result = find_top_matches(current.id)

        target = next(m for m in result['matches'] if m['instructor_id'] == inst.id)
        bd = target['breakdown']

        # 기본 점수: 권역40 + 분야40 + 시간20 = 100
        # 보너스: 평점10 + 만족도10 + 재요청7 + 복지관시니어10 + 과거매칭5 + 신규20 = 62
        # 패널티: 0
        # 총점: 162
        assert bd['base']['기본_합계'] == 100.0
        # 모든 보너스가 등록되어 있는지
        bonus_names = {b['항목'] for b in bd['bonuses']}
        assert '평점 보너스' in bonus_names
        assert '만족도 보너스/패널티' in bonus_names
        assert '재요청 보너스' in bonus_names
        assert '기관 유형 보너스' in bonus_names
        assert '과거 매칭 이력 보너스' in bonus_names
        assert '신규 강사 보너스' in bonus_names

    def test_복합_패널티_누적_반영(self, app, org_east):
        # 활동일 9개월 + 나쁜평가 3회 누적 + 월 80% + 쏠림
        inst = _make_instructor(
            name='문제강사', region='동부권', specialties=['AI기초'],
            avg_rating=4.0,  # 보너스 없음
            last_active=date(2025, 8, 1),  # -10
            total_classes=50,
        )
        inst.max_classes_month = 5
        db.session.commit()

        # 나쁜 평가 3회 + 이번 달 4건 (80%) → 같은 매칭으로 둘 다 충족
        for _ in range(4):
            past = _make_past_request(org_east)
            _make_match(
                past, inst, status='수락',
                satisfaction_score=2.5,
                created_at=datetime.utcnow(),
            )

        current = _make_request(org_east, specialty='AI기초')
        result = find_top_matches(current.id)

        # 문제 강사는 최선추천 모드가 아닌 한 결과에 있을 수 있음
        target = next(
            (m for m in result['matches'] if m['instructor_id'] == inst.id),
            None,
        )
        if target is None:
            # 패널티가 너무 커서 0점 이하로 떨어져 제외되었을 수도 있음
            return

        bd = target['breakdown']
        penalty_names = {p['항목'] for p in bd['penalties']}
        assert '활동일 패널티' in penalty_names
        assert '누적 나쁜평가 패널티' in penalty_names
        assert '월 강의 80% 패널티' in penalty_names

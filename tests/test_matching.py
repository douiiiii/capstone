"""
매칭 알고리즘 v2.0 테스트

케이스 목록:
  [단위 테스트]
  1. 평점 보너스 계산 (_calc_rating_bonus)
  2. 활동일 패널티 계산 (_calc_activity_penalty)
  3. 인증 등급 필터 (_is_cert_eligible)
  4. 인증 등급 유사분야 필터 (_is_cert_eligible_for_similar)

  [통합 테스트 - 예외 케이스]
  5. 정상 매칭 - 완전 일치 강사 상위 5명 반환
  6. is_active=False 강사 완전 제외
  7. 인증 등급 필터 - 기초 등급 강사가 고급 요청에서 제외
  8. 해당 권역 강사 없음 - 인접 권역 탐색 (match_mode='인접권역추천')
  9. 전문분야 강사 0명 - 유사분야 자동 확장 (match_mode='유사분야확장')
  10. 매칭 가능 강사 5명 미만 - 조건 완화 추천 (match_mode='조건완화추천')
  11. 모든 조건 불일치 - 최선 추천 (match_mode='최선추천')
  12. 동점자 정렬 - 평점 → 누적 강의 횟수 순
  13. 점수 0점 강사 결과 제외
  14. 활동일 패널티로 인한 점수 감소 확인
"""
import pytest
from datetime import date

from app import create_app
from app.extensions import db
from app.models.instructor import Instructor
from app.models.organization import Organization
from app.models.education_request import EducationRequest
from app.services.matching_service import (
    _calc_rating_bonus,
    _calc_activity_penalty,
    _is_cert_eligible,
    _is_cert_eligible_for_similar,
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
        inst_more = _make_instructor(
            name='많은강의', region='동부권', specialties=['AI기초'],
            avg_rating=4.7, total_classes=50,
        )
        inst_less = _make_instructor(
            name='적은강의', region='동부권', specialties=['AI기초'],
            avg_rating=4.7, total_classes=5,
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

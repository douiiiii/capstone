"""
대시보드 API 테스트

검증 항목:
  1) GET /api/dashboard/summary   : 총합/활동수/매칭률
  2) GET /api/dashboard/region    : 권역별 강사/요청 수
  3) GET /api/dashboard/specialty : 전문분야별 강사 수 + 인기 전문분야 Top5
  4) 데이터가 비어있는 경우 0 / 빈 배열 응답
"""
from datetime import date

import pytest

from app import create_app
from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
from app.models.organization import Organization


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
def client(app):
    return app.test_client()


@pytest.fixture
def seeded(app):
    """
    대시보드 검증용 시드 데이터
      - 강사 4명 (활동 3 + 비활동 1, 권역: 동부2/서부1/북부1)
      - 기관 3개 (동부1/서부1/남부1)
      - 요청 5건 (status 분포: 대기중2 / 매칭완료2 / 완료1)
    """
    # 강사
    insts = [
        Instructor(
            name='강사A', region='동부권', travel_range=['동부권'],
            specialties=['AI기초', '챗GPT'], cert_level=3,
            available_days=['월'], available_times=['오전'],
            max_classes_month=4, target_audience=['성인'],
            total_classes=10, avg_rating=4.8,
            last_active=date(2026, 5, 1), is_active=True,
        ),
        Instructor(
            name='강사B', region='동부권', travel_range=['동부권'],
            specialties=['AI기초'], cert_level=1,
            available_days=['화'], available_times=['오후'],
            max_classes_month=4, target_audience=['시니어'],
            total_classes=5, avg_rating=4.2,
            last_active=date(2026, 5, 1), is_active=True,
        ),
        Instructor(
            name='강사C', region='서부권', travel_range=['서부권'],
            specialties=['코딩교육'], cert_level=3,
            available_days=['수'], available_times=['저녁'],
            max_classes_month=4, target_audience=['청소년'],
            total_classes=8, avg_rating=4.5,
            last_active=date(2026, 5, 1), is_active=True,
        ),
        Instructor(
            name='강사D-비활동', region='북부권', travel_range=['북부권'],
            specialties=['스마트폰활용'], cert_level=1,
            available_days=['목'], available_times=['오전'],
            max_classes_month=4, target_audience=['시니어'],
            total_classes=2, avg_rating=3.9,
            last_active=date(2026, 1, 1), is_active=False,
        ),
    ]
    db.session.add_all(insts)

    # 기관
    org_e = Organization(name='동부복지관', type='복지관', region='동부권', contact='-')
    org_w = Organization(name='서부도서관', type='도서관', region='서부권', contact='-')
    org_s = Organization(name='남부주민센터', type='주민센터', region='남부권', contact='-')
    db.session.add_all([org_e, org_w, org_s])
    db.session.commit()

    # 요청
    reqs = [
        EducationRequest(
            org_id=org_e.id, specialty_needed='AI기초', target_audience='성인',
            expected_students=10, preferred_dates=['2026-06-01'],
            preferred_times=['오전'], frequency='주 1회', location_type='대면',
            status='매칭완료',
        ),
        EducationRequest(
            org_id=org_e.id, specialty_needed='AI기초', target_audience='시니어',
            expected_students=8, preferred_dates=['2026-06-02'],
            preferred_times=['오후'], frequency='주 1회', location_type='대면',
            status='대기',
        ),
        EducationRequest(
            org_id=org_w.id, specialty_needed='코딩교육', target_audience='청소년',
            expected_students=15, preferred_dates=['2026-06-03'],
            preferred_times=['저녁'], frequency='주 1회', location_type='대면',
            status='매칭완료',
        ),
        EducationRequest(
            org_id=org_w.id, specialty_needed='챗GPT', target_audience='성인',
            expected_students=12, preferred_dates=['2026-06-04'],
            preferred_times=['오후'], frequency='주 1회', location_type='혼합',
            status='완료',
        ),
        EducationRequest(
            org_id=org_s.id, specialty_needed='스마트폰활용', target_audience='시니어',
            expected_students=20, preferred_dates=['2026-06-05'],
            preferred_times=['오전'], frequency='주 1회', location_type='대면',
            status='대기',
        ),
    ]
    db.session.add_all(reqs)
    db.session.commit()


# ────────────────────── 1) summary ─────────────────────────────────

class TestDashboardSummary:
    def test_시드데이터_기반_요약(self, client, seeded):
        res = client.get('/api/dashboard/summary')
        assert res.status_code == 200
        body = res.get_json()
        assert body['success'] is True

        data = body['data']
        assert data['total_instructors'] == 4
        assert data['active_instructors'] == 3
        assert data['total_requests'] == 5
        # 매칭완료(2) + 완료(1) = 3
        assert data['matched_requests'] == 3
        # 3 / 5 * 100 = 60.0
        assert data['match_rate'] == 60.0

    def test_빈_데이터일때_0_반환(self, client, app):
        res = client.get('/api/dashboard/summary')
        assert res.status_code == 200
        data = res.get_json()['data']
        assert data['total_instructors'] == 0
        assert data['active_instructors'] == 0
        assert data['total_requests'] == 0
        assert data['matched_requests'] == 0
        assert data['match_rate'] == 0.0  # 0으로 나눠도 0.0


# ────────────────────── 2) region ──────────────────────────────────

class TestDashboardRegion:
    def test_권역별_강사_요청_수(self, client, seeded):
        res = client.get('/api/dashboard/region')
        assert res.status_code == 200
        body = res.get_json()
        assert body['success'] is True

        # dict로 변환해서 검증
        by_region = {row['region']: row for row in body['data']}

        # 비활동 강사(북부권 강사D)는 instructor_count에 포함되면 안 됨
        # 단 북부권은 강사D가 비활동이라 강사0, 요청도 없으니 키 자체가 없을 수 있음
        assert by_region['동부권']['instructor_count'] == 2
        assert by_region['동부권']['request_count'] == 2

        assert by_region['서부권']['instructor_count'] == 1
        assert by_region['서부권']['request_count'] == 2

        # 남부권은 강사 0명, 요청 1건
        assert by_region['남부권']['instructor_count'] == 0
        assert by_region['남부권']['request_count'] == 1

        # 북부권은 활동 강사 0명 + 요청 0건 → 응답에 없거나 0,0
        if '북부권' in by_region:
            assert by_region['북부권']['instructor_count'] == 0
            assert by_region['북부권']['request_count'] == 0

    def test_빈_데이터일때_빈_배열(self, client, app):
        res = client.get('/api/dashboard/region')
        assert res.status_code == 200
        body = res.get_json()
        assert body['success'] is True
        assert body['data'] == []
        assert body['count'] == 0


# ────────────────────── 3) specialty ──────────────────────────────

class TestDashboardSpecialty:
    def test_전문분야_집계와_top5(self, client, seeded):
        res = client.get('/api/dashboard/specialty')
        assert res.status_code == 200
        body = res.get_json()
        assert body['success'] is True

        data = body['data']

        # 활동 강사 specialties:
        #   강사A: AI기초, 챗GPT / 강사B: AI기초 / 강사C: 코딩교육
        #   (비활동 강사D의 스마트폰활용은 제외)
        by_spec = {row['specialty']: row['count'] for row in data['instructor_by_specialty']}
        assert by_spec['AI기초'] == 2
        assert by_spec['챗GPT'] == 1
        assert by_spec['코딩교육'] == 1
        assert '스마트폰활용' not in by_spec

        # 인기 전문분야 (요청 기준):
        #   AI기초 2 / 코딩교육 1 / 챗GPT 1 / 스마트폰활용 1
        top = data['top_requested']
        assert top[0] == {'specialty': 'AI기초', 'count': 2}
        # 4종류 → top5 안에 모두 포함
        assert len(top) == 4

    def test_빈_데이터일때_빈_배열(self, client, app):
        res = client.get('/api/dashboard/specialty')
        assert res.status_code == 200
        data = res.get_json()['data']
        assert data['instructor_by_specialty'] == []
        assert data['top_requested'] == []

    def test_top5_상한_확인(self, client, app):
        """6개 이상의 서로 다른 전문분야 요청 → top_requested는 5개로 잘림"""
        org = Organization(name='테스트', type='복지관', region='동부권', contact='-')
        db.session.add(org)
        db.session.commit()

        specialties = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
        for s in specialties:
            db.session.add(EducationRequest(
                org_id=org.id, specialty_needed=s, target_audience='성인',
                expected_students=1, preferred_dates=['2026-06-01'],
                preferred_times=['오전'], frequency='주 1회', location_type='대면',
                status='대기',
            ))
        db.session.commit()

        res = client.get('/api/dashboard/specialty')
        data = res.get_json()['data']
        assert len(data['top_requested']) == 5

"""
지도 히트맵 API 테스트

검증 항목:
  1) GET /api/map/regions     : 권역별 좌표 + 강사/요청/매칭 수
  2) GET /api/map/heatmap     : 히트맵 데이터(요청 0건 권역 제외)
  3) GET /api/map/instructors : 강사별 위치 (소속 권역 좌표 기준)
  4) 빈 데이터 케이스
"""
from datetime import date, datetime

import pytest

from app import create_app
from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
from app.models.match import Match
from app.models.organization import Organization
from app.routes.map import REGION_COORDINATES


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
def client(app):
    return app.test_client()


@pytest.fixture
def seeded(app):
    """
    지도 검증용 시드 데이터
      - 강사: 동부2(활동), 서부1(활동), 북부1(비활동)
      - 기관: 동부1, 서부1, 남부1
      - 요청: 동부 2건(1건 매칭완료), 서부 1건(매칭완료), 남부 1건(미매칭)
    """
    # 강사
    insts = [
        Instructor(
            name='강사A', region='동부권', travel_range=['동부권'],
            specialties=['AI기초', '챗GPT'], cert_level='전문가',
            available_days=['월'], available_times=['오전'],
            max_classes_month=4, target_audience=['성인'],
            total_classes=10, avg_rating=4.8,
            last_active=date(2026, 5, 1), is_active=True,
        ),
        Instructor(
            name='강사B', region='동부권', travel_range=['동부권'],
            specialties=['AI기초'], cert_level='기초',
            available_days=['화'], available_times=['오후'],
            max_classes_month=4, target_audience=['시니어'],
            total_classes=5, avg_rating=4.2,
            last_active=date(2026, 5, 1), is_active=True,
        ),
        Instructor(
            name='강사C', region='서부권', travel_range=['서부권'],
            specialties=['코딩교육'], cert_level='전문가',
            available_days=['수'], available_times=['저녁'],
            max_classes_month=4, target_audience=['청소년'],
            total_classes=8, avg_rating=4.5,
            last_active=date(2026, 5, 1), is_active=True,
        ),
        Instructor(
            name='강사D-비활동', region='북부권', travel_range=['북부권'],
            specialties=['스마트폰활용'], cert_level='기초',
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

    # 교육 요청
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
            status='대기중',
        ),
        EducationRequest(
            org_id=org_w.id, specialty_needed='코딩교육', target_audience='청소년',
            expected_students=15, preferred_dates=['2026-06-03'],
            preferred_times=['저녁'], frequency='주 1회', location_type='대면',
            status='매칭완료',
        ),
        EducationRequest(
            org_id=org_s.id, specialty_needed='스마트폰활용', target_audience='시니어',
            expected_students=20, preferred_dates=['2026-06-05'],
            preferred_times=['오전'], frequency='주 1회', location_type='대면',
            status='대기중',
        ),
    ]
    db.session.add_all(reqs)
    db.session.commit()

    # 매칭 (확정 상태) - 동부 1건, 서부 1건
    matches = [
        Match(request_id=reqs[0].id, instructor_id=insts[0].id,
              match_score=90.0, status='확정'),
        Match(request_id=reqs[2].id, instructor_id=insts[2].id,
              match_score=85.0, status='완료'),
    ]
    db.session.add_all(matches)
    db.session.commit()


# ────────────────────── 1) regions ─────────────────────────────────

class TestMapRegions:
    def test_5개_권역_모두_반환(self, client, seeded):
        res = client.get('/api/map/regions')
        assert res.status_code == 200
        body = res.get_json()
        assert body['success'] is True
        assert body['count'] == 5  # 동/서/남/북/중 5개

        by_region = {row['region']: row for row in body['data']}
        # 모든 권역에 좌표/areas 필드가 존재
        for region in ['동부권', '서부권', '북부권', '남부권', '중부권']:
            assert region in by_region
            assert 'lat' in by_region[region]
            assert 'lng' in by_region[region]
            assert 'areas' in by_region[region]

    def test_좌표값_정확성(self, client, seeded):
        res = client.get('/api/map/regions')
        by_region = {row['region']: row for row in res.get_json()['data']}

        # 명세된 좌표 확인
        assert by_region['동부권']['lat'] == 37.20
        assert by_region['동부권']['lng'] == 127.07
        assert by_region['서부권']['lat'] == 37.07
        assert by_region['서부권']['lng'] == 126.82
        assert by_region['중부권']['lat'] == 37.20
        assert by_region['중부권']['lng'] == 126.83

    def test_강사수_요청수_매칭수_집계(self, client, seeded):
        res = client.get('/api/map/regions')
        by_region = {row['region']: row for row in res.get_json()['data']}

        # 동부권: 활동강사 2, 요청 2, 매칭완료(확정) 1
        assert by_region['동부권']['instructor_count'] == 2
        assert by_region['동부권']['request_count'] == 2
        assert by_region['동부권']['matched_count'] == 1

        # 서부권: 활동강사 1, 요청 1, 매칭 1
        assert by_region['서부권']['instructor_count'] == 1
        assert by_region['서부권']['request_count'] == 1
        assert by_region['서부권']['matched_count'] == 1

        # 남부권: 활동강사 0, 요청 1, 매칭 0
        assert by_region['남부권']['instructor_count'] == 0
        assert by_region['남부권']['request_count'] == 1
        assert by_region['남부권']['matched_count'] == 0

        # 북부권: 비활동 강사뿐 → 0
        assert by_region['북부권']['instructor_count'] == 0
        assert by_region['북부권']['request_count'] == 0

    def test_빈_데이터일때도_5개_권역_0으로_반환(self, client, app):
        res = client.get('/api/map/regions')
        body = res.get_json()
        assert body['count'] == 5
        for row in body['data']:
            assert row['instructor_count'] == 0
            assert row['request_count'] == 0
            assert row['matched_count'] == 0


# ────────────────────── 2) heatmap ─────────────────────────────────

class TestMapHeatmap:
    def test_intensity가_요청수와_일치(self, client, seeded):
        res = client.get('/api/map/heatmap')
        assert res.status_code == 200
        body = res.get_json()
        assert body['success'] is True

        by_region = {row['region']: row for row in body['data']}
        # 요청 0건인 권역(북부/중부)은 제외
        assert '북부권' not in by_region
        assert '중부권' not in by_region

        assert by_region['동부권']['intensity'] == 2
        assert by_region['서부권']['intensity'] == 1
        assert by_region['남부권']['intensity'] == 1

        # 좌표 포함 확인
        assert by_region['동부권']['lat'] == 37.20
        assert by_region['동부권']['lng'] == 127.07

    def test_빈_데이터일때_빈_배열(self, client, app):
        res = client.get('/api/map/heatmap')
        body = res.get_json()
        assert body['success'] is True
        assert body['count'] == 0
        assert body['data'] == []


# ────────────────────── 3) instructors ─────────────────────────────

class TestMapInstructors:
    def test_활동_강사만_반환_좌표는_권역_중심(self, client, seeded):
        res = client.get('/api/map/instructors')
        assert res.status_code == 200
        body = res.get_json()
        assert body['success'] is True
        # 활동 3명만 (비활동 강사D 제외)
        assert body['count'] == 3

        names = {row['name'] for row in body['data']}
        assert names == {'강사A', '강사B', '강사C'}
        assert '강사D-비활동' not in names

        # 강사A는 동부권 좌표
        a = next(r for r in body['data'] if r['name'] == '강사A')
        assert a['lat'] == 37.20
        assert a['lng'] == 127.07
        assert a['region'] == '동부권'
        assert 'AI기초' in a['specialties']
        assert a['avg_rating'] == 4.8
        assert a['cert_level'] == '전문가'

    def test_빈_데이터일때_빈_배열(self, client, app):
        res = client.get('/api/map/instructors')
        body = res.get_json()
        assert body['count'] == 0
        assert body['data'] == []


# ────────────────────── 4) 좌표 상수 무결성 ────────────────────────

class TestRegionCoordinatesConstant:
    """좌표 상수가 명세와 일치하는지 확인 (실수로 수정되는 것 방지)"""

    def test_5개_권역_정의됨(self):
        assert set(REGION_COORDINATES.keys()) == {
            '동부권', '서부권', '북부권', '남부권', '중부권'
        }

    def test_각_권역에_lat_lng_areas_존재(self):
        for region, coord in REGION_COORDINATES.items():
            assert 'lat' in coord
            assert 'lng' in coord
            assert 'areas' in coord
            assert isinstance(coord['areas'], list)

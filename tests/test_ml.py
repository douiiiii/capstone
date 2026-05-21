"""
ML 사전 준비 시스템 테스트 (v5.0)

검증 항목:
  A) 로깅 — find_top_matches 호출 시 MLTrainingLog 자동 생성
  B) 피처 인코더 — 카테고리 → 코드 변환, imputation
  C) 피드백 루프 — /api/match/select, /api/match/feedback
  D) 데이터 품질 — /api/ml/data-quality, /api/ml/status
  E) 매칭 엔진 라우터 — A/B 선택, 미구현 엔진 호출 시 예외
"""
from datetime import date

import pytest

from app import create_app
from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
from app.models.match import Match
from app.models.ml_training_log import MLTrainingLog
from app.models.organization import Organization
from app.services.feature_encoder import (
    DEFAULT_AVG_RATING,
    encode_instructor,
    encode_org_type,
    encode_region,
    encode_request,
    encode_specialty,
    encode_time,
    impute_avg_rating,
    impute_last_active,
)
from app.services.matching_engine import (
    DEFAULT_ENGINE,
    get_engine_name,
    pick_ab_engine,
    run_matching,
)
from app.services.matching_service import find_top_matches


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


def _make_org(name='복지관A', type='복지관', region='동부권'):
    org = Organization(name=name, type=type, region=region, contact='-')
    db.session.add(org)
    db.session.commit()
    return org


def _make_instructor(
    name='강사', region='동부권', specialties=None, cert_level='전문가',
    avg_rating=4.5, total_classes=10, available_times=None,
):
    inst = Instructor(
        name=name, region=region, travel_range=[region],
        specialties=specialties or ['AI기초'],
        cert_level=cert_level, available_days=['월'],
        available_times=available_times or ['오전'],
        max_classes_month=4, target_audience=['성인'],
        total_classes=total_classes, avg_rating=avg_rating,
        last_active=date(2026, 5, 1), is_active=True,
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
# B) 피처 인코더
# ═══════════════════════════════════════════════════════════════════

class TestEncoder:
    def test_권역_코드(self, app):
        assert encode_region('동부권') == 1
        assert encode_region('중부권') == 5
        assert encode_region(None) == 0
        assert encode_region('알수없는권역') == 0

    def test_시간대_코드(self, app):
        assert encode_time('오전') == 1
        assert encode_time('저녁') == 3
        assert encode_time(None) == 0

    def test_분야_코드(self, app):
        assert encode_specialty('AI기초') == 11
        assert encode_specialty('파이썬') == 22

    def test_기관유형_코드_키워드매칭(self, app):
        assert encode_org_type('초등학교') == 4
        assert encode_org_type('주식회사') == 5
        assert encode_org_type('노인복지관') == 1
        assert encode_org_type(None) == 0

    def test_강사_인코딩(self, app):
        inst = _make_instructor()
        encoded = encode_instructor(inst)
        assert encoded['region_code'] == 1   # 동부권
        assert 11 in encoded['specialty_codes']  # AI기초
        assert encoded['cert_level_code'] == 3   # 전문가
        assert encoded['avg_rating'] == 4.5

    def test_요청_인코딩(self, app):
        org = _make_org(type='복지관', region='서부권')
        req = _make_request(org, specialty='AI기초', preferred_times=['오전', '오후'])
        encoded = encode_request(req)
        assert encoded['org_region_code'] == 2   # 서부권
        assert encoded['org_type_code'] == 1     # 복지관
        assert encoded['specialty_code'] == 11
        assert encoded['preferred_time_codes'] == [1, 2]


class TestImputation:
    def test_평점_없으면_4_0_기본값(self, app):
        assert impute_avg_rating(None) == DEFAULT_AVG_RATING
        assert impute_avg_rating(0) == DEFAULT_AVG_RATING
        assert impute_avg_rating(0.0) == DEFAULT_AVG_RATING
        assert impute_avg_rating(4.8) == 4.8

    def test_활동일_없으면_오늘(self, app):
        result = impute_last_active(None)
        assert result == date.today()

    def test_강사_avg_rating_None_도_4_0으로(self, app):
        inst = _make_instructor(avg_rating=0)
        encoded = encode_instructor(inst)
        assert encoded['avg_rating'] == DEFAULT_AVG_RATING


# ═══════════════════════════════════════════════════════════════════
# A) 매칭 로그 자동 생성
# ═══════════════════════════════════════════════════════════════════

class TestMLLogging:
    def test_매칭_시_MLTrainingLog_자동_생성(self, app):
        org = _make_org()
        for i in range(3):
            _make_instructor(name=f'강사{i}', specialties=['AI기초'])
        req = _make_request(org)

        result = find_top_matches(req.id)
        assert result['total_count'] >= 1

        # 추천된 강사 수만큼 로그 생성
        logs = MLTrainingLog.query.filter_by(request_id=req.id).all()
        assert len(logs) == result['total_count']
        # 초기 상태 확인
        for log in logs:
            assert log.was_selected is False
            assert log.was_conducted is False
            assert log.final_satisfaction is None
            assert log.engine_version == 'rule_based_v4'
            assert log.feature_snapshot is not None
            assert 'instructor' in log.feature_snapshot
            assert 'request' in log.feature_snapshot

    def test_재매칭_시_기존_로그_삭제후_재생성(self, app):
        org = _make_org()
        _make_instructor(name='강사1', specialties=['AI기초'])
        req = _make_request(org)

        find_top_matches(req.id)
        first_count = MLTrainingLog.query.filter_by(request_id=req.id).count()

        # 강사 추가 후 재매칭
        _make_instructor(name='강사2', specialties=['AI기초'])
        find_top_matches(req.id)
        second_count = MLTrainingLog.query.filter_by(request_id=req.id).count()

        assert second_count == 2  # 누적되지 않고 재생성


# ═══════════════════════════════════════════════════════════════════
# C) 피드백 루프
# ═══════════════════════════════════════════════════════════════════

class TestMatchSelect:
    def test_select_시_was_selected_True(self, client, app):
        org = _make_org()
        i1 = _make_instructor(name='강사1', specialties=['AI기초'])
        i2 = _make_instructor(name='강사2', specialties=['AI기초'])
        req = _make_request(org)
        find_top_matches(req.id)

        res = client.post('/api/match/select', json={
            'request_id': req.id,
            'instructor_id': i1.id,
            'not_selected_reasons': {str(i2.id): '거리'},
        })
        assert res.status_code == 200

        log1 = MLTrainingLog.query.filter_by(
            request_id=req.id, instructor_id=i1.id,
        ).first()
        log2 = MLTrainingLog.query.filter_by(
            request_id=req.id, instructor_id=i2.id,
        ).first()
        assert log1.was_selected is True
        assert log2.was_selected is False
        assert log2.not_selected_reason == '거리'

        # Match 테이블에도 상태 반영
        m1 = Match.query.filter_by(request_id=req.id, instructor_id=i1.id).first()
        m2 = Match.query.filter_by(request_id=req.id, instructor_id=i2.id).first()
        assert m1.status == '확정'
        assert m2.status == '거절'

    def test_select_파라미터_누락_400(self, client, app):
        res = client.post('/api/match/select', json={'request_id': 1})
        assert res.status_code == 400


class TestMatchFeedback:
    def test_feedback_저장(self, client, app):
        org = _make_org()
        inst = _make_instructor(specialties=['AI기초'])
        req = _make_request(org)
        find_top_matches(req.id)
        client.post('/api/match/select', json={
            'request_id': req.id, 'instructor_id': inst.id,
        })

        res = client.post('/api/match/feedback', json={
            'request_id': req.id,
            'instructor_id': inst.id,
            'satisfaction_score': 4.5,
        })
        assert res.status_code == 200

        log = MLTrainingLog.query.filter_by(
            request_id=req.id, instructor_id=inst.id,
        ).first()
        assert log.final_satisfaction == 4.5
        assert log.was_conducted is True
        assert log.is_labeled is True

        # Match 테이블에도 만족도 + status='완료'
        m = Match.query.filter_by(request_id=req.id, instructor_id=inst.id).first()
        assert m.satisfaction_score == 4.5
        assert m.status == '완료'

    def test_feedback_범위_초과_400(self, client, app):
        res = client.post('/api/match/feedback', json={
            'request_id': 1, 'instructor_id': 1, 'satisfaction_score': 6.0,
        })
        assert res.status_code == 400

    def test_feedback_로그_없으면_404(self, client, app):
        res = client.post('/api/match/feedback', json={
            'request_id': 999, 'instructor_id': 999, 'satisfaction_score': 4.0,
        })
        assert res.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# D) 데이터 품질 / 상태
# ═══════════════════════════════════════════════════════════════════

class TestMLEndpoints:
    def test_features_endpoint(self, client, app):
        org = _make_org()
        _make_instructor(specialties=['AI기초'])
        req = _make_request(org)

        res = client.get(f'/api/ml/features/{req.id}')
        assert res.status_code == 200
        body = res.get_json()
        assert body['request_features']['specialty_code'] == 11
        assert len(body['instructor_features']) == 1
        assert body['instructor_features'][0]['region_code'] == 1

    def test_features_없는_요청_404(self, client, app):
        res = client.get('/api/ml/features/999')
        assert res.status_code == 404

    def test_status_빈데이터(self, client, app):
        res = client.get('/api/ml/status')
        body = res.get_json()['data']
        assert body['labeled_count'] == 0
        assert body['target'] == 500
        assert body['needed_more'] == 500
        assert body['is_ready_for_training'] is False

    def test_status_완전_라벨링된_로그_카운트(self, client, app):
        org = _make_org()
        inst = _make_instructor(specialties=['AI기초'])
        req = _make_request(org)
        find_top_matches(req.id)
        client.post('/api/match/select', json={
            'request_id': req.id, 'instructor_id': inst.id,
        })
        client.post('/api/match/feedback', json={
            'request_id': req.id, 'instructor_id': inst.id,
            'satisfaction_score': 4.7,
        })

        res = client.get('/api/ml/status')
        body = res.get_json()['data']
        assert body['labeled_count'] == 1
        assert body['needed_more'] == 499

    def test_data_quality_보고서(self, client, app):
        # 평점 0(결측), 활동일 미설정 강사 1명 (region 은 NOT NULL 이라 빈 문자열로)
        db.session.add(Instructor(
            name='결측강사', region='', travel_range=[],
            specialties=['AI기초'], cert_level='기초',
            available_days=['월'], available_times=['오전'],
            max_classes_month=4, target_audience=['성인'],
            total_classes=0, avg_rating=0.0, last_active=None,
            is_active=True,
        ))
        db.session.commit()

        res = client.get('/api/ml/data-quality')
        body = res.get_json()['data']
        assert body['instructor']['missing_rating'] == 1
        assert body['instructor']['missing_last_active'] == 1
        assert body['instructor']['missing_region'] == 1  # 빈 문자열도 missing 으로 카운트
        # 결측이 있으니 100점 미만
        assert body['quality_score'] < 100


# ═══════════════════════════════════════════════════════════════════
# E) 매칭 엔진 라우터 (A/B 구조)
# ═══════════════════════════════════════════════════════════════════

class TestMatchingEngine:
    def test_기본_엔진은_rule_based(self, app, monkeypatch):
        monkeypatch.delenv('MATCHING_ENGINE', raising=False)
        assert get_engine_name() == DEFAULT_ENGINE

    def test_명시적_엔진이_우선(self, app):
        assert get_engine_name('ml_v1') == 'ml_v1'

    def test_환경변수가_적용됨(self, app, monkeypatch):
        monkeypatch.setenv('MATCHING_ENGINE', 'ml_v1')
        assert get_engine_name() == 'ml_v1'

    def test_run_matching_엔진명_포함(self, app):
        org = _make_org()
        _make_instructor(specialties=['AI기초'])
        req = _make_request(org)

        result = run_matching(req.id, engine='rule_based_v4')
        assert result['engine'] == 'rule_based_v4'

    def test_ml_v1_엔진은_미구현_예외(self, app):
        org = _make_org()
        _make_instructor(specialties=['AI기초'])
        req = _make_request(org)

        with pytest.raises(NotImplementedError):
            run_matching(req.id, engine='ml_v1')

    def test_알수없는_엔진_ValueError(self, app):
        with pytest.raises(ValueError):
            run_matching(1, engine='unknown')

    def test_ab_engine_결정성(self, app):
        """같은 request_id 는 항상 같은 엔진"""
        engines = ['rule_based_v4', 'ml_v1']
        first = pick_ab_engine(request_id=42, engines=engines)
        second = pick_ab_engine(request_id=42, engines=engines)
        assert first == second
        # 모든 후보 중 하나
        assert first in engines

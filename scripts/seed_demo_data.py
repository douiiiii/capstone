"""
화성시 AI 시민리더 허브 — 시연/엣지 케이스 더미데이터 시드 스크립트
=====================================================================

목적:
  1. 알고리즘 엣지 케이스(낮은 평점/비활동/신규/베테랑/장기 미활동) 검증
  2. 시연/발표용 풍부한 데이터 (5권역 분포, 정기강의, 대규모, VIP)

원칙:
  - 기존 데이터 절대 삭제하지 않음. INSERT/UPDATE 만.
  - cert_level 은 정수(1/2/3)
  - 권역명/JSON 배열 등 합의된 형식 준수
  - 화성시 실제 동네/지명 반영

실행:
  source .venv/bin/activate && python scripts/seed_demo_data.py
"""
import os
import sys
import json
from datetime import date, datetime, timedelta
import random

# 프로젝트 루트 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models.instructor import Instructor
from app.models.organization import Organization
from app.models.education_request import EducationRequest
from app.models.match import Match
from app.models.class_session import ClassSession
from app.models.ml_training_log import MLTrainingLog
from app.services.matching_service import find_top_matches
from app.services.class_session_service import create_sessions_for_match


random.seed(20260526)  # 재현 가능한 결과
TODAY = date(2026, 5, 26)


# ──────────────────────────────────────────────────────────────────────
# Instructor 모델 컬럼은 String(20) 으로 선언돼 있으나
# 실제 Supabase DB 컬럼은 integer (1=기초/2=중급/3=전문가).
# 그래서 ORM add_all 대신 명시적 ::integer 캐스트가 들어간 raw SQL 로 삽입.
# ──────────────────────────────────────────────────────────────────────
_INSTRUCTOR_INSERT_SQL = text("""
    INSERT INTO instructors (
        name, region, travel_range, specialties, cert_level,
        available_days, available_times, max_classes_month,
        target_audience, total_classes, avg_rating, last_active,
        is_active, preferred_org_types, disliked_org_types,
        cert_level_updated_at
    ) VALUES (
        :name, :region, CAST(:travel_range AS json), CAST(:specialties AS json),
        CAST(:cert_level AS integer),
        CAST(:available_days AS json), CAST(:available_times AS json),
        :max_classes_month,
        CAST(:target_audience AS json), :total_classes, :avg_rating, :last_active,
        :is_active,
        CAST(:preferred_org_types AS json), CAST(:disliked_org_types AS json),
        :cert_level_updated_at
    )
    RETURNING id
""")


def _instructor_to_params(i: Instructor) -> dict:
    """ORM Instructor 객체를 raw INSERT 파라미터 dict 로 변환 (JSON 직렬화)."""
    def _j(v):
        return json.dumps(v) if v is not None else None
    return {
        'name': i.name,
        'region': i.region,
        'travel_range': _j(i.travel_range),
        'specialties': _j(i.specialties),
        'cert_level': i.cert_level,  # int 그대로
        'available_days': _j(i.available_days),
        'available_times': _j(i.available_times),
        'max_classes_month': i.max_classes_month,
        'target_audience': _j(i.target_audience),
        'total_classes': i.total_classes,
        'avg_rating': i.avg_rating,
        'last_active': i.last_active,
        'is_active': i.is_active,
        'preferred_org_types': _j(i.preferred_org_types),
        'disliked_org_types': _j(i.disliked_org_types),
        'cert_level_updated_at': i.cert_level_updated_at,
    }


def insert_instructors_raw(instructors: list[Instructor]) -> list[int]:
    """ORM 우회. cert_level::integer 캐스트와 함께 한 줄씩 INSERT.
    반환: 새로 발급된 ID 리스트 (입력 순서 보존)."""
    ids: list[int] = []
    for inst in instructors:
        params = _instructor_to_params(inst)
        new_id = db.session.execute(_INSTRUCTOR_INSERT_SQL, params).scalar_one()
        ids.append(new_id)
    db.session.commit()
    return ids


# ──────────────────────────────────────────────────────────────────────
# 강사 30명
# ──────────────────────────────────────────────────────────────────────

def build_instructors() -> list[Instructor]:
    """일반 강사 15명 + 엣지 케이스 강사 15명"""
    rows: list[Instructor] = []

    # ── 일반 강사 15명 (5권역 × 3명) ──────────────────────────────
    # 동부권 3명
    rows.extend([
        Instructor(
            name='강은서', region='동부권',
            travel_range=['동부권', '중부권'],
            specialties=['AI 기초', '챗GPT 활용'],
            cert_level=2,
            available_days=['평일'], available_times=['오전', '오후'],
            max_classes_month=8,
            target_audience=['시니어', '성인'],
            total_classes=22, avg_rating=4.3,
            last_active=TODAY - timedelta(days=10), is_active=True,
            preferred_org_types=['복지기관', '공공기관'],
        ),
        Instructor(
            name='조현우', region='동부권',
            travel_range=['동부권', '중부권', '남부권'],
            specialties=['파이썬 기초', '코딩교육', '데이터분석'],
            cert_level=3,
            available_days=['평일', '주말'], available_times=['오후', '저녁'],
            max_classes_month=10,
            target_audience=['청소년', '성인'],
            total_classes=38, avg_rating=4.7,
            last_active=TODAY - timedelta(days=5), is_active=True,
            preferred_org_types=['학교', '교육기관'],
        ),
        Instructor(
            name='문지윤', region='동부권',
            travel_range=['동부권'],
            specialties=['스마트폰활용', '디지털 리터러시'],
            cert_level=1,
            available_days=['평일'], available_times=['오전'],
            max_classes_month=6,
            target_audience=['시니어'],
            total_classes=15, avg_rating=4.1,
            last_active=TODAY - timedelta(days=14), is_active=True,
            preferred_org_types=['복지기관'],
        ),
    ])

    # 서부권 3명
    rows.extend([
        Instructor(
            name='황민서', region='서부권',
            travel_range=['서부권', '중부권', '북부권'],
            specialties=['엑셀AI', '업무자동화'],
            cert_level=2,
            available_days=['평일'], available_times=['오전', '오후'],
            max_classes_month=8,
            target_audience=['직장인', '성인'],
            total_classes=29, avg_rating=4.5,
            last_active=TODAY - timedelta(days=7), is_active=True,
            preferred_org_types=['기업', '공공기관'],
        ),
        Instructor(
            name='배준영', region='서부권',
            travel_range=['서부권', '중부권'],
            specialties=['유튜브AI', '콘텐츠 제작', 'SNS마케팅'],
            cert_level=3,
            available_days=['평일', '주말'], available_times=['오후', '저녁'],
            max_classes_month=10,
            target_audience=['청년창업자', '성인', '청소년'],
            total_classes=44, avg_rating=4.6,
            last_active=TODAY - timedelta(days=3), is_active=True,
            preferred_org_types=['교육기관', '기업'],
        ),
        Instructor(
            name='임소율', region='서부권',
            travel_range=['서부권', '북부권'],
            specialties=['디지털 리터러시', 'AI 기초'],
            cert_level=2,
            available_days=['평일'], available_times=['오전'],
            max_classes_month=6,
            target_audience=['시니어', '성인'],
            total_classes=20, avg_rating=4.2,
            last_active=TODAY - timedelta(days=11), is_active=True,
            preferred_org_types=['복지기관', '공공기관'],
        ),
    ])

    # 남부권 3명
    rows.extend([
        Instructor(
            name='장태민', region='남부권',
            travel_range=['남부권', '중부권', '동부권'],
            specialties=['이미지 생성AI', '챗GPT 활용'],
            cert_level=3,
            available_days=['평일', '주말'], available_times=['오후', '저녁'],
            max_classes_month=10,
            target_audience=['청소년', '성인'],
            total_classes=35, avg_rating=4.6,
            last_active=TODAY - timedelta(days=2), is_active=True,
            preferred_org_types=['학교', '문화기관'],
        ),
        Instructor(
            name='하예린', region='남부권',
            travel_range=['남부권', '중부권'],
            specialties=['스크래치', '코딩교육'],
            cert_level=2,
            available_days=['평일'], available_times=['오후'],
            max_classes_month=7,
            target_audience=['초등학생', '중학생'],
            total_classes=24, avg_rating=4.4,
            last_active=TODAY - timedelta(days=9), is_active=True,
            preferred_org_types=['학교'],
        ),
        Instructor(
            name='권도훈', region='남부권',
            travel_range=['남부권'],
            specialties=['디지털 리터러시', 'AI 기초', '스마트폰활용'],
            cert_level=1,
            available_days=['평일', '주말'], available_times=['오전', '오후'],
            max_classes_month=8,
            target_audience=['시니어'],
            total_classes=12, avg_rating=4.0,
            last_active=TODAY - timedelta(days=15), is_active=True,
            preferred_org_types=['복지기관'],
        ),
    ])

    # 북부권 3명
    rows.extend([
        Instructor(
            name='유서아', region='북부권',
            travel_range=['북부권', '중부권', '서부권'],
            specialties=['데이터분석', '엑셀AI', '파이썬 기초'],
            cert_level=3,
            available_days=['평일'], available_times=['오전', '오후'],
            max_classes_month=9,
            target_audience=['직장인', '구직자', '성인'],
            total_classes=40, avg_rating=4.8,
            last_active=TODAY - timedelta(days=4), is_active=True,
            preferred_org_types=['기업', '공공기관'],
        ),
        Instructor(
            name='남시현', region='북부권',
            travel_range=['북부권', '중부권'],
            specialties=['디지털마케팅', '콘텐츠 제작'],
            cert_level=2,
            available_days=['평일', '주말'], available_times=['오후'],
            max_classes_month=8,
            target_audience=['청년창업자', '성인'],
            total_classes=27, avg_rating=4.5,
            last_active=TODAY - timedelta(days=6), is_active=True,
            preferred_org_types=['기업', '교육기관'],
        ),
        Instructor(
            name='편하준', region='북부권',
            travel_range=['북부권'],
            specialties=['AI 기초', '스마트폰활용'],
            cert_level=1,
            available_days=['평일'], available_times=['오전'],
            max_classes_month=6,
            target_audience=['시니어'],
            total_classes=18, avg_rating=4.2,
            last_active=TODAY - timedelta(days=12), is_active=True,
            preferred_org_types=['복지기관'],
        ),
    ])

    # 중부권 3명 (현재 비어있는 권역 보강)
    rows.extend([
        Instructor(
            name='안재호', region='중부권',
            travel_range=['중부권', '동부권', '서부권', '남부권', '북부권'],
            specialties=['AI 기초', '챗GPT 활용', '디지털 리터러시'],
            cert_level=2,
            available_days=['평일'], available_times=['오전', '오후'],
            max_classes_month=10,
            target_audience=['공무원', '성인', '시니어'],
            total_classes=36, avg_rating=4.6,
            last_active=TODAY - timedelta(days=3), is_active=True,
            preferred_org_types=['공공기관'],
        ),
        Instructor(
            name='차윤아', region='중부권',
            travel_range=['중부권', '동부권', '남부권'],
            specialties=['업무자동화', '엑셀AI'],
            cert_level=3,
            available_days=['평일'], available_times=['오후', '저녁'],
            max_classes_month=8,
            target_audience=['직장인', '공무원'],
            total_classes=30, avg_rating=4.7,
            last_active=TODAY - timedelta(days=5), is_active=True,
            preferred_org_types=['공공기관', '기업'],
        ),
        Instructor(
            name='도가람', region='중부권',
            travel_range=['중부권', '서부권', '북부권'],
            specialties=['이미지 생성AI', '유튜브AI', 'SNS마케팅'],
            cert_level=3,
            available_days=['평일', '주말'], available_times=['오후', '저녁'],
            max_classes_month=9,
            target_audience=['청년창업자', '성인'],
            total_classes=26, avg_rating=4.4,
            last_active=TODAY - timedelta(days=8), is_active=True,
            preferred_org_types=['교육기관', '문화기관'],
        ),
    ])

    # ── 엣지 케이스 강사 15명 ─────────────────────────────────────

    # 1) 낮은 평점 강사 3명 (3.0~3.9)
    rows.extend([
        Instructor(
            name='엣지_저평점_이재우', region='동부권',
            travel_range=['동부권', '중부권'],
            specialties=['AI 기초', '챗GPT 활용'],
            cert_level=2,
            available_days=['평일'], available_times=['오전'],
            max_classes_month=8,
            target_audience=['성인'],
            total_classes=14, avg_rating=3.2,
            last_active=TODAY - timedelta(days=20), is_active=True,
            preferred_org_types=['복지기관'],
        ),
        Instructor(
            name='엣지_저평점_서다은', region='서부권',
            travel_range=['서부권'],
            specialties=['디지털 리터러시', '스마트폰활용'],
            cert_level=1,
            available_days=['평일'], available_times=['오후'],
            max_classes_month=6,
            target_audience=['시니어'],
            total_classes=11, avg_rating=3.6,
            last_active=TODAY - timedelta(days=25), is_active=True,
            preferred_org_types=['복지기관'],
        ),
        Instructor(
            name='엣지_저평점_홍성진', region='북부권',
            travel_range=['북부권', '중부권'],
            specialties=['엑셀AI', '업무자동화'],
            cert_level=2,
            available_days=['평일'], available_times=['오전', '오후'],
            max_classes_month=8,
            target_audience=['성인', '직장인'],
            total_classes=17, avg_rating=3.9,
            last_active=TODAY - timedelta(days=18), is_active=True,
            preferred_org_types=['기업'],
        ),
    ])

    # 2) 비활동 강사 3명 (is_active=False)
    rows.extend([
        Instructor(
            name='엣지_비활동_백승호', region='동부권',
            travel_range=['동부권', '중부권'],
            specialties=['AI 기초', '챗GPT 활용', '이미지 생성AI'],
            cert_level=3,
            available_days=['평일'], available_times=['오후'],
            max_classes_month=8,
            target_audience=['청소년', '성인'],
            total_classes=33, avg_rating=4.5,
            last_active=TODAY - timedelta(days=45), is_active=False,
            preferred_org_types=['학교'],
        ),
        Instructor(
            name='엣지_비활동_나은빈', region='남부권',
            travel_range=['남부권', '중부권'],
            specialties=['파이썬 기초', '코딩교육'],
            cert_level=3,
            available_days=['평일'], available_times=['저녁'],
            max_classes_month=6,
            target_audience=['청소년'],
            total_classes=21, avg_rating=4.4,
            last_active=TODAY - timedelta(days=60), is_active=False,
            preferred_org_types=['학교'],
        ),
        Instructor(
            name='엣지_비활동_곽준희', region='서부권',
            travel_range=['서부권', '중부권'],
            specialties=['디지털마케팅', '콘텐츠 제작'],
            cert_level=2,
            available_days=['평일'], available_times=['오후'],
            max_classes_month=7,
            target_audience=['청년창업자'],
            total_classes=19, avg_rating=4.3,
            last_active=TODAY - timedelta(days=90), is_active=False,
            preferred_org_types=['기업'],
        ),
    ])

    # 3) 신규 강사 3명 (total_classes 0~5, avg_rating 0 = 평점 없음)
    rows.extend([
        Instructor(
            name='엣지_신규_김찬우', region='동부권',
            travel_range=['동부권', '중부권'],
            specialties=['AI 기초', '챗GPT 활용'],
            cert_level=1,
            available_days=['평일'], available_times=['오전', '오후'],
            max_classes_month=8,
            target_audience=['성인'],
            total_classes=0, avg_rating=0.0,
            last_active=TODAY - timedelta(days=2), is_active=True,
            preferred_org_types=['복지기관', '학교'],
        ),
        Instructor(
            name='엣지_신규_정세빈', region='중부권',
            travel_range=['중부권', '서부권', '북부권'],
            specialties=['이미지 생성AI', '콘텐츠 제작'],
            cert_level=2,
            available_days=['평일', '주말'], available_times=['오후', '저녁'],
            max_classes_month=8,
            target_audience=['청소년', '성인'],
            total_classes=3, avg_rating=0.0,
            last_active=TODAY - timedelta(days=1), is_active=True,
            preferred_org_types=['교육기관'],
        ),
        Instructor(
            name='엣지_신규_오민혁', region='남부권',
            travel_range=['남부권', '중부권'],
            specialties=['스크래치', '코딩교육'],
            cert_level=2,
            available_days=['평일'], available_times=['오후'],
            max_classes_month=6,
            target_audience=['초등학생', '중학생'],
            total_classes=5, avg_rating=0.0,
            last_active=TODAY - timedelta(days=4), is_active=True,
            preferred_org_types=['학교'],
        ),
    ])

    # 4) 베테랑 강사 3명 (total_classes 80+, 평점 4.8+)
    rows.extend([
        Instructor(
            name='엣지_베테랑_정원석', region='동부권',
            travel_range=['동부권', '중부권', '남부권', '서부권', '북부권'],
            specialties=['AI 기초', '챗GPT 활용', '이미지 생성AI', '데이터분석'],
            cert_level=3,
            available_days=['평일', '주말'], available_times=['오전', '오후', '저녁'],
            max_classes_month=12,
            target_audience=['시니어', '성인', '청소년', '공무원'],
            total_classes=92, avg_rating=4.9,
            last_active=TODAY - timedelta(days=1), is_active=True,
            preferred_org_types=['학교', '공공기관', '복지기관'],
        ),
        Instructor(
            name='엣지_베테랑_송미희', region='중부권',
            travel_range=['중부권', '동부권', '서부권', '남부권', '북부권'],
            specialties=['업무자동화', '엑셀AI', '데이터분석', '챗GPT 활용'],
            cert_level=3,
            available_days=['평일'], available_times=['오전', '오후'],
            max_classes_month=10,
            target_audience=['공무원', '직장인', '성인'],
            total_classes=110, avg_rating=4.95,
            last_active=TODAY - timedelta(days=2), is_active=True,
            preferred_org_types=['공공기관', '기업'],
        ),
        Instructor(
            name='엣지_베테랑_류재석', region='서부권',
            travel_range=['서부권', '중부권', '북부권', '남부권'],
            specialties=['유튜브AI', '콘텐츠 제작', 'SNS마케팅', '디지털마케팅'],
            cert_level=3,
            available_days=['평일', '주말'], available_times=['오후', '저녁'],
            max_classes_month=11,
            target_audience=['청년창업자', '성인', '청소년'],
            total_classes=85, avg_rating=4.82,
            last_active=TODAY - timedelta(days=1), is_active=True,
            preferred_org_types=['교육기관', '기업', '문화기관'],
        ),
    ])

    # 5) 6개월 미활동 강사 3명 (last_active 약 6~9개월 전)
    rows.extend([
        Instructor(
            name='엣지_장기미활동_지수연', region='동부권',
            travel_range=['동부권', '중부권'],
            specialties=['AI 기초', '챗GPT 활용'],
            cert_level=3,
            available_days=['평일'], available_times=['오전'],
            max_classes_month=8,
            target_audience=['성인'],
            total_classes=24, avg_rating=4.5,
            last_active=date(2025, 9, 20), is_active=True,  # ~8개월 전
            preferred_org_types=['복지기관'],
        ),
        Instructor(
            name='엣지_장기미활동_허지원', region='서부권',
            travel_range=['서부권', '북부권'],
            specialties=['엑셀AI', '업무자동화'],
            cert_level=2,
            available_days=['평일'], available_times=['오후'],
            max_classes_month=8,
            target_audience=['직장인'],
            total_classes=18, avg_rating=4.3,
            last_active=date(2025, 11, 5), is_active=True,  # ~6.5개월 전
            preferred_org_types=['기업'],
        ),
        Instructor(
            name='엣지_장기미활동_양선우', region='남부권',
            travel_range=['남부권', '중부권'],
            specialties=['디지털 리터러시', '스마트폰활용'],
            cert_level=1,
            available_days=['평일'], available_times=['오전'],
            max_classes_month=6,
            target_audience=['시니어'],
            total_classes=22, avg_rating=4.2,
            last_active=date(2025, 7, 15), is_active=True,  # ~10개월 전
            preferred_org_types=['복지기관'],
        ),
    ])

    return rows


# ──────────────────────────────────────────────────────────────────────
# 기관 20개
# ──────────────────────────────────────────────────────────────────────

def build_organizations() -> list[Organization]:
    rows: list[Organization] = []

    # ── 일반 기관 15개 ───────────────────────────────────────────
    # 학교 5개
    rows.extend([
        Organization(name='동탄1초등학교', type='학교', region='동부권', contact='031-371-2001'),
        Organization(name='동탄2중학교', type='학교', region='동부권', contact='031-371-2002'),
        Organization(name='향남중학교', type='학교', region='서부권', contact='031-352-3001'),
        Organization(name='봉담초등학교', type='학교', region='북부권', contact='031-298-4001'),
        Organization(name='우정고등학교', type='학교', region='남부권', contact='031-356-5001'),
    ])
    # 기업 5개
    rows.extend([
        Organization(name='동탄테크파크 입주기업협의회', type='기업', region='동부권', contact='031-371-6001'),
        Organization(name='향남제약단지 안전관리실', type='기업', region='서부권', contact='031-352-6002'),
        Organization(name='발안일반산업단지 사업자회', type='기업', region='서부권', contact='031-352-6003'),
        Organization(name='기안중소기업 클러스터', type='기업', region='북부권', contact='031-298-6004'),
        Organization(name='장안일반산업단지 협력회', type='기업', region='남부권', contact='031-356-6005'),
    ])
    # 복지기관 3개
    rows.extend([
        Organization(name='향남노인복지관', type='복지기관', region='서부권', contact='031-352-7001'),
        Organization(name='우정종합사회복지관', type='복지기관', region='남부권', contact='031-356-7002'),
        Organization(name='기안장애인복지관', type='복지기관', region='북부권', contact='031-298-7003'),
    ])
    # 공공기관 2개
    rows.extend([
        Organization(name='봉담행정복지센터', type='공공기관', region='북부권', contact='031-298-8001'),
        Organization(name='장안행정복지센터', type='공공기관', region='남부권', contact='031-356-8002'),
    ])

    # ── 엣지 케이스 기관 5개 ─────────────────────────────────────
    # 중부권 학교 2개 (지금까지 중부권 기관이 거의 없음)
    rows.extend([
        Organization(name='화성시청 부설 청소년교육원', type='학교', region='중부권', contact='031-369-9001'),
        Organization(name='중부권 시민혁신학교', type='학교', region='중부권', contact='031-369-9002'),
    ])
    # 대규모 행사 가능 기관 2개 (이름에 '대형/컨벤션' 명시)
    rows.extend([
        Organization(name='동탄대형컨벤션센터', type='문화기관', region='동부권', contact='031-371-9003'),
        Organization(name='화성시청 대강당 (수용 300명)', type='공공기관', region='중부권', contact='031-369-9004'),
    ])
    # 외곽 지역 기관 1개 (장안권 끝, 인접권역 적음)
    rows.append(
        Organization(name='장안 도서산간 마을공동체 센터', type='복지기관', region='남부권', contact='031-356-9005'),
    )

    return rows


# ──────────────────────────────────────────────────────────────────────
# 교육 요청 50건
# ──────────────────────────────────────────────────────────────────────

def build_requests(org_map: dict[str, Organization]) -> list[EducationRequest]:
    """
    org_map: {기관명: Organization} — 신규 추가된 기관만 포함
    """
    rows: list[EducationRequest] = []

    def org_id(name: str) -> int:
        return org_map[name].id

    # ── 일반 요청 30건 (대기중 20 / 매칭중 5 / 완료 5, 1회성 25 / 정기 5) ──
    general: list[dict] = [
        # 대기중 20건 — 다양한 권역/시간/전문분야
        dict(org='동탄1초등학교',          spec='스크래치',         ta='초등학생',  n=20, dates=['2026-06-10'],
             times=['오후'], freq='1회성', loc='대면', st='대기'),
        dict(org='동탄2중학교',            spec='AI 기초',          ta='중학생',    n=30, dates=['2026-06-12'],
             times=['오전'], freq='1회성', loc='대면', st='대기'),
        dict(org='향남중학교',             spec='챗GPT 활용',       ta='중학생',    n=25, dates=['2026-06-15'],
             times=['오후'], freq='1회성', loc='대면', st='대기'),
        dict(org='봉담초등학교',           spec='스크래치',         ta='초등학생',  n=18, dates=['2026-06-18'],
             times=['오후'], freq='1회성', loc='대면', st='대기'),
        dict(org='우정고등학교',           spec='파이썬 기초',      ta='고등학생',  n=22, dates=['2026-06-20'],
             times=['오후'], freq='1회성', loc='대면', st='대기'),
        dict(org='동탄테크파크 입주기업협의회', spec='업무자동화',    ta='직장인',    n=15, dates=['2026-06-22'],
             times=['오전'], freq='1회성', loc='대면', st='대기'),
        dict(org='향남제약단지 안전관리실',     spec='엑셀AI',         ta='직원',      n=12, dates=['2026-06-23'],
             times=['오후'], freq='1회성', loc='대면', st='대기'),
        dict(org='발안일반산업단지 사업자회',   spec='챗GPT 활용',     ta='직장인',    n=10, dates=['2026-06-24'],
             times=['오후'], freq='1회성', loc='대면', st='대기'),
        dict(org='기안중소기업 클러스터',       spec='데이터분석',     ta='직장인',    n=14, dates=['2026-06-25'],
             times=['오전'], freq='1회성', loc='대면', st='대기'),
        dict(org='장안일반산업단지 협력회',     spec='업무자동화',     ta='직원',      n=15, dates=['2026-06-26'],
             times=['오전'], freq='1회성', loc='대면', st='대기'),
        dict(org='향남노인복지관',             spec='스마트폰활용',   ta='시니어',    n=20, dates=['2026-06-27'],
             times=['오전'], freq='1회성', loc='대면', st='대기'),
        dict(org='우정종합사회복지관',         spec='디지털 리터러시', ta='시니어',    n=18, dates=['2026-06-29'],
             times=['오전'], freq='1회성', loc='대면', st='대기'),
        dict(org='기안장애인복지관',           spec='AI 기초',         ta='장애인',    n=10, dates=['2026-07-01'],
             times=['오후'], freq='1회성', loc='대면', st='대기'),
        dict(org='봉담행정복지센터',           spec='챗GPT 활용',      ta='공무원',    n=25, dates=['2026-07-02'],
             times=['오후'], freq='1회성', loc='대면', st='대기'),
        dict(org='장안행정복지센터',           spec='이미지 생성AI',   ta='성인',      n=15, dates=['2026-07-03'],
             times=['오후'], freq='1회성', loc='대면', st='대기'),
        dict(org='화성시청 부설 청소년교육원', spec='스크래치',        ta='초등학생',  n=20, dates=['2026-07-04'],
             times=['오후'], freq='1회성', loc='대면', st='대기'),
        dict(org='중부권 시민혁신학교',         spec='AI 기초',         ta='중학생',    n=24, dates=['2026-07-06'],
             times=['오전'], freq='1회성', loc='대면', st='대기'),
        dict(org='동탄대형컨벤션센터',          spec='유튜브AI',        ta='청년창업자', n=40, dates=['2026-07-08'],
             times=['저녁'], freq='1회성', loc='대면', st='대기'),
        dict(org='화성시청 대강당 (수용 300명)', spec='AI 기초',        ta='공무원',    n=80, dates=['2026-07-10'],
             times=['오후'], freq='1회성', loc='대면', st='대기'),
        dict(org='장안 도서산간 마을공동체 센터', spec='스마트폰활용',  ta='시니어',    n=12, dates=['2026-07-12'],
             times=['오전'], freq='1회성', loc='대면', st='대기'),

        # 매칭중 5건
        dict(org='동탄1초등학교',  spec='코딩교육',     ta='초등학생', n=20, dates=['2026-06-05'],
             times=['오후'], freq='1회성', loc='대면', st='매칭중'),
        dict(org='향남노인복지관', spec='AI 기초',      ta='시니어',   n=18, dates=['2026-06-06'],
             times=['오전'], freq='1회성', loc='대면', st='매칭중'),
        dict(org='기안중소기업 클러스터', spec='챗GPT 활용', ta='직장인', n=15, dates=['2026-06-07'],
             times=['오후'], freq='1회성', loc='대면', st='매칭중'),
        dict(org='봉담행정복지센터', spec='엑셀AI',     ta='공무원',   n=20, dates=['2026-06-08'],
             times=['오후'], freq='1회성', loc='대면', st='매칭중'),
        dict(org='동탄테크파크 입주기업협의회', spec='데이터분석', ta='직장인', n=12, dates=['2026-06-09'],
             times=['오전'], freq='1회성', loc='대면', st='매칭중'),

        # 완료 5건 (실제 매칭 진행)
        dict(org='동탄2중학교',        spec='AI 기초',      ta='중학생',    n=30, dates=['2026-06-02'],
             times=['오전'], freq='1회성', loc='대면', st='완료'),
        dict(org='향남중학교',         spec='챗GPT 활용',   ta='중학생',    n=28, dates=['2026-06-03'],
             times=['오후'], freq='1회성', loc='대면', st='완료'),
        dict(org='우정고등학교',       spec='파이썬 기초',  ta='고등학생',  n=22, dates=['2026-06-04'],
             times=['오후'], freq='1회성', loc='대면', st='완료'),
        dict(org='향남노인복지관',     spec='디지털 리터러시', ta='시니어', n=20, dates=['2026-06-05'],
             times=['오전'], freq='1회성', loc='대면', st='완료'),
        dict(org='발안일반산업단지 사업자회', spec='업무자동화', ta='직장인', n=15, dates=['2026-06-06'],
             times=['오후'], freq='1회성', loc='대면', st='완료'),
    ]

    # 정기 강의 5건 (일반)
    general.extend([
        dict(org='동탄1초등학교',   spec='스크래치',    ta='초등학생', n=18,
             dates=['2026-06-15'], times=['오후'], freq='주 1회', loc='대면', st='대기'),
        dict(org='향남노인복지관',  spec='스마트폰활용', ta='시니어',   n=15,
             dates=['2026-06-18'], times=['오전'], freq='주 1회', loc='대면', st='대기'),
        dict(org='기안장애인복지관', spec='AI 기초',     ta='장애인',   n=10,
             dates=['2026-06-20'], times=['오후'], freq='주 1회', loc='대면', st='대기'),
        dict(org='장안행정복지센터', spec='이미지 생성AI', ta='성인',   n=15,
             dates=['2026-06-22'], times=['오후'], freq='주 1회', loc='대면', st='매칭중'),
        dict(org='기안중소기업 클러스터', spec='데이터분석', ta='직장인', n=15,
             dates=['2026-06-25'], times=['오전'], freq='주 1회', loc='대면', st='완료'),
    ])

    # ── 엣지 케이스 요청 20건 ───────────────────────────────────────
    edge: list[dict] = []

    # (a) 매칭 어려운 5건
    edge.extend([
        # 희귀 전문분야 — 스크래치 + 주말 저녁
        dict(org='중부권 시민혁신학교', spec='스크래치', ta='초등학생', n=12,
             dates=['2026-06-13', '2026-06-20', '2026-06-27'], times=['저녁'], freq='주 1회',
             loc='대면', st='대기'),
        # 희귀 + 외곽
        dict(org='장안 도서산간 마을공동체 센터', spec='콘텐츠 제작', ta='시니어', n=8,
             dates=['2026-06-14'], times=['저녁'], freq='1회성', loc='대면', st='대기'),
        # 특수 시간대 — 주말 저녁
        dict(org='우정고등학교', spec='파이썬 기초', ta='고등학생', n=15,
             dates=['2026-06-21', '2026-06-28'], times=['저녁'], freq='주 1회', loc='대면', st='대기'),
        # 특수 — 토요일 저녁 / 청년창업
        dict(org='동탄대형컨벤션센터', spec='SNS마케팅', ta='청년창업자', n=25,
             dates=['2026-06-27'], times=['저녁'], freq='1회성', loc='대면', st='대기'),
        # 외곽 시니어 + 특수 시간
        dict(org='장안 도서산간 마을공동체 센터', spec='유튜브AI', ta='시니어', n=10,
             dates=['2026-06-29'], times=['저녁'], freq='1회성', loc='대면', st='대기'),
    ])

    # (b) 정기 강의 10건
    edge.extend([
        # 주 2회 × 2개월
        dict(org='동탄2중학교', spec='AI 기초', ta='중학생', n=28,
             dates=['2026-06-09'], times=['오전', '오후'], freq='주 2회 × 2개월', loc='대면', st='대기'),
        dict(org='향남중학교', spec='챗GPT 활용', ta='중학생', n=26,
             dates=['2026-06-10'], times=['오후', '저녁'], freq='주 2회 × 2개월', loc='대면', st='대기'),
        dict(org='봉담초등학교', spec='스크래치', ta='초등학생', n=20,
             dates=['2026-06-11'], times=['오후'], freq='주 2회 × 2개월', loc='대면', st='대기'),
        # 주 3회 × 3개월
        dict(org='동탄테크파크 입주기업협의회', spec='업무자동화', ta='직장인', n=18,
             dates=['2026-06-08'], times=['오전', '오후', '저녁'], freq='주 3회 × 3개월', loc='대면', st='대기'),
        dict(org='기안중소기업 클러스터', spec='엑셀AI', ta='직장인', n=16,
             dates=['2026-06-09'], times=['오전', '오후', '저녁'], freq='주 3회 × 3개월', loc='대면', st='대기'),
        dict(org='장안일반산업단지 협력회', spec='데이터분석', ta='직원', n=20,
             dates=['2026-06-10'], times=['오전', '오후', '저녁'], freq='주 3회 × 3개월', loc='대면', st='대기'),
        # 매주 1회 × 6개월
        dict(org='향남노인복지관', spec='스마트폰활용', ta='시니어', n=20,
             dates=['2026-06-04'], times=['오전'], freq='주 1회 × 6개월', loc='대면', st='대기'),
        dict(org='우정종합사회복지관', spec='디지털 리터러시', ta='시니어', n=18,
             dates=['2026-06-05'], times=['오전'], freq='주 1회 × 6개월', loc='대면', st='대기'),
        dict(org='기안장애인복지관', spec='AI 기초', ta='장애인', n=12,
             dates=['2026-06-06'], times=['오후'], freq='주 1회 × 6개월', loc='대면', st='대기'),
        dict(org='화성시청 부설 청소년교육원', spec='코딩교육', ta='초등학생', n=22,
             dates=['2026-06-07'], times=['오후'], freq='주 1회 × 6개월', loc='대면', st='완료'),
    ])

    # (c) 대규모 요청 3건 (100명 이상)
    edge.extend([
        dict(org='화성시청 대강당 (수용 300명)', spec='AI 기초', ta='공무원', n=200,
             dates=['2026-06-15'], times=['오후'], freq='1회성', loc='대면', st='대기'),
        dict(org='동탄대형컨벤션센터', spec='챗GPT 활용', ta='성인', n=150,
             dates=['2026-06-20'], times=['저녁'], freq='1회성', loc='대면', st='대기'),
        dict(org='화성시청 대강당 (수용 300명)', spec='이미지 생성AI', ta='공무원', n=120,
             dates=['2026-07-10'], times=['오후'], freq='1회성', loc='대면', st='완료'),
    ])

    # (d) VIP 요청 2건 (관공서 긴급)
    edge.extend([
        dict(org='봉담행정복지센터', spec='AI 기초', ta='공무원', n=40,
             dates=[(TODAY + timedelta(days=3)).isoformat()], times=['오후'],
             freq='1회성', loc='대면', st='매칭중'),
        dict(org='장안행정복지센터', spec='챗GPT 활용', ta='공무원', n=35,
             dates=[(TODAY + timedelta(days=5)).isoformat()], times=['오전'],
             freq='1회성', loc='대면', st='매칭중'),
    ])

    # 모두 합쳐 EducationRequest 생성
    for r in general + edge:
        rows.append(EducationRequest(
            org_id=org_id(r['org']),
            specialty_needed=r['spec'],
            target_audience=r['ta'],
            expected_students=r['n'],
            preferred_dates=r['dates'],
            preferred_times=r['times'],
            frequency=r['freq'],
            location_type=r['loc'],
            status=r['st'],
            created_at=datetime.utcnow(),
        ))

    return rows


# ──────────────────────────────────────────────────────────────────────
# 매칭 + 세션 생성 (status='완료' 신규 요청)
# ──────────────────────────────────────────────────────────────────────

def run_matching_for_completed(new_request_ids: list[int]) -> dict:
    """
    신규 추가된 요청 중 status='완료' 인 것들에 대해 매칭 실행.
    그 후 top1 매칭을 '완료' 로 승격하고 세션을 생성한다.

    반환: {requests_matched, matches_created, sessions_created, satisfaction_set}
    """
    summary = {'requests_matched': 0, 'matches_created': 0,
               'sessions_created': 0, 'satisfaction_set': 0}

    completed_ids: list[int] = []
    for rid in new_request_ids:
        req = EducationRequest.query.get(rid)
        if req and req.status == '완료':
            # find_top_matches 는 status를 '완료'로 다시 설정하므로 안전
            completed_ids.append(rid)

    for rid in completed_ids:
        # find_top_matches: 매칭 생성 + ML 로그 기록 + request.status='완료'
        result = find_top_matches(rid, top_n=5)
        if not result:
            continue
        summary['requests_matched'] += 1

        matches = Match.query.filter_by(request_id=rid)\
            .order_by(Match.match_score.desc()).all()
        if not matches:
            continue
        summary['matches_created'] += len(matches)

        # top1 → '수락' 으로 승격하고 세션 생성
        # (DB check 제약: matches.status ∈ {매칭제안, 수락, 거절, 최종확정})
        # '수락' 은 CONFIRMED_MATCH_STATUSES 이므로 세션 생성 트리거됨.
        top = matches[0]
        top.status = '수락'
        top.satisfaction_score = round(random.uniform(4.0, 5.0), 1)
        summary['satisfaction_set'] += 1
        db.session.flush()

        sessions = create_sessions_for_match(top, commit=False)
        summary['sessions_created'] += len(sessions)

        # 2위 매칭에도 일부 만족도 부여 (3.0~4.5)
        if len(matches) >= 2 and random.random() < 0.5:
            matches[1].satisfaction_score = round(random.uniform(3.0, 4.5), 1)
            summary['satisfaction_set'] += 1

    db.session.commit()
    return summary


# ──────────────────────────────────────────────────────────────────────
# ML 학습 로그 보강 (기존 매칭 중 30% 라벨링)
# ──────────────────────────────────────────────────────────────────────

def fill_ml_training_labels(target_count: int = 50) -> dict:
    """
    기존 ML 로그 중 was_selected=False, final_satisfaction=None 인 것들 중
    일부를 라벨링하여 ML 학습 가능 데이터 N건 확보.

    전략:
      - request 별로 1건은 was_selected=True/conducted=True/satisfaction 부여
      - 같은 request 의 나머지는 was_selected=False + not_selected_reason
    """
    summary = {'labeled_logs': 0, 'requests_touched': 0}

    # 기존 매칭이 있고 ML 로그가 있는 request_id 추출
    from sqlalchemy import func
    candidate_request_ids = (
        db.session.query(MLTrainingLog.request_id)
        .filter(MLTrainingLog.was_selected.is_(False))
        .filter(MLTrainingLog.final_satisfaction.is_(None))
        .group_by(MLTrainingLog.request_id)
        .having(func.count(MLTrainingLog.id) >= 3)
        .all()
    )
    candidate_request_ids = [r[0] for r in candidate_request_ids]
    random.shuffle(candidate_request_ids)

    not_selected_reasons = [
        '시간대 불일치',
        '평점이 더 높은 후보 선호',
        '기관 측 일정 변경',
        '권역 거리 고려',
        '신규 강사 대신 베테랑 선호',
    ]

    for rid in candidate_request_ids:
        if summary['labeled_logs'] >= target_count:
            break
        logs = (
            MLTrainingLog.query
            .filter_by(request_id=rid)
            .order_by(MLTrainingLog.match_score.desc())
            .all()
        )
        if not logs:
            continue

        winner = logs[0]
        # 70% 확률로 실제 진행/만족도 부여, 30%는 was_selected만 True (was_conducted False)
        winner.was_selected = True
        if random.random() < 0.7:
            winner.was_conducted = True
            winner.final_satisfaction = round(random.uniform(3.5, 5.0), 1)
        winner.updated_at = datetime.utcnow()
        summary['labeled_logs'] += 1

        for log in logs[1:]:
            if summary['labeled_logs'] >= target_count:
                break
            log.was_selected = False
            log.not_selected_reason = random.choice(not_selected_reasons)
            log.updated_at = datetime.utcnow()
            summary['labeled_logs'] += 1

        summary['requests_touched'] += 1

    db.session.commit()
    return summary


# ──────────────────────────────────────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────────────────────────────────────

def main() -> None:
    app = create_app()
    with app.app_context():
        print('=' * 70)
        print('화성시 AI 시민리더 허브 — 더미데이터 시드 시작')
        print('=' * 70)

        # 시작 시점 카운트
        before = {
            'instructor': Instructor.query.count(),
            'organization': Organization.query.count(),
            'request': EducationRequest.query.count(),
            'match': Match.query.count(),
            'session': ClassSession.query.count(),
            'ml_log': MLTrainingLog.query.count(),
        }
        print(f'[시작 전] {before}')
        print()

        # ── 1. 강사 30명 ─────────────────────────────────────────
        print('[1/5] 강사 30명 추가...')
        instructors = build_instructors()
        new_inst_ids = insert_instructors_raw(instructors)
        print(f'  → {len(new_inst_ids)} 명 추가 완료 (id {min(new_inst_ids)}~{max(new_inst_ids)})')

        # ── 2. 기관 20개 ─────────────────────────────────────────
        print('[2/5] 기관 20개 추가...')
        orgs = build_organizations()
        db.session.add_all(orgs)
        db.session.flush()
        org_map = {o.name: o for o in orgs}
        print(f'  → {len(orgs)} 개 추가 완료 (id {orgs[0].id}~{orgs[-1].id})')

        # ── 3. 교육 요청 50건 ────────────────────────────────────
        print('[3/5] 교육 요청 50건 추가...')
        requests = build_requests(org_map)
        db.session.add_all(requests)
        db.session.flush()
        new_request_ids = [r.id for r in requests]
        print(f'  → {len(requests)} 건 추가 완료 (id {requests[0].id}~{requests[-1].id})')
        db.session.commit()

        # ── 4. 매칭 + 세션 자동 생성 ─────────────────────────────
        print('[4/5] 완료 요청에 매칭+세션 자동 생성...')
        match_sum = run_matching_for_completed(new_request_ids)
        print(f'  → {match_sum}')

        # ── 5. ML 학습 로그 보강 ────────────────────────────────
        print('[5/5] ML 학습 로그 라벨링...')
        ml_sum = fill_ml_training_labels(target_count=50)
        print(f'  → {ml_sum}')

        # 종료 시점 카운트
        after = {
            'instructor': Instructor.query.count(),
            'organization': Organization.query.count(),
            'request': EducationRequest.query.count(),
            'match': Match.query.count(),
            'session': ClassSession.query.count(),
            'ml_log': MLTrainingLog.query.count(),
        }
        print()
        print('=' * 70)
        print('[종료 후]')
        for k in after:
            print(f'  {k:>15} : {before[k]:>4} → {after[k]:>4} (+{after[k]-before[k]})')
        print('=' * 70)


if __name__ == '__main__':
    main()

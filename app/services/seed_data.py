from datetime import date

from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
from app.models.organization import Organization


def seed_if_empty():
    """DB가 비어있을 때만 더미 데이터를 삽입"""
    if Instructor.query.first():
        return

    # ─────────────────────────────────────────────
    # 기관(의뢰처) 더미 데이터 10개
    # ─────────────────────────────────────────────
    organizations = [
        Organization(name='동탄종합복지관',      type='복지관',    region='동부권', contact='031-1234-5678'),
        Organization(name='향남도서관',          type='도서관',    region='서부권', contact='031-2345-6789'),
        Organization(name='봉담주민센터',        type='주민센터',  region='북부권', contact='031-3456-7890'),
        Organization(name='우정노인복지센터',    type='복지관',    region='남부권', contact='031-4567-8901'),
        Organization(name='화성시청평생학습관',  type='평생학습관', region='중부권', contact='031-5678-9012'),
        Organization(name='동탄2청소년수련관',   type='청소년관',  region='동부권', contact='031-6789-0123'),
        Organization(name='팔탄문화원',          type='문화원',    region='서부권', contact='031-7890-1234'),
        Organization(name='기안도서관',          type='도서관',    region='북부권', contact='031-8901-2345'),
        Organization(name='장안복지관',          type='복지관',    region='남부권', contact='031-9012-3456'),
        Organization(name='화성시민교육원',      type='교육원',    region='중부권', contact='031-0123-4567'),
    ]
    db.session.add_all(organizations)
    db.session.flush()

    # ─────────────────────────────────────────────
    # 강사 더미 데이터 (기본 10명 + 엣지 케이스 3명)
    #
    # cert_level 기준 (v2.0 개선):
    #   기초 : AI기초, 스마트폰활용
    #   중급 : 기초 + 챗GPT, 데이터분석, 코딩교육
    #   전문가: 모든 분야
    # ─────────────────────────────────────────────
    instructors = [
        # ── 기본 강사 (인증 등급 v2.0 기준으로 정정) ──────────────────
        Instructor(
            name='김지현', region='동부권',
            travel_range=['동부권', '중부권', '남부권'],
            specialties=['AI기초', '머신러닝'],
            cert_level='전문가',              # 머신러닝은 전문가 필요
            available_days=['월', '화', '수'],
            available_times=['오전', '오후'],
            max_classes_month=8,
            target_audience=['시니어', '성인'],
            total_classes=45, avg_rating=4.8,
            last_active=date(2026, 4, 30), is_active=True,
        ),
        Instructor(
            name='이민준', region='서부권',
            travel_range=['서부권', '중부권', '북부권'],
            specialties=['코딩교육', '파이썬'],
            cert_level='전문가',              # 파이썬은 전문가 필요
            available_days=['화', '목', '금'],
            available_times=['오후', '저녁'],
            max_classes_month=6,
            target_audience=['청소년', '성인'],
            total_classes=32, avg_rating=4.5,
            last_active=date(2026, 4, 28), is_active=True,
        ),
        Instructor(
            name='박수연', region='북부권',
            travel_range=['북부권', '중부권'],
            specialties=['영상편집', 'SNS활용'],
            cert_level='전문가',              # 영상편집, SNS활용은 전문가 필요
            available_days=['월', '수', '금'],
            available_times=['오전'],
            max_classes_month=4,
            target_audience=['성인', '시니어'],
            total_classes=28, avg_rating=4.7,
            last_active=date(2026, 5, 1), is_active=True,
        ),
        Instructor(
            name='최동훈', region='남부권',
            travel_range=['남부권', '중부권', '동부권'],
            specialties=['엑셀', '업무자동화'],
            cert_level='전문가',
            available_days=['월', '화', '수', '목'],
            available_times=['오전', '오후'],
            max_classes_month=10,
            target_audience=['성인'],
            total_classes=60, avg_rating=4.9,
            last_active=date(2026, 5, 2), is_active=True,
        ),
        Instructor(
            name='정유리', region='중부권',
            travel_range=['중부권', '동부권', '서부권', '북부권', '남부권'],
            specialties=['스마트폰활용', '인터넷뱅킹', '키오스크'],
            cert_level='전문가',
            available_days=['월', '화', '수', '목', '금'],
            available_times=['오전', '오후', '저녁'],
            max_classes_month=12,
            target_audience=['시니어'],
            total_classes=80, avg_rating=4.9,
            last_active=date(2026, 5, 3), is_active=True,
        ),
        Instructor(
            name='한상철', region='동부권',
            travel_range=['동부권', '중부권'],
            specialties=['데이터분석', '인공지능활용'],
            cert_level='전문가',
            available_days=['화', '목'],
            available_times=['오후'],
            max_classes_month=5,
            target_audience=['성인'],
            total_classes=20, avg_rating=4.6,
            last_active=date(2026, 4, 25), is_active=True,
        ),
        Instructor(
            name='윤미래', region='서부권',
            travel_range=['서부권', '북부권'],
            specialties=['유튜브제작', '디지털마케팅'],
            cert_level='전문가',              # 유튜브제작, 디지털마케팅은 전문가 필요
            available_days=['수', '금', '토'],
            available_times=['오후', '저녁'],
            max_classes_month=6,
            target_audience=['청소년', '성인'],
            total_classes=15, avg_rating=4.4,
            last_active=date(2026, 5, 1), is_active=True,
        ),
        Instructor(
            name='임태양', region='북부권',
            travel_range=['북부권', '서부권', '중부권'],
            specialties=['앱개발', '웹개발'],
            cert_level='전문가',
            available_days=['월', '화', '목'],
            available_times=['오후', '저녁'],
            max_classes_month=8,
            target_audience=['청소년', '성인'],
            total_classes=35, avg_rating=4.7,
            last_active=date(2026, 5, 2), is_active=True,
        ),
        Instructor(
            # 기초 등급 강사 - AI기초, 스마트폰활용 요청에만 매칭 가능
            name='강나연', region='남부권',
            travel_range=['남부권', '중부권'],
            specialties=['모바일앱', '스마트폰활용'],
            cert_level='기초',
            available_days=['화', '수', '목'],
            available_times=['오전', '오후'],
            max_classes_month=6,
            target_audience=['시니어', '성인'],
            total_classes=18, avg_rating=4.3,
            last_active=date(2026, 4, 20), is_active=True,
        ),
        Instructor(
            name='오준혁', region='중부권',
            travel_range=['중부권', '동부권', '남부권'],
            specialties=['오피스활용', 'RPA'],
            cert_level='전문가',              # 오피스활용, RPA는 전문가 필요
            available_days=['월', '수', '금'],
            available_times=['오전'],
            max_classes_month=7,
            target_audience=['성인'],
            total_classes=25, avg_rating=4.5,
            last_active=date(2026, 4, 30), is_active=True,
        ),

        # ── 엣지 케이스 강사 (테스트용) ─────────────────────────────
        Instructor(
            # 테스트: is_active=False → 매칭에서 완전 제외 확인용
            name='신민호', region='동부권',
            travel_range=['동부권', '중부권'],
            specialties=['AI기초', '챗GPT'],
            cert_level='전문가',
            available_days=['월', '화', '수', '목', '금'],
            available_times=['오전', '오후', '저녁'],
            max_classes_month=10,
            target_audience=['시니어', '성인', '청소년'],
            total_classes=50, avg_rating=5.0,
            last_active=date(2026, 5, 10),
            is_active=False,               # ← 비활성: 매칭 제외
        ),
        Instructor(
            # 테스트: 6개월 초과 미활동 → -10점 패널티 확인용
            name='류진아', region='동부권',
            travel_range=['동부권', '중부권', '남부권'],
            specialties=['AI기초', '머신러닝'],
            cert_level='전문가',
            available_days=['월', '화', '수'],
            available_times=['오전', '오후'],
            max_classes_month=6,
            target_audience=['시니어', '성인'],
            total_classes=10, avg_rating=4.6,
            last_active=date(2025, 8, 1),  # ← 약 9개월 전: -10점 패널티
            is_active=True,
        ),
        Instructor(
            # 테스트: 중급 등급 강사 (코딩교육·데이터분석 요청만 매칭 가능)
            name='백지수', region='동부권',
            travel_range=['동부권', '중부권'],
            specialties=['코딩교육', '데이터분석'],
            cert_level='중급',
            available_days=['월', '수', '금'],
            available_times=['오후', '저녁'],
            max_classes_month=5,
            target_audience=['청소년', '성인'],
            total_classes=22, avg_rating=4.6,
            last_active=date(2026, 5, 10), is_active=True,
        ),
    ]
    db.session.add_all(instructors)
    db.session.flush()

    # ─────────────────────────────────────────────
    # 교육 요청 더미 데이터 10개
    # ─────────────────────────────────────────────
    requests = [
        EducationRequest(
            org_id=organizations[0].id,
            specialty_needed='AI기초',
            target_audience='시니어',
            expected_students=20,
            preferred_dates=['2026-06-01', '2026-06-08', '2026-06-15'],
            preferred_times=['오전', '오후'],
            frequency='주 1회',
            location_type='대면',
            status='대기중',
        ),
        EducationRequest(
            org_id=organizations[1].id,
            specialty_needed='코딩교육',
            target_audience='청소년',
            expected_students=15,
            preferred_dates=['2026-06-03', '2026-06-10'],
            preferred_times=['오후'],
            frequency='격주',
            location_type='대면',
            status='대기중',
        ),
        EducationRequest(
            org_id=organizations[2].id,
            specialty_needed='스마트폰활용',
            target_audience='시니어',
            expected_students=25,
            preferred_dates=['2026-05-20', '2026-05-27'],
            preferred_times=['오전'],
            frequency='주 1회',
            location_type='대면',
            status='대기중',
        ),
        EducationRequest(
            org_id=organizations[3].id,
            specialty_needed='엑셀',
            target_audience='성인',
            expected_students=10,
            preferred_dates=['2026-06-05', '2026-06-12', '2026-06-19'],
            preferred_times=['오후', '저녁'],
            frequency='주 2회',
            location_type='온라인',
            status='대기중',
        ),
        EducationRequest(
            org_id=organizations[4].id,
            specialty_needed='영상편집',
            target_audience='성인',
            expected_students=12,
            preferred_dates=['2026-06-02', '2026-06-09'],
            preferred_times=['오후'],
            frequency='격주',
            location_type='혼합',
            status='대기중',
        ),
        EducationRequest(
            org_id=organizations[5].id,
            specialty_needed='파이썬',
            target_audience='청소년',
            expected_students=18,
            preferred_dates=['2026-06-04', '2026-06-11', '2026-06-18'],
            preferred_times=['오후', '저녁'],
            frequency='주 1회',
            location_type='대면',
            status='대기중',
        ),
        EducationRequest(
            org_id=organizations[6].id,
            specialty_needed='SNS활용',
            target_audience='성인',
            expected_students=8,
            preferred_dates=['2026-05-25', '2026-06-01'],
            preferred_times=['오전'],
            frequency='월 2회',
            location_type='대면',
            status='대기중',
        ),
        EducationRequest(
            org_id=organizations[7].id,
            specialty_needed='데이터분석',
            target_audience='성인',
            expected_students=10,
            preferred_dates=['2026-06-06', '2026-06-13'],
            preferred_times=['오후'],
            frequency='격주',
            location_type='온라인',
            status='대기중',
        ),
        EducationRequest(
            org_id=organizations[8].id,
            specialty_needed='인터넷뱅킹',
            target_audience='시니어',
            expected_students=30,
            preferred_dates=['2026-06-01', '2026-06-08', '2026-06-15', '2026-06-22'],
            preferred_times=['오전'],
            frequency='주 1회',
            location_type='대면',
            status='대기중',
        ),
        EducationRequest(
            org_id=organizations[9].id,
            specialty_needed='업무자동화',
            target_audience='성인',
            expected_students=15,
            preferred_dates=['2026-06-03', '2026-06-10', '2026-06-17'],
            preferred_times=['오전', '오후'],
            frequency='주 1회',
            location_type='혼합',
            status='대기중',
        ),
    ]
    db.session.add_all(requests)
    db.session.commit()

    print('✅ 더미 데이터 삽입 완료 (기관 10개, 강사 13개, 교육 요청 10개)')

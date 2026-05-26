"""
수요처(기관) 4가지 시나리오로 매칭 알고리즘이 의도대로 동작하는지 검증.

각 시나리오마다:
  · 강사 풀(공통 또는 시나리오별)을 in-memory DB 에 적재
  · 수요처(기관 + 교육요청)를 생성
  · find_top_matches 실행
  · 점수표/매칭모드/주요 검증 포인트 출력

시나리오 요약
  ① 동부권 복지관 / 스마트폰활용 / 시니어 정기 강의
     - 기본 점수(권역+분야+시간) + 정기 보너스(D-1) + 복지관 시니어 보너스(B-1)
  ② 서부권 초등학교 / 코딩교육 / 청소년 비정기
     - 학교 + 누적 강의 30회 이상 강사 +10점 보너스(B-1) 검증
  ③ 북부권 IT기업 / 챗GPT / 성인
     - 전문가 cert 보너스(B-1) + 인증등급 필터로 기초 강사 자동 제외 검증
  ④ 남부권 도서관 / 영상편집 / 성인 (남부권/인접권역 영상편집 강사 없음)
     - '유사분야확장' 또는 '조건완화추천' 모드 진입 확인
"""
import os
os.environ['FLASK_ENV'] = 'testing'

from datetime import date

from app import create_app
from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
from app.models.organization import Organization
from app.services.matching_service import find_top_matches


# ───────────── 공통 출력 유틸 ───────────────────────────────────────

def _print_header(no: int, title: str, lines: list[str]):
    print('=' * 90)
    print(f'시나리오 {no} ─ {title}')
    for ln in lines:
        print(f'   {ln}')
    print('=' * 90)


def _print_request(req: EducationRequest):
    org = req.organization
    print(f'  [수요처]  {org.name} ({org.type}, {org.region})')
    print(f'  [요청]    분야={req.specialty_needed}  대상={req.target_audience}'
          f'  시간={req.preferred_times}  빈도={req.frequency}')


def _print_table(result: dict):
    matches = result.get('matches', [])
    print(f"\n  ▶ match_mode : {result.get('match_mode')}   "
          f"({result.get('match_mode_reason')})")
    print(f"  ▶ 총 매칭 수 : {result.get('total_count', 0)}명")

    hdr = (f"  {'순위':<4}{'강사':<10}{'권역':<7}{'분야':<14}"
           f"{'총점':>6}{'권역':>6}{'분야':>6}{'시간':>5}"
           f"{'평점':>6}{'활동':>6}  유형")
    print('\n' + hdr)
    print('  ' + '─' * 86)
    for i, m in enumerate(matches, 1):
        sd = m.get('score_detail', {})
        specs = ', '.join(m.get('instructor_specialties') or [])[:13]
        print(
            f"  {i:<4}{m['instructor_name']:<10}"
            f"{m['instructor_region']:<7}{specs:<14}"
            f"{m['match_score']:>6.1f}"
            f"{sd.get('권역_점수',0):>6.1f}"
            f"{sd.get('전문분야_점수',0):>6.1f}"
            f"{sd.get('시간대_점수',0):>5.1f}"
            f"{sd.get('평점_보너스',0):>6.1f}"
            f"{sd.get('활동일_패널티',0):>6.1f}  "
            f"{m.get('match_type','-')}"
        )

    # 상위 1명 breakdown 자세히
    if matches:
        top = matches[0]
        bd = top.get('breakdown', {})
        print(f"\n  [상위 1명 보너스/패널티 — {top['instructor_name']}]")
        for b in bd.get('bonuses', []):
            print(f"    +{b['점수']:>4.0f}  {b['항목']:<18} {b['사유']}")
        for p in bd.get('penalties', []):
            print(f"    {p['점수']:>5.0f}  {p['항목']:<18} {p['사유']}")


def _print_checks(checks: list[tuple[str, bool]]):
    print('\n  ── 검증 결과 ────────────────────────────────────────────')
    all_ok = True
    for desc, ok in checks:
        icon = '✅' if ok else '❌'
        print(f'  {icon}  {desc}')
        if not ok:
            all_ok = False
    print(f"\n  → 결과: {'✅ 통과' if all_ok else '❌ 일부 실패'}\n")


# ─────────── 공통 강사 풀 (시나리오마다 db.create_all 직후 호출) ─────

def _seed_common_instructors():
    """다양한 권역·분야·등급의 강사 풀을 만들어 둔다."""
    pool = [
        # 동부권
        Instructor(name='김정민', region='동부권',
                   travel_range=['동부권', '중부권'],
                   specialties=['스마트폰활용', '인터넷뱅킹'],
                   cert_level=3,
                   available_days=['월', '수'], available_times=['오전'],
                   max_classes_month=6, target_audience=['시니어'],
                   total_classes=42, avg_rating=4.8,
                   last_active=date(2026, 5, 10), is_active=True),
        Instructor(name='박서연', region='동부권',
                   travel_range=['동부권', '남부권'],
                   specialties=['스마트폰활용'],
                   cert_level=1,
                   available_days=['화', '목'], available_times=['오전'],
                   max_classes_month=4, target_audience=['시니어', '성인'],
                   total_classes=12, avg_rating=4.5,
                   last_active=date(2026, 5, 5), is_active=True),

        # 서부권
        Instructor(name='이도현', region='서부권',
                   travel_range=['서부권', '중부권', '북부권'],
                   specialties=['코딩교육', '파이썬'],
                   cert_level=3,
                   available_days=['월', '화', '수'],
                   available_times=['오후'],
                   max_classes_month=6, target_audience=['청소년', '성인'],
                   total_classes=55, avg_rating=4.7,
                   last_active=date(2026, 5, 12), is_active=True),
        Instructor(name='최유나', region='서부권',
                   travel_range=['서부권'],
                   specialties=['코딩교육'],
                   cert_level=2,
                   available_days=['수', '목'], available_times=['오후'],
                   max_classes_month=4, target_audience=['청소년'],
                   total_classes=8, avg_rating=4.4,
                   last_active=date(2026, 5, 2), is_active=True),

        # 북부권
        Instructor(name='장은우', region='북부권',
                   travel_range=['북부권', '중부권'],
                   specialties=['챗GPT', 'AI기초'],
                   cert_level=3,
                   available_days=['월', '수'], available_times=['오전', '오후'],
                   max_classes_month=6, target_audience=['성인'],
                   total_classes=38, avg_rating=4.9,
                   last_active=date(2026, 5, 11), is_active=True),
        Instructor(name='한지원', region='북부권',
                   travel_range=['북부권'],
                   specialties=['AI기초'],
                   cert_level=1,  # 챗GPT 직접 강의 불가
                   available_days=['화'], available_times=['오전'],
                   max_classes_month=3, target_audience=['성인'],
                   total_classes=6, avg_rating=4.6,
                   last_active=date(2026, 5, 1), is_active=True),

        # 중부권 (전권역 이동 가능 만능형)
        Instructor(name='오세영', region='중부권',
                   travel_range=['중부권', '동부권', '서부권',
                                 '북부권', '남부권'],
                   specialties=['챗GPT', '데이터분석', '엑셀'],
                   cert_level=3,
                   available_days=['월', '화', '수', '목', '금'],
                   available_times=['오전', '오후'],
                   max_classes_month=10, target_audience=['성인', '시니어'],
                   total_classes=70, avg_rating=4.85,
                   last_active=date(2026, 5, 14), is_active=True),

        # 남부권
        Instructor(name='윤하늘', region='남부권',
                   travel_range=['남부권', '중부권'],
                   specialties=['엑셀', '업무자동화'],
                   cert_level=3,
                   available_days=['월', '수'], available_times=['오후'],
                   max_classes_month=5, target_audience=['성인'],
                   total_classes=25, avg_rating=4.6,
                   last_active=date(2026, 5, 3), is_active=True),
    ]
    db.session.add_all(pool)
    db.session.commit()
    return pool


# ───────────── 시나리오 1: 복지관 / 스마트폰 / 시니어 정기 ─────────

def scenario_1():
    _print_header(
        1, '동부권 복지관 — 스마트폰활용 정기 강의 (시니어 대상)',
        [
            '· 정기 강의 보너스(D-1) 및 복지관×시니어 강사 보너스(B-1) 확인',
            '· 동부권 정확 일치 강사 우선 + 인접 권역(중부권) 자동 합류',
        ]
    )
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        _seed_common_instructors()

        org = Organization(name='동탄종합복지관', type='복지관',
                           region='동부권', contact='-')
        db.session.add(org)
        db.session.flush()
        req = EducationRequest(
            org_id=org.id, specialty_needed='스마트폰활용',
            target_audience='시니어', expected_students=20,
            preferred_dates=['2026-06-01', '2026-06-08'],
            preferred_times=['오전'],
            frequency='주 1회 정기', location_type='대면',
            status='대기',
        )
        db.session.add(req)
        db.session.commit()
        _print_request(req)

        result = find_top_matches(req.id)
        _print_table(result)

        matches = result['matches']
        names = [m['instructor_name'] for m in matches]
        top_name = names[0] if names else None
        top_score = matches[0]['match_score'] if matches else 0
        # 김정민: 동부권(40) + 스마트폰활용(40) + 오전(20) = 100
        #        + 평점 4.8 (+10) + 활동최근(0) + 복지관×시니어(+10)
        #        + 정기 보너스(+10, 월 6회 ≥ 3) = 130
        _print_checks([
            ('정상 매칭 모드', result['match_mode'] == '정상'),
            ('1위 = 김정민(동부권 전문가)', top_name == '김정민'),
            ('1위 총점 130점 (정기+복지관 보너스 모두 적용)',
             abs(top_score - 130.0) < 0.1),
            ('박서연(기초 등급) 결과 포함 (AI기초/스마트폰 = 기초 cert 허용)',
             '박서연' in names),
            ('상위 2명은 분야 완전 일치 강사(김정민/박서연)',
             set(names[:2]) == {'김정민', '박서연'}),
            ('분야 불일치 강사는 후순위 (기본점수 100점 > 분야 미일치 강사)',
             matches[0]['match_score'] > matches[2]['match_score'] + 30
             if len(matches) >= 3 else False),
        ])
        db.drop_all()


# ───────────── 시나리오 2: 학교 / 코딩교육 / 청소년 ───────────────

def scenario_2():
    _print_header(
        2, '서부권 초등학교 — 코딩교육 (청소년 대상, 비정기)',
        [
            '· 학교 + 누적 30회 이상 강사 → +10점 (B-1)',
            '· 누적 강의 부족한 중급 강사도 후보에 포함되지만 후순위',
        ]
    )
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        _seed_common_instructors()

        org = Organization(name='향남초등학교', type='학교',
                           region='서부권', contact='-')
        db.session.add(org)
        db.session.flush()
        req = EducationRequest(
            org_id=org.id, specialty_needed='코딩교육',
            target_audience='청소년', expected_students=18,
            preferred_dates=['2026-06-04'],
            preferred_times=['오후'],
            frequency='월 2회', location_type='대면',
            status='대기',
        )
        db.session.add(req)
        db.session.commit()
        _print_request(req)

        result = find_top_matches(req.id)
        _print_table(result)

        matches = result['matches']
        names = [m['instructor_name'] for m in matches]
        scores = {m['instructor_name']: m['match_score'] for m in matches}
        # 이도현: 서부권(40) + 코딩교육(40) + 오후(20) = 100
        #        + 평점 4.7(+5) + 학교×30회이상(+10) = 115
        # 최유나: 서부권(40) + 코딩교육(40) + 오후(20) = 100
        #        + 평점 4.4(0) + 학교 누적부족(0) + 신규강사(<5 아님이라 0)
        #        실제 total_classes=8 → 신규 아님 → 100점
        _print_checks([
            ('정상 매칭 모드', result['match_mode'] == '정상'),
            ('이도현 1위 (학교×경험30+ 보너스 적용)',
             names and names[0] == '이도현'),
            ('이도현 115점 (학교 +10 보너스)',
             abs(scores.get('이도현', 0) - 115.0) < 0.1),
            ('최유나 결과 포함 (중급 등급도 코딩교육 가능)',
             '최유나' in names),
            ('최유나 100점 (보너스 없음)',
             abs(scores.get('최유나', 0) - 100.0) < 0.1),
            ('상위 2명은 분야 완전 일치 강사(이도현/최유나)',
             set(names[:2]) == {'이도현', '최유나'}),
            ('이도현 > 최유나 (학교×경험 보너스로 15점 차)',
             scores.get('이도현', 0) - scores.get('최유나', 0) == 15.0),
        ])
        db.drop_all()


# ───────────── 시나리오 3: 기업 / 챗GPT / 성인 ────────────────────

def scenario_3():
    _print_header(
        3, '북부권 IT기업 — 챗GPT 사내 교육 (성인 대상)',
        [
            '· 기업 + 전문가 cert 강사 → +10점 (B-1)',
            '· 챗GPT 는 중급 이상만 강의 가능 → 기초 강사 자동 제외',
        ]
    )
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        _seed_common_instructors()

        org = Organization(name='기안테크', type='기업',
                           region='북부권', contact='-')
        db.session.add(org)
        db.session.flush()
        req = EducationRequest(
            org_id=org.id, specialty_needed='챗GPT',
            target_audience='성인', expected_students=15,
            preferred_dates=['2026-06-05'],
            preferred_times=['오후'],
            frequency='1회성', location_type='대면',
            status='대기',
        )
        db.session.add(req)
        db.session.commit()
        _print_request(req)

        result = find_top_matches(req.id)
        _print_table(result)

        matches = result['matches']
        names = [m['instructor_name'] for m in matches]
        scores = {m['instructor_name']: m['match_score'] for m in matches}
        # 장은우: 북부권(40) + 챗GPT(40) + 오후(20) = 100
        #        + 평점 4.9(+10) + 기업×전문가(+10) = 120
        # 오세영(중부권, travel_range 포함, 챗GPT 전문가): 권역(20 인접) + 40 + 20 = 80
        #        + 평점 4.85(+10) + 기업×전문가(+10) = 100
        _print_checks([
            ('정상 매칭 모드', result['match_mode'] == '정상'),
            ('장은우 1위(북부권 전문가)',
             names and names[0] == '장은우'),
            ('장은우 120점 (기업×전문가 +10 적용)',
             abs(scores.get('장은우', 0) - 120.0) < 0.1),
            ('오세영(중부권) 인접 권역 합류',
             '오세영' in names),
            ('한지원(기초 cert) 결과에서 자동 제외 (챗GPT 강의 불가)',
             '한지원' not in names),
        ])
        db.drop_all()


# ───────────── 시나리오 4: 도서관 / 영상편집 / 성인 (강사 없음) ───

def scenario_4():
    _print_header(
        4, '남부권 도서관 — 영상편집 강의 (남부권 영상편집 강사 없음)',
        [
            '· 영상편집 강사가 풀에 없으므로 유사 그룹(미디어·콘텐츠) 자동 확장',
            "· 또는 분야 불일치로 match_mode 가 '유사분야확장' 또는 '조건완화추천'",
        ]
    )
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        _seed_common_instructors()

        org = Organization(name='우정도서관', type='도서관',
                           region='남부권', contact='-')
        db.session.add(org)
        db.session.flush()
        req = EducationRequest(
            org_id=org.id, specialty_needed='영상편집',
            target_audience='성인', expected_students=10,
            preferred_dates=['2026-06-07'],
            preferred_times=['오후'],
            frequency='1회성', location_type='대면',
            status='대기',
        )
        db.session.add(req)
        db.session.commit()
        _print_request(req)

        result = find_top_matches(req.id)
        _print_table(result)

        matches = result['matches']
        mode = result['match_mode']
        # 공통 풀에는 영상편집/SNS활용/유튜브제작 누구도 없음 → '최선추천' 가능성 큼
        _print_checks([
            ('영상편집 직접 일치 강사 없음 → 정상 모드 아님',
             mode != '정상'),
            ('match_mode 가 fallback 계열 (최선추천/유사분야확장/조건완화)',
             mode in ('최선추천', '유사분야확장', '조건완화추천')),
            ('결과 강사는 존재함 (fallback 으로라도 추천)',
             len(matches) > 0),
            ('failure_reasons 가 채워짐 (5명 미만 또는 분야 불일치)',
             bool(result.get('failure_reasons')) or len(matches) >= 5),
        ])
        if result.get('failure_reasons'):
            print('  ▶ 실패 원인 분석:')
            for r in result['failure_reasons']:
                print(f"     - [{r['code']}] {r['message']}")
        db.drop_all()


# ─────────────────────── 실행 ─────────────────────────────────────

if __name__ == '__main__':
    scenario_1()
    scenario_2()
    scenario_3()
    scenario_4()

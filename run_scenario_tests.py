"""
4가지 시나리오 테스트 스크립트
각 테스트는 격리된 인메모리 DB를 사용하여 독립적으로 실행
"""
import os
os.environ['FLASK_ENV'] = 'testing'

from datetime import date
from app import create_app
from app.extensions import db
from app.models.instructor import Instructor
from app.models.organization import Organization
from app.models.education_request import EducationRequest
from app.services.matching_service import find_top_matches

# ── 공통 출력 유틸 ─────────────────────────────────────────────────

def print_result_table(result: dict, checks: list[tuple[str, bool]]):
    matches = result.get('matches', [])
    mode    = result.get('match_mode', '-')
    reason  = result.get('match_mode_reason', '-')

    print(f"\n  ▶ match_mode   : {mode}")
    print(f"  ▶ 사유         : {reason}")
    print(f"  ▶ 총 매칭 수   : {result.get('total_count', 0)}명\n")

    hdr = f"  {'순위':<3} {'강사명':<8} {'권역':<6} {'전문분야':<14} " \
          f"{'총점':>5} {'권역':>5} {'전문분야':>6} {'시간':>4} " \
          f"{'평점보너스':>6} {'활동패널티':>6} {'유형':<10}"
    print(hdr)
    print("  " + "─" * 100)

    for i, m in enumerate(matches, 1):
        sd = m.get('score_detail', {})
        specs = ', '.join(m.get('instructor_specialties') or [])
        print(
            f"  {i:<3} {m['instructor_name']:<8} "
            f"{m['instructor_region']:<6} "
            f"{specs:<14} "
            f"{m['match_score']:>5.1f} "
            f"{sd.get('권역_점수',0):>5.1f} "
            f"{sd.get('전문분야_점수',0):>6.1f} "
            f"{sd.get('시간대_점수',0):>4.1f} "
            f"{sd.get('평점_보너스',0):>6.1f} "
            f"{sd.get('활동일_패널티',0):>6.1f} "
            f"{m.get('match_type','-'):<10}"
        )

    print("\n  ── 검증 결과 ──────────────────────────────────────────────")
    all_ok = True
    for desc, ok in checks:
        icon = "✅" if ok else "❌"
        print(f"  {icon}  {desc}")
        if not ok:
            all_ok = False
    print(f"\n  → 정상 작동 여부: {'✅ 통과' if all_ok else '❌ 일부 실패'}\n")


# ── 테스트 1 : 정상 매칭 (A 기능 확인) ───────────────────────────

def test_1():
    print("=" * 80)
    print("테스트 1 ─ 정상 매칭 (A 기능 확인)")
    print("  동부권 기관이 AI기초 교육을 평일 오전 요청")
    print("  → 평점 보너스·인증 등급·활동일 패널티 반영 여부 확인")
    print("=" * 80)

    app = create_app('testing')
    with app.app_context():
        db.create_all()

        org = Organization(name='동부권테스트기관', type='복지관', region='동부권', contact='000')
        db.session.add(org)
        db.session.flush()

        instructors = [
            # 강사A: 4.9점 평점 → +10보너스, 최근 활동
            Instructor(name='강사A', region='동부권', travel_range=['동부권'],
                       specialties=['AI기초'], cert_level=3,
                       available_days=['월','화','수'], available_times=['오전'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=30, avg_rating=4.9,
                       last_active=date(2026,5,1), is_active=True),
            # 강사B: 4.6점 → +5보너스, 강사C와 동점 → 평점으로 앞서기
            Instructor(name='강사B', region='동부권', travel_range=['동부권'],
                       specialties=['AI기초'], cert_level=3,
                       available_days=['월','화','수'], available_times=['오전'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=20, avg_rating=4.6,
                       last_active=date(2026,5,1), is_active=True),
            # 강사C: 기초 cert (AI기초 가능), 4.5점 → +5보너스, 강사B와 동점
            Instructor(name='강사C', region='동부권', travel_range=['동부권'],
                       specialties=['AI기초'], cert_level=1,
                       available_days=['월','화','수'], available_times=['오전'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=20, avg_rating=4.5,
                       last_active=date(2026,5,1), is_active=True),
            # 강사D: 4.3점 → 보너스 없음
            Instructor(name='강사D', region='동부권', travel_range=['동부권'],
                       specialties=['AI기초'], cert_level=3,
                       available_days=['월','화','수'], available_times=['오전'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=15, avg_rating=4.3,
                       last_active=date(2026,5,1), is_active=True),
            # 강사E: 9개월 전 활동 → -10점 패널티
            Instructor(name='강사E', region='동부권', travel_range=['동부권'],
                       specialties=['AI기초'], cert_level=3,
                       available_days=['월','화','수'], available_times=['오전'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=25, avg_rating=4.6,
                       last_active=date(2025,8,1), is_active=True),
            # 강사F: 비활성 → 결과 제외
            Instructor(name='강사F', region='동부권', travel_range=['동부권'],
                       specialties=['AI기초'], cert_level=3,
                       available_days=['월','화','수'], available_times=['오전'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=50, avg_rating=5.0,
                       last_active=date(2026,5,1), is_active=False),
        ]
        db.session.add_all(instructors)
        db.session.flush()

        req = EducationRequest(
            org_id=org.id, specialty_needed='AI기초',
            target_audience='성인', expected_students=20,
            preferred_dates=['2026-06-01'], preferred_times=['오전'],
            frequency='주 1회', location_type='대면', status='대기',
        )
        db.session.add(req)
        db.session.commit()

        result = find_top_matches(req.id)
        matches = result['matches']
        ids     = [m['instructor_id'] for m in matches]
        scores  = {m['instructor_name']: m['match_score'] for m in matches}
        bonuses = {m['instructor_name']: m['score_detail']['평점_보너스'] for m in matches}
        pens    = {m['instructor_name']: m['score_detail']['활동일_패널티'] for m in matches}

        f_id = next((i.id for i in instructors if i.name == '강사F'), None)

        checks = [
            ("비활성 강사F 제외됨",           f_id not in ids),
            ("강사A 총점 110점 (보너스+10)",   abs(scores.get('강사A',0) - 110.0) < 0.1),
            ("강사E 총점 95점 (패널티-10)",    abs(scores.get('강사E',0) - 95.0)  < 0.1),
            ("강사A 평점 보너스 10점",         bonuses.get('강사A',0) == 10.0),
            ("강사E 활동일 패널티 -10점",      pens.get('강사E',0) == -10.0),
            ("기초 cert 강사C 매칭 포함",      '강사C' in scores),
            ("강사B(4.6) 강사C(4.5) 보다 앞 순위",
             ids.index(next(i.id for i in instructors if i.name=='강사B')) <
             ids.index(next(i.id for i in instructors if i.name=='강사C'))
             if '강사C' in scores else False),
            ("match_mode 정상",                result['match_mode'] == '정상'),
        ]

        print_result_table(result, checks)
        db.drop_all()


# ── 테스트 2 : 동점자 처리 (B 기능 확인) ─────────────────────────

def test_2():
    print("=" * 80)
    print("테스트 2 ─ 동점자 처리 (B 기능 확인)")
    print("  서부권 기관이 코딩교육을 오후 요청")
    print("  → 동점자는 평점 → 누적 강의 횟수 순 / 상위 5명 반환 확인")
    print("=" * 80)

    app = create_app('testing')
    with app.app_context():
        db.create_all()

        org = Organization(name='서부권테스트기관', type='도서관', region='서부권', contact='000')
        db.session.add(org)
        db.session.flush()

        # 6명 모두 서부권·코딩교육·오후 가능 → 기본 점수 동일
        # 차이: avg_rating, total_classes
        data = [
            ('강사1', 4.8, 80),   # 110점 그룹 - 누적강의 최다
            ('강사2', 4.8, 50),   # 110점 그룹
            ('강사3', 4.8, 30),   # 110점 그룹 - 누적강의 최소
            ('강사4', 4.6, 60),   # 105점 그룹
            ('강사5', 4.5, 100),  # 105점 그룹 - 누적강의 많지만 평점 낮음
            ('강사6', 4.3, 200),  # 100점 - top5 밖
        ]
        instructors = []
        for name, rating, classes in data:
            inst = Instructor(
                name=name, region='서부권', travel_range=['서부권'],
                specialties=['코딩교육'], cert_level=3,
                available_days=['월','화','수'], available_times=['오후'],
                max_classes_month=6, target_audience=['성인'],
                total_classes=classes, avg_rating=rating,
                last_active=date(2026,5,10), is_active=True,
            )
            db.session.add(inst)
            instructors.append(inst)
        db.session.flush()

        req = EducationRequest(
            org_id=org.id, specialty_needed='코딩교육',
            target_audience='성인', expected_students=15,
            preferred_dates=['2026-06-01'], preferred_times=['오후'],
            frequency='주 1회', location_type='대면', status='대기',
        )
        db.session.add(req)
        db.session.commit()

        result = find_top_matches(req.id)
        matches = result['matches']
        names   = [m['instructor_name'] for m in matches]

        # 기대 순서: 강사1 > 강사2 > 강사3 (동점 110, 평점 동일 → 강의수 순)
        #           > 강사4 > 강사5 (동점 105, 평점 차이 → 4.6 > 4.5)
        checks = [
            ("상위 5명 반환",                      len(matches) == 5),
            ("강사6 제외 (100점, 6위)",             '강사6' not in names),
            ("강사1 1위 (110점, 강의80)",           names[0] == '강사1' if names else False),
            ("강사2 2위 (110점, 강의50)",           names[1] == '강사2' if len(names)>1 else False),
            ("강사3 3위 (110점, 강의30)",           names[2] == '강사3' if len(names)>2 else False),
            ("강사4 4위 (105점, 평점4.6)",          names[3] == '강사4' if len(names)>3 else False),
            ("강사5 5위 (105점, 평점4.5)",          names[4] == '강사5' if len(names)>4 else False),
            ("match_mode 정상",                     result['match_mode'] == '정상'),
        ]

        print_result_table(result, checks)
        db.drop_all()


# ── 테스트 3 : 해당 권역 강사 없음 (C 기능 확인) ─────────────────

def test_3():
    print("=" * 80)
    print("테스트 3 ─ 해당 권역 강사 없음 (C 기능 확인)")
    print("  서부권 기관이 앱개발 교육을 오후 요청 (서부권 강사 미등록)")
    print("  → 인접 권역(북부권·중부권) 강사 자동 탐색 확인")
    print("=" * 80)

    app = create_app('testing')
    with app.app_context():
        db.create_all()

        # 서부권 기관
        org = Organization(name='서부권테스트기관', type='문화원', region='서부권', contact='000')
        db.session.add(org)
        db.session.flush()

        # 서부권 강사 없음 → 북부권·중부권에만 배치
        # 북부권·중부권은 서부권의 인접 권역
        instructors = [
            Instructor(name='북부권강사1', region='북부권',
                       travel_range=['북부권','서부권','중부권'],
                       specialties=['앱개발'], cert_level=3,
                       available_days=['월','화'], available_times=['오후'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=40, avg_rating=4.8,
                       last_active=date(2026,5,5), is_active=True),
            Instructor(name='북부권강사2', region='북부권',
                       travel_range=['북부권','중부권'],
                       specialties=['앱개발','웹개발'], cert_level=3,
                       available_days=['화','수'], available_times=['오후'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=25, avg_rating=4.5,
                       last_active=date(2026,5,3), is_active=True),
            Instructor(name='중부권강사1', region='중부권',
                       travel_range=['중부권','서부권','북부권'],
                       specialties=['앱개발'], cert_level=3,
                       available_days=['월','수'], available_times=['오후'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=30, avg_rating=4.7,
                       last_active=date(2026,5,1), is_active=True),
        ]
        db.session.add_all(instructors)
        db.session.flush()

        req = EducationRequest(
            org_id=org.id, specialty_needed='앱개발',
            target_audience='성인', expected_students=12,
            preferred_dates=['2026-06-01'], preferred_times=['오후'],
            frequency='격주', location_type='대면', status='대기',
        )
        db.session.add(req)
        db.session.commit()

        result = find_top_matches(req.id)
        matches = result['matches']
        region_scores = [m['score_detail']['권역_점수'] for m in matches]

        checks = [
            ("match_mode = '인접권역추천'",     result['match_mode'] == '인접권역추천'),
            ("결과 강사 존재 (0명 아님)",         len(matches) > 0),
            ("전원 권역 점수 ≤ 20점 (인접 권역)", all(s <= 20.0 for s in region_scores)),
            ("서부권 소속 강사 없음",             all(m['instructor_region'] != '서부권' for m in matches)),
            ("전문분야 점수 40점 (완전 일치)",    all(m['score_detail']['전문분야_점수'] == 40.0 for m in matches)),
        ]

        print_result_table(result, checks)
        db.drop_all()


# ── 테스트 4 : 전문분야 불일치 (C 기능 확인) ─────────────────────

def test_4():
    print("=" * 80)
    print("테스트 4 ─ 전문분야 불일치 (C 기능 확인)")
    print("  북부권 기관이 '챗GPT' 교육 요청 (강사 미보유 분야)")
    print("  → 인증 등급 제한으로 일부 제외 → 5명 미만 → 조건 완화 추천 확인")
    print("=" * 80)

    app = create_app('testing')
    with app.app_context():
        db.create_all()

        org = Organization(name='북부권테스트기관', type='주민센터', region='북부권', contact='000')
        db.session.add(org)
        db.session.flush()

        instructors = [
            # ── 정상 pool (전문가 cert, 챗GPT 매칭 가능) ────────────────
            # 전문분야 불일치 (영상편집·SNS = 다른 그룹) → specialty=0
            Instructor(name='전문가A', region='북부권',
                       travel_range=['북부권'],
                       specialties=['영상편집'], cert_level=3,
                       available_days=['월','화'], available_times=['오전'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=30, avg_rating=4.9,
                       last_active=date(2026,5,1), is_active=True),
            Instructor(name='전문가B', region='북부권',
                       travel_range=['북부권'],
                       specialties=['SNS활용'], cert_level=3,
                       available_days=['월','화'], available_times=['오전'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=20, avg_rating=4.7,
                       last_active=date(2026,5,1), is_active=True),

            # ── 조건 완화 대상 (기초 cert → 챗GPT 직접 불가) ────────────
            # AI기초 보유 + 기초 cert → 챗GPT 요청에 cert 필터에서 제외되나
            # _is_cert_eligible_for_similar: AI기초∈AI·디지털(챗GPT그룹) → True
            # → 조건완화 후보로 specialty=20(유사분야) 점수 부여
            Instructor(name='기초강사A', region='북부권',
                       travel_range=['북부권'],
                       specialties=['AI기초'], cert_level=1,
                       available_days=['월','화'], available_times=['오전'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=15, avg_rating=4.8,
                       last_active=date(2026,5,1), is_active=True),
            Instructor(name='기초강사B', region='중부권',
                       travel_range=['중부권','북부권'],
                       specialties=['AI기초'], cert_level=1,
                       available_days=['월','화'], available_times=['오전'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=10, avg_rating=4.5,
                       last_active=date(2026,5,1), is_active=True),
            Instructor(name='기초강사C', region='서부권',
                       travel_range=['서부권','북부권'],
                       specialties=['AI기초'], cert_level=1,
                       available_days=['월','화'], available_times=['오전'],
                       max_classes_month=4, target_audience=['성인'],
                       total_classes=8, avg_rating=4.5,
                       last_active=date(2026,5,1), is_active=True),
        ]
        db.session.add_all(instructors)
        db.session.flush()

        req = EducationRequest(
            org_id=org.id, specialty_needed='챗GPT',
            target_audience='성인', expected_students=10,
            preferred_dates=['2026-06-01'], preferred_times=['오전'],
            frequency='주 1회', location_type='대면', status='대기',
        )
        db.session.add(req)
        db.session.commit()

        result = find_top_matches(req.id)
        matches = result['matches']
        types   = [m['match_type'] for m in matches]

        checks = [
            ("match_mode = '조건완화추천'",            result['match_mode'] == '조건완화추천'),
            ("총 5명 추천",                             len(matches) == 5),
            ("조건완화추천 match_type 강사 포함",       '조건완화추천' in types),
            ("정상 match_type 강사도 포함",             '정상' in types),
            ("기초강사A(AI기초 유사분야) 포함됨",
             any(m['instructor_name'] == '기초강사A' for m in matches)),
            ("기초강사들 전문분야 점수 20점(유사분야)",
             all(
                 m['score_detail']['전문분야_점수'] == 20.0
                 for m in matches if m['match_type'] == '조건완화추천'
             )),
        ]

        print_result_table(result, checks)
        db.drop_all()


# ── 실행 ────────────────────────────────────────────────────────

if __name__ == '__main__':
    test_1()
    test_2()
    test_3()
    test_4()

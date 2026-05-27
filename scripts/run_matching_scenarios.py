"""
풍부해진 데이터(강사 130명, 기관 60개, 요청 135건)로
현실적인 매칭 시나리오 10건을 실행해 알고리즘 동작을 검증한다.

각 시나리오:
  1. 임시 EducationRequest 생성 (org_id 는 기존 기관 사용)
  2. find_top_matches() 실행 → DB 에 매칭/ML 로그/요청.status='완료' 기록
  3. 결과(top N, breakdown, failure_reasons, match_mode) 출력
  4. 시나리오 종료 후 임시 요청과 관련 매칭/세션/ML 로그 일괄 정리

비활동/신규/부하/상성 등 횡단 검증(시나리오 6,7,9,10)은
앞 시나리오에서 만들어진 매칭 결과를 집계하여 검증.

실행:
  source .venv/bin/activate && python scripts/run_matching_scenarios.py
"""
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text, func

from app import create_app
from app.extensions import db
from app.models.organization import Organization
from app.models.instructor import Instructor
from app.models.education_request import EducationRequest
from app.models.match import Match
from app.models.class_session import ClassSession
from app.models.ml_training_log import MLTrainingLog
from app.services.matching_service import (
    find_top_matches,
    calculate_match_score,
    _build_scoring_context,
    _is_cert_eligible,
)
from app.services.class_session_service import (
    create_sessions_for_match,
    build_session_schedule,
)


TODAY = date(2026, 5, 26)


# ──────────────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    print()
    print('━' * 72)
    print(f'  {title}')
    print('━' * 72)


def make_request(**kw) -> EducationRequest:
    """임시 매칭 시나리오용 요청 생성."""
    kw.setdefault('status', '대기')
    kw.setdefault('location_type', '대면')
    kw.setdefault('created_at', datetime.utcnow())
    req = EducationRequest(**kw)
    db.session.add(req)
    db.session.commit()
    return req


def cleanup_request(rid: int) -> None:
    """시나리오 종료 후 임시 요청과 관련 데이터를 모두 삭제."""
    db.session.execute(
        text('DELETE FROM class_sessions WHERE match_id IN '
             '(SELECT id FROM matches WHERE request_id=:rid)'),
        {'rid': rid},
    )
    db.session.execute(text('DELETE FROM ml_training_logs WHERE request_id=:rid'),
                       {'rid': rid})
    db.session.execute(text('DELETE FROM matches WHERE request_id=:rid'),
                       {'rid': rid})
    db.session.execute(text('DELETE FROM education_requests WHERE id=:rid'),
                       {'rid': rid})
    db.session.commit()


def print_top_matches(result: dict, top_n: int = 5) -> None:
    """find_top_matches 결과를 표 형태로 출력. matches 원소는 Match.to_dict() 기반."""
    print(f"  매칭 모드: {result['match_mode']}")
    print(f"  사유: {result['match_mode_reason']}")
    print(f"  매칭 수: {result['total_count']}")
    if result.get('auto_excluded'):
        print(f"  자동 제외: {len(result['auto_excluded'])}명 "
              f"(첫 3건: {[x['instructor_name'] for x in result['auto_excluded'][:3]]})")
    if result.get('failure_reasons'):
        print(f"  실패 사유: {result['failure_reasons']}")

    print()
    print(f"  {'순위':<4}{'강사':<28}{'권역':<8}{'cert':<6}{'총점':<8}{'유형':<12}{'누적':<6}{'평점':<6}")
    print(f"  {'-'*84}")
    for idx, m in enumerate(result.get('matches', [])[:top_n], 1):
        inst = db.session.get(Instructor, m['instructor_id'])
        score = m['match_score']
        mtype = m.get('match_type', '정상')
        flag = '★ 신규' if (inst.total_classes or 0) < 10 else ''
        print(f"  {idx:<4}{inst.name:<28}{inst.region:<8}"
              f"{str(inst.cert_level):<6}{score:<8.1f}{mtype:<12}"
              f"{(inst.total_classes or 0):<6}{(inst.avg_rating or 0):<6.2f}{flag}")

    # 시나리오 이슈 #3: 6번째 슬롯 (신규 강사) 출력
    slot = result.get('newcomer_slot')
    if slot:
        print(f"\n  ▸ 6번째 슬롯 (신규 강사): {slot['instructor_name']} "
              f"({slot['instructor_region']}, cert={slot['instructor_cert_level']}, "
              f"누적={slot['instructor_total_classes']}, 평점={slot['instructor_avg_rating']:.2f})")
        print(f"    총점: {slot['match_score']:.1f} / 기본: {slot['base_score']:.1f}")
        print(f"    노출 이유: {slot['exposure_reason']}")
    else:
        print("\n  ▸ 6번째 슬롯: 노출 가능한 신규 강사 후보 없음")


def print_breakdown_from_match_dict(m: dict, label: str = '') -> None:
    """Match.to_dict() + breakdown 응답에서 1명 강사의 점수 breakdown 출력."""
    inst = db.session.get(Instructor, m['instructor_id'])
    bd = m['breakdown']
    print(f"\n  [{label}] {inst.name} (id={inst.id}, region={inst.region}, "
          f"cert={inst.cert_level}, 누적={inst.total_classes}, 평점={inst.avg_rating})")
    base = bd['base']
    print(f"    기본:  권역 {base['권역_점수']} + 전문 {base['전문분야_점수']} + "
          f"시간 {base['시간대_점수']} = {base['기본_합계']}")
    for b in bd['bonuses']:
        print(f"    + {b['항목']}: {b['점수']} ({b['사유']})")
    for p in bd['penalties']:
        print(f"    - {p['항목']}: {p['점수']} ({p['사유']})")
    print(f"    최종: {bd['최종_총점']}")


def print_breakdown(item: dict, label: str = '') -> None:
    """calculate_match_score 결과(instructor 객체 포함)용 breakdown 출력."""
    bd = item['breakdown']
    inst = item['instructor']
    print(f"\n  [{label}] {inst.name} (id={inst.id}, region={inst.region}, "
          f"cert={inst.cert_level}, 누적={inst.total_classes}, 평점={inst.avg_rating})")
    base = bd['base']
    print(f"    기본:  권역 {base['권역_점수']} + 전문 {base['전문분야_점수']} + "
          f"시간 {base['시간대_점수']} = {base['기본_합계']}")
    for b in bd['bonuses']:
        print(f"    + {b['항목']}: {b['점수']} ({b['사유']})")
    for p in bd['penalties']:
        print(f"    - {p['항목']}: {p['점수']} ({p['사유']})")
    print(f"    최종: {bd['최종_총점']}")


def collect_match_summary(scenario_results: list[dict]) -> dict:
    """앞 시나리오들의 매칭 결과에서 신규/비활동 강사 통계 집계.
    matches 원소는 Match.to_dict() 형식 — 강사 객체는 instructor_id 로 조회."""
    contains_new = 0
    contains_inactive = 0
    # 시나리오 이슈 #3: 6번째 슬롯 통계 추가
    with_newcomer_slot = 0
    total = 0
    for sr in scenario_results:
        if not sr.get('matches'):
            continue
        # 시나리오 9,10 처럼 instructor 객체를 직접 담은 결과는 스킵 (find_top_matches 결과만)
        first = sr['matches'][0]
        if 'instructor_id' not in first:
            continue
        total += 1
        insts = [
            db.session.get(Instructor, m['instructor_id'])
            for m in sr['matches'][:5]
        ]
        if any((i.total_classes or 0) < 10 for i in insts if i):
            contains_new += 1
        if any(i and not i.is_active for i in insts):
            contains_inactive += 1
        # 시나리오 이슈 #3: newcomer_slot 채워졌는지
        if sr.get('newcomer_slot'):
            with_newcomer_slot += 1
    return {
        'total_scenarios': total,
        'with_new': contains_new,
        'with_inactive': contains_inactive,
        'with_newcomer_slot': with_newcomer_slot,
    }


# ──────────────────────────────────────────────────────────────────────
# 시나리오 1 — 동탄초등학교 + 초등학생 AI기초 + 평일 오후 1회성
# ──────────────────────────────────────────────────────────────────────

def scenario_1() -> dict:
    banner('시나리오 1 — 동탄초등학교 / 초등학생 AI기초 / 평일 오후 / 1회성')
    org = Organization.query.filter_by(name='동탄1초등학교').first()
    print(f'  기관: {org.name} (id={org.id}, 권역={org.region})')

    req = make_request(
        org_id=org.id, specialty_needed='AI 기초',
        target_audience='초등학생', expected_students=22,
        preferred_dates=['2026-06-09'], preferred_times=['오후'],
        frequency='1회성',
    )
    print(f'  요청: id={req.id}, 분야={req.specialty_needed}, '
          f'대상={req.target_audience}, {req.frequency}')

    result = find_top_matches(req.id, top_n=5)
    print_top_matches(result)
    if result['matches']:
        # 1위 breakdown
        print_breakdown_from_match_dict(result['matches'][0], label='1위')
        if len(result['matches']) >= 2:
            print_breakdown_from_match_dict(result['matches'][1], label='2위')

    return result


# ──────────────────────────────────────────────────────────────────────
# 시나리오 2 — 향남고등학교 / 코딩교육 / 주2회×2개월 정기
# ──────────────────────────────────────────────────────────────────────

def scenario_2() -> dict:
    banner('시나리오 2 — 향남고등학교 / 코딩교육 / 주 2회 × 2개월 정기')
    # 향남고등학교는 기존 데이터에 25번이 있음
    org = Organization.query.filter_by(id=25).first() \
        or Organization.query.filter_by(name='향남고등학교').first()
    print(f'  기관: {org.name} (id={org.id}, 권역={org.region})')

    req = make_request(
        org_id=org.id, specialty_needed='코딩교육',
        target_audience='고등학생', expected_students=20,
        preferred_dates=['2026-06-08'], preferred_times=['오후', '저녁'],
        frequency='주 2회 × 2개월',
    )
    print(f'  요청: id={req.id}, 분야={req.specialty_needed}, {req.frequency}')

    # 예상 세션 개수 확인: 주 2회 × 8주 = 16개
    schedule = build_session_schedule(req)
    print(f'  예상 세션 schedule 길이: {len(schedule)}')

    result = find_top_matches(req.id, top_n=5)
    print_top_matches(result)
    if not result['matches']:
        return result

    # top1 을 '수락' 으로 승격 → 세션 자동 생성 검증
    top1 = Match.query.filter_by(request_id=req.id) \
        .order_by(Match.match_score.desc()).first()
    top1.status = '수락'
    db.session.flush()
    sessions = create_sessions_for_match(top1)
    sessions_now = ClassSession.query.filter_by(match_id=top1.id).all()
    print(f'\n  → top1({top1.instructor.name}) 매칭을 \'수락\' 으로 승격 후 '
          f'생성된 세션: {len(sessions_now)}개')

    # 시간대 충돌 검사: 같은 강사가 동일 날짜/시간에 다른 매칭 있는지
    # (강사의 누적 세션 중 같은 날짜+시간 중복 개수)
    s_first = sessions_now[0] if sessions_now else None
    if s_first:
        same_slot = ClassSession.query.filter(
            ClassSession.instructor_id == top1.instructor_id,
            ClassSession.session_date == s_first.session_date,
            ClassSession.session_time == s_first.session_time,
        ).count()
        print(f'  → 동일 강사·동일 슬롯({s_first.session_date}/{s_first.session_time}) '
              f'세션 개수: {same_slot} '
              f'{"(이 시나리오에서 추가된 것 포함)" if same_slot >= 1 else ""}')

    print_breakdown_from_match_dict(result['matches'][0], label='1위')
    return result


# ──────────────────────────────────────────────────────────────────────
# 시나리오 3 — 공무원 대상 챗GPT 활용 / 3일 이내 VIP 긴급
# ──────────────────────────────────────────────────────────────────────

def scenario_3() -> dict:
    banner('시나리오 3 — VIP 긴급 / 봉담행정복지센터 / 공무원 챗GPT / 3일 이내')
    org = Organization.query.filter_by(name='봉담행정복지센터').first()
    print(f'  기관: {org.name} (id={org.id}, 권역={org.region})')

    urgent_date = (TODAY + timedelta(days=2)).isoformat()
    req = make_request(
        org_id=org.id, specialty_needed='챗GPT 활용',
        target_audience='공무원', expected_students=30,
        preferred_dates=[urgent_date], preferred_times=['오후'],
        frequency='1회성',
    )
    print(f'  요청: id={req.id}, 날짜={urgent_date} (오늘+2일)')

    result = find_top_matches(req.id, top_n=5)
    print_top_matches(result)

    # cert_level 분포 확인
    if result['matches']:
        levels = [m['instructor_cert_level'] for m in result['matches'][:5]]
        avg_cert = sum(l or 0 for l in levels) / max(len(levels), 1)
        print(f'\n  ▸ 상위 5명 cert_level 평균: {avg_cert:.2f} '
              f'(높을수록 고급 강사 선호)')
        print(f'  ▸ cert_level 분포: {levels}')
        print_breakdown_from_match_dict(result['matches'][0], label='1위')

    return result


# ──────────────────────────────────────────────────────────────────────
# 시나리오 4 — 화성시청 대강당 / 시민 200명 / AI 리터러시
# ──────────────────────────────────────────────────────────────────────

def scenario_4() -> dict:
    banner('시나리오 4 — 화성시청 대강당 / 시민 200명 / AI 리터러시')
    org = Organization.query.filter_by(name='화성시청 대강당 (수용 300명)').first()
    print(f'  기관: {org.name} (id={org.id}, 권역={org.region})')

    req = make_request(
        org_id=org.id, specialty_needed='디지털 리터러시',
        target_audience='성인', expected_students=200,
        preferred_dates=['2026-06-18'], preferred_times=['오후'],
        frequency='1회성',
    )
    print(f'  요청: id={req.id}, 예상 인원={req.expected_students}명')

    result = find_top_matches(req.id, top_n=5)
    print_top_matches(result)

    # 상위 5명의 베테랑/평점 확인
    if result['matches']:
        top5 = result['matches'][:5]
        ratings = [m['instructor_avg_rating'] or 0 for m in top5]
        totals = [m['instructor_total_classes'] or 0 for m in top5]
        veteran_count = sum(1 for t in totals if t >= 80)
        high_rating = sum(1 for r in ratings if r >= 4.8)
        print(f'\n  ▸ 상위 5명 평균 평점: {sum(ratings)/5:.2f}')
        print(f'  ▸ 상위 5명 평균 누적강의: {sum(totals)/5:.1f}')
        print(f'  ▸ 베테랑(누적≥80): {veteran_count}명 / 평점≥4.8: {high_rating}명')
        print_breakdown_from_match_dict(result['matches'][0], label='1위')

    return result


# ──────────────────────────────────────────────────────────────────────
# 시나리오 5 — 스크래치 / 주말 저녁 (희귀+특수)
# ──────────────────────────────────────────────────────────────────────

def scenario_5() -> dict:
    banner('시나리오 5 — 희귀 분야 + 어려운 시간대 / 스크래치 / 주말 저녁')
    # 동탄1초등학교 + 토요일 저녁
    org = Organization.query.filter_by(name='동탄1초등학교').first()
    print(f'  기관: {org.name} (id={org.id}, 권역={org.region})')

    sat = '2026-06-13'  # 토요일
    req = make_request(
        org_id=org.id, specialty_needed='스크래치',
        target_audience='초등학생', expected_students=15,
        preferred_dates=[sat], preferred_times=['저녁'],
        frequency='1회성',
    )
    print(f'  요청: id={req.id}, 날짜={sat} (토), 시간=저녁')

    result = find_top_matches(req.id, top_n=5)
    print_top_matches(result)

    # failure_reasons 확인
    req2 = db.session.get(EducationRequest, req.id)
    print(f'\n  ▸ DB 에 저장된 failure_reasons: {req2.failure_reasons}')
    if result.get('match_mode') != '정상':
        print(f'  ▸ 조건 완화/최선추천 발동: {result["match_mode"]}')

    return result


# ──────────────────────────────────────────────────────────────────────
# 시나리오 8 — 중부권 기관 / 인접 권역 자동 탐색
# ──────────────────────────────────────────────────────────────────────

def scenario_8() -> dict:
    banner('시나리오 8 — 중부권 기관 / 권역 부족 → 인접 권역 탐색')
    org = Organization.query.filter_by(
        name='화성시청 부설 청소년교육원',
    ).first()
    print(f'  기관: {org.name} (id={org.id}, 권역={org.region})')

    # 중부권 활성 강사 수 미리 출력
    center_count = Instructor.query.filter_by(
        region='중부권', is_active=True,
    ).count()
    print(f'  중부권 활동 강사 수: {center_count}명')

    req = make_request(
        org_id=org.id, specialty_needed='파이썬 기초',
        target_audience='중학생', expected_students=20,
        preferred_dates=['2026-06-16'], preferred_times=['오후'],
        frequency='1회성',
    )

    result = find_top_matches(req.id, top_n=5)
    print_top_matches(result)

    # 상위 5명의 권역 분포
    if result['matches']:
        regions = [m['instructor_region'] for m in result['matches'][:5]]
        from collections import Counter
        dist = Counter(regions)
        print(f'\n  ▸ 상위 5명 권역 분포: {dict(dist)}')
        print(f'  ▸ 중부권 강사 포함: {dist.get("중부권", 0)}명, '
              f'인접 권역(동/서/남/북): {sum(v for k,v in dist.items() if k != "중부권")}명')

    return result


# ──────────────────────────────────────────────────────────────────────
# 시나리오 9 — 강사 부하 분산 (월 강의 많은 강사 패널티)
# ──────────────────────────────────────────────────────────────────────

def scenario_9_load_balance() -> dict:
    banner('시나리오 9 — 부하 분산 / 이번 달 매칭 많이 받은 강사 패널티')

    # 활성 강사로 컨텍스트 빌드 → month_match_counts
    actives = Instructor.query.filter_by(is_active=True).all()
    ctx = _build_scoring_context(actives)
    counts = ctx['month_match_counts']
    most_matched = sorted(counts.items(), key=lambda x: -x[1])[:10]
    print(f'  ▸ 이번 달({TODAY.year}-{TODAY.month}) 매칭 많은 강사 TOP 10')
    for inst_id, c in most_matched:
        inst = db.session.get(Instructor, inst_id)
        if c == 0:
            continue
        print(f'    - {inst.name} (id={inst.id}, region={inst.region}) '
              f'→ {c}/{inst.max_classes_month}회')

    # 첫 번째 다중 매칭 강사를 골라 임의 요청에 대해 점수 확인
    target = next((db.session.get(Instructor, i) for i, c in most_matched if c > 0), None)
    if not target:
        print('  (이번 달 매칭이 있는 강사가 없음 — 부하 패널티 시뮬레이션 생략)')
        return {'matches': []}

    print(f'\n  ▸ 부하 패널티 시뮬레이션 강사: {target.name} '
          f'(이번 달 {counts.get(target.id, 0)}/{target.max_classes_month}회)')
    # 임시 요청 생성 후 calculate_match_score 로 점수 breakdown 확인
    org = Organization.query.filter_by(region=target.region).first()
    req = make_request(
        org_id=org.id, specialty_needed='AI 기초',
        target_audience='성인', expected_students=20,
        preferred_dates=['2026-06-20'], preferred_times=['오후'],
        frequency='1회성',
    )
    item = calculate_match_score(target, req, ctx)
    print_breakdown(item, label=f'{target.name} (부하 비교 단건)')

    # 비교: 같은 권역의 부하 0인 강사 한 명
    light = next(
        (i for i in actives
         if i.region == target.region and counts.get(i.id, 0) == 0
         and _is_cert_eligible(i, 'AI 기초')),
        None,
    )
    if light:
        item2 = calculate_match_score(light, req, ctx)
        print_breakdown(item2, label=f'{light.name} (부하 0 비교)')
        diff = item['total_score'] - item2['total_score']
        print(f'\n  ▸ 점수 차이: {item["total_score"]:.1f} - '
              f'{item2["total_score"]:.1f} = {diff:.1f}')

    # 시나리오 이슈 #2: 권역 0점 가드 검증
    # target 과 권역이 다르고 인접도/이동도 안 되는 강사를 찾아 -20 가드가 적용되는지 확인
    from app.services.region_service import are_adjacent
    far_inst = next(
        (i for i in actives
         if i.region != target.region
         and not are_adjacent(i.region, target.region)
         and target.region not in (i.travel_range or [])),
        None,
    )
    if far_inst:
        far_item = calculate_match_score(far_inst, req, ctx)
        print_breakdown(far_item, label=f'{far_inst.name} (권역 부적합 가드 검증)')
        guard = next(
            (p for p in far_item['breakdown']['penalties']
             if p['항목'] == '권역 부적합 가드'),
            None,
        )
        print(f'  ▸ 권역 부적합 가드 적용 여부: '
              f'{"✅ 적용됨 (" + str(guard["점수"]) + "점)" if guard else "❌ 미적용"}')

    return {'matches': [item], '_request_id': req.id}


# ──────────────────────────────────────────────────────────────────────
# 시나리오 10 — 상성 시스템 / 학교 만족도 높은 강사 → 다른 학교 요청
# ──────────────────────────────────────────────────────────────────────

def scenario_10_chemistry() -> dict:
    banner('시나리오 10 — 상성 시스템 / 학교 만족 강사 → 다른 학교 요청')

    # 학교 + satisfaction>=4.5 인 매칭의 강사 찾기
    rows = db.session.query(
        Match.instructor_id, Match.satisfaction_score,
        Organization.id, Organization.name, Organization.type,
    ).join(EducationRequest, EducationRequest.id == Match.request_id) \
     .join(Organization, Organization.id == EducationRequest.org_id) \
     .filter(Organization.type == '학교') \
     .filter(Match.satisfaction_score >= 4.5) \
     .limit(5).all()

    if not rows:
        print('  (학교+만족도≥4.5 매칭이 없음 — 상성 시뮬레이션 생략)')
        return {'matches': []}

    print('  ▸ 학교에서 만족도 ≥ 4.5 강사:')
    for inst_id, sat, org_id, org_name, org_type in rows:
        inst = db.session.get(Instructor, inst_id)
        print(f'    - {inst.name} (id={inst.id}, 평점={inst.avg_rating}, '
              f'선호유형={inst.preferred_org_types}) ← {org_name}({org_type}, {sat:.1f})')

    target_inst = db.session.get(Instructor, rows[0][0])
    seed_org_id = rows[0][2]

    # 다른 학교 요청 생성 (같은 학교는 제외)
    other_school = Organization.query.filter(
        Organization.type == '학교',
        Organization.id != seed_org_id,
    ).first()
    print(f'\n  ▸ 다른 학교 요청 시도: {other_school.name} ({other_school.region})')
    req = make_request(
        org_id=other_school.id, specialty_needed='AI 기초',
        target_audience='중학생', expected_students=22,
        preferred_dates=['2026-06-19'], preferred_times=['오후'],
        frequency='1회성',
    )

    actives = Instructor.query.filter_by(is_active=True).all()
    ctx = _build_scoring_context(actives)
    item = calculate_match_score(target_inst, req, ctx)

    print_breakdown(item, label='상성 검증 강사')
    # 상성/선호/과거매칭 보너스 항목 추출
    bonuses = item['breakdown']['bonuses']
    chem_items = [b for b in bonuses
                  if '상성' in b['항목'] or '선호' in b['항목']
                  or '과거' in b['항목'] or '기관 유형' in b['항목']]
    print(f'\n  ▸ 관련 보너스 항목 합계: '
          f'+{sum(b["점수"] for b in chem_items):.1f}점')
    for b in chem_items:
        print(f'    - {b["항목"]}: +{b["점수"]} ({b["사유"]})')

    return {'matches': [item], '_request_id': req.id}


# ──────────────────────────────────────────────────────────────────────
# 시나리오 6 — 신규 강사 노출 (다중 시나리오 결과 검증)
# 시나리오 7 — 비활동 강사 제외 (다중 시나리오 결과 검증)
# ──────────────────────────────────────────────────────────────────────

def scenario_6_and_7(scenario_results: list[dict]) -> dict:
    banner('시나리오 6/7 — 신규 강사 노출 보장 + 비활동 강사 제외')
    summary = collect_match_summary(scenario_results)
    print(f'  ▸ 검증한 시나리오 수: {summary["total_scenarios"]}')
    print(f'  ▸ Top 5 에 신규 강사 1명 이상 포함: '
          f'{summary["with_new"]}/{summary["total_scenarios"]}')
    # 시나리오 이슈 #3: 6번째 슬롯 (newcomer_slot) 노출률
    print(f'  ▸ 6번째 슬롯에 신규 강사 노출: '
          f'{summary["with_newcomer_slot"]}/{summary["total_scenarios"]} '
          f'(신규 강사 항상 노출 — 클수록 정상)')
    print(f'  ▸ Top 5 에 비활동 강사 포함: '
          f'{summary["with_inactive"]}/{summary["total_scenarios"]} '
          f'(0 이어야 정상)')

    # 직접 DB 에서도 확인: 매칭에 비활동 강사가 등장한 적이 있는지
    inactive_match_count = db.session.execute(text("""
        SELECT COUNT(*) FROM matches m
        JOIN instructors i ON i.id = m.instructor_id
        WHERE i.is_active = false
    """)).scalar()
    print(f'  ▸ matches 테이블 전체 — 비활동 강사가 등장한 row 수: '
          f'{inactive_match_count} (0 이어야 정상)')
    return summary


# ──────────────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────────────

def main() -> None:
    app = create_app()
    with app.app_context():
        print('=' * 72)
        print('  화성시 AI 시민리더 허브 — 매칭 시나리오 검증 (10건)')
        print(f'  현재 데이터: 강사 {Instructor.query.count()}명 / '
              f'기관 {Organization.query.count()}개 / '
              f'요청 {EducationRequest.query.count()}건')
        print('=' * 72)

        cleanup_ids: list[int] = []
        scenario_results: list[dict] = []

        # 1~5: 실제 매칭 시나리오
        for fn in (scenario_1, scenario_2, scenario_3, scenario_4, scenario_5):
            try:
                res = fn()
            except Exception as e:
                print(f'  ✗ 시나리오 실패: {e}')
                db.session.rollback()
                continue
            scenario_results.append(res)
            # 추적: result 안에 _request_id 가 있거나, 마지막 생성 요청 id 추적
            last_req = (
                EducationRequest.query.order_by(EducationRequest.id.desc()).first()
            )
            if last_req:
                cleanup_ids.append(last_req.id)

        # 8: 권역 부족
        res8 = scenario_8()
        scenario_results.append(res8)
        last_req = EducationRequest.query.order_by(EducationRequest.id.desc()).first()
        if last_req:
            cleanup_ids.append(last_req.id)

        # 9: 부하 분산
        res9 = scenario_9_load_balance()
        if res9.get('_request_id'):
            cleanup_ids.append(res9['_request_id'])

        # 10: 상성
        res10 = scenario_10_chemistry()
        if res10.get('_request_id'):
            cleanup_ids.append(res10['_request_id'])

        # 6/7: 횡단 검증
        scenario_6_and_7(scenario_results)

        # 정리
        banner('cleanup — 시나리오용 임시 요청 정리')
        unique_ids = list(set(cleanup_ids))
        for rid in unique_ids:
            cleanup_request(rid)
        print(f'  ▸ 정리된 요청 id: {sorted(unique_ids)}')

        # 최종 카운트
        print()
        print('=' * 72)
        print('  종료 후 DB 상태:')
        print(f'    instructors  = {Instructor.query.count()}')
        print(f'    organizations= {Organization.query.count()}')
        print(f'    requests     = {EducationRequest.query.count()}')
        print(f'    matches      = {Match.query.count()}')
        print(f'    sessions     = {ClassSession.query.count()}')
        print(f'    ml_logs      = {MLTrainingLog.query.count()}')
        print('=' * 72)


if __name__ == '__main__':
    main()

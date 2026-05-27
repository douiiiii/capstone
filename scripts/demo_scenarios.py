"""
화성시 AI 시민리더 허브 — 발표용 시연 스크립트
=================================================

발표 당일 그대로 실행해서 6가지 핵심 기능을 차례대로 시연한다.

실행 방법:
    python scripts/demo_scenarios.py

    # 특정 시나리오만 실행
    python scripts/demo_scenarios.py --only 1
    python scripts/demo_scenarios.py --only 1,3,5

시연 흐름 (각 5~10초):
  ① 일반 매칭         — 점수 breakdown 으로 알고리즘 투명성 강조
  ② 정기 강의         — 매칭 1건 → 세션 16개 자동 생성
  ③ 부하 분산         — 월 매칭 80% 도달 강사 -15점 패널티
  ④ 상성 시스템       — 기관 유형 평균 평점 4.5+ 강사 +15점 보너스
  ⑤ 신규 강사 슬롯    — Top 5 외 6번째 슬롯에 신규 강사 항상 노출
  ⑥ 자동 조건 완화    — 정확 일치 강사 없을 때 유사 분야로 자동 확장

각 시나리오마다 시작/종료 마크와 핵심 포인트를 한글로 출력.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from typing import Iterable

# 프로젝트 루트를 import path 에 추가 (scripts/ 디렉토리에서 실행해도 동작)
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models.education_request import EducationRequest  # noqa: E402
from app.models.instructor import Instructor  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.services.class_session_service import build_session_schedule  # noqa: E402
from app.services.matching_service import find_top_matches  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# 출력 유틸
# ─────────────────────────────────────────────────────────────────────

BAR = '═' * 72
SUB_BAR = '─' * 72


def banner(title: str, num: int | None = None) -> None:
    """시나리오 시작 배너"""
    print()
    print(BAR)
    label = f'[시나리오 {num}] {title}' if num else title
    print(f'  {label}')
    print(BAR)


def end_banner(num: int | None = None) -> None:
    """시나리오 종료 배너"""
    label = f'시나리오 {num} 종료' if num else '종료'
    print(SUB_BAR)
    print(f'  ✓ {label}')
    print(SUB_BAR)


def section(title: str) -> None:
    print()
    print(f'━━ {title} ━━')


def kv(label: str, value, indent: int = 2) -> None:
    """들여쓰기 키-값 출력"""
    print(f'{" " * indent}{label:<28} {value}')


def highlight(msg: str) -> None:
    print(f'  ★ {msg}')


# ─────────────────────────────────────────────────────────────────────
# 시연 데이터 준비
# ─────────────────────────────────────────────────────────────────────

def _ensure_demo_data():
    """시연용 강사/기관/요청을 미리 만들어 둔다 (이미 있으면 그대로 사용)."""
    org = Organization.query.filter_by(name='데모-동탄복지관').first()
    if not org:
        org = Organization(
            name='데모-동탄복지관',
            type='복지관',
            region='동부권',
            contact='031-000-0000',
        )
        db.session.add(org)
        db.session.flush()

    # 강사가 1명도 없으면 시드가 아직 안 돈 것 — 기본 시드 트리거
    if Instructor.query.count() == 0:
        from app.services.seed_data import seed_if_empty
        seed_if_empty()

    return org


def _pick_or_create_request(
    org: Organization,
    specialty: str,
    frequency: str,
    preferred_times: list[str],
    days_ahead: int = 7,
) -> EducationRequest:
    """동일 조건의 요청이 이미 있으면 재사용, 없으면 새로 만든다."""
    start = date.today() + timedelta(days=days_ahead)
    req = EducationRequest(
        org_id=org.id,
        specialty_needed=specialty,
        target_audience='시니어',
        expected_students=20,
        preferred_dates=[start.isoformat()],
        preferred_times=preferred_times,
        frequency=frequency,
        location_type='대면',
        status='대기',
    )
    db.session.add(req)
    db.session.commit()
    return req


# ─────────────────────────────────────────────────────────────────────
# ① 일반 매칭 — 점수 breakdown
# ─────────────────────────────────────────────────────────────────────

def scenario_1_basic_match() -> None:
    banner('일반 매칭 — 점수 breakdown 으로 알고리즘 투명성 보여주기', 1)
    print('  📌 핵심: 권역 40 + 전문분야 40 + 시간 20 = 100점 만점')
    print('     + 평점/활동/만족도/상성 등 보너스·패널티 누적')

    org = _ensure_demo_data()
    req = _pick_or_create_request(
        org, specialty='AI기초', frequency='1회성',
        preferred_times=['오전'],
    )
    section(f'요청 #{req.id}: {org.name} / 분야={req.specialty_needed} / 시간={req.preferred_times}')

    result = find_top_matches(req.id, top_n=3)
    section(f'매칭 결과 ({result["match_mode"]}) — Top {len(result["matches"])}명')

    for rank, m in enumerate(result['matches'], start=1):
        kv(f'[{rank}위] {m["instructor_name"]}', f'{m["match_score"]:.1f}점 ({m["match_type"]})')
        bd = m['breakdown']
        kv(' 기본 점수', f"권역 {bd['base']['권역_점수']} + 분야 {bd['base']['전문분야_점수']} + 시간 {bd['base']['시간대_점수']} = {bd['base']['기본_합계']}", indent=4)
        if bd['bonuses']:
            for b in bd['bonuses'][:3]:
                kv(' +보너스', f"{b['항목']} {b['점수']:+.0f} ({b['사유']})", indent=4)
        if bd['penalties']:
            for p in bd['penalties'][:3]:
                kv(' -패널티', f"{p['항목']} {p['점수']:+.0f} ({p['사유']})", indent=4)
        print()

    highlight('알고리즘이 왜 이 점수를 줬는지 모두 설명 가능 (블랙박스 X)')
    end_banner(1)


# ─────────────────────────────────────────────────────────────────────
# ② 정기 강의 — 세션 자동 생성
# ─────────────────────────────────────────────────────────────────────

def scenario_2_regular_class() -> None:
    banner('정기 강의 — 매칭 1건이 강의 16개로 자동 풀이', 2)
    print('  📌 핵심: 매칭은 1건이지만, 시스템은 강의 N개를 미리 잡아둔다')
    print('     · "주 1회 × 4개월" → 16개 세션')
    print('     · 충돌 검사 / 부하 분산 / total_classes 계산의 단일 소스')

    org = _ensure_demo_data()
    req = _pick_or_create_request(
        org, specialty='AI기초',
        frequency='주 1회 × 4개월',
        preferred_times=['오전'],
    )
    section(f'요청 #{req.id}: frequency = "{req.frequency}"')

    schedule = build_session_schedule(req)
    kv('생성될 세션 수', f'{len(schedule)} 개')
    section('세션 일정 (앞 5개만 출력)')
    for i, (d, t) in enumerate(schedule[:5], start=1):
        kv(f' {i}회차', f'{d} {t}', indent=4)
    if len(schedule) > 5:
        kv(' ...', f'외 {len(schedule)-5}개', indent=4)

    highlight(f'1건 매칭 → {len(schedule)}개 세션 자동 생성 (정기 강의 완벽 지원)')
    end_banner(2)


# ─────────────────────────────────────────────────────────────────────
# ③ 부하 분산 — -15점 패널티
# ─────────────────────────────────────────────────────────────────────

def scenario_3_load_balance() -> None:
    banner('부하 분산 — 일이 몰린 강사에게 -15점 패널티', 3)
    print('  📌 핵심: 같은 강사한테 일이 몰리는 걸 자동으로 막는다')
    print('     · 이번 달 매칭이 max_classes_month 의 80% 이상 → -15점')
    print('     · 가장 많이 매칭된 강사에게 추가 -10점 (쏠림 패널티)')
    print('     · 월 한도 초과 시 후보에서 자동 제외')

    org = _ensure_demo_data()
    req = _pick_or_create_request(
        org, specialty='AI기초', frequency='1회성',
        preferred_times=['오전'],
    )

    result = find_top_matches(req.id, top_n=5)

    section('Top 5 매칭 — 부하 관련 항목만 추출')
    found_load = False
    for rank, m in enumerate(result['matches'], start=1):
        bd = m['breakdown']
        load_items = [
            p for p in bd['penalties']
            if '월 강의' in p['항목'] or '쏠림' in p['항목']
        ]
        if load_items:
            found_load = True
            kv(f'[{rank}위] {m["instructor_name"]}', f'{m["match_score"]:.1f}점')
            for p in load_items:
                kv(' -부하 패널티', f"{p['항목']} {p['점수']:+.0f} ({p['사유']})", indent=4)

    if not found_load:
        kv('(이번 매칭 데모에서는 부하 패널티 대상 강사 없음)', '— 다음 시나리오로 이동')

    section('자동 제외 강사')
    if result.get('auto_excluded'):
        for ex in result['auto_excluded'][:3]:
            kv(' 제외', f"{ex['instructor_name']} — {ex['사유']}", indent=4)
    else:
        kv(' (이번엔 제외 없음)', '— 강사 풀에 여유 있음', indent=4)

    highlight('한 명에게 일이 쏠리지 않게 시스템이 자동 분산')
    end_banner(3)


# ─────────────────────────────────────────────────────────────────────
# ④ 상성 시스템 — +15점 보너스
# ─────────────────────────────────────────────────────────────────────

def scenario_4_chemistry() -> None:
    banner('상성 시스템 — 기관 유형 평균 만족도 4.5+ 강사 +15점', 4)
    print('  📌 핵심: 강사-기관 간 "궁합" 을 점수에 반영')
    print('     · 해당 기관 유형에서 평균 만족도 4.5 이상 → +15점 상성')
    print('     · 강사 선호 기관 유형 일치 → +10점')
    print('     · 강사 비선호 기관 유형  → -5점')

    # 복지관 요청을 만들어 복지관 경험 많은 강사가 부각되도록
    org = Organization.query.filter_by(type='복지관').first()
    if not org:
        org = _ensure_demo_data()
    req = _pick_or_create_request(
        org, specialty='AI기초', frequency='1회성',
        preferred_times=['오전'],
    )

    section(f'요청 #{req.id}: 기관 유형 = {org.type}')
    result = find_top_matches(req.id, top_n=5)

    found_any = False
    for rank, m in enumerate(result['matches'], start=1):
        bd = m['breakdown']
        chem_items = [
            b for b in bd['bonuses']
            if '상성' in b['항목'] or '선호' in b['항목']
        ]
        if chem_items:
            found_any = True
            kv(f'[{rank}위] {m["instructor_name"]}', f'{m["match_score"]:.1f}점')
            for b in chem_items:
                kv(' +상성', f"{b['항목']} {b['점수']:+.0f} ({b['사유']})", indent=4)

    if not found_any:
        kv('이번 매칭에서는 상성 보너스 대상자 없음', '(만족도 이력 누적 필요)')

    highlight('단순 점수 합산이 아닌, 강사-기관 궁합까지 자동 반영')
    end_banner(4)


# ─────────────────────────────────────────────────────────────────────
# ⑤ 신규 강사 6번째 슬롯
# ─────────────────────────────────────────────────────────────────────

def scenario_5_newcomer_slot() -> None:
    banner('신규 강사 6번째 슬롯 — Top 5 외 추가 노출 보장', 5)
    print('  📌 핵심: 평점 없고 이력 없는 신규 강사도 매칭 기회 확보')
    print('     · Top 5 와는 별개로 6번째 슬롯에 신규 강사 1명 항상 노출')
    print('     · 누적 강의 < 10회 + 권역/분야/시간 중 1개 이상 일치')

    org = _ensure_demo_data()
    req = _pick_or_create_request(
        org, specialty='AI기초', frequency='1회성',
        preferred_times=['오전'],
    )

    result = find_top_matches(req.id, top_n=5)
    section('Top 5 매칭')
    for rank, m in enumerate(result['matches'], start=1):
        kv(
            f'[{rank}위] {m["instructor_name"]}',
            f'{m["match_score"]:.1f}점 / 누적 {m["instructor_total_classes"] or 0}회 / {m["match_type"]}',
        )

    section('🆕 6번째 슬롯 (신규 강사 보장)')
    slot = result.get('newcomer_slot')
    if slot:
        kv('강사', f'{slot["instructor_name"]} (id={slot["instructor_id"]})')
        kv('누적 강의', f'{slot["instructor_total_classes"] or 0}회')
        kv('기본 점수', f'{slot["base_score"]:.0f}점 (권역 {slot["region_score"]:.0f} + 분야 {slot["specialty_score"]:.0f} + 시간 {slot["time_score"]:.0f})')
        kv('노출 사유', slot['exposure_reason'])
        highlight('Top 5 에 못 들어도 신규 강사를 강제로 1명 더 노출 → 공정성 확보')
    else:
        kv('이번 매칭에서는 신규 강사 후보 없음', '(매칭 가능한 신규 강사 부재)')

    end_banner(5)


# ─────────────────────────────────────────────────────────────────────
# ⑥ 자동 조건 완화 — 유사분야 확장
# ─────────────────────────────────────────────────────────────────────

def scenario_6_relaxation() -> None:
    banner('자동 조건 완화 — 정확 매칭 부족 시 유사 분야 확장', 6)
    print('  📌 핵심: "매칭 강사가 없어요" 는 절대 띄우지 않는다')
    print('     · 인접 권역 자동 탐색 → 유사 분야 확장 → 조건완화추천 → 최선추천')
    print('     · 매칭 모드(match_mode) 로 어떤 단계까지 갔는지 명시')

    # 일부러 까다로운 분야로 요청해 조건 완화가 일어나도록 유도
    org = _ensure_demo_data()
    req = _pick_or_create_request(
        org, specialty='RPA', frequency='1회성',  # 흔치 않은 분야
        preferred_times=['저녁'],                  # 흔치 않은 시간대
    )

    section(f'까다로운 요청 #{req.id}: 분야={req.specialty_needed}, 시간={req.preferred_times}')

    result = find_top_matches(req.id, top_n=5)

    kv('match_mode', result['match_mode'])
    kv('mode_reason', result['match_mode_reason'])
    kv('매칭 강사 수', f'{result["total_count"]}명')

    if result.get('failure_reasons'):
        section('실패 원인 분석 (failure_reasons)')
        for r in result['failure_reasons']:
            kv(' ⚠', f"{r['code']}: {r['message']}", indent=4)

    section('Top 매칭')
    for rank, m in enumerate(result['matches'][:3], start=1):
        kv(f'[{rank}위] {m["instructor_name"]}', f'{m["match_score"]:.1f}점 ({m["match_type"]})')

    highlight('한 단계씩 조건을 풀어가며 끝까지 매칭을 시도 → 매칭률 100%')
    end_banner(6)


# ─────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────

SCENARIOS = {
    1: ('일반 매칭 / 점수 breakdown', scenario_1_basic_match),
    2: ('정기 강의 / 세션 16개 자동 생성', scenario_2_regular_class),
    3: ('부하 분산 / -15점 패널티', scenario_3_load_balance),
    4: ('상성 시스템 / +15점 보너스', scenario_4_chemistry),
    5: ('신규 강사 6번째 슬롯', scenario_5_newcomer_slot),
    6: ('매칭 실패 시 자동 조건 완화', scenario_6_relaxation),
}


def parse_only(value: str | None) -> Iterable[int]:
    if not value:
        return SCENARIOS.keys()
    nums = []
    for s in value.split(','):
        s = s.strip()
        if s.isdigit():
            n = int(s)
            if n in SCENARIOS:
                nums.append(n)
    return nums or SCENARIOS.keys()


def main() -> int:
    parser = argparse.ArgumentParser(description='발표용 시연 스크립트')
    parser.add_argument(
        '--only', type=str, default=None,
        help='특정 시나리오만 실행 (예: --only 1,3,5)',
    )
    args = parser.parse_args()

    app = create_app('development')
    with app.app_context():
        print()
        print('╔' + '═' * 70 + '╗')
        print('║  화성시 AI 시민리더 허브 — 발표 시연                            ║')
        print(f'║  {datetime.now().strftime("%Y-%m-%d %H:%M")}                                              ║')
        print('╚' + '═' * 70 + '╝')

        targets = list(parse_only(args.only))
        print(f'\n  실행할 시나리오: {targets}\n')

        for num in targets:
            title, func = SCENARIOS[num]
            try:
                func()
            except Exception as e:  # noqa: BLE001
                print(f'\n  ❌ 시나리오 {num} 실행 중 오류: {e}\n')

        print()
        print('═' * 72)
        print('  모든 시연 종료.')
        print('═' * 72)
        print()

    return 0


if __name__ == '__main__':
    sys.exit(main())

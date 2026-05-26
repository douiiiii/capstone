"""
매칭 0건 요청 일괄 재매칭
─────────────────────────────────────────────────────────────────────

대상:
  · status ∈ ('대기', '매칭중')
  · matches 테이블에 연결된 row 0건

흐름:
  1) 대상 요청 목록 추출 + 사전 통계
  2) find_top_matches 일괄 실행 (요청별 트랜잭션)
  3) 사후 분석
     · 매칭 성공/실패 건수
     · 권역별 성공률
     · 잔여 0건 요청 실패 원인
"""
from __future__ import annotations

import sys
from collections import Counter

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models.education_request import EducationRequest
from app.models.match import Match
from app.services.matching_service import find_top_matches


PENDING_STATUSES = ('대기', '매칭중')


def _collect_targets() -> list[EducationRequest]:
    """매칭 0건이고 status ∈ PENDING_STATUSES 인 요청"""
    reqs = EducationRequest.query.filter(
        EducationRequest.status.in_(PENDING_STATUSES)
    ).all()
    targets = []
    for r in reqs:
        cnt = Match.query.filter_by(request_id=r.id).count()
        if cnt == 0:
            targets.append(r)
    return targets


def _print_section(title: str) -> None:
    print('\n' + title)
    print('─' * 70)


def main() -> int:
    app = create_app('default')
    with app.app_context():
        print('=' * 70)
        print('  매칭 0건 요청 일괄 재매칭')
        print('=' * 70)

        # ── 1) 사전 통계 ──────────────────────────────────────────────
        _print_section('[1/4] 사전 통계')
        total_reqs = EducationRequest.query.count()
        before_with = (
            db.session.query(EducationRequest.id)
            .join(Match, Match.request_id == EducationRequest.id)
            .distinct().count()
        )
        before_without = total_reqs - before_with
        targets = _collect_targets()
        print(f'  · 전체 요청          : {total_reqs}')
        print(f'  · 매칭 보유 요청      : {before_with}')
        print(f'  · 매칭 0건 요청       : {before_without}')
        print(f'  · 이번 재매칭 대상    : {len(targets)} (대기/매칭중 + 매칭 0건)')

        # ── 2) 일괄 재매칭 ────────────────────────────────────────────
        _print_section('[2/4] find_top_matches 일괄 실행')
        success, failed = 0, 0
        failure_codes = Counter()
        per_region = Counter()
        per_region_success = Counter()
        for i, req in enumerate(targets, 1):
            region = req.organization.region if req.organization else '미상'
            per_region[region] += 1
            try:
                result = find_top_matches(req.id)
                if result and result.get('total_count', 0) > 0:
                    success += 1
                    per_region_success[region] += 1
                else:
                    failed += 1
                    for r in (result or {}).get('failure_reasons') or []:
                        failure_codes[r.get('code', '?')] += 1
            except Exception as e:
                failed += 1
                failure_codes['exception'] += 1
                print(f'    ! id={req.id} 예외: {e!r}')
            # 진행률 표시
            if i % 10 == 0 or i == len(targets):
                print(f'    ... 진행 {i}/{len(targets)} '
                      f'(성공 {success}, 실패 {failed})')

        # ── 3) 사후 통계 ──────────────────────────────────────────────
        _print_section('[3/4] 사후 통계')
        after_with = (
            db.session.query(EducationRequest.id)
            .join(Match, Match.request_id == EducationRequest.id)
            .distinct().count()
        )
        after_without = total_reqs - after_with
        before_rate = before_with / total_reqs * 100
        after_rate = after_with / total_reqs * 100
        print(f'  · 매칭 보유 요청  : {before_with} → {after_with} '
              f'(+{after_with - before_with})')
        print(f'  · 매칭 성공률    : {before_rate:5.1f}% → {after_rate:5.1f}% '
              f'(+{after_rate - before_rate:.1f}pt)')
        print(f'  · 이번 실행 성공/실패 : {success} / {failed}')
        print(f'  · 매칭 잔여 0건  : {after_without}')

        # ── 4) 권역별 성공률 / 실패 사유 ─────────────────────────────
        _print_section('[4/4] 권역별 성공률 & 잔여 실패 원인')
        print('  · 권역별 재매칭 결과')
        print(f"      {'권역':10} {'대상':>5} {'성공':>5} {'실패':>5} {'성공률':>7}")
        for region, total in sorted(per_region.items(), key=lambda x: -x[1]):
            s = per_region_success.get(region, 0)
            rate = s / total * 100 if total else 0
            print(f'      {region:10} {total:>5} {s:>5} {total - s:>5} {rate:>6.1f}%')

        # 여전히 매칭 0건인 요청 + 실패 원인
        leftovers = []
        for r in EducationRequest.query.all():
            cnt = Match.query.filter_by(request_id=r.id).count()
            if cnt == 0:
                leftovers.append(r)
        print(f'\n  · 잔여 매칭 0건 요청: {len(leftovers)}건')
        if leftovers:
            print(f"      {'id':>4} {'권역':8} {'분야':14} {'사유':>40}")
            for r in leftovers[:30]:
                region = r.organization.region if r.organization else '?'
                fr = r.failure_reasons or []
                fr_text = ', '.join(x.get('code', '?') for x in fr) or '(원인 미저장)'
                print(f'      {r.id:>4} {region:8} {(r.specialty_needed or ""):14} {fr_text:>40}')

        # 실패 사유 코드별 집계
        if failure_codes:
            print('\n  · 실패 원인 코드 집계 (이번 재매칭에서 발생)')
            for code, cnt in failure_codes.most_common():
                print(f'      {code:20} {cnt:>4}건')

        print('\n' + '=' * 70)
        print('  ✅ 일괄 재매칭 완료')
        print('=' * 70)
        return 0


if __name__ == '__main__':
    sys.exit(main())

"""
강사 중복 데이터 정리 스크립트 (시나리오 이슈 #1)

발견된 패턴:
  - 강사 130명 중 50쌍(100명)이 완전 동일한 정보로 중복 등록됨
  - 각 쌍은 (lower_id, higher_id) 형태이며 lower_id 측이 일반적으로
    matches/ml_logs 활동 이력이 더 많음

정리 전략:
  1. 같은 이름 + 같은 권역 + 같은 specialties + 같은 cert_level 인 강사들을 그룹화
  2. 각 그룹에서 '활동 이력(matches 수)' 이 더 많은 쪽을 keep
  3. drop 측의 FK 참조(matches, class_sessions, ml_training_logs, grade_history)를
     keep 측으로 일괄 이전
  4. drop 측 instructor row 삭제
  5. 정리 전/후 통계 비교 리포트 출력

실행:
  source .venv/bin/activate && python scripts/dedupe_instructors.py [--apply]

기본은 dry-run. --apply 옵션 지정 시 실제 DB 변경.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, text

from app import create_app
from app.extensions import db
from app.models.instructor import Instructor
from app.models.match import Match
from app.models.class_session import ClassSession
from app.models.ml_training_log import MLTrainingLog
from app.models.grade_history import GradeHistory


def _dup_key(inst: Instructor) -> tuple:
    """중복 판정 키 — 이름 + 권역 + 정렬한 specialties + cert_level."""
    specs = tuple(sorted(inst.specialties or []))
    return (inst.name, inst.region, specs, inst.cert_level or 0)


def find_duplicate_groups() -> list[list[Instructor]]:
    """중복 그룹(2명 이상) 목록 반환."""
    groups: dict[tuple, list[Instructor]] = defaultdict(list)
    for inst in Instructor.query.all():
        groups[_dup_key(inst)].append(inst)
    return [g for g in groups.values() if len(g) > 1]


def activity_score(inst: Instructor) -> tuple:
    """
    유지/삭제 결정용 활동 점수.
    1) matches 수 많을수록 우선
    2) class_sessions 수 많을수록
    3) ml_logs 수 많을수록
    4) id 작은 쪽 (시드 안정성)
    """
    mcnt = Match.query.filter_by(instructor_id=inst.id).count()
    scnt = ClassSession.query.filter_by(instructor_id=inst.id).count()
    lcnt = MLTrainingLog.query.filter_by(instructor_id=inst.id).count()
    return (mcnt, scnt, lcnt, -inst.id)


def migrate_refs(keep_id: int, drop_id: int) -> dict:
    """
    drop_id 의 FK 참조를 keep_id 로 일괄 이전.
    동일 (request_id, instructor_id) 매칭이 양쪽에 모두 존재하는 경우는
    keep 쪽을 살리고 drop 쪽은 삭제 (UNIQUE 제약은 없지만 중복 매칭은 의미 없음).
    """
    stats = {'matches_moved': 0, 'matches_dropped': 0,
             'sessions_moved': 0, 'ml_logs_moved': 0,
             'grade_histories_moved': 0}

    # matches: 동일 request_id 에 keep 매칭이 이미 있으면 drop 쪽 제거,
    #         아니면 drop 매칭을 keep_id 로 재배정
    drop_matches = Match.query.filter_by(instructor_id=drop_id).all()
    keep_req_ids = {
        m.request_id for m in Match.query.filter_by(instructor_id=keep_id).all()
    }
    for m in drop_matches:
        if m.request_id in keep_req_ids:
            # 같은 요청에 keep/drop 둘 다 추천된 케이스 → drop 쪽 매칭 + 자식 정리
            ClassSession.query.filter_by(match_id=m.id).delete(synchronize_session=False)
            db.session.delete(m)
            stats['matches_dropped'] += 1
        else:
            m.instructor_id = keep_id
            stats['matches_moved'] += 1
            keep_req_ids.add(m.request_id)

    # class_sessions (match FK 미경유 직접 참조분)
    moved = ClassSession.query.filter_by(instructor_id=drop_id).update(
        {ClassSession.instructor_id: keep_id}, synchronize_session=False,
    )
    stats['sessions_moved'] += moved

    # ml_training_logs: (request_id, instructor_id) 동일 row가 양쪽에 있으면 drop
    drop_logs = MLTrainingLog.query.filter_by(instructor_id=drop_id).all()
    keep_log_keys = {
        (l.request_id, l.instructor_id)
        for l in MLTrainingLog.query.filter_by(instructor_id=keep_id).all()
    }
    keep_log_keys = {(rid, keep_id) for (rid, _) in keep_log_keys}
    for lg in drop_logs:
        if (lg.request_id, keep_id) in keep_log_keys:
            db.session.delete(lg)
        else:
            lg.instructor_id = keep_id
            stats['ml_logs_moved'] += 1

    # grade_history
    moved = GradeHistory.query.filter_by(instructor_id=drop_id).update(
        {GradeHistory.instructor_id: keep_id}, synchronize_session=False,
    )
    stats['grade_histories_moved'] += moved

    return stats


def print_snapshot(label: str) -> dict:
    """현재 DB 상태 스냅샷 출력."""
    snap = {
        'instructors': Instructor.query.count(),
        'matches': Match.query.count(),
        'sessions': ClassSession.query.count(),
        'ml_logs': MLTrainingLog.query.count(),
        'grade_histories': GradeHistory.query.count(),
    }
    print(f'\n[{label}]')
    for k, v in snap.items():
        print(f'  {k:<18}= {v}')
    return snap


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help='실제 DB에 정리 적용 (미지정 시 dry-run)')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        before = print_snapshot('정리 전')

        groups = find_duplicate_groups()
        print(f'\n발견된 중복 그룹: {len(groups)}개')

        total_to_drop = 0
        plan: list[dict] = []
        for grp in groups:
            grp_sorted = sorted(grp, key=activity_score, reverse=True)
            keep = grp_sorted[0]
            drops = grp_sorted[1:]
            total_to_drop += len(drops)
            plan.append({
                'name': keep.name,
                'keep_id': keep.id,
                'keep_matches': Match.query.filter_by(instructor_id=keep.id).count(),
                'drop_ids': [d.id for d in drops],
                'drop_matches': [
                    Match.query.filter_by(instructor_id=d.id).count() for d in drops
                ],
            })

        print(f'삭제 예정 강사 row 수: {total_to_drop}')
        print(f'정리 후 예상 강사 수: {before["instructors"] - total_to_drop}\n')

        print('정리 계획 (전체):')
        print(f'  {"name":<10}{"keep_id":<10}{"keep_m":<10}{"drop_ids":<20}{"drop_m":<10}')
        print('  ' + '-' * 60)
        for p in plan:
            print(f'  {p["name"]:<10}{p["keep_id"]:<10}{p["keep_matches"]:<10}'
                  f'{str(p["drop_ids"]):<20}{str(p["drop_matches"]):<10}')

        if not args.apply:
            print('\n** DRY-RUN ** — 실제 변경 없음. 적용하려면 --apply 옵션 추가.')
            return

        # 실제 적용
        agg = {'matches_moved': 0, 'matches_dropped': 0, 'sessions_moved': 0,
               'ml_logs_moved': 0, 'grade_histories_moved': 0, 'instructors_dropped': 0}
        for p in plan:
            keep_id = p['keep_id']
            for drop_id in p['drop_ids']:
                stats = migrate_refs(keep_id, drop_id)
                for k, v in stats.items():
                    agg[k] += v
                Instructor.query.filter_by(id=drop_id).delete(synchronize_session=False)
                agg['instructors_dropped'] += 1
        db.session.commit()

        print('\n정리 결과 집계:')
        for k, v in agg.items():
            print(f'  {k:<25}= {v}')

        after = print_snapshot('정리 후')

        print('\n변화량 (전→후):')
        for k in before:
            print(f'  {k:<18}: {before[k]} → {after[k]}  (Δ {after[k] - before[k]:+d})')


if __name__ == '__main__':
    main()

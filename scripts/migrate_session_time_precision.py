"""
class_sessions 시간 정밀도 향상 마이그레이션 (검증 이슈 #7 수정)

추가되는 컬럼:
  session_start_time  TIME NULL
  session_end_time    TIME NULL

기존 카테고리(오전/오후/저녁) 기반 세션은 다음 기본 시간 범위로 백필:
  오전 : 09:00 ~ 12:00
  오후 : 13:00 ~ 17:00
  저녁 : 18:00 ~ 21:00

실행:
  source .venv/bin/activate && python scripts/migrate_session_time_precision.py [--apply]

기본은 dry-run. --apply 지정 시 실제 ALTER + 백필.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app import create_app
from app.extensions import db


SESSION_TIME_RANGES = {
    '오전': ('09:00', '12:00'),
    '오후': ('13:00', '17:00'),
    '저녁': ('18:00', '21:00'),
}


def has_column(conn, table: str, column: str) -> bool:
    row = conn.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name=:t AND column_name=:c
    """), {'t': table, 'c': column}).first()
    return bool(row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        conn = db.engine.connect()
        trans = conn.begin()
        try:
            need_start = not has_column(conn, 'class_sessions', 'session_start_time')
            need_end = not has_column(conn, 'class_sessions', 'session_end_time')
            print(f'session_start_time 컬럼 필요: {need_start}')
            print(f'session_end_time 컬럼 필요  : {need_end}')

            total = conn.execute(text(
                'SELECT COUNT(*) FROM class_sessions'
            )).scalar()
            print(f'기존 class_sessions row 수 : {total}')

            cat_counts = conn.execute(text("""
                SELECT session_time, COUNT(*) FROM class_sessions
                GROUP BY session_time
            """)).all()
            print(f'카테고리 분포               : {dict(cat_counts)}')

            if not args.apply:
                trans.rollback()
                print('\n** DRY-RUN ** — 적용하려면 --apply 옵션 추가.')
                return

            if need_start:
                conn.execute(text(
                    'ALTER TABLE class_sessions '
                    'ADD COLUMN session_start_time TIME NULL'
                ))
                print('+ session_start_time 컬럼 추가')
            if need_end:
                conn.execute(text(
                    'ALTER TABLE class_sessions '
                    'ADD COLUMN session_end_time TIME NULL'
                ))
                print('+ session_end_time 컬럼 추가')

            # 카테고리 기반 백필
            backfilled = 0
            for cat, (s, e) in SESSION_TIME_RANGES.items():
                res = conn.execute(
                    text('''
                        UPDATE class_sessions
                        SET session_start_time = :s, session_end_time = :e
                        WHERE session_time = :cat
                          AND (session_start_time IS NULL OR session_end_time IS NULL)
                    '''),
                    {'s': s, 'e': e, 'cat': cat},
                )
                backfilled += res.rowcount or 0
            print(f'\n카테고리 기준 백필: {backfilled} row')

            trans.commit()
            print('\n✅ 마이그레이션 완료.')
        except Exception:
            trans.rollback()
            raise
        finally:
            conn.close()


if __name__ == '__main__':
    main()

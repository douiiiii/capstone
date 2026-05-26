"""
Supabase DB 추가 컬럼 정합화 (2차 마이그레이션)
─────────────────────────────────────────────────────────────────────

대상 컬럼:
  instructors.travel_range         (varchar)  → jsonb 배열 (권역명까지 매핑)
  instructors.target_audience      (varchar)  → jsonb 배열
  instructors.preferred_org_types  (text)     → jsonb 배열
  instructors.disliked_org_types   (text)     → jsonb 배열

특별 규칙:
  - travel_range '화성시 전역' → 5개 권역 전체 배열
  - travel_range 의 각 토큰은 1차 마이그레이션과 동일한 권역 매핑 적용
  - preferred/disliked 가 NULL 인 행은 NULL 유지

안전장치는 1차와 동일:
  · 백업 테이블 존재/row 수 검증
  · 단일 트랜잭션 — 실패 시 자동 롤백
"""
from __future__ import annotations

import sys
from sqlalchemy import text

from app import create_app
from app.extensions import db


REGION_LIST = ['동부권', '서부권', '북부권', '남부권', '중부권']

# travel_range, target_audience 등에 추가 변환할 컬럼 목록
EXTRA_TARGETS = [
    ('instructors', 'travel_range',        'travel'),   # 권역 매핑 포함
    ('instructors', 'target_audience',     'plain'),    # 단순 분리
    ('instructors', 'preferred_org_types', 'nullable'), # NULL 보존
    ('instructors', 'disliked_org_types',  'nullable'), # NULL 보존
]


def get_column_type(conn, table: str, column: str) -> str:
    return conn.execute(text("""
        SELECT data_type FROM information_schema.columns
        WHERE table_schema='public' AND table_name=:t AND column_name=:c
    """), {'t': table, 'c': column}).scalar()


# ─────────────────────────────────────────────────────────────────────
# 1) 사전 검증
# ─────────────────────────────────────────────────────────────────────
def check_backups(conn) -> None:
    print('\n[1/4] 백업 테이블 사전 검증')
    print('─' * 70)
    pairs = [
        ('instructors', 'instructors_backup'),
        ('organizations', 'organizations_backup'),
        ('education_requests', 'education_requests_backup'),
    ]
    for src, bak in pairs:
        exists = conn.execute(text("""
            SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename=:t)
        """), {'t': bak}).scalar()
        if not exists:
            raise RuntimeError(f'❌ {bak} 백업 테이블이 없습니다.')
        c1 = conn.execute(text(f'SELECT count(*) FROM {src}')).scalar()
        c2 = conn.execute(text(f'SELECT count(*) FROM {bak}')).scalar()
        if c1 != c2:
            raise RuntimeError(f'❌ {src}({c1}) ≠ {bak}({c2}) row 수 불일치')
        print(f'  ✅ {src:25} ↔ {bak:30}  row={c1}')


# ─────────────────────────────────────────────────────────────────────
# 2) 헬퍼 함수 정의
# ─────────────────────────────────────────────────────────────────────
def install_helpers(conn) -> None:
    # plain split: '[,·]' 로 분리 → JSONB 배열
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION _mig_split_jsonb_array(input text)
        RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
          SELECT CASE
            WHEN input IS NULL OR btrim(input) = '' THEN '[]'::jsonb
            ELSE to_jsonb(
              ARRAY(
                SELECT btrim(item)
                FROM unnest(regexp_split_to_array(input, '[,·]')) AS item
                WHERE btrim(item) <> ''
              )
            )
          END;
        $$;
    """))
    # travel_range 전용: '화성시 전역' 처리 + 권역 매핑
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION _mig_travel_to_jsonb(input text)
        RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
          SELECT CASE
            WHEN input IS NULL OR btrim(input) = '' THEN '[]'::jsonb
            WHEN input LIKE '%화성시 전역%' THEN
              '["동부권","서부권","북부권","남부권","중부권"]'::jsonb
            ELSE to_jsonb(
              ARRAY(
                SELECT DISTINCT CASE btrim(item)
                  WHEN '동탄'  THEN '동부권'
                  WHEN '동탄1' THEN '동부권'
                  WHEN '동탄2' THEN '동부권'
                  WHEN '향남'  THEN '서부권'
                  WHEN '팔탄'  THEN '서부권'
                  WHEN '봉담'  THEN '북부권'
                  WHEN '기안'  THEN '북부권'
                  WHEN '우정'  THEN '남부권'
                  WHEN '장안'  THEN '남부권'
                  WHEN '남양'  THEN '남부권'
                  WHEN '화성시청' THEN '중부권'
                  ELSE btrim(item)
                END
                FROM unnest(regexp_split_to_array(input, '[,·]')) AS item
                WHERE btrim(item) <> ''
              )
            )
          END;
        $$;
    """))


def drop_helpers(conn) -> None:
    conn.execute(text('DROP FUNCTION IF EXISTS _mig_split_jsonb_array(text)'))
    conn.execute(text('DROP FUNCTION IF EXISTS _mig_travel_to_jsonb(text)'))


# ─────────────────────────────────────────────────────────────────────
# 3) 컬럼 변환
# ─────────────────────────────────────────────────────────────────────
def convert_column(conn, table: str, column: str, mode: str) -> None:
    current = get_column_type(conn, table, column)
    if current == 'jsonb':
        print(f'  ⏭️  {table}.{column:22} 이미 jsonb — 스킵')
        return

    if mode == 'travel':
        using = f'_mig_travel_to_jsonb({column})'
    elif mode == 'nullable':
        # NULL 은 그대로 NULL 로 보존
        using = (
            f'CASE WHEN {column} IS NULL THEN NULL '
            f'ELSE _mig_split_jsonb_array({column}) END'
        )
    else:  # 'plain'
        using = f'_mig_split_jsonb_array({column})'

    conn.execute(text(f"""
        ALTER TABLE {table}
        ALTER COLUMN {column} TYPE jsonb
        USING ({using})
    """))
    print(f'  ✅ {table}.{column:22} {current} → jsonb  (mode={mode})')


def run_conversions(conn) -> None:
    print('\n[2/4] 컬럼 JSONB 변환')
    print('─' * 70)
    install_helpers(conn)
    for table, column, mode in EXTRA_TARGETS:
        convert_column(conn, table, column, mode)
    drop_helpers(conn)


# ─────────────────────────────────────────────────────────────────────
# 4) 무결성 검증
# ─────────────────────────────────────────────────────────────────────
def integrity_check(conn) -> None:
    print('\n[3/4] 무결성 검증 (row 수 + NULL 미증가)')
    print('─' * 70)

    # row 수
    pairs = [
        ('instructors', 'instructors_backup'),
        ('organizations', 'organizations_backup'),
        ('education_requests', 'education_requests_backup'),
    ]
    for src, bak in pairs:
        c1 = conn.execute(text(f'SELECT count(*) FROM {src}')).scalar()
        c2 = conn.execute(text(f'SELECT count(*) FROM {bak}')).scalar()
        if c1 != c2:
            raise RuntimeError(f'❌ row 수 변동: {src}({c1}) ≠ {bak}({c2})')
        print(f'  ✅ {src:25} row 수 {c1}')

    # NULL 비교 (백업과 동일하거나 작아야 함)
    for table, column, _ in EXTRA_TARGETS:
        bak = f'{table}_backup'
        new_nulls = conn.execute(
            text(f'SELECT count(*) FROM {table} WHERE {column} IS NULL')
        ).scalar()
        old_nulls = conn.execute(
            text(f'SELECT count(*) FROM {bak} WHERE {column} IS NULL')
        ).scalar()
        if new_nulls > old_nulls:
            raise RuntimeError(
                f'❌ {table}.{column} NULL 증가: {old_nulls} → {new_nulls}')
        print(f'  ✅ {table}.{column:22} NULL old={old_nulls} → new={new_nulls}')


# ─────────────────────────────────────────────────────────────────────
# 5) 변환 결과 리포트
# ─────────────────────────────────────────────────────────────────────
def print_report(conn) -> None:
    print('\n[4/4] 변환 결과 리포트')
    print('─' * 70)

    # travel_range 권역 분포 (배열 펼쳐서)
    print('  · travel_range 권역 빈도 (배열 펼침)')
    for r in conn.execute(text("""
        SELECT elem, count(*) AS cnt
        FROM instructors, jsonb_array_elements_text(travel_range) AS elem
        GROUP BY elem ORDER BY cnt DESC
    """)).fetchall():
        print(f'      {r[0]:8} {r[1]:>4}건')

    # target_audience 분포
    print('  · target_audience 빈도')
    for r in conn.execute(text("""
        SELECT elem, count(*) AS cnt
        FROM instructors, jsonb_array_elements_text(target_audience) AS elem
        GROUP BY elem ORDER BY cnt DESC
    """)).fetchall():
        print(f'      {r[0]:8} {r[1]:>4}건')

    # 변환 샘플
    print('  · 변환 샘플 (instructors id=1, 2, 3)')
    for r in conn.execute(text("""
        SELECT id, travel_range, target_audience, preferred_org_types, disliked_org_types
        FROM instructors WHERE id IN (1,2,3) ORDER BY id
    """)).fetchall():
        print(f'      id={r[0]}')
        print(f'        travel_range        : {r[1]}')
        print(f'        target_audience     : {r[2]}')
        print(f'        preferred_org_types : {r[3]}')
        print(f'        disliked_org_types  : {r[4]}')


def main() -> int:
    app = create_app('default')
    with app.app_context():
        print('=' * 70)
        print('  추가 컬럼 정합화 (2차 마이그레이션)')
        print('=' * 70)
        try:
            with db.engine.begin() as conn:
                check_backups(conn)
                run_conversions(conn)
                integrity_check(conn)
                print_report(conn)
            print('\n' + '=' * 70)
            print('  ✅ 2차 마이그레이션 성공 — 트랜잭션 커밋됨')
            print('=' * 70)
            return 0
        except Exception as e:
            print('\n' + '=' * 70)
            print(f'  ❌ 마이그레이션 실패 — 자동 롤백됨')
            print(f'  사유: {e!r}')
            print('=' * 70)
            return 1


if __name__ == '__main__':
    sys.exit(main())

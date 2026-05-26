"""
Supabase DB 데이터 정합화 마이그레이션 (1회용)
─────────────────────────────────────────────────────────────────────

목적: 합의된 형식으로 기존 데이터를 변환한다.

변환 범위 (사용자 합의):
  instructors
    · region          : 구 권역명 → 5권역 (동/서/북/남/중부권)
    · specialties     : 콤마/가운뎃점 문자열 → JSONB 배열
    · cert_level      : 이미 정수(1/2/3) — 스킵
    · available_days  : 콤마/가운뎃점 문자열 → JSONB 배열
    · available_times : 콤마/가운뎃점 문자열 → JSONB 배열
  organizations
    · region          : instructors 와 동일 매핑
  education_requests
    · preferred_times : 콤마/가운뎃점 문자열 → JSONB 배열

안전장치:
  1) instructors_backup / organizations_backup / education_requests_backup 의
     존재와 row 수 일치를 사전 검증한다. 없거나 다르면 즉시 중단.
  2) 모든 변환은 단일 트랜잭션 안에서 수행한다. 어떤 한 단계라도 실패하면
     자동 롤백된다 (with engine.begin()).
  3) 변환 후 row 수 동일·NULL 미증가 검증을 트랜잭션 안에서 수행한다.
     검증 실패 시 예외 → 롤백.
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager

from sqlalchemy import text

from app import create_app
from app.extensions import db


# ─────────────────────────────────────────────────────────────────────
# 0. 권역 매핑
# ─────────────────────────────────────────────────────────────────────
REGION_MAP = {
    '동탄': '동부권', '동탄1': '동부권', '동탄2': '동부권',
    '향남': '서부권', '팔탄': '서부권',
    '봉담': '북부권', '기안': '북부권',
    '우정': '남부권', '장안': '남부권', '남양': '남부권',
    '화성시청': '중부권',
}

# 변환 대상 (text/varchar → JSONB array) 컬럼 정의
JSONB_ARRAY_TARGETS = [
    ('instructors',         'specialties'),
    ('instructors',         'available_days'),
    ('instructors',         'available_times'),
    ('education_requests',  'preferred_times'),
]

# 검증 대상 테이블
TABLES_INTEGRITY = [
    ('instructors',        'instructors_backup'),
    ('organizations',      'organizations_backup'),
    ('education_requests', 'education_requests_backup'),
]


# ─────────────────────────────────────────────────────────────────────
# 1. 사전 검증: 백업 테이블 존재 + 행 수 일치
# ─────────────────────────────────────────────────────────────────────
def check_backups(conn) -> None:
    print('\n[1/6] 백업 테이블 사전 검증')
    print('─' * 70)
    for src, bak in TABLES_INTEGRITY:
        exists = conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_tables
                WHERE schemaname='public' AND tablename=:t
            )
        """), {'t': bak}).scalar()
        if not exists:
            raise RuntimeError(f'❌ 백업 테이블 {bak} 가 존재하지 않습니다. 마이그레이션 중단.')
        c1 = conn.execute(text(f'SELECT count(*) FROM {src}')).scalar()
        c2 = conn.execute(text(f'SELECT count(*) FROM {bak}')).scalar()
        if c1 != c2:
            raise RuntimeError(
                f'❌ {src}({c1}) 와 {bak}({c2}) row 수가 다릅니다. 마이그레이션 중단.')
        print(f'  ✅ {src:25} ↔ {bak:30}  row={c1}')


# ─────────────────────────────────────────────────────────────────────
# 2. 컬럼 타입 조회 (이미 jsonb 면 변환 스킵)
# ─────────────────────────────────────────────────────────────────────
def get_column_type(conn, table: str, column: str) -> str:
    return conn.execute(text("""
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=:t AND column_name=:c
    """), {'t': table, 'c': column}).scalar()


# ─────────────────────────────────────────────────────────────────────
# 3. 권역 매핑 UPDATE  (instructors.region, organizations.region)
# ─────────────────────────────────────────────────────────────────────
def update_regions(conn) -> None:
    print('\n[2/6] 권역 매핑 UPDATE')
    print('─' * 70)

    # CASE WHEN 절을 동적으로 생성
    case_lines = '\n        '.join(
        f"WHEN :{f'k{i}'} THEN :{f'v{i}'}" for i in range(len(REGION_MAP))
    )
    params = {}
    for i, (k, v) in enumerate(REGION_MAP.items()):
        params[f'k{i}'] = k
        params[f'v{i}'] = v

    for table in ('instructors', 'organizations'):
        sql = text(f"""
            UPDATE {table}
            SET region = CASE region
                {case_lines}
                ELSE region
            END
        """)
        result = conn.execute(sql, params)
        # 변환 결과 확인
        after = conn.execute(text(f"""
            SELECT region, count(*) FROM {table} GROUP BY region ORDER BY region
        """)).fetchall()
        print(f'  ✅ {table:20} (rows updated={result.rowcount})')
        print('     변환 후:', ', '.join(f'{r[0]}={r[1]}' for r in after))


# ─────────────────────────────────────────────────────────────────────
# 4. 텍스트 → JSONB 배열 컬럼 변환
#    Postgres ALTER COLUMN ... USING 으로 in-place 변환.
#    구분자: 콤마(,) 와 가운뎃점(·)
# ─────────────────────────────────────────────────────────────────────
def _ensure_split_function(conn) -> None:
    """
    ALTER COLUMN ... USING 절 안에는 서브쿼리를 쓸 수 없으므로
    한 줄짜리 IMMUTABLE 함수로 감싼다.
    """
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


def convert_to_jsonb_array(conn, table: str, column: str) -> None:
    current_type = get_column_type(conn, table, column)
    if current_type == 'jsonb':
        print(f'  ⏭️  {table}.{column:18} 이미 jsonb — 스킵')
        return

    sql = text(f"""
        ALTER TABLE {table}
        ALTER COLUMN {column} TYPE jsonb
        USING _mig_split_jsonb_array({column})
    """)
    conn.execute(sql)
    print(f'  ✅ {table}.{column:18}  {current_type} → jsonb')


def convert_jsonb_columns(conn) -> None:
    print('\n[3/6] JSONB 배열 변환 (text/varchar → jsonb)')
    print('─' * 70)
    _ensure_split_function(conn)
    for table, column in JSONB_ARRAY_TARGETS:
        convert_to_jsonb_array(conn, table, column)
    # 마이그레이션 함수 정리 (선택)
    conn.execute(text('DROP FUNCTION IF EXISTS _mig_split_jsonb_array(text)'))


# ─────────────────────────────────────────────────────────────────────
# 5. cert_level 확인 (이미 정수면 스킵 — 일관성 확인만)
# ─────────────────────────────────────────────────────────────────────
def verify_cert_level(conn) -> None:
    print('\n[4/6] cert_level 정합 검증')
    print('─' * 70)
    dt = get_column_type(conn, 'instructors', 'cert_level')
    if dt == 'integer':
        rows = conn.execute(text("""
            SELECT cert_level, count(*) FROM instructors
            GROUP BY cert_level ORDER BY cert_level
        """)).fetchall()
        print(f'  ⏭️  이미 integer (1=기초, 2=중급, 3=전문가) — 변환 불필요')
        for r in rows:
            print(f'     레벨 {r[0]} → {r[1]}명')
    else:
        # 문자열 등급이 섞여있는 경우 매핑
        print(f'  ⚙️  현재 타입 = {dt}, 매핑 적용 중...')
        conn.execute(text("""
            ALTER TABLE instructors
            ALTER COLUMN cert_level TYPE integer
            USING (CASE cert_level
              WHEN '기초' THEN 1
              WHEN '중급' THEN 2
              WHEN '전문가' THEN 3
              ELSE NULL
            END)
        """))
        print('  ✅ cert_level 변환 완료')


# ─────────────────────────────────────────────────────────────────────
# 6. 무결성 검증
#    - row 수가 백업과 동일
#    - 변환 컬럼들의 NULL 개수가 백업 대비 증가하지 않음
# ─────────────────────────────────────────────────────────────────────
def integrity_check(conn) -> None:
    print('\n[5/6] 무결성 검증 (row 수 + NULL 미증가)')
    print('─' * 70)

    # row 수
    for src, bak in TABLES_INTEGRITY:
        c1 = conn.execute(text(f'SELECT count(*) FROM {src}')).scalar()
        c2 = conn.execute(text(f'SELECT count(*) FROM {bak}')).scalar()
        if c1 != c2:
            raise RuntimeError(
                f'❌ row 수 변동: {src}({c1}) ≠ {bak}({c2})')
        print(f'  ✅ {src:25} row 수 {c1} (백업과 동일)')

    # NULL 검사 (변환 대상 컬럼 + region)
    null_targets = JSONB_ARRAY_TARGETS + [
        ('instructors', 'region'),
        ('organizations', 'region'),
    ]
    for table, column in null_targets:
        bak = {
            'instructors': 'instructors_backup',
            'organizations': 'organizations_backup',
            'education_requests': 'education_requests_backup',
        }[table]
        new_nulls = conn.execute(
            text(f'SELECT count(*) FROM {table} WHERE {column} IS NULL')
        ).scalar()
        old_nulls = conn.execute(
            text(f'SELECT count(*) FROM {bak} WHERE {column} IS NULL')
        ).scalar()
        if new_nulls > old_nulls:
            raise RuntimeError(
                f'❌ {table}.{column} NULL 증가: {old_nulls} → {new_nulls}')
        print(f'  ✅ {table}.{column:18} NULL  old={old_nulls} → new={new_nulls}')


# ─────────────────────────────────────────────────────────────────────
# 7. 변환 결과 리포트
# ─────────────────────────────────────────────────────────────────────
def print_report(conn) -> None:
    print('\n[6/6] 변환 결과 리포트')
    print('─' * 70)

    # 권역별 강사 수
    rows = conn.execute(text("""
        SELECT region, count(*) FROM instructors GROUP BY region ORDER BY region
    """)).fetchall()
    print('  · 권역별 강사 수')
    for r in rows:
        print(f'      {r[0]:8} {r[1]:>4}명')

    # 인증등급 분포
    rows = conn.execute(text("""
        SELECT cert_level, count(*) FROM instructors GROUP BY cert_level ORDER BY cert_level
    """)).fetchall()
    label = {1: '기초', 2: '중급', 3: '전문가'}
    print('  · 인증등급 분포')
    for r in rows:
        print(f'      레벨 {r[0]} ({label.get(r[0], "?"):4}) {r[1]:>4}명')

    # specialties JSONB 배열 샘플
    rows = conn.execute(text("""
        SELECT id, specialties FROM instructors ORDER BY id LIMIT 3
    """)).fetchall()
    print('  · instructors.specialties 변환 샘플')
    for r in rows:
        print(f'      id={r[0]} → {r[1]}')

    # education_requests preferred_times 샘플
    rows = conn.execute(text("""
        SELECT id, preferred_times FROM education_requests ORDER BY id LIMIT 3
    """)).fetchall()
    print('  · education_requests.preferred_times 변환 샘플')
    for r in rows:
        print(f'      id={r[0]} → {r[1]}')


# ─────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────
def main() -> int:
    app = create_app('default')
    with app.app_context():
        print('=' * 70)
        print('  Supabase DB 데이터 정합화 마이그레이션 시작')
        print('=' * 70)
        try:
            # 단일 트랜잭션 — 한 단계라도 실패하면 모든 변경이 롤백됨
            with db.engine.begin() as conn:
                check_backups(conn)
                update_regions(conn)
                convert_jsonb_columns(conn)
                verify_cert_level(conn)
                integrity_check(conn)
                print_report(conn)
            print('\n' + '=' * 70)
            print('  ✅ 마이그레이션 성공 — 트랜잭션 커밋됨')
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

"""
더미 데이터 시드 (현재 Supabase 실제 DB 사용으로 비활성화)

이전에는 라인 21~335 에 약 315줄 분량의 주석 처리된 dead code 가 남아 있었으나
(기관 10개 / 강사 13개 / 교육요청 10개 정의), Supabase 전환 이후 사용되지 않아
2026-05 시점에 완전 제거했다. 더미 데이터가 다시 필요해지면
scripts/seed_demo_data.py 를 참고해 새 스크립트를 만들 것.
"""
from app.models.instructor import Instructor


def seed_if_empty() -> None:
    """DB가 비어있을 때 더미 데이터를 삽입하던 함수 — 현재는 NO-OP."""
    if Instructor.query.first():
        return
    # Supabase 사용 시 더미 데이터 삽입은 비활성화한다.
    # 새 환경 부트스트랩이 필요하면 scripts/seed_demo_data.py 를 사용한다.
    print('ℹ️  Supabase DB 사용 중 — 더미 데이터 삽입 생략')

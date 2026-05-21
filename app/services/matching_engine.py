"""
매칭 엔진 라우터 (v5.0 신규)

규칙 기반 매칭(현재)과 미래의 ML 기반 매칭을 같은 인터페이스로 호출하기 위한 추상화.
A/B 테스트가 가능하도록 엔진 이름을 키로 분기.

엔진 선택 우선순위:
  1. 명시적 인자 (engine='ml_v1')
  2. 환경변수 MATCHING_ENGINE
  3. 기본값 'rule_based_v4'

사용 예:
  result = run_matching(request_id, engine='rule_based_v4')

ML 엔진은 추후 실제 모델이 준비되면 ml_engine_v1 함수로 채워넣는다.
현재는 NotImplementedError 만 발생시킨다.
"""
import hashlib
import os
from typing import Callable

from app.services.matching_service import find_top_matches

DEFAULT_ENGINE = 'rule_based_v4'


def _ml_engine_v1_stub(request_id: int, top_n: int = 5) -> dict | None:
    """
    ML 모델 기반 매칭 (미구현 placeholder).
    실제 ML 모델 학습 완료 후 이 함수를 구현하면 됨.
    인터페이스는 find_top_matches 와 동일하게 유지할 것.
    """
    raise NotImplementedError(
        'ml_v1 엔진은 아직 구현되지 않았습니다. '
        '학습 데이터가 충분히 쌓이면 (GET /api/ml/status 참고) ml_engine_v1 을 구현하세요.'
    )


# 엔진 이름 → 함수 매핑 (새 엔진 추가 시 여기에만 등록)
ENGINES: dict[str, Callable[..., dict | None]] = {
    'rule_based_v4': find_top_matches,
    'ml_v1': _ml_engine_v1_stub,
}


def get_engine_name(explicit: str | None = None) -> str:
    """현재 사용할 엔진 이름 결정"""
    if explicit:
        return explicit
    return os.environ.get('MATCHING_ENGINE', DEFAULT_ENGINE)


def pick_ab_engine(request_id: int, engines: list[str], salt: str = 'matching') -> str:
    """
    A/B 테스트용 결정적(deterministic) 엔진 선택.
    같은 request_id 는 항상 같은 엔진을 받도록 hash 기반으로 분배.

    engines : 후보 엔진 이름 리스트 (예: ['rule_based_v4', 'ml_v1'])
    """
    if not engines:
        return DEFAULT_ENGINE
    h = hashlib.md5(f'{salt}:{request_id}'.encode()).hexdigest()
    bucket = int(h, 16) % len(engines)
    return engines[bucket]


def run_matching(
    request_id: int,
    top_n: int = 5,
    engine: str | None = None,
) -> dict | None:
    """
    선택된 엔진으로 매칭 실행. 응답에 사용 엔진 이름을 포함하여 반환.
    """
    name = get_engine_name(engine)
    func = ENGINES.get(name)
    if not func:
        raise ValueError(f'알 수 없는 매칭 엔진: {name}')
    result = func(request_id, top_n=top_n)
    if result is not None:
        result['engine'] = name
    return result

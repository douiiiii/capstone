# 화성시 권역 정의 및 관련 유틸리티

# 권역별 세부 지역 목록
REGION_DISTRICTS = {
    '동부권': ['동탄1', '동탄2'],
    '서부권': ['향남', '팔탄'],
    '북부권': ['봉담', '기안'],
    '남부권': ['우정', '장안'],
    '중부권': ['화성시청'],
}

# 인접 권역 정의 (서로 맞닿은 권역)
ADJACENT_REGIONS = {
    '동부권': ['중부권', '남부권'],
    '서부권': ['중부권', '북부권'],
    '북부권': ['중부권', '서부권'],
    '남부권': ['중부권', '동부권'],
    '중부권': ['동부권', '서부권', '북부권', '남부권'],
}


def get_region_by_district(district: str) -> str | None:
    """세부 지역명으로 권역 반환"""
    for region, districts in REGION_DISTRICTS.items():
        if district in districts:
            return region
    return None


def are_adjacent(region1: str, region2: str) -> bool:
    """두 권역이 인접한지 여부 반환"""
    return region2 in ADJACENT_REGIONS.get(region1, [])


def get_all_regions() -> list:
    return list(REGION_DISTRICTS.keys())


# 검증 이슈 #3 수정: 권역명 자동 정규화
# 외부 API 입력으로 '동탄1', '향남' 같은 세부 지역명이 들어오면 표준 권역명으로 변환.
# 이미 표준 권역명이면 그대로 반환. 매칭 불가능한 임의 문자열도 그대로 통과시켜
# 후속 단계가 명시적으로 0점 처리하도록 함.
# 추가로 '동탄' 처럼 prefix 만 들어와도 첫 일치 동네에 매핑되는 부분 매칭 지원.
def normalize_region(name: str | None) -> str | None:
    """
    동네명/권역명을 받아 표준 권역명으로 정규화.

    동작:
      - None / 빈 문자열 → None
      - '동부권' 같은 표준 권역명 → 그대로
      - '동탄1' / '향남' 처럼 등록된 동네명 → 해당 권역
      - '동탄' 처럼 동네명 prefix → 첫 일치 권역 (예: 동탄→동부권)
      - 그 외 → 원본 문자열 그대로 반환 (후속 단계에서 0점 처리)
    """
    if not name:
        return None
    if name in REGION_DISTRICTS:
        return name
    if name in ADJACENT_REGIONS:
        return name
    # 정확 매칭
    region = get_region_by_district(name)
    if region:
        return region
    # prefix 매칭: '동탄' → '동탄1' / '동탄2' 의 권역
    for r, districts in REGION_DISTRICTS.items():
        for d in districts:
            if d.startswith(name) or name.startswith(d):
                return r
    return name

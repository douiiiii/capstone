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

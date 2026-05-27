# API 명세서 (API Reference)

> 화성시 AI 시민리더 허브 백엔드 API 문서.
> 협업용 — 1번 팀장(프론트엔드) + 2번 팀원(DB·백엔드).

- **베이스 URL** — `http://localhost:5000` (개발) / 운영 도메인 별도 안내
- **공통 prefix** — `/api`
- **요청/응답 인코딩** — `application/json; charset=utf-8`
- **에러 응답 포맷** — 모든 4xx/5xx 응답은 다음 구조:
  ```json
  {
    "success": false,
    "error": "사람-친화 메시지",
    "code": "ERROR_CODE"
  }
  ```

## 공통 에러 코드

| code | HTTP | 의미 |
|------|------|------|
| `INVALID_BODY` | 400 | 요청 본문(JSON) 누락 |
| `MISSING_FIELDS` | 400 | 필수 필드 누락 |
| `INVALID_TYPE` | 400 | 필드 타입 오류 (예: 정수 자리에 문자열) |
| `OUT_OF_RANGE` | 400 | 값이 허용 범위를 벗어남 |
| `INVALID_INPUT` | 400 | DB 변환 실패 등 입력 오류 |
| `VALUE_ERROR` | 400 | 일반 ValueError |
| `NOT_FOUND` | 404 | 리소스 없음 |
| `INVALID_TRANSITION` | 409 | 상태 전이 불가 (예: 이미 수락된 매칭) |
| `MATCHING_FAILED` | 500 | 매칭 엔진 내부 오류 |
| `DB_ERROR` | 500 | 데이터베이스 오류 |

---

# 1. 강사 (Instructors)

## 1.1 GET `/api/instructors` — 강사 목록 조회

### 쿼리 파라미터

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `region` | string | X | — | 권역 필터 (예: `동부권`) |
| `specialty` | string | X | — | 전문분야 필터 (예: `AI기초`) |
| `is_active` | boolean | X | `true` | 활동 여부 |

### 요청 예시
```sh
GET /api/instructors?region=동부권&specialty=AI기초
```

### 응답 예시
```json
{
  "success": true,
  "count": 2,
  "data": [
    {
      "id": 7,
      "name": "김지현",
      "region": "동부권",
      "travel_range": ["중부권"],
      "specialties": ["AI기초", "챗GPT"],
      "available_days": ["월", "화", "수"],
      "available_times": ["오전", "오후"],
      "max_classes_month": 30,
      "target_audience": ["시니어", "성인"],
      "total_classes": 18,
      "avg_rating": 4.7,
      "last_active": "2026-05-20",
      "is_active": true,
      "preferred_org_types": ["복지관"],
      "disliked_org_types": []
    }
  ]
}
```

> ⚠️ `cert_level` 은 관리자 전용 API 에서만 노출됨.

### 사용 시나리오
- 강사 목록 페이지 좌측 패널 — 필터링 + 정렬
- 권역별 강사 카운트 표시

---

## 1.2 PATCH `/api/instructors/<id>/max-classes` — 월 최대 강의 횟수 변경

### Path 파라미터
| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | int | 강사 ID |

### Request Body
```json
{ "max_classes_month": 25 }
```

| 필드 | 타입 | 필수 | 범위 |
|------|------|------|------|
| `max_classes_month` | int | ✓ | 10 ~ 40 |

### 응답 예시
```json
{
  "success": true,
  "instructor_id": 7,
  "max_classes_month": 25
}
```

### 에러 케이스
- `MISSING_FIELDS` — `max_classes_month` 누락
- `INVALID_TYPE` — 정수가 아닌 값
- `OUT_OF_RANGE` — 10 미만 / 40 초과
- `NOT_FOUND` — 강사 ID 존재하지 않음

### 사용 시나리오
- 강사 마이페이지 "월 강의 가능 횟수" 수정
- 휴직/복귀 시 한도 조정

---

# 2. 교육 요청 (Education Requests)

## 2.1 GET `/api/requests` — 교육 요청 목록

### 쿼리 파라미터
| 필드 | 타입 | 설명 |
|------|------|------|
| `status` | string | `대기` / `매칭중` / `완료` |

### 응답 예시
```json
{
  "success": true,
  "count": 25,
  "data": [
    {
      "id": 1,
      "org_id": 3,
      "org_name": "동탄복지관",
      "org_region": "동부권",
      "specialty_needed": "AI기초",
      "target_audience": "시니어",
      "expected_students": 20,
      "preferred_dates": ["2026-06-01"],
      "preferred_times": ["오전"],
      "frequency": "주 1회 × 4개월",
      "location_type": "대면",
      "status": "완료",
      "created_at": "2026-05-15 10:00:00",
      "failure_reasons": null
    }
  ]
}
```

### 사용 시나리오
- 관리자 대시보드 — 신규 요청 알림
- 수요처 마이페이지 — 내 요청 이력

---

# 3. 매칭 (Matches)

## 3.1 POST `/api/match` — 강사 매칭 실행

> **핵심 API**. 교육 요청 기반으로 Top 5 매칭 + 신규강사 6번째 슬롯 반환.

### Request Body
```json
{ "request_id": 1 }
```

### 응답 예시 (정상 매칭)
```json
{
  "success": true,
  "request_id": 1,
  "org_name": "동탄복지관",
  "specialty_needed": "AI기초",
  "match_mode": "정상",
  "match_mode_reason": "정상 매칭",
  "message": "정상 - 상위 5명의 강사가 매칭되었습니다.",
  "total_count": 5,
  "auto_excluded": [],
  "data": [
    {
      "id": 101,
      "request_id": 1,
      "instructor_id": 7,
      "instructor_name": "김지현",
      "instructor_region": "동부권",
      "instructor_specialties": ["AI기초", "챗GPT"],
      "instructor_cert_level": 3,
      "instructor_avg_rating": 4.7,
      "instructor_total_classes": 18,
      "match_type": "정상",
      "match_score": 115.0,
      "score_breakdown": {
        "권역 점수 (40점 만점)": 40.0,
        "전문분야 점수 (40점 만점)": 40.0,
        "시간대 점수 (20점 만점)": 20.0,
        "기본 합계": 100.0,
        "평점 보너스": 5.0,
        "활동일 패널티": 0,
        "최종 총점": 115.0
      },
      "status": "매칭제안",
      "satisfaction_score": null,
      "created_at": "2026-05-27 15:00:00",
      "breakdown": {
        "base": {
          "권역_점수": 40.0,
          "전문분야_점수": 40.0,
          "시간대_점수": 20.0,
          "기본_합계": 100.0
        },
        "bonuses": [
          {"항목": "평점 보너스", "점수": 5.0, "사유": "평점 4.7 (4.5~4.7 → +5점)"},
          {"항목": "상성 보너스", "점수": 15.0, "사유": "복지관 평균 만족도 4.8 → +15점"}
        ],
        "penalties": [],
        "보너스_합계": 20.0,
        "패널티_합계": 0.0,
        "최종_총점": 115.0,
        "점수_공식": "40(권역) +40(전문분야) +20(시간대) +5(평점 보너스) +15(상성 보너스) = 115",
        "요일_검증": {
          "요청_요일": ["월"],
          "강사_가능요일": ["월", "화", "수"],
          "검증결과": "호환",
          "사유": "요일 ['월'] 가능"
        }
      },
      "score_detail": { "...": "역호환 필드" }
    }
  ]
}
```

### 응답 — 조건 완화 / 신규강사 슬롯 추가 케이스
정상 응답에 다음 필드 추가:

```json
{
  "match_mode": "조건완화추천",
  "match_mode_reason": "매칭 가능 강사가 2명으로 5명 미만이어서 유사 분야 강사를 추가 추천합니다.",
  "failure_reasons": [
    {"code": "no_specialty", "message": "전문분야 일치 강사 없음"}
  ],
  "newcomer_slot": {
    "instructor_id": 22,
    "instructor_name": "박신입",
    "instructor_region": "동부권",
    "match_score": 60.0,
    "base_score": 60.0,
    "exposure_reason": "신규 강사 노출 보장 — 누적 강의 2회, 권역 40+분야 0+시간 20=60점 기본 매칭",
    "slot_type": "신규강사슬롯"
  }
}
```

### `match_mode` 값
| 값 | 의미 |
|---|------|
| `정상` | 권역/분야/시간 정확 일치로 5명 매칭 |
| `인접권역추천` | 같은 권역 강사 없어 인접 권역으로 확장 |
| `유사분야확장` | 정확 분야 강사 없어 유사 분야로 확장 |
| `조건완화추천` | 5명 미만이라 조건 완화로 추가 |
| `최선추천` | 조건 부합 강사 0명 → 평점 상위 3명 |
| `강사없음` | 활성 강사가 1명도 없음 |

### `match_type` 값 (개별 매칭)
| 값 | 의미 |
|---|------|
| `정상` | 일반 매칭 |
| `조건완화추천` | 유사 분야로 추가된 케이스 |
| `최선추천` | 평점 기준 fallback |
| `신규강사보장` | E 항 신규 강사 보장으로 삽입 |

### 사용 시나리오
- 수요처가 요청 작성 후 "매칭하기" 버튼 클릭
- 관리자가 미매칭 요청 일괄 재매칭

---

## 3.2 GET `/api/matches/<request_id>` — 특정 요청의 매칭 결과

### 응답 예시
```json
{
  "success": true,
  "request_id": 1,
  "request_info": { "...": "요청 객체 전체" },
  "count": 5,
  "data": [ "...": "Match 객체 배열 (점수 내림차순)" ]
}
```

---

## 3.3 POST `/api/match/<id>/accept` — 매칭 수락

### Request Body (선택)
```json
{ "note": "메모" }
```

### 응답 예시
```json
{
  "success": true,
  "match_id": 101,
  "status": "수락",
  "message": "매칭이 수락되었습니다."
}
```

### 에러 케이스
- `INVALID_TRANSITION` 409 — 이미 수락/거절/최종확정된 매칭

### 상태 전이
`매칭제안` → `수락` (이 API)
이후 `최종확정` 으로 가려면 `/api/match/select` 호출.

---

## 3.4 POST `/api/match/<id>/reject` — 매칭 거절 + 다음 후보 추천

### Request Body (선택)
```json
{ "reason": "거리가 멀어서" }
```

### 응답 예시
```json
{
  "success": true,
  "match_id": 101,
  "status": "거절",
  "reason": "거리가 멀어서",
  "next_candidate": {
    "id": 102, "instructor_name": "이상우", "match_score": 110.0, "...": "..."
  },
  "message": "매칭이 거절되었습니다."
}
```

### 동작
- `매칭제안` 또는 `수락` 상태에서만 가능
- 거절된 매칭의 다음 후보 강사 1명을 자동으로 응답에 포함
- ML 학습 로그에 거절 사유 자동 기록

---

## 3.5 POST `/api/match/select` — 최종 강사 선택

> 수요처가 Top 5 중 한 명을 최종 선택할 때 호출.

### Request Body
```json
{
  "request_id": 1,
  "instructor_id": 7,
  "not_selected_reasons": {
    "3": "거리가 멀어서",
    "5": "시간대 안 맞음"
  }
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `request_id` | int | ✓ | 요청 ID |
| `instructor_id` | int | ✓ | 선택한 강사 ID |
| `not_selected_reasons` | object | X | 선택 안 된 강사들의 사유 (키 = 강사 id) |

### 응답 예시
```json
{
  "success": true,
  "request_id": 1,
  "instructor_id": 7,
  "not_selected_count": 2,
  "message": "선택이 기록되었습니다."
}
```

### 부수 효과
- 선택된 매칭 → `status='최종확정'`
- 나머지 매칭 → `status='거절'`
- **강의 세션 자동 생성** (frequency 에 따라 1~N개)
- ML 학습 로그에 라벨 기록

---

## 3.6 POST `/api/match/feedback` — 강의 후 만족도 평가

### Request Body
```json
{
  "request_id": 1,
  "instructor_id": 7,
  "satisfaction_score": 4.5,
  "was_conducted": true
}
```

| 필드 | 타입 | 필수 | 범위 |
|------|------|------|------|
| `satisfaction_score` | float | ✓ | 0.0 ~ 5.0 |
| `was_conducted` | boolean | X | 기본 `true` |

### 응답 예시
```json
{
  "success": true,
  "satisfaction_score": 4.5,
  "was_conducted": true,
  "message": "피드백이 기록되었습니다."
}
```

### 부수 효과
- `Match.satisfaction_score` 업데이트
- `ClassSession.status` → `완료` 갱신
- `Instructor.total_classes` 재계산
- `MLTrainingLog.was_conducted` + `final_satisfaction` 갱신

---

## 3.7 POST `/api/matches/expire-stale` — 30일 무응답 자동 만료

### 응답 예시
```json
{
  "success": true,
  "expired_count": 3,
  "cutoff_date": "2026-04-27T00:00:00",
  "message": "3건의 매칭제안이 자동 만료되었습니다."
}
```

> 크론잡으로 매일 1회 호출 추천.

---

# 4. 대시보드 (Dashboard)

## 4.1 GET `/api/dashboard/summary` — 전체 KPI

### 응답 예시
```json
{
  "success": true,
  "data": {
    "total_instructors": 42,
    "active_instructors": 38,
    "total_requests": 63,
    "matched_requests": 60,
    "match_rate": 95.2
  }
}
```

> `match_rate` 단위: 퍼센트.

### 사용 시나리오
- 메인 대시보드 상단 KPI 카드 4~5개
- 관리자 페이지 첫 화면

---

## 4.2 GET `/api/dashboard/region` — 권역별 통계

### 응답 예시
```json
{
  "success": true,
  "count": 5,
  "data": [
    {"region": "남부권", "instructor_count": 5, "request_count": 8},
    {"region": "동부권", "instructor_count": 12, "request_count": 18},
    {"region": "북부권", "instructor_count": 7, "request_count": 9},
    {"region": "서부권", "instructor_count": 6, "request_count": 11},
    {"region": "중부권", "instructor_count": 8, "request_count": 12}
  ]
}
```

### 사용 시나리오
- 권역별 막대 그래프 (강사 vs 요청 비교)

---

## 4.3 GET `/api/dashboard/specialty` — 전문분야별 통계

### 응답 예시
```json
{
  "success": true,
  "data": {
    "instructor_by_specialty": [
      {"specialty": "AI기초", "count": 18},
      {"specialty": "챗GPT", "count": 12}
    ],
    "top_requested": [
      {"specialty": "AI기초", "count": 22},
      {"specialty": "스마트폰활용", "count": 11}
    ]
  }
}
```

### 사용 시나리오
- 도넛 차트 — 전문분야 분포
- 인기 분야 Top 5 막대 그래프

---

## 4.4 GET `/api/dashboard/failure-stats` — 매칭 실패 패턴

### 응답 예시
```json
{
  "success": true,
  "data": {
    "top_reasons": [
      {"code": "no_region", "message": "해당 권역(인접 권역 포함) 강사 없음", "count": 8},
      {"code": "no_specialty", "message": "전문분야 일치 강사 없음", "count": 5}
    ],
    "by_region": [
      {"region": "남부권", "failed_request_count": 3},
      {"region": "동부권", "failed_request_count": 1}
    ],
    "total_failed_requests": 11
  }
}
```

### 사용 시나리오
- 관리자 페이지 "어디에 강사가 부족한가?" 인사이트

---

# 5. 지도 (Map)

> 모든 좌표는 `lat` / `lng` 키 사용. Kakao / Leaflet / Google 호환.

## 5.1 GET `/api/map/regions` — 권역별 중심 좌표 + 카운트

### 응답 예시
```json
{
  "success": true,
  "count": 5,
  "data": [
    {
      "region": "동부권",
      "lat": 37.20, "lng": 127.07,
      "areas": ["동탄1", "동탄2"],
      "instructor_count": 12,
      "request_count": 18,
      "matched_count": 16
    }
  ]
}
```

---

## 5.2 GET `/api/map/heatmap` — 히트맵 데이터

### 응답 예시
```json
{
  "success": true,
  "count": 5,
  "data": [
    {"region": "동부권", "lat": 37.20, "lng": 127.07, "intensity": 18}
  ]
}
```

> `intensity = 0` 인 권역은 응답에서 제외.

### 사용 시나리오
- Leaflet `L.heatLayer(data.map(d => [d.lat, d.lng, d.intensity]))`
- Kakao 사용자 정의 오버레이

---

## 5.3 GET `/api/map/instructors` — 강사별 위치

### 응답 예시
```json
{
  "success": true,
  "count": 38,
  "data": [
    {
      "id": 7, "name": "김지현",
      "region": "동부권", "lat": 37.20, "lng": 127.07,
      "specialties": ["AI기초", "챗GPT"],
      "avg_rating": 4.7,
      "cert_level": 3
    }
  ]
}
```

> ⚠️ 좌표는 **개인 정확 좌표가 아니라 소속 권역 중심 좌표** (개인정보 보호).

---

# 6. 관리자 (Admin)

> 모든 admin API 는 헤더에 `X-Admin-Token: <ADMIN_TOKEN>` 필수.
> 환경변수 `ADMIN_TOKEN` 미설정 시 503 응답.

## 6.1 GET `/api/admin/instructors` — 등급 포함 전체 강사

### 응답 예시 (`/api/instructors` 와 동일 + `cert_level` 추가)
```json
{
  "success": true,
  "count": 42,
  "data": [
    {
      "id": 7, "name": "김지현",
      "cert_level": 3,
      "cert_level_updated_at": "2026-04-01 09:00:00",
      "...": "..."
    }
  ]
}
```

---

## 6.2 GET `/api/admin/growth` — 승급 대상 강사

### 응답 예시
```json
{
  "success": true,
  "count": 3,
  "data": [
    {
      "instructor_id": 12,
      "instructor_name": "박중급",
      "current_grade": 2,
      "next_grade": 3,
      "progress_pct": 92,
      "classes_progress": "55/60",
      "rating_progress": "4.6/4.5"
    }
  ]
}
```

---

## 6.3 GET `/api/admin/grade-history` — 등급 변경 이력

### 응답 예시
```json
{
  "success": true,
  "count": 5,
  "data": [
    {
      "id": 1,
      "instructor_id": 7,
      "instructor_name": "김지현",
      "from_grade": 2,
      "to_grade": 3,
      "reason": "강의 62회 + 평점 4.7",
      "changed_at": "2026-04-01 09:00:00"
    }
  ]
}
```

---

## 6.4 POST `/api/admin/grade-upgrade` — 일괄 자동 승급

### Request Body
없음.

### 응답 예시
```json
{
  "success": true,
  "upgraded_count": 2,
  "data": [
    { "instructor_id": 12, "from_grade": 2, "to_grade": 3, "reason": "강의 62회 + 평점 4.7" }
  ]
}
```

> 호출 시 모든 활성 강사를 검사해 조건 충족자를 한 번에 승급.

---

# 7. ML (Machine Learning)

## 7.1 GET `/api/ml/features/<request_id>` — 정규화 피처

### 응답 예시
```json
{
  "success": true,
  "request_id": 1,
  "request_features": {
    "specialty_onehot": [0, 1, 0, ...],
    "time_onehot": [1, 0, 0],
    "...": "..."
  },
  "instructor_features": [
    {"instructor_id": 7, "...": "..."},
    {"instructor_id": 8, "...": "..."}
  ]
}
```

### 사용 시나리오
- ML 모델 추론 (현재는 더미)
- 학습 데이터 검증

---

## 7.2 GET `/api/ml/data-quality` — 데이터 품질 리포트

### 응답 예시
```json
{
  "success": true,
  "data": {
    "missing_rates": { "cert_level": 0.05, "avg_rating": 0.0 },
    "outlier_count": 2,
    "quality_score": 92
  }
}
```

---

## 7.3 GET `/api/ml/status` — 학습 데이터 진척도

### 응답 예시
```json
{
  "success": true,
  "data": {
    "total_logs": 312,
    "labeled_logs": 87,
    "target_logs": 500,
    "progress_pct": 17.4,
    "ready_for_training": false
  }
}
```

---

# 부록 A. 매칭 상태 흐름

```
        [매칭제안]
       ╱     ╲
   accept    reject
     ↓         ↓
   [수락]    [거절]
     ↓
   select
     ↓
  [최종확정]
     ↓
  feedback
     ↓
  satisfaction_score 저장
```

DB CHECK 제약: `matches.status ∈ {'매칭제안', '수락', '거절', '최종확정'}`.

---

# 부록 B. 요청 상태 흐름

```
   [대기] → POST /api/match → [완료]
              (매칭 1번이라도 생성됨)
```

DB CHECK 제약: `education_requests.status ∈ {'대기', '매칭중', '완료'}`.

# 화성시 AI 시민리더 허브 플랫폼

> 화성시 AI 시민리더(강사)와 교육 기관(학교/복지관/도서관/기업 등)을 연결하는
> 매칭 알고리즘 백엔드 — Python Flask + PostgreSQL(Supabase) 기반.

[![tests](https://img.shields.io/badge/tests-195%20passed-brightgreen)](#테스트)
[![match-rate](https://img.shields.io/badge/match--rate-100%25-brightgreen)](#핵심-성과)
[![algorithm](https://img.shields.io/badge/algorithm-v4.8-blue)](#알고리즘-버전-히스토리)

---

## 📌 프로젝트 소개

화성시에는 AI/디지털 교육을 진행할 수 있는 **시민리더(강사)**가 다수 양성되어 있지만,
정작 교육이 필요한 **수요처(복지관·도서관·학교)**와의 매칭이 비효율적으로 운영되어 왔다.

본 프로젝트는 다음을 해결한다:

1. **점수 기반 자동 매칭** — 권역/전문분야/시간 등을 100점 만점으로 정량화
2. **부하 분산** — 한 강사에게 일이 몰리지 않도록 패널티/제외 로직
3. **신규 강사 노출 보장** — 평점·이력이 적은 신규 강사도 매칭 기회 확보
4. **수요처 맞춤 추천** — 기관 유형·과거 만족도 기반 상성 시스템
5. **대시보드 + 지도 시각화** — 권역별 강사/요청 분포 한눈에 확인
6. **ML 전환 준비** — 룰 기반 매칭 결과를 ML 학습용 데이터로 자동 적재

> 📷 *(시연 화면 캡처 자리 — 대시보드 / 매칭 결과 / 지도 히트맵)*

---

## 🎯 핵심 성과

| 항목 | 수치 |
|------|------|
| **매칭 성공률** | 100% (모든 교육 요청에 최소 1명 이상 매칭 / fallback 포함) |
| **테스트 통과 수** | **195개** (pytest) |
| **알고리즘 버전** | **v4.8** (룰 기반 + ML 준비 완료) |
| **API 엔드포인트** | **30+개** (강사/요청/매칭/대시보드/지도/관리자/ML) |
| **검증 이슈 해결** | 7대 검증 이슈 + 3대 시나리오 이슈 전체 해결 |
| **더미 데이터 규모** | 강사 40+명 / 기관 30+곳 / 요청 60+건 / 매칭 300+건 |

---

## 🚀 주요 기능

### 1) 매칭 알고리즘 v4.8

100점 만점 기본 점수 + 보너스/패널티 누적식 (총점 무제한):

- **기본 100점**
  - 권역 점수 40점 (일치 40 / 인접 20 / 이동가능 10)
  - 전문분야 점수 40점 (완전 일치 40 / 유사 분야 20)
  - 시간대 점수 20점 (완전 일치 20 / 부분 일치 10)
- **보너스 / 패널티**
  - 평점 / 활동일 / 만족도 / 재요청 / 신규강사 / 상성 / 성장 …
  - 부하 분산 (-15) / 매칭 쏠림 (-10) / 권역 부적합 가드 (-20)
- **자동 제외**
  - 월 강의 최대치 초과 / 정기 강의 일정 충돌 / 요일 불일치 / 인증 등급 부적합

### 2) 정기 강의 세션 시스템

매칭 1건 = 강의 N개 (frequency 자동 파싱)

- `1회성` → 세션 1개
- `주 1회 × 4개월` → 세션 16개
- `주 2회 × 3개월` → 세션 24개

자동 생성된 세션은 충돌 검사·부하 분산에 사용된다.

### 3) 신규 강사 6번째 슬롯

Top 5 매칭과 **별개로** 항상 신규 강사 1명을 추가 노출.
경험 적은 강사에게도 기회를 보장한다.

### 4) 대시보드 + 지도 히트맵

- `GET /api/dashboard/summary` — 전체 요약 KPI
- `GET /api/dashboard/region` — 권역별 통계
- `GET /api/dashboard/failure-stats` — 매칭 실패 패턴 분석
- `GET /api/map/heatmap` — 권역별 수요 강도 (Kakao/Leaflet 호환)

### 5) ML 전환 준비 (v5.0)

매칭 호출마다 추천된 강사 전체에 대해 **MLTrainingLog** 자동 적재.
피처 스냅샷 + 라벨(선택/진행/만족도) 함께 보관 → 향후 학습용.

---

## 🧱 기술 스택

| 영역 | 사용 기술 |
|------|----------|
| 백엔드 | **Python 3.11 · Flask 3.0.3 · SQLAlchemy 3.1.1** |
| 데이터베이스 | **PostgreSQL** (운영 — Supabase) / SQLite (개발) |
| 운영 서버 | Gunicorn 22.0.0 |
| 환경 관리 | python-dotenv 1.0.1 |
| 테스트 | pytest · pytest-flask |
| (예정) ML | scikit-learn / XGBoost — 피처는 이미 적재 중 |

---

## 📂 폴더 구조

```
capstone/
├── README.md                    # ← 본 문서
├── main.py / run.py             # 앱 진입점 (FLASK_ENV 기반 config 선택)
├── config.py                    # 환경별 설정 (DEV / PROD / TEST)
├── devserver.sh                 # 개발 서버 부트 스크립트
├── requirements.txt             # Python 패키지 의존성
├── .env.example                 # 환경 변수 템플릿
│
├── app/
│   ├── __init__.py              # Flask 앱 팩토리 + 블루프린트 등록
│   ├── extensions.py            # SQLAlchemy 인스턴스
│   │
│   ├── models/                  # DB 모델 (SQLAlchemy ORM)
│   │   ├── instructor.py            # 강사
│   │   ├── organization.py          # 수요처(기관)
│   │   ├── education_request.py     # 교육 요청
│   │   ├── match.py                 # 매칭 결과
│   │   ├── class_session.py         # 강의 세션 (v5.1 신규)
│   │   ├── grade_history.py         # 등급 변경 이력 (v4.0)
│   │   └── ml_training_log.py       # ML 학습용 로그 (v5.0)
│   │
│   ├── routes/                  # API 블루프린트
│   │   ├── instructors.py           # 강사 API
│   │   ├── requests.py              # 교육 요청 API
│   │   ├── matches.py               # 매칭/수락/거절/피드백 API
│   │   ├── dashboard.py             # 대시보드 KPI API
│   │   ├── map.py                   # 지도 / 히트맵 API
│   │   ├── admin.py                 # 관리자 API (등급 승급 등)
│   │   ├── ml.py                    # ML 데이터 API
│   │   └── _errors.py               # 공용 에러 핸들러
│   │
│   └── services/                # 비즈니스 로직
│       ├── matching_service.py      # 매칭 알고리즘 본체 (1300+ LoC)
│       ├── class_session_service.py # 강의 세션 자동 생성
│       ├── grade_service.py         # 강사 등급 자동 업그레이드
│       ├── region_service.py        # 권역 정의 / 인접 / 정규화
│       ├── feature_encoder.py       # ML 피처 인코딩
│       ├── data_quality.py          # 데이터 품질 리포트
│       ├── ml_logger.py             # ML 학습 로그 적재
│       └── seed_data.py             # 더미 데이터 자동 시드
│
├── scripts/                     # 일회성 운영/마이그레이션 스크립트
│   ├── seed_demo_data.py            # 더미 데이터 풍부화
│   ├── dedupe_instructors.py        # 중복 강사 정리
│   ├── migrate_*.py                 # 스키마 마이그레이션
│   ├── rematch_*.py                 # 일괄 재매칭
│   ├── run_matching_scenarios.py    # 매칭 시나리오 일괄 실행
│   └── demo_scenarios.py            # 🆕 발표용 데모 시연 스크립트
│
├── tests/                       # pytest 테스트 (195개)
│   ├── test_matching.py             # 매칭 알고리즘 (93개)
│   ├── test_v4.py                   # v4.0 신규 기능 (31개)
│   ├── test_ml.py                   # ML 관련 (28개)
│   ├── test_class_sessions.py       # 세션 시스템 (26개)
│   ├── test_map.py                  # 지도 API (10개)
│   ├── test_dashboard.py            # 대시보드 (7개)
│   └── ...
│
└── docs/                        # 🆕 문서
    ├── API_REFERENCE.md             # API 명세 (프론트/백엔드 협업용)
    └── DATA_FORMAT.md               # 데이터 형식 명세
```

---

## ⚙️ 설치 방법

### 1. 저장소 클론

```sh
git clone https://github.com/<your-org>/capstone.git
cd capstone
```

### 2. 가상환경 + 패키지 설치

```sh
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 환경 변수 설정

```sh
cp .env.example .env
# .env 파일을 열어 DATABASE_URL / ADMIN_TOKEN 등 설정
```

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DATABASE_URL` | (미설정 시 SQLite) | `postgresql://user:pw@host:5432/db` |
| `SECRET_KEY` | `hwaseong-hub-dev-key` | Flask 세션 키 |
| `FLASK_ENV` | `development` | development / production / testing |
| `PORT` | 5000 | 개발 서버 포트 |
| `ADMIN_TOKEN` | (미설정 시 관리자 API 비활성) | `X-Admin-Token` 헤더 검증값 |

---

## ▶️ 실행 방법

### 로컬 개발 (SQLite + 더미 데이터 자동 시드)

```sh
python run.py
# → http://localhost:5000
```

### Supabase / PostgreSQL 연결

```sh
# .env 의 DATABASE_URL 설정 후
python run.py
```

### 운영 (Gunicorn)

```sh
gunicorn -w 4 -b 0.0.0.0:8000 run:app
```

### 시연용 데모

```sh
# 6가지 핵심 기능을 순서대로 시연
python scripts/demo_scenarios.py
```

---

## 🧪 테스트

```sh
pytest -q                          # 195개 테스트 일괄 실행
pytest tests/test_matching.py -v   # 매칭 알고리즘만
pytest -k "regular_class"          # 키워드 매칭
```

---

## 📡 API 엔드포인트 목록

> 상세 명세는 [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) 참조.

### 🧑‍🏫 강사 (Instructors)
| Method | URL | 설명 |
|--------|-----|------|
| `GET`   | `/api/instructors` | 강사 목록 (region/specialty 필터) |
| `PATCH` | `/api/instructors/<id>/max-classes` | 월 최대 강의 횟수 변경 |

### 📋 교육 요청 (Requests)
| Method | URL | 설명 |
|--------|-----|------|
| `GET` | `/api/requests` | 교육 요청 목록 (status 필터) |

### 🤝 매칭 (Matches)
| Method | URL | 설명 |
|--------|-----|------|
| `POST` | `/api/match` | 요청 기반 강사 매칭 (Top 5 + 신규강사 슬롯) |
| `GET`  | `/api/matches/<request_id>` | 특정 요청의 매칭 결과 조회 |
| `POST` | `/api/match/<id>/accept` | 매칭 수락 |
| `POST` | `/api/match/<id>/reject` | 매칭 거절 + 다음 후보 추천 |
| `POST` | `/api/match/select` | 최종 강사 선택 (ML 학습용 라벨) |
| `POST` | `/api/match/feedback` | 강의 완료 후 만족도 제출 |
| `POST` | `/api/matches/expire-stale` | 30일 이상 매칭제안 자동 만료 |

### 📊 대시보드 (Dashboard)
| Method | URL | 설명 |
|--------|-----|------|
| `GET` | `/api/dashboard/summary` | 전체 KPI 요약 |
| `GET` | `/api/dashboard/region` | 권역별 통계 |
| `GET` | `/api/dashboard/specialty` | 전문분야별 통계 |
| `GET` | `/api/dashboard/failure-stats` | 매칭 실패 패턴 |

### 🗺️ 지도 (Map)
| Method | URL | 설명 |
|--------|-----|------|
| `GET` | `/api/map/regions` | 권역 중심 좌표 + 카운트 |
| `GET` | `/api/map/heatmap` | 히트맵(lat/lng/intensity) |
| `GET` | `/api/map/instructors` | 강사별 위치 |

### 🔐 관리자 (Admin) — `X-Admin-Token` 헤더 필수
| Method | URL | 설명 |
|--------|-----|------|
| `GET`  | `/api/admin/instructors` | 등급 포함 전체 강사 |
| `GET`  | `/api/admin/growth` | 승급 대상 강사 |
| `GET`  | `/api/admin/grade-history` | 등급 변경 이력 |
| `POST` | `/api/admin/grade-upgrade` | 일괄 자동 승급 |

### 🤖 ML (Machine Learning)
| Method | URL | 설명 |
|--------|-----|------|
| `GET` | `/api/ml/features/<request_id>` | 요청의 정규화 피처 벡터 |
| `GET` | `/api/ml/data-quality` | 데이터 품질 리포트 |
| `GET` | `/api/ml/status` | 학습 데이터 진척도 |

---

## 🛠️ 알고리즘 버전 히스토리

| 버전 | 핵심 변경 | 시점 |
|------|----------|------|
| **v1.0** | 권역/분야/시간 100점 만점 기본 매칭 | 초기 |
| **v2.0** | 평점 보너스 + 활동일 패널티 + 인증 등급 필터 | 고도화 1차 |
| **v3.0** | 피드백/맞춤추천/부하분산/연속강의/신규강사 | 고도화 2차 |
| **v4.0** | 강사-수요처 상성 시스템 + 강사 성장 추적 + 실패 원인 분석 | 고도화 3차 |
| **v5.0** | ML 학습 데이터 자동 적재 (피처 + 라벨) | ML 준비 |
| **v5.1** | 강의 세션 시스템 (정기 강의 N개 풀이) | 세션 |
| **v4.5** | 신규 강사 6번째 슬롯 / 권역 0점 가드 / 중복 강사 정리 | 시나리오 안정화 |
| **v4.8** | 7대 검증 이슈 전체 해결 (요일검증·에러처리·시간정밀도) | 현재 |

> 내부 코드 식별자는 `engine_version='rule_based_v4'` 로 통일.

---

## 🌐 화성시 권역 정의

| 권역 | 세부 지역 | 중심 좌표 (lat, lng) |
|------|----------|-------------------|
| **동부권** | 동탄1, 동탄2 | 37.20, 127.07 |
| **서부권** | 향남, 팔탄 | 37.07, 126.82 |
| **북부권** | 봉담, 기안 | 37.22, 126.92 |
| **남부권** | 우정, 장안 | 37.00, 126.83 |
| **중부권** | 화성시청 | 37.20, 126.83 |

**인접 관계** — 모든 권역은 `중부권` 과 인접. 동↔남, 서↔북 추가 인접.

> 📷 *(권역 다이어그램 자리 — 화성시 권역 시각화)*

---

## 🗓️ 주요 마일스톤

프로젝트는 약 5개월에 걸쳐 **"기초 구조 → 점수 고도화 → 운영 안정화 → 발표 준비"**
순서로 진행되었다. 각 단계별 핵심 산출물은 다음과 같다.

### Phase 1 — 기초 구조 (M1)
> 매칭 백엔드의 뼈대를 세운 단계.
- ✅ Flask 앱 팩토리 + 블루프린트 구조 확립
- ✅ 강사 / 기관 / 교육요청 / 매칭 4대 도메인 모델 정의
- ✅ `POST /api/match` — 권역 40 + 분야 40 + 시간 20 = 100점 만점 v1.0 알고리즘

### Phase 2 — 알고리즘 고도화 (M2)
> 단순 점수 합산을 넘어 "현실에서 쓸 수 있는" 추천 로직으로 발전.
- ✅ **v2.0** — 평점 보너스 / 활동일 패널티 / 인증 등급 필터 도입
- ✅ **Supabase 연동** — SQLite → PostgreSQL 전환, 성능 테스트
- ✅ **v3.0** — 피드백 / 맞춤추천 / 부하분산 / 연속강의 / 신규강사 추가

### Phase 3 — 시각화 & 운영 도구 (M3)
> 데이터가 보이지 않으면 의미가 없다 — 프론트엔드 협업을 위한 API 정비.
- ✅ 대시보드 API (`summary` / `region` / `specialty`) 구현
- ✅ 지도 히트맵 API (`regions` / `heatmap` / `instructors`) 구현
- ✅ 관리자 API + `X-Admin-Token` 인증 시스템

### Phase 4 — ML 전환 준비 (M4)
> 룰 기반에서 학습 기반으로 가는 다리.
- ✅ **v4.0** — 강사-수요처 상성 시스템 / 성장 추적 / 매칭 실패 원인 분석
- ✅ **v5.0** — MLTrainingLog 자동 적재 + 피처 정규화 + 데이터 품질 리포트
- ✅ **v5.1** — 정기 강의 세션 시스템 (매칭 1건 → 강의 N개)

### Phase 5 — 운영 안정화 (M5)
> 진짜 데이터로 돌려보고 깨진 부분을 모두 수리.
- ✅ DB 데이터 형식 통일 + 일괄 재매칭 (매칭 성공률 **41% → 72.5%**)
- ✅ 마이그레이션 40건 처리 → 매칭 성공률 **100% 달성**
- ✅ 더미 데이터 풍부화 + 모델/DB 불일치 이슈 해소
- ✅ 시나리오 테스트 + **3대 시나리오 이슈** 해결 (중복 강사 / 권역 가드 / 신규 강사 슬롯)
- ✅ **7대 검증 이슈** 전체 수정 (요일 검증 / 에러 처리 / 라벨링 / 시간 정밀도 등)

### Phase 6 — 발표 준비 (M6, 현재)
> 결과물을 보여줄 수 있는 형태로 마무리.
- ✅ 종합 README + 알고리즘 버전 히스토리
- ✅ 발표용 6가지 시연 스크립트 (`scripts/demo_scenarios.py`)
- ✅ API 명세 + 데이터 형식 명세 (협업 문서)

### 📈 핵심 지표 변화

| 시점 | 매칭 성공률 | 알고리즘 버전 | 테스트 |
|------|-----------|-----------|-------|
| Phase 1 종료 | — | v1.0 | 0 |
| Phase 2 종료 | — | v3.0 | ~50 |
| Phase 4 종료 | — | v5.1 | ~140 |
| Phase 5 중 | 41% → 72.5% → **100%** | v4.5 | ~180 |
| 현재 (Phase 6) | **100%** | **v4.8** | **195** |

> 협업 문서:
> - [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) — 프론트엔드 연동용 API 명세
> - [`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md) — DB 스키마 / JSONB / 점수 체계 명세

---

## 📝 라이선스

본 프로젝트는 수원대학교 캡스톤 디자인 과제용 비공개 저장소입니다.
화성시청 협조 — AI 시민리더 사업 데이터 기반.

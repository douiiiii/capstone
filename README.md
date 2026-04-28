# 화성시 AI 시민리더 허브 플랫폼 — 매칭 백엔드

화성시 AI 시민리더를 교육 기관과 연결하는 매칭 알고리즘 백엔드 (Python Flask)

## 프로젝트 구조

```
capstone/
├── run.py                        # 앱 진입점
├── config.py                     # 환경별 설정 (SQLite/PostgreSQL 전환)
├── .env.example                  # 환경변수 예시
└── app/
    ├── __init__.py               # Flask 앱 팩토리
    ├── extensions.py             # SQLAlchemy 인스턴스
    ├── models/                   # DB 모델 (instructors, organizations, education_requests, matches)
    ├── routes/                   # API 엔드포인트 블루프린트
    └── services/                 # 비즈니스 로직 (매칭 알고리즘, 권역 정의, 더미 데이터)
```

## API 엔드포인트

| 메서드 | URL | 설명 |
|--------|-----|------|
| `GET`  | `/api/instructors` | 강사 목록 (`?region=동부권&specialty=AI기초` 필터) |
| `GET`  | `/api/requests` | 교육 요청 목록 (`?status=대기중` 필터) |
| `POST` | `/api/match` | 교육 요청 기반 강사 매칭 (상위 3명) |
| `GET`  | `/api/matches/<request_id>` | 특정 요청의 매칭 결과 조회 |

### POST /api/match 요청 예시

```json
{ "request_id": 1 }
```

## 매칭 점수 계산 방식 (100점 만점)

| 항목 | 만점 | 조건 |
|------|------|------|
| 권역 점수 | 40점 | 같은 권역 40 / 인접 권역 20 / 이동가능 범위 내 10 |
| 전문분야 점수 | 40점 | 완전 일치 40 / 유사 분야 20 |
| 시간대 점수 | 20점 | 완전 일치 20 / 부분 일치 10 |

## 화성시 권역 정의

- **동부권**: 동탄1, 동탄2
- **서부권**: 향남, 팔탄
- **북부권**: 봉담, 기안
- **남부권**: 우정, 장안
- **중부권**: 화성시청 인근

## 실행 방법

```sh
# 개발 서버 (SQLite 자동 사용, 더미 데이터 자동 삽입)
python run.py

# 또는 기존 스크립트
./devserver.sh
```

### PostgreSQL 연결 시

```sh
cp .env.example .env
# .env 파일에서 DATABASE_URL 설정 후 실행
python run.py
```

## 개발 환경

- Python 3.11
- Flask 3.0.3
- Flask-SQLAlchemy 3.1.1
- PostgreSQL (운영) / SQLite (개발)

from app.extensions import db
from datetime import datetime


class ClassSession(db.Model):
    """
    개별 강의 세션 모델 (v5.1 신규)

    하나의 Match(매칭)는 강의 빈도(frequency)와 기간에 따라
    여러 개의 ClassSession 으로 풀어진다.
      - 1회성 강의 : 매칭 1건 → 세션 1개
      - 정기 강의  : 매칭 1건 → 주기·기간에 맞춰 세션 N개

    누적 강의 횟수(total_classes), 월 부하 계산, 시간대 충돌 검사 등은
    이 테이블을 단일 소스(single source of truth) 로 사용한다.
    """
    __tablename__ = 'class_sessions'

    id = db.Column(db.Integer, primary_key=True)
    # 어떤 매칭에서 파생된 세션인지
    match_id = db.Column(db.Integer, db.ForeignKey('matches.id'), nullable=False)
    # 강사 FK (조회 편의를 위해 비정규화. Match.instructor_id 와 동일)
    instructor_id = db.Column(db.Integer, db.ForeignKey('instructors.id'), nullable=False)
    # 개별 강의 날짜 (예: 2026-06-01)
    session_date = db.Column(db.Date, nullable=False)
    # 시간대 (오전 / 오후 / 저녁)
    session_time = db.Column(db.String(10), nullable=False)
    # 상태 (예정 / 완료 / 취소)
    status = db.Column(db.String(10), default='예정', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'match_id': self.match_id,
            'instructor_id': self.instructor_id,
            'session_date': str(self.session_date) if self.session_date else None,
            'session_time': self.session_time,
            'status': self.status,
            'created_at': str(self.created_at) if self.created_at else None,
        }

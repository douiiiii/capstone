"""
강사 등급 변경 이력 모델 (v4.0 신규)

자동 업그레이드 발생 시 1행씩 기록.
관리자 API(GET /api/admin/grade-history)에서 조회 가능.
"""
from datetime import datetime

from app.extensions import db


class GradeHistory(db.Model):
    __tablename__ = 'grade_histories'

    id = db.Column(db.Integer, primary_key=True)
    instructor_id = db.Column(db.Integer, db.ForeignKey('instructors.id'), nullable=False)
    from_grade = db.Column(db.String(20))               # 변경 전 등급
    to_grade = db.Column(db.String(20), nullable=False) # 변경 후 등급
    reason = db.Column(db.String(200))                  # 변경 사유 (예: '강의 12회 + 평점 4.3')
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'instructor_id': self.instructor_id,
            'instructor_name': self.instructor.name if self.instructor else None,
            'from_grade': self.from_grade,
            'to_grade': self.to_grade,
            'reason': self.reason,
            'changed_at': str(self.changed_at) if self.changed_at else None,
        }

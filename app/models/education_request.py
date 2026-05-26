from app.extensions import db
from datetime import datetime


class EducationRequest(db.Model):
    """교육 요청 모델"""
    __tablename__ = 'education_requests'

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)
    specialty_needed = db.Column(db.String(50))     # 필요 전문 분야
    target_audience = db.Column(db.String(50))      # 교육 대상 (시니어, 성인, 청소년)
    expected_students = db.Column(db.Integer)       # 예상 수강생 수
    preferred_dates = db.Column(db.JSON)            # 선호 날짜 목록
    preferred_times = db.Column(db.JSON)            # 선호 시간대 목록
    frequency = db.Column(db.String(20))            # 수업 빈도 (주 1회, 격주 등)
    location_type = db.Column(db.String(20))        # 수업 방식 (대면, 온라인, 혼합)
    # 요청 상태 — DB CHECK 제약: {'대기', '매칭중', '완료'}
    status = db.Column(db.String(20), default='대기')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # v4.0: 매칭 실패 원인 (find_top_matches 결과가 5명 미만일 때 저장)
    # 예: [{"code": "no_region", "message": "..."}]
    failure_reasons = db.Column(db.JSON)

    matches = db.relationship('Match', backref='request', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'org_id': self.org_id,
            'org_name': self.organization.name if self.organization else None,
            'org_region': self.organization.region if self.organization else None,
            'specialty_needed': self.specialty_needed,
            'target_audience': self.target_audience,
            'expected_students': self.expected_students,
            'preferred_dates': self.preferred_dates,
            'preferred_times': self.preferred_times,
            'frequency': self.frequency,
            'location_type': self.location_type,
            'status': self.status,
            'created_at': str(self.created_at) if self.created_at else None,
            'failure_reasons': self.failure_reasons,
        }

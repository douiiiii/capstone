from app.extensions import db
from datetime import datetime


class Match(db.Model):
    """강사-교육요청 매칭 결과 모델"""
    __tablename__ = 'matches'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('education_requests.id'), nullable=False)
    instructor_id = db.Column(db.Integer, db.ForeignKey('instructors.id'), nullable=False)
    match_score = db.Column(db.Float)           # 총 매칭 점수
    region_score = db.Column(db.Float)          # 권역 점수 (40점 만점)
    specialty_score = db.Column(db.Float)       # 전문분야 점수 (40점 만점)
    time_score = db.Column(db.Float)            # 시간대 점수 (20점 만점)
    rating_bonus = db.Column(db.Float, default=0.0)       # 평점 보너스 (+10 / +5 / 0)
    activity_penalty = db.Column(db.Float, default=0.0)   # 활동일 패널티 (-5 / -10 / 0)
    # 매칭 유형: 정상 / 조건완화추천 / 최선추천 / 신규강사보장
    match_type = db.Column(db.String(20), default='정상')
    status = db.Column(db.String(20), default='매칭제안')  # 매칭 상태 (매칭제안, 수락, 거절, 확정, 완료)
    # 수요처가 매칭 완료 후 매기는 만족도 점수 (1.0 ~ 5.0)
    satisfaction_score = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        instructor = self.instructor
        return {
            'id': self.id,
            'request_id': self.request_id,
            'instructor_id': self.instructor_id,
            'instructor_name': instructor.name if instructor else None,
            'instructor_region': instructor.region if instructor else None,
            'instructor_specialties': instructor.specialties if instructor else None,
            'instructor_cert_level': instructor.cert_level if instructor else None,
            'instructor_avg_rating': instructor.avg_rating if instructor else None,
            'instructor_total_classes': instructor.total_classes if instructor else None,
            'match_type': self.match_type,
            'match_score': self.match_score,
            'score_breakdown': {
                '권역 점수 (40점 만점)': self.region_score,
                '전문분야 점수 (40점 만점)': self.specialty_score,
                '시간대 점수 (20점 만점)': self.time_score,
                '기본 합계': (
                    (self.region_score or 0)
                    + (self.specialty_score or 0)
                    + (self.time_score or 0)
                ),
                '평점 보너스': self.rating_bonus,
                '활동일 패널티': -(self.activity_penalty or 0),
                '최종 총점': self.match_score,
            },
            'status': self.status,
            'satisfaction_score': self.satisfaction_score,
            'created_at': str(self.created_at) if self.created_at else None,
        }

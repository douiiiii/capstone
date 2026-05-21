from app.extensions import db


class Instructor(db.Model):
    """강사 모델"""
    __tablename__ = 'instructors'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    region = db.Column(db.String(20), nullable=False)       # 소속 권역 (동부권, 서부권 등)
    travel_range = db.Column(db.JSON)                       # 이동 가능 권역 목록
    specialties = db.Column(db.JSON)                        # 전문 분야 목록
    cert_level = db.Column(db.String(20))                   # 자격 수준 (기초, 중급, 전문가)
    available_days = db.Column(db.JSON)                     # 수업 가능 요일
    available_times = db.Column(db.JSON)                    # 수업 가능 시간대
    max_classes_month = db.Column(db.Integer, default=4)    # 월 최대 수업 횟수
    target_audience = db.Column(db.JSON)                    # 교육 가능 대상
    total_classes = db.Column(db.Integer, default=0)        # 누적 수업 수
    avg_rating = db.Column(db.Float, default=0.0)           # 평균 평점
    last_active = db.Column(db.Date)                        # 마지막 활동일
    is_active = db.Column(db.Boolean, default=True)         # 활동 여부

    # v4.0: 강사 - 수요처 상성 시스템
    preferred_org_types = db.Column(db.JSON)                # 선호 기관 유형 (예: ['학교', '복지관'])
    disliked_org_types = db.Column(db.JSON)                 # 비선호 기관 유형
    cert_level_updated_at = db.Column(db.DateTime)          # 등급 마지막 변경 일시

    matches = db.relationship('Match', backref='instructor', lazy=True)
    grade_histories = db.relationship('GradeHistory', backref='instructor', lazy=True)

    def to_dict(self, include_grade_info: bool = False):
        """
        일반 API 응답용 dict.
        include_grade_info=True 인 경우 관리자용 등급 상세 정보를 포함.
        """
        data = {
            'id': self.id,
            'name': self.name,
            'region': self.region,
            'travel_range': self.travel_range,
            'specialties': self.specialties,
            # 인증 등급은 관리자 전용 → 일반 응답에서는 제외
            'available_days': self.available_days,
            'available_times': self.available_times,
            'max_classes_month': self.max_classes_month,
            'target_audience': self.target_audience,
            'total_classes': self.total_classes,
            'avg_rating': self.avg_rating,
            'last_active': str(self.last_active) if self.last_active else None,
            'is_active': self.is_active,
            'preferred_org_types': self.preferred_org_types,
            'disliked_org_types': self.disliked_org_types,
        }
        if include_grade_info:
            data['cert_level'] = self.cert_level
            data['cert_level_updated_at'] = (
                str(self.cert_level_updated_at) if self.cert_level_updated_at else None
            )
        return data

from app.extensions import db


class Organization(db.Model):
    """기관(의뢰처) 모델"""
    __tablename__ = 'organizations'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50))     # 기관 유형 (복지관, 도서관, 주민센터 등)
    region = db.Column(db.String(20))   # 소속 권역
    contact = db.Column(db.String(100)) # 연락처

    requests = db.relationship('EducationRequest', backref='organization', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'region': self.region,
            'contact': self.contact,
        }

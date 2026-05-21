"""
ML 학습용 매칭 로그 모델 (v5.0 신규)

매칭이 발생할 때마다 추천된 강사 각각에 대해 1행씩 기록.
나중에 ML 모델 학습 시 라벨(was_selected/was_conducted/final_satisfaction)과
피처(feature_snapshot)를 함께 가져올 수 있도록 설계.

수명주기:
  1. find_top_matches() 가 호출되면 추천된 N명 각각에 대해 행 생성
     - was_selected=False, was_conducted=False, final_satisfaction=None
  2. POST /api/match/select 호출 시 선택된 강사 행을 was_selected=True 로,
     나머지 행에는 not_selected_reason 기록
  3. POST /api/match/feedback 호출 시 final_satisfaction + was_conducted 갱신
"""
from datetime import datetime

from app.extensions import db


class MLTrainingLog(db.Model):
    __tablename__ = 'ml_training_logs'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('education_requests.id'), nullable=False)
    instructor_id = db.Column(db.Integer, db.ForeignKey('instructors.id'), nullable=False)

    # 라벨 (학습 타겟)
    was_selected = db.Column(db.Boolean, default=False)         # 수요처가 최종 선택했는지
    was_conducted = db.Column(db.Boolean, default=False)        # 실제 강의 진행됐는지
    final_satisfaction = db.Column(db.Float)                    # 1.0~5.0 (없으면 None)

    # 메타데이터
    not_selected_reason = db.Column(db.String(200))             # 선택되지 않은 사유
    match_score = db.Column(db.Float)                           # 매칭 시점 점수 (스냅샷)
    engine_version = db.Column(db.String(20), default='rule_based_v4')  # 사용 매칭 엔진

    # 피처 스냅샷 — 매칭 시점에 모델 학습에 필요한 모든 입력값을 JSON으로 보관
    feature_snapshot = db.Column(db.JSON)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'request_id': self.request_id,
            'instructor_id': self.instructor_id,
            'was_selected': self.was_selected,
            'was_conducted': self.was_conducted,
            'final_satisfaction': self.final_satisfaction,
            'not_selected_reason': self.not_selected_reason,
            'match_score': self.match_score,
            'engine_version': self.engine_version,
            'feature_snapshot': self.feature_snapshot,
            'created_at': str(self.created_at) if self.created_at else None,
            'updated_at': str(self.updated_at) if self.updated_at else None,
        }

    @property
    def is_labeled(self) -> bool:
        """학습 가능한 완전 라벨 여부 (선택 + 진행 + 만족도 모두 확정)"""
        return (
            self.was_selected is not None
            and self.was_conducted is not None
            and self.final_satisfaction is not None
        )

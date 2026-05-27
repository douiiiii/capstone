# 모든 모델을 임포트하여 SQLAlchemy가 테이블을 인식하도록 함
from .instructor import Instructor  # noqa: F401
from .organization import Organization  # noqa: F401
from .education_request import EducationRequest  # noqa: F401
from .match import Match  # noqa: F401
from .grade_history import GradeHistory  # noqa: F401
from .ml_training_log import MLTrainingLog  # noqa: F401
from .class_session import ClassSession  # noqa: F401

from flask_sqlalchemy import SQLAlchemy

# SQLAlchemy 인스턴스 (순환 참조 방지를 위해 별도 모듈로 분리)
db = SQLAlchemy()

from flask import Flask
from .extensions import db
from config import config


def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # SQLAlchemy 초기화
    db.init_app(app)

    # 블루프린트 등록
    from .routes.instructors import instructors_bp
    from .routes.requests import requests_bp
    from .routes.matches import matches_bp
    from .routes.dashboard import dashboard_bp

    app.register_blueprint(instructors_bp, url_prefix='/api')
    app.register_blueprint(requests_bp, url_prefix='/api')
    app.register_blueprint(matches_bp, url_prefix='/api')
    app.register_blueprint(dashboard_bp, url_prefix='/api')

    with app.app_context():
        # 모든 모델 import (테이블 생성을 위해 필요)
        from . import models  # noqa: F401
        db.create_all()

        # 테스트 환경에서는 시드 데이터 삽입 생략 (테스트 픽스처가 직접 관리)
        if not app.config.get('TESTING'):
            from .services.seed_data import seed_if_empty
            seed_if_empty()

    return app

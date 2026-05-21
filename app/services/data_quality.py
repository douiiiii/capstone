"""
ML 데이터 품질 분석 서비스 (v5.0 신규)

수집된 학습 데이터의 결측값/이상값 현황을 분석.
GET /api/ml/data-quality, GET /api/ml/status 응답에 사용.
"""
from app.models.education_request import EducationRequest
from app.models.instructor import Instructor
from app.models.ml_training_log import MLTrainingLog
from app.services.feature_encoder import (
    MATCH_SCORE_VALID_RANGE,
    RATING_VALID_RANGE,
)

# 학습 가능 데이터 목표 수 (이 수치에 도달하면 ML 모델 학습 검토)
ML_TRAINING_TARGET = 500


def _instructor_missing_stats() -> dict:
    """강사 데이터 결측값/이상값 카운트"""
    instructors = Instructor.query.all()
    missing_rating = 0
    missing_last_active = 0
    missing_region = 0
    outlier_rating = 0
    for inst in instructors:
        if inst.avg_rating is None or inst.avg_rating == 0:
            missing_rating += 1
        if not inst.last_active:
            missing_last_active += 1
        if not inst.region:
            missing_region += 1
        if inst.avg_rating is not None and not (
            RATING_VALID_RANGE[0] <= inst.avg_rating <= RATING_VALID_RANGE[1]
        ):
            outlier_rating += 1
    return {
        'total': len(instructors),
        'missing_rating': missing_rating,
        'missing_last_active': missing_last_active,
        'missing_region': missing_region,
        'outlier_rating': outlier_rating,
    }


def _request_missing_stats() -> dict:
    """교육 요청 결측값 카운트"""
    requests = EducationRequest.query.all()
    missing_specialty = 0
    missing_times = 0
    missing_org = 0
    for r in requests:
        if not r.specialty_needed:
            missing_specialty += 1
        if not r.preferred_times:
            missing_times += 1
        if not r.organization:
            missing_org += 1
    return {
        'total': len(requests),
        'missing_specialty': missing_specialty,
        'missing_times': missing_times,
        'missing_organization': missing_org,
    }


def _ml_log_quality_stats() -> dict:
    """학습 로그 라벨링/이상값 카운트"""
    logs = MLTrainingLog.query.all()
    labeled = 0
    selected_only = 0      # 선택은 됐지만 만족도 미입력
    outlier_score = 0
    outlier_satisfaction = 0
    for log in logs:
        if log.is_labeled:
            labeled += 1
        elif log.was_selected and log.final_satisfaction is None:
            selected_only += 1
        # 점수 이상값
        if log.match_score is not None and not (
            MATCH_SCORE_VALID_RANGE[0] <= log.match_score <= MATCH_SCORE_VALID_RANGE[1]
        ):
            outlier_score += 1
        # 만족도 이상값
        if log.final_satisfaction is not None and not (
            RATING_VALID_RANGE[0] <= log.final_satisfaction <= RATING_VALID_RANGE[1]
        ):
            outlier_satisfaction += 1
    return {
        'total_logs': len(logs),
        'labeled': labeled,
        'selected_only': selected_only,
        'outlier_match_score': outlier_score,
        'outlier_satisfaction': outlier_satisfaction,
    }


def _calc_quality_score(inst_stats: dict, req_stats: dict, log_stats: dict) -> float:
    """
    데이터 품질 점수 (0~100).
    결측/이상값 비율이 낮을수록 높은 점수.
    """
    penalties = 0.0

    def _penalty(missing: int, total: int, weight: float) -> float:
        if total == 0:
            return 0.0
        return (missing / total) * weight

    # 강사 측 (가중치 합 30)
    if inst_stats['total']:
        penalties += _penalty(inst_stats['missing_rating'], inst_stats['total'], 10)
        penalties += _penalty(inst_stats['missing_last_active'], inst_stats['total'], 5)
        penalties += _penalty(inst_stats['missing_region'], inst_stats['total'], 10)
        penalties += _penalty(inst_stats['outlier_rating'], inst_stats['total'], 5)

    # 요청 측 (가중치 합 25)
    if req_stats['total']:
        penalties += _penalty(req_stats['missing_specialty'], req_stats['total'], 10)
        penalties += _penalty(req_stats['missing_times'], req_stats['total'], 5)
        penalties += _penalty(req_stats['missing_organization'], req_stats['total'], 10)

    # 로그 측 (가중치 합 20: 이상값만)
    if log_stats['total_logs']:
        penalties += _penalty(log_stats['outlier_match_score'], log_stats['total_logs'], 10)
        penalties += _penalty(log_stats['outlier_satisfaction'], log_stats['total_logs'], 10)

    score = max(0.0, 100.0 - penalties)
    return round(score, 1)


def build_quality_report() -> dict:
    """전체 데이터 품질 리포트 (GET /api/ml/data-quality)"""
    inst_stats = _instructor_missing_stats()
    req_stats = _request_missing_stats()
    log_stats = _ml_log_quality_stats()
    quality_score = _calc_quality_score(inst_stats, req_stats, log_stats)
    return {
        'instructor': inst_stats,
        'request': req_stats,
        'ml_log': log_stats,
        'quality_score': quality_score,
    }


def build_ml_status() -> dict:
    """ML 준비 현황 (GET /api/ml/status)"""
    log_stats = _ml_log_quality_stats()
    quality_score = _calc_quality_score(
        _instructor_missing_stats(),
        _request_missing_stats(),
        log_stats,
    )
    labeled = log_stats['labeled']
    return {
        'labeled_count': labeled,
        'total_logs': log_stats['total_logs'],
        'target': ML_TRAINING_TARGET,
        'needed_more': max(0, ML_TRAINING_TARGET - labeled),
        'progress_pct': round(min(labeled / ML_TRAINING_TARGET * 100, 100.0), 1),
        'quality_score': quality_score,
        'is_ready_for_training': labeled >= ML_TRAINING_TARGET,
    }

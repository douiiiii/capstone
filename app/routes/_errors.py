"""
공용 API 에러 응답 헬퍼 (검증 이슈 #5 수정)

목적:
  - 모든 라우트에서 일관된 에러 응답 포맷 사용
  - 잘못된 타입/누락 필드/존재하지 않는 리소스를 4xx 로 명시 처리
  - SQLAlchemy/Value 예외 가드로 500 노출 방지

공통 응답 포맷:
  { "success": False, "error": "메시지", "code": "ERROR_CODE" }
"""
from functools import wraps
from flask import jsonify
from sqlalchemy.exc import SQLAlchemyError, DataError


def error_response(message: str, code: str, status: int = 400):
    """일관된 에러 응답 생성."""
    return jsonify({
        'success': False,
        'error': message,
        'code': code,
    }), status


def require_fields(data: dict | None, required: list[str]):
    """
    필수 필드 검증. 누락 시 (response, status) 튜플, 정상이면 None.
    """
    if not isinstance(data, dict):
        return error_response('요청 본문(JSON)이 필요합니다.', 'INVALID_BODY', 400)
    missing = [f for f in required if f not in data or data[f] in (None, '')]
    if missing:
        return error_response(
            f'필수 필드 누락: {missing}', 'MISSING_FIELDS', 400,
        )
    return None


def coerce_int(value, field_name: str):
    """
    값을 int 로 변환. 실패 시 (response, status) 튜플 반환.
    성공 시 (int, None) 반환.
    """
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, error_response(
            f'{field_name} 는 정수여야 합니다 (받음: {value!r})',
            'INVALID_TYPE', 400,
        )


def handle_api_errors(view_func):
    """
    라우트 데코레이터.
    SQLAlchemy/Value 예외를 캐치해 일관된 4xx/5xx 응답으로 변환한다.
    뷰가 정상 응답을 반환하면 그대로 통과.
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        try:
            return view_func(*args, **kwargs)
        except DataError as e:
            # 잘못된 타입으로 인한 DB 변환 실패 (예: id='not-a-number')
            return error_response(
                f'잘못된 입력 형식: {str(e.orig)[:120]}',
                'INVALID_INPUT', 400,
            )
        except ValueError as e:
            return error_response(str(e), 'VALUE_ERROR', 400)
        except KeyError as e:
            return error_response(f'필드 누락: {e}', 'MISSING_FIELDS', 400)
        except SQLAlchemyError as e:
            return error_response(
                f'데이터베이스 오류: {type(e).__name__}',
                'DB_ERROR', 500,
            )
    return wrapper

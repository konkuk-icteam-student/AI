"""기존 `app.api:app` 실행 경로를 위한 호환 모듈."""

from app.main import app

__all__ = ["app"]

"""기존 import 경로를 위한 호환 모듈."""

from app.faqs.chunker import build_faq_header, chunk_faq

__all__ = ["build_faq_header", "chunk_faq"]

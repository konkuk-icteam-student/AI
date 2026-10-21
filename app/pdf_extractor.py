"""기존 import 경로를 위한 PDF 호환 모듈."""

from app.ingestion.parser import (
    extract_page_text,
    extract_pdf_pages,
    normalize_direct_text,
    normalize_ocr_text,
)

__all__ = [
    "extract_page_text",
    "extract_pdf_pages",
    "normalize_direct_text",
    "normalize_ocr_text",
]

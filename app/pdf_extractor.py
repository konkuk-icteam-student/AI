from __future__ import annotations

from pathlib import Path

import pymupdf

from app.ocr import ocr_pdf_page


def normalize_direct_text(text: str) -> str:
    """텍스트 PDF의 직접 추출 결과를 한 줄 형태로 정규화한다."""
    if not text:
        return ""
    return " ".join(text.split()).strip()


def normalize_ocr_text(text: str) -> str:
    """OCR 결과는 조직/업무 마커가 있으므로 줄 구조를 유지한다."""
    if not text:
        return ""

    lines: list[str] = []
    for line in text.splitlines():
        cleaned = " ".join(line.split()).strip()
        if cleaned:
            lines.append(cleaned)

    return "\n".join(lines)


def extract_page_text(
    page: pymupdf.Page,
    extraction_mode: str,
) -> tuple[str, str]:
    """
    extraction_mode에 따라 추출 방식을 강제로 결정한다.

    text:
        page.get_text("text")만 사용한다.

    ocr:
        직접 추출 글자 수를 확인하지 않고 무조건 ocr_pdf_page()를 사용한다.
    """
    if extraction_mode == "text":
        raw_text = page.get_text("text")
        return normalize_direct_text(raw_text), "text"

    if extraction_mode == "ocr":
        ocr_text = ocr_pdf_page(page)
        return normalize_ocr_text(ocr_text), "ocr"

    raise ValueError(
        "extraction_mode은 'text' 또는 'ocr'이어야 합니다. "
        f"현재 값: {extraction_mode!r}"
    )


def extract_pdf_pages(
    pdf_path: Path,
    extraction_mode: str,
) -> list[dict]:
    """
    PDF를 페이지별로 추출한다.

    documents/text_pdf의 파일은 extraction_mode="text",
    documents/image_pdf의 파일은 extraction_mode="ocr"로 호출한다.
    """
    if extraction_mode not in {"text", "ocr"}:
        raise ValueError(
            "extraction_mode은 'text' 또는 'ocr'이어야 합니다."
        )

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 파일이 없습니다: {pdf_path}")

    document = pymupdf.open(str(pdf_path))
    pages: list[dict] = []

    try:
        for page_index, page in enumerate(document, start=1):
            try:
                text, extraction_type = extract_page_text(
                    page,
                    extraction_mode=extraction_mode,
                )
            except Exception as exc:
                print(
                    f"[PDF] {pdf_path.name} "
                    f"page={page_index} "
                    f"mode={extraction_mode} "
                    f"추출 실패: {type(exc).__name__}: {exc}"
                )
                continue

            print(
                f"[PDF] {pdf_path.name} "
                f"page={page_index} "
                f"type={extraction_type} "
                f"chars={len(text)}"
            )

            if not text:
                continue

            pages.append(
                {
                    "page": page_index,
                    "text": text,
                    "extraction_type": extraction_type,
                }
            )
    finally:
        document.close()

    return pages


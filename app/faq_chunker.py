from __future__ import annotations

import re
from datetime import date

from app.config import FAQ_CHUNK_OVERLAP, FAQ_CHUNK_SIZE
from app.utils import split_text


def build_faq_header(
    title: str,
    category_names: list[str] | None = None,
    created_at: date | None = None,
) -> str:
    """
    FAQ 청크 앞에 붙일 헤더를 만든다.

    청크 단위로도 어떤 FAQ의 내용인지 알 수 있도록
    제목, 카테고리, 작성일 메타데이터를 포함한다.
    """
    parts = [f"[FAQ] {title.strip()}"]

    if category_names:
        joined_names = ", ".join(
            name.strip()
            for name in category_names
            if name.strip()
        )

        if joined_names:
            parts.append(f"카테고리: {joined_names}")

    if created_at is not None:
        parts.append(f"작성일: {created_at.isoformat()}")

    return " | ".join(parts)


def _split_paragraph_first(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    """
    문단 경계를 우선 기준으로 텍스트를 청크로 나눈다.

    - 문단(빈 줄 기준)을 순서대로 청크에 담고,
      chunk_size를 넘으면 새 청크를 시작한다.
    - 문단 하나가 chunk_size보다 길면
      고정 크기 슬라이딩 윈도우(split_text)로 분할한다.
    - 문단 구분이 없으면 전체를 split_text로 분할한다.
    """
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]

    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            if current:
                chunks.append(current)
                current = ""

            chunks.extend(
                split_text(
                    paragraph,
                    chunk_size=chunk_size,
                    overlap=overlap,
                )
            )
            continue

        if not current:
            current = paragraph

        elif (
            len(current) + len(paragraph) + 1
            <= chunk_size
        ):
            current = f"{current}\n{paragraph}"

        else:
            chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)

    return chunks


def chunk_faq(
    text: str,
    *,
    title: str,
    category_names: list[str] | None = None,
    created_at: date | None = None,
    chunk_size: int = FAQ_CHUNK_SIZE,
    overlap: int = FAQ_CHUNK_OVERLAP,
) -> list[str]:
    """
    FAQ 텍스트(질문+답변 결합, HTML 제거 완료 상태)를
    헤더가 붙은 청크 목록으로 변환한다.
    """
    cleaned_text = text.strip()

    if not cleaned_text:
        return []

    header = build_faq_header(
        title=title,
        category_names=category_names,
        created_at=created_at,
    )

    body_chunks = _split_paragraph_first(
        cleaned_text,
        chunk_size=chunk_size,
        overlap=overlap,
    )

    return [
        f"{header}\n{chunk}"
        for chunk in body_chunks
    ]

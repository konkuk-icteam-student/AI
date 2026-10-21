from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.core.config import CHUNK_OVERLAP, CHUNK_SIZE


# 규정집에서 일반적으로 사용하는 조문 머리를 찾는다.
# 예: 제1조(목적), 제1조의2(적용범위), 제 1 조 (목적)
# 본문의 "제5조에 따라"를 조문 경계로 잘못 보지 않도록
# 줄 시작에 있는 표식만 인정한다.
ARTICLE_HEADER_RE = re.compile(
    r"(?m)^[ \t]*"
    r"(?P<header>"
    r"제\s*\d+\s*조(?:\s*의\s*\d+)?"
    r"(?![가-힣A-Za-z0-9])"
    r"(?:"
    r"\s*[\(\uff08][^\n\)\uff09]{1,100}[\)\uff09]"
    r"|(?=\s*(?:$|[①-⑳]))"
    r")"
    r")"
)


@dataclass(frozen=True)
class PageText:
    page: int
    text: str


@dataclass(frozen=True)
class RegulationChunk:
    text: str
    page: int
    article: str | None


def split_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """조문을 찾지 못했을 때 사용하는 기존 고정 길이 폴백."""
    if not text:
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size는 0보다 커야 합니다.")
    if overlap < 0:
        raise ValueError("overlap은 0 이상이어야 합니다.")
    if overlap >= chunk_size:
        raise ValueError("overlap은 chunk_size보다 작아야 합니다.")

    chunks: list[str] = []
    step = chunk_size - overlap
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start += step
    return chunks


def _normalize_page_text(text: str) -> str:
    """줄 경계는 조문 인식에 필요하므로 유지하고 줄 내 공백만 정리한다."""
    lines = [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in text.splitlines()
    ]
    return "\n".join(line for line in lines if line)


def _page_for_offset(
    offset: int,
    page_boundaries: list[tuple[int, int]],
) -> int:
    page = page_boundaries[0][1]
    for boundary, boundary_page in page_boundaries:
        if boundary > offset:
            break
        page = boundary_page
    return page


def chunk_regulation_pages(
    pages: Iterable[PageText],
    *,
    fallback_chunk_size: int = CHUNK_SIZE,
    fallback_overlap: int = CHUNK_OVERLAP,
) -> list[RegulationChunk]:
    """
    여러 PDF 페이지를 하나의 문서로 보고 조문 단위로 나눈다.

    - 조문이 페이지 경계를 넘어가도 하나의 청크로 유지한다.
    - 첫 조문 앞의 장·절 제목은 첫 조문에 함께 붙인다.
    - 조문 표식이 하나도 없는 문서만 기존 900자 방식으로 폴백한다.
    """
    normalized_pages: list[PageText] = []
    for page in pages:
        normalized = _normalize_page_text(page.text)
        if normalized:
            normalized_pages.append(PageText(page=page.page, text=normalized))

    if not normalized_pages:
        return []

    parts: list[str] = []
    page_boundaries: list[tuple[int, int]] = []
    offset = 0
    for page in normalized_pages:
        page_boundaries.append((offset, page.page))
        parts.append(page.text)
        offset += len(page.text) + 1

    document_text = "\n".join(parts)
    matches = list(ARTICLE_HEADER_RE.finditer(document_text))

    if not matches:
        chunks: list[RegulationChunk] = []
        for page in normalized_pages:
            for text in split_text(
                page.text,
                chunk_size=fallback_chunk_size,
                overlap=fallback_overlap,
            ):
                chunks.append(
                    RegulationChunk(text=text, page=page.page, article=None)
                )
        return chunks

    chunks = []
    preamble = document_text[: matches[0].start()].strip()

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(document_text)
        article_text = document_text[match.start():end].strip()
        if index == 0 and preamble:
            article_text = f"{preamble}\n{article_text}"

        if not article_text:
            continue

        chunks.append(
            RegulationChunk(
                text=article_text,
                page=_page_for_offset(match.start(), page_boundaries),
                article=re.sub(r"\s+", "", match.group("header")),
            )
        )

    return chunks

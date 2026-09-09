from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from app.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
)

from app.pdf_extractor import (
    extract_pdf_pages,
)

logger = logging.getLogger(__name__)


# ============================================================
# 전화번호부 구조 Marker
# ============================================================

MAJOR_PREFIX = "[MAJOR]"
ORGANIZATION_PREFIX = "[ORGANIZATION]"

# phone_contacts의 major_org/organization/role은
# btree 인덱스가 걸려 있어 너무 길면 저장이 실패한다
# (postgres 8kB 페이지 기준 행당 약 2704바이트 제한).
# 조직명/직책은 실제로 이보다 훨씬 짧으므로,
# OCR 오검출로 비정상적으로 긴 값이 들어오면 잘라낸다.
MAX_CONTACT_FIELD_LENGTH = 200


def _cap_field_length(
    value: str,
    field_name: str,
) -> str:

    if len(value) <= MAX_CONTACT_FIELD_LENGTH:
        return value

    logger.warning(
        "%s 길이가 비정상적으로 길어 %d자로 자름 "
        "(원래 %d자, OCR 오검출 가능성): %r...",
        field_name,
        MAX_CONTACT_FIELD_LENGTH,
        len(value),
        value[:50],
    )

    return value[:MAX_CONTACT_FIELD_LENGTH]


# ============================================================
# OCR 문자 정규화
# ============================================================

def normalize_ocr_text(
    text: str,
) -> str:

    replacements = {
        "–": "-",
        "—": "-",
        "−": "-",
        "‐": "-",

        "∼": "~",
        "～": "~",
        "˜": "~",
    }

    for old, new in replacements.items():
        text = text.replace(
            old,
            new,
        )

    return text


# ============================================================
# OCR 한 줄 정리
# ============================================================

def clean_ocr_line(
    line: str,
) -> str:

    if not line:
        return ""

    line = normalize_ocr_text(
        line
    ).strip()

    if not line:
        return ""

    line = re.sub(
        r"[ \t]+",
        " ",
        line,
    )

    line = re.sub(
        r"\s*\|\s*",
        " | ",
        line,
    )

    return line.strip()


# ============================================================
# 일반 PDF
#
# 고정 글자 수 + overlap
# ============================================================

def split_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """
    일반 텍스트 PDF용 단순 sliding-window 청킹.

    예:
        chunk_size = 1000
        overlap = 200

        chunk 0: 0 ~ 999
        chunk 1: 800 ~ 1799
        chunk 2: 1600 ~ 2599

    별도의 문장/조항 분석을 하지 않는다.
    """

    if not text:
        return []

    if chunk_size <= 0:
        raise ValueError(
            "chunk_size는 0보다 커야 합니다."
        )

    if overlap < 0:
        raise ValueError(
            "overlap은 0 이상이어야 합니다."
        )

    if overlap >= chunk_size:
        raise ValueError(
            "overlap은 chunk_size보다 "
            "작아야 합니다."
        )

    chunks: list[str] = []

    step = (
        chunk_size
        - overlap
    )

    start = 0

    while start < len(text):

        end = min(
            start + chunk_size,
            len(text),
        )

        chunk = text[
            start:end
        ].strip()

        if chunk:
            chunks.append(
                chunk
            )

        if end >= len(text):
            break

        start += step

    return chunks


# ============================================================
# 일반 OCR 줄 단위 청크
# ============================================================

def split_ocr_lines(
    text: str,
    lines_per_chunk: int = 20,
    overlap_lines: int = 3,
) -> list[str]:

    if not text:
        return []

    if lines_per_chunk <= 0:
        raise ValueError(
            "lines_per_chunk는 0보다 "
            "커야 합니다."
        )

    if overlap_lines < 0:
        raise ValueError(
            "overlap_lines는 0 이상이어야 합니다."
        )

    if overlap_lines >= lines_per_chunk:
        raise ValueError(
            "overlap_lines는 lines_per_chunk보다 "
            "작아야 합니다."
        )

    lines: list[str] = []

    for raw_line in text.splitlines():

        cleaned = clean_ocr_line(
            raw_line
        )

        if cleaned:
            lines.append(
                cleaned
            )

    if not lines:
        return []

    chunks: list[str] = []

    step = (
        lines_per_chunk
        - overlap_lines
    )

    start = 0

    while start < len(lines):

        end = min(
            start + lines_per_chunk,
            len(lines),
        )

        chunk = "\n".join(
            lines[
                start:end
            ]
        ).strip()

        if chunk:
            chunks.append(
                chunk
            )

        if end >= len(lines):
            break

        start += step

    return chunks


# ============================================================
# 전화번호 판별
# ============================================================

PHONE_PATTERN = re.compile(
    r"""
    ^
    (?:
        \d{2,3}\)\d{3,4}-\d{4}(?:~\d{1,4})?
        |
        \d{2,3}-\d{3,4}-\d{4}(?:~\d{1,4})?
        |
        \d{3,4}-\d{4}(?:~\d{1,4})?
        |
        \d{3,4}~\d{1,4}
        |
        \d{3,4}(?:,\s*\d{3,4})+
        |
        \d{3,4}
    )
    $
    """,
    re.VERBOSE,
)


def is_phone_number(
    value: str,
) -> bool:

    value = normalize_ocr_text(
        value
    )

    value = re.sub(
        r"\s+",
        "",
        value,
    )

    return bool(
        PHONE_PATTERN.fullmatch(
            value
        )
    )


# ============================================================
# ORGANIZATION marker
# ============================================================

def parse_organization_marker(
    line: str,
    current_major: str | None,
) -> tuple[
    str | None,
    str | None,
]:

    value = line[
        len(
            ORGANIZATION_PREFIX
        ):
    ].strip()

    if not value:
        return (
            current_major,
            None,
        )

    if ">" in value:

        major, organization = [
            part.strip()
            for part
            in value.split(
                ">",
                1,
            )
        ]

        return (
            major or current_major,
            organization or None,
        )

    return (
        current_major,
        value,
    )


# ============================================================
# 업무 | 번호
# ============================================================

def parse_contact_line(
    line: str,
) -> tuple[
    str,
    str,
] | None:

    line = clean_ocr_line(
        line
    )

    if "|" not in line:
        return None

    role, phone = line.rsplit(
        "|",
        1,
    )

    role = role.strip()

    phone = normalize_ocr_text(
        phone
    ).strip()

    if not role:
        return None

    if not phone:
        return None

    if not is_phone_number(
        phone
    ):
        return None

    return (
        role,
        phone,
    )


# ============================================================
# 구조화 전화번호부
# ============================================================

def parse_phonebook_text(
    text: str,
) -> list[
    dict[str, str]
]:

    contacts: list[
        dict[str, str]
    ] = []

    current_major: str | None = None

    current_organization: (
        str | None
    ) = None

    for raw_line in text.splitlines():

        line = clean_ocr_line(
            raw_line
        )

        if not line:
            continue

        # MAJOR
        if line.startswith(
            MAJOR_PREFIX
        ):

            current_major = (
                line[
                    len(
                        MAJOR_PREFIX
                    ):
                ].strip()
            )

            current_organization = None

            continue

        # ORGANIZATION
        if line.startswith(
            ORGANIZATION_PREFIX
        ):

            (
                marker_major,
                marker_organization,
            ) = parse_organization_marker(
                line,
                current_major,
            )

            if marker_major:
                current_major = (
                    marker_major
                )

            if marker_organization:
                current_organization = (
                    marker_organization
                )

            continue

        parsed = parse_contact_line(
            line
        )

        if parsed is None:
            continue

        role, phone = parsed

        contacts.append(
            {
                "major_org": _cap_field_length(
                    current_major or "",
                    "major_org",
                ),

                "organization": _cap_field_length(
                    current_organization or "",
                    "organization",
                ),

                "role": _cap_field_length(
                    role,
                    "role",
                ),

                "phone": phone,
            }
        )

    return contacts


# ============================================================
# 구조화 전화번호부 판단
# ============================================================

def is_structured_phonebook(
    text: str,
) -> bool:

    return (
        MAJOR_PREFIX in text
        or ORGANIZATION_PREFIX in text
    )


# ============================================================
# 전화번호 검색용 document
# ============================================================

def build_contact_document(
    contact: dict[str, str],
) -> str:

    lines: list[str] = []

    major_org = contact.get(
        "major_org",
        "",
    ).strip()

    organization = contact.get(
        "organization",
        "",
    ).strip()

    role = contact.get(
        "role",
        "",
    ).strip()

    phone = contact.get(
        "phone",
        "",
    ).strip()

    if major_org:
        lines.append(
            f"대조직: {major_org}"
        )

    if organization:
        lines.append(
            f"조직: {organization}"
        )

    if role:
        lines.append(
            f"업무: {role}"
        )

    if phone:
        lines.append(
            f"전화번호: {phone}"
        )

    return "\n".join(
        lines
    )


# ============================================================
# Chunk ID
# ============================================================

def create_chunk_id(
    source: str,
    page: int,
    chunk_index: int,
    text: str,
) -> str:

    raw = (
        f"{source}|"
        f"{page}|"
        f"{chunk_index}|"
        f"{text}"
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()


# ============================================================
# PDF -> 최종 record
# ============================================================

def extract_pdf_chunks(
    pdf_path: Path,
    extraction_mode: str,
) -> list[
    dict[str, Any]
]:

    if extraction_mode not in {"text", "ocr"}:
        raise ValueError(
            "extraction_mode은 'text' 또는 'ocr'이어야 합니다."
        )

    print(
        f"\n[PDF 읽기] "
        f"{pdf_path.name} "
        f"mode={extraction_mode}"
    )

    pages = extract_pdf_pages(
        pdf_path,
        extraction_mode=extraction_mode,
    )

    records: list[
        dict[str, Any]
    ] = []

    for page_data in pages:

        page_number = int(
            page_data[
                "page"
            ]
        )

        text = str(
            page_data[
                "text"
            ]
        )

        extraction_type = str(
            page_data[
                "extraction_type"
            ]
        )

        # ====================================================
        # OCR + 구조화 전화번호부
        # ====================================================

        if (
            extraction_type == "ocr"
            and is_structured_phonebook(
                text
            )
        ):

            contacts = (
                parse_phonebook_text(
                    text
                )
            )

            print(
                f"  - {page_number}페이지: "
                f"{len(text):,}자, "
                f"{len(contacts)}개 연락처, "
                "structured-phonebook"
            )

            for (
                contact_index,
                contact,
            ) in enumerate(
                contacts
            ):

                document = (
                    build_contact_document(
                        contact
                    )
                )

                if not document:
                    continue

                chunk_id = (
                    create_chunk_id(
                        source=pdf_path.name,
                        page=page_number,
                        chunk_index=(
                            contact_index
                        ),
                        text=document,
                    )
                )

                records.append(
                    {
                        "id": chunk_id,

                        "document": (
                            document
                        ),

                        "metadata": {
                            "source": (
                                pdf_path.name
                            ),

                            "page": (
                                page_number
                            ),

                            "chunk_index": (
                                contact_index
                            ),

                            "extraction_type": (
                                extraction_type
                            ),

                            "record_type": (
                                "phone_contact"
                            ),

                            "major_org": (
                                contact.get(
                                    "major_org",
                                    "",
                                )
                            ),

                            "organization": (
                                contact.get(
                                    "organization",
                                    "",
                                )
                            ),

                            "role": (
                                contact.get(
                                    "role",
                                    "",
                                )
                            ),

                            "phone": (
                                contact.get(
                                    "phone",
                                    "",
                                )
                            ),
                        },
                    }
                )

            continue

        # ====================================================
        # 일반 OCR
        # ====================================================

        if extraction_type == "ocr":

            page_chunks = (
                split_ocr_lines(
                    text,
                    lines_per_chunk=20,
                    overlap_lines=3,
                )
            )

        # ====================================================
        # 일반 텍스트 규정 PDF
        # ====================================================

        else:

            page_chunks = (
                split_text(
                    text
                )
            )

        print(
            f"  - {page_number}페이지: "
            f"{len(text):,}자, "
            f"{len(page_chunks)}개 청크, "
            f"{extraction_type}"
        )

        for (
            chunk_index,
            chunk,
        ) in enumerate(
            page_chunks
        ):

            chunk_id = (
                create_chunk_id(
                    source=pdf_path.name,
                    page=page_number,
                    chunk_index=(
                        chunk_index
                    ),
                    text=chunk,
                )
            )

            records.append(
                {
                    "id": chunk_id,

                    "document": chunk,

                    "metadata": {
                        "source": (
                            pdf_path.name
                        ),

                        "page": (
                            page_number
                        ),

                        "chunk_index": (
                            chunk_index
                        ),

                        "extraction_type": (
                            extraction_type
                        ),

                        "record_type": (
                            "text_chunk"
                        ),
                    },
                }
            )

    return records


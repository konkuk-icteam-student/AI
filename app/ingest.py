from __future__ import annotations

import logging
from typing import Any

import requests

from app.config import (
    TEXT_PDF_DIR,
    IMAGE_PDF_DIR,
    EMBEDDING_MODEL,
    EMBED_BATCH_SIZE,
    INSERT_BATCH_SIZE,
    OLLAMA_BASE_URL,
    OLLAMA_CONNECT_TIMEOUT,
    OLLAMA_REQUEST_TIMEOUT,
    ensure_directories,
    validate_config,
)

from app.db import (
    get_conn,
    init_schema,
    to_vector_literal,
    upsert_phone_contacts,
)

from app.utils import (
    extract_pdf_chunks,
)


# ============================================================
# Ollama 확인
# ============================================================

def check_ollama() -> None:
    """
    Ollama 서버와 임베딩 모델이
    준비되어 있는지 확인한다.
    """

    try:
        response = requests.get(
            f"{OLLAMA_BASE_URL}/api/tags",
            timeout=OLLAMA_CONNECT_TIMEOUT,
        )

        response.raise_for_status()

    except requests.RequestException as exc:

        raise RuntimeError(
            "Ollama 서버에 연결할 수 없습니다.\n"
            "다음 명령으로 상태를 확인하세요:\n"
            "systemctl status ollama\n"
            "또는\n"
            "ollama serve"
        ) from exc

    response_data = (
        response.json()
    )

    models = response_data.get(
        "models",
        [],
    )

    model_names = {
        model.get(
            "name",
            "",
        )
        for model in models
    }

    model_exists = any(
        name == EMBEDDING_MODEL
        or name.startswith(
            f"{EMBEDDING_MODEL}:"
        )
        for name in model_names
    )

    if not model_exists:

        available_models = (
            ", ".join(
                sorted(
                    model_names
                )
            )
            or "없음"
        )

        raise RuntimeError(
            "임베딩 모델을 찾을 수 없습니다: "
            f"{EMBEDDING_MODEL}\n"
            f"현재 설치 모델: "
            f"{available_models}\n"
            "설치 명령:\n"
            f"ollama pull {EMBEDDING_MODEL}"
        )

    print(
        f"[Ollama 확인] "
        f"{EMBEDDING_MODEL}"
    )


# ============================================================
# 임베딩 API
# ============================================================

def embed_texts(
    texts: list[str],
) -> list[list[float]]:
    """
    Ollama API를 이용하여
    여러 텍스트를 임베딩한다.
    """

    if not texts:
        return []

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={
                "model": (
                    EMBEDDING_MODEL
                ),
                "input": texts,
            },
            timeout=(
                OLLAMA_REQUEST_TIMEOUT
            ),
        )

        response.raise_for_status()

    except requests.RequestException as exc:

        error_detail = ""

        if exc.response is not None:

            error_detail = (
                "\nOllama 응답: "
                f"{exc.response.text}"
            )

        raise RuntimeError(
            "임베딩 요청에 실패했습니다: "
            f"{exc}"
            f"{error_detail}"
        ) from exc

    response_data = (
        response.json()
    )

    embeddings = (
        response_data.get(
            "embeddings"
        )
    )

    if not isinstance(
        embeddings,
        list,
    ):
        raise RuntimeError(
            "Ollama 응답에 "
            "embeddings가 없습니다.\n"
            f"응답: {response_data}"
        )

    if (
        len(embeddings)
        != len(texts)
    ):
        raise RuntimeError(
            "요청한 텍스트 수와 "
            "반환된 임베딩 수가 "
            "일치하지 않습니다.\n"
            f"요청 수: {len(texts)}\n"
            f"반환 수: "
            f"{len(embeddings)}"
        )

    return embeddings


# ============================================================
# 전체 임베딩 생성
# ============================================================

def generate_embeddings(
    records: list[
        dict[str, Any]
    ],
) -> None:
    """
    모든 record에 임베딩 값을 추가한다.

    일반 PDF:
        기존 청크 document

    전화번호부:
        구조화된 검색용 document

    둘 다 regulation_chunks에서
    검색 가능하도록 임베딩한다.
    """

    total = len(
        records
    )

    for start in range(
        0,
        total,
        EMBED_BATCH_SIZE,
    ):

        end = min(
            start
            + EMBED_BATCH_SIZE,
            total,
        )

        batch = records[
            start:end
        ]

        texts = [
            record["document"]
            for record in batch
        ]

        embeddings = (
            embed_texts(
                texts
            )
        )

        for (
            record,
            embedding,
        ) in zip(
            batch,
            embeddings,
        ):

            record[
                "embedding"
            ] = embedding

        print(
            f"[임베딩 생성] "
            f"{end}/{total}"
        )


# ============================================================
# regulation_chunks 초기화
# ============================================================

def reset_regulation_chunks() -> None:
    """
    기존 규정/RAG 청크를 모두 삭제한다.

    기존 ingest 방식과 동일하다.
    """

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                TRUNCATE TABLE
                regulation_chunks
                """
            )

    print(
        "[규정 청크 초기화] "
        "regulation_chunks"
    )


# ============================================================
# phone_contacts 초기화
# ============================================================

def reset_phone_contacts() -> None:
    """
    전화번호부 구조화 테이블을 초기화한다.

    전체 ingest를 다시 실행할 때
    삭제된 전화번호가 DB에 남는 문제를 방지한다.
    """

    with get_conn() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                TRUNCATE TABLE
                phone_contacts
                """
            )

    print(
        "[전화번호부 초기화] "
        "phone_contacts"
    )


# ============================================================
# regulation_chunks 저장
# ============================================================

def save_records(
    records: list[
        dict[str, Any]
    ],
) -> None:
    """
    청크, 메타데이터, 임베딩을
    기존 regulation_chunks에 저장한다.

    중요:
    기존 일반 PDF 저장 방식은 변경하지 않는다.
    """

    total = len(
        records
    )

    insert_sql = """
        INSERT INTO regulation_chunks (
            source,
            page,
            chunk_index,
            content,
            embedding
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s::vector
        )
    """

    with get_conn() as conn:

        with conn.cursor() as cur:

            for start in range(
                0,
                total,
                INSERT_BATCH_SIZE,
            ):

                end = min(
                    start
                    + INSERT_BATCH_SIZE,
                    total,
                )

                batch = records[
                    start:end
                ]

                rows = []

                for record in batch:

                    metadata = (
                        record[
                            "metadata"
                        ]
                    )

                    rows.append(
                        (
                            metadata[
                                "source"
                            ],

                            metadata[
                                "page"
                            ],

                            metadata[
                                "chunk_index"
                            ],

                            record[
                                "document"
                            ],

                            to_vector_literal(
                                record[
                                    "embedding"
                                ]
                            ),
                        )
                    )

                cur.executemany(
                    insert_sql,
                    rows,
                )

                print(
                    f"[DB 저장] "
                    f"{end}/{total}"
                )


# ============================================================
# 구조화 전화번호 연락처 추출
# ============================================================

def collect_phone_contacts(
    records: list[
        dict[str, Any]
    ],
) -> list[dict]:
    """
    전체 record 중
    record_type == phone_contact
    인 것만 추출한다.

    일반 PDF record는 절대 포함되지 않는다.
    """

    contacts: list[
        dict
    ] = []

    for record in records:

        metadata = record.get(
            "metadata",
            {},
        )

        record_type = (
            metadata.get(
                "record_type",
                ""
            )
        )

        # --------------------------------------------
        # 일반 PDF / 일반 청크는 건드리지 않는다.
        # --------------------------------------------

        if (
            record_type
            != "phone_contact"
        ):
            continue

        role = str(
            metadata.get(
                "role",
                "",
            )
        ).strip()

        phone = str(
            metadata.get(
                "phone",
                "",
            )
        ).strip()

        # 구조화 데이터에서
        # 직책 또는 번호가 비어있으면 저장하지 않는다.
        if not role:
            continue

        if not phone:
            continue

        contacts.append(
            {
                "id": (
                    record["id"]
                ),

                "source": str(
                    metadata.get(
                        "source",
                        "",
                    )
                ),

                "page": int(
                    metadata.get(
                        "page",
                        0,
                    )
                ),

                "contact_index": int(
                    metadata.get(
                        "chunk_index",
                        0,
                    )
                ),

                "major_org": str(
                    metadata.get(
                        "major_org",
                        "",
                    )
                ),

                "organization": str(
                    metadata.get(
                        "organization",
                        "",
                    )
                ),

                "role": role,

                "phone": phone,

                "content": str(
                    record.get(
                        "document",
                        "",
                    )
                ),
            }
        )

    return contacts


# ============================================================
# 구조화 전화번호 저장
# ============================================================

def save_phone_contacts(
    records: list[
        dict[str, Any]
    ],
) -> int:
    """
    구조화 전화번호 record만
    phone_contacts 테이블에 저장한다.
    """

    contacts = (
        collect_phone_contacts(
            records
        )
    )

    if not contacts:

        print(
            "[전화번호부 저장] "
            "구조화 연락처 없음"
        )

        return 0

    saved_count = (
        upsert_phone_contacts(
            contacts
        )
    )

    print(
        "[전화번호부 저장] "
        f"{saved_count}건"
    )

    return saved_count


# ============================================================
# Record 통계
# ============================================================

def print_record_statistics(
    records: list[
        dict[str, Any]
    ],
) -> None:
    """
    ingest 전에 일반 청크와
    전화번호 구조화 record 수를 확인한다.
    """

    normal_count = 0
    phone_count = 0

    for record in records:

        metadata = record.get(
            "metadata",
            {},
        )

        if (
            metadata.get(
                "record_type"
            )
            == "phone_contact"
        ):

            phone_count += 1

        else:

            normal_count += 1

    print()
    print(
        "[Record 통계]"
    )

    print(
        "  일반 청크: "
        f"{normal_count}"
    )

    print(
        "  전화번호 연락처: "
        f"{phone_count}"
    )

    print(
        "  전체: "
        f"{len(records)}"
    )


# ============================================================
# Main
# ============================================================

def main() -> None:

    # app/ocr.py의 logger.debug(원문 OCR 텍스트, PII 포함)는
    # 기본 INFO 레벨에서 숨겨지고, logger.info 이상 요약만 출력된다.
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s %(levelname)s "
            "%(name)s: %(message)s"
        ),
    )

    print(
        "=" * 60
    )

    print(
        "규정 PDF 인덱싱 시작"
    )

    print(
        "=" * 60
    )

    # ========================================================
    # 환경 확인
    # ========================================================

    validate_config()

    ensure_directories()

    check_ollama()

    init_schema()

    # ========================================================
    # PDF 검색
    #
    # text_pdf  -> 항상 PyMuPDF 직접 추출
    # image_pdf -> 항상 OCR
    # ========================================================

    text_pdf_files = sorted(
        TEXT_PDF_DIR.glob("*.pdf")
    )

    image_pdf_files = sorted(
        IMAGE_PDF_DIR.glob("*.pdf")
    )

    if not text_pdf_files and not image_pdf_files:
        raise FileNotFoundError(
            "PDF 파일이 없습니다.\n"
            f"text_pdf: {TEXT_PDF_DIR}\n"
            f"image_pdf: {IMAGE_PDF_DIR}"
        )

    print(
        f"[TEXT PDF 개수] {len(text_pdf_files)}"
    )
    print(
        f"[IMAGE PDF 개수] {len(image_pdf_files)}"
    )

    # ========================================================
    # PDF -> Record
    # ========================================================

    all_records: list[dict[str, Any]] = []
    failed_files: list[str] = []

    pdf_groups = [
        ("TEXT", "text", text_pdf_files),
        ("IMAGE", "ocr", image_pdf_files),
    ]

    for label, extraction_mode, pdf_files in pdf_groups:
        for pdf_file in pdf_files:
            print()
            print("-" * 60)
            print(
                f"[{label} PDF 처리 시작] "
                f"{pdf_file.name}"
            )
            print("-" * 60)

            try:
                records = extract_pdf_chunks(
                    pdf_file,
                    extraction_mode=extraction_mode,
                )
            except Exception as exc:
                print(
                    f"[처리 실패] {pdf_file.name}: "
                    f"{type(exc).__name__}: {exc}"
                )
                failed_files.append(
                    f"{label}: {pdf_file.name}"
                )
                continue

            all_records.extend(records)

    if failed_files:

        print()
        print(
            "[처리 실패 파일] "
            f"{len(failed_files)}개"
        )

        for name in failed_files:
            print(f"  - {name}")

    if not all_records:

        raise RuntimeError(
            "PDF에서 추출된 "
            "청크가 없습니다."
        )

    # ========================================================
    # 통계
    # ========================================================

    print_record_statistics(
        all_records
    )

    print(
        f"\n[전체 청크 수] "
        f"{len(all_records)}"
    )

    # ========================================================
    # 임베딩 생성
    # ========================================================

    generate_embeddings(
        all_records
    )

    # ========================================================
    # 기존 DB 초기화
    # ========================================================

    reset_regulation_chunks()

    reset_phone_contacts()

    # ========================================================
    # 기존 RAG DB 저장
    # ========================================================

    save_records(
        all_records
    )

    # ========================================================
    # 전화번호부 구조화 DB 저장
    #
    # regulation_chunks 저장(save_records)이 이미 끝난 뒤이므로,
    # 여기서 실패해도 일반 RAG 검색용 데이터는 이미 저장돼 있다.
    # 구조화 저장만 실패로 남기고 인덱싱 자체는 완료 처리한다.
    # ========================================================

    try:
        phone_count = (
            save_phone_contacts(
                all_records
            )
        )

    except Exception as exc:
        print(
            "[전화번호부 저장 실패] "
            f"{type(exc).__name__}: {exc}"
        )

        phone_count = 0

    # ========================================================
    # 완료
    # ========================================================

    print()
    print(
        "=" * 60
    )

    print(
        "인덱싱 완료"
    )

    print(
        f"regulation_chunks 저장: "
        f"{len(all_records)}"
    )

    print(
        f"phone_contacts 저장: "
        f"{phone_count}"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":
    main()


"""PDF 수집부터 임베딩·저장까지 인덱싱 순서를 관리한다."""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import (
    TEXT_PDF_DIR,
    IMAGE_PDF_DIR,
    EMBEDDING_MODEL,
    EMBED_BATCH_SIZE,
    ensure_directories,
    validate_config,
)
from app.infrastructure.database import close_pool, init_schema
from app.ingestion.embedder import check_ollama, embed_texts
from app.ingestion.records import extract_pdf_chunks
from app.ingestion.storage import (
    reset_regulation_chunks,
    reset_phone_contacts,
    save_records,
    save_phone_contacts,
)


def generate_embeddings(records: list[dict[str, Any]]) -> None:
    """
    모든 record에 임베딩 값을 추가한다.

    일반 PDF:
        기존 청크 document

    전화번호부:
        구조화된 검색용 document

    둘 다 regulation_chunks에서
    검색 가능하도록 임베딩한다.
    """

    total = len(records)

    for start in range(0, total, EMBED_BATCH_SIZE):

        end = min(start + EMBED_BATCH_SIZE, total)

        batch = records[start:end]

        texts = [record["document"] for record in batch]

        embeddings = embed_texts(texts)

        for record, embedding in zip(batch, embeddings):

            record["embedding"] = embedding

        print(f"[임베딩 생성] " f"{end}/{total}")


def print_record_statistics(records: list[dict[str, Any]]) -> None:
    """
    ingest 전에 일반 청크와
    전화번호 구조화 record 수를 확인한다.
    """

    normal_count = 0
    phone_count = 0

    for record in records:

        metadata = record.get("metadata", {})

        if metadata.get("record_type") == "phone_contact":

            phone_count += 1

        else:

            normal_count += 1

    print()
    print("[Record 통계]")

    print("  일반 청크: " f"{normal_count}")

    print("  전화번호 연락처: " f"{phone_count}")

    print("  전체: " f"{len(records)}")


def main() -> None:

    # app/ingestion/ocr.py의 logger.debug(원문 OCR 텍스트, PII 포함)는
    # 기본 INFO 레벨에서 숨겨지고, logger.info 이상 요약만 출력된다.
    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s %(levelname)s " "%(name)s: %(message)s"),
    )

    print("=" * 60)

    print("규정 PDF 인덱싱 시작")

    print("=" * 60)

    # ========================================================
    # 환경 확인
    # ========================================================

    validate_config()

    ensure_directories()

    check_ollama()
    print(f"[Ollama 확인] {EMBEDDING_MODEL}")

    init_schema()

    # ========================================================
    # PDF 검색
    #
    # text_pdf  -> 항상 PyMuPDF 직접 추출
    # image_pdf -> 항상 OCR
    # ========================================================

    text_pdf_files = sorted(TEXT_PDF_DIR.glob("*.pdf"))

    image_pdf_files = sorted(IMAGE_PDF_DIR.glob("*.pdf"))

    if not text_pdf_files and not image_pdf_files:
        raise FileNotFoundError(
            "PDF 파일이 없습니다.\n"
            f"text_pdf: {TEXT_PDF_DIR}\n"
            f"image_pdf: {IMAGE_PDF_DIR}"
        )

    print(f"[TEXT PDF 개수] {len(text_pdf_files)}")
    print(f"[IMAGE PDF 개수] {len(image_pdf_files)}")

    # ========================================================
    # PDF -> Record
    # ========================================================

    all_records: list[dict[str, Any]] = []
    failed_files: list[str] = []

    pdf_groups = [("TEXT", "text", text_pdf_files), ("IMAGE", "ocr", image_pdf_files)]

    for label, extraction_mode, pdf_files in pdf_groups:
        for pdf_file in pdf_files:
            print()
            print("-" * 60)
            print(f"[{label} PDF 처리 시작] " f"{pdf_file.name}")
            print("-" * 60)

            try:
                records = extract_pdf_chunks(pdf_file, extraction_mode=extraction_mode)
            except Exception as exc:
                print(f"[처리 실패] {pdf_file.name}: " f"{type(exc).__name__}: {exc}")
                failed_files.append(f"{label}: {pdf_file.name}")
                continue

            all_records.extend(records)

    if failed_files:

        print()
        print("[처리 실패 파일] " f"{len(failed_files)}개")

        for name in failed_files:
            print(f"  - {name}")

    if not all_records:

        raise RuntimeError("PDF에서 추출된 " "청크가 없습니다.")

    # ========================================================
    # 통계
    # ========================================================

    print_record_statistics(all_records)

    print(f"\n[전체 청크 수] " f"{len(all_records)}")

    # ========================================================
    # 임베딩 생성
    # ========================================================

    generate_embeddings(all_records)

    # ========================================================
    # 기존 DB 초기화
    # ========================================================

    reset_regulation_chunks()

    reset_phone_contacts()

    # ========================================================
    # 기존 RAG DB 저장
    # ========================================================

    save_records(all_records)

    # ========================================================
    # 전화번호부 구조화 DB 저장
    #
    # regulation_chunks 저장(save_records)이 이미 끝난 뒤이므로,
    # 여기서 실패해도 일반 RAG 검색용 데이터는 이미 저장돼 있다.
    # 구조화 저장만 실패로 남기고 인덱싱 자체는 완료 처리한다.
    # ========================================================

    try:
        phone_count = save_phone_contacts(all_records)

    except Exception as exc:
        print("[전화번호부 저장 실패] " f"{type(exc).__name__}: {exc}")

        phone_count = 0

    # ========================================================
    # 완료
    # ========================================================

    print()
    print("=" * 60)

    print("인덱싱 완료")

    print(f"regulation_chunks 저장: " f"{len(all_records)}")

    print(f"phone_contacts 저장: " f"{phone_count}")

    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    finally:
        close_pool()

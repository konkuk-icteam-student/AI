"""인덱싱 결과의 PostgreSQL 저장을 담당한다."""

from __future__ import annotations

from typing import Any

from app.core.config import INSERT_BATCH_SIZE
from app.infrastructure.database import (
    get_conn,
    to_vector_literal,
    upsert_phone_contacts,
)
from app.ingestion.contacts import collect_phone_contacts


def reset_regulation_chunks() -> None:
    """전체 인덱싱을 위해 기존 규정 청크를 초기화한다."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE regulation_chunks")
    print("[규정 청크 초기화] regulation_chunks")


def reset_phone_contacts() -> None:
    """삭제된 번호가 남지 않도록 기존 연락처를 초기화한다."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE phone_contacts")
    print("[전화번호부 초기화] phone_contacts")


def save_records(records: list[dict[str, Any]]) -> None:
    """청크·메타데이터·임베딩을 기존 규정 테이블에 저장한다."""
    insert_sql = """
        INSERT INTO regulation_chunks (source, page, chunk_index, content, embedding)
        VALUES (%s, %s, %s, %s, %s::vector)
    """
    total = len(records)
    with get_conn() as conn:
        with conn.cursor() as cur:
            for start in range(0, total, INSERT_BATCH_SIZE):
                end = min(start + INSERT_BATCH_SIZE, total)
                rows = []
                for record in records[start:end]:
                    metadata = record["metadata"]
                    rows.append(
                        (
                            metadata["source"],
                            metadata["page"],
                            metadata["chunk_index"],
                            record["document"],
                            to_vector_literal(record["embedding"]),
                        )
                    )
                cur.executemany(insert_sql, rows)
                print(f"[DB 저장] {end}/{total}")


def save_phone_contacts(records: list[dict[str, Any]]) -> int:
    """전화번호부 청크에서 추출한 구조화 연락처를 저장한다."""
    contacts = collect_phone_contacts(records)
    if not contacts:
        print("[전화번호부 저장] 구조화 연락처 없음")
        return 0

    saved_count = upsert_phone_contacts(contacts)
    print(f"[전화번호부 저장] {saved_count}건")
    return saved_count

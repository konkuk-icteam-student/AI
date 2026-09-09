from __future__ import annotations

from datetime import date

from app.db import get_conn, to_vector_literal
from app.faq_chunker import chunk_faq
from app.ingest import embed_texts


def upsert_faq(
    faq_id: int,
    text: str,
    *,
    title: str,
    category_ids: list[int] | None = None,
    category_names: list[str] | None = None,
    created_at: date | None = None,
) -> int:
    """
    FAQ 한 건을 청킹·임베딩하여 faq_chunks에 저장한다.

    같은 faq_id의 기존 청크는 모두 삭제 후 새로 저장하므로
    생성과 수정 모두 이 함수 하나로 처리된다.

    반환: 저장된 청크 수
    """
    chunks = chunk_faq(
        text,
        title=title,
        category_names=category_names,
        created_at=created_at,
    )

    if not chunks:
        delete_faq(faq_id)
        return 0

    embeddings = embed_texts(chunks)

    insert_sql = (
        "INSERT INTO faq_chunks "
        "(faq_id, chunk_index, content, "
        "category_ids, faq_created_at, embedding) "
        "VALUES (%s, %s, %s, %s::bigint[], %s, %s::vector)"
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM faq_chunks "
                "WHERE faq_id = %s",
                (faq_id,),
            )

            cur.executemany(
                insert_sql,
                [
                    (
                        faq_id,
                        chunk_index,
                        chunk,
                        category_ids or [],
                        created_at,
                        to_vector_literal(embedding),
                    )
                    for chunk_index, (
                        chunk,
                        embedding,
                    ) in enumerate(
                        zip(chunks, embeddings)
                    )
                ],
            )

    return len(chunks)


def delete_faq(faq_id: int) -> int:
    """
    FAQ 삭제 시 해당 faq_id의 청크를 모두 제거한다.

    반환: 삭제된 청크 수
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM faq_chunks "
                "WHERE faq_id = %s",
                (faq_id,),
            )

            deleted_count = cur.rowcount

    return deleted_count

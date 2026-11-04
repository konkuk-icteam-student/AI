from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Iterator

import psycopg2
import psycopg2.pool

from app.core.config import DATABASE_URL, EMBEDDING_DIM

_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = Lock()


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """
    Postgres 연결 풀을 지연 생성하여 반환한다.
    """
    global _pool

    with _pool_lock:
        if _pool is None:
            _pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1, maxconn=10, dsn=DATABASE_URL
            )
        return _pool


def close_pool() -> None:
    """서버 종료 시 연결 풀을 닫고 다음 초기화를 준비한다."""
    global _pool

    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


@contextmanager
def get_conn() -> Iterator[psycopg2.extensions.connection]:
    """
    연결 풀에서 연결을 하나 빌려 사용하고 반환한다.

    정상 종료 시 commit,
    예외 발생 시 rollback 한다.
    """
    pool = _get_pool()
    conn = pool.getconn()

    try:
        yield conn
        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        pool.putconn(conn)


# ============================================================
# 스키마 정의
# ============================================================
#
# regulation_chunks
#   - 기존 일반 규정 PDF/RAG 청크
#
# faq_chunks
#   - 기존 FAQ 청크
#
# phone_contacts
#   - 전화번호부 전용 구조화 연락처
#
# 중요:
# 기존 regulation_chunks / faq_chunks 구조는 변경하지 않는다.
# ============================================================


SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;


-- ==========================================================
-- 기존 규정 PDF 청크
-- ==========================================================

CREATE TABLE IF NOT EXISTS regulation_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    source TEXT NOT NULL,

    page INT NOT NULL,

    chunk_index INT NOT NULL,

    content TEXT NOT NULL,

    embedding vector({EMBEDDING_DIM}) NOT NULL,

    created_at TIMESTAMPTZ
        NOT NULL
        DEFAULT now()
);


CREATE INDEX IF NOT EXISTS
idx_regulation_chunks_embedding
ON regulation_chunks
USING hnsw (
    embedding vector_cosine_ops
);


CREATE INDEX IF NOT EXISTS
idx_regulation_chunks_fts
ON regulation_chunks
USING gin (
    to_tsvector(
        'simple',
        content
    )
);


-- ==========================================================
-- 기존 FAQ 청크
-- ==========================================================

CREATE TABLE IF NOT EXISTS faq_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    faq_id BIGINT NOT NULL,

    chunk_index INT NOT NULL,

    content TEXT NOT NULL,

    category_ids BIGINT[]
        NOT NULL
        DEFAULT '{{}}',

    faq_created_at DATE,

    embedding vector({EMBEDDING_DIM})
        NOT NULL,

    created_at TIMESTAMPTZ
        NOT NULL
        DEFAULT now()
);


CREATE INDEX IF NOT EXISTS
idx_faq_chunks_faq_id
ON faq_chunks (
    faq_id
);


CREATE INDEX IF NOT EXISTS
idx_faq_chunks_embedding
ON faq_chunks
USING hnsw (
    embedding vector_cosine_ops
);


CREATE INDEX IF NOT EXISTS
idx_faq_chunks_fts
ON faq_chunks
USING gin (
    to_tsvector(
        'simple',
        content
    )
);


-- ==========================================================
-- 전화번호부 구조화 연락처
-- ==========================================================

CREATE TABLE IF NOT EXISTS phone_contacts (
    id TEXT PRIMARY KEY,

    source TEXT NOT NULL,

    page INT NOT NULL,

    contact_index INT NOT NULL,

    major_org TEXT,

    organization TEXT,

    role TEXT NOT NULL,

    phone TEXT NOT NULL,

    content TEXT NOT NULL,

    created_at TIMESTAMPTZ
        NOT NULL
        DEFAULT now()
);


-- PDF 단위 삭제/조회
CREATE INDEX IF NOT EXISTS
idx_phone_contacts_source
ON phone_contacts (
    source
);


-- 대조직 검색
CREATE INDEX IF NOT EXISTS
idx_phone_contacts_major_org
ON phone_contacts (
    major_org
);


-- 하위 조직 검색
CREATE INDEX IF NOT EXISTS
idx_phone_contacts_organization
ON phone_contacts (
    organization
);


-- 직책/업무 검색
CREATE INDEX IF NOT EXISTS
idx_phone_contacts_role
ON phone_contacts (
    role
);


-- 전화번호 역검색
CREATE INDEX IF NOT EXISTS
idx_phone_contacts_phone
ON phone_contacts (
    phone
);


-- 자연어/부분 문자열 검색 보조
CREATE INDEX IF NOT EXISTS
idx_phone_contacts_content_fts
ON phone_contacts
USING gin (
    to_tsvector(
        'simple',
        content
    )
);
"""


# ============================================================
# pgvector 변환
# ============================================================


def to_vector_literal(embedding: list[float]) -> str:
    """
    임베딩 리스트를 pgvector 리터럴 문자열로 변환한다.

    SQL에서 %s::vector 형태로 바인딩하여 사용한다.
    """

    return "[" + ",".join(repr(float(value)) for value in embedding) + "]"


# ============================================================
# 스키마 초기화
# ============================================================


def init_schema() -> None:
    """
    pgvector 확장,
    기존 청크 테이블,
    전화번호 구조화 테이블,
    인덱스를 생성한다.

    이미 존재하면 변경하지 않는다.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)


# ============================================================
# 여러 전화번호 연락처 일괄 저장
# ============================================================


def upsert_phone_contacts(contacts: list[dict]) -> int:
    """
    구조화된 연락처 여러 건을 한 번에 저장한다.

    ingest에서 매 건마다 connection을 열지 않도록
    batch 처리한다.

    반환:
        저장한 연락처 수
    """

    if not contacts:
        return 0

    rows = []

    for contact in contacts:

        rows.append(
            (
                contact["id"],
                contact["source"],
                int(contact["page"]),
                int(contact["contact_index"]),
                contact.get("major_org", ""),
                contact.get("organization", ""),
                contact.get("role", ""),
                contact.get("phone", ""),
                contact.get("content", ""),
            )
        )

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.executemany(
                """
                INSERT INTO phone_contacts (
                    id,
                    source,
                    page,
                    contact_index,
                    major_org,
                    organization,
                    role,
                    phone,
                    content
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )

                ON CONFLICT (id)

                DO UPDATE SET
                    source = EXCLUDED.source,
                    page = EXCLUDED.page,
                    contact_index = EXCLUDED.contact_index,
                    major_org = EXCLUDED.major_org,
                    organization = EXCLUDED.organization,
                    role = EXCLUDED.role,
                    phone = EXCLUDED.phone,
                    content = EXCLUDED.content
                """,
                rows,
            )

    return len(rows)


# ============================================================
# DB 상태 확인
# ============================================================


def check_db() -> dict[str, int]:
    """
    DB 연결과 테이블 상태를 확인한다.

    반환:
        각 테이블의 데이터 수
    """

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT count(*)
                FROM regulation_chunks
                """)

            regulation_count = cur.fetchone()[0]

            cur.execute("""
                SELECT count(*)
                FROM faq_chunks
                """)

            faq_count = cur.fetchone()[0]

            cur.execute("""
                SELECT count(*)
                FROM phone_contacts
                """)

            phone_count = cur.fetchone()[0]

    return {
        "regulation_chunks": (regulation_count),
        "faq_chunks": (faq_count),
        "phone_contacts": (phone_count),
    }

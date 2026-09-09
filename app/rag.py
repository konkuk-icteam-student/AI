from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

from app.config import (
    CANDIDATE_K,
    EMBEDDING_MODEL,
    MIN_RELEVANCE_SCORE,
    OLLAMA_BASE_URL,
    OLLAMA_REQUEST_TIMEOUT,
    RRF_K,
    TOP_K,
)
from app.db import get_conn, to_vector_literal


@dataclass
class RegulationSearchResult:
    """
    규정 검색 결과 한 개를 표현하는 자료형
    """

    document: str
    source: str
    page: int
    chunk_index: int
    score: float


@dataclass
class FaqSearchResult:
    """
    FAQ 검색 결과 한 개를 표현하는 자료형
    """

    document: str
    faq_id: int
    chunk_index: int
    score: float


def embed_query(query: str) -> list[float]:
    """
    사용자 질문을 Ollama 임베딩 모델로 벡터화한다.
    """
    query = query.strip()

    if not query:
        raise ValueError("질문이 비어 있습니다.")

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={
                "model": EMBEDDING_MODEL,
                "input": query,
            },
            timeout=OLLAMA_REQUEST_TIMEOUT,
        )

        response.raise_for_status()

    except requests.RequestException as exc:
        detail = ""

        if exc.response is not None:
            detail = f"\nOllama 응답: {exc.response.text}"

        raise RuntimeError(
            f"질문 임베딩 생성에 실패했습니다: {exc}{detail}"
        ) from exc

    data = response.json()
    embeddings = data.get("embeddings")

    if not isinstance(embeddings, list):
        raise RuntimeError(
            f"올바르지 않은 Ollama 응답입니다: {data}"
        )

    if not embeddings:
        raise RuntimeError(
            "Ollama가 임베딩을 반환하지 않았습니다."
        )

    first_embedding = embeddings[0]

    if not isinstance(first_embedding, list):
        raise RuntimeError(
            f"임베딩 형식이 올바르지 않습니다: {data}"
        )

    return first_embedding


def expand_regulation_queries(query: str) -> list[str]:
    """
    복합 질문을 규정 검색에 적합한 여러 검색어로 확장한다.

    예:
    "평점 2.3, 소득분위 3분위, 부모 장애인인데
    받을 수 있는 장학금?"

    ->
    원래 질문
    교내 장학금 종류 및 선발 기준
    장학생 성적 기준 및 평점 기준
    소득분위 저소득층 장학금 선발 기준
    장애인 또는 장애인 가족 장학금 선발 기준
    """

    query = query.strip()
    queries = [query]

    # 장학 관련
    if "장학" in query:
        queries.extend(
            [
                "교내 장학금 종류 및 선발 기준",
                "장학생 성적 기준 및 평점 기준",
            ]
        )

    if "소득분위" in query or "소득" in query:
        queries.append(
            "소득분위 저소득층 장학금 선발 기준"
        )

    if "기초생활수급" in query:
        queries.append(
            "기초생활수급자 장학금 선발 기준"
        )

    if "장애" in query:
        queries.append(
            "장애인 또는 장애인 가족 장학금 선발 기준"
        )

    # 휴학 / 복학 관련
    if "휴학" in query:
        queries.extend(
            [
                "휴학 신청 자격 절차 기간",
                "휴학 등록금 처리 및 복학 절차",
            ]
        )

    if "복학" in query:
        queries.append(
            "복학 신청 절차 및 등록"
        )

    # 졸업 관련
    if "졸업" in query:
        queries.extend(
            [
                "졸업 요건 이수학점 전공학점",
                "졸업 자격 및 졸업 기준",
            ]
        )

    # 학사경고 관련
    if "학사경고" in query:
        queries.extend(
            [
                "학사경고 기준",
                "연속 학사경고 불이익 수강신청 등록 제한",
            ]
        )

    # 중복 제거
    queries = list(dict.fromkeys(queries))

    # 실제로 어떤 검색어로 확장됐는지 확인
    print(f"[EXPANDED QUERIES] count={len(queries)}")

    return queries


def _rrf_combine(
    rankings: list[list[Any]],
    k: int = RRF_K,
) -> list[Any]:
    """
    여러 랭킹 목록을 Reciprocal Rank Fusion으로 병합한다.

    각 목록에서 순위 r인 항목에
    1 / (k + r)
    점수를 부여한다.
    """

    scores: dict[Any, float] = {}

    for ranking in rankings:
        for rank, item_id in enumerate(
            ranking,
            start=1,
        ):
            scores[item_id] = (
                scores.get(item_id, 0.0)
                + 1.0 / (k + rank)
            )

    return sorted(
        scores,
        key=lambda item_id: scores[item_id],
        reverse=True,
    )


def _hybrid_candidates(
    vector_sql: str,
    keyword_sql: str,
    vector_params: tuple,
    keyword_params: tuple,
) -> tuple[list[Any], dict[Any, tuple]]:
    """
    벡터 검색과 키워드 검색을 각각 수행하고
    RRF로 병합한다.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                vector_sql,
                vector_params,
            )
            vector_rows = cur.fetchall()

            cur.execute(
                keyword_sql,
                keyword_params,
            )
            keyword_rows = cur.fetchall()

    rows_by_id: dict[Any, tuple] = {}

    for row in vector_rows + keyword_rows:
        rows_by_id[row[0]] = row

    fused_ids = _rrf_combine(
        [
            [row[0] for row in vector_rows],
            [row[0] for row in keyword_rows],
        ]
    )

    return (
        fused_ids[:CANDIDATE_K],
        rows_by_id,
    )


def _rerank_and_filter(
    query: str,
    candidate_ids: list[Any],
    rows_by_id: dict[Any, tuple],
    content_index: int,
    top_k: int,
    min_score: float,
) -> list[tuple[tuple, float]]:
    """
    Cross-Encoder를 이용해 후보를 재정렬하고
    관련도 기준을 통과한 결과만 반환한다.
    """

    if not candidate_ids:
        return []

    # torch / transformers 로딩 비용이 크기 때문에
    # 실제 검색 시점에 import
    from app.reranker import rerank

    passages = [
        rows_by_id[candidate_id][content_index]
        for candidate_id in candidate_ids
    ]

    scores = rerank(
        query,
        passages,
    )

    scored_rows = sorted(
        zip(
            (
                rows_by_id[candidate_id]
                for candidate_id in candidate_ids
            ),
            scores,
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )

    # reranker 점수 확인용
    print("[RERANK RESULTS]")
    for row, score in scored_rows:
        print(f"[RERANK] score={float(score):.4f}")
   
    return [
        (row, float(score))
        for row, score in scored_rows[:top_k]
        if float(score) >= min_score
    ]


def _search_regulation_candidates(
    query: str,
) -> tuple[list[Any], dict[Any, tuple]]:
    """
    검색어 하나에 대해

    Vector Search
        +
    Keyword Search
        ↓
    RRF

    를 수행한다.
    """

    query_vector = to_vector_literal(
        embed_query(query)
    )

    vector_sql = (
        "SELECT id, content, source, page, chunk_index "
        "FROM regulation_chunks "
        "ORDER BY embedding <=> %s::vector "
        "LIMIT %s"
    )

    keyword_sql = (
        "SELECT id, content, source, page, chunk_index "
        "FROM regulation_chunks "
        "WHERE to_tsvector('simple', content) "
        "@@ plainto_tsquery('simple', %s) "
        "ORDER BY ts_rank_cd("
        "to_tsvector('simple', content), "
        "plainto_tsquery('simple', %s)"
        ") DESC "
        "LIMIT %s"
    )

    return _hybrid_candidates(
        vector_sql=vector_sql,
        keyword_sql=keyword_sql,
        vector_params=(
            query_vector,
            CANDIDATE_K,
        ),
        keyword_params=(
            query,
            query,
            CANDIDATE_K,
        ),
    )


def search_regulations(
    query: str,
    top_k: int = TOP_K,
    min_score: float = MIN_RELEVANCE_SCORE,
) -> list[RegulationSearchResult]:
    """
    규정 검색.

    복합 질문
        ↓
    검색어 확장
        ↓
    각각 Vector + Keyword 검색
        ↓
    각각 RRF
        ↓
    전체 후보 다시 RRF
        ↓
    원래 질문 기준 Cross-Encoder
        ↓
    최종 TOP_K
    """

    query = query.strip()

    if not query:
        raise ValueError(
            "질문이 비어 있습니다."
        )

    if top_k <= 0:
        raise ValueError(
            "top_k는 0보다 커야 합니다."
        )

    # ----------------------------------------
    # 1. 복합 질문 확장
    # ----------------------------------------

    expanded_queries = (
        expand_regulation_queries(query)
    )

    rankings: list[list[Any]] = []
    all_rows_by_id: dict[Any, tuple] = {}

    # ----------------------------------------
    # 2. 각각의 검색어로 후보 검색
    # ----------------------------------------

    for search_query in expanded_queries:

        candidate_ids, rows_by_id = (
            _search_regulation_candidates(
                search_query
            )
        )

        if candidate_ids:
            rankings.append(
                candidate_ids
            )

        all_rows_by_id.update(
            rows_by_id
        )

    if not rankings:
        return []

    # ----------------------------------------
    # 3. 여러 검색 결과를 다시 RRF로 병합
    # ----------------------------------------

    fused_ids = _rrf_combine(
        rankings
    )

    candidate_ids = (
        fused_ids[:CANDIDATE_K]
    )

    # ----------------------------------------
    # 4. 원래 질문으로 Cross-Encoder 재평가
    # ----------------------------------------

    scored_rows = _rerank_and_filter(
        query=query,
        candidate_ids=candidate_ids,
        rows_by_id=all_rows_by_id,
        content_index=1,
        top_k=top_k,
        min_score=min_score,
    )

    # ----------------------------------------
    # 5. 최종 결과 생성
    # ----------------------------------------

    return [
        RegulationSearchResult(
            document=str(row[1]),
            source=str(row[2]),
            page=int(row[3]),
            chunk_index=int(row[4]),
            score=score,
        )
        for row, score in scored_rows
    ]


def search_faqs(
    query: str,
    category_ids: list[int] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    top_k: int = TOP_K,
    min_score: float = MIN_RELEVANCE_SCORE,
) -> list[FaqSearchResult]:
    """
    FAQ 검색.

    FAQ 검색은 기존 구조를 그대로 유지한다.
    """

    query = query.strip()

    if not query:
        raise ValueError(
            "질문이 비어 있습니다."
        )

    if top_k <= 0:
        raise ValueError(
            "top_k는 0보다 커야 합니다."
        )

    filter_sql = ""
    filter_params: list[Any] = []

    if category_ids:
        filter_sql += (
            " AND category_ids && %s::bigint[]"
        )
        filter_params.append(
            category_ids
        )

    if date_from is not None:
        filter_sql += (
            " AND faq_created_at >= %s"
        )
        filter_params.append(
            date_from
        )

    if date_to is not None:
        filter_sql += (
            " AND faq_created_at <= %s"
        )
        filter_params.append(
            date_to
        )

    query_vector = to_vector_literal(
        embed_query(query)
    )

    vector_sql = (
        "SELECT id, content, faq_id, chunk_index "
        "FROM faq_chunks "
        "WHERE TRUE"
        f"{filter_sql} "
        "ORDER BY embedding <=> %s::vector "
        "LIMIT %s"
    )

    keyword_sql = (
        "SELECT id, content, faq_id, chunk_index "
        "FROM faq_chunks "
        "WHERE to_tsvector('simple', content) "
        "@@ plainto_tsquery('simple', %s)"
        f"{filter_sql} "
        "ORDER BY ts_rank_cd("
        "to_tsvector('simple', content), "
        "plainto_tsquery('simple', %s)"
        ") DESC "
        "LIMIT %s"
    )

    candidate_ids, rows_by_id = (
        _hybrid_candidates(
            vector_sql=vector_sql,
            keyword_sql=keyword_sql,
            vector_params=(
                *filter_params,
                query_vector,
                CANDIDATE_K,
            ),
            keyword_params=(
                query,
                *filter_params,
                query,
                CANDIDATE_K,
            ),
        )
    )

    scored_rows = _rerank_and_filter(
        query=query,
        candidate_ids=candidate_ids,
        rows_by_id=rows_by_id,
        content_index=1,
        top_k=top_k,
        min_score=min_score,
    )

    return [
        FaqSearchResult(
            document=str(row[1]),
            faq_id=int(row[2]),
            chunk_index=int(row[3]),
            score=score,
        )
        for row, score in scored_rows
    ]


def print_search_results(
    results: list[RegulationSearchResult],
) -> None:
    """
    터미널에서 검색 결과 확인
    """

    if not results:
        print(
            "\n관련 규정을 찾을 수 없습니다."
        )
        return

    print(
        f"\n검색 결과 {len(results)}개를 찾았습니다."
    )

    for index, result in enumerate(
        results,
        start=1,
    ):
        print(
            "\n" + "=" * 70
        )
        print(
            f"[검색 결과 {index}]"
        )
        print(
            f"출처: {result.source}"
        )
        print(
            f"페이지: {result.page}"
        )
        print(
            f"청크 번호: {result.chunk_index}"
        )
        print(
            f"관련도 점수: {result.score:.4f}"
        )
        print(
            "-" * 70
        )
        print(
            result.document
        )


def main() -> None:
    """
    rag.py 단독 테스트
    """

    print("=" * 60)
    print("규정 검색 테스트")
    print("=" * 60)

    query = input(
        "질문을 입력하세요: "
    ).strip()

    try:
        results = search_regulations(
            query
        )

    except Exception as exc:
        print(
            f"\n오류: {exc}"
        )
        return

    print_search_results(
        results
    )


if __name__ == "__main__":
    main()

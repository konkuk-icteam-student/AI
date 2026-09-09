from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import (
    EMBEDDING_MODEL,
    LLM_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_REQUEST_TIMEOUT,
    ensure_directories,
    validate_config,
)
from app.db import check_db, init_schema
from app.faqs import delete_faq, upsert_faq
from app.llm import answer_question
from app.rag import search_faqs


class ChatQueryRequest(BaseModel):
    """
    POST /api/v1/chat/query 요청 형식
    """

    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="규정과 FAQ에 대해 질문할 내용",
        examples=[
            "정보보호위원회는 어떤 사항을 심의하나요?"
        ],
    )


class RegulationSourceResponse(BaseModel):
    """
    답변 근거로 사용된 규정 출처
    """

    source: str
    page: int
    chunk_index: int
    score: float


class FaqSourceResponse(BaseModel):
    """
    답변 근거로 사용된 FAQ 출처
    """

    faq_id: int
    chunk_index: int
    score: float


class ChatQueryResponse(BaseModel):
    """
    POST /api/v1/chat/query 응답 형식
    """

    answer: str
    regulation_sources: list[RegulationSourceResponse]
    faq_sources: list[FaqSourceResponse]
    conflict_detected: bool


class FaqUpsertRequest(BaseModel):
    """
    POST /api/v1/faqs 요청 형식

    CommuteMate(Spring)가 FAQ 생성/수정 시
    질문+답변 결합 텍스트(HTML 제거 완료)를 전송한다.
    """

    faq_id: int = Field(
        ...,
        description="CommuteMate faq.id",
    )
    text: str = Field(
        ...,
        min_length=1,
        description="질문+답변 결합 텍스트 (HTML 제거 완료)",
    )
    title: str = Field(
        ...,
        min_length=1,
        description="FAQ 제목",
    )
    category_ids: list[int] = Field(
        default_factory=list,
        description="FAQ 카테고리 id 목록",
    )
    category_names: list[str] = Field(
        default_factory=list,
        description="FAQ 카테고리 이름 목록 (청크 헤더용)",
    )
    created_at: date | None = Field(
        default=None,
        description="FAQ 작성일",
    )


class FaqUpsertResponse(BaseModel):
    """
    POST /api/v1/faqs 응답 형식
    """

    faq_id: int
    chunk_count: int


class FaqDeleteResponse(BaseModel):
    """
    DELETE /api/v1/faqs/{faq_id} 응답 형식
    """

    faq_id: int
    deleted_chunks: int


class FaqSearchItem(BaseModel):
    """
    FAQ 검색 결과 한 건 (faq_id 기준으로 중복 제거됨)
    """

    faq_id: int
    score: float


class FaqSearchResponse(BaseModel):
    """
    GET /api/v1/faqs/search 응답 형식

    검색 결과 목록만 반환하며 LLM은 호출하지 않는다.
    """

    results: list[FaqSearchItem]


class CategoryRecommendRequest(BaseModel):
    """
    POST /api/v1/category/recommend 요청 형식

    프롬프트 구성과 응답 파싱은 CommuteMate(Spring)가 담당하고,
    이 엔드포인트는 LLM 호출만 대신하는 프록시다.
    """

    prompt: str = Field(
        ...,
        min_length=1,
        description="Spring이 구성한 카테고리 추천 프롬프트",
    )


class CategoryRecommendResponse(BaseModel):
    """
    POST /api/v1/category/recommend 응답 형식
    """

    response: str


class HealthResponse(BaseModel):
    """
    GET /health 응답 형식
    """

    status: str
    regulation_chunks: int
    faq_chunks: int
    embedding_model: str
    llm_model: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 서버 시작 시 설정과 DB 스키마를 검사한다.
    """
    validate_config()
    ensure_directories()

    try:
        init_schema()
        counts = check_db()

        print("=" * 60)
        print("규정+FAQ RAG API 서버 시작")
        print(
            f"규정 청크: "
            f"{counts['regulation_chunks']}"
        )
        print(f"FAQ 청크: {counts['faq_chunks']}")
        print(f"임베딩 모델: {EMBEDDING_MODEL}")
        print(f"LLM 모델: {LLM_MODEL}")
        print("=" * 60)

    except Exception as exc:
        print(f"[시작 경고] {exc}")

    yield

    print("규정+FAQ RAG API 서버 종료")


app = FastAPI(
    title="업무일지(CommuteMate) 규정+FAQ RAG API",
    description=(
        "규정과 FAQ를 하이브리드 검색하고 "
        "챗봇 답변을 생성하는 API"
    ),
    version="2.0.0",
    lifespan=lifespan,
)


# 개발 단계 CORS 설정
# React 또는 Spring Boot에서 직접 호출할 때 필요하다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    """
    기본 주소 확인용
    """
    return {
        "message": "업무일지(CommuteMate) 규정+FAQ RAG API",
        "docs": "/docs",
        "health": "/health",
    }


@app.get(
    "/health",
    response_model=HealthResponse,
)
def health_check() -> HealthResponse:
    """
    서버와 DB 상태 확인
    """
    try:
        counts = check_db()

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    return HealthResponse(
        status="ok",
        regulation_chunks=counts["regulation_chunks"],
        faq_chunks=counts["faq_chunks"],
        embedding_model=EMBEDDING_MODEL,
        llm_model=LLM_MODEL,
    )


def _run_chat_query(query: str) -> ChatQueryResponse:
    """
    챗봇 질의 공통 처리
    """
    question = query.strip()

    if not question:
        raise HTTPException(
            status_code=400,
            detail="질문이 비어 있습니다.",
        )

    try:
        result: dict[str, Any] = answer_question(question)

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

    regulation_sources = [
        RegulationSourceResponse(
            source=item.source,
            page=item.page,
            chunk_index=item.chunk_index,
            score=item.score,
        )
        for item in result["regulation_results"]
    ]

    faq_sources = [
        FaqSourceResponse(
            faq_id=item.faq_id,
            chunk_index=item.chunk_index,
            score=item.score,
        )
        for item in result["faq_results"]
    ]

    return ChatQueryResponse(
        answer=str(result["answer"]),
        regulation_sources=regulation_sources,
        faq_sources=faq_sources,
        conflict_detected=bool(
            result["conflict_detected"]
        ),
    )


@app.post(
    "/api/v1/chat/query",
    response_model=ChatQueryResponse,
)
def chat_query(
    request: ChatQueryRequest,
) -> ChatQueryResponse:
    """
    규정+FAQ 근거 기반 챗봇 답변을 생성한다.

    규정과 FAQ 둘 다 근거가 없을 때만 LLM을 호출하지 않고
    안전 메시지를 반환한다.
    """
    return _run_chat_query(request.query)


@app.post(
    "/api/v1/faqs",
    response_model=FaqUpsertResponse,
)
def faq_upsert(
    request: FaqUpsertRequest,
) -> FaqUpsertResponse:
    """
    FAQ 청킹·임베딩·저장 (생성/수정 공용 upsert)
    """
    try:
        chunk_count = upsert_faq(
            faq_id=request.faq_id,
            text=request.text,
            title=request.title,
            category_ids=request.category_ids,
            category_names=request.category_names,
            created_at=request.created_at,
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    return FaqUpsertResponse(
        faq_id=request.faq_id,
        chunk_count=chunk_count,
    )


@app.delete(
    "/api/v1/faqs/{faq_id}",
    response_model=FaqDeleteResponse,
)
def faq_delete(faq_id: int) -> FaqDeleteResponse:
    """
    FAQ 삭제 시 해당 청크를 모두 제거한다.
    """
    deleted_chunks = delete_faq(faq_id)

    return FaqDeleteResponse(
        faq_id=faq_id,
        deleted_chunks=deleted_chunks,
    )


@app.get(
    "/api/v1/faqs/search",
    response_model=FaqSearchResponse,
)
def faq_search(
    q: str = Query(
        ...,
        min_length=1,
        description="검색 키워드",
    ),
    category_ids: list[int] | None = Query(
        default=None,
        description="카테고리 id 필터",
    ),
    date_from: date | None = Query(
        default=None,
        description="작성일 시작 필터",
    ),
    date_to: date | None = Query(
        default=None,
        description="작성일 끝 필터",
    ),
    top_k: int = Query(
        default=50,
        ge=1,
        le=200,
        description="반환할 최대 FAQ 수",
    ),
) -> FaqSearchResponse:
    """
    FAQ 하이브리드 검색 (LLM 미호출)

    청크 단위 검색 결과를 faq_id 기준으로 중복 제거하여
    관련도 순 faq_id 목록을 반환한다.
    """
    try:
        chunk_results = search_faqs(
            q,
            category_ids=category_ids,
            date_from=date_from,
            date_to=date_to,
            top_k=top_k,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    best_by_faq: dict[int, float] = {}

    for chunk in chunk_results:
        current_best = best_by_faq.get(chunk.faq_id)

        if (
            current_best is None
            or chunk.score > current_best
        ):
            best_by_faq[chunk.faq_id] = chunk.score

    results = [
        FaqSearchItem(faq_id=faq_id, score=score)
        for faq_id, score in sorted(
            best_by_faq.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]

    return FaqSearchResponse(results=results)


@app.post(
    "/api/v1/category/recommend",
    response_model=CategoryRecommendResponse,
)
def category_recommend(
    request: CategoryRecommendRequest,
) -> CategoryRecommendResponse:
    """
    카테고리 추천용 LLM 프록시

    Spring이 구성한 프롬프트를 그대로 LLM에 전달하고
    응답 텍스트를 반환한다.
    """
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": LLM_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": request.prompt,
                    }
                ],
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.2,
                },
            },
            timeout=OLLAMA_REQUEST_TIMEOUT,
        )

        response.raise_for_status()

    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"LLM 호출에 실패했습니다: {exc}",
        ) from exc

    data = response.json()
    content = (
        data.get("message", {}).get("content")
    )

    if not isinstance(content, str) or not content.strip():
        raise HTTPException(
            status_code=502,
            detail=f"Ollama 응답 형식이 올바르지 않습니다: {data}",
        )

    return CategoryRecommendResponse(
        response=content.strip()
    )

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.category.router import router as category_router
from app.chat.router import router as chat_router
from app.core.config import (
    EMBEDDING_MODEL,
    LLM_MODEL,
    ensure_directories,
    validate_config,
)
from app.infrastructure.database import check_db, init_schema
from app.faqs.router import router as faqs_router


class HealthResponse(BaseModel):
    status: str
    regulation_chunks: int
    faq_chunks: int
    embedding_model: str
    llm_model: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_config()
    ensure_directories()
    try:
        init_schema()
        counts = check_db()
        print("=" * 60)
        print("규정+FAQ RAG API 서버 시작")
        print(f"규정 청크: {counts['regulation_chunks']}")
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
    description="규정과 FAQ를 하이브리드 검색하고 챗봇 답변을 생성하는 API",
    version="2.0.0",
    lifespan=lifespan,
)

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

app.include_router(chat_router)
app.include_router(faqs_router)
app.include_router(category_router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": "업무일지(CommuteMate) 규정+FAQ RAG API",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    try:
        counts = check_db()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return HealthResponse(
        status="ok",
        regulation_chunks=counts["regulation_chunks"],
        faq_chunks=counts["faq_chunks"],
        embedding_model=EMBEDDING_MODEL,
        llm_model=LLM_MODEL,
    )

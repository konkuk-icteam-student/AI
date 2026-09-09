from __future__ import annotations

import os
from pathlib import Path


# 프로젝트 최상위 경로
BASE_DIR = Path(__file__).resolve().parent.parent

# 문서와 데이터 저장 경로
DOCUMENTS_DIR = BASE_DIR / "documents"
TEXT_PDF_DIR = DOCUMENTS_DIR / "text_pdf"
IMAGE_PDF_DIR = DOCUMENTS_DIR / "image_pdf"
DATA_DIR = BASE_DIR / "data"

# Postgres(pgvector) 연결 문자열
# CommuteMate가 운영 중인 pgvector/pgvector:pg17 인스턴스를 공유한다.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/postgres",
)

# Ollama 서버 주소
OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://localhost:11434",
)

# Ollama 모델
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "qwen3-embedding:0.6b",
)

LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "qwen3:8b",
)

# 임베딩 벡터 차원 (qwen3-embedding:0.6b 기준)
EMBEDDING_DIM = int(
    os.getenv("EMBEDDING_DIM", "1024")
)

# 재정렬(Cross-Encoder) 모델
RERANKER_MODEL = os.getenv(
    "RERANKER_MODEL",
    "BAAI/bge-reranker-v2-m3",
)

# 규정 PDF 청크 설정
CHUNK_SIZE = int(
    os.getenv("CHUNK_SIZE", "900")
)

CHUNK_OVERLAP = int(
    os.getenv("CHUNK_OVERLAP", "150")
)

# FAQ 청크 설정 (질문+답변 결합 텍스트 기준)
FAQ_CHUNK_SIZE = int(
    os.getenv("FAQ_CHUNK_SIZE", "500")
)

FAQ_CHUNK_OVERLAP = int(
    os.getenv("FAQ_CHUNK_OVERLAP", "80")
)

# 임베딩 요청 배치 크기
EMBED_BATCH_SIZE = int(
    os.getenv("EMBED_BATCH_SIZE", "16")
)

# DB 저장 배치 크기
INSERT_BATCH_SIZE = int(
    os.getenv("INSERT_BATCH_SIZE", "100")
)

# RAG 검색 설정
# 벡터/키워드 검색 각각에서 뽑는 후보 수
CANDIDATE_K = int(
    os.getenv("CANDIDATE_K", "25")
)

# Reciprocal Rank Fusion 상수
RRF_K = int(
    os.getenv("RRF_K", "60")
)

# 재정렬 후 최종 반환 개수
TOP_K = int(
    os.getenv("TOP_K", "5")
)

# 재정렬 점수(raw logit) 임계값
# bge-reranker-v2-m3 기준 양수면 관련, 크게 음수면 무관하다.
# 이 값 미만의 후보는 근거로 사용하지 않는다.
MIN_RELEVANCE_SCORE = float(
    os.getenv("MIN_RELEVANCE_SCORE", "-1.0")
)

# HTTP 요청 제한 시간
OLLAMA_CONNECT_TIMEOUT = int(
    os.getenv("OLLAMA_CONNECT_TIMEOUT", "10")
)

OLLAMA_REQUEST_TIMEOUT = int(
    os.getenv("OLLAMA_REQUEST_TIMEOUT", "300")
)


def validate_config() -> None:
    """
    설정값이 올바른지 검사한다.
    ingest, rag, api 실행 전에 호출할 수 있다.
    """
    if CHUNK_SIZE <= 0:
        raise ValueError("CHUNK_SIZE는 0보다 커야 합니다.")

    if CHUNK_OVERLAP < 0:
        raise ValueError("CHUNK_OVERLAP은 0 이상이어야 합니다.")

    if CHUNK_OVERLAP >= CHUNK_SIZE:
        raise ValueError(
            "CHUNK_OVERLAP은 CHUNK_SIZE보다 작아야 합니다."
        )

    if FAQ_CHUNK_SIZE <= 0:
        raise ValueError(
            "FAQ_CHUNK_SIZE는 0보다 커야 합니다."
        )

    if FAQ_CHUNK_OVERLAP < 0:
        raise ValueError(
            "FAQ_CHUNK_OVERLAP은 0 이상이어야 합니다."
        )

    if FAQ_CHUNK_OVERLAP >= FAQ_CHUNK_SIZE:
        raise ValueError(
            "FAQ_CHUNK_OVERLAP은 FAQ_CHUNK_SIZE보다 작아야 합니다."
        )

    if EMBED_BATCH_SIZE <= 0:
        raise ValueError(
            "EMBED_BATCH_SIZE는 0보다 커야 합니다."
        )

    if INSERT_BATCH_SIZE <= 0:
        raise ValueError(
            "INSERT_BATCH_SIZE는 0보다 커야 합니다."
        )

    if EMBEDDING_DIM <= 0:
        raise ValueError(
            "EMBEDDING_DIM은 0보다 커야 합니다."
        )

    if CANDIDATE_K <= 0:
        raise ValueError("CANDIDATE_K는 0보다 커야 합니다.")

    if RRF_K <= 0:
        raise ValueError("RRF_K는 0보다 커야 합니다.")

    if TOP_K <= 0:
        raise ValueError("TOP_K는 0보다 커야 합니다.")

    if TOP_K > CANDIDATE_K:
        raise ValueError(
            "TOP_K는 CANDIDATE_K 이하로 설정하세요."
        )


def ensure_directories() -> None:
    """
    프로젝트에서 필요한 디렉터리를 생성한다.
    """
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    TEXT_PDF_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_PDF_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


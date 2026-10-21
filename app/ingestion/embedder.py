from __future__ import annotations

import requests

from app.core.config import (
    EMBEDDING_MODEL,
    OLLAMA_CONNECT_TIMEOUT,
    OLLAMA_BASE_URL,
    OLLAMA_REQUEST_TIMEOUT,
)


def check_ollama() -> None:
    """임베딩에 필요한 Ollama 서버와 모델을 확인한다."""
    try:
        response = requests.get(
            f"{OLLAMA_BASE_URL}/api/tags",
            timeout=OLLAMA_CONNECT_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            "Ollama 서버에 연결할 수 없습니다.\n"
            "systemctl status ollama 또는 ollama serve로 상태를 확인하세요."
        ) from exc

    model_names = {
        model.get("name", "")
        for model in response.json().get("models", [])
        if isinstance(model, dict)
    }
    model_exists = any(
        name == EMBEDDING_MODEL or name.startswith(f"{EMBEDDING_MODEL}:")
        for name in model_names
    )
    if not model_exists:
        available = ", ".join(sorted(model_names)) or "없음"
        raise RuntimeError(
            f"임베딩 모델을 찾을 수 없습니다: {EMBEDDING_MODEL}\n"
            f"현재 설치 모델: {available}\n"
            f"설치 명령: ollama pull {EMBEDDING_MODEL}"
        )


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Ollama API로 여러 텍스트를 한 번에 임베딩한다."""
    if not texts:
        return []

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={"model": EMBEDDING_MODEL, "input": texts},
            timeout=OLLAMA_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        detail = f"\nOllama 응답: {exc.response.text}" if exc.response is not None else ""
        raise RuntimeError(f"임베딩 요청에 실패했습니다: {exc}{detail}") from exc

    data = response.json()
    embeddings = data.get("embeddings")
    if not isinstance(embeddings, list):
        raise RuntimeError(f"Ollama 응답에 embeddings가 없습니다: {data}")
    if len(embeddings) != len(texts):
        raise RuntimeError(
            "요청한 텍스트 수와 반환된 임베딩 수가 일치하지 않습니다.\n"
            f"요청 수: {len(texts)}\n반환 수: {len(embeddings)}"
        )
    return embeddings

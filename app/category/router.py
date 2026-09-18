import requests
from fastapi import APIRouter, HTTPException

from app.category.schemas import CategoryRecommendRequest, CategoryRecommendResponse
from app.core.config import LLM_MODEL, OLLAMA_BASE_URL, OLLAMA_REQUEST_TIMEOUT


router = APIRouter(tags=["category"])


@router.post("/api/v1/category/recommend", response_model=CategoryRecommendResponse)
def category_recommend(request: CategoryRecommendRequest) -> CategoryRecommendResponse:
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": request.prompt}],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.2},
            },
            timeout=OLLAMA_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail=f"LLM 호출에 실패했습니다: {exc}") from exc

    data = response.json()
    content = data.get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(
            status_code=502,
            detail=f"Ollama 응답 형식이 올바르지 않습니다: {data}",
        )
    return CategoryRecommendResponse(response=content.strip())

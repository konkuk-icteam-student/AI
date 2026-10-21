from typing import Any

from fastapi import APIRouter, HTTPException

from app.chat.schemas import (
    ChatQueryRequest,
    ChatQueryResponse,
    FaqSourceResponse,
    RegulationSourceResponse,
)
from app.chat.service import answer_question


router = APIRouter(tags=["chat"])


def _run_chat_query(query: str) -> ChatQueryResponse:
    question = query.strip()
    if not question:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다.")

    try:
        result: dict[str, Any] = answer_question(question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ChatQueryResponse(
        answer=str(result["answer"]),
        regulation_sources=[
            RegulationSourceResponse(
                source=item.source,
                page=item.page,
                chunk_index=item.chunk_index,
                score=item.score,
            )
            for item in result["regulation_results"]
        ],
        faq_sources=[
            FaqSourceResponse(
                faq_id=item.faq_id,
                chunk_index=item.chunk_index,
                score=item.score,
            )
            for item in result["faq_results"]
        ],
        conflict_detected=bool(result["conflict_detected"]),
    )


@router.post("/api/v1/chat/query", response_model=ChatQueryResponse)
def chat_query(request: ChatQueryRequest) -> ChatQueryResponse:
    return _run_chat_query(request.query)

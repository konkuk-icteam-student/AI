from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.faqs.schemas import (
    FaqDeleteResponse,
    FaqSearchItem,
    FaqSearchResponse,
    FaqUpsertRequest,
    FaqUpsertResponse,
)
from app.faqs.service import delete_faq, upsert_faq
from app.search.service import search_faqs


router = APIRouter(tags=["faqs"])


@router.post("/api/v1/faqs", response_model=FaqUpsertResponse)
def faq_upsert(request: FaqUpsertRequest) -> FaqUpsertResponse:
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
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FaqUpsertResponse(faq_id=request.faq_id, chunk_count=chunk_count)


@router.delete("/api/v1/faqs/{faq_id}", response_model=FaqDeleteResponse)
def faq_delete(faq_id: int) -> FaqDeleteResponse:
    return FaqDeleteResponse(faq_id=faq_id, deleted_chunks=delete_faq(faq_id))


@router.get("/api/v1/faqs/search", response_model=FaqSearchResponse)
def faq_search(
    q: str = Query(..., min_length=1),
    category_ids: list[int] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    top_k: int = Query(default=50, ge=1, le=200),
) -> FaqSearchResponse:
    try:
        chunks = search_faqs(
            q,
            category_ids=category_ids,
            date_from=date_from,
            date_to=date_to,
            top_k=top_k,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    best_by_faq: dict[int, float] = {}
    for chunk in chunks:
        current = best_by_faq.get(chunk.faq_id)
        if current is None or chunk.score > current:
            best_by_faq[chunk.faq_id] = chunk.score

    return FaqSearchResponse(
        results=[
            FaqSearchItem(faq_id=faq_id, score=score)
            for faq_id, score in sorted(
                best_by_faq.items(), key=lambda item: item[1], reverse=True
            )
        ]
    )

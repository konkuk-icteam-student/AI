from datetime import date

from pydantic import BaseModel, Field


class FaqUpsertRequest(BaseModel):
    faq_id: int = Field(..., description="CommuteMate faq.id")
    text: str = Field(..., min_length=1, description="질문+답변 결합 텍스트")
    title: str = Field(..., min_length=1, description="FAQ 제목")
    category_ids: list[int] = Field(default_factory=list)
    category_names: list[str] = Field(default_factory=list)
    created_at: date | None = None


class FaqUpsertResponse(BaseModel):
    faq_id: int
    chunk_count: int


class FaqDeleteResponse(BaseModel):
    faq_id: int
    deleted_chunks: int


class FaqSearchItem(BaseModel):
    faq_id: int
    score: float


class FaqSearchResponse(BaseModel):
    results: list[FaqSearchItem]

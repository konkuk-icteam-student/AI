from pydantic import BaseModel, Field


class ChatQueryRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="규정과 FAQ에 대해 질문할 내용",
        examples=["정보보호위원회는 어떤 사항을 심의하나요?"],
    )


class RegulationSourceResponse(BaseModel):
    source: str
    page: int
    chunk_index: int
    score: float


class FaqSourceResponse(BaseModel):
    faq_id: int
    chunk_index: int
    score: float


class ChatQueryResponse(BaseModel):
    answer: str
    regulation_sources: list[RegulationSourceResponse]
    faq_sources: list[FaqSourceResponse]
    conflict_detected: bool

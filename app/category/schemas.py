from pydantic import BaseModel, Field


class CategoryRecommendRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=1,
        description="Spring이 구성한 카테고리 추천 프롬프트",
    )


class CategoryRecommendResponse(BaseModel):
    response: str

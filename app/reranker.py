from __future__ import annotations

from functools import lru_cache

import torch
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
)

from app.config import RERANKER_MODEL


@lru_cache(maxsize=1)
def _get_reranker():
    """
    Cross-Encoder 재정렬 모델과 토크나이저를 로드한다.

    최초 호출 시 한 번만 로드하고 이후에는 캐시를 사용한다.

    low_cpu_mem_usage=False로 meta 디바이스 지연 로딩을 끄고
    가중치를 즉시 실체화한다 — 켜져 있으면 환경에 따라
    "Tensor on device cpu is not on the expected device meta!"
    에러가 발생한다.
    """
    device = (
        "cuda"
        if torch.cuda.is_available()
        else "cpu"
    )

    tokenizer = AutoTokenizer.from_pretrained(
        RERANKER_MODEL
    )

    model = AutoModelForSequenceClassification.from_pretrained(
        RERANKER_MODEL,
        low_cpu_mem_usage=False,
    )

    model.to(device)
    model.eval()

    return tokenizer, model, device


def rerank(
    query: str,
    passages: list[str],
) -> list[float]:
    """
    질문과 각 후보 문서의 관련도를 Cross-Encoder로 계산한다.

    반환값은 raw logit 점수 목록이다 (확률 아님).
    대체로 양수면 관련, 크게 음수면 무관하다.
    """
    if not passages:
        return []

    tokenizer, model, device = _get_reranker()

    inputs = tokenizer(
        [
            [query, passage]
            for passage in passages
        ],
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    ).to(device)

    with torch.no_grad():
        outputs = model(**inputs)

    scores = outputs.logits.view(-1).tolist()

    return [
        float(score)
        for score in scores
    ]

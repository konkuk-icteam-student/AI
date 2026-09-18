from __future__ import annotations

import re
from collections import OrderedDict

import requests

from app.chat.prompts import render_chat_prompt
from app.core.config import (
    LLM_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_REQUEST_TIMEOUT,
)
from app.search.service import (
    FaqSearchResult,
    RegulationSearchResult,
    search_faqs,
    search_regulations,
)


NO_RESULT_MESSAGE = "관련 내용을 찾을 수 없습니다."

FAQ_ONLY_NOTICE = (
    "공식 규정 근거는 확인되지 않았으며, "
    "과거 처리 사례(FAQ)를 기반으로 안내드립니다."
)

# 답변 마지막 줄의 충돌 마커를 찾는 정규식
# LLM 답변 문장을 키워드 매칭하는 방식은 표현이 매번 달라
# 신뢰할 수 없으므로, 고정된 마커 한 줄을 출력하게 하고 파싱한다.
CONFLICT_MARKER_RE = re.compile(
    r"\n?\s*CONFLICT:\s*(yes|no)\s*$",
    re.IGNORECASE,
)


def build_regulation_context(
    results: list[RegulationSearchResult],
) -> str:
    """
    규정 검색 결과를 LLM에 전달할 문맥으로 변환한다.
    """
    if not results:
        return "(관련 규정 자료 없음)"

    context_parts: list[str] = []

    for index, result in enumerate(results, start=1):
        context_parts.append(
            "\n".join(
                [
                    f"[규정 자료 {index}]",
                    f"문서명: {result.source}",
                    f"페이지: {result.page}",
                    f"청크 번호: {result.chunk_index}",
                    f"내용:",
                    result.document,
                ]
            )
        )

    return "\n\n".join(context_parts)


def build_faq_context(
    results: list[FaqSearchResult],
) -> str:
    """
    FAQ 검색 결과를 LLM에 전달할 문맥으로 변환한다.
    """
    if not results:
        return "(관련 FAQ 자료 없음)"

    context_parts: list[str] = []

    for index, result in enumerate(results, start=1):
        context_parts.append(
            "\n".join(
                [
                    f"[FAQ 자료 {index}]",
                    f"FAQ 번호: {result.faq_id}",
                    f"내용:",
                    result.document,
                ]
            )
        )

    return "\n\n".join(context_parts)


def build_prompt(
    question: str,
    regulation_results: list[RegulationSearchResult],
    faq_results: list[FaqSearchResult],
) -> str:
    regulation_context = build_regulation_context(
        regulation_results
    )
    faq_context = build_faq_context(faq_results)

    return render_chat_prompt(
        question=question,
        regulation_context=regulation_context,
        faq_context=faq_context,
        faq_only_notice=FAQ_ONLY_NOTICE,
    )


def call_ollama(prompt: str) -> str:
    """
    Ollama의 qwen3:8b 모델을 호출한다.
    """
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": LLM_MODEL,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {
                 "temperature": 0.2,
                 "top_p": 0.8,
                 "num_predict": 700,
                 "repeat_penalty": 1.1,
	          },
            },
            timeout=OLLAMA_REQUEST_TIMEOUT,
        )

        response.raise_for_status()

    except requests.RequestException as exc:
        detail = ""

        if exc.response is not None:
            detail = f"\nOllama 응답: {exc.response.text}"

        raise RuntimeError(
            f"LLM 호출에 실패했습니다: {exc}{detail}"
        ) from exc

    data = response.json()
    answer = data.get("response")

    if not isinstance(answer, str):
        raise RuntimeError(
            f"Ollama 응답 형식이 올바르지 않습니다: {data}"
        )

    answer = answer.strip()

    if not answer:
        raise RuntimeError(
            "Ollama가 비어 있는 답변을 반환했습니다."
        )

    return answer


def extract_conflict_marker(
    raw_answer: str,
) -> tuple[str, bool]:
    """
    답변 마지막 줄의 CONFLICT 마커를 파싱하고 제거한다.

    마커가 없으면 충돌 없음(False)으로 간주한다.
    사용자에게 보여주는 답변에는 마커가 포함되지 않는다.
    """
    match = CONFLICT_MARKER_RE.search(raw_answer)

    if match is None:
        return raw_answer.strip(), False

    conflict_detected = (
        match.group(1).lower() == "yes"
    )

    cleaned_answer = CONFLICT_MARKER_RE.sub(
        "",
        raw_answer,
    ).strip()

    return cleaned_answer, conflict_detected


def make_source_text(
    results: list[RegulationSearchResult],
) -> str:
    """
    검색 결과를 기준으로 출처 목록을 만든다.
    """
    unique_sources: OrderedDict[
        tuple[str, int],
        None,
    ] = OrderedDict()

    for result in results:
        key = (
            result.source,
            result.page,
        )
        unique_sources[key] = None

    source_lines = [
        f"- {source}, {page}페이지"
        for source, page in unique_sources
    ]

    return "\n".join(source_lines)


def answer_question(
    question: str,
) -> dict[str, object]:
    """
    질문 검색부터 최종 답변 생성까지 수행한다.

    규정과 FAQ를 각각 독립적으로 검색하고
    (병합 랭킹 없음), 두 소스 모두 근거가 없을 때만
    LLM을 호출하지 않고 안전 메시지를 반환한다.
    """
    cleaned_question = question.strip()

    if not cleaned_question:
        raise ValueError("질문이 비어 있습니다.")

    regulation_results = search_regulations(
        cleaned_question
    )
    faq_results = search_faqs(cleaned_question)

    if not regulation_results and not faq_results:
        return {
            "answer": NO_RESULT_MESSAGE,
            "regulation_results": [],
            "faq_results": [],
            "conflict_detected": False,
        }

    prompt = build_prompt(
        question=cleaned_question,
        regulation_results=regulation_results,
        faq_results=faq_results,
    )

    raw_answer = call_ollama(prompt)

    answer, conflict_detected = extract_conflict_marker(
        raw_answer
    )

    return {
        "answer": answer,
        "regulation_results": regulation_results,
        "faq_results": faq_results,
        "conflict_detected": conflict_detected,
    }


def main() -> None:
    print("=" * 60)
    print("규정+FAQ 질의응답 테스트")
    print("=" * 60)

    question = input("질문을 입력하세요: ").strip()
    try:
        response = answer_question(question)

    except Exception as exc:
        print(f"\n오류: {exc}")
        return

    print("\n" + "=" * 60)
    print("답변")
    print("=" * 60)
    print(response["answer"])

    if response["conflict_detected"]:
        print("\n[주의] 규정과 FAQ 사례 간 충돌이 감지되었습니다.")

    regulation_results = response["regulation_results"]

    if (
        isinstance(regulation_results, list)
        and regulation_results
    ):
        print("\n검색된 출처")
        print(make_source_text(regulation_results))


if __name__ == "__main__":
    main()

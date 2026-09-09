from __future__ import annotations

import re
from collections import OrderedDict

import requests

from app.config import (
    LLM_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_REQUEST_TIMEOUT,
)
from app.rag import (
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

    return f"""
당신은 업무일지(CommuteMate) 규정과 FAQ를 안내하는 AI입니다.

사용자의 질문을 자연스럽게 이해하고, 반드시 아래 [규정 자료]와 [FAQ 자료]만을 근거로 답변하세요.

규칙:
1. 답변의 사실적 근거는 반드시 [규정 자료]와 [FAQ 자료]에서 가져오세요.
2. 사전학습 지식, 일반 상식, 외부 정보를 답변의 근거로 사용하지 마세요.
3. 규정 자료에 없는 사실, 조건, 제도 또는 예외를 새로 만들지 마세요.
4. 사용자의 표현과 규정의 표현이 다르더라도 문맥상 의미가 같으면 연결하여 이해하세요.

예시:
- 하는 일 → 역할, 기능, 업무
- 받을 수 있나요 → 지급 대상 또는 선발 대상에 해당하는지
- 학점 → 문맥에 따라 평점 또는 취득학점
- 성적 → 평점, 실점 또는 성적 기준
- 장학금을 받을 수 있나요 → 장학생 선발기준을 충족하는지

5. 단, 의미가 여러 가지로 해석될 수 있는 표현은 임의로 하나로 확정하지 마세요.
   예를 들어 "학점 1.8"은 일반적으로 평점을 의미할 가능성이 있지만,
   규정에서 취득학점과 평점을 구분하고 있으므로 답변에서는 "평점 1.8로 해석하면"이라고 명시하세요.

6. 규정에 숫자, 평점, 취득학점, 기간, 횟수 또는 자격 조건이 있고,
   사용자가 자신의 값을 제시하면 규정의 기준과 비교하여 충족 여부를 판단하세요.

예시:
- 규정 기준이 평점 2.0 이상이고 사용자의 평점이 1.8이면,
  일반적인 평점 기준을 충족하지 않는다고 판단할 수 있습니다.

7. 여러 규정 자료를 함께 확인해야 답할 수 있다면 관련 내용을 종합하세요.
8. 일반 원칙과 예외 규정이 함께 있으면 일반 원칙과 예외를 모두 설명하세요.
9. 사용자의 정보가 부족해 최종적인 수혜 여부를 확정할 수 없다면,
   현재 확인할 수 있는 조건과 추가로 필요한 조건을 구분하여 설명하세요.
10. 검색된 규정 중 일부만 질문과 관련 있다면 관련 있는 규정만 사용하세요.
11. 답변은 규정의 의미를 유지하면서 사용자가 이해하기 쉬운 자연스러운 한국어로 작성하세요.
12. 지나치게 단정하지 말고 다음 표현을 적절히 사용하세요.
   - 일반적인 기준으로는
   - 평점으로 해석하면
   - 해당 예외 조건에 해당하는 경우
   - 현재 제공된 정보만으로는 확정하기 어렵습니다
13. [규정 자료]와 [FAQ 자료] 모두에서 질문과 관련된 근거를 전혀 확인할 수 없는 경우에만 다음 문장을 출력하세요.

관련 내용을 찾을 수 없습니다.

14. 답변 뒤에는 실제 판단에 사용한 규정 문장을 원문 그대로 표시하세요.
15. 문서명과 페이지는 실제 사용한 규정 자료의 정보만 표시하세요.
16. [FAQ 자료]는 과거 실제 처리 사례로서 보조 근거입니다.
    규정과 FAQ의 내용이 서로 다르면 항상 현재 규정을 우선하고,
    충돌한다는 사실을 답변에 명시하세요.
17. [규정 자료]에 관련 근거가 없고 [FAQ 자료]에만 근거가 있으면,
    답변 서두에 다음 문장을 반드시 포함하세요.

{FAQ_ONLY_NOTICE}

18. 답변을 모두 마친 뒤 반드시 마지막 줄에 규정과 FAQ 사례 간
    충돌 여부를 다음 형식으로만 출력하세요. 다른 텍스트와 섞지 마세요.
    충돌이 있으면 CONFLICT: yes, 없으면 CONFLICT: no

[규정 자료]
{regulation_context}

[FAQ 자료]
{faq_context}

[질문]
{question}

다음 형식으로 답하세요.

답변:
<사용자가 이해하기 쉬운 자연스러운 답변>

근거 원문:
- <실제 답변에 사용한 규정 문장 원문>

참고 FAQ:
- <답변에 참고한 FAQ 사례 요약, 참고한 FAQ가 없으면 "없음">

CONFLICT: <yes 또는 no>

""".strip()


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

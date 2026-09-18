"""기존 실행·import 경로를 유지하는 채팅 호환 모듈."""

from app.chat.service import (
    FAQ_ONLY_NOTICE,
    NO_RESULT_MESSAGE,
    answer_question,
    build_faq_context,
    build_prompt,
    build_regulation_context,
    call_ollama,
    extract_conflict_marker,
    main,
    make_source_text,
)

__all__ = [
    "FAQ_ONLY_NOTICE",
    "NO_RESULT_MESSAGE",
    "answer_question",
    "build_faq_context",
    "build_prompt",
    "build_regulation_context",
    "call_ollama",
    "extract_conflict_marker",
    "make_source_text",
]


if __name__ == "__main__":
    main()

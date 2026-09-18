from __future__ import annotations

from pathlib import Path


DEFAULT_CHAT_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "prompts"
    / "chat_answer.txt"
)

REQUIRED_TOKENS = {
    "{{FAQ_ONLY_NOTICE}}",
    "{{REGULATION_CONTEXT}}",
    "{{FAQ_CONTEXT}}",
    "{{QUESTION}}",
}


def render_chat_prompt(
    *,
    question: str,
    regulation_context: str,
    faq_context: str,
    faq_only_notice: str,
    path: Path = DEFAULT_CHAT_PROMPT_PATH,
) -> str:
    """외부 템플릿을 읽어 채팅 프롬프트를 만든다."""
    try:
        template = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"채팅 프롬프트 파일이 없습니다: {path}") from exc

    missing = sorted(token for token in REQUIRED_TOKENS if token not in template)
    if missing:
        raise RuntimeError(
            "채팅 프롬프트에 필수 표시자가 없습니다: " + ", ".join(missing)
        )

    replacements = {
        "{{QUESTION}}": question,
        "{{REGULATION_CONTEXT}}": regulation_context,
        "{{FAQ_CONTEXT}}": faq_context,
        "{{FAQ_ONLY_NOTICE}}": faq_only_notice,
    }
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered.strip()

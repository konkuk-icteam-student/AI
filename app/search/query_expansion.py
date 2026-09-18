from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_RULES_PATH = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "search"
    / "query_expansions.json"
)


def load_query_expansion_rules(
    path: Path = DEFAULT_RULES_PATH,
) -> list[dict[str, Any]]:
    """비개발자가 관리하는 JSON 파일에서 검색 확장 규칙을 읽는다."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"검색 확장 규칙 파일이 없습니다: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"검색 확장 규칙 JSON 형식이 올바르지 않습니다: "
            f"{path} ({exc.msg}, {exc.lineno}번 줄)"
        ) from exc

    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise RuntimeError("검색 확장 파일에는 rules 목록이 필요합니다.")

    rules: list[dict[str, Any]] = []
    for index, rule in enumerate(data["rules"], start=1):
        if not isinstance(rule, dict):
            raise RuntimeError(f"검색 확장 {index}번 규칙이 객체가 아닙니다.")
        keywords = rule.get("keywords")
        expansions = rule.get("expansions")
        if (
            not isinstance(keywords, list)
            or not all(isinstance(value, str) and value.strip() for value in keywords)
            or not isinstance(expansions, list)
            or not all(isinstance(value, str) and value.strip() for value in expansions)
        ):
            raise RuntimeError(
                f"검색 확장 {index}번 규칙의 keywords와 expansions는 "
                "비어 있지 않은 문자열 목록이어야 합니다."
            )
        rules.append(rule)
    return rules


def expand_regulation_query(
    query: str,
    path: Path = DEFAULT_RULES_PATH,
) -> list[str]:
    cleaned_query = query.strip()
    if not cleaned_query:
        return []

    queries = [cleaned_query]
    for rule in load_query_expansion_rules(path):
        match_mode = str(rule.get("match", "any")).lower()
        matched_keywords = [keyword in cleaned_query for keyword in rule["keywords"]]
        matched = all(matched_keywords) if match_mode == "all" else any(matched_keywords)
        if matched:
            queries.extend(str(value).strip() for value in rule["expansions"])

    return list(dict.fromkeys(queries))

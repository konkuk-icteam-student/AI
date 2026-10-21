"""기존 실행·import 경로를 유지하는 검색 호환 모듈."""

from app.search.service import (
    FaqSearchResult,
    RegulationSearchResult,
    embed_query,
    expand_regulation_queries,
    main,
    print_search_results,
    search_faqs,
    search_regulations,
)

__all__ = [
    "FaqSearchResult",
    "RegulationSearchResult",
    "embed_query",
    "expand_regulation_queries",
    "print_search_results",
    "search_faqs",
    "search_regulations",
]


if __name__ == "__main__":
    main()

"""기존 `python -m app.ingest` 명령을 유지하는 호환 모듈."""

from app.ingestion.service import (
    check_ollama,
    embed_texts,
    generate_embeddings,
    main,
    save_records,
)

__all__ = [
    "check_ollama",
    "embed_texts",
    "generate_embeddings",
    "save_records",
]


if __name__ == "__main__":
    main()

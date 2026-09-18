"""FAQ 관리 및 인덱싱 기능."""

from app.faqs.service import delete_faq, upsert_faq

__all__ = ["delete_faq", "upsert_faq"]

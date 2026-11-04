"""청크 메타데이터에서 구조화 연락처를 구성한다."""

from __future__ import annotations

from typing import Any


def collect_phone_contacts(records: list[dict[str, Any]]) -> list[dict]:
    """전화번호부 청크 중 직책과 번호가 있는 연락처만 반환한다."""
    contacts = []
    for record in records:
        metadata = record.get("metadata", {})
        if metadata.get("record_type", "") != "phone_contact":
            continue

        role = str(metadata.get("role", "")).strip()
        phone = str(metadata.get("phone", "")).strip()
        if not role or not phone:
            continue

        contacts.append(
            {
                "id": record["id"],
                "source": str(metadata.get("source", "")),
                "page": int(metadata.get("page", 0)),
                "contact_index": int(metadata.get("chunk_index", 0)),
                "major_org": str(metadata.get("major_org", "")),
                "organization": str(metadata.get("organization", "")),
                "role": role,
                "phone": phone,
                "content": str(record.get("document", "")),
            }
        )
    return contacts

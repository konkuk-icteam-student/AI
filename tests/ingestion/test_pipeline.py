from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from app.ingestion import embedder, records, service
from app.ingestion.contacts import collect_phone_contacts


class IngestionPipelineTest(unittest.TestCase):
    def test_text_records_preserve_article_across_pages(self):
        pages = [
            {"page": 1, "text": "제1조(목적) 첫 문장", "extraction_type": "text"},
            {
                "page": 2,
                "text": "이어지는 문장\n제2조(절차) 절차",
                "extraction_type": "text",
            },
        ]
        with patch.object(records, "extract_pdf_pages", return_value=pages):
            result = records.extract_pdf_chunks(Path("규정.pdf"), "text")
        self.assertEqual(len(result), 2)
        self.assertIn("이어지는 문장", result[0]["document"])
        self.assertEqual(result[0]["metadata"]["page"], 1)
        self.assertEqual(result[1]["metadata"]["article"], "제2조(절차)")

    def test_ocr_records_keep_line_overlap(self):
        lines = [f"문장 {i}" for i in range(25)]
        with patch.object(
            records,
            "extract_pdf_pages",
            return_value=[
                {"page": 3, "text": "\n".join(lines), "extraction_type": "ocr"}
            ],
        ):
            result = records.extract_pdf_chunks(Path("스캔.pdf"), "ocr")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["document"].splitlines(), lines[:20])
        self.assertEqual(result[1]["document"].splitlines(), lines[17:])

    def test_only_valid_phone_records_are_collected(self):
        phone = {
            "id": "contact",
            "document": "안내",
            "metadata": {
                "record_type": "phone_contact",
                "source": "전화.pdf",
                "page": 2,
                "chunk_index": 1,
                "role": " 안내 ",
                "phone": " 1234 ",
            },
        }
        invalid = {**phone, "metadata": {**phone["metadata"], "phone": " "}}
        result = collect_phone_contacts(
            [{"metadata": {"record_type": "text_chunk"}}, invalid, phone]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["phone"], "1234")
        self.assertEqual(result[0]["role"], "안내")
        self.assertEqual(result[0]["id"], "contact")

    def test_indexing_uses_the_shared_embedding_function(self):
        items = [{"document": "규정"}, {"document": "연락처"}]
        self.assertIs(service.embed_texts, embedder.embed_texts)
        with patch.object(service, "embed_texts", return_value=[[1.0], [2.0]]) as embed:
            service.generate_embeddings(items)
        embed.assert_called_once_with(["규정", "연락처"])
        self.assertEqual([item["embedding"] for item in items], [[1.0], [2.0]])

    def test_embedding_count_mismatch_is_rejected(self):
        response = Mock()
        response.json.return_value = {"embeddings": [[1.0]]}
        with patch.object(embedder.requests, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "일치하지 않습니다"):
                embedder.embed_texts(["첫째", "둘째"])

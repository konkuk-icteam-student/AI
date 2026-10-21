import unittest

from app.ingestion.chunker import PageText, chunk_regulation_pages


class RegulationChunkerTest(unittest.TestCase):
    def test_splits_by_article_and_keeps_preamble(self):
        chunks = chunk_regulation_pages(
            [
                PageText(
                    page=1,
                    text=(
                        "제1장 총칙\n"
                        "제1조(목적) 이 규정은 목적을 정한다.\n"
                        "제2조(적용범위) 교직원에게 적용한다."
                    ),
                )
            ]
        )

        self.assertEqual(2, len(chunks))
        self.assertTrue(chunks[0].text.startswith("제1장 총칙\n제1조(목적)"))
        self.assertEqual("제1조(목적)", chunks[0].article)
        self.assertTrue(chunks[1].text.startswith("제2조(적용범위)"))

    def test_keeps_article_across_page_boundary(self):
        chunks = chunk_regulation_pages(
            [
                PageText(page=3, text="제7조(절차) 첫 번째 문장."),
                PageText(page=4, text="두 번째 문장.\n제8조(예외) 예외을 정한다."),
            ]
        )

        self.assertEqual(2, len(chunks))
        self.assertIn("두 번째 문장", chunks[0].text)
        self.assertEqual(3, chunks[0].page)
        self.assertEqual(4, chunks[1].page)

    def test_supports_subarticle_and_spaced_header(self):
        chunks = chunk_regulation_pages(
            [PageText(page=1, text="제 1 조의 2 (특례) 특례를 정한다.")]
        )
        self.assertEqual(1, len(chunks))
        self.assertEqual("제1조의2(특례)", chunks[0].article)

    def test_does_not_split_inline_article_reference(self):
        chunks = chunk_regulation_pages(
            [
                PageText(
                    page=1,
                    text="제1조(목적) 제5조에 따라 처리한다.\n제2조(절차) 절차를 정한다.",
                )
            ]
        )
        self.assertEqual(2, len(chunks))

    def test_does_not_split_reference_at_line_start(self):
        chunks = chunk_regulation_pages(
            [
                PageText(
                    page=1,
                    text=(
                        "제1조(목적) 기본 원칙을 정한다.\n"
                        "제5조에 따라 세부 절차를 처리한다.\n"
                        "제2조(적용) 적용 범위를 정한다."
                    ),
                )
            ]
        )
        self.assertEqual(2, len(chunks))
        self.assertIn("제5조에 따라", chunks[0].text)

    def test_does_not_treat_table_of_contents_entry_as_article(self):
        chunks = chunk_regulation_pages(
            [
                PageText(
                    page=1,
                    text=(
                        "목차\n제1조 목적 1\n제2조 적용범위 2\n"
                        "제1조(목적) 이 규정은 목적을 정한다.\n"
                        "제2조(적용범위) 적용 범위를 정한다."
                    ),
                )
            ]
        )
        self.assertEqual(2, len(chunks))
        self.assertIn("제1조 목적 1", chunks[0].text)

    def test_uses_legacy_fallback_without_article_headers(self):
        chunks = chunk_regulation_pages(
            [PageText(page=9, text="abcdefghij")],
            fallback_chunk_size=6,
            fallback_overlap=2,
        )
        self.assertEqual(["abcdef", "efghij"], [chunk.text for chunk in chunks])
        self.assertTrue(all(chunk.article is None for chunk in chunks))


if __name__ == "__main__":
    unittest.main()

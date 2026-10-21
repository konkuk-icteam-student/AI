import json
import tempfile
import unittest
from pathlib import Path

from app.search.query_expansion import expand_regulation_query


class QueryExpansionTest(unittest.TestCase):
    def test_reads_rules_from_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(
                json.dumps(
                    {
                        "rules": [
                            {
                                "keywords": ["휴학", "복학"],
                                "expansions": ["학적 변동 절차"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = expand_regulation_query("휴학은 어떻게 하나요?", path=path)
        self.assertEqual(["휴학은 어떻게 하나요?", "학적 변동 절차"], result)

    def test_supports_all_match_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(
                json.dumps(
                    {
                        "rules": [
                            {
                                "keywords": ["장학", "장애"],
                                "match": "all",
                                "expansions": ["장애학생 장학금"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                ["장학 질문"],
                expand_regulation_query("장학 질문", path=path),
            )
            self.assertEqual(
                ["장애 장학 질문", "장애학생 장학금"],
                expand_regulation_query("장애 장학 질문", path=path),
            )


if __name__ == "__main__":
    unittest.main()

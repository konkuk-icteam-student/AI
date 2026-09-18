import tempfile
import unittest
from pathlib import Path

from app.chat.prompts import render_chat_prompt


class PromptTemplateTest(unittest.TestCase):
    def test_replaces_managed_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompt.txt"
            path.write_text(
                "{{QUESTION}}|{{REGULATION_CONTEXT}}|{{FAQ_CONTEXT}}|{{FAQ_ONLY_NOTICE}}",
                encoding="utf-8",
            )
            rendered = render_chat_prompt(
                question="질문",
                regulation_context="규정",
                faq_context="FAQ",
                faq_only_notice="안내",
                path=path,
            )
        self.assertEqual("질문|규정|FAQ|안내", rendered)

    def test_rejects_missing_required_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompt.txt"
            path.write_text("{{QUESTION}}", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                render_chat_prompt(
                    question="q",
                    regulation_context="r",
                    faq_context="f",
                    faq_only_notice="n",
                    path=path,
                )


if __name__ == "__main__":
    unittest.main()

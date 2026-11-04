import unittest
from unittest.mock import patch

from app.core import config


class ConfigValidationTest(unittest.TestCase):
    def test_rejects_nonpositive_timeouts(self):
        for name in ("OLLAMA_CONNECT_TIMEOUT", "OLLAMA_REQUEST_TIMEOUT"):
            for value in (0, -1):
                with self.subTest(name=name, value=value), patch.object(
                    config, name, value
                ):
                    with self.assertRaisesRegex(ValueError, name):
                        config.validate_config()

    def test_rejects_nonfinite_relevance_threshold(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), patch.object(
                config, "MIN_RELEVANCE_SCORE", value
            ):
                with self.assertRaisesRegex(ValueError, "MIN_RELEVANCE_SCORE"):
                    config.validate_config()

    def test_rejects_empty_database_url(self):
        with patch.object(config, "DATABASE_URL", " "):
            with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
                config.validate_config()

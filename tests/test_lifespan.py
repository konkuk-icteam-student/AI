import unittest
from unittest.mock import patch

from app import main


class LifespanTest(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_closes_pool_even_when_application_raises(self):
        with patch.object(main, "validate_config"), patch.object(
            main, "ensure_directories"
        ), patch.object(main, "init_schema"), patch.object(
            main, "check_db", return_value={"regulation_chunks": 1, "faq_chunks": 0}
        ), patch.object(
            main, "close_pool"
        ) as close:
            with self.assertRaisesRegex(RuntimeError, "종료 중 오류"):
                async with main.lifespan(main.app):
                    raise RuntimeError("종료 중 오류")
        close.assert_called_once()

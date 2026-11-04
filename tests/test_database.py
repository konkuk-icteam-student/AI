from concurrent.futures import ThreadPoolExecutor
import time
import unittest
from unittest.mock import MagicMock, patch

from app.infrastructure import database


class DatabasePoolTest(unittest.TestCase):
    def setUp(self):
        self.pool_state = patch.object(database, "_pool", None)
        self.pool_state.start()
        self.addCleanup(self.pool_state.stop)

    def test_concurrent_initialization_creates_one_threaded_pool(self):
        pool = MagicMock()

        def create_pool(**kwargs):
            time.sleep(0.02)
            return pool

        with patch.object(
            database.psycopg2.pool, "ThreadedConnectionPool", side_effect=create_pool
        ) as factory:
            with ThreadPoolExecutor(max_workers=16) as executor:
                results = list(executor.map(lambda _: database._get_pool(), range(32)))
        factory.assert_called_once()
        self.assertTrue(all(result is pool for result in results))

    def test_close_allows_a_fresh_pool_to_be_created(self):
        first, second = MagicMock(), MagicMock()
        with patch.object(
            database.psycopg2.pool,
            "ThreadedConnectionPool",
            side_effect=[first, second],
        ):
            self.assertIs(database._get_pool(), first)
            database.close_pool()
            database.close_pool()
            self.assertIs(database._get_pool(), second)
        first.closeall.assert_called_once()

    def test_failed_operation_rolls_back_and_returns_connection(self):
        pool = MagicMock()
        connection = pool.getconn.return_value
        with patch.object(database, "_get_pool", return_value=pool):
            with self.assertRaisesRegex(RuntimeError, "실패"):
                with database.get_conn():
                    raise RuntimeError("실패")
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        pool.putconn.assert_called_once_with(connection)

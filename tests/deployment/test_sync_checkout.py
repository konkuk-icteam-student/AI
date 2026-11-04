"""Test server-side source checkout sync without SSH or external GitHub access."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/scripts/sync-checkout.sh"


class CheckoutSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init", "-b", "main", str(self.source), cwd=self.root)
        self.git("config", "user.name", "test", cwd=self.source)
        self.git("config", "user.email", "test@example.com", cwd=self.source)
        (self.source / ".gitignore").write_text(
            "rag_evaluation/results/*\n!rag_evaluation/results/.gitkeep\n"
        )
        (self.source / "version.txt").write_text("main\n")
        self.git("add", ".", cwd=self.source)
        self.git("commit", "-m", "main version", cwd=self.source)
        self.main_sha = self.git("rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self.git("switch", "-c", "develop", cwd=self.source)
        (self.source / "version.txt").write_text("develop\n")
        self.git("add", ".", cwd=self.source)
        self.git("commit", "-m", "develop version", cwd=self.source)
        self.develop_sha = self.git("rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self.origin = self.root / "origin.git"
        self.git("clone", "--bare", str(self.source), str(self.origin), cwd=self.root)
        self.checkouts = self.root / "chatbot"
        (self.checkouts / "staging").mkdir(parents=True)
        (self.checkouts / "prod").mkdir(parents=True)

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd, text=True, capture_output=True,
            check=True, timeout=10,
        )

    def sync(self, environment, sha):
        return subprocess.run(
            ["bash", str(SCRIPT), environment, sha, str(self.origin),
             str(self.checkouts)],
            text=True, capture_output=True, timeout=30,
        )

    def test_initial_sync_tracks_correct_branches(self):
        for env, sha, branch, expected in [
            ("staging", self.develop_sha, "develop", "develop\n"),
            ("prod", self.main_sha, "main", "main\n"),
        ]:
            result = self.sync(env, sha)
            self.assertEqual(result.returncode, 0, result.stderr)
            path = self.checkouts / env
            self.assertEqual((path / "version.txt").read_text(), expected)
            self.assertEqual(self.git("rev-parse", "HEAD", cwd=path).stdout.strip(), sha)
            self.assertEqual(
                self.git("branch", "--show-current", cwd=path).stdout.strip(), branch
            )

    def test_next_push_fast_forwards_and_preserves_results(self):
        self.assertEqual(self.sync("staging", self.develop_sha).returncode, 0)
        dest = self.checkouts / "staging"
        results = dest / "rag_evaluation/results"
        results.mkdir(parents=True)
        output = results / "20261104_170000_answers.csv"
        output.write_text("saved response\n")
        (self.source / "version.txt").write_text("develop-v2\n")
        self.git("add", ".", cwd=self.source)
        self.git("commit", "-m", "develop update", cwd=self.source)
        next_sha = self.git("rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self.git("push", str(self.origin), "develop", cwd=self.source)
        result = self.sync("staging", next_sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((dest / "version.txt").read_text(), "develop-v2\n")
        self.assertEqual(output.read_text(), "saved response\n")
        self.assertEqual(self.git("status", "--porcelain", cwd=dest).stdout.strip(), "")

    def test_modified_tracked_files_are_not_overwritten(self):
        self.assertEqual(self.sync("staging", self.develop_sha).returncode, 0)
        dest = self.checkouts / "staging"
        (dest / "version.txt").write_text("local edit\n")
        result = self.sync("staging", self.develop_sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local edits", result.stderr)
        self.assertEqual((dest / "version.txt").read_text(), "local edit\n")

    def test_nonempty_non_git_folder_is_preserved(self):
        path = self.checkouts / "prod" / "important.csv"
        path.write_text("keep\n")
        result = self.sync("prod", self.main_sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(path.exists())

    def test_reject_invalid_sha_and_unknown_environment(self):
        self.assertNotEqual(self.sync("staging", "not-a-sha").returncode, 0)
        self.assertNotEqual(self.sync("other", self.develop_sha).returncode, 0)


if __name__ == "__main__":
    unittest.main()

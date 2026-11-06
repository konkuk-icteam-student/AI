"""Tests for content-only checkout syncing without Docker or network calls."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

REPOSITORY = Path(__file__).resolve().parents[2]
SYNC = REPOSITORY / "deploy/scripts/sync-content-only-remote.sh"


def git(*args, cwd=None):
    return subprocess.check_output(
        ["git"] + list(args),
        cwd=str(cwd) if cwd else None,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    ).strip()


class ContentOnlyStagingSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / "origin.git"
        self.seed = self.root / "seed"
        self.home = self.root / "home"
        self.checkout = self.home / "chatbot/staging"
        self.deploy = self.root / "deploy-root"
        (self.deploy / "staging").mkdir(parents=True)
        self.checkout.parent.mkdir(parents=True)

        git("init", "--bare", str(self.origin))
        git("clone", str(self.origin), str(self.seed))
        git("checkout", "-b", "develop", cwd=self.seed)
        git("config", "user.name", "CI Test", cwd=self.seed)
        git("config", "user.email", "ci-test@example.invalid", cwd=self.seed)
        (self.seed / ".gitignore").write_text(
            "rag_evaluation/results/*\n", encoding="utf-8"
        )
        (self.seed / "app").mkdir()
        (self.seed / "app/main.py").write_text("print('hello')\n", encoding="utf-8")
        (self.seed / "README.md").write_text("# Initial\n", encoding="utf-8")
        git("add", ".", cwd=self.seed)
        git("commit", "-m", "base image source", cwd=self.seed)
        self.initial = git("rev-parse", "HEAD", cwd=self.seed)
        git("push", "-u", "origin", "develop", cwd=self.seed)
        git("clone", "--branch", "develop", str(self.origin), str(self.checkout))
        (self.deploy / "staging/.current-image").write_text(
            self.initial + "\n", encoding="utf-8"
        )
        self.output = self.checkout / "rag_evaluation/results/ongoing_answers.csv"
        self.output.parent.mkdir(parents=True)
        self.output.write_text("ID,AI 답변\nA001,계속 처리 중\n", encoding="utf-8")

    def commit(self, path, content):
        target = self.seed / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        git("add", path, cwd=self.seed)
        git("commit", "-m", "modify " + path, cwd=self.seed)
        git("push", "origin", "develop", cwd=self.seed)
        return git("rev-parse", "HEAD", cwd=self.seed)

    def sync(self, sha):
        return subprocess.run(
            ["bash", str(SYNC), sha, str(self.origin), str(self.deploy)],
            env={**os.environ, "HOME": str(self.home)},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )

    def test_docs_only_updates_checkout_without_touching_results(self):
        sha = self.commit("docs/test.md", "# Documentation update\n")
        result = self.sync(sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.checkout), sha)
        self.assertEqual(self.output.read_text(encoding="utf-8"),
                         "ID,AI 답변\nA001,계속 처리 중\n")
        self.assertEqual((self.deploy / "staging/.current-image").read_text().strip(),
                         self.initial)

    def test_question_csv_only_updates_checkout(self):
        sha = self.commit("rag_evaluation/questions/new.csv", "질문\n테스트 질문\n")
        result = self.sync(sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.checkout / "rag_evaluation/questions/new.csv").exists())

    def test_code_change_refuses_source_only_sync(self):
        sha = self.commit("app/main.py", "print('new code')\n")
        result = self.sync(sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker rollout required", result.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.checkout), self.initial)

    def test_prior_code_push_followed_by_docs_is_not_misclassified(self):
        self.commit("app/main.py", "print('new code')\n")
        sha = self.commit("README.md", "# Readme update\n")
        result = self.sync(sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker rollout required", result.stderr)

    def test_old_queued_workflow_never_rolls_back_checkout(self):
        old = self.commit("README.md", "# First update\n")
        new = self.commit("docs/second.md", "# Second update\n")
        self.assertEqual(self.sync(new).returncode, 0)
        result = self.sync(old)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.checkout), new)

    def test_untracked_output_and_tracked_edit_are_protected(self):
        sha = self.commit("README.md", "# Updated\n")
        (self.checkout / "README.md").write_text("# Unsaved local edits\n",
                                                   encoding="utf-8")
        result = self.sync(sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tracked local changes", result.stderr)
        self.assertEqual(self.output.read_text(encoding="utf-8"),
                         "ID,AI 답변\nA001,계속 처리 중\n")

    def test_no_active_image_marker_prevents_sync(self):
        sha = self.commit("README.md", "# Updated\n")
        (self.deploy / "staging/.current-image").unlink()
        result = self.sync(sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("marker", result.stderr)


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
NEW_TAG = "a" * 40
OLD_TAG = "b" * 40
IMAGE = "ghcr.io/example/chatbot"


class DeploymentScriptsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        for directory in ("deploy", "resources"):
            shutil.copytree(REPOSITORY / directory, self.source / directory)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "docker.log"
        self.env = {
            **os.environ,
            "DEPLOY_ROOT": str(self.runtime),
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "MOCK_DOCKER_LOG": str(self.log),
        }
        self.mock_command("curl", "#!/usr/bin/env bash\nexit 0\n")
        self.mock_command(
            "ollama",
            "#!/usr/bin/env bash\n"
            "printf 'NAME ID SIZE MODIFIED\\nqwen3-embedding:0.6b x 639MB now\\nqwen3:8b y 5GB now\\n'\n",
        )
        self.mock_command(
            "docker",
            """#!/usr/bin/env bash
set -eu
if [[ "$1" == login ]]; then
    cat > "$DOCKER_CONFIG/config.json"
    exit 0
fi
if [[ "$1" == inspect ]]; then
    container="${@: -1}"
    tag="${MOCK_IMAGE_OVERRIDE:-${container#api-}}"
    printf 'ghcr.io/example/chatbot:%s\\n' "$tag"
    exit 0
fi
printf '%s %s\\n' "${IMAGE_TAG:-}" "$*" >> "$MOCK_DOCKER_LOG"
for arg in "$@"; do
    if [[ "$arg" == up && "${IMAGE_TAG:-}" == "${MOCK_FAIL_UP_TAG:-never}" ]]; then
        exit 1
    fi
    if [[ "$arg" == ps ]]; then
        printf 'api-%s\\n' "$IMAGE_TAG"
        exit 0
    fi
done
""",
        )

    def mock_command(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_script(self, name, *arguments):
        return subprocess.run(
            ["bash", str(self.source / "deploy/scripts" / name), *arguments],
            env=self.env,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def prepare(self):
        result = self.run_script("prepare-env.sh", "staging")
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.runtime / "staging"

    def test_preparation_protects_password_and_existing_environment(self):
        runtime = self.prepare()
        env_file = runtime / ".env"
        before = env_file.read_text()
        self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)
        documents = self.runtime / "storage/staging/documents"
        for folder in (documents, documents / "text_pdf", documents / "image_pdf"):
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o755)
        password = next(line.split("=", 1)[1] for line in before.splitlines() if line.startswith("POSTGRES_PASSWORD="))
        self.assertEqual(len(password), 64)
        result = self.run_script("prepare-env.sh", "staging")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(env_file.read_text(), before)
        self.assertNotIn(password, result.stdout)

    def test_success_records_verified_image_and_readable_configuration(self):
        runtime = self.prepare()
        result = self.run_script("deploy.sh", "staging", IMAGE, NEW_TAG)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((runtime / ".current-image").read_text().strip(), NEW_TAG)
        self.assertIn(f"IMAGE_TAG={NEW_TAG}", (runtime / ".deployment.env").read_text())
        prompt = self.runtime / "config/staging/prompts/chat_answer.txt"
        self.assertEqual(stat.S_IMODE(prompt.stat().st_mode), 0o644)

    def test_healthy_but_wrong_image_is_not_recorded_as_success(self):
        runtime = self.prepare()
        self.env["MOCK_IMAGE_OVERRIDE"] = OLD_TAG
        result = self.run_script("deploy.sh", "staging", IMAGE, NEW_TAG)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((runtime / ".current-image").exists())
        self.assertIn("does not match", result.stderr)

    def test_failed_upgrade_redeploys_previous_image(self):
        runtime = self.prepare()
        (runtime / ".current-image").write_text(OLD_TAG + "\n")
        self.env["MOCK_FAIL_UP_TAG"] = NEW_TAG
        result = self.run_script("deploy.sh", "staging", IMAGE, NEW_TAG)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((runtime / ".current-image").read_text().strip(), OLD_TAG)
        self.assertIn(f"IMAGE_TAG={OLD_TAG}", (runtime / ".deployment.env").read_text())
        self.assertIn(f"{OLD_TAG} compose", self.log.read_text())

    def test_missing_environment_stops_before_container_operations(self):
        result = self.run_script("deploy.sh", "staging", IMAGE, NEW_TAG)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.runtime / "staging/.env").exists())
        self.assertFalse(self.log.exists())

    def test_remote_registry_credentials_are_removed_after_failure(self):
        self.prepare()
        self.env["MOCK_FAIL_UP_TAG"] = NEW_TAG
        token = "temporary-registry-token-for-test"
        result = subprocess.run(
            ["bash", str(self.source / "deploy/scripts/deploy-remote.sh"),
             "staging", IMAGE, NEW_TAG, str(self.runtime), "test-user"],
            input=token + "\n", env=self.env, text=True,
            capture_output=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.runtime.glob(".registry-auth.*")), [])
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_ssh_arguments_reject_shell_injection_before_using_credentials(self):
        self.env.update({
            "DEPLOY_HOST": "203.252.168.90;touch /tmp/injected",
            "DEPLOY_USER": "kuai",
            "DEPLOY_SSH_PRIVATE_KEY": "not-used",
            "DEPLOY_HOST_KEY": "not-used",
            "GHCR_USER": "test-user",
            "GHCR_TOKEN": "not-used",
        })
        result = self.run_script("deploy-ssh.sh", "staging", IMAGE, NEW_TAG)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid deployment", result.stderr)


if __name__ == "__main__":
    unittest.main()

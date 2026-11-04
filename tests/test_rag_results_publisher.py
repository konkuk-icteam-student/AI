"""RAG answer CSV publisher tests; all Git remotes are local temporary directories."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

PUBLISHER_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'publish_rag_results.py'
spec = importlib.util.spec_from_file_location('rag_csv_publisher', str(PUBLISHER_PATH))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def git(*args, cwd=None):
    return subprocess.run(['git'] + list(args), cwd=str(cwd) if cwd else None,
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


class RagResultsPublishingTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.results = self.root / 'results'
        self.results.mkdir()

    def answer(self, run_id='20261104_165000', success=2, total=2, label='regulations'):
        csvfile = self.results / '{}_{}_answers.csv'.format(run_id, label)
        csvfile.write_text('ID,AI 답변\n1,안내\n', encoding='utf-8')
        summary = self.results / '{}_{}_summary.json'.format(run_id, label)
        summary.write_text(json.dumps({'total': total, 'success': success,
                                       'not_run': total - success}), encoding='utf-8')
        return csvfile

    def test_complete_only_and_run_filter(self):
        ready = self.answer()
        self.answer('20261104_165001', success=1, total=2)
        self.answer('20261104_165002', success=0, total=0)
        self.assertEqual(publisher.completed_csvs(self.results), [ready])
        self.assertEqual(publisher.completed_csvs(self.results, '20261104_165001'), [])
        self.assertEqual(publisher.completed_csvs(self.results, '20261104_165000'), [ready])

    def test_publish_only_answer_csv_without_touching_deployment_branches(self):
        origin = self.root / 'origin.git'
        seed = self.root / 'seed'
        git('init', '--bare', str(origin))
        git('clone', str(origin), str(seed))
        git('checkout', '-b', 'rag-results/staging', cwd=seed)
        git('config', 'user.name', 'Test Runner', cwd=seed)
        git('config', 'user.email', 'test@example.com', cwd=seed)
        (seed / '.gitignore').write_text('rag_evaluation/results/*\n', encoding='utf-8')
        (seed / 'README.md').write_text('RAG repository\n', encoding='utf-8')
        git('add', '.', cwd=seed)
        git('commit', '-m', 'initial results branch', cwd=seed)
        git('push', 'origin', 'rag-results/staging', cwd=seed)

        good = self.answer()
        rawfile = self.results / '20261104_165000_regulations_raw.jsonl'
        rawfile.write_text('{"private": true}\n', encoding='utf-8')

        self.assertTrue(publisher.publish([good], 'staging', str(origin), str(origin)))
        self.assertFalse(publisher.publish([good], 'staging', str(origin), str(origin)))

        downloaded = self.root / 'downloaded'
        git('clone', '--branch', 'rag-results/staging', str(origin), str(downloaded))
        uploaded = downloaded / 'rag_evaluation/results/staging' / good.name
        self.assertEqual(uploaded.read_text(encoding='utf-8'), good.read_text(encoding='utf-8'))
        self.assertFalse((downloaded / 'rag_evaluation/results/staging' / rawfile.name).exists())

        next_csv = self.answer('20261104_170000', label='faq')
        self.assertTrue(publisher.publish([next_csv], 'staging', str(origin), str(origin)))
        git('pull', cwd=downloaded)
        self.assertTrue(uploaded.exists())
        self.assertTrue((downloaded / 'rag_evaluation/results/staging' / next_csv.name).exists())


if __name__ == '__main__':
    unittest.main()

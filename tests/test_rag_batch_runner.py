"""RAG 일괄 질문기: 네트워크/DB 없이 수행하는 테스트."""
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

RUNNER = Path(__file__).resolve().parents[1] / 'scripts' / 'run_rag_batch.py'
spec = importlib.util.spec_from_file_location('rag_batch_runner', str(RUNNER))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RagBatchRunnerTest(unittest.TestCase):
    def test_read_csv_keeps_metadata_and_korean_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'questions.csv'
            with path.open('w', encoding='utf-8-sig', newline='') as f:
                w = csv.DictWriter(f, ['ID', '질문', '기대 정답 / 기대 응답'])
                w.writeheader()
                w.writerow({'ID': 'A001', '질문': '개방이사는 언제 추천하나요?',
                            '기대 정답 / 기대 응답': '정답 비공개'})
            fields, rows = runner.read_csv(path)
            self.assertEqual(rows[0]['_batch_id'], 'A001')
            self.assertEqual(rows[0]['기대 정답 / 기대 응답'], '정답 비공개')
            self.assertEqual(fields, ['ID', '질문', '기대 정답 / 기대 응답'])

    def test_api_only_sends_question(self):
        class Proc:
            returncode = 0
            stdout = (json.dumps({'answer': '답변', 'regulation_sources': [],
                                  'faq_sources': [], 'conflict_detected': False}) +
                      runner.STATUS_MARKER + '200').encode('utf-8')
            stderr = b''
        with mock.patch.object(runner.subprocess, 'run', return_value=Proc()) as sub:
            result = runner.call_api('법인의 정관?', 'chatbot-staging-api-1',
                                     'http://127.0.0.1:18000/api/v1/chat/query', 30)
            self.assertEqual(result['status'], 'OK')
            sent = json.loads(sub.call_args.kwargs['input'].decode('utf-8'))
            self.assertEqual(sent, {'query': '법인의 정관?'})

    def test_saves_timestamped_files_with_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / '20261104_164500_questions'
            rows = [{'ID': 'A001', '질문': '정관?', '기대 정답': '기대값', '_batch_id': 'A001'}]
            saved = {'A001': {
                'status': 'OK', 'http_status': 200, 'error': '', 'elapsed_sec': 2.3,
                'timestamp': '20261104_164500',
                'response': {'answer': '테스트 응답', 'conflict_detected': False,
                             'regulation_sources': [{'source': '정관.pdf', 'page': 1,
                                                     'chunk_index': 2, 'score': 1.3}],
                             'faq_sources': []}}}
            summary = runner.save_progress(base, ['ID', '질문', '기대 정답'], rows, saved)
            self.assertEqual(summary['success'], 1)
            with Path(str(base) + '_answers.csv').open('r', encoding='utf-8-sig') as f:
                result = next(csv.DictReader(f))
                self.assertEqual(result['AI 답변'], '테스트 응답')
                self.assertEqual(result['기대 정답'], '기대값')
                self.assertIn('정관.pdf', result['AI 근거 규정'])


if __name__ == '__main__':
    unittest.main()

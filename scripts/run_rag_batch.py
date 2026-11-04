#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAG 질문 CSV를 Docker 내부 FastAPI에 순차 전송하고 날짜별 결과를 저장한다.

Python 3.6+ 표준 라이브러리만 사용. 운영 DB를 변경하지 않는다.
"""
import argparse
import collections
import csv
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_DIR = ROOT / 'rag_evaluation' / 'questions'
RESULTS_DIR = ROOT / 'rag_evaluation' / 'results'
ADDED = ['AI 답변', 'AI 근거 규정', 'AI FAQ 근거', '근거 충돌 감지',
         '응답시간(초)', 'API 상태', 'HTTP 상태', '오류', '처리시각(KST)']
STATUS_MARKER = '\n__RAG_HTTP_STATUS__:'


def timestamp():
    kst = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(kst).strftime('%Y%m%d_%H%M%S')


def read_csv(path):
    with path.open('r', encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        if '질문' not in fields:
            raise ValueError('{}: CSV에 질문 열이 없습니다.'.format(path))
        rows = list(reader)
    ids = []
    for i, row in enumerate(rows, 1):
        row['_batch_id'] = (row.get('ID') or str(i)).strip()
        if not row['_batch_id']:
            raise ValueError('{}: ID가 비어 있습니다.'.format(path))
        ids.append(row['_batch_id'])
    if len(ids) != len(set(ids)):
        raise ValueError('{}: 중복 ID가 있습니다.'.format(path))
    if not rows:
        raise ValueError('{}: 질문이 없습니다.'.format(path))
    return fields, rows


def call_api(question, container, url, timeout):
    payload = json.dumps({'query': question}, ensure_ascii=False).encode('utf-8')
    cmd = [
        'docker', 'exec', '-i', container,
        'curl', '-sS', '--connect-timeout', '10', '--max-time', str(timeout),
        '-X', 'POST', url, '-H', 'Content-Type: application/json',
        '--data-binary', '@-', '-w', STATUS_MARKER + '%{http_code}',
    ]
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, input=payload, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout + 15)
        output = proc.stdout.decode('utf-8', errors='replace')
        body, sep, code = output.rpartition(STATUS_MARKER)
        http_code = int(code.strip()) if sep and code.strip().isdigit() else 0
        response = json.loads(body) if body else None
        status = ('OK' if proc.returncode == 0 and http_code == 200
                  and isinstance(response, dict) and isinstance(response.get('answer'), str)
                  else 'HTTP_{}'.format(http_code) if http_code else 'TRANSPORT_ERROR')
        error = '' if status == 'OK' else (proc.stderr.decode('utf-8', errors='replace').strip()
                                          or str(response or output)[:500])
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        status, http_code, response, error = 'TRANSPORT_ERROR', 0, None, str(exc)
    return {'status': status, 'http_status': http_code, 'response': response,
            'error': error[:1000], 'elapsed_sec': round(time.monotonic() - start, 3)}


def source_string(response, key):
    chunks = []
    for item in response.get(key, []) or []:
        if key == 'regulation_sources':
            chunks.append('{} (p.{}, chunk {}, score {})'.format(
                item.get('source', ''), item.get('page', ''),
                item.get('chunk_index', ''), item.get('score', '')))
        else:
            chunks.append('FAQ {} (chunk {}, score {})'.format(
                item.get('faq_id', ''), item.get('chunk_index', ''), item.get('score', '')))
    return ' ; '.join(chunks)


def output_row(row, record):
    result = {k: v for k, v in row.items() if k != '_batch_id'}
    response = (record or {}).get('response') or {}
    result.update({
        'AI 답변': response.get('answer', ''),
        'AI 근거 규정': source_string(response, 'regulation_sources'),
        'AI FAQ 근거': source_string(response, 'faq_sources'),
        '근거 충돌 감지': response.get('conflict_detected', ''),
        '응답시간(초)': (record or {}).get('elapsed_sec', ''),
        'API 상태': (record or {}).get('status', 'NOT_RUN'),
        'HTTP 상태': (record or {}).get('http_status', ''),
        '오류': (record or {}).get('error', ''),
        '처리시각(KST)': (record or {}).get('timestamp', ''),
    })
    return result


def save_progress(base, fields, rows, previous):
    csv_path, summary_path = Path(str(base) + '_answers.csv'), Path(str(base) + '_summary.json')
    columns = fields + [name for name in ADDED if name not in fields]
    tmp = Path(str(csv_path) + '.tmp')
    with tmp.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, columns, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow(output_row(row, previous.get(row['_batch_id'])))
    os.replace(str(tmp), str(csv_path))
    counts = collections.Counter((previous.get(row['_batch_id']) or {}).get('status', 'NOT_RUN')
                                 for row in rows)
    summary = {'input': str(base.name), 'total': len(rows),
               'success': counts.get('OK', 0), 'not_run': counts.get('NOT_RUN', 0),
               'statuses': dict(counts),
               'note': '기대 정답과 AI 응답의 일치 여부는 자동 채점하지 않습니다.'}
    tmp = Path(str(summary_path) + '.tmp')
    with tmp.open('w', encoding='utf-8') as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(summary_path))
    return summary


def load_previous(path):
    previous = {}
    if path.exists():
        with path.open('r', encoding='utf-8') as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                    previous[str(item['id'])] = item
                except (ValueError, KeyError, TypeError):
                    continue
    return previous


def run_file(path, args, run_id):
    fields, rows = read_csv(path)
    # Windows/Unix 파일명에 안전한 문자만 유지한다.
    label = re.sub(r'[^A-Za-z0-9가-힣_-]+', '_', path.stem)[:55].strip('_') or 'questions'
    base = RESULTS_DIR / '{}_{}'.format(run_id, label)
    raw_path = Path(str(base) + '_raw.jsonl')
    previous = load_previous(raw_path)
    print('\n[{}] {}개 질문 → {}'.format(path.name, len(rows), base), flush=True)
    attempted = 0
    try:
        with raw_path.open('a', encoding='utf-8') as raw:
            for i, row in enumerate(rows, 1):
                qid = row['_batch_id']
                if (previous.get(qid) or {}).get('status') == 'OK' and (previous.get(qid) or {}).get('question') == (row.get('질문') or '').strip():
                    continue
                if args.limit and attempted >= args.limit:
                    break
                question = (row.get('질문') or '').strip()
                if question:
                    result = call_api(question, args.container, args.url, args.timeout)
                    for attempt in range(args.retries):
                        if result['status'] == 'OK' or result['status'].startswith('HTTP_4') and result['status'] != 'HTTP_429':
                            break
                        time.sleep(min(2 ** attempt, 10))
                        result = call_api(question, args.container, args.url, args.timeout)
                else:
                    result = {'status': 'EMPTY_QUESTION', 'response': None,
                              'http_status': 0, 'error': '빈 질문', 'elapsed_sec': 0}
                record = {'id': qid, 'question': question, 'timestamp': timestamp(), **result}
                raw.write(json.dumps(record, ensure_ascii=False) + '\n')
                raw.flush()
                previous[qid] = record
                attempted += 1
                save_progress(base, fields, rows, previous)
                print('  [{}/{}] {}: {}'.format(i, len(rows), qid, result['status']), flush=True)
                if args.pause:
                    time.sleep(args.pause)
    except KeyboardInterrupt:
        print('중단됨. 동일 --run-id로 이어서 실행할 수 있습니다.', file=sys.stderr)
    summary = save_progress(base, fields, rows, previous)
    print('완료: {}/{}개 성공 | {}개 미처리'.format(
        summary['success'], summary['total'], summary['not_run']))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description='CSV 질문을 Docker RAG API로 보내 답변을 날짜별 CSV/JSONL로 저장')
    parser.add_argument('--input', help='단일 CSV 경로. 미지정 시 questions/의 모든 CSV 처리')
    parser.add_argument('--container', default='chatbot-staging-api-1')
    parser.add_argument('--url', default='http://127.0.0.1:18000/api/v1/chat/query')
    parser.add_argument('--timeout', type=int, default=360)
    parser.add_argument('--retries', type=int, default=2)
    parser.add_argument('--limit', type=int, default=0, help='파일당 새 질문 처리 개수, 0=전체')
    parser.add_argument('--pause', type=float, default=0)
    parser.add_argument('--run-id', help='이어하기용 실행 ID (예: 20261104_154500)')
    args = parser.parse_args(argv)
    if args.limit < 0 or args.retries < 0 or args.timeout < 1 or args.pause < 0:
        parser.error('limit/retries/pause는 0 이상, timeout은 1 이상이어야 합니다.')
    if args.run_id and not re.fullmatch(r'\d{8}_\d{6}', args.run_id):
        parser.error('--run-id는 YYYYMMDD_HHMMSS 형식이어야 합니다.')
    paths = [Path(args.input)] if args.input else sorted(QUESTIONS_DIR.glob('*.csv'))
    if not paths:
        parser.error('rag_evaluation/questions/에 CSV 파일이 없습니다.')
    for path in paths:
        if not path.is_file():
            parser.error('{} 파일이 없습니다.'.format(path))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or timestamp()
    print('RAG API: docker exec {} → {}'.format(args.container, args.url))
    for path in paths:
        run_file(path, args, run_id)
    return 0


if __name__ == '__main__':
    sys.exit(main())
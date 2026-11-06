#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publish only COMPLETE RAG answer CSVs with their summary JSONs to a results branch.

Python 3.6+ stdlib; never changes develop/main or the working checkout.
Requires a dedicated write-enabled GitHub deploy key on the server.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / 'rag_evaluation' / 'results'
CLONE_URL = 'https://github.com/konkuk-icteam-student/AI.git'
PUSH_URL = 'git@github.com:konkuk-icteam-student/AI.git'
BRANCHES = {'staging': 'rag-results/staging', 'prod': 'rag-results/prod'}
KEY_DEFAULT = '~/.ssh/ai-rag-results'


def paired_summary(csv_path):
    """Return the summary file for an answer CSV; reject missing/unsafe pairs."""
    suffix = '_answers.csv'
    if not csv_path.name.endswith(suffix):
        raise ValueError('Not an answer CSV: {}'.format(csv_path))
    summary_path = csv_path.with_name(csv_path.name[:-len(suffix)] + '_summary.json')
    if summary_path.is_symlink() or not summary_path.is_file():
        raise RuntimeError('Missing or unsafe summary JSON for {}'.format(csv_path.name))
    return summary_path


def completed_csvs(results_dir, run_id=None):
    """Select answer CSVs only if the corresponding summary reports 100% API success."""
    chosen = []
    if not results_dir.is_dir():
        return chosen
    for csv_path in sorted(results_dir.glob('*_answers.csv')):
        if csv_path.is_symlink() or not csv_path.is_file():
            continue
        if run_id and not csv_path.name.startswith(run_id + '_'):
            continue
        try:
            summary_path = paired_summary(csv_path)
            with summary_path.open('r', encoding='utf-8') as stream:
                summary = json.load(stream)
            total = summary['total']
            if (not isinstance(total, int) or total <= 0 or
                    summary.get('success') != total or
                    summary.get('not_run') != 0 or
                    not csv_path.stat().st_size):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        chosen.append(csv_path)
    return chosen


def git(args, cwd=None, env=None, check=True):
    proc = subprocess.run(['git'] + list(args), cwd=str(cwd) if cwd else None,
                          env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)
    if check and proc.returncode:
        raise RuntimeError('git {} failed:\n{}'.format(args[0], proc.stderr.strip()))
    return proc


def ssh_environment(key_file):
    key = Path(key_file).expanduser().resolve()
    if not key.is_file():
        raise RuntimeError('GitHub deploy key not found: {} (see rag_evaluation/README.md)'.format(key))
    if key.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError('GitHub deploy key is not private; run chmod 600 {}'.format(key))
    env = os.environ.copy()
    env['GIT_SSH_COMMAND'] = (
        'ssh -i {} -o IdentitiesOnly=yes -o BatchMode=yes '
        '-o StrictHostKeyChecking=yes'.format(shlex.quote(str(key)))
    )
    return env


def publish(csv_paths, environment, clone_url=CLONE_URL, push_url=PUSH_URL, ssh_env=None):
    """Publish only the supplied CSVs. Retry non-fast-forward races, never force push."""
    if environment not in BRANCHES:
        raise ValueError('unknown deployment environment: {}'.format(environment))
    if not csv_paths:
        return False
    branch = BRANCHES[environment]
    for attempt in range(3):
        with tempfile.TemporaryDirectory(prefix='rag-results-publish-') as tempdir:
            checkout = Path(tempdir) / 'checkout'
            git(['clone', '--quiet', '--depth', '1', '--single-branch',
                 '--branch', branch, clone_url, str(checkout)])
            relative_paths = []
            for csv_file in csv_paths:
                # Publish the completed CSV and its validated summary as one commit.
                for source_file in (csv_file, paired_summary(csv_file)):
                    dest = Path('rag_evaluation/results') / environment / source_file.name
                    target = checkout / dest
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(str(source_file), str(target))
                    relative_paths.append(str(dest))
            git(['-C', str(checkout), 'add', '--force', '--'] + relative_paths)
            if git(['-C', str(checkout), 'diff', '--cached', '--quiet'],
                   check=False).returncode == 0:
                print('GitHub: all completed CSVs already published to {}'.format(branch))
                return False
            git(['-C', str(checkout), '-c', 'user.name=RAG Results Bot',
                 '-c', 'user.email=rag-results-bot@users.noreply.github.com',
                 'commit', '--quiet', '-m',
                 'results({}): publish {} completed answer bundle(s)'.format(
                     environment, len(csv_paths))])
            pushed = git(['-C', str(checkout), 'push', '--quiet', push_url,
                          'HEAD:refs/heads/{}'.format(branch)],
                         env=ssh_env, check=False)
            if pushed.returncode == 0:
                print('GitHub: {} completed CSV/summary pair(s) pushed to {}'.format(len(csv_paths), branch))
                return True
            stderr = pushed.stderr.lower()
            if ('non-fast-forward' not in stderr and 'fetch first' not in stderr
                    and 'failed to push some refs' not in stderr):
                raise RuntimeError('GitHub push failed:\n{}'.format(pushed.stderr.strip()))
            if attempt == 2:
                raise RuntimeError('GitHub push conflicted after 3 retries:\n{}'.format(
                    pushed.stderr.strip()))
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Push completed *_answers.csv and matching *_summary.json pairs to the results-only GitHub branch')
    parser.add_argument('--environment', choices=sorted(BRANCHES), default='staging')
    parser.add_argument('--run-id', help='Publish only files from YYYYMMDD_HHMMSS')
    parser.add_argument('--key', default=os.environ.get('RAG_RESULTS_SSH_KEY', KEY_DEFAULT))
    parser.add_argument('--dry-run', action='store_true', help='Show files without pushing')
    args = parser.parse_args(argv)
    if args.run_id and not re.fullmatch(r'\d{8}_\d{6}', args.run_id):
        parser.error('run-id must be YYYYMMDD_HHMMSS')
    files = completed_csvs(RESULTS_DIR, args.run_id)
    if not files:
        print('No fully completed answer CSVs to publish (all rows must be OK).')
        return 0
    print('Publishing to {}:'.format(BRANCHES[args.environment]))
    for path in files:
        print('  {}'.format(path.name))
        print('  {}'.format(paired_summary(path).name))
    if args.dry_run:
        return 0
    try:
        publish(files, args.environment, ssh_env=ssh_environment(args.key))
    except (OSError, RuntimeError, ValueError) as exc:
        print('Publish failed; local CSV files are preserved: {}'.format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

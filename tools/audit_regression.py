#!/usr/bin/env python3
"""Run the full baseline differential in fresh temporary source archives.

No production worktree/runtime is used. Exit 0 means no newly failing test
identities, NOT an all-green suite, an authority approval or live readiness.
Both raw exit codes, counts and identities remain visible in the JSON receipt.
Requires a local Git checkout containing the explicit base revision.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = '9213a0e1a8e95e6fc3684e878fe8170846aba76c'


def snapshot(ref: str, destination: Path):
    payload = subprocess.check_output(['git', 'archive', '--format=zip', ref], cwd=ROOT)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            path = Path(info.filename)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('unsafe git archive member')
        archive.extractall(destination)


def run_suite(root: Path, log: Path, timeout: int) -> dict:
    with log.open('w', encoding='utf-8') as stream:
        proc = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tools/tests', '-v'],
                              cwd=root, stdout=stream, stderr=subprocess.STDOUT,
                              timeout=timeout, env={**os.environ, 'PYTHONHASHSEED': '0'})
    text = log.read_text(encoding='utf-8', errors='replace')
    found = re.findall(r'^(FAIL|ERROR): (.*)$', text, re.M)
    match = re.search(r'^Ran (\d+) tests? in ([0-9.]+)s$', text, re.M)
    if not match:
        raise RuntimeError(f'no completed unittest summary in {log.name}')
    return {'exit_code': proc.returncode, 'tests_run': int(match.group(1)),
            'seconds': float(match.group(2)), 'all_tests_passed': proc.returncode == 0,
            'failures': sum(kind == 'FAIL' for kind, _ in found),
            'errors': sum(kind == 'ERROR' for kind, _ in found),
            'failed_tests': {name: kind for kind, name in found}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default=BASE)
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--out', type=Path, required=True, help='isolated receipt directory')
    parser.add_argument('--timeout', type=int, default=900, help='per-suite seconds')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('timeout must be positive')
    args.out.mkdir(parents=True, exist_ok=True)
    base_sha = subprocess.check_output(['git', 'rev-parse', '--verify', args.base + '^{commit}'], cwd=ROOT, text=True).strip()
    head_sha = subprocess.check_output(['git', 'rev-parse', '--verify', args.head + '^{commit}'], cwd=ROOT, text=True).strip()
    try:
        with tempfile.TemporaryDirectory(prefix='axiomem-differential-') as temp:
            base, head = Path(temp) / 'base', Path(temp) / 'head'
            snapshot(base_sha, base); snapshot(head_sha, head)
            before = run_suite(base, args.out / 'baseline-unittest.log', args.timeout)
            after = run_suite(head, args.out / 'head-unittest.log', args.timeout)
        old, new = before['failed_tests'], after['failed_tests']
        introduced, fixed = sorted(new.keys() - old.keys()), sorted(old.keys() - new.keys())
        kinds = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
        report = {'schema_version': 'isolated-baseline-differential/1',
                  'baseline_sha': base_sha, 'head_sha': head_sha,
                  'before': before, 'after': after,
                  'introduced_failed_test_ids': introduced,
                  'fixed_failed_test_ids': fixed, 'changed_failure_kinds': kinds,
                  'no_new_failed_test_ids': not introduced,
                  'live_platform_evidence': False, 'authority_gate_waived': False,
                  'limitation': 'Identity-level differential only. Existing failures remain failures; same test ID does not prove unchanged failure cause. No production files, auth or CLI are supplied.'}
        (args.out / 'differential.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({k: v for k, v in report.items() if k not in {'before','after'}}, ensure_ascii=False, indent=2))
        return 0 if not introduced else 2
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        (args.out / 'differential-error.json').write_text(json.dumps({'ok':False,'error':str(exc)}, ensure_ascii=False), encoding='utf-8')
        print(f'DIFFERENTIAL INCOMPLETE: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

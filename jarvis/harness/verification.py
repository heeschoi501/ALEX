"""코딩 일꾼의 관련 시험 지도와 독립 확인. 모델의 '통과했다'는 말로 판정하지 않는다.

직접 import와 변경 함수 사용을 정적으로 연결하는 보조 검사다. 바깥 전체 회귀 검사를 대체하지 않는다.
"""
from __future__ import annotations

import ast
import copy
import fnmatch
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

PATH = re.compile(r'(?<![A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_.-]*[\\/])*[A-Za-z_][A-Za-z0-9_.-]*\.py')
VERIFY_SKIP = {'__pycache__', '.git', '.opencode', '.claude', '.codex', 'node_modules', '.pytest_cache',
               'logs', 'data', 'runtime', 'dist', 'tools', 'venv', '.venv', '.venv-alex-study',
               '.codex-remote-attachments', 'jarvis.local.json', '.env'}


def python_snapshot(root: Path) -> dict[str, bytes]:
    root = root.resolve()
    found = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in VERIFY_SKIP and not (Path(directory) / d).is_symlink()]
        for name in sorted(files):
            path = Path(directory) / name
            if name.endswith('.py') and not path.is_symlink() and root in path.resolve().parents:
                found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def source_changes(before: dict[str, bytes], root: Path, tests: dict[str, bytes]) -> dict:
    after = python_snapshot(root)
    return {rel: changed_names(before.get(rel, b'').decode('utf-8-sig', 'replace'),
                               after.get(rel, b'').decode('utf-8-sig', 'replace'))
            for rel in before.keys() | after.keys() if rel not in tests and before.get(rel) != after.get(rel)}


def verify_copy(index, workspace, paths: list[str], deadline: float) -> list[dict]:
    """실제 작업 폴더의 검사·캐시·파일은 복원/실행하지 않는다. 승인 규칙은 복사본에서도 동일하다."""
    from .tools import Workspace
    before = python_snapshot(workspace.root)
    ignored = shutil.ignore_patterns(*VERIFY_SKIP, '*.pyc', '.env.*', '*.pem', '*.key')

    def ignore(directory, names):
        return set(ignored(directory, names)) | {n for n in names if (Path(directory) / n).is_symlink()}

    with tempfile.TemporaryDirectory(prefix='alex-code-check-') as temp:
        root = Path(temp) / 'project'
        shutil.copytree(workspace.root, root, ignore=ignore)
        frozen = copy.copy(index)
        frozen.root = root.resolve()
        sandbox = Workspace(root, shell=workspace.shell, rules=list(workspace.rules or []), ask=workspace.ask,
                            cancel_event=workspace.cancel_event, read_max_lines=workspace.read_max_lines)
        checks = frozen.verify(sandbox, paths, deadline)
    if python_snapshot(workspace.root) != before:
        return [{'path': '', 'status': 'unverified', 'reason': '검사 중 작업 파일이 변경되었습니다.',
                 'output': '현재 파일의 검증 결과로 인정하지 않았습니다.'}]
    return checks


def _module(path: str) -> str:
    return path.removesuffix('.py').replace('/', '.').removesuffix('.__init__')


def _constant_targets(node) -> list[str]:
    """'NAME = 값'·'NAME: 형 = 값' 처럼 이름 하나에 넣는 대입이면 그 이름들(도움말 글·표 같은 상수)."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
    return [t.id for t in targets if isinstance(t, ast.Name)] if targets and all(isinstance(t, ast.Name) for t in targets) else []


def changed_names(before: str, after: str) -> set[str] | None:
    """함수·메서드·이름 하나짜리 상수만 바뀌었으면 그 이름(상수를 읽는 함수 이름도 — 시험은 보통 함수를 부른다).
    그 밖의 전역 문장·클래스 구조가 바뀌면 None(모듈 전체를 검사)."""
    def signatures(text):
        functions, values, other, readers = {}, {}, [], {}
        def visit(body, prefix=''):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions[prefix + node.name] = ast.dump(node, include_attributes=False)
                    readers[prefix + node.name] = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                elif isinstance(node, ast.ClassDef):
                    other.append((prefix + node.name, [ast.dump(n) for n in (*node.bases, *node.keywords, *node.decorator_list)]))
                    visit(node.body, prefix + node.name + '.')
                elif names := _constant_targets(node):
                    for name in names:
                        values[prefix + name] = ast.dump(node, include_attributes=False)
                else:
                    other.append((prefix, ast.dump(node, include_attributes=False)))
        visit(ast.parse(text).body)
        return functions, values, other, readers
    try:
        old, old_values, old_other, _ = signatures(before)
        new, new_values, new_other, readers = signatures(after)
    except (SyntaxError, ValueError):
        return None
    if old_other != new_other:
        return None
    names = {key.rsplit('.', 1)[-1] for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
    constants = {key.rsplit('.', 1)[-1] for key in old_values.keys() | new_values.keys()
                 if old_values.get(key) != new_values.get(key)}
    names |= constants | {key.rsplit('.', 1)[-1] for key, used in readers.items() if used & constants}
    return names


class TestIndex:
    def __init__(self, root: Path, prompt: str):
        self.root, self.entries, self.originals = root.resolve(), {}, {}
        for rel, data in sorted(python_snapshot(root).items()):
            if not Path(rel).name.startswith('test_'):
                continue
            self.originals[rel] = data
            try:
                tree = ast.parse(data.decode('utf-8-sig'))
            except (SyntaxError, UnicodeError):
                continue
            imports, used = set(), set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
                    imports.update(node.module + '.' + alias.name for alias in node.names)
                elif isinstance(node, ast.Name):
                    used.add(node.id)
                elif isinstance(node, ast.Attribute):
                    used.add(node.attr)
            self.entries[rel] = (imports, used)
        mentioned = {match.replace('\\', '/') for match in PATH.findall(prompt)}
        self.named = {rel for rel in self.originals if rel in mentioned or Path(rel).name in mentioned}
        self.sources = {rel for rel in mentioned if rel not in self.originals and (root / rel).is_file()
                        and self.root in (root / rel).resolve().parents}

    def select(self, changes: dict[str, set[str] | None]) -> list[str]:
        found = set(self.named)
        for rel, (imports, used) in self.entries.items():
            for source, names in changes.items():
                if _module(source) in imports and (names is None or names & used):
                    found.add(rel)
        return sorted(found)

    def hint(self) -> str:
        related = self.select({p: None for p in self.sources})
        if not related:
            return ''
        return ('\n\n[ALEX 관련 시험 지도 — 직접 import 기준, 전체 회귀 검사는 별도로 수행]\n'
                + '\n'.join('- ' + p for p in related[:20])
                + ('\n(더 많은 관련 시험은 수정 후 독립 검사에 포함됩니다.)' if len(related) > 20 else '')
                + '\n먼저 이 시험의 관련 부분과 수정할 API를 확인하세요. 기존 시험은 변경할 수 없습니다. '
                  '완료 답변 뒤 ALEX가 관련 시험을 직접 실행하며, 실패하면 실제 출력으로 재수정을 요청합니다.')

    def already_passed(self, workspace) -> dict[str, str]:
        """에이전트가 **마지막 수정 뒤에** 하네스 bash 로 직접 돌려 통과한 시험 파일 → 그 근거. 모델의 말이 아니라 실제 실행
        기록(종료 코드 0·실행 수 > 건너뜀 수)만 본다. 일부만 돌린 것(-k, 클래스·메서드 지정)은 세지 않는다."""
        passed: dict[str, str] = {}
        for run in getattr(workspace, 'test_runs', []):
            words = [str(w) for w in run.get('words') or []]
            if run.get('edits') != workspace.edits or run.get('exit') != 0 or words[:3] != ['python', '-m', 'unittest']:
                continue
            ran = re.findall(r'Ran (\d+) tests? in ', run.get('tail', ''))
            skipped = re.findall(r'OK \(skipped=(\d+)\)', run.get('tail', ''))
            if not ran or int(ran[-1]) <= (int(skipped[-1]) if skipped else 0) or '-k' in words:
                continue
            files = self._files_run(words[3:])
            for rel in files:
                passed.setdefault(rel, ' '.join(words))
        return passed

    def _files_run(self, args: list[str]) -> set[str]:
        quiet = {'-v', '-q', '--verbose', '--quiet', '-b', '--buffer'}
        if args[:1] == ['discover']:
            rest = [a for a in args[1:] if a not in quiet]
            if len(rest) % 2:
                return set()
            options = dict(zip(rest[::2], rest[1::2]))
            if set(options) - {'-s', '--start-directory', '-p', '--pattern', '-t', '--top-level-directory'}:
                return set()
            folder = options.get('-s', options.get('--start-directory', '.')).replace('\\', '/').strip('./') or ''
            pattern = options.get('-p', options.get('--pattern', 'test*.py'))
            return {rel for rel in self.originals
                    if (not folder or rel.startswith(folder + '/')) and fnmatch.fnmatch(Path(rel).name, pattern)}
        found = set()
        for arg in args:
            if arg.startswith('-'):
                if arg in quiet:
                    continue
                return set()
            rel = arg.replace('\\', '/') if arg.endswith('.py') else arg.replace('.', '/') + '.py'
            if rel not in self.originals:
                return set()   # 클래스·메서드만 고른 실행이면 파일 전체로 치지 않는다
            found.add(rel)
        return found

    def restore(self) -> None:
        for rel, data in self.originals.items():
            path = self.root / rel
            if self.root not in path.resolve().parents:
                raise ValueError('합격 검사 경로가 작업 폴더 밖으로 바뀌었습니다: ' + rel)
            if not path.exists() or path.read_bytes() != data:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

    def verify(self, workspace, paths: list[str], deadline: float, workers: int = 1) -> list[dict]:
        """workers > 1 이면 파일별로 나란히(ALEX 전체 검사 _run_parallel_suite 와 같은 방식). /code 는 허락을 물을 수 있어 1."""
        from concurrent.futures import ThreadPoolExecutor
        from ..developer import _suite_digest, _suite_report
        self.restore()
        # 같은 초에 같은 길이로 고친 소스를 이전 pyc로 검사하지 않는다. 이 격리 작업 폴더만 대상으로 한다.
        for cache in self.root.rglob('*.pyc'):
            if '__pycache__' in cache.parts and self.root in cache.resolve().parents:
                cache.unlink(missing_ok=True)

        def one(rel: str) -> dict:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {'path': rel, 'status': 'unverified', 'output': '검사 시간 예산이 끝났습니다.'}
            path = Path(rel)
            command = subprocess.list2cmdline(['python', '-m', 'unittest', 'discover', '-s', path.parent.as_posix(),
                                               '-p', path.name, '-v'])
            output = workspace.run('bash', {'command': command, 'timeout': min(120, remaining) * 1000})
            exit_codes = re.findall(r'\(exit code (-?\d+)\)', output)
            code = int(exit_codes[-1]) if exit_codes else None
            status, failures, reason = _suite_report(code, _suite_digest(output)) if code is not None else (
                'unreadable', (), '검사가 끝나지 않았거나 종료 코드를 확인하지 못했습니다.')
            count = re.findall(r'Ran (\d+) tests? in ', output)
            skip = re.findall(r'OK \(skipped=(\d+)\)', output)
            if status == 'ok' and (not count or int(count[-1]) <= (int(skip[-1]) if skip else 0)):
                status, reason = 'unreadable', '실제로 실행된 합격 검사가 없습니다.'
            return {'path': rel, 'command': command, 'exit': code,
                    'status': 'verified' if status == 'ok' else 'failed' if status == 'failed' else 'unverified',
                    'failures': sorted(failures), 'reason': reason, 'output': output[-10000:]}

        if workers > 1 and len(paths) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                return list(pool.map(one, paths))
        checks = []
        for rel in paths:
            checks.append(one(rel))
            if checks[-1]['output'] == '검사 시간 예산이 끝났습니다.':
                break
        return checks

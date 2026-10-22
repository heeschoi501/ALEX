"""코딩 일꾼의 관련 시험 지도와 독립 확인. 모델의 '통과했다'는 말로 판정하지 않는다.

직접 import와 변경 함수 사용을 정적으로 연결하는 보조 검사다. 바깥 전체 회귀 검사를 대체하지 않는다.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
import time

PATH = re.compile(r'(?<![A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_.-]*[\\/])*[A-Za-z_][A-Za-z0-9_.-]*\.py')


def _module(path: str) -> str:
    return path.removesuffix('.py').replace('/', '.').removesuffix('.__init__')


def changed_names(before: str, after: str) -> set[str] | None:
    """함수·메서드만 바뀌었으면 그 이름. 전역 값·클래스 구조 변경은 모듈 전체를 검사한다."""
    def signatures(text):
        functions, other = {}, []
        def visit(body, prefix=''):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions[prefix + node.name] = ast.dump(node, include_attributes=False)
                elif isinstance(node, ast.ClassDef):
                    other.append((prefix + node.name, [ast.dump(n) for n in (*node.bases, *node.keywords, *node.decorator_list)]))
                    visit(node.body, prefix + node.name + '.')
                else:
                    other.append((prefix, ast.dump(node, include_attributes=False)))
        visit(ast.parse(text).body)
        return functions, other
    try:
        old, old_other = signatures(before)
        new, new_other = signatures(after)
    except (SyntaxError, ValueError):
        return None
    if old_other != new_other:
        return None
    return {key.rsplit('.', 1)[-1] for key in old.keys() | new.keys() if old.get(key) != new.get(key)}


class TestIndex:
    def __init__(self, root: Path, prompt: str):
        self.root, self.entries, self.originals = root.resolve(), {}, {}
        for path in sorted(root.rglob('test_*.py')):
            if not path.is_file() or self.root not in path.resolve().parents:
                continue
            rel = path.relative_to(root).as_posix()
            if {'logs', 'runtime', 'data', 'node_modules', '__pycache__'} & set(Path(rel).parts):
                continue
            data = path.read_bytes()
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

    def restore(self) -> None:
        for rel, data in self.originals.items():
            path = self.root / rel
            if self.root not in path.resolve().parents:
                raise ValueError('합격 검사 경로가 작업 폴더 밖으로 바뀌었습니다: ' + rel)
            if not path.exists() or path.read_bytes() != data:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

    def verify(self, workspace, paths: list[str], deadline: float) -> list[dict]:
        from ..developer import _suite_digest, _suite_report
        self.restore()
        # 같은 초에 같은 길이로 고친 소스를 이전 pyc로 검사하지 않는다. 이 격리 작업 폴더만 대상으로 한다.
        for cache in self.root.rglob('*.pyc'):
            if '__pycache__' in cache.parts and self.root in cache.resolve().parents:
                cache.unlink(missing_ok=True)
        checks = []
        for rel in paths:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                checks.append({'path': rel, 'status': 'unverified', 'output': '검사 시간 예산이 끝났습니다.'})
                break
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
            checks.append({'path': rel, 'command': command, 'exit': code,
                           'status': 'verified' if status == 'ok' else 'failed' if status == 'failed' else 'unverified',
                           'failures': sorted(failures), 'reason': reason, 'output': output[-10000:]})
        return checks

"""코딩 수정안 요청에 붙이는 '이 파일이 쓰는 객체의 API' — 시그니처만 (2026-09-30 claude, ADR-072).

실측(쉬운 평가 16개의 유일한 실패, Codex 분석 docs/CODEX_EVAL_COMPARISON_20260930.md): `content = self.file_read_tool.run(path=relative_path)`
한 줄이 지워진 버그를 27b 가 `read()`·`read_file()` 처럼 없는 이름으로만 고치려다 교훈을 켜고 꺼도 모두 실패했다 — FileReadTool 의
실제 메서드를 몰랐다. 그래서 고칠 파일에서 `self.<속성> = 클래스(...)` 로 만든 객체의 클래스를 찾아, 그 클래스가 정의된 프로젝트 파일에서
공개 메서드의 **시그니처 줄**만 보여 준다(본문 없음). 실패 출력·지시와 겹치는 이름이 먼저 들어간다.

참고: Aider repo map(https://aider.chat/docs/repomap.html) — 저장소의 중요한 클래스·함수 시그니처를 그래프 순위로 골라 약 1k 토큰에 담아
"모듈이 내보내는 API 를 지도만 보고 쓰게" 한다. ALEX 는 전체 순위 대신 지금 고치는 파일이 가진 객체만 담는다.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re

API_BUDGET = 1200
_WORD = re.compile(r"[A-Za-z][a-z0-9]*|[A-Z]+(?![a-z])")


def _tokens(text: str) -> set[str]:
    """식별자를 _·대소문자 경계로 쪼갠 낱말(소문자). file_read_tool·FileReadTool → {file, read, tool}."""
    return {w.lower() for part in re.split(r"[^A-Za-z0-9]+", text) for w in _WORD.findall(part) if len(w) >= 3}


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    line = f"{'async def' if isinstance(node, ast.AsyncFunctionDef) else 'def'} {node.name}({ast.unparse(node.args)}){returns}"
    notes = []
    if node.args.kwarg is not None:  # run(**kwargs) 만으로는 인자 이름을 모른다 — 본문이 읽는 키를 적는다(path= 등)
        name = node.args.kwarg.arg
        keys = sorted({sub.slice.value if isinstance(sub, ast.Subscript) else sub.args[0].value
                       for sub in ast.walk(node)
                       if (isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) and sub.value.id == name
                           and isinstance(sub.slice, ast.Constant) and isinstance(sub.slice.value, str))
                       or (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "get"
                           and isinstance(sub.func.value, ast.Name) and sub.func.value.id == name and sub.args
                           and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str))})
        if keys:
            notes.append(f"{name}: {', '.join(keys)}")
    doc = ast.get_docstring(node)
    if doc and len(doc.splitlines()[0]) <= 80:
        notes.append(doc.splitlines()[0])
    return line + (f"  # {' / '.join(notes)}" if notes else "")


def _class_lines(tree: ast.Module, name: str) -> list[str]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            methods = [f"    {_signature(item)}" for item in node.body
                       if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and (not item.name.startswith("_") or item.name == "__init__")]
            return [f"class {name}:"] + methods
    return []


def _module_file(root: Path, relative_path: str, node: ast.ImportFrom) -> Path | None:
    """`from .tools import X` / `from jarvis.tools import X` 가 가리키는 프로젝트 안 파일."""
    if not node.module:
        return None
    if node.level:
        base = (root / relative_path).parent
        for _ in range(node.level - 1):
            base = base.parent
        candidate = base / (node.module.replace(".", "/") + ".py")
    else:
        candidate = root / (node.module.replace(".", "/") + ".py")
    try:
        candidate = candidate.resolve()
        root_resolved = root.resolve()
    except OSError:
        return None
    return candidate if candidate.is_file() and root_resolved in candidate.parents else None


_SKIP_TYPES = {"None", "Optional", "Any", "Union", "Callable", "Path", "Sequence", "Iterable", "Iterator", "Mapping"}


def _annotation_class(node: ast.AST | None) -> str:
    """타입 표시에서 클래스 이름 하나 — `Storage`, `Storage | None`, `Optional[Storage]`, `"Storage"`, `mod.Storage`."""
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return ""
    for sub in ast.walk(node):
        name = sub.id if isinstance(sub, ast.Name) else sub.attr if isinstance(sub, ast.Attribute) else ""
        if name[:1].isupper() and name not in _SKIP_TYPES:
            return name
    return ""


def _attr_types(tree: ast.Module, class_name: str = "") -> dict[str, str]:
    """`self.<속성>` 이 쥔 객체의 클래스 이름 — `self.x = Cls(...)`, `self.x: Cls = …`, 매개변수로 받은 `self.x = x`(x: Cls),
    `self.x = x or Cls(...)`. class_name 을 주면 그 클래스 안만 본다."""
    found: dict[str, str] = {}
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and (not class_name or node.name == class_name)]
    for cls in classes:
        for func in (item for item in cls.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))):
            params = {arg.arg: _annotation_class(arg.annotation)
                      for arg in func.args.args + func.args.kwonlyargs + func.args.posonlyargs}
            for node in ast.walk(func):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                kind = _annotation_class(node.annotation) if isinstance(node, ast.AnnAssign) else ""
                values = [node.value] if node.value is not None else []
                if values and isinstance(values[0], ast.BoolOp):
                    values = values[0].values
                for value in values:
                    if kind:
                        break
                    if isinstance(value, ast.Call):
                        func_name = value.func.id if isinstance(value.func, ast.Name) else \
                            value.func.attr if isinstance(value.func, ast.Attribute) else ""
                        kind = func_name if func_name[:1].isupper() else ""
                    elif isinstance(value, ast.Name):
                        kind = params.get(value.id, "")
                for target in targets:
                    if (kind and isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                            and target.value.id == "self"):
                        found.setdefault(target.attr, kind)
    return found


def _imports(root: Path, relative_path: str, tree: ast.Module) -> dict[str, Path]:
    where: dict[str, Path] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            path = _module_file(root, relative_path, node)
            for alias in node.names:
                if path is not None:
                    where[alias.asname or alias.name] = path
    return where


def api_block(root: Path, relative_path: str, text: str, focus: str, budget: int = API_BUDGET) -> str:
    """relative_path 의 코드가 쓰는 프로젝트 클래스·함수의 공개 시그니처. 없으면 "".

    2026-10-04 넓힘(claude, ADR-072 보충): 처음 판은 `self.<속성> = 클래스(...)` 만 봤다. 역사 과제(PERFORMANCE 37)의 실패는 모두 다른 모양이었다 —
    ①다른 파일에서 가져와 쓰는 함수·클래스(`build_summary_provider`, `DeveloperMode(core).approved`) ②매개변수를 거친 객체(`core: JarvisCore` 의
    `core.storage` → Storage 메서드) ③생성자 매개변수로 받은 속성(`self.storage = storage`). 셋 다 담고, 큰 클래스는 예산이 모자라면 메서드 이름만.
    """
    if not relative_path.lower().endswith(".py"):
        return ""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ""
    here = (root / relative_path).resolve()
    parsed: dict[Path, ast.Module | None] = {here: tree}
    imports: dict[Path, dict[str, Path]] = {here: _imports(root, relative_path, tree)}

    def module(path: Path) -> ast.Module | None:
        if path not in parsed:
            try:
                parsed[path] = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SyntaxError):
                parsed[path] = None
            if parsed[path] is not None:
                try:
                    imports[path] = _imports(root, path.relative_to(root.resolve()).as_posix(), parsed[path])
                except ValueError:
                    imports[path] = {}
        return parsed[path]

    def locate(name: str, origin: Path) -> tuple[Path, ast.Module] | None:
        """origin 파일에서 name 이 가리키는 정의가 있는 파일 — 그 파일에 정의돼 있거나 프로젝트에서 가져온 것."""
        tree_here = module(origin)
        if tree_here is not None and any(isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
                                         for n in tree_here.body):
            return origin, tree_here
        path = imports.get(origin, {}).get(name)
        found = module(path) if path is not None else None
        return (path, found) if found is not None else None

    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    items: list[tuple[str, str, Path, str]] = []  # (머리말, 이름, 정의 파일, 종류)
    for attr, cls in _attr_types(tree).items():  # ① self.<속성>
        spot = locate(cls, here)
        if spot:
            items.append((f"self.{attr}", cls, spot[0], "class"))
    for func in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):  # ② 매개변수.속성
        for arg in func.args.args + func.args.kwonlyargs + func.args.posonlyargs:
            owner = _annotation_class(arg.annotation)
            spot = locate(owner, here) if owner else None
            if not spot:
                continue
            accessed = {n.attr for n in ast.walk(func) if isinstance(n, ast.Attribute)
                        and isinstance(n.value, ast.Name) and n.value.id == arg.arg}
            types = _attr_types(spot[1], owner)
            for attr in sorted(accessed):
                if attr in types:
                    inner = locate(types[attr], spot[0])
                    if inner:
                        items.append((f"{arg.arg}.{attr}", types[attr], inner[0], "class"))
    reached = {name for _, name, _, _ in items}  # 속성·매개변수로 이미 잡힌 클래스는 그쪽 머리말(어떻게 쓰는지)로만 보인다
    for name, path in imports[here].items():  # ③ 가져와 쓰는 함수·클래스
        if name in used and name not in reached and module(path) is not None:
            for node in parsed[path].body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name and not name.startswith("_"):
                    items.append((f"from {path.stem}", name, path, "function"))
                elif isinstance(node, ast.ClassDef) and node.name == name:
                    items.append((f"from {path.stem}", name, path, "class"))
    if not items:
        return ""
    focus_words = _tokens(focus)

    def methods(path: Path, name: str) -> list[str]:
        for node in parsed[path].body:
            if isinstance(node, ast.ClassDef) and node.name == name:
                return [item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and not item.name.startswith("_")]
        return []

    def score(item):
        head, name, path, kind = item
        words = _tokens(head) | _tokens(name) | (set().union(*map(_tokens, methods(path, name))) if kind == "class" else set())
        return (-len(words & focus_words), -text.count(name), head, name)

    lines = ["이 파일이 쓰는 객체의 API(시그니처만, 읽기 전용 — 없는 메서드·함수를 지어내지 말 것):"]
    size_used = len(lines[0]) + 1
    shown: set[str] = set()
    for head, name, path, kind in sorted(items, key=score):
        if name in shown:
            continue
        if kind == "function":
            node = next(n for n in parsed[path].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
            block = [f"{head}: {_signature(node)}"]
        else:
            block = _class_lines(parsed[path], name)
            if len(block) < 2:  # 공개 메서드가 없는 클래스(예외 등)는 자리만 차지한다
                continue
            block[0] = f"{head} → {block[0]}"
            if size_used + sum(len(line) + 1 for line in block) > budget:  # 큰 클래스는 이름만이라도
                names = methods(path, name)
                block = [f"{head} → class {name}: 메서드 {', '.join(names)}"] if names else []
        size = sum(len(line) + 1 for line in block)
        if not block or size_used + size > budget:
            continue
        lines += block
        size_used += size
        shown.add(name)
    return "\n".join(lines) + "\n" if shown else ""

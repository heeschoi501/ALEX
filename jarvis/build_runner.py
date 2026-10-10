"""CPU build checks with runtime I/O guards; this is not an OS sandbox.

Run as an isolated Python script, not through a workspace-controlled module.
The parent accepts check evidence only for unchanged workspace sources.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import sys
import types
import unittest


# 레지스트리는 읽기만 허용한다. pygame 의 SysFont 와 표준 mimetypes 가 글꼴·형식 목록을 레지스트리에서 읽는다
# (2026-10-05: pygame 테트리스 6개 중 5개가 winreg.OpenKey 차단으로 실행 확인을 못 했다). 만들기·쓰기·지우기는 계속 막는다.
READ_ONLY_WINREG = {"winreg.OpenKey", "winreg.OpenKey/result", "winreg.EnumKey", "winreg.EnumValue", "winreg.QueryInfoKey",
                    "winreg.QueryValue", "winreg.ExpandEnvironmentStrings", "winreg.PyHKEY.Detach"}


def install_guard(workspace: Path) -> None:
    fonts = Path(os.environ.get("WINDIR") or os.environ.get("SYSTEMROOT") or r"C:\Windows") / "Fonts"
    readable = (workspace, Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve(), fonts)

    def inside(path: object, roots: tuple[Path, ...]) -> bool:
        if isinstance(path, int):
            return True  # stdout/stderr and handles already checked at open time
        try:
            resolved = Path(os.fsdecode(path)).resolve()
            return any(resolved == root or root in resolved.parents for root in roots)
        except (TypeError, ValueError, OSError):
            return False

    def guard(event: str, args: tuple) -> None:
        if event == "open":
            path, mode, flags = args
            writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if not inside(path, (workspace,) if writing else readable):
                raise PermissionError("ALEX 실행 보호: 작업 폴더 밖 파일 접근")
        elif event in {"os.listdir", "os.scandir", "os.chdir"}:
            if not inside(args[0] if args and args[0] is not None else ".", readable):
                raise PermissionError("ALEX 실행 보호: 작업 폴더 밖 탐색")
        elif event == "os.mkdir":
            if not inside(args[0], (workspace,)):
                raise PermissionError("ALEX 실행 보호: 작업 폴더 밖 폴더 생성")
        elif event in {"os.remove", "os.rmdir", "os.rename", "os.link", "os.symlink", "os.chmod",
                       "os.truncate", "os.system", "os.exec", "os.posix_spawn", "os.fork",
                       "os.forkpty", "os.kill", "os.killpg", "os.startfile", "os.startfile/2"}:
            raise PermissionError(f"ALEX 실행 보호: {event} 금지")
        elif event.startswith(("socket.", "subprocess.", "winreg.", "ctypes.")):
            if event in READ_ONLY_WINREG:
                return
            raise PermissionError(f"ALEX 실행 보호: {event} 금지")

    sys.addaudithook(guard)


def main() -> None:
    target, workspace, evidence = (Path(arg).resolve() for arg in sys.argv[1:4])
    if workspace not in target.parents or not target.is_file():
        raise ValueError("검사 파일은 작업 폴더 안에 있어야 합니다.")
    # Open the evidence handle before installing the guard. Generated code cannot
    # open this path; even an early os._exit(0) leaves no positive check evidence.
    with evidence.open("w", encoding="utf-8") as record:
        checks = 0

        def checked(value):
            nonlocal checks
            checks += 1
            return value

        class Assertions(ast.NodeTransformer):
            def visit_Assert(self, node):
                self.generic_visit(node)
                node.test = ast.copy_location(ast.Call(func=ast.Name(id="__alex_checked__", ctx=ast.Load()),
                                                      args=[node.test], keywords=[]), node.test)
                return node

        # unittest assertions execute in the library rather than the target AST.
        case_file = str(Path(unittest.case.__file__).resolve())
        def count_unittest(frame, event, arg):
            nonlocal checks
            if event == "call" and frame.f_code.co_filename == case_file and frame.f_code.co_name.startswith("assert"):
                checks += 1

        sys.argv = [str(target)]
        sys.path[:0] = [str(target.parent), str(workspace)]
        install_guard(workspace)
        try:
            tree = Assertions().visit(ast.parse(target.read_text(encoding="utf-8"), filename=str(target)))
            ast.fix_missing_locations(tree)
            module = types.ModuleType("__main__")
            module.__file__ = str(target)
            module.__dict__["__alex_checked__"] = checked
            # unittest.main() discovers tests from the real __main__ module.
            sys.modules["__main__"] = module
            sys.setprofile(count_unittest)
            exec(compile(tree, str(target), "exec", optimize=0), module.__dict__)
        finally:
            sys.setprofile(None)
            json.dump({"checks": checks}, record)


if __name__ == "__main__":
    main()

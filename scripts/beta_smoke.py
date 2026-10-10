"""Offline smoke test for v0.2.0 Beta-only state/edit features."""
from __future__ import annotations

from pathlib import Path
import tempfile
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.portable import export_portable_state, import_portable_state
from jarvis.storage import Storage
from jarvis.workspace import SafeWorkspaceEditor


def check(label: str, condition: bool) -> None:
    if not condition:
        raise RuntimeError(label)
    print(f"[OK] {label}")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = root / "project"
        project.mkdir()
        source = project / "demo.py"
        source.write_text("value = 1\n", encoding="utf-8")

        a = Storage(root / "a.db")
        a.remember("preferred_mode", "local-first")
        task_id = a.add_task("validate RTX 4070", 1)
        check("task persisted", a.list_tasks()[0].task_id == task_id)
        a.set_state("active_project_relative", "project")
        check("app state persisted", a.get_state("active_project_relative") == "project")

        editor = SafeWorkspaceEditor(project, root / "workspace_backups")
        proposal = editor.create_proposal("demo.py", "value = 2\n")
        check("edit proposal is non-destructive", source.read_text(encoding="utf-8") == "value = 1\n")
        editor.apply(proposal.proposal_id)
        check("approved edit applied", source.read_text(encoding="utf-8") == "value = 2\n")
        editor.restore_latest("demo.py")
        check("edit rollback restored original", source.read_text(encoding="utf-8") == "value = 1\n")

        bundle = root / "portable.json"
        export_portable_state(a, bundle)
        b = Storage(root / "b.db")
        stats = import_portable_state(b, bundle)
        check("portable memory imported", b.recall("preferred_mode") == "local-first")
        check("portable task imported", stats["tasks_added"] == 1 and len(b.list_tasks()) == 1)

    print("\nBeta smoke test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Portable local state bundles for moving ALEX state between devices.

This is deliberately *not* automatic sync. It provides a small, inspectable
JSON format for long-term memories and tasks so Laptop/Desktop can exchange
state without copying a live SQLite database over a network share.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .storage import Storage, StorageError

BUNDLE_FORMAT = "project-jarvis-portable-state"
# v2 에서 experiences 를 추가했다. v1 번들도 계속 읽는다 — 이미 만든 파일을 못 읽게 하면
# 기기 이동이라는 목적 자체가 깨진다.
BUNDLE_VERSION = 2
SUPPORTED_VERSIONS = (1, 2)
MAX_EXPERIENCES = 2000
MAX_BUNDLE_BYTES = 5_000_000


class PortableStateError(RuntimeError):
    pass


def export_portable_state(storage: Storage, destination: Path) -> Path:
    target = Path(destination)
    memories = storage.list_memories(limit=100)
    tasks = [asdict(item) for item in storage.list_tasks(include_done=True, limit=500)]
    # 경험은 **id 와 split 을 그대로** 싣는다. 받는 쪽에서 새로 기록하면 id 가 바뀌고
    # split 도 바뀌어, 같은 내용이 한 기기에서는 학습용 다른 기기에서는 평가용이 된다.
    experiences = [asdict(item) for item in storage.list_experiences(limit=MAX_EXPERIENCES)]
    payload = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "memories": memories,
        "tasks": tasks,
        "experiences": experiences,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise PortableStateError("Portable state가 5MB를 초과하여 export를 중단했습니다.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
    except OSError as exc:
        raise PortableStateError(f"Portable state export 실패: {exc}") from exc
    return target


def _load_bundle(source: Path) -> dict[str, Any]:
    path = Path(source)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PortableStateError(f"Portable state 파일을 읽을 수 없습니다: {exc}") from exc
    if size <= 0 or size > MAX_BUNDLE_BYTES:
        raise PortableStateError("Portable state 파일 크기가 허용 범위를 벗어났습니다.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortableStateError(f"Portable state JSON이 올바르지 않습니다: {exc}") from exc
    if not isinstance(payload, dict):
        raise PortableStateError("Portable state 최상위 값은 JSON object여야 합니다.")
    if payload.get("format") != BUNDLE_FORMAT or payload.get("version") not in SUPPORTED_VERSIONS:
        raise PortableStateError("지원하지 않는 Portable state 형식/버전입니다.")
    if not isinstance(payload.get("memories", {}), dict) or not isinstance(payload.get("tasks", []), list):
        raise PortableStateError("Portable state memories/tasks 형식이 올바르지 않습니다.")
    if not isinstance(payload.get("experiences", []), list):
        raise PortableStateError("Portable state experiences 형식이 올바르지 않습니다.")
    return payload


def import_portable_state(storage: Storage, source: Path) -> dict[str, int]:
    """Merge bundle conservatively.

    Existing memory keys win; the imported file never silently overwrites a
    local memory. Tasks are de-duplicated by normalized text + status.
    """
    payload = _load_bundle(Path(source))
    stats = {"memories_added": 0, "memory_conflicts": 0, "tasks_added": 0, "tasks_skipped": 0,
             "experiences_added": 0, "experiences_skipped": 0, "experiences_rejected": 0}
    existing_memories = storage.list_memories(limit=100)
    for raw_key, raw_value in payload.get("memories", {}).items():
        key, value = str(raw_key).strip(), str(raw_value)
        if not key:
            continue
        if key in existing_memories:
            stats["memory_conflicts"] += 1
            continue
        try:
            storage.remember(key, value)
        except StorageError as exc:
            raise PortableStateError(f"Memory import 실패 ({key}): {exc}") from exc
        stats["memories_added"] += 1

    current = {(item.text.strip().casefold(), item.status) for item in storage.list_tasks(include_done=True, limit=500)}
    for raw in payload.get("tasks", []):
        if not isinstance(raw, dict):
            stats["tasks_skipped"] += 1
            continue
        text = str(raw.get("text", "")).strip()
        status = str(raw.get("status", "open"))
        try:
            priority = int(raw.get("priority", 2))
        except (TypeError, ValueError):
            priority = 2
        key = (text.casefold(), status)
        if not text or key in current or priority not in {1, 2, 3} or status not in {"open", "done"}:
            stats["tasks_skipped"] += 1
            continue
        try:
            task_id = storage.add_task(text, priority)
            if status == "done":
                storage.set_task_status(task_id, "done")
        except StorageError as exc:
            raise PortableStateError(f"Task import 실패 ({text[:40]}): {exc}") from exc
        current.add(key)
        stats["tasks_added"] += 1

    # 경험은 id 를 보존해 넣는다. 이미 있으면 건너뛰고, content_hash 가 본문과 어긋나면
    # 옮기는 중에 변조된 것이므로 **그 한 건만 거부하고 나머지는 계속 받는다** —
    # 파일 하나가 깨졌다고 전체 이동이 실패하면 기기 이동이 불가능해진다.
    for raw in payload.get("experiences", []):
        if not isinstance(raw, dict):
            stats["experiences_rejected"] += 1
            continue
        try:
            added = storage.import_experience(raw)
        except StorageError:
            stats["experiences_rejected"] += 1
            continue
        stats["experiences_added" if added else "experiences_skipped"] += 1
    return stats

"""OpenCode 무료 모델 목록(2026-10-23) — OpenCode v1.18.34 provider.ts 와 같은 출처·같은 거름.

- 출처: models.dev 의 api.json 'opencode'(OpenCode Zen) 공급자. 계정 키가 없을 때 OpenCode 는 입력 비용 0 인 모델만 남기고(provider.ts
  opencode 로더), 지원 종료(deprecated)·실험(alpha) 모델은 목록에서 뺀다. 여기에 ALEX 가 쓰려면 도구 호출(tool_call)이 되는 것만.
- 무료 단계는 **OpenCode 안에서만** 쓸 수 있다 — 밖에서 같은 주소를 부르면 403 FreeTierError(2026-10-23 확인). 그래서 ALEX 는
  opencode.exe(OpenCodeWorker)로 쓴다. OpenCode 인 척하며 직접 부르지 않는다.
- 목록은 하루 한 번 새로 받아 data 폴더에 둔다(인터넷이 없으면 마지막으로 받은 것).
"""
from __future__ import annotations

import json
from pathlib import Path
import time
import urllib.request

CATALOG_URL = "https://models.dev/api.json"
CACHE_SECONDS = 24 * 3600
PREFIX = "opencode/"


def _download() -> dict:
    request = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "ALEX (models list)"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def free_models(catalog: dict) -> list[dict]:
    """OpenCode 가 키 없이 보여 주는 모델 중 ALEX 가 쓸 수 있는 것 — id 는 'opencode/<모델>'."""
    provider = catalog.get("opencode") if isinstance(catalog, dict) else None
    out = []
    for key, model in ((provider or {}).get("models") or {}).items():
        if not isinstance(model, dict):
            continue
        cost = model.get("cost") or {}
        if cost.get("input", 1) != 0 or model.get("status") in ("deprecated", "alpha") or not model.get("tool_call"):
            continue
        limit = model.get("limit") or {}
        out.append({"id": PREFIX + key, "name": str(model.get("name") or key), "context": int(limit.get("context") or 0),
                    "output": int(limit.get("output") or 0), "reasoning": bool(model.get("reasoning"))})
    return sorted(out, key=lambda m: m["name"].lower())


def load_free_models(cache: Path, download=None, now=time.time) -> list[dict]:
    """하루 넘은 목록이면 새로 받는다. 못 받으면 마지막 목록(없으면 빈 목록)."""
    download = download or _download  # 부를 때 찾는다(시험이 _download 를 바꿔 끼울 수 있게)
    cache = Path(cache)
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = None
    if isinstance(saved, dict) and now() - float(saved.get("at") or 0) < CACHE_SECONDS:
        return list(saved.get("models") or [])
    try:
        models = free_models(download())
    except Exception:  # noqa: BLE001 — 인터넷 없음·형식 바뀜이면 마지막 목록
        return list((saved or {}).get("models") or [])
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"at": now(), "models": models}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return models


def is_free_model(model: str) -> bool:
    return str(model or "").startswith(PREFIX)

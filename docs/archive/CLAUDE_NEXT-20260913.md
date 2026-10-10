# Claude Handoff — Project JARVIS v0.2.0 Local Beta

## CURRENT STATUS

GPT continued from Claude v0.1.1 through v0.1.2/v0.1.3 and now v0.2.0.

v0.2.0 is intended to be a **daily-testable Local Beta**, not a movie-style finished assistant. It has real local/provider/session/memory/project/task/edit flows. Physical GPU/audio/network features still require target machines.

Authoritative baseline before handoff:

```text
257/257 unit tests PASS
Router golden corpus 29/29
Security smoke 11/11
Alpha smoke PASS
Beta smoke PASS
Python 3.10 syntax audit PASS
Dependency audit PASS
compileall PASS
```

Run `python scripts/verify_release.py` first. If the count differs because GPT added tests after this handoff file was written, trust the repository/test output and update this document.

## CHANGED / IMPORTANT FILES

Core:

- `jarvis/core.py` — routing, context, cloud gate, local file/project QA, edit proposal orchestration
- `jarvis/storage.py` — schema v4, sessions, summaries, memory, route feedback, Task, app state
- `jarvis/workspace.py` — approval-based atomic source edit + backup/undo
- `jarvis/portable.py` — Memory+Task device-transfer bundle
- `jarvis/tools.py` — bounded file/project tools
- `jarvis/providers.py` — Mock/Ollama/OpenAI/Anthropic/Gemini
- `jarvis/hardware.py` — Laptop/Desktop/WSL profile
- `jarvis/device.py` — opt-in fixed-target WOL + fixed-argv VS Code launcher
- `jarvis/app.py` — CLI and commands
- `jarvis/web.py` — localhost-only Web UI

Verification:

- `scripts/verify_release.py`
- `scripts/alpha_smoke.py`
- `scripts/beta_smoke.py`
- `scripts/security_smoke.py`
- `scripts/router_eval.py`
- `scripts/archive_audit.py`
- `scripts/package_release.py`
- `scripts/benchmark_local.py`
- `scripts/check_environment.py`

Docs:

- `README.md`
- `SECURITY.md`
- `DECISIONS.md`
- `TODO.md`
- `TEST_RESULTS.md`
- `HARDWARE_VALIDATION.md`
- `RELEASE_NOTES.md`

## NEW v0.2.0 USER FLOWS

### Safe edit

```text
/edit-file path.py :: instruction
→ Local model only
→ proposal id + unified diff
→ NO WRITE
/apply-edit <id> --yes
→ original backup
→ atomic replacement
/undo-file path.py --yes
```

Never change this to automatic model-authorized writes.

### Task

```text
/task-add 1 실제 4070 벤치마크
/tasks
/task-done 1
```

### Portable state

```text
/state-export transfer.json
/state-import transfer.json --yes
```

Existing Memory key wins on import.

## DO NOT REGRESS

1. No arbitrary shell execution.
2. No automatic model file writes.
3. No Cloud call before approval in CLI.
4. Sensitive hard-block remains active even if approval is disabled.
5. Web must stay localhost-only and Cloud-denied by design.
6. Persistent Memory/Summary stay out of Cloud context by default.
7. Automatic project retrieval skips credential-like filenames.
8. Keep stdlib-only mandatory runtime unless a dependency is justified.
9. Keep Python >=3.10 compatibility.
10. Do not invent hardware benchmark numbers.

## FIRST CLAUDE TASK — REAL LAPTOP

On the user's RTX 4070 Laptop:

```bash
python scripts/verify_release.py
python scripts/check_environment.py
python scripts/setup_models.py
python scripts/setup_models.py --pull
python scripts/benchmark_local.py --repeat 3
```

Then record actual results in `HARDWARE_VALIDATION.md`.

Manual tests:

1. 30+ turn conversation
2. rolling summary
3. restart/resume
4. Memory save/recall
5. Task add/done/reopen
6. `/ask-file`
7. `/ask-project`
8. `/edit-file` on a disposable test file, inspect diff, apply, undo
9. localhost Web UI
10. portable state export into a disposable second DB/project
11. optional: configure/test `/open-vscode --yes`
12. optional: configure/test `/wake-desktop --yes` only with the real desktop MAC

Do not test edit on important production source before disposable-file validation.

## SECOND CLAUDE TASK — DESKTOP

Repeat on RTX 5080 Desktop. Record:

- detected profile
- selected models
- first-token latency
- total latency
- tokens/sec
- model load duration
- VRAM before/after
- 8B vs 14B qualitative difference
- General↔Coder model switch latency

## AFTER HARDWARE PASS

Only then decide whether to implement next:

- Voice STT/TTS
- authenticated Remote Worker + WOL
- real multi-device state sync
- restricted approved process runner
- semantic/vector retrieval

Do not add LangGraph simply because it is available. Add it only if a real workflow now needs stateful branching/retries that the current small orchestrator cannot cleanly express.

## HANDOFF FORMAT BACK TO GPT

Update repository files, not only chat:

```text
[CURRENT STATUS]
[CHANGED FILES]
[DECISIONS]
[TEST RESULT]
[HARDWARE RESULT]
[KNOWN ISSUES]
[NEXT TASK]
```

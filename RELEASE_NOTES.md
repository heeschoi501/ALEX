# Project JARVIS v0.2.0 — Release Notes

## 성격

v0.1.3 Local Alpha를 기반으로, 실제 개인용 코딩/비서 사용에 필요한 **승인 기반 파일 편집, Task 상태, project 복원, portable device state**를 추가한 Local Beta다.

## v0.2.0 신규 기능

- `SafeWorkspaceEditor`
  - 기존 UTF-8 텍스트/코드 파일만 편집
  - Local model proposal only
  - unified diff
  - explicit `/apply-edit ... --yes`
  - stale proposal 거부
  - 원본 자동 백업
  - atomic replace
  - `/undo-file`
- SQLite Task
  - priority 1/2/3
  - open/done
  - add/list/reopen/delete
- active project relative path persistence
- portable state JSON
  - Long-term Memory
  - Task
  - conservative merge
- `/status`
- opt-in fixed-target Wake-on-LAN
- opt-in fixed-argv VS Code launcher (no shell)
- Python edit proposal AST syntax validation
- schema version 4
- Beta-specific offline smoke test

## 유지된 핵심 안전장치

- Cloud approval + sensitive hard block 독립
- persistent Memory/Summary Cloud 기본 제외
- Web Cloud 강제 승인/거부
- Web DNS rebinding Host 방어
- remote Ollama opt-in
- file traversal/symlink escape 방어
- release archive sensitive/runtime-state audit
- ResourceWarning-as-error unit test

## 검증 상태

최종 수치는 `TEST_RESULTS.md`가 authoritative하다.

## 실제 장비에서 남은 검증

RTX 4070 Laptop / RTX 5080 Desktop에서 `HARDWARE_VALIDATION.md` 실행. 이 저장소에서는 GPU benchmark 수치를 추정하지 않는다.

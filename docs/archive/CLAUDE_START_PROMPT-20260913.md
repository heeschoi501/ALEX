# Claude Code Start Prompt — Project JARVIS v0.2.0

You are continuing an existing repository. Do not redesign it from memory or chat history.

Read in this order:

1. `PROJECT_JARVIS.md`
2. `README.md`
3. `CLAUDE_NEXT.md`
4. `DECISIONS.md`
5. `SECURITY.md`
6. `TODO.md`
7. `TEST_RESULTS.md`
8. `HARDWARE_VALIDATION.md`

Then run:

```bash
python scripts/verify_release.py
```

Do not modify code until the current baseline is understood.

The first useful work is **real RTX 4070 Laptop validation**, not another architecture rewrite. Never fabricate benchmark values.

Preserve these invariants:

- model output cannot directly write files
- `/edit-file` only proposes; explicit apply is separate
- no arbitrary shell execution
- Cloud approval/privacy gate stays intact
- Web stays localhost-only and Cloud-denied
- no persistent Memory/Summary in Cloud by default
- stdlib-only mandatory runtime
- Python >=3.10
- tests before/after substantial changes

After laptop validation, repeat on RTX 5080 Desktop and record measured data in the repository.

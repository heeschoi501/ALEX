# Project JARVIS v0.2.0 — Gemini Independent Review Request

You are the independent reviewer. Do not merely agree with GPT/Claude.

Read `PROJECT_JARVIS.md`, current source, `README.md`, `DECISIONS.md`, `SECURITY.md`, `TEST_RESULTS.md`, `TODO.md`, and `HARDWARE_VALIDATION.md`.

## Review format

A. Overall score 1–5
B. Critical issues
C. Important issues
D. Over-engineering
E. Missing pieces
F. Security
G. Performance
H. Simplified proposal
I. Concrete actions for GPT + Claude

## v0.2.0 focus

Critically inspect:

1. `jarvis/workspace.py`
   - traversal/symlink behavior
   - stale proposal check
   - full-file replacement safety
   - backup/atomic replace/undo
   - whether proposal output parsing can cause destructive surprises
2. `jarvis/portable.py`
   - conflict policy
   - malformed/oversized JSON
   - whether Memory/Task export creates privacy risks
3. SQLite schema v4 and upgrade behavior
4. project relative-path restore boundary
5. Cloud sensitive-data gate and provider adapters
6. Web localhost assumptions / DNS rebinding defense
7. Laptop 8GB and Desktop 16GB default models
8. Whether v0.2.0 has become too broad
9. Whether a restricted process runner should remain excluded
10. Whether lexical retrieval is still sufficient before adding vector DB

Do not accept claimed GPU performance without actual `HARDWARE_VALIDATION.md` measurements.

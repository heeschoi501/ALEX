"""ALEX 코딩 하네스의 지시문·도구 설명서(ADR-112) — OpenCode 의 기본 지시문(default.txt — Qwen 같은 모델에 OpenCode 가 쓰는 것)과
도구 설명서를 그대로 쓰고, 이름·도움말 줄만 ALEX 로 바꾼다. 끝에 환경(<env>)·스킬 목록·작업 지시(AGENTS.md/CLAUDE.md/CONTEXT.md·
ALEX 규칙·교훈)·언어를 붙인다. 모드: build(기본)·plan(읽기만 하며 계획 — OpenCode plan.txt)·explore/general(하위 에이전트).
"""
from __future__ import annotations

from datetime import date
import platform
from pathlib import Path

from . import opencode_text as oc

_DROP = (
    "If the user asks for help or wants to give feedback inform them of the following:\n"
    "- /help: Get help with using opencode\n"
    "- To give feedback, users should report the issue at https://github.com/anomalyco/opencode/issues\n\n"
    "When the user directly asks about opencode (eg 'can opencode do...', 'does opencode have...') or asks in second "
    "person (eg 'are you able...', 'can you do...'), first use the WebFetch tool to gather information to answer the "
    "question from opencode docs at https://opencode.ai\n\n"
)
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "CONTEXT.md")  # OpenCode instruction.ts — 처음 찾은 것 하나
SUBAGENTS = {
    "general": "General-purpose agent for researching complex questions and executing multi-step tasks. Use this agent "
               "to execute multiple units of work in parallel.",
    "explore": "Fast agent specialized for exploring codebases. Use this when you need to quickly find files by patterns "
               "(eg. \"src/components/**/*.tsx\"), search code for keywords (eg. \"API endpoints\"), or answer questions "
               "about the codebase (eg. \"how do API endpoints work?\"). When calling this agent, specify the desired "
               "thoroughness level: \"quick\" for basic searches, \"medium\" for moderate exploration, or \"very thorough\" "
               "for comprehensive analysis across multiple locations and naming conventions.",
}
MODE_TOOLS = {
    "build": ["read", "edit", "write", "apply_patch", "glob", "grep", "bash", "todowrite", "task", "question", "skill",
              "webfetch", "websearch"],
    "plan": ["read", "glob", "grep", "todowrite", "task", "question", "skill", "webfetch", "websearch"],
    "general": ["read", "edit", "write", "apply_patch", "glob", "grep", "bash", "skill", "webfetch", "websearch"],
    "explore": ["read", "glob", "grep", "webfetch", "websearch"],
}


def base_prompt() -> str:
    text = oc.DEFAULT_PROMPT
    assert _DROP in text, "OpenCode default.txt 모양이 바뀌었다 — opencode_text.py 를 다시 만들 때 확인"
    text = text.replace(_DROP, "")
    text = text.replace("You are opencode, an interactive CLI tool that helps users with software engineering tasks.",
                        "You are ALEX, a local coding agent that helps the user with software engineering tasks.")
    text = text.replace("Remember that your output will be displayed on a command line interface.",
                        "Remember that your output will be displayed in the ALEX chat window.")
    return text


def instruction_file(root: Path) -> Path | None:
    return next((Path(root) / name for name in INSTRUCTION_FILES if (Path(root) / name).is_file()), None)


def system_prompt(root: Path, *, rules: str = "", lessons: str = "", mode: str = "build",
                  skills: dict | None = None) -> str:
    root = Path(root)
    git = (root / ".git").exists()
    parts = [oc.EXPLORE_PROMPT if mode == "explore" else base_prompt()]
    if mode != "explore":
        parts.append("\n# Language\nWrite your final message to the user in Korean (the user's language). Keep code, "
                     "file paths, identifiers and commands as they are.")
    parts.append("\nHere is some useful information about the environment you are running in:\n<env>\n"
                 f"  Working directory: {root}\n  Is directory a git repo: {'yes' if git else 'no'}\n"
                 f"  Platform: {'win32' if platform.system() == 'Windows' else platform.system().lower()}\n"
                 f"  Today's date: {date.today():%a %b %d %Y}\n</env>")
    if skills and mode != "explore":
        parts.append("\n<available_skills>\n" + "\n".join(
            f"  <skill>\n    <name>{s.name}</name>\n    <description>{s.description}</description>\n  </skill>"
            for s in skills.values()) + "\n</available_skills>")
    found = instruction_file(root)
    if found is not None:
        parts.append("\nInstructions from: " + str(found) + "\n" + found.read_text(encoding="utf-8", errors="replace")[:8000])
    if rules:
        parts.append("\n# Workspace rules (from ALEX — these override other instructions)\n" + rules.strip())
    if lessons:
        parts.append("\n# Lessons ALEX learned before (apply only when relevant)\n" + lessons.strip())
    return "\n".join(parts)


def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": properties,
                                                            "required": required}}}


def bash_description(allowed: list[str], timeout: float) -> str:
    listed = ", ".join(f"`{p}`" for p in allowed) if allowed else "(none — every command needs the user's approval)"
    return ("Executes a given command in the workspace directory with optional timeout.\n\n"
            "IMPORTANT: This tool is for running programs such as tests. DO NOT use it for file operations (reading, "
            "writing, editing, searching, finding files) - use the specialized tools for this instead.\n\n"
            "Usage notes:\n"
            f"- Commands that run without asking (`*` matches any arguments): {listed}\n"
            "- Run one command per call. Pipes, redirection, `&&`, `;` and command substitution are not supported.\n"
            f"- You can specify an optional timeout in milliseconds (default {int(timeout * 1000)}ms).\n"
            "- The output includes the exit code. If the output is long, only the end is shown and the full output is "
            "saved to a file you can read.\n"
            "- After changing code, run the relevant tests with this tool and fix failures based on the actual output.")


def tool_specs(names: list[str], *, allowed: list[str], timeout: float, subagents: dict[str, str] | None = None,
               extra: list[dict] | None = None) -> list[dict]:
    todo_item = {"type": "object", "properties": {
        "content": {"type": "string", "description": "Brief description of the task"},
        "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"],
                   "description": "Current status of the task"},
        "priority": {"type": "string", "enum": ["high", "medium", "low"], "description": "Priority level of the task"}},
        "required": ["content", "status"]}
    agents = subagents if subagents is not None else SUBAGENTS
    question_item = {"type": "object", "properties": {
        "question": {"type": "string", "description": "The complete question to ask"},
        "header": {"type": "string", "description": "Very short label (max 30 chars)"},
        "options": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string", "description": "Display text (1-5 words)"},
            "description": {"type": "string", "description": "Explanation of the choice"}}, "required": ["label"]}},
        "multiple": {"type": "boolean", "description": "Allow selecting multiple choices"}},
        "required": ["question", "options"]}
    specs = {
        "read": _function("read", oc.READ, {
            "filePath": {"type": "string", "description": "The absolute path to the file or directory to read"},
            "offset": {"type": "integer", "description": "The line number to start reading from (1-indexed)"},
            "limit": {"type": "integer", "description": "The maximum number of lines to read (defaults to 2000)"}},
            ["filePath"]),
        "edit": _function("edit", oc.EDIT, {
            "filePath": {"type": "string", "description": "The absolute path to the file to modify"},
            "oldString": {"type": "string", "description": "The text to replace"},
            "newString": {"type": "string", "description": "The text to replace it with (must be different from oldString)"},
            "replaceAll": {"type": "boolean", "description": "Replace all occurrences of oldString (default false)"}},
            ["filePath", "oldString", "newString"]),
        "write": _function("write", oc.WRITE, {
            "filePath": {"type": "string", "description": "The absolute path to the file to write (must be absolute, not relative)"},
            "content": {"type": "string", "description": "The content to write to the file"}},
            ["filePath", "content"]),
        "apply_patch": _function("apply_patch", oc.APPLY_PATCH, {
            "patchText": {"type": "string", "description": "The full patch text that describes all changes to be made"}},
            ["patchText"]),
        "glob": _function("glob", oc.GLOB, {
            "pattern": {"type": "string", "description": "The glob pattern to match files against"},
            "path": {"type": "string", "description": "The directory to search in. If not specified, the current working "
                     "directory will be used. Omit this field to use the default directory."}},
            ["pattern"]),
        "grep": _function("grep", oc.GREP, {
            "pattern": {"type": "string", "description": "The regex pattern to search for in file contents"},
            "path": {"type": "string", "description": "The directory to search in. Defaults to the current working directory."},
            "include": {"type": "string", "description": 'File pattern to include in the search (e.g. "*.js", "*.{ts,tsx}")'}},
            ["pattern"]),
        "bash": _function("bash", bash_description(allowed, timeout), {
            "command": {"type": "string", "description": "The command to execute"},
            "timeout": {"type": "integer", "description": "Optional timeout in milliseconds"},
            "description": {"type": "string", "description": "Clear, concise description of what this command does in 5-10 words"}},
            ["command"]),
        "todowrite": _function("todowrite", oc.TODOWRITE, {
            "todos": {"type": "array", "items": todo_item, "description": "The updated todo list"}}, ["todos"]),
        "task": _function("task", oc.TASK + "\n\nAvailable agent types and the tools they have access to:\n"
                          + "\n".join(f"- {name}: {text}" for name, text in agents.items()), {
            "description": {"type": "string", "description": "A short (3-5 words) description of the task"},
            "prompt": {"type": "string", "description": "The task for the agent to perform"},
            "subagent_type": {"type": "string", "enum": sorted(agents), "description": "The type of specialized agent to use for this task"},
            "task_id": {"type": "string", "description": "Only set this to resume a previous task (continues the same subagent session)"}},
            ["description", "prompt", "subagent_type"]),
        "question": _function("question", oc.QUESTION, {
            "questions": {"type": "array", "items": question_item, "description": "Questions to ask"}}, ["questions"]),
        "skill": _function("skill", oc.SKILL, {
            "name": {"type": "string", "description": "The name of the skill from available_skills"}}, ["name"]),
        "webfetch": _function("webfetch", oc.WEBFETCH, {
            "url": {"type": "string", "description": "The URL to fetch content from"},
            "format": {"type": "string", "enum": ["markdown", "text", "html"], "description": "The format to return the content in"}},
            ["url"]),
        "websearch": _function("websearch", oc.WEBSEARCH, {
            "query": {"type": "string", "description": "Websearch query"}}, ["query"]),
    }
    return [specs[name] for name in names if name in specs] + list(extra or [])


MAX_STEPS_PROMPT = """CRITICAL - MAXIMUM STEPS REACHED

The maximum number of steps allowed for this task has been reached. Tools are disabled until next user input. Respond with text only.

STRICT REQUIREMENTS:
1. Do NOT make any tool calls (no reads, writes, edits, searches, or any other tools)
2. MUST provide a text response summarizing work done so far
3. This constraint overrides ALL other instructions, including any user requests for edits or tool use

Response must include:
- Statement that maximum steps for this agent have been reached
- Summary of what has been accomplished so far
- List of any remaining tasks that were not completed"""

DOOM_LOOP_REMINDER = ("<system-reminder>You have called the same tool with the same arguments several times in a row "
                      "and got the same result. Stop repeating it. Re-read the error or output, change your approach, "
                      "or finish with a summary of what is blocking you.</system-reminder>")

COMPACTION_REQUEST = """Summarize the conversation so far so another coding agent can continue the work. Use exactly these sections:

## Goal
## Instructions and constraints
## Progress (what is done, with exact file paths)
## Key findings (errors, test results, decisions)
## Remaining work (next concrete steps)

Keep exact file paths, function names, commands and error messages. Prefer terse bullets."""


def tools(allowed: tuple[str, ...], timeout: float) -> list[dict]:
    """예전 이름(시험·호환용) — build 모드의 기본 7개."""
    return tool_specs(["read", "edit", "write", "glob", "grep", "bash", "todowrite"], allowed=list(allowed), timeout=timeout)

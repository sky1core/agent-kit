#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


SHARED_RULE = "AGENTS.md"
LOCAL_RULE = "AGENTS.local.md"
CLAUDE_SHARED_BRIDGE = "CLAUDE.md"
CLAUDE_LOCAL_BRIDGE = "CLAUDE.local.md"
KIRO_STEERING_REL = ".kiro/steering/agents-local-overlay.md"
KIRO_STEERING_PATHSPEC = ":(icase,literal).kiro/steering/agents-local-overlay.md"
LOCAL_RULE_PATHSPEC = ":(icase,literal)AGENTS.local.md"
CLAUDE_LOCAL_BRIDGE_PATHSPEC = ":(icase,literal)CLAUDE.local.md"
DEFAULT_CODEX_MAX_CHARS = 32768
DEFAULT_CLAUDE_MAX_CHARS = 10000
DEFAULT_RAW_MAX_CHARS = 32768
CODEX_PROJECT_DOC_MAX_BYTES = 32768
CODEX_PROJECT_DOC_MAX_BYTES_ENV = "AGENTS_OVERLAY_CODEX_PROJECT_DOC_MAX_BYTES"


class OverlayError(Exception):
    pass


class QuietExit(Exception):
    pass


@dataclass(frozen=True)
class RepoContext:
    start: Path
    top: Path
    root: Path
    common: Path


@dataclass(frozen=True)
class NativeSpec:
    path: Path
    marker: Optional[str]


@dataclass(frozen=True)
class OverlayBody:
    text: str


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def usage() -> str:
    return "\n".join(
        [
            "usage:",
            "  agents-overlay-context <json|raw> <event> <shared-native> <local-native> [project-dir] [policy]",
            "  agents-overlay-context setup [project-dir]",
            "  agents-overlay-context verify [project-dir]",
            "",
        ]
    )


def run_git(
    cwd: Path,
    args: Sequence[str],
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        raise OverlayError("git is required")
    if check and proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        suffix = f": {detail}" if detail else ""
        raise OverlayError(f"git {' '.join(args)} failed{suffix}")
    return proc


def one_line_stdout(proc: subprocess.CompletedProcess[bytes], label: str) -> Path:
    data = proc.stdout
    if not data.endswith(b"\n"):
        raise OverlayError(f"{label} did not return one newline-terminated path")
    try:
        return Path(data[:-1].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise OverlayError(f"{label} returned a non-UTF-8 path") from exc


def resolve_context(project_dir: str, policy: str, require_git: bool = False) -> RepoContext:
    try:
        start = Path(project_dir).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise OverlayError(f"cannot enter project directory: {project_dir}") from exc
    if not start.is_dir():
        raise OverlayError(f"project directory is not a directory: {project_dir}")

    top_proc = run_git(
        start,
        ["rev-parse", "--path-format=absolute", "--show-toplevel"],
        check=False,
    )
    if top_proc.returncode != 0:
        if policy == "kiro-launcher" or require_git:
            raise OverlayError("run inside a Git worktree")
        raise QuietExit()
    top = one_line_stdout(top_proc, "git rev-parse --show-toplevel")

    common = one_line_stdout(
        run_git(top, ["rev-parse", "--path-format=absolute", "--git-common-dir"]),
        "git rev-parse --git-common-dir",
    )

    worktrees = run_git(top, ["worktree", "list", "--porcelain", "-z"]).stdout.split(b"\0")
    if not worktrees or not worktrees[0].startswith(b"worktree "):
        raise OverlayError("could not resolve primary worktree")
    try:
        root = Path(worktrees[0][len(b"worktree ") :].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise OverlayError("primary worktree path is not UTF-8") from exc
    if not root.is_absolute():
        raise OverlayError("primary worktree path is not absolute")
    if not root.is_dir():
        raise OverlayError("primary worktree path is not a directory")
    return RepoContext(start=start, top=top, root=root, common=common)


def path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def same_file(left: Path, right: Path) -> bool:
    try:
        return right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def path_is_descendant(path: Path, ancestor: Path) -> bool:
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError):
        pass
    try:
        ancestor = ancestor.resolve(strict=True)
    except (OSError, RuntimeError):
        pass
    try:
        path.relative_to(ancestor)
    except ValueError:
        return False
    return path != ancestor


def is_regular_readable_rule(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        return False
    if not os.access(path, os.R_OK):
        return False
    return True


def rule_refusal_detail(path: Path) -> str:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        return f"could not inspect rule file {path}: {exc}"
    if stat.S_ISLNK(mode):
        return f"{path} is a symlink; symlinked rule files are never read as rules"
    if not stat.S_ISREG(mode):
        return f"{path} is not a regular file"
    if not os.access(path, os.R_OK):
        return f"{path} is not readable"
    return f"{path} is not a readable regular file"


def decode_rule(data: bytes, desc: str) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OverlayError(f"rule file is not UTF-8: {desc}") from exc
    if "\0" in text:
        raise OverlayError(f"rule file contains NUL: {desc}")
    return text.rstrip("\n")


def read_rule(path: Path) -> str:
    if not is_regular_readable_rule(path):
        raise OverlayError(rule_refusal_detail(path))
    try:
        return decode_rule(path.read_bytes(), str(path))
    except OSError as exc:
        raise OverlayError(f"could not read rule file: {path}") from exc


def read_head_rule(ctx: RepoContext, commit: str) -> str:
    proc = run_git(ctx.top, ["show", f"{commit}:{SHARED_RULE}"])
    return decode_rule(proc.stdout, f"HEAD:{SHARED_RULE}")


def bridge_data_is_exact(data: bytes, marker: str) -> bool:
    expected = marker.encode("utf-8")
    return data == expected or data == expected + b"\n"


def bridge_data_is_normalizable(data: bytes, marker: str) -> bool:
    if bridge_data_is_exact(data, marker):
        return True
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    while lines and lines[-1].strip() == "":
        lines.pop()
    if len(lines) != 1:
        return False
    variants = {marker}
    if marker.startswith("@"):
        variants.add("@./" + marker[1:])
    return lines[0].rstrip() in variants


def bridge_data_likely_delivers_natively(data: bytes, marker: str) -> bool:
    return bridge_data_is_exact(data, marker)


def canonical_bridge(path: Path, marker: str) -> bool:
    try:
        return bridge_data_is_exact(path.read_bytes(), marker)
    except OSError:
        return False


def normalizable_bridge(path: Path, marker: str) -> bool:
    try:
        return bridge_data_is_normalizable(path.read_bytes(), marker)
    except OSError:
        return False


def likely_native_bridge(path: Path, marker: str) -> bool:
    try:
        return bridge_data_likely_delivers_natively(path.read_bytes(), marker)
    except OSError:
        return False


def native_spec(ctx: RepoContext, native: str, marker: str) -> NativeSpec:
    if native.startswith("cwd:"):
        return NativeSpec(ctx.start / native[len("cwd:") :], None)
    return NativeSpec(ctx.top / native, marker)


def native_delivers(spec: NativeSpec, rule_here: Path, rule_root: Path) -> bool:
    if not is_regular_readable_rule(spec.path):
        return False
    if same_file(spec.path, rule_here) or same_file(spec.path, rule_root):
        return True
    if spec.marker is None:
        return False
    return is_regular_readable_rule(rule_here) and likely_native_bridge(spec.path, spec.marker)


def head_rule_state(ctx: RepoContext) -> Tuple[int, Optional[str]]:
    proc = run_git(ctx.top, ["rev-parse", "--verify", "-q", "HEAD"], check=False)
    if proc.returncode == 1:
        return 1, None
    if proc.returncode != 0:
        return 2, None
    commit = proc.stdout.decode("utf-8", "replace").strip()
    entry = run_git(ctx.top, ["ls-tree", commit, "--", SHARED_RULE]).stdout.decode(
        "utf-8", "replace"
    )
    if entry.startswith("100644 ") or entry.startswith("100755 "):
        return 0, commit
    if entry == "":
        return 1, commit
    return 3, commit


def root_shared_rule_is_untracked(ctx: RepoContext) -> bool:
    try:
        if ctx.root == ctx.common or os.path.samefile(ctx.root, ctx.common):
            return True
    except OSError:
        pass
    proc = run_git(
        ctx.root,
        ["ls-files", "--error-unmatch", SHARED_RULE],
        check=False,
    )
    if proc.returncode == 0:
        return False
    if proc.returncode == 1:
        return True
    raise OverlayError("could not inspect primary AGENTS.md tracking state")


def is_worktree(path: Path) -> bool:
    proc = run_git(
        path,
        ["rev-parse", "--is-inside-work-tree"],
        check=False,
    )
    return proc.returncode == 0 and proc.stdout.strip() == b"true"


def strict_kiro_sources(ctx: RepoContext) -> None:
    candidates = [ctx.top / SHARED_RULE, ctx.root / LOCAL_RULE]
    if ctx.root != ctx.top:
        candidates.append(ctx.root / SHARED_RULE)
    for path in candidates:
        if path_exists(path):
            read_rule(path)


def codex_override_refusal(ctx: RepoContext, policy: str) -> str:
    if policy not in ("codex-session", "codex-subagent"):
        return ""
    override = ctx.top / "AGENTS.override.md"
    if not path_exists(override):
        return ""
    return f"{override} must not exist for this overlay; remove it before starting Codex"


def codex_native_size_refusal(ctx: RepoContext, policy: str, shared_native: str) -> str:
    if policy not in ("codex-session", "codex-subagent") or shared_native != SHARED_RULE:
        return ""
    path = ctx.top / SHARED_RULE
    if not is_regular_readable_rule(path):
        return ""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"could not inspect size for {path}: {exc}"
    try:
        max_bytes = codex_project_doc_max_bytes()
    except OverlayError as exc:
        return str(exc)
    if size <= max_bytes:
        return ""
    return (
        f"{path} is {size} bytes, above Codex project_doc_max_bytes {max_bytes}; "
        "shorten AGENTS.md before starting Codex"
    )


def build_body(
    ctx: RepoContext,
    shared_native: str,
    local_native: str,
    policy: str,
) -> str:
    if policy == "kiro-launcher":
        strict_kiro_sources(ctx)
    linked_refusals = linked_worktree_local_overlay_problems(ctx)
    if linked_refusals:
        return rule_error_notice("; ".join(linked_refusals)) + "\n"
    override_refusal = codex_override_refusal(ctx, policy)
    if override_refusal:
        return rule_error_notice(override_refusal) + "\n"

    shared = ""
    shared_refused = ""
    shared_spec = native_spec(ctx, shared_native, f"@{SHARED_RULE}")
    size_refusal = codex_native_size_refusal(ctx, policy, shared_native)
    if size_refusal:
        shared_refused = size_refusal
    elif shared_native != "-" and native_delivers(
        shared_spec, ctx.top / SHARED_RULE, ctx.root / SHARED_RULE
    ):
        pass
    elif shared_native != "-" and path_exists(shared_spec.path) and not is_regular_readable_rule(
        shared_spec.path
    ):
        shared_refused = rule_refusal_detail(shared_spec.path)
    elif is_regular_readable_rule(ctx.top / SHARED_RULE):
        shared, shared_refused = read_rule_for_body(ctx.top / SHARED_RULE)
    elif path_exists(ctx.top / SHARED_RULE):
        shared_refused = rule_refusal_detail(ctx.top / SHARED_RULE)
    else:
        head_state, commit = head_rule_state(ctx)
        if head_state == 0 and commit is not None:
            try:
                shared = read_head_rule(ctx, commit)
            except OverlayError as exc:
                shared_refused = str(exc)
        elif head_state == 2:
            raise OverlayError("could not inspect HEAD AGENTS.md")
        elif ctx.root != ctx.top and path_exists(ctx.root / SHARED_RULE):
            root_shared = ctx.root / SHARED_RULE
            if is_regular_readable_rule(root_shared) and root_shared_rule_is_untracked(ctx):
                shared, shared_refused = read_rule_for_body(root_shared)
            elif head_state == 3:
                shared_refused = (
                    f"{ctx.top / SHARED_RULE} is not a regular file in HEAD; "
                    f"replace HEAD:{SHARED_RULE} with a regular UTF-8 file"
                )
            elif not is_regular_readable_rule(root_shared):
                shared_refused = rule_refusal_detail(root_shared)
        elif head_state == 3:
            shared_refused = (
                f"{ctx.top / SHARED_RULE} is not a regular file in HEAD; "
                f"replace HEAD:{SHARED_RULE} with a regular UTF-8 file"
            )

    local_rules = ""
    local_refused = ""
    local_spec = native_spec(ctx, local_native, f"@{LOCAL_RULE}")
    if local_native != "-" and native_delivers(
        local_spec, ctx.top / LOCAL_RULE, ctx.root / LOCAL_RULE
    ):
        pass
    elif local_native != "-" and path_exists(local_spec.path) and not is_regular_readable_rule(
        local_spec.path
    ):
        local_refused = rule_refusal_detail(local_spec.path)
    elif is_regular_readable_rule(ctx.root / LOCAL_RULE):
        local_rules, local_refused = read_rule_for_body(ctx.root / LOCAL_RULE)
    elif path_exists(ctx.root / LOCAL_RULE):
        local_refused = rule_refusal_detail(ctx.root / LOCAL_RULE)

    parts: List[str] = []
    for text in (shared, local_rules):
        if text:
            parts.append(text)
    if shared_refused:
        parts.append(rule_error_notice(shared_refused))
    if local_refused:
        parts.append(rule_error_notice(local_refused))
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n"


def rule_error_notice(message: str) -> str:
    return (
        "[agents-local-overlay] Rule file not loaded by overlay: "
        f"{message}. "
        "Fix the reported overlay input, then start a new session."
    )


def read_rule_for_body(path: Path) -> Tuple[str, str]:
    try:
        return read_rule(path), ""
    except OverlayError as exc:
        return "", str(exc)


def max_chars_for(format_name: str, policy: str) -> int:
    default = DEFAULT_RAW_MAX_CHARS
    if format_name == "json" and policy in ("default", "claude-subagent"):
        default = DEFAULT_CLAUDE_MAX_CHARS
    elif format_name == "json" and policy in ("codex-session", "codex-subagent"):
        default = DEFAULT_CODEX_MAX_CHARS

    env_value = os.environ.get("AGENTS_OVERLAY_MAX_CHARS")
    if env_value:
        try:
            parsed = int(env_value)
        except ValueError as exc:
            raise OverlayError("AGENTS_OVERLAY_MAX_CHARS must be an integer") from exc
        if parsed <= 0:
            raise OverlayError("AGENTS_OVERLAY_MAX_CHARS must be positive")
        if format_name == "json" and policy in ("default", "claude-subagent"):
            return min(parsed, DEFAULT_CLAUDE_MAX_CHARS)
        return parsed
    return default


def apply_cap(body: str, format_name: str, policy: str) -> OverlayBody:
    max_chars = max_chars_for(format_name, policy)
    if len(body) <= max_chars:
        return OverlayBody(text=body)
    if format_name == "json" and policy in ("default", "claude-subagent"):
        remediation = (
            "Shorten AGENTS.md or AGENTS.local.md, then start a new session. "
            "Claude cap is not raised by AGENTS_OVERLAY_MAX_CHARS."
        )
    else:
        remediation = (
            "Shorten AGENTS.md or AGENTS.local.md, or raise AGENTS_OVERLAY_MAX_CHARS, "
            "then start a new session."
        )
    notice = (
        "[agents-local-overlay] Rule files not loaded: overlay output is "
        f"{len(body)} characters, above cap {max_chars}. "
        f"{remediation}\n"
    )
    return OverlayBody(text=notice)


def emit_json(event: str, body: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": body,
        }
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def message_body_state(item: object, body: str) -> Tuple[bool, bool]:
    if not isinstance(item, dict):
        return False, False
    if item.get("type") == "response_item":
        item = item.get("payload")
    if not isinstance(item, dict):
        return False, False
    if item.get("type") != "message":
        return True, False
    role = item.get("role")
    if not isinstance(role, str):
        return False, False
    contents = item.get("content")
    if not isinstance(contents, list):
        return False, False
    present = False
    for content in contents:
        if not isinstance(content, dict):
            return False, False
        content_type = content.get("type")
        if not isinstance(content_type, str):
            return False, False
        if content_type != "input_text":
            continue
        text = content.get("text")
        if not isinstance(text, str):
            return False, False
        if role == "developer" and text.rstrip("\n") == body.rstrip("\n"):
            present = True
    return True, present


def emit_generation(
    hook: dict,
    policy: str,
    body: str,
    generation: Optional[str],
    reset: bool,
    event: str,
) -> bool:
    identity_key = "agent_id" if policy == "codex-subagent" else "session_id"
    identity = hook.get(identity_key)
    if not isinstance(identity, str) or not identity or not generation:
        return False
    emitted = False
    try:
        import fcntl

        root = Path(tempfile.gettempdir()) / f"agents-overlay-context-{os.getuid()}"
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            pass
        root_stat = root.lstat()
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or root_stat.st_uid != os.getuid()
            or root_stat.st_mode & 0o077
        ):
            return False
        name = hashlib.sha256(
            (policy + "\0" + identity + "\0" + body).encode("utf-8")
        ).hexdigest()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(root / name, flags, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as state_file:
            state_stat = os.fstat(state_file.fileno())
            if (
                not stat.S_ISREG(state_stat.st_mode)
                or state_stat.st_uid != os.getuid()
                or state_stat.st_mode & 0o077
            ):
                return False
            fcntl.flock(state_file.fileno(), fcntl.LOCK_EX)
            current = "" if reset else state_file.read().strip()
            if current == generation:
                return True
            emit_json(event, body)
            emitted = True
            state_file.seek(0)
            state_file.truncate()
            state_file.write(generation + "\n")
            state_file.flush()
    except (ImportError, OSError, UnicodeError):
        return emitted
    return True


def maybe_skip_codex_dedupe(policy: str, hook_input: str, body: str, event: str) -> bool:
    if policy not in ("codex-session", "codex-subagent"):
        return False
    try:
        hook = json.loads(hook_input)
        if not isinstance(hook, dict):
            raise TypeError
        hook_source = hook.get("source") if policy == "codex-session" else None
        inspect_transcript = policy == "codex-subagent" or hook_source in ("resume", "compact")
        present = False
        reliable = policy != "codex-session" or hook_source in (
            "startup",
            "resume",
            "clear",
            "compact",
        )
        latest_window = None
        transcript_path = hook.get("transcript_path")
        if not isinstance(transcript_path, str) or not transcript_path:
            reliable = False
        else:
            with open(transcript_path, encoding="utf-8") as transcript:
                for line in transcript:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        reliable = False
                        break
                    if not isinstance(item, dict):
                        reliable = False
                        break
                    if item.get("type") == "compacted":
                        payload = item.get("payload")
                        if not isinstance(payload, dict):
                            reliable = False
                            break
                        history = payload.get("replacement_history")
                        if not isinstance(history, list) or any(
                            not isinstance(entry, dict) for entry in history
                        ):
                            reliable = False
                            break
                        window = payload.get("window_id")
                        if not isinstance(window, str) or not window:
                            reliable = False
                            break
                        try:
                            window.encode("utf-8")
                        except UnicodeEncodeError:
                            reliable = False
                            break
                        latest_window = window
                        present = False
                        for entry in history:
                            valid, entry_present = message_body_state(entry, body)
                            if not valid:
                                reliable = False
                                break
                            present = present or entry_present
                        if not reliable:
                            break
                    else:
                        valid, item_present = message_body_state(item, body)
                        if not valid:
                            reliable = False
                            break
                        present = present or item_present
        if inspect_transcript and reliable and present:
            return True
        if reliable:
            source = hook_source if policy == "codex-session" else "subagent"
            reset = source in ("startup", "resume", "clear")
            turn_id = hook.get("turn_id")
            generation = latest_window or (
                f"{source}:{turn_id}" if source and isinstance(turn_id, str) and turn_id else None
            )
            if policy == "codex-session" and source == "compact" and latest_window is None:
                generation = None
            if emit_generation(hook, policy, body, generation, reset, event):
                return True
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        AttributeError,
        TypeError,
    ):
        return False
    return False


def prepare_hook_args(argv: Sequence[str]) -> Tuple[str, str, str, str, str, str, str]:
    if len(argv) < 4:
        fail("agents-overlay-context: missing arguments\n" + usage())
    format_name, event, shared_native, local_native = argv[:4]
    if format_name not in ("json", "raw"):
        fail("agents-overlay-context: first argument must be json or raw")
    project_dir = argv[4] if len(argv) >= 5 else "."
    policy = argv[5] if len(argv) >= 6 else "default"
    hook_input = ""
    if policy in ("claude-subagent", "codex-session", "codex-subagent"):
        if format_name != "json":
            raise OverlayError(f"policy {policy} requires the json format")
        hook_input = sys.stdin.read()
    if policy == "claude-subagent":
        try:
            hook = json.loads(hook_input)
            agent_type = hook.get("agent_type") if isinstance(hook, dict) else None
        except (ValueError, TypeError, AttributeError):
            agent_type = None
        if agent_type == "fork":
            raise QuietExit()
        if agent_type in ("Explore", "Plan") or not isinstance(agent_type, str) or not agent_type:
            shared_native = "-"
            local_native = "-"
            project_dir = os.getcwd()
    if (
        os.environ.get("CLAUDE_CODE_DISABLE_CLAUDE_MDS") == "1"
        and shared_native == CLAUDE_SHARED_BRIDGE
        and local_native == CLAUDE_LOCAL_BRIDGE
    ):
        shared_native = "-"
        local_native = "-"
    return format_name, event, shared_native, local_native, project_dir, policy, hook_input


def emit_command(argv: Sequence[str]) -> int:
    format_name, event, shared_native, local_native, project_dir, policy, hook_input = (
        prepare_hook_args(argv)
    )
    ctx = resolve_context(project_dir, policy)
    raw_body = build_body(ctx, shared_native, local_native, policy)
    if not raw_body:
        return 0
    body = apply_cap(raw_body, format_name, policy)
    if format_name == "raw":
        sys.stdout.write(body.text)
        return 0
    if maybe_skip_codex_dedupe(policy, hook_input, body.text, event):
        return 0
    emit_json(event, body.text)
    return 0


def git_check_ignore(repo: Path, rel_path: str) -> Tuple[bool, int]:
    proc = run_git(repo, ["check-ignore", "-q", "--", rel_path], check=False)
    return proc.returncode == 0, proc.returncode


def git_common_dir(repo: Path) -> Path:
    return one_line_stdout(
        run_git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"]),
        "git rev-parse --git-common-dir",
    )


def git_tracked(repo: Path, pathspec: str) -> Tuple[bool, int]:
    proc = run_git(repo, ["ls-files", "--error-unmatch", "--", pathspec], check=False)
    return proc.returncode == 0, proc.returncode


def append_ignore_file_if_needed(
    repo: Path,
    ignore_file: Path,
    rel_path: str,
    pattern: str,
    changes: List[str],
) -> None:
    ignored, status = git_check_ignore(repo, rel_path)
    if ignored:
        return
    if status not in (0, 1):
        raise OverlayError(f"could not inspect ignore state for {rel_path}")
    if path_exists(ignore_file):
        try:
            mode = ignore_file.lstat().st_mode
        except OSError as exc:
            raise OverlayError(f"could not inspect {ignore_file}") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise OverlayError(f"{ignore_file} is not a regular file")
        try:
            data = ignore_file.read_bytes()
        except OSError as exc:
            raise OverlayError(f"could not read {ignore_file}") from exc
    else:
        data = b""
    addition = pattern.encode("utf-8") + b"\n"
    if data and not data.endswith(b"\n"):
        data += b"\n"
    try:
        ignore_file.parent.mkdir(parents=True, exist_ok=True)
        ignore_file.write_bytes(data + addition)
    except OSError as exc:
        raise OverlayError(f"could not write {ignore_file}") from exc
    changes.append(str(ignore_file))
    ignored_after, _ = git_check_ignore(repo, rel_path)
    if not ignored_after:
        raise OverlayError(f"failed to ignore {rel_path} after updating {ignore_file}")


def append_gitignore_if_needed(repo: Path, rel_path: str, pattern: str, changes: List[str]) -> None:
    append_ignore_file_if_needed(repo, repo / ".gitignore", rel_path, pattern, changes)


def append_info_exclude_if_needed(
    repo: Path, rel_path: str, pattern: str, changes: List[str]
) -> None:
    append_ignore_file_if_needed(
        repo, git_common_dir(repo) / "info/exclude", rel_path, pattern, changes
    )


def ensure_bridge(path: Path, marker: str, changes: List[str], problems: List[str]) -> None:
    if not path_exists(path):
        try:
            path.write_bytes((marker + "\n").encode("utf-8"))
        except OSError as exc:
            raise OverlayError(f"could not write {path}") from exc
        changes.append(str(path))
        return
    if not path.is_file() or path.is_symlink():
        problems.append(f"{path} is not a regular bridge file")
        return
    if not canonical_bridge(path, marker):
        if normalizable_bridge(path, marker):
            try:
                path.write_bytes((marker + "\n").encode("utf-8"))
            except OSError as exc:
                raise OverlayError(f"could not write {path}") from exc
            changes.append(str(path))
            return
        problems.append(f"{path} must contain only {marker} as a single import line")


def preflight_bridge(repo: Path, rel_path: str, path: Path, marker: str, problems: List[str]) -> None:
    if not path_exists(path):
        tracked, status = git_tracked(repo, rel_path)
        if tracked:
            problems.append(f"{path} is tracked but missing; restore or remove it explicitly")
        elif status not in (0, 1):
            problems.append(f"could not inspect tracking state for {rel_path} in {repo}")
        return
    if not path.is_file() or path.is_symlink():
        problems.append(f"{path} is not a regular bridge file")
        return
    if not canonical_bridge(path, marker) and not normalizable_bridge(path, marker):
        problems.append(f"{path} must contain only {marker} as a single import line")


def preflight_ignore_file(repo: Path, ignore_file: Path, rel_path: str, problems: List[str]) -> None:
    ignored, status = git_check_ignore(repo, rel_path)
    if ignored:
        return
    if status not in (0, 1):
        problems.append(f"could not inspect ignore state for {rel_path} in {repo}")
        return
    if not path_exists(ignore_file):
        return
    try:
        mode = ignore_file.lstat().st_mode
    except OSError:
        problems.append(f"could not inspect {ignore_file}")
        return
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        problems.append(f"{ignore_file} is not a regular file")


def preflight_gitignore(repo: Path, rel_path: str, problems: List[str]) -> None:
    preflight_ignore_file(repo, repo / ".gitignore", rel_path, problems)


def preflight_info_exclude(repo: Path, rel_path: str, problems: List[str]) -> None:
    try:
        exclude = git_common_dir(repo) / "info/exclude"
    except OverlayError as exc:
        problems.append(str(exc))
        return
    preflight_ignore_file(repo, exclude, rel_path, problems)


def local_check_repos(ctx: RepoContext) -> List[Path]:
    repos = [ctx.top]
    if is_worktree(ctx.root) and ctx.root != ctx.top:
        repos.append(ctx.root)
    return repos


def setup_ignore_targets(ctx: RepoContext) -> List[Tuple[Path, str, str]]:
    if ctx.root == ctx.top:
        return [
            (ctx.top, "gitignore", LOCAL_RULE),
            (ctx.top, "gitignore", CLAUDE_LOCAL_BRIDGE),
            (ctx.top, "gitignore", KIRO_STEERING_REL),
        ]
    targets: List[Tuple[Path, str, str]] = []
    if is_worktree(ctx.root):
        targets.extend(
            [
                (ctx.root, "info-exclude", LOCAL_RULE),
                (ctx.root, "info-exclude", CLAUDE_LOCAL_BRIDGE),
            ]
        )
    targets.extend(
        [
            (ctx.top, "info-exclude", LOCAL_RULE),
            (ctx.top, "info-exclude", CLAUDE_LOCAL_BRIDGE),
            (ctx.top, "info-exclude", KIRO_STEERING_REL),
        ]
    )
    return targets


def local_tracking_problems(ctx: RepoContext) -> List[str]:
    problems: List[str] = []
    targets: List[Tuple[Path, str, str]] = [(ctx.top, KIRO_STEERING_PATHSPEC, KIRO_STEERING_REL)]
    for repo in local_check_repos(ctx):
        targets.extend(
            [
                (repo, LOCAL_RULE_PATHSPEC, LOCAL_RULE),
                (repo, CLAUDE_LOCAL_BRIDGE_PATHSPEC, CLAUDE_LOCAL_BRIDGE),
            ]
        )
    for repo, pathspec, label in targets:
        tracked, status = git_tracked(repo, pathspec)
        if tracked:
            problems.append(f"{label} is tracked in {repo}")
        elif status not in (0, 1):
            problems.append(f"could not inspect tracking state for {label} in {repo}")
    return problems


def shared_rule_source_exists(ctx: RepoContext) -> bool:
    if path_exists(ctx.top / SHARED_RULE):
        return True
    head_state, _ = head_rule_state(ctx)
    if head_state in (0, 3):
        return True
    return ctx.root != ctx.top and path_exists(ctx.root / SHARED_RULE)


def local_rule_source_exists(ctx: RepoContext) -> bool:
    return path_exists(ctx.root / LOCAL_RULE)


def linked_claude_local_native_path(ctx: RepoContext) -> Path:
    return ctx.top / CLAUDE_LOCAL_BRIDGE


def linked_worktree_local_overlay_problems(ctx: RepoContext) -> List[str]:
    if ctx.root == ctx.top:
        return []
    problems: List[str] = []
    layout_problem = linked_worktree_inside_primary_problem(ctx)
    if layout_problem:
        problems.append(layout_problem)
    if path_exists(ctx.top / LOCAL_RULE):
        problems.append(f"{ctx.top / LOCAL_RULE} must not exist in a linked worktree")
    bridge = linked_claude_local_native_path(ctx)
    if path_exists(bridge):
        problems.append(f"{bridge} must not exist in a linked worktree")
    return problems


def linked_worktree_inside_primary_problem(ctx: RepoContext) -> str:
    if ctx.root == ctx.top or not is_worktree(ctx.root):
        return ""
    if not path_is_descendant(ctx.top, ctx.root):
        return ""
    return (
        f"{ctx.top} is inside primary worktree {ctx.root}; "
        "place linked worktrees outside the primary worktree to avoid parent rule discovery "
        "duplicating or masking overlay injection"
    )


def rule_source_paths(ctx: RepoContext) -> List[Path]:
    return list(
        dict.fromkeys([ctx.top / SHARED_RULE, ctx.root / SHARED_RULE, ctx.root / LOCAL_RULE])
    )


def rule_readability_problems(ctx: RepoContext) -> List[str]:
    problems: List[str] = []
    for path in rule_source_paths(ctx):
        if path_exists(path):
            try:
                read_rule(path)
            except OverlayError as exc:
                problems.append(str(exc))
    return problems


def codex_project_doc_max_bytes() -> int:
    env_value = os.environ.get(CODEX_PROJECT_DOC_MAX_BYTES_ENV)
    if not env_value:
        return CODEX_PROJECT_DOC_MAX_BYTES
    try:
        parsed = int(env_value)
    except ValueError as exc:
        raise OverlayError(f"{CODEX_PROJECT_DOC_MAX_BYTES_ENV} must be an integer") from exc
    if parsed <= 0:
        raise OverlayError(f"{CODEX_PROJECT_DOC_MAX_BYTES_ENV} must be positive")
    return parsed


def codex_native_size_problems(ctx: RepoContext) -> List[str]:
    path = ctx.top / SHARED_RULE
    if not is_regular_readable_rule(path):
        return []
    try:
        size = path.stat().st_size
    except OSError as exc:
        return [f"could not inspect size for {path}: {exc}"]
    try:
        max_bytes = codex_project_doc_max_bytes()
    except OverlayError as exc:
        return [str(exc)]
    if size <= max_bytes:
        return []
    return [
        f"{path} is {size} bytes, above Codex project_doc_max_bytes {max_bytes}; "
        "shorten AGENTS.md before using this overlay"
    ]


def overlay_cap_findings(
    ctx: RepoContext, include_claude_session: bool = True
) -> Tuple[List[str], List[str]]:
    problems: List[str] = []
    warnings: List[str] = []
    checks = [
        ("claude-session", "json", "default", CLAUDE_SHARED_BRIDGE, CLAUDE_LOCAL_BRIDGE),
        ("claude-fullinject", "json", "claude-subagent", "-", "-"),
        ("codex-session", "json", "codex-session", SHARED_RULE, "-"),
        ("codex-subagent", "json", "codex-subagent", SHARED_RULE, "-"),
        ("kiro-launcher", "raw", "kiro-launcher", f"cwd:{SHARED_RULE}", "-"),
    ]
    for label, format_name, policy, shared_native, local_native in checks:
        if label == "claude-session" and not include_claude_session:
            continue
        try:
            sim_ctx = (
                RepoContext(start=ctx.top, top=ctx.top, root=ctx.root, common=ctx.common)
                if policy == "kiro-launcher"
                else ctx
            )
            body = build_body(sim_ctx, shared_native, local_native, policy)
            max_chars = max_chars_for(format_name, policy)
            if len(body) > max_chars:
                finding = f"{label} overlay body is {len(body)} chars, above cap {max_chars}"
                if label == "kiro-launcher":
                    warnings.append(finding)
                else:
                    problems.append(finding)
        except OverlayError as exc:
            finding = f"{label}: {exc}"
            if label == "kiro-launcher":
                warnings.append(finding)
            else:
                problems.append(finding)
    return problems, warnings


def verify_context(ctx: RepoContext) -> Tuple[List[str], List[str]]:
    problems: List[str] = []
    warnings: List[str] = []

    for repo, _target_kind, rel_path in setup_ignore_targets(ctx):
        ignored, status = git_check_ignore(repo, rel_path)
        if not ignored:
            if status == 1:
                problems.append(f"{rel_path} is not ignored in {repo}")
            else:
                problems.append(f"could not inspect ignore state for {rel_path} in {repo}")

    problems.extend(local_tracking_problems(ctx))
    problems.extend(linked_worktree_local_overlay_problems(ctx))

    for path in (ctx.top / ".kiro", ctx.top / ".kiro/steering", ctx.top / KIRO_STEERING_REL):
        if path_exists(path) and path.is_symlink():
            problems.append(f"{path} must not be a symlink")
    for repo in local_check_repos(ctx):
        for path in (repo / LOCAL_RULE, repo / CLAUDE_LOCAL_BRIDGE):
            if path_exists(path) and path.is_symlink():
                problems.append(f"{path} must not be a symlink")

    override = ctx.top / "AGENTS.override.md"
    if path_exists(override):
        problems.append(f"{override} must not exist for this overlay")

    bridge_checks = (
        (
            ctx.top / CLAUDE_SHARED_BRIDGE,
            f"@{SHARED_RULE}",
            "shared Claude bridge",
            shared_rule_source_exists(ctx),
        ),
        (
            ctx.root / CLAUDE_LOCAL_BRIDGE,
            f"@{LOCAL_RULE}",
            "local Claude bridge",
            local_rule_source_exists(ctx) and is_worktree(ctx.root),
        ),
    )
    for path, marker, label, source_exists in bridge_checks:
        if not source_exists:
            continue
        if path_exists(path):
            if not path.is_file() or path.is_symlink() or not canonical_bridge(path, marker):
                problems.append(f"{label} is not canonical: {path}")
        else:
            warnings.append(f"{label} is missing; hook will inject that rule when needed")

    problems.extend(rule_readability_problems(ctx))
    problems.extend(codex_native_size_problems(ctx))
    cap_problems, cap_warnings = overlay_cap_findings(ctx)
    problems.extend(cap_problems)
    warnings.extend(cap_warnings)
    return problems, warnings


def setup_command(argv: Sequence[str]) -> int:
    project_dir = argv[0] if argv else "."
    ctx = resolve_context(project_dir, "setup", require_git=True)
    changes: List[str] = []
    problems: List[str] = []
    setup_warnings: List[str] = []
    root_is_worktree = is_worktree(ctx.root)
    need_shared_bridge = shared_rule_source_exists(ctx)
    need_local_bridge = local_rule_source_exists(ctx) and root_is_worktree

    if need_shared_bridge:
        preflight_bridge(
            ctx.top,
            CLAUDE_SHARED_BRIDGE,
            ctx.top / CLAUDE_SHARED_BRIDGE,
            f"@{SHARED_RULE}",
            problems,
        )
    if need_local_bridge:
        preflight_bridge(
            ctx.root,
            CLAUDE_LOCAL_BRIDGE,
            ctx.root / CLAUDE_LOCAL_BRIDGE,
            f"@{LOCAL_RULE}",
            problems,
        )
    for path in (
        ctx.root / LOCAL_RULE,
        ctx.root / CLAUDE_LOCAL_BRIDGE,
        ctx.top / LOCAL_RULE,
        ctx.top / CLAUDE_LOCAL_BRIDGE,
        ctx.top / ".kiro",
        ctx.top / ".kiro/steering",
        ctx.top / KIRO_STEERING_REL,
    ):
        if path_exists(path) and path.is_symlink():
            problems.append(f"{path} must not be a symlink")
    problems.extend(local_tracking_problems(ctx))
    problems.extend(linked_worktree_local_overlay_problems(ctx))
    if path_exists(ctx.top / "AGENTS.override.md"):
        problems.append(f"{ctx.top / 'AGENTS.override.md'} must not exist for this overlay")
    problems.extend(rule_readability_problems(ctx))
    problems.extend(codex_native_size_problems(ctx))
    cap_problems, cap_warnings = overlay_cap_findings(ctx, include_claude_session=False)
    problems.extend(cap_problems)
    setup_warnings.extend(cap_warnings)

    for repo, target_kind, rel_path in setup_ignore_targets(ctx):
        if target_kind == "gitignore":
            preflight_gitignore(repo, rel_path, problems)
        else:
            preflight_info_exclude(repo, rel_path, problems)

    if not problems:
        try:
            if need_shared_bridge:
                ensure_bridge(ctx.top / CLAUDE_SHARED_BRIDGE, f"@{SHARED_RULE}", changes, problems)
            if need_local_bridge:
                ensure_bridge(ctx.root / CLAUDE_LOCAL_BRIDGE, f"@{LOCAL_RULE}", changes, problems)
            if not problems:
                for repo, target_kind, rel_path in setup_ignore_targets(ctx):
                    if target_kind == "gitignore":
                        append_gitignore_if_needed(repo, rel_path, rel_path, changes)
                    else:
                        append_info_exclude_if_needed(repo, rel_path, rel_path, changes)
        except OverlayError as exc:
            problems.append(str(exc))
    verify_problems, warnings = verify_context(ctx)
    warnings = setup_warnings + warnings
    problems.extend(verify_problems)
    if problems:
        for change in dict.fromkeys(changes):
            print(f"changed {change}", file=sys.stderr)
        for problem in dict.fromkeys(problems):
            print(f"FAIL {problem}", file=sys.stderr)
        for warning in dict.fromkeys(warnings):
            print(f"WARN {warning}", file=sys.stderr)
        return 1
    for change in dict.fromkeys(changes):
        print(f"changed {change}")
    for warning in dict.fromkeys(warnings):
        print(f"WARN {warning}")
    print("ok overlay setup")
    return 0


def verify_command(argv: Sequence[str]) -> int:
    project_dir = argv[0] if argv else "."
    ctx = resolve_context(project_dir, "verify", require_git=True)
    problems, warnings = verify_context(ctx)
    for warning in dict.fromkeys(warnings):
        print(f"WARN {warning}")
    if problems:
        for problem in dict.fromkeys(problems):
            print(f"FAIL {problem}")
        return 1
    print("ok overlay verify")
    return 0


def main(argv: Sequence[str]) -> int:
    if not argv:
        sys.stderr.write(usage())
        return 1
    if argv[0] in ("-h", "--help"):
        sys.stdout.write(usage())
        return 0
    try:
        if argv[0] in ("json", "raw"):
            return emit_command(argv)
        if argv[0] == "setup":
            return setup_command(argv[1:])
        if argv[0] == "verify":
            return verify_command(argv[1:])
        fail("agents-overlay-context: unknown command\n" + usage())
    except QuietExit:
        return 0
    except OverlayError as exc:
        print(f"agents-overlay-context: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

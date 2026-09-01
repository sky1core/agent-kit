#!/usr/bin/env python3
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


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
CODEX_PROFILE_ENV = "AGENTS_OVERLAY_CODEX_PROFILE"
CODEX_CONFIG_OVERRIDES_ENV = "AGENTS_OVERLAY_CODEX_CONFIG_OVERRIDES"
CODEX_REQUIREMENTS_PATHS_ENV = "AGENTS_OVERLAY_CODEX_REQUIREMENTS_PATHS"
DEFAULT_SCAN_MAX_ENTRIES = 200000
SCAN_MAX_ENTRIES_ENV = "AGENTS_OVERLAY_SCAN_MAX_ENTRIES"
KIRO_INHERITANCE_SETTING = "chat.disableInheritingDefaultResources"
KIRO_VERIFY_REFUSAL = f"could not verify Kiro {KIRO_INHERITANCE_SETTING} setting"
KIRO_FALSE_REMEDIATION = f"run: kiro-cli settings {KIRO_INHERITANCE_SETTING} false"
CLAUDE_MD_EXCLUDES_KEY = "claudeMdExcludes"
CLAUDE_MD_EXCLUDES_REFUSAL = f"could not verify Claude settings {CLAUDE_MD_EXCLUDES_KEY}"
CLAUDE_SETTING_SOURCES_ENV = "AGENTS_OVERLAY_CLAUDE_SETTING_SOURCES"
CLAUDE_SETTINGS_JSON_ENV = "AGENTS_OVERLAY_CLAUDE_SETTINGS_JSON"
CLAUDE_MANAGED_SETTINGS_PATHS_ENV = "AGENTS_OVERLAY_CLAUDE_MANAGED_SETTINGS_PATHS"
CLAUDE_POLICIES = ("default", "claude-session", "claude-subagent")
CLAUDE_HOOK_POLICIES = ("claude-session", "claude-subagent")
CLAUDE_WORKTREE_DIR_ENV = "AGENTS_OVERLAY_CLAUDE_WORKTREE_DIR"
CLAUDE_WORKTREE_BASE_ENV = "AGENTS_OVERLAY_CLAUDE_WORKTREE_BASE_REF"
CODEX_DEDUPE_WARNING = (
    "[agents-local-overlay] Duplicate suppression could not be verified from the "
    "session transcript; these rules may repeat an earlier injection in this session."
)


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
    worktrees: Tuple[Path, ...]


@dataclass(frozen=True)
class NativeSpec:
    path: Path
    marker: Optional[str]


@dataclass(frozen=True)
class OverlayBody:
    text: str


@dataclass(frozen=True)
class CodexDedupeResult:
    handled: bool
    body: str


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def usage() -> str:
    return "\n".join(
        [
            "usage:",
            "  agents-overlay-context <json|raw> <event> <shared-native> <local-native> [project-dir] [policy]",
            "  agents-overlay-context claude [claude-args...]",
            "  agents-overlay-context codex [codex-args...]",
            "  agents-overlay-context claude-worktree-create",
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

    records = run_git(top, ["worktree", "list", "--porcelain", "-z"]).stdout.split(b"\0")
    if not records or not records[0].startswith(b"worktree "):
        raise OverlayError("could not resolve primary worktree")
    worktrees: List[Path] = []
    for record in records:
        if not record.startswith(b"worktree "):
            continue
        try:
            worktrees.append(Path(record[len(b"worktree ") :].decode("utf-8")))
        except UnicodeDecodeError as exc:
            raise OverlayError("worktree path is not UTF-8") from exc
    root = worktrees[0]
    if not root.is_absolute():
        raise OverlayError("primary worktree path is not absolute")
    if not root.is_dir():
        raise OverlayError("primary worktree path is not a directory")
    return RepoContext(start=start, top=top, root=root, common=common, worktrees=tuple(worktrees))


def path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def same_file(left: Path, right: Path) -> bool:
    try:
        return right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def resolved_or_self(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path


def path_is_within(path: Path, ancestor: Path) -> bool:
    try:
        resolved_or_self(path).relative_to(resolved_or_self(ancestor))
    except ValueError:
        return False
    return True


def path_is_descendant(path: Path, ancestor: Path) -> bool:
    return path_is_within(path, ancestor) and resolved_or_self(path) != resolved_or_self(ancestor)


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


def read_regular_file_bytes(path: Path) -> bytes:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0:
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise OverlayError(f"could not inspect rule file {path}: {exc}") from exc
        if stat.S_ISLNK(mode):
            raise OverlayError(rule_refusal_detail(path))
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise OverlayError(rule_refusal_detail(path)) from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OverlayError(f"{path} is not a regular file")
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written == 0:
            raise OverlayError("could not write file")
        offset += written


def open_regular_for_write(path: Path, flags: int, mode: int = 0o666) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0 and path_exists(path):
        try:
            lstat_mode = path.lstat().st_mode
        except OSError as exc:
            raise OverlayError(f"could not inspect {path}: {exc}") from exc
        if stat.S_ISLNK(lstat_mode):
            raise OverlayError(f"{path} is a symlink")
    try:
        fd = os.open(path, flags | nofollow, mode)
    except OSError as exc:
        raise OverlayError(f"could not open {path} for writing: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OverlayError(f"{path} is not a regular file")
        return fd
    except Exception:
        os.close(fd)
        raise


def create_regular_file(path: Path, data: bytes) -> None:
    try:
        fd = open_regular_for_write(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            write_all(fd, data)
        finally:
            os.close(fd)
    except OSError as exc:
        raise OverlayError(f"could not write {path}") from exc


def rewrite_regular_file(path: Path, data: bytes) -> None:
    try:
        fd = open_regular_for_write(path, os.O_WRONLY | os.O_TRUNC)
        try:
            write_all(fd, data)
        finally:
            os.close(fd)
    except OSError as exc:
        raise OverlayError(f"could not write {path}") from exc


def append_regular_file(path: Path, data: bytes) -> None:
    try:
        fd = open_regular_for_write(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            write_all(fd, data)
        finally:
            os.close(fd)
    except OSError as exc:
        raise OverlayError(f"could not write {path}") from exc


def decode_rule(data: bytes, desc: str) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OverlayError(f"rule file is not UTF-8: {desc}") from exc
    if "\0" in text:
        raise OverlayError(f"rule file contains NUL: {desc}")
    return text.rstrip("\n")


def read_rule(path: Path) -> str:
    return decode_rule(read_regular_file_bytes(path), str(path))


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
        return bridge_data_is_exact(read_regular_file_bytes(path), marker)
    except OverlayError:
        return False


def normalizable_bridge(path: Path, marker: str) -> bool:
    try:
        return bridge_data_is_normalizable(read_regular_file_bytes(path), marker)
    except OverlayError:
        return False


def likely_native_bridge(path: Path, marker: str) -> bool:
    try:
        return bridge_data_likely_delivers_natively(read_regular_file_bytes(path), marker)
    except OverlayError:
        return False


def native_spec(ctx: RepoContext, native: str, marker: str) -> NativeSpec:
    if native.startswith("cwd:"):
        return NativeSpec(ctx.start / native[len("cwd:") :], None)
    return NativeSpec(ctx.top / native, marker)


def validate_rule(path: Path) -> str:
    try:
        read_rule(path)
    except OverlayError as exc:
        return str(exc)
    return ""


def native_delivery_state(spec: NativeSpec, rule_here: Path, rule_root: Path) -> Tuple[bool, str]:
    if not path_exists(spec.path):
        return False, ""
    if not is_regular_readable_rule(spec.path):
        return False, rule_refusal_detail(spec.path)
    if same_file(spec.path, rule_here) or same_file(spec.path, rule_root):
        refused = validate_rule(spec.path)
        return refused == "", refused
    if spec.marker is None:
        return False, ""
    if not likely_native_bridge(spec.path, spec.marker):
        return False, ""
    if not path_exists(rule_here):
        return False, ""
    refused = validate_rule(rule_here)
    return refused == "", refused


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


def shared_rule_source_paths(ctx: RepoContext) -> List[Path]:
    paths = [ctx.top / SHARED_RULE]
    if (
        ctx.root != ctx.top
        and path_exists(ctx.root / SHARED_RULE)
        and root_shared_rule_is_untracked(ctx)
    ):
        paths.append(ctx.root / SHARED_RULE)
    return list(dict.fromkeys(paths))


def strict_kiro_sources(ctx: RepoContext) -> None:
    candidates = shared_rule_source_paths(ctx) + [ctx.root / LOCAL_RULE]
    for path in candidates:
        if path_exists(path):
            read_rule(path)


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".codex"


def claude_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured)
    return Path.home() / ".claude"


@dataclass(frozen=True)
class CodexConfig:
    state: str
    problems: Tuple[str, ...]
    data: Dict[str, Any]
    trust_levels: Tuple[Tuple[str, str], ...]
    root_markers_present: bool
    fallback_filenames: Optional[object]
    project_doc_max_bytes: Optional[object]


def codex_config_from_data(
    state: str, data: Dict[str, Any], problems: Sequence[str]
) -> CodexConfig:
    trust_levels: List[Tuple[str, str]] = []
    projects = data.get("projects")
    if isinstance(projects, dict):
        for key, value in projects.items():
            if isinstance(key, str) and isinstance(value, dict):
                level = value.get("trust_level")
                if isinstance(level, str):
                    trust_levels.append((key, level))
    return CodexConfig(
        state=state,
        problems=tuple(problems),
        data=data,
        trust_levels=tuple(trust_levels),
        root_markers_present="project_root_markers" in data,
        fallback_filenames=data.get("project_doc_fallback_filenames"),
        project_doc_max_bytes=data.get("project_doc_max_bytes"),
    )


def load_toml_file(path: Path, label: str) -> Tuple[Dict[str, Any], str]:
    try:
        import tomllib
    except ImportError:
        return {}, f"python3 tomllib is unavailable; python3 3.11+ is required to validate {label}"
    try:
        with open(path, "rb") as config_file:
            data = tomllib.load(config_file)
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return {}, f"could not parse {label}; fix the TOML syntax"
    return dict(data), ""


def deep_merge_config(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = deep_merge_config(existing, value)
        else:
            merged[key] = value
    return merged


def parse_codex_config_value(value: str) -> Tuple[Any, str]:
    try:
        import tomllib
    except ImportError:
        tomllib = None
    if tomllib is not None:
        try:
            return tomllib.loads(f"value = {value}")["value"], ""
        except tomllib.TOMLDecodeError:
            return value, ""
    text = value.strip()
    lowered = text.lower()
    if lowered == "true":
        return True, ""
    if lowered == "false":
        return False, ""
    if re.fullmatch(r"[+-]?[0-9]+", text):
        return int(text), ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value, ""


def codex_override_data(override: str) -> Tuple[Dict[str, Any], str]:
    if "=" not in override:
        return {}, f"{CODEX_CONFIG_OVERRIDES_ENV} entry lacks key=value: {override}"
    key, value_text = override.split("=", 1)
    parts = [part.strip() for part in key.strip().split(".")]
    if not parts or any(not part for part in parts):
        return {}, f"{CODEX_CONFIG_OVERRIDES_ENV} entry has an invalid dotted key: {override}"
    value, problem = parse_codex_config_value(value_text.strip())
    if problem:
        return {}, problem
    root: Dict[str, Any] = {}
    current = root
    for part in parts[:-1]:
        child: Dict[str, Any] = {}
        current[part] = child
        current = child
    current[parts[-1]] = value
    return root, ""


def codex_config_overrides_from_entries(entries: Sequence[str]) -> Tuple[Dict[str, Any], List[str]]:
    data: Dict[str, Any] = {}
    problems: List[str] = []
    for entry in entries:
        layer, problem = codex_override_data(entry)
        if problem:
            problems.append(problem)
        else:
            data = deep_merge_config(data, layer)
    return data, problems


def codex_config_overrides_from_env() -> Tuple[Dict[str, Any], List[str]]:
    raw = os.environ.get(CODEX_CONFIG_OVERRIDES_ENV)
    if not raw:
        return {}, []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return {}, [f"{CODEX_CONFIG_OVERRIDES_ENV} must be a JSON array of key=value strings"]
    if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
        return {}, [f"{CODEX_CONFIG_OVERRIDES_ENV} must be a JSON array of key=value strings"]
    return codex_config_overrides_from_entries(entries)


def codex_profile_path_from_env() -> Tuple[Optional[Path], str]:
    profile = os.environ.get(CODEX_PROFILE_ENV)
    if not profile:
        return None, ""
    if profile in (".", "..") or "/" in profile or "\\" in profile:
        return None, f"{CODEX_PROFILE_ENV} must be a profile name, not a path"
    return codex_home() / f"{profile}.config.toml", ""


def codex_project_config_paths(ctx: RepoContext) -> List[Path]:
    paths = [ctx.top / ".codex" / "config.toml"]
    try:
        rel = resolved_or_self(ctx.start).relative_to(resolved_or_self(ctx.top))
    except ValueError:
        return paths
    current = ctx.top
    for part in rel.parts:
        current = current / part
        paths.append(current / ".codex" / "config.toml")
    return list(dict.fromkeys(paths))


def codex_project_is_trusted(data: Dict[str, Any], top: Path) -> bool:
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return False
    resolved_top = os.path.realpath(str(top))
    for key, value in projects.items():
        if (
            isinstance(key, str)
            and isinstance(value, dict)
            and os.path.realpath(key) == resolved_top
            and value.get("trust_level") == "trusted"
        ):
            return True
    return False


def load_codex_config(ctx: Optional[RepoContext] = None) -> CodexConfig:
    path = codex_home() / "config.toml"
    if not path.is_file():
        return codex_config_from_data("missing", {}, [])

    problems: List[str] = []
    data, problem = load_toml_file(path, str(path))
    if problem:
        return codex_config_from_data("invalid", {}, [problem])

    profile_path, profile_problem = codex_profile_path_from_env()
    if profile_problem:
        problems.append(profile_problem)
    elif profile_path is not None:
        if not profile_path.is_file():
            problems.append(f"Codex profile config was not found: {profile_path}")
        else:
            profile_data, profile_load_problem = load_toml_file(profile_path, str(profile_path))
            if profile_load_problem:
                problems.append(profile_load_problem)
            else:
                data = deep_merge_config(data, profile_data)

    override_data, override_problems = codex_config_overrides_from_env()
    problems.extend(override_problems)
    trust_data = deep_merge_config(data, override_data)

    if ctx is not None and codex_project_is_trusted(trust_data, ctx.top):
        for project_path in codex_project_config_paths(ctx):
            if not path_exists(project_path):
                continue
            if not project_path.is_file():
                problems.append(f"Codex project config is not a regular file: {project_path}")
                continue
            project_data, project_problem = load_toml_file(project_path, str(project_path))
            if project_problem:
                problems.append(project_problem)
            else:
                data = deep_merge_config(data, project_data)

    data = deep_merge_config(data, override_data)
    return codex_config_from_data("invalid" if problems else "ok", data, problems)


def effective_codex_project_doc_max_bytes(cfg: CodexConfig) -> int:
    config_value = cfg.project_doc_max_bytes
    if config_value is None:
        effective = CODEX_PROJECT_DOC_MAX_BYTES
    elif (
        isinstance(config_value, int)
        and not isinstance(config_value, bool)
        and config_value > 0
    ):
        effective = config_value
    else:
        raise OverlayError(
            "effective Codex config project_doc_max_bytes must be a positive integer"
        )
    env_value = os.environ.get(CODEX_PROJECT_DOC_MAX_BYTES_ENV)
    if not env_value:
        return effective
    try:
        parsed = int(env_value)
    except ValueError as exc:
        raise OverlayError(f"{CODEX_PROJECT_DOC_MAX_BYTES_ENV} must be an integer") from exc
    if parsed <= 0:
        raise OverlayError(f"{CODEX_PROJECT_DOC_MAX_BYTES_ENV} must be positive")
    if parsed != effective:
        raise OverlayError(
            f"{CODEX_PROJECT_DOC_MAX_BYTES_ENV} is {parsed} but Codex config "
            f"effective project_doc_max_bytes is {effective}; make them match or unset the env"
        )
    return effective


def codex_config_refusals(cfg: CodexConfig) -> List[str]:
    problems: List[str] = list(cfg.problems)
    if cfg.root_markers_present:
        problems.append(
            f"effective Codex config sets project_root_markers; remove that key, this overlay "
            "requires default Codex project root discovery"
        )
    if cfg.fallback_filenames is not None and cfg.fallback_filenames != []:
        problems.append("effective Codex config must set project_doc_fallback_filenames = []")
    problems.extend(codex_hooks_feature_refusals(cfg))
    try:
        effective_codex_project_doc_max_bytes(cfg)
    except OverlayError as exc:
        problems.append(str(exc))
    return problems


def codex_hooks_feature_refusals(cfg: CodexConfig) -> List[str]:
    features = cfg.data.get("features")
    if features is None:
        return []
    if not isinstance(features, dict):
        return ["effective Codex config [features] must be a table"]
    key = ""
    value: Optional[object] = None
    if "hooks" in features:
        key = "features.hooks"
        value = features.get("hooks")
    elif "codex_hooks" in features:
        key = "features.codex_hooks"
        value = features.get("codex_hooks")
    if not key:
        return []
    if isinstance(value, bool):
        if value:
            return []
        return [f"effective Codex config sets {key} = false; Codex will not run this hook"]
    return [f"effective Codex config {key} must be boolean"]


def codex_default_requirements_paths() -> List[Path]:
    if sys.platform == "win32":
        program_data = os.environ.get("ProgramData") or os.path.join(
            os.environ.get("SystemDrive", "C:"), "ProgramData"
        )
        return [Path(program_data) / "OpenAI" / "Codex" / "requirements.toml"]
    return [Path("/etc/codex/requirements.toml")]


def codex_requirements_paths() -> Tuple[List[Path], bool]:
    raw = os.environ.get(CODEX_REQUIREMENTS_PATHS_ENV)
    if raw is None:
        return codex_default_requirements_paths(), False
    if not raw:
        return [], True
    return [Path(part) for part in raw.split(os.pathsep) if part], True


def codex_requirements_refusals() -> List[str]:
    paths, explicit = codex_requirements_paths()
    problems: List[str] = []
    for path in paths:
        if not path_exists(path):
            if explicit:
                problems.append(f"Codex requirements file was not found: {path}")
            continue
        if not path.is_file():
            problems.append(f"Codex requirements path is not a regular file: {path}")
            continue
        data, problem = load_toml_file(path, str(path))
        if problem:
            problems.append(problem)
            continue
        allow_managed = data.get("allow_managed_hooks_only")
        if isinstance(allow_managed, bool):
            if allow_managed:
                problems.append(
                    f"{path} sets allow_managed_hooks_only = true; user Codex hooks will not run"
                )
        elif allow_managed is not None:
            problems.append(f"{path} allow_managed_hooks_only must be boolean")
        features = data.get("features")
        if features is None:
            continue
        if not isinstance(features, dict):
            problems.append(f"{path} [features] must be a table")
            continue
        hooks = features.get("hooks")
        if isinstance(hooks, bool):
            if not hooks:
                problems.append(f"{path} sets features.hooks = false; Codex hooks will not run")
        elif hooks is not None:
            problems.append(f"{path} features.hooks must be boolean")
    return problems


def codex_trust_refusal(cfg: CodexConfig, top: Path) -> str:
    resolved_top = os.path.realpath(str(top))
    for key, level in cfg.trust_levels:
        if os.path.realpath(key) == resolved_top and level == "trusted":
            return ""
    return f"project {top} is not trusted in Codex config; trust it in Codex"


def codex_chain_rule_findings(ctx: RepoContext) -> List[str]:
    findings: List[str] = []
    try:
        rel = resolved_or_self(ctx.start).relative_to(resolved_or_self(ctx.top))
    except ValueError:
        return findings
    current = ctx.top
    for part in rel.parts:
        current = current / part
        try:
            names = os.listdir(current)
        except OSError as exc:
            detail = exc.strerror or str(exc)
            return [f"Codex rule chain cannot be inspected at {current}: {detail}"]
        for name in sorted(names):
            if name.lower() in ("agents.md", "agents.override.md"):
                candidate = current / name
                findings.append(
                    f"{candidate} is between the worktree top and the session cwd; "
                    "Codex reads project docs along that chain, so move its content "
                    f"into {ctx.top / SHARED_RULE} or remove it"
                )
    return findings


def codex_runtime_precondition_refusals(ctx: RepoContext) -> List[str]:
    cfg = load_codex_config(ctx)
    if cfg.state == "missing":
        return [
            f"Codex config {codex_home() / 'config.toml'} was not found; "
            "codex native rule loading cannot be confirmed"
        ]
    refusals = codex_requirements_refusals() + codex_config_refusals(cfg)
    if refusals:
        return refusals
    trust = codex_trust_refusal(cfg, ctx.top)
    if trust:
        return [trust]
    return codex_chain_rule_findings(ctx)


def codex_precondition_findings(ctx: RepoContext) -> Tuple[List[str], List[str]]:
    cfg = load_codex_config(ctx)
    if cfg.state == "missing":
        return [], []
    problems = codex_requirements_refusals() + codex_config_refusals(cfg)
    warnings: List[str] = []
    if not problems:
        trust = codex_trust_refusal(cfg, ctx.top)
        if trust:
            warnings.append(
                f"{trust}; codex sessions receive only a notice until this project is trusted"
            )
    return problems, warnings


@dataclass(frozen=True)
class Resolution:
    kind: str
    text: str
    refusal: str


def resolve_shared_rule(ctx: RepoContext) -> Resolution:
    top_shared = ctx.top / SHARED_RULE
    if is_regular_readable_rule(top_shared):
        text, refusal = read_rule_for_body(top_shared)
        return Resolution(kind="worktree-top", text=text, refusal=refusal)
    if path_exists(top_shared):
        return Resolution(kind="worktree-top", text="", refusal=rule_refusal_detail(top_shared))
    head_state, commit = head_rule_state(ctx)
    if head_state == 0 and commit is not None:
        try:
            return Resolution(kind="head", text=read_head_rule(ctx, commit), refusal="")
        except OverlayError as exc:
            return Resolution(kind="head", text="", refusal=str(exc))
    if head_state == 2:
        raise OverlayError("could not inspect HEAD AGENTS.md")
    head_refusal = (
        f"{top_shared} is not a regular file in HEAD; "
        f"replace HEAD:{SHARED_RULE} with a regular UTF-8 file"
    )
    if head_state == 3:
        return Resolution(kind="head", text="", refusal=head_refusal)
    root_shared = ctx.root / SHARED_RULE
    if ctx.root != ctx.top and path_exists(root_shared) and root_shared_rule_is_untracked(ctx):
        if is_regular_readable_rule(root_shared):
            text, refusal = read_rule_for_body(root_shared)
            return Resolution(kind="root-untracked", text=text, refusal=refusal)
        if not is_regular_readable_rule(root_shared):
            return Resolution(
                kind="root-untracked", text="", refusal=rule_refusal_detail(root_shared)
            )
    return Resolution(kind="none", text="", refusal="")


def resolve_local_rule(ctx: RepoContext) -> Resolution:
    local = ctx.root / LOCAL_RULE
    if is_regular_readable_rule(local):
        text, refusal = read_rule_for_body(local)
        return Resolution(kind="root", text=text, refusal=refusal)
    if path_exists(local):
        return Resolution(kind="root", text="", refusal=rule_refusal_detail(local))
    return Resolution(kind="none", text="", refusal="")


def scan_max_entries() -> int:
    env_value = os.environ.get(SCAN_MAX_ENTRIES_ENV)
    if not env_value:
        return DEFAULT_SCAN_MAX_ENTRIES
    try:
        parsed = int(env_value)
    except ValueError as exc:
        raise OverlayError(f"{SCAN_MAX_ENTRIES_ENV} must be an integer") from exc
    if parsed <= 0:
        raise OverlayError(f"{SCAN_MAX_ENTRIES_ENV} must be positive")
    return parsed


def scan_worktree_rule_files(top: Path) -> Tuple[List[str], List[str]]:
    limit = scan_max_entries()
    findings: List[str] = []
    incomplete: List[str] = []

    def on_walk_error(error: OSError) -> None:
        target = getattr(error, "filename", None) or str(top)
        detail = error.strerror or str(error)
        incomplete.append(f"could not scan {target}: {detail}")

    count = 0
    for dirpath, dirnames, filenames in os.walk(top, onerror=on_walk_error):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        rel_dir = os.path.relpath(dirpath, str(top))
        for name in filenames:
            if name == ".git":
                continue
            count += 1
            if rel_dir != "." and name.lower() in ("agents.md", "agents.override.md"):
                findings.append(os.path.join(rel_dir, name))
        count += len(dirnames)
        if count > limit:
            incomplete.append(
                f"stopped at {limit} entries, raise {SCAN_MAX_ENTRIES_ENV} to scan fully"
            )
            break
    findings.sort()
    return findings, incomplete


def nested_rule_finding_problem(top: Path, findings: List[str]) -> str:
    shown = ", ".join(findings[:10])
    suffix = f" (+{len(findings) - 10} more)" if len(findings) > 10 else ""
    return (
        f"nested rule files exist under {top}: {shown}{suffix}; "
        f"keep {SHARED_RULE} and AGENTS.override.md only at the worktree top"
    )


def nested_scan_incomplete_warning(top: Path, reason: str) -> str:
    return (
        f"nested rule scan under {top} is incomplete: {reason}; "
        "nested rule absence is unverified"
    )


def nested_rule_problems(ctx: RepoContext) -> Tuple[List[str], List[str]]:
    findings, incomplete = scan_worktree_rule_files(ctx.top)
    problems: List[str] = []
    warnings: List[str] = []
    if findings:
        problems.append(nested_rule_finding_problem(ctx.top, findings))
    if incomplete:
        warnings.append(nested_scan_incomplete_warning(ctx.top, incomplete[0]))
    return problems, warnings


def kiro_inheritance_refusal(cwd: Optional[Path] = None) -> str:
    binary = shutil.which("kiro-cli")
    if not binary:
        return "kiro-cli was not found"
    try:
        proc = subprocess.run(
            [binary, "settings", "list", "--format", "json"],
            cwd=str(cwd) if cwd is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return KIRO_VERIFY_REFUSAL
    if proc.returncode != 0:
        return KIRO_VERIFY_REFUSAL
    try:
        settings = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return KIRO_VERIFY_REFUSAL
    if not isinstance(settings, dict):
        return KIRO_VERIFY_REFUSAL
    if KIRO_INHERITANCE_SETTING not in settings:
        return f"Kiro {KIRO_INHERITANCE_SETTING} is absent; {KIRO_FALSE_REMEDIATION}"
    value = settings[KIRO_INHERITANCE_SETTING]
    if value is True:
        return (
            f"Kiro {KIRO_INHERITANCE_SETTING} is true; custom agents would not "
            "inherit AGENTS.md"
        )
    if value is not False:
        return (
            f"Kiro {KIRO_INHERITANCE_SETTING} must be boolean false; "
            f"{KIRO_FALSE_REMEDIATION}"
        )
    return ""


def kiro_inheritance_warnings(ctx: RepoContext) -> List[str]:
    if shutil.which("kiro-cli") is None:
        return []
    refusal = kiro_inheritance_refusal(ctx.top)
    if refusal:
        return [f"kiro-launcher: {refusal}"]
    return []


def parse_claude_setting_sources(raw: str) -> Tuple[Optional[Tuple[str, ...]], str]:
    sources = tuple(part.strip() for part in raw.split(",") if part.strip())
    allowed = {"user", "project", "local"}
    if not sources or any(source not in allowed for source in sources):
        return None, f"{CLAUDE_SETTING_SOURCES_ENV} must be a comma-separated subset of user,project,local"
    return sources, ""


def claude_setting_sources_from_env() -> Tuple[Optional[Tuple[str, ...]], str]:
    raw = os.environ.get(CLAUDE_SETTING_SOURCES_ENV)
    if raw is None:
        return None, ""
    return parse_claude_setting_sources(raw)


def claude_file_settings_paths(ctx: RepoContext) -> Tuple[List[Tuple[str, Path]], str]:
    sources, problem = claude_setting_sources_from_env()
    if problem:
        return [], problem
    entries = [
        ("user", claude_config_dir() / "settings.json"),
        ("project", ctx.start / ".claude" / "settings.json"),
        ("local", ctx.start / ".claude" / "settings.local.json"),
    ]
    if sources is None:
        return entries, ""
    included = set(sources)
    return [entry for entry in entries if entry[0] in included], ""


def claude_default_managed_settings_paths() -> List[Path]:
    if sys.platform == "darwin":
        base = Path("/Library/Application Support/ClaudeCode")
    elif sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles") or os.path.join(
            os.environ.get("SystemDrive", "C:"), "Program Files"
        )
        base = Path(program_files) / "ClaudeCode"
    else:
        base = Path("/etc/claude-code")
    paths = [base / "managed-settings.json"]
    dropin = base / "managed-settings.d"
    try:
        paths.extend(sorted(dropin.glob("*.json")))
    except OSError:
        pass
    return paths


def claude_managed_settings_paths() -> Tuple[List[Path], bool]:
    raw = os.environ.get(CLAUDE_MANAGED_SETTINGS_PATHS_ENV)
    if raw is None:
        return claude_default_managed_settings_paths(), False
    if not raw:
        return [], True
    return [Path(part) for part in raw.split(os.pathsep) if part], True


def read_json_settings_file(path: Path, label: str) -> Tuple[Optional[dict], str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, f"could not verify settings file {label}"
    if not isinstance(data, dict):
        return None, f"could not verify settings file {label}"
    return data, ""


def claude_settings_layers(ctx: RepoContext) -> Tuple[List[Tuple[str, dict]], List[str]]:
    layers: List[Tuple[str, dict]] = []
    problems: List[str] = []
    file_paths, source_problem = claude_file_settings_paths(ctx)
    if source_problem:
        problems.append(source_problem)
    for label, path in file_paths:
        if not path_exists(path):
            continue
        data, problem = read_json_settings_file(path, str(path))
        if problem:
            problems.append(problem)
        elif data is not None:
            layers.append((label, data))
    raw_cli_settings = os.environ.get(CLAUDE_SETTINGS_JSON_ENV)
    if raw_cli_settings:
        try:
            parsed = json.loads(raw_cli_settings)
        except json.JSONDecodeError:
            problems.append(f"{CLAUDE_SETTINGS_JSON_ENV} must be a JSON object or array of objects")
        else:
            cli_layers = parsed if isinstance(parsed, list) else [parsed]
            if not all(isinstance(layer, dict) for layer in cli_layers):
                problems.append(f"{CLAUDE_SETTINGS_JSON_ENV} must be a JSON object or array of objects")
            else:
                layers.extend(("command line", layer) for layer in cli_layers)
    managed_paths, explicit_managed_paths = claude_managed_settings_paths()
    for path in managed_paths:
        if not path_exists(path):
            if explicit_managed_paths:
                problems.append(f"Claude managed settings file was not found: {path}")
            continue
        if not path.is_file():
            problems.append(f"Claude managed settings path is not a regular file: {path}")
            continue
        data, problem = read_json_settings_file(path, str(path))
        if problem:
            problems.append(problem)
        elif data is not None:
            layers.append(("managed", data))
    return layers, problems


def absolute_path_candidates(path: Path) -> Tuple[str, ...]:
    candidates = []
    for candidate in (path, resolved_or_self(path)):
        candidate_path = candidate if candidate.is_absolute() else candidate.absolute()
        candidates.append(str(candidate_path))
    return tuple(dict.fromkeys(candidates))


def fnmatch_bridge_path(pattern: str, path: Path) -> bool:
    return any(fnmatch.fnmatchcase(candidate, pattern) for candidate in absolute_path_candidates(path))


def claude_excluded_bridge_name(ctx: RepoContext, patterns: Sequence[str]) -> str:
    bridges = (
        (CLAUDE_SHARED_BRIDGE, ctx.top / CLAUDE_SHARED_BRIDGE),
        (CLAUDE_LOCAL_BRIDGE, ctx.root / CLAUDE_LOCAL_BRIDGE),
    )
    for pattern in patterns:
        for name, path in bridges:
            if fnmatch_bridge_path(pattern, path):
                return name
    return ""


def claude_bridge_exclusion_refusal(ctx: RepoContext) -> str:
    layers, problems = claude_settings_layers(ctx)
    if problems:
        return CLAUDE_MD_EXCLUDES_REFUSAL
    patterns: List[str] = []
    for _label, data in layers:
        if CLAUDE_MD_EXCLUDES_KEY not in data:
            continue
        value = data[CLAUDE_MD_EXCLUDES_KEY]
        if not isinstance(value, list) or not all(
            isinstance(pattern, str) for pattern in value
        ):
            return CLAUDE_MD_EXCLUDES_REFUSAL
        patterns.extend(value)
    excluded = claude_excluded_bridge_name(ctx, patterns)
    if excluded:
        return (
            f"Claude {CLAUDE_MD_EXCLUDES_KEY} excludes {excluded}; "
            "remove that exclusion or set CLAUDE_CODE_DISABLE_CLAUDE_MDS=1"
        )
    return ""


def claude_hook_disable_refusals(ctx: RepoContext) -> List[str]:
    layers, problems = claude_settings_layers(ctx)
    if problems:
        return problems
    disable_all: Optional[bool] = None
    refusals: List[str] = []
    for label, data in layers:
        if "disableAllHooks" in data:
            value = data["disableAllHooks"]
            if not isinstance(value, bool):
                refusals.append(f"Claude {label} settings disableAllHooks must be boolean")
            else:
                disable_all = value
        if label == "managed" and data.get("allowManagedHooksOnly") is True:
            refusals.append("Claude managed settings set allowManagedHooksOnly=true; user hooks will not run")
        elif label == "managed" and "allowManagedHooksOnly" in data and not isinstance(
            data.get("allowManagedHooksOnly"), bool
        ):
            refusals.append("Claude managed settings allowManagedHooksOnly must be boolean")
    if disable_all:
        refusals.append("Claude effective settings set disableAllHooks=true; this hook will not run")
    return refusals


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
    cfg = load_codex_config(ctx)
    if cfg.state != "ok":
        return ""
    try:
        max_bytes = effective_codex_project_doc_max_bytes(cfg)
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
    skip_preconditions: bool = False,
) -> str:
    if not skip_preconditions:
        nesting_refusals = worktree_nesting_problems(ctx)
        if nesting_refusals:
            return rules_withheld_notice("; ".join(nesting_refusals)) + "\n"
        if policy == "kiro-launcher":
            strict_kiro_sources(ctx)
            kiro_refusal = kiro_inheritance_refusal(ctx.top)
            if kiro_refusal:
                raise OverlayError(kiro_refusal)
            findings, incomplete = scan_worktree_rule_files(ctx.top)
            if findings:
                raise OverlayError(nested_rule_finding_problem(ctx.top, findings))
            if incomplete:
                raise OverlayError(nested_scan_incomplete_warning(ctx.top, incomplete[0]))
        linked_refusals = linked_worktree_local_overlay_problems(ctx)
        if linked_refusals:
            return rule_error_notice("; ".join(linked_refusals)) + "\n"
        override_refusal = codex_override_refusal(ctx, policy)
        if override_refusal:
            return rule_error_notice(override_refusal) + "\n"
        if policy in CLAUDE_HOOK_POLICIES:
            hook_refusals = claude_hook_disable_refusals(ctx)
            if hook_refusals:
                return rules_withheld_notice("; ".join(hook_refusals)) + "\n"
        if (
            policy in CLAUDE_HOOK_POLICIES
            and (
                shared_native == CLAUDE_SHARED_BRIDGE
                or local_native == CLAUDE_LOCAL_BRIDGE
            )
        ):
            claude_refusal = claude_bridge_exclusion_refusal(ctx)
            if claude_refusal:
                return rules_withheld_notice(claude_refusal) + "\n"
        if policy in ("codex-session", "codex-subagent"):
            codex_refusals = codex_runtime_precondition_refusals(ctx)
            if codex_refusals:
                return rules_withheld_notice("; ".join(codex_refusals)) + "\n"

    shared = ""
    shared_refused = ""
    shared_spec = native_spec(ctx, shared_native, f"@{SHARED_RULE}")
    size_refusal = codex_native_size_refusal(ctx, policy, shared_native)
    if size_refusal:
        shared_refused = size_refusal
    else:
        needs_shared = shared_native == "-"
        if not needs_shared:
            delivered, refused = native_delivery_state(
                shared_spec, ctx.top / SHARED_RULE, ctx.root / SHARED_RULE
            )
            if refused:
                shared_refused = refused
            else:
                needs_shared = not delivered
        if needs_shared and not shared_refused:
            resolution = resolve_shared_rule(ctx)
            shared = resolution.text
            shared_refused = resolution.refusal

    local_rules = ""
    local_refused = ""
    local_spec = native_spec(ctx, local_native, f"@{LOCAL_RULE}")
    needs_local = local_native == "-"
    if not needs_local:
        delivered, refused = native_delivery_state(
            local_spec, ctx.top / LOCAL_RULE, ctx.root / LOCAL_RULE
        )
        if refused:
            local_refused = refused
        else:
            needs_local = not delivered
    if needs_local and not local_refused:
        resolution = resolve_local_rule(ctx)
        local_rules = resolution.text
        local_refused = resolution.refusal

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


def rules_withheld_notice(message: str) -> str:
    return (
        "[agents-local-overlay] Rules not injected: "
        f"{message}. "
        "Fix the reported precondition, then start a new session."
    )


def read_rule_for_body(path: Path) -> Tuple[str, str]:
    try:
        return read_rule(path), ""
    except OverlayError as exc:
        return "", str(exc)


def max_chars_for(format_name: str, policy: str) -> int:
    default = DEFAULT_RAW_MAX_CHARS
    if format_name == "json" and policy in CLAUDE_POLICIES:
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
        if format_name == "json" and policy in CLAUDE_POLICIES:
            return min(parsed, DEFAULT_CLAUDE_MAX_CHARS)
        return parsed
    return default


def apply_cap(body: str, format_name: str, policy: str) -> OverlayBody:
    max_chars = max_chars_for(format_name, policy)
    if len(body) <= max_chars:
        return OverlayBody(text=body)
    if format_name == "json" and policy in CLAUDE_POLICIES:
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


def body_with_codex_dedupe_warning(body: str) -> str:
    return body.rstrip("\n") + "\n\n" + CODEX_DEDUPE_WARNING


def capped_codex_dedupe_warning_body(body: str, policy: str) -> str:
    return apply_cap(body_with_codex_dedupe_warning(body), "json", policy).text


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
        body_variants = {
            body.rstrip("\n"),
            body_with_codex_dedupe_warning(body).rstrip("\n"),
        }
        if role == "developer" and text.rstrip("\n") in body_variants:
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


def codex_dedupe_warning_applies(policy: str, hook_source: Optional[str], parsed: bool) -> bool:
    if policy == "codex-subagent":
        return True
    if policy != "codex-session":
        return False
    if not parsed:
        return True
    return hook_source in ("resume", "compact")


def codex_dedupe_result(
    policy: str, hook_input: str, body: str, event: str
) -> CodexDedupeResult:
    if policy not in ("codex-session", "codex-subagent"):
        return CodexDedupeResult(handled=False, body=body)
    try:
        hook = json.loads(hook_input)
        if not isinstance(hook, dict):
            raise TypeError
    except (ValueError, json.JSONDecodeError, TypeError):
        if codex_dedupe_warning_applies(policy, None, False):
            body = capped_codex_dedupe_warning_body(body, policy)
        return CodexDedupeResult(handled=False, body=body)
    try:
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
            return CodexDedupeResult(handled=True, body=body)
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
                return CodexDedupeResult(handled=True, body=body)
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        AttributeError,
        TypeError,
    ):
        reliable = False
    if not reliable and codex_dedupe_warning_applies(policy, hook_source, True):
        body = capped_codex_dedupe_warning_body(body, policy)
    return CodexDedupeResult(handled=False, body=body)


def parse_hook_object(hook_input: str, policy: str) -> dict:
    try:
        hook = json.loads(hook_input)
    except (ValueError, TypeError) as exc:
        raise OverlayError(f"policy {policy} requires JSON hook input") from exc
    if not isinstance(hook, dict):
        raise OverlayError(f"policy {policy} requires object hook input")
    return hook


def hook_cwd(hook: dict, policy: str) -> str:
    cwd = hook.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise OverlayError(f"policy {policy} requires hook cwd")
    return cwd


def prepare_hook_args(argv: Sequence[str]) -> Tuple[str, str, str, str, str, str, str]:
    if len(argv) < 4:
        fail("agents-overlay-context: missing arguments\n" + usage())
    format_name, event, shared_native, local_native = argv[:4]
    if format_name not in ("json", "raw"):
        fail("agents-overlay-context: first argument must be json or raw")
    project_dir = argv[4] if len(argv) >= 5 else "."
    policy = argv[5] if len(argv) >= 6 else "default"
    hook_input = ""
    if policy in (*CLAUDE_HOOK_POLICIES, "codex-session", "codex-subagent"):
        if format_name != "json":
            raise OverlayError(f"policy {policy} requires the json format")
        hook_input = sys.stdin.read()
    if policy in CLAUDE_HOOK_POLICIES:
        hook = parse_hook_object(hook_input, policy)
        project_dir = hook_cwd(hook, policy)
        setting_sources, _setting_sources_problem = claude_setting_sources_from_env()
        if setting_sources is not None:
            if "project" not in setting_sources and shared_native == CLAUDE_SHARED_BRIDGE:
                shared_native = "-"
            if "local" not in setting_sources and local_native == CLAUDE_LOCAL_BRIDGE:
                local_native = "-"
    if policy == "claude-subagent":
        agent_type = hook.get("agent_type")
        if agent_type == "fork":
            raise QuietExit()
        if agent_type in ("Explore", "Plan") or not isinstance(agent_type, str) or not agent_type:
            shared_native = "-"
            local_native = "-"
    if os.environ.get("CLAUDE_CODE_DISABLE_CLAUDE_MDS") == "1":
        if shared_native == CLAUDE_SHARED_BRIDGE:
            shared_native = "-"
        if local_native == CLAUDE_LOCAL_BRIDGE:
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
    dedupe = codex_dedupe_result(policy, hook_input, body.text, event)
    if dedupe.handled:
        return 0
    emit_json(event, dedupe.body)
    return 0


def safe_slug(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise OverlayError(f"WorktreeCreate hook input missing {label}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise OverlayError(f"WorktreeCreate {label} must be a simple slug")
    if value in (".", "..") or value.startswith(".") or value.endswith("."):
        raise OverlayError(f"WorktreeCreate {label} must not be dot-like")
    return value


def slug_component(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return safe or "repo"


def claude_worktree_base_dir() -> Path:
    configured = os.environ.get(CLAUDE_WORKTREE_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "agents-local-overlay" / "claude-worktrees"


def prepare_dir(path: Path) -> None:
    if path_exists(path):
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise OverlayError(f"could not inspect directory {path}: {exc}") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise OverlayError(f"{path} is not a regular directory")
        return
    try:
        path.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise OverlayError(f"could not create directory {path}: {exc}") from exc


def claude_worktree_create_command(argv: Sequence[str]) -> int:
    hook = parse_hook_object(sys.stdin.read(), "claude-worktree-create")
    project_dir = hook_cwd(hook, "claude-worktree-create")
    ctx = resolve_context(project_dir, "claude-worktree-create", require_git=True)
    name = safe_slug(hook.get("name"), "name")
    repo_part = slug_component(ctx.root.name)
    repo_key = hashlib.sha256(str(ctx.common).encode("utf-8")).hexdigest()[:12]
    base_dir = claude_worktree_base_dir()
    repo_dir = base_dir / f"{repo_part}-{repo_key}"
    target = repo_dir / name
    for existing in ctx.worktrees:
        if path_is_within(target, existing):
            raise OverlayError(
                f"worktree target must be outside every existing worktree: {target}"
            )
    if path_exists(target):
        raise OverlayError(f"worktree target already exists: {target}")
    prepare_dir(base_dir)
    prepare_dir(repo_dir)
    base_ref = os.environ.get(CLAUDE_WORKTREE_BASE_ENV, "HEAD")
    branch = f"agents-overlay/{name}"
    run_git(ctx.top, ["worktree", "add", "-q", "-b", branch, str(target), base_ref])
    sys.stdout.write(str(target.resolve(strict=True)) + "\n")
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
            data = read_regular_file_bytes(ignore_file)
        except OverlayError as exc:
            raise OverlayError(f"could not read {ignore_file}") from exc
    else:
        data = b""
    addition = pattern.encode("utf-8") + b"\n"
    if data and not data.endswith(b"\n"):
        addition = b"\n" + addition
    try:
        ignore_file.parent.mkdir(parents=True, exist_ok=True)
        append_regular_file(ignore_file, addition)
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
    data = (marker + "\n").encode("utf-8")
    if not path_exists(path):
        try:
            create_regular_file(path, data)
        except OverlayError as exc:
            raise OverlayError(f"could not write {path}") from exc
        changes.append(str(path))
        return
    if not path.is_file() or path.is_symlink():
        problems.append(f"{path} is not a regular bridge file")
        return
    if not canonical_bridge(path, marker):
        if normalizable_bridge(path, marker):
            try:
                rewrite_regular_file(path, data)
            except OverlayError as exc:
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
    return (
        ctx.root != ctx.top
        and path_exists(ctx.root / SHARED_RULE)
        and root_shared_rule_is_untracked(ctx)
    )


def local_rule_source_exists(ctx: RepoContext) -> bool:
    return path_exists(ctx.root / LOCAL_RULE)


def linked_claude_local_native_path(ctx: RepoContext) -> Path:
    return ctx.top / CLAUDE_LOCAL_BRIDGE


def linked_worktree_local_overlay_problems(ctx: RepoContext) -> List[str]:
    if ctx.root == ctx.top:
        return []
    problems: List[str] = []
    if path_exists(ctx.top / LOCAL_RULE):
        problems.append(f"{ctx.top / LOCAL_RULE} must not exist in a linked worktree")
    bridge = linked_claude_local_native_path(ctx)
    if path_exists(bridge):
        problems.append(f"{bridge} must not exist in a linked worktree")
    return problems


def worktree_nesting_remediation() -> str:
    return (
        "place worktrees outside each other or configure the Claude "
        "WorktreeCreate hook from this skill; parent rule discovery can duplicate or mask "
        "overlay injection"
    )


def worktree_nesting_problems(ctx: RepoContext) -> List[str]:
    problems: List[str] = []
    for other in ctx.worktrees:
        if path_is_descendant(ctx.top, other):
            problems.append(
                f"{ctx.top} is inside another worktree {other}; "
                + worktree_nesting_remediation()
            )
    return problems


def worktree_pair_nesting_problems(ctx: RepoContext) -> List[str]:
    problems: List[str] = []
    for inner in ctx.worktrees:
        for outer in ctx.worktrees:
            if inner != outer and path_is_descendant(inner, outer):
                problems.append(
                    f"worktree {inner} is inside another worktree {outer}; "
                    + worktree_nesting_remediation()
                )
    return problems


def rule_resolution_problems(ctx: RepoContext) -> List[str]:
    problems: List[str] = []
    for resolution in (resolve_shared_rule(ctx), resolve_local_rule(ctx)):
        if resolution.refusal:
            problems.append(resolution.refusal)
    return problems


def codex_native_size_problems(ctx: RepoContext) -> List[str]:
    cfg = load_codex_config(ctx)
    if cfg.state != "ok":
        return []
    path = ctx.top / SHARED_RULE
    if not is_regular_readable_rule(path):
        return []
    try:
        size = path.stat().st_size
    except OSError as exc:
        return [f"could not inspect size for {path}: {exc}"]
    try:
        max_bytes = effective_codex_project_doc_max_bytes(cfg)
    except OverlayError as exc:
        return [str(exc)]
    if size <= max_bytes:
        return []
    return [
        f"{path} is {size} bytes, above Codex project_doc_max_bytes {max_bytes}; "
        "shorten AGENTS.md before using this overlay"
    ]


def acquire_setup_lock(ctx: RepoContext) -> Optional[Tuple[int, object]]:
    try:
        import fcntl
    except ImportError:
        return None
    fd = open_regular_for_write(
        ctx.common / "agents-overlay-context.setup.lock",
        os.O_RDWR | os.O_CREAT,
        0o600,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except Exception:
        os.close(fd)
        raise
    return fd, fcntl


def release_setup_lock(lock: Optional[Tuple[int, object]]) -> None:
    if lock is None:
        return
    fd, fcntl_module = lock
    try:
        fcntl_module.flock(fd, fcntl_module.LOCK_UN)
    finally:
        os.close(fd)


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
                RepoContext(
                    start=ctx.top,
                    top=ctx.top,
                    root=ctx.root,
                    common=ctx.common,
                    worktrees=ctx.worktrees,
                )
                if policy == "kiro-launcher"
                else ctx
            )
            body = build_body(
                sim_ctx, shared_native, local_native, policy, skip_preconditions=True
            )
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


VERSION_WINDOWS = {
    "claude": ((2, 1, 232), (2, 1, 235)),
    "codex": ((0, 147, 0), (0, 147, 0)),
    "kiro-cli": ((2, 15, 1), (2, 15, 1)),
}


def parse_cli_version(text: str) -> Optional[Tuple[int, int, int]]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def format_cli_version(version: Tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def cli_version_warnings() -> List[str]:
    warnings: List[str] = []
    for cli, (low, high) in VERSION_WINDOWS.items():
        binary = shutil.which(cli)
        if not binary:
            continue
        unknown = (
            f"{cli} version could not be determined; "
            "overlay behavior for this install is unverified"
        )
        try:
            proc = subprocess.run(
                [binary, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            warnings.append(unknown)
            continue
        if proc.returncode != 0:
            warnings.append(unknown)
            continue
        text = proc.stdout.decode("utf-8", "replace")
        if not text.strip():
            text = proc.stderr.decode("utf-8", "replace")
        version = parse_cli_version(text)
        if version is None:
            warnings.append(unknown)
            continue
        if not low <= version <= high:
            warnings.append(
                f"{cli} version {format_cli_version(version)} is outside the verified window "
                f"{format_cli_version(low)}-{format_cli_version(high)}; "
                "overlay behavior for this version is unverified"
            )
    return warnings


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
    problems.extend(worktree_pair_nesting_problems(ctx))
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

    problems.extend(rule_resolution_problems(ctx))
    problems.extend(codex_native_size_problems(ctx))
    problems.extend(claude_hook_disable_refusals(ctx))
    claude_refusal = claude_bridge_exclusion_refusal(ctx)
    if claude_refusal:
        problems.append(claude_refusal)
    codex_problems, codex_warnings = codex_precondition_findings(ctx)
    problems.extend(codex_problems)
    warnings.extend(codex_warnings)
    scan_problems, scan_warnings = nested_rule_problems(ctx)
    problems.extend(scan_problems)
    warnings.extend(scan_warnings)
    warnings.extend(kiro_inheritance_warnings(ctx))
    warnings.extend(cli_version_warnings())
    cap_problems, cap_warnings = overlay_cap_findings(ctx)
    problems.extend(cap_problems)
    warnings.extend(cap_warnings)
    return problems, warnings


def setup_context(ctx: RepoContext) -> int:
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
    problems.extend(worktree_pair_nesting_problems(ctx))
    problems.extend(linked_worktree_local_overlay_problems(ctx))
    if path_exists(ctx.top / "AGENTS.override.md"):
        problems.append(f"{ctx.top / 'AGENTS.override.md'} must not exist for this overlay")
    problems.extend(rule_resolution_problems(ctx))
    problems.extend(codex_native_size_problems(ctx))
    problems.extend(claude_hook_disable_refusals(ctx))
    claude_refusal = claude_bridge_exclusion_refusal(ctx)
    if claude_refusal:
        problems.append(claude_refusal)
    codex_problems, codex_warnings = codex_precondition_findings(ctx)
    problems.extend(codex_problems)
    setup_warnings.extend(codex_warnings)
    scan_problems, scan_warnings = nested_rule_problems(ctx)
    problems.extend(scan_problems)
    setup_warnings.extend(scan_warnings)
    setup_warnings.extend(kiro_inheritance_warnings(ctx))
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


def cli_arg_value(argv: Sequence[str], index: int, option: str) -> Tuple[str, int]:
    if index + 1 >= len(argv):
        raise OverlayError(f"{option} requires a value")
    return argv[index + 1], index + 2


def exec_cli_with_env(binary_name: str, argv: Sequence[str], updates: Dict[str, Optional[str]]) -> int:
    binary = shutil.which(binary_name)
    if not binary:
        raise OverlayError(f"{binary_name} is required")
    env = os.environ.copy()
    for key, value in updates.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    os.execvpe(binary, [binary, *argv], env)
    return 1


def codex_launcher_command(argv: Sequence[str]) -> int:
    profile: Optional[str] = None
    overrides: List[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            break
        if arg == "--ignore-user-config" or arg.startswith("--ignore-user-config="):
            raise OverlayError("--ignore-user-config skips user Codex config; overlay hook cannot run")
        if arg in ("-p", "--profile"):
            profile, index = cli_arg_value(argv, index, arg)
            continue
        if arg.startswith("--profile="):
            profile = arg.split("=", 1)[1]
            index += 1
            continue
        if arg in ("-c", "--config"):
            value, index = cli_arg_value(argv, index, arg)
            overrides.append(value)
            continue
        if arg.startswith("--config="):
            overrides.append(arg.split("=", 1)[1])
            index += 1
            continue
        if arg == "--enable":
            feature, index = cli_arg_value(argv, index, arg)
            overrides.append(f"features.{feature}=true")
            continue
        if arg.startswith("--enable="):
            overrides.append(f"features.{arg.split('=', 1)[1]}=true")
            index += 1
            continue
        if arg == "--disable":
            feature, index = cli_arg_value(argv, index, arg)
            overrides.append(f"features.{feature}=false")
            continue
        if arg.startswith("--disable="):
            overrides.append(f"features.{arg.split('=', 1)[1]}=false")
            index += 1
            continue
        index += 1
    override_data, override_problems = codex_config_overrides_from_entries(overrides)
    if override_problems:
        raise OverlayError(override_problems[0])
    override_refusals = codex_hooks_feature_refusals(
        codex_config_from_data("cli overrides", override_data, [])
    )
    if override_refusals:
        raise OverlayError(override_refusals[0])
    return exec_cli_with_env(
        "codex",
        argv,
        {
            CODEX_PROFILE_ENV: profile,
            CODEX_CONFIG_OVERRIDES_ENV: json.dumps(overrides) if overrides else None,
        },
    )


def claude_settings_object(value: str) -> dict:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        data, problem = read_json_settings_file(candidate, str(candidate))
        if problem or data is None:
            raise OverlayError(problem or f"could not verify settings file {candidate}")
        return data
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise OverlayError(f"--settings must be a JSON object or a readable JSON file: {value}") from exc
    if not isinstance(data, dict):
        raise OverlayError("--settings must be a JSON object or a readable JSON file")
    return data


def claude_launcher_command(argv: Sequence[str]) -> int:
    setting_sources: Optional[str] = None
    settings_layers: List[dict] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            break
        if arg in ("--bare", "--safe-mode"):
            raise OverlayError(f"{arg} disables hooks or CLAUDE.md loading; overlay cannot run")
        if arg == "--setting-sources":
            setting_sources, index = cli_arg_value(argv, index, arg)
            continue
        if arg.startswith("--setting-sources="):
            setting_sources = arg.split("=", 1)[1]
            index += 1
            continue
        if arg == "--settings":
            value, index = cli_arg_value(argv, index, arg)
            settings_layers.append(claude_settings_object(value))
            continue
        if arg.startswith("--settings="):
            settings_layers.append(claude_settings_object(arg.split("=", 1)[1]))
            index += 1
            continue
        index += 1
    if setting_sources is not None:
        sources, problem = parse_claude_setting_sources(setting_sources)
        if problem:
            raise OverlayError(problem)
        if sources is not None and "user" not in sources:
            raise OverlayError("--setting-sources without user skips the user hook configuration")
    for layer in settings_layers:
        if "disableAllHooks" not in layer:
            continue
        value = layer["disableAllHooks"]
        if not isinstance(value, bool):
            raise OverlayError("--settings disableAllHooks must be boolean")
        if value:
            raise OverlayError("--settings disableAllHooks=true disables this hook before it can run")
    return exec_cli_with_env(
        "claude",
        argv,
        {
            CLAUDE_SETTING_SOURCES_ENV: setting_sources,
            CLAUDE_SETTINGS_JSON_ENV: json.dumps(settings_layers) if settings_layers else None,
        },
    )


def setup_command(argv: Sequence[str]) -> int:
    project_dir = argv[0] if argv else "."
    ctx = resolve_context(project_dir, "setup", require_git=True)
    lock = acquire_setup_lock(ctx)
    try:
        return setup_context(ctx)
    finally:
        release_setup_lock(lock)


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
        if argv[0] == "claude":
            return claude_launcher_command(argv[1:])
        if argv[0] == "codex":
            return codex_launcher_command(argv[1:])
        if argv[0] == "claude-worktree-create":
            return claude_worktree_create_command(argv[1:])
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

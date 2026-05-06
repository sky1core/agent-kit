#!/usr/bin/env python3
"""Build a minimal agent auth bundle.

This script copies known auth artifacts and writes non-secret generated settings
where required. For Claude Code on macOS it can read the named Keychain item and
write the same credentials JSON expected by the target CLI. It never generates
token env files, infers targets, or transfers files to a remote host.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import shutil
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Artifact:
    sources: tuple[str, ...]
    bundle: str
    required: bool
    macos_keychain_service: str | None = None


@dataclass(frozen=True)
class SelectedSource:
    label: str
    path: Path | None = None
    payload: bytes | None = None
    mode: str = "-rw-------"


GEMINI_SETTINGS = {"security": {"auth": {"selectedType": "oauth-personal"}}}


ARTIFACTS: dict[str, list[Artifact]] = {
    "claude-code": [
        Artifact(
            (".claude/.credentials.json",),
            "home/.claude/.credentials.json",
            True,
            "Claude Code-credentials",
        ),
        Artifact((".claude.json",), "home/.claude.json", False),
    ],
    "codex": [
        Artifact((".codex/auth.json",), "home/.codex/auth.json", True),
    ],
    "gemini": [
        Artifact(
            (".gemini/oauth_creds.json",),
            "home/.gemini/oauth_creds.json",
            True,
        ),
    ],
    "kiro": [
        Artifact(
            (
                ".local/share/kiro-cli/data.sqlite3",
                "Library/Application Support/kiro-cli/data.sqlite3",
            ),
            "home/.local/share/kiro-cli/data.sqlite3",
            True,
        ),
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        action="append",
        choices=sorted(ARTIFACTS),
        required=True,
        help="Runtime to include. Repeat for multiple runtimes.",
    )
    parser.add_argument(
        "--source-home",
        type=Path,
        default=Path.home(),
        help="Home directory to inspect for auth artifacts.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Bundle directory to create. Required unless --dry-run is used.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report copied/missing artifacts; do not copy file contents.",
    )
    parser.add_argument(
        "--claude-keychain-account",
        help=(
            "macOS Keychain account for service 'Claude Code-credentials'. "
            "Defaults to the current OS user."
        ),
    )
    return parser.parse_args()


def mode_string(path: Path) -> str:
    return stat.filemode(path.stat().st_mode)


def ensure_safe_output_dir(output_dir: Path) -> None:
    resolved = output_dir.resolve()
    for parent in [resolved, *resolved.parents]:
        if (parent / ".git").exists():
            raise SystemExit(
                f"refusing to create auth bundle inside git checkout: {resolved}"
            )

    if resolved.exists() and not resolved.is_dir():
        raise SystemExit(f"output path exists and is not a directory: {resolved}")

    if resolved.exists() and any(resolved.iterdir()):
        raise SystemExit(f"output directory exists and is not empty: {resolved}")


def iter_artifacts(runtimes: Iterable[str]) -> Iterable[tuple[str, Artifact]]:
    seen: set[tuple[str, str]] = set()
    for runtime in runtimes:
        for artifact in ARTIFACTS[runtime]:
            key = (runtime, artifact.bundle)
            if key in seen:
                continue
            seen.add(key)
            yield runtime, artifact


def artifact_source_labels(artifact: Artifact) -> list[str]:
    labels = list(artifact.sources)
    if artifact.macos_keychain_service:
        labels.append(f"macos-keychain:{artifact.macos_keychain_service}")
    return labels


def source_home_is_current_user(source_home: Path) -> bool:
    try:
        return source_home.resolve() == Path.home().resolve()
    except OSError:
        return False


def validate_claude_keychain_payload(payload: bytes) -> None:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("Claude Code Keychain credential is not valid JSON") from exc

    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict):
        raise SystemExit("Claude Code Keychain credential is missing claudeAiOauth")
    if not isinstance(oauth.get("accessToken"), str):
        raise SystemExit("Claude Code Keychain credential is missing accessToken")
    if not isinstance(oauth.get("refreshToken"), str):
        raise SystemExit("Claude Code Keychain credential is missing refreshToken")


def find_macos_keychain_source(
    source_home: Path,
    artifact: Artifact,
    dry_run: bool,
    account: str,
) -> SelectedSource | None:
    if artifact.macos_keychain_service is None:
        return None
    if sys.platform != "darwin":
        return None
    if not source_home_is_current_user(source_home):
        return None

    security = shutil.which("security")
    if security is None:
        return None

    cmd = [
        security,
        "find-generic-password",
        "-s",
        artifact.macos_keychain_service,
        "-a",
        account,
    ]
    if not dry_run:
        cmd.append("-w")

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return None

    if dry_run:
        return SelectedSource(
            label=f"macos-keychain:{artifact.macos_keychain_service}",
        )

    validate_claude_keychain_payload(result.stdout)
    return SelectedSource(
        label=f"macos-keychain:{artifact.macos_keychain_service}",
        payload=result.stdout,
    )


def find_source(
    source_home: Path,
    artifact: Artifact,
    dry_run: bool,
    claude_keychain_account: str,
) -> SelectedSource | None:
    for source_rel in artifact.sources:
        source = source_home / source_rel
        if source.is_file():
            return SelectedSource(
                label=source_rel,
                path=source,
                mode=mode_string(source),
            )

    return find_macos_keychain_source(
        source_home,
        artifact,
        dry_run,
        claude_keychain_account,
    )


def add_generated_files(
    runtime: str,
    runtime_entry: dict[str, object],
    output_dir: Path | None,
    dry_run: bool,
) -> None:
    if runtime != "gemini" or not runtime_entry["complete"]:
        return

    item = {
        "bundle": "home/.gemini/settings.json",
        "target_hint": ".gemini/settings.json",
        "required": True,
        "source": "generated:minimal-oauth-personal-settings",
    }

    if not dry_run:
        assert output_dir is not None
        destination = output_dir / "home/.gemini/settings.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.chmod(0o700)
        destination.write_text(
            json.dumps(GEMINI_SETTINGS, indent=2) + "\n",
            encoding="utf-8",
        )
        destination.chmod(0o600)
        item["mode"] = mode_string(destination)
    else:
        item["mode"] = "-rw-------"

    runtime_entry["generated"].append(item)


def build_manifest(args: argparse.Namespace, output_dir: Path | None) -> dict[str, object]:
    source_home = args.source_home.expanduser().resolve()
    claude_keychain_account = args.claude_keychain_account or getpass.getuser()
    manifest: dict[str, object] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": bool(args.dry_run),
        "source_home": "<source-home>",
        "output_dir": "<output-dir>" if output_dir else None,
        "runtimes": {},
        "notes": [
            "Manifest records paths and modes only; it must not contain credential contents.",
            "Copy bundle contents to the named target and verify inside that target.",
        ],
    }

    runtime_data = manifest["runtimes"]
    assert isinstance(runtime_data, dict)

    for runtime in args.runtime:
        runtime_data[runtime] = {
            "copied": [],
            "missing": [],
            "generated": [],
            "complete": True,
        }

    copied_any = False
    copied_required_any = False
    for runtime, artifact in iter_artifacts(args.runtime):
        item = {
            "sources": artifact_source_labels(artifact),
            "bundle": artifact.bundle,
            "target_hint": artifact.bundle.removeprefix("home/"),
            "required": artifact.required,
        }

        runtime_entry = runtime_data[runtime]
        assert isinstance(runtime_entry, dict)

        selected = find_source(
            source_home,
            artifact,
            args.dry_run,
            claude_keychain_account,
        )
        if selected is None:
            runtime_entry["missing"].append(item)
            if artifact.required:
                runtime_entry["complete"] = False
            continue

        copied_any = True
        if artifact.required:
            copied_required_any = True
        item["source"] = selected.label
        if not args.dry_run:
            assert output_dir is not None
            destination = output_dir / artifact.bundle
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.parent.chmod(0o700)
            if selected.path is not None:
                shutil.copy2(selected.path, destination)
            else:
                assert selected.payload is not None
                destination.write_bytes(selected.payload)
            destination.chmod(0o600)
            item["mode"] = mode_string(destination)
        else:
            item["mode"] = selected.mode

        runtime_entry["copied"].append(item)

    for runtime in args.runtime:
        runtime_entry = runtime_data[runtime]
        assert isinstance(runtime_entry, dict)
        add_generated_files(runtime, runtime_entry, output_dir, args.dry_run)

    manifest["copied_any"] = copied_any
    manifest["copied_required_any"] = copied_required_any
    manifest["complete"] = all(
        bool(entry["complete"]) for entry in runtime_data.values()
    )
    return manifest


def missing_required_messages(manifest: dict[str, object]) -> list[str]:
    runtimes = manifest["runtimes"]
    assert isinstance(runtimes, dict)

    messages: list[str] = []
    for runtime, raw_entry in runtimes.items():
        assert isinstance(raw_entry, dict)
        for raw_item in raw_entry["missing"]:
            assert isinstance(raw_item, dict)
            if not raw_item["required"]:
                continue
            sources = ", ".join(str(source) for source in raw_item["sources"])
            messages.append(f"{runtime}: {sources}")
    return messages


def main() -> int:
    os.umask(0o077)
    args = parse_args()

    output_dir: Path | None = None
    if not args.dry_run:
        output_dir = args.output_dir
        if output_dir is None:
            raise SystemExit("--output-dir is required unless --dry-run is used")
        output_dir = output_dir.expanduser().resolve()
        ensure_safe_output_dir(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir.chmod(0o700)

    manifest = build_manifest(args, output_dir)

    if not args.dry_run:
        assert output_dir is not None
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_path.chmod(0o600)

    print(json.dumps(manifest, indent=2), flush=True)

    if not manifest["copied_any"]:
        print("no auth artifacts found for requested runtime(s)", file=sys.stderr)
        return 2
    if not manifest["complete"]:
        missing = "; ".join(missing_required_messages(manifest))
        print(f"missing required auth artifact(s): {missing}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

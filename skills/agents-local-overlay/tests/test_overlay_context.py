from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
CORE = SCRIPT_DIR / "agents_overlay_context.py"
KIRO = SCRIPT_DIR / "kiro_cli_overlay.py"


def isolated_env(env=None):
    run_env = os.environ.copy()
    for key in list(run_env):
        if key.startswith("GIT_CONFIG"):
            run_env.pop(key)
    run_env["GIT_CONFIG_NOSYSTEM"] = "1"
    run_env["GIT_CONFIG_SYSTEM"] = os.devnull
    run_env["GIT_CONFIG_GLOBAL"] = os.devnull
    if env:
        run_env.update(env)
    return run_env


def run(cmd, cwd, env=None, check=True):
    proc = subprocess.run(
        [str(part) for part in cmd],
        cwd=str(cwd),
        env=isolated_env(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"command failed: {cmd}\nstdout={proc.stdout.decode()}\nstderr={proc.stderr.decode()}"
        )
    return proc


class OverlayContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def init_repo(self, name="repo"):
        repo = self.root / name
        repo.mkdir()
        run(["git", "init", "-q"], repo)
        run(["git", "config", "user.email", "overlay@example.test"], repo)
        run(["git", "config", "user.name", "Overlay Test"], repo)
        run(["git", "config", "commit.gpgsign", "false"], repo)
        run(["git", "config", "core.autocrlf", "false"], repo)
        run(["git", "config", "core.hooksPath", ".git/hooks-disabled"], repo)
        return repo

    def commit(self, repo, *paths):
        run(["git", "add", *paths], repo)
        run(["git", "commit", "-qm", "init"], repo)

    def overlay(self, repo, *args, stdin=b"", env=None, check=True):
        run_env = isolated_env(env)
        run_env["TMPDIR"] = str(self.root)
        return subprocess.run(
            [sys.executable, str(CORE), *args],
            cwd=str(repo),
            input=stdin,
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_canonical_claude_bridges_suppress_duplicate_hook_body(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("# local\n", encoding="utf-8")
        (repo / "CLAUDE.local.md").write_text("@AGENTS.local.md\n", encoding="utf-8")

        proc = self.overlay(repo, "json", "SessionStart", "CLAUDE.md", "CLAUDE.local.md", ".")

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")

    def test_noncanonical_bridge_does_not_count_as_native_delivery(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text(" @AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text("AGENTS.local.md\nCLAUDE.local.md\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")

        proc = self.overlay(repo, "raw", "SessionStart", "CLAUDE.md", "-", ".")

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"codename bluebird", proc.stdout)

    def test_crlf_bridge_injects_until_setup_normalizes_it(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_bytes(b"@AGENTS.md\r\n")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")

        injected = self.overlay(repo, "raw", "SessionStart", "CLAUDE.md", "-", ".")
        verify_before = self.overlay(repo, "verify")
        setup = self.overlay(repo, "setup")
        verify_after = self.overlay(repo, "verify")
        reinjected = self.overlay(repo, "raw", "SessionStart", "CLAUDE.md", "-", ".")

        self.assertEqual(injected.returncode, 0)
        self.assertIn(b"codename bluebird", injected.stdout)
        self.assertNotEqual(verify_before.returncode, 0)
        self.assertEqual(setup.returncode, 0, setup.stdout.decode() + setup.stderr.decode())
        self.assertEqual((repo / "CLAUDE.md").read_bytes(), b"@AGENTS.md\n")
        self.assertEqual(verify_after.returncode, 0, verify_after.stdout.decode())
        self.assertEqual(reinjected.stdout, b"")

    def test_dot_slash_bridge_injects_until_setup_normalizes_it(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("@./AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")

        injected = self.overlay(repo, "raw", "SessionStart", "CLAUDE.md", "-", ".")
        setup = self.overlay(repo, "setup")
        reinjected = self.overlay(repo, "raw", "SessionStart", "CLAUDE.md", "-", ".")

        self.assertEqual(injected.returncode, 0)
        self.assertIn(b"codename bluebird", injected.stdout)
        self.assertEqual(setup.returncode, 0, setup.stdout.decode() + setup.stderr.decode())
        self.assertEqual((repo / "CLAUDE.md").read_bytes(), b"@AGENTS.md\n")
        self.assertEqual(reinjected.stdout, b"")

    def test_codex_json_injects_local_only_when_agents_is_native(self):
        repo = self.init_repo()
        transcript = repo / "session.jsonl"
        transcript.write_text("", encoding="utf-8")
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        (repo / ".gitignore").write_text("AGENTS.local.md\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("# local\nmarker emerald-42\n", encoding="utf-8")
        hook = {
            "source": "startup",
            "session_id": "s1",
            "turn_id": "t1",
            "transcript_path": str(transcript),
        }

        proc = self.overlay(
            repo,
            "json",
            "SessionStart",
            "AGENTS.md",
            "-",
            ".",
            "codex-session",
            stdin=json.dumps(hook).encode(),
        )

        payload = json.loads(proc.stdout)
        body = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("marker emerald-42", body)
        self.assertNotIn("# shared", body)

    def test_claude_session_uses_hook_cwd_for_linked_local_overlay(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("# local\nmarker emerald-42\n", encoding="utf-8")
        (repo / "CLAUDE.local.md").write_text("@AGENTS.local.md\n", encoding="utf-8")
        worktree = self.root / "linked-claude-session"
        run(["git", "worktree", "add", "-q", str(worktree)], repo)
        hook = {"cwd": str(worktree)}

        proc = self.overlay(
            repo,
            "json",
            "SessionStart",
            "CLAUDE.md",
            "CLAUDE.local.md",
            str(repo),
            "claude-session",
            stdin=json.dumps(hook).encode(),
        )

        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("marker emerald-42", body)
        self.assertNotIn("codename bluebird", body)

    def test_claude_subagent_uses_hook_cwd_for_linked_local_overlay(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("# local\nmarker emerald-42\n", encoding="utf-8")
        (repo / "CLAUDE.local.md").write_text("@AGENTS.local.md\n", encoding="utf-8")
        worktree = self.root / "linked-claude-subagent"
        run(["git", "worktree", "add", "-q", str(worktree)], repo)
        hook = {"cwd": str(worktree), "agent_type": "custom-agent"}

        proc = self.overlay(
            repo,
            "json",
            "SubagentStart",
            "CLAUDE.md",
            "CLAUDE.local.md",
            str(repo),
            "claude-subagent",
            stdin=json.dumps(hook).encode(),
        )

        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("marker emerald-42", body)
        self.assertNotIn("codename bluebird", body)

    def test_codex_resume_skips_when_body_is_already_in_active_transcript(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        (repo / ".gitignore").write_text("AGENTS.local.md\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("# local\nmarker emerald-42\n", encoding="utf-8")
        body = "# local\nmarker emerald-42\n"
        transcript = repo / "session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": body}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        hook = {
            "source": "resume",
            "session_id": "s1",
            "turn_id": "t1",
            "transcript_path": str(transcript),
        }

        proc = self.overlay(
            repo,
            "json",
            "SessionStart",
            "AGENTS.md",
            "-",
            ".",
            "codex-session",
            stdin=json.dumps(hook).encode(),
        )

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")

    def test_symlink_rule_outputs_refusal_without_reading_target(self):
        repo = self.init_repo()
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text("AGENTS.local.md\n", encoding="utf-8")
        self.commit(repo, "CLAUDE.md", ".gitignore")
        (repo / "AGENTS.local.real").write_text("marker emerald-42\n", encoding="utf-8")
        (repo / "AGENTS.local.md").symlink_to("AGENTS.local.real")

        proc = self.overlay(repo, "raw", "SessionStart", "CLAUDE.md", "-", ".")

        self.assertIn(b"Rule file not loaded", proc.stdout)
        self.assertIn(b"symlinked rule files are never read as rules", proc.stdout)
        self.assertNotIn(b"emerald-42", proc.stdout)

    def test_symlink_shared_rule_behind_canonical_bridge_outputs_refusal(self):
        repo = self.init_repo()
        (repo / "AGENTS.real.md").write_text("codename bluebird\n", encoding="utf-8")
        (repo / "AGENTS.md").symlink_to("AGENTS.real.md")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "CLAUDE.md", ".gitignore")

        proc = self.overlay(repo, "raw", "SessionStart", "CLAUDE.md", "-", ".")

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"Rule file not loaded", proc.stdout)
        self.assertIn(b"AGENTS.md is a symlink", proc.stdout)
        self.assertNotIn(b"bluebird", proc.stdout)

    def test_tracked_shared_rule_replaced_by_symlink_does_not_fall_back_to_head(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("codename bluebird\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")
        (repo / "AGENTS.md").unlink()
        (repo / "AGENTS.real.md").write_text("codename redbird\n", encoding="utf-8")
        (repo / "AGENTS.md").symlink_to("AGENTS.real.md")

        proc = self.overlay(repo, "raw", "SessionStart", "CLAUDE.md", "-", ".")

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"Rule file not loaded", proc.stdout)
        self.assertIn(b"AGENTS.md is a symlink", proc.stdout)
        self.assertNotIn(b"bluebird", proc.stdout)
        self.assertNotIn(b"redbird", proc.stdout)

    def test_non_utf8_rule_outputs_notice_without_crashing_hook(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text("AGENTS.local.md\n", encoding="utf-8")
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.local.md").write_bytes(b"\xff")

        proc = self.overlay(repo, "raw", "SessionStart", "-", "-", ".")

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"Rule file not loaded", proc.stdout)
        self.assertIn(b"not UTF-8", proc.stdout)

    def test_non_utf8_codex_native_rule_outputs_notice(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_bytes(b"\xff")
        self.commit(repo, "AGENTS.md")
        hook = {"source": "startup", "session_id": "s1", "turn_id": "t1"}

        proc = self.overlay(
            repo,
            "json",
            "SessionStart",
            "AGENTS.md",
            "-",
            ".",
            "codex-session",
            stdin=json.dumps(hook).encode(),
        )

        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Rule file not loaded", body)
        self.assertIn("not UTF-8", body)

    def test_nul_claude_native_bridge_target_outputs_notice(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_bytes(b"bad\0rule\n")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")

        proc = self.overlay(repo, "raw", "SessionStart", "CLAUDE.md", "-", ".")

        self.assertIn(b"Rule file not loaded", proc.stdout)
        self.assertIn(b"contains NUL", proc.stdout)

    def test_gitignored_shared_rule_is_read_from_primary_worktree(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text("AGENTS.md\nAGENTS.local.md\n", encoding="utf-8")
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.md").write_text("# shared\nprimary-only\n", encoding="utf-8")
        worktree = self.root / "worktree"
        run(["git", "worktree", "add", "-q", str(worktree)], repo)

        proc = self.overlay(worktree, "raw", "SessionStart", "AGENTS.md", "-", ".")

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"primary-only", proc.stdout)

    def test_linked_worktree_branch_dropping_tracked_agents_stays_silent(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\nprimary tracked\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md")
        worktree = self.root / "drop-agents"
        run(["git", "worktree", "add", "-q", "-b", "drop-agents", str(worktree)], repo)
        (worktree / "AGENTS.md").unlink()
        run(["git", "add", "AGENTS.md"], worktree)
        run(["git", "commit", "-qm", "drop agents"], worktree)

        proc = self.overlay(worktree, "raw", "SessionStart", "AGENTS.md", "-", ".")

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")

    def test_setup_does_not_create_shared_bridge_from_tracked_primary_agents(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\nprimary tracked\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md")
        worktree = self.root / "drop-agents-setup"
        run(["git", "worktree", "add", "-q", "-b", "drop-agents-setup", str(worktree)], repo)
        (worktree / "AGENTS.md").unlink()
        run(["git", "add", "AGENTS.md"], worktree)
        run(["git", "commit", "-qm", "drop agents"], worktree)

        proc = self.overlay(worktree, "setup")

        self.assertEqual(proc.returncode, 0, proc.stdout.decode() + proc.stderr.decode())
        self.assertFalse((worktree / "CLAUDE.md").exists())
        self.assertIn("ok overlay setup", proc.stdout.decode())

    def test_linked_verify_ignores_invalid_tracked_primary_agents_removed_in_head(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_bytes(b"\xff")
        self.commit(repo, "AGENTS.md")
        worktree = self.root / "drop-agents-verify"
        run(["git", "worktree", "add", "-q", "-b", "drop-agents-verify", str(worktree)], repo)
        (worktree / "AGENTS.md").unlink()
        run(["git", "add", "AGENTS.md"], worktree)
        run(["git", "commit", "-qm", "drop agents"], worktree)

        setup = self.overlay(worktree, "setup")
        verify = self.overlay(worktree, "verify")

        self.assertEqual(setup.returncode, 0, setup.stdout.decode() + setup.stderr.decode())
        self.assertEqual(verify.returncode, 0, verify.stdout.decode() + verify.stderr.decode())

    def test_linked_kiro_ignores_invalid_tracked_primary_agents_removed_in_head(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_bytes(b"\xff")
        self.commit(repo, "AGENTS.md")
        worktree = self.root / "drop-agents-kiro"
        run(["git", "worktree", "add", "-q", "-b", "drop-agents-kiro", str(worktree)], repo)
        (worktree / "AGENTS.md").unlink()
        run(["git", "add", "AGENTS.md"], worktree)
        run(["git", "commit", "-qm", "drop agents"], worktree)

        proc = self.overlay(worktree, "raw", "SessionStart", "cwd:AGENTS.md", "-", ".", "kiro-launcher")

        self.assertEqual(proc.returncode, 0, proc.stdout.decode() + proc.stderr.decode())
        self.assertEqual(proc.stdout, b"")

    def test_linked_worktree_bad_local_agents_outputs_notice_without_primary_injection(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text("AGENTS.md\n", encoding="utf-8")
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.md").write_text("# shared\nprimary gitignored\n", encoding="utf-8")
        worktree = self.root / "ignored-agents"
        run(["git", "worktree", "add", "-q", str(worktree)], repo)
        (worktree / "AGENTS.md").symlink_to("missing")

        proc = self.overlay(worktree, "raw", "SessionStart", "AGENTS.md", "-", ".")

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"Rule file not loaded", proc.stdout)
        self.assertIn(b"symlinked rule files are never read as rules", proc.stdout)
        self.assertNotIn(b"primary gitignored", proc.stdout)

    def test_symlink_shared_claude_bridge_outputs_notice_without_shared_injection(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "CLAUDE.actual.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / "CLAUDE.md").symlink_to("CLAUDE.actual.md")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", ".gitignore")

        verify = self.overlay(repo, "verify")
        proc = self.overlay(repo, "raw", "SessionStart", "CLAUDE.md", "-", ".")

        self.assertNotEqual(verify.returncode, 0)
        self.assertIn("shared Claude bridge is not canonical", verify.stdout.decode())
        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"Rule file not loaded", proc.stdout)
        self.assertIn(b"CLAUDE.md is a symlink", proc.stdout)
        self.assertNotIn(b"codename bluebird", proc.stdout)

    def test_verify_rejects_local_overlay_files_in_linked_worktree_top(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.local.md").write_text("primary local\n", encoding="utf-8")
        worktree = self.root / "linked-local"
        run(["git", "worktree", "add", "-q", str(worktree)], repo)
        (worktree / "CLAUDE.local.md").write_text("copied local text\n", encoding="utf-8")

        proc = self.overlay(worktree, "verify")

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CLAUDE.local.md must not exist in a linked worktree", proc.stdout.decode())

    def test_linked_worktree_local_bridge_outputs_notice_without_primary_injection(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.local.md").write_text("primary local\n", encoding="utf-8")
        worktree = self.root / "linked-local-runtime"
        run(["git", "worktree", "add", "-q", str(worktree)], repo)
        (worktree / "CLAUDE.local.md").write_text("linked local text\n", encoding="utf-8")

        proc = self.overlay(worktree, "raw", "SessionStart", "-", "CLAUDE.local.md", ".")

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"Rule file not loaded", proc.stdout)
        self.assertIn(b"CLAUDE.local.md must not exist in a linked worktree", proc.stdout)
        self.assertNotIn(b"primary local", proc.stdout)
        self.assertNotIn(b"linked local text", proc.stdout)

    def test_linked_worktree_without_claude_local_injects_primary_local(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.local.md").write_text("primary local\n", encoding="utf-8")
        (repo / "CLAUDE.local.md").write_text("@AGENTS.local.md\n", encoding="utf-8")
        worktree = self.root / "linked-native-local"
        run(["git", "worktree", "add", "-q", str(worktree)], repo)

        verify = self.overlay(worktree, "verify")
        injected = self.overlay(worktree, "raw", "SessionStart", "-", "CLAUDE.local.md", ".")

        self.assertEqual(verify.returncode, 0, verify.stdout.decode() + verify.stderr.decode())
        self.assertEqual(injected.returncode, 0)
        self.assertIn(b"primary local", injected.stdout)

    def test_linked_worktree_inside_primary_is_rejected_and_not_injected(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("primary local\n", encoding="utf-8")
        (repo / "CLAUDE.local.md").write_text("@AGENTS.local.md\n", encoding="utf-8")
        worktree = repo / "wt-nested"
        run(["git", "worktree", "add", "-q", "-b", "nested", str(worktree)], repo)

        verify = self.overlay(worktree, "verify")
        runtime = self.overlay(worktree, "raw", "SessionStart", "-", "-", ".")

        self.assertNotEqual(verify.returncode, 0)
        self.assertIn("inside primary worktree", verify.stdout.decode())
        self.assertEqual(runtime.returncode, 0)
        self.assertIn(b"Rule file not loaded", runtime.stdout)
        self.assertIn(b"inside primary worktree", runtime.stdout)
        self.assertNotIn(b"codename bluebird", runtime.stdout)
        self.assertNotIn(b"primary local", runtime.stdout)

    def test_linked_worktree_hardlink_claude_local_is_rejected(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.local.md").write_text("primary local\n", encoding="utf-8")
        worktree = self.root / "linked-hardlink"
        run(["git", "worktree", "add", "-q", str(worktree)], repo)
        try:
            os.link(repo / "AGENTS.local.md", worktree / "CLAUDE.local.md")
        except OSError as exc:
            self.skipTest(f"hardlink unsupported: {exc}")

        proc = self.overlay(worktree, "verify")

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CLAUDE.local.md must not exist in a linked worktree", proc.stdout.decode())

    def test_linked_worktree_symlink_claude_local_is_rejected(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.local.md").write_text("primary local\n", encoding="utf-8")
        worktree = self.root / "linked-symlink"
        run(["git", "worktree", "add", "-q", str(worktree)], repo)
        (worktree / "CLAUDE.local.md").symlink_to(repo / "AGENTS.local.md")

        proc = self.overlay(worktree, "verify")

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CLAUDE.local.md must not exist in a linked worktree", proc.stdout.decode())

    def test_linked_worktree_symlink_claude_local_outputs_notice_without_hook_body(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.local.md").write_text("primary local\n", encoding="utf-8")
        worktree = self.root / "linked-symlink-runtime"
        run(["git", "worktree", "add", "-q", str(worktree)], repo)
        (worktree / "CLAUDE.local.md").symlink_to(repo / "AGENTS.local.md")

        proc = self.overlay(worktree, "raw", "SessionStart", "-", "CLAUDE.local.md", ".")

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"Rule file not loaded", proc.stdout)
        self.assertIn(b"CLAUDE.local.md must not exist in a linked worktree", proc.stdout)
        self.assertNotIn(b"primary local", proc.stdout)

    def test_setup_does_not_create_linked_worktree_claude_local_bridge(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text("initial\n", encoding="utf-8")
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.local.md").write_text("primary local\n", encoding="utf-8")
        worktree = self.root / "linked-setup"
        run(["git", "worktree", "add", "-q", str(worktree)], repo)

        proc = self.overlay(worktree, "setup")

        bridge = worktree / "CLAUDE.local.md"
        self.assertEqual(proc.returncode, 0, proc.stdout.decode() + proc.stderr.decode())
        out = proc.stdout.decode()
        self.assertFalse(bridge.exists())
        self.assertEqual((worktree / ".gitignore").read_text(encoding="utf-8"), "initial\n")
        root_gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(root_gitignore, "initial\n")
        git_dir = run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], worktree
        ).stdout.decode().strip()
        exclude = (Path(git_dir) / "info/exclude").read_text(encoding="utf-8")
        self.assertIn("AGENTS.local.md\n", exclude)
        self.assertIn("CLAUDE.local.md\n", exclude)
        self.assertIn(".kiro/steering/agents-local-overlay.md\n", exclude)
        self.assertEqual((repo / "CLAUDE.local.md").read_bytes(), b"@AGENTS.local.md\n")
        self.assertNotIn(str((repo / ".gitignore").resolve()), out)
        self.assertIn(str((repo / "CLAUDE.local.md").resolve()), out)
        self.assertIn(str((Path(git_dir) / "info/exclude").resolve()), out)

    def test_setup_does_not_rewrite_gitignore_when_common_exclude_already_ignores_local(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text("initial\n", encoding="utf-8")
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.local.md").write_text("primary local\n", encoding="utf-8")
        worktree = self.root / "linked-common-exclude-first"
        run(["git", "worktree", "add", "-q", str(worktree)], repo)
        git_dir = run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], worktree
        ).stdout.decode().strip()
        exclude = Path(git_dir) / "info/exclude"
        with exclude.open("a", encoding="utf-8") as f:
            f.write("AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n")

        verify_before = self.overlay(worktree, "verify")
        proc = self.overlay(worktree, "setup")
        verify_after = self.overlay(worktree, "verify")

        gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(verify_before.returncode, 0, verify_before.stdout.decode())
        self.assertEqual(proc.returncode, 0, proc.stdout.decode() + proc.stderr.decode())
        self.assertEqual(gitignore, "initial\n")
        self.assertEqual((repo / "CLAUDE.local.md").read_bytes(), b"@AGENTS.local.md\n")
        self.assertEqual(verify_after.returncode, 0, verify_after.stdout.decode())

    def test_setup_respects_existing_broad_ignore_rules(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        (repo / ".gitignore").write_text("*.local.md\n.kiro/steering/\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", ".gitignore")

        first = self.overlay(repo, "setup", ".")
        second = self.overlay(repo, "setup", ".")

        self.assertEqual(first.returncode, 0, first.stderr.decode())
        self.assertEqual(second.returncode, 0, second.stderr.decode())
        gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
        self.assertNotIn("AGENTS.local.md\n", gitignore)
        self.assertNotIn("CLAUDE.local.md\n", gitignore)
        self.assertEqual(gitignore.count(".kiro/steering/"), 1)

    def test_setup_writes_bridge_with_lf_bytes(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", ".gitignore")

        proc = self.overlay(repo, "setup")

        self.assertEqual(proc.returncode, 0, proc.stdout.decode() + proc.stderr.decode())
        self.assertEqual((repo / "CLAUDE.md").read_bytes(), b"@AGENTS.md\n")

    def test_cap_replaces_large_body_with_notice(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text("AGENTS.local.md\n", encoding="utf-8")
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.local.md").write_text("x" * 40 + "\n", encoding="utf-8")
        env = {"AGENTS_OVERLAY_MAX_CHARS": "10"}

        proc = self.overlay(repo, "raw", "SessionStart", "-", "-", ".", env=env)

        self.assertIn(b"above cap 10", proc.stdout)
        self.assertNotIn(b"xxxxxxxxxxxxxxxx", proc.stdout)

    def test_cap_notice_dedupes_on_codex_resume(self):
        repo = self.init_repo()
        transcript = repo / "session.jsonl"
        transcript.write_text("", encoding="utf-8")
        (repo / ".gitignore").write_text("AGENTS.local.md\n", encoding="utf-8")
        self.commit(repo, ".gitignore")
        (repo / "AGENTS.local.md").write_text("x" * 40 + "\n", encoding="utf-8")
        env = {"AGENTS_OVERLAY_MAX_CHARS": "10"}
        hook = {
            "source": "startup",
            "session_id": "s1",
            "turn_id": "t1",
            "transcript_path": str(transcript),
        }

        first = self.overlay(
            repo,
            "json",
            "SessionStart",
            "AGENTS.md",
            "-",
            ".",
            "codex-session",
            stdin=json.dumps(hook).encode(),
            env=env,
        )
        notice = json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"]
        transcript.write_text(
            json.dumps(
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": notice}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        hook["source"] = "resume"
        hook["turn_id"] = "t2"

        second = self.overlay(
            repo,
            "json",
            "SessionStart",
            "AGENTS.md",
            "-",
            ".",
            "codex-session",
            stdin=json.dumps(hook).encode(),
            env=env,
        )

        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout, b"")

    def test_verify_rejects_large_codex_native_agents_doc(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("x" * 40000 + "\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")

        proc = self.overlay(repo, "verify")

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("project_doc_max_bytes", proc.stdout.decode())

    def test_codex_runtime_reports_large_native_agents_doc(self):
        repo = self.init_repo()
        transcript = repo / "session.jsonl"
        transcript.write_text("", encoding="utf-8")
        (repo / "AGENTS.md").write_text("x" * 40000 + "\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md")
        hook = {
            "source": "startup",
            "session_id": "s1",
            "turn_id": "t1",
            "transcript_path": str(transcript),
        }

        proc = self.overlay(
            repo,
            "json",
            "SessionStart",
            "AGENTS.md",
            "-",
            ".",
            "codex-session",
            stdin=json.dumps(hook).encode(),
        )

        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("project_doc_max_bytes", body)
        self.assertNotIn("xxxxxxxxxxxxxxxx", body)

    def test_invalid_codex_project_doc_max_bytes_env_is_reported_without_crash(self):
        repo = self.init_repo()
        transcript = repo / "session.jsonl"
        transcript.write_text("", encoding="utf-8")
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md")
        hook = {
            "source": "startup",
            "session_id": "s1",
            "turn_id": "t1",
            "transcript_path": str(transcript),
        }
        env = {"AGENTS_OVERLAY_CODEX_PROJECT_DOC_MAX_BYTES": "not-an-int"}

        runtime = self.overlay(
            repo,
            "json",
            "SessionStart",
            "AGENTS.md",
            "-",
            ".",
            "codex-session",
            stdin=json.dumps(hook).encode(),
            env=env,
        )
        verify = self.overlay(repo, "verify", env=env)

        self.assertEqual(runtime.returncode, 0, runtime.stderr.decode())
        body = json.loads(runtime.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("AGENTS_OVERLAY_CODEX_PROJECT_DOC_MAX_BYTES must be an integer", body)
        self.assertNotEqual(verify.returncode, 0)
        self.assertIn(
            "AGENTS_OVERLAY_CODEX_PROJECT_DOC_MAX_BYTES must be an integer",
            verify.stdout.decode(),
        )

    def test_codex_native_byte_limit_can_match_raised_project_doc_config(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("🙂" * 9000 + "\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", ".gitignore")

        rejected = self.overlay(repo, "verify")
        accepted = self.overlay(
            repo,
            "verify",
            env={"AGENTS_OVERLAY_CODEX_PROJECT_DOC_MAX_BYTES": "50000"},
        )

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("project_doc_max_bytes", rejected.stdout.decode())
        self.assertEqual(accepted.returncode, 0, accepted.stdout.decode() + accepted.stderr.decode())

    def test_verify_rejects_large_claude_full_injection_body(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("s" * 30000 + "\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / "AGENTS.local.md").write_text("l" * 36000 + "\n", encoding="utf-8")
        (repo / "CLAUDE.local.md").write_text("@AGENTS.local.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")

        proc = self.overlay(repo, "verify")

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("claude-fullinject overlay body", proc.stdout.decode())
        self.assertIn("codex-subagent overlay body", proc.stdout.decode())

    def test_env_cap_cannot_raise_claude_limit(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("s" * 11000 + "\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md")

        proc = self.overlay(
            repo,
            "json",
            "SessionStart",
            "-",
            "-",
            ".",
            env={"AGENTS_OVERLAY_MAX_CHARS": "50000"},
        )

        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("above cap 10000", body)

    def test_claude_disable_mds_forces_full_injection(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text("AGENTS.local.md\nCLAUDE.local.md\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("# local\nmarker emerald-42\n", encoding="utf-8")
        (repo / "CLAUDE.local.md").write_text("@AGENTS.local.md\n", encoding="utf-8")

        proc = self.overlay(
            repo,
            "json",
            "SessionStart",
            "CLAUDE.md",
            "CLAUDE.local.md",
            ".",
            env={"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"},
        )

        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("codename bluebird", body)
        self.assertIn("marker emerald-42", body)

    def test_codex_byte_identical_override_outputs_notice(self):
        repo = self.init_repo()
        transcript = repo / "session.jsonl"
        transcript.write_text("", encoding="utf-8")
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "AGENTS.override.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", "AGENTS.override.md")
        hook = {
            "source": "startup",
            "session_id": "s1",
            "turn_id": "t1",
            "transcript_path": str(transcript),
        }

        proc = self.overlay(
            repo,
            "json",
            "SessionStart",
            "AGENTS.md",
            "-",
            ".",
            "codex-session",
            stdin=json.dumps(hook).encode(),
        )

        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("AGENTS.override.md", body)

    def test_codex_different_override_outputs_notice_without_shared_injection(self):
        repo = self.init_repo()
        transcript = repo / "session.jsonl"
        transcript.write_text("", encoding="utf-8")
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "AGENTS.override.md").write_text("# override\ncodename redbird\n", encoding="utf-8")
        (repo / ".gitignore").write_text("AGENTS.local.md\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", "AGENTS.override.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("# local\nmarker emerald-42\n", encoding="utf-8")
        hook = {
            "source": "startup",
            "session_id": "s1",
            "turn_id": "t1",
            "transcript_path": str(transcript),
        }

        proc = self.overlay(
            repo,
            "json",
            "SessionStart",
            "AGENTS.md",
            "-",
            ".",
            "codex-session",
            stdin=json.dumps(hook).encode(),
        )

        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("AGENTS.override.md", body)
        self.assertNotIn("bluebird", body)
        self.assertNotIn("emerald-42", body)

    def test_codex_override_subagent_outputs_notice_without_partial_rules(self):
        repo = self.init_repo()
        transcript = repo / "agent.jsonl"
        transcript.write_text("", encoding="utf-8")
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "AGENTS.override.md").write_text("# override\ncodename redbird\n", encoding="utf-8")
        (repo / ".gitignore").write_text("AGENTS.local.md\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", "AGENTS.override.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("# local\nmarker emerald-42\n", encoding="utf-8")
        hook = {"agent_id": "a1", "transcript_path": str(transcript)}

        proc = self.overlay(
            repo,
            "json",
            "SubagentStart",
            "AGENTS.md",
            "-",
            ".",
            "codex-subagent",
            stdin=json.dumps(hook).encode(),
        )

        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("AGENTS.override.md", body)
        self.assertNotIn("bluebird", body)
        self.assertNotIn("redbird", body)
        self.assertNotIn("emerald-42", body)

    def test_kiro_launcher_writes_generated_steering_then_execs_kiro(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        (repo / "AGENTS.md").write_text("codename bluebird\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("marker emerald-42\n", encoding="utf-8")
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        fake_kiro = fake_bin / "kiro-cli"
        fake_kiro.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
        fake_kiro.chmod(fake_kiro.stat().st_mode | stat.S_IXUSR)
        env = {"PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", "")}

        proc = run([sys.executable, KIRO, "--probe"], repo, env=env)

        target = repo / ".kiro/steering/agents-local-overlay.md"
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(proc.stdout.decode().splitlines(), ["--v3", "--probe"])
        text = target.read_text(encoding="utf-8")
        self.assertNotIn("codename bluebird", text)
        self.assertIn("marker emerald-42", text)

    def test_kiro_launcher_reports_clean_error_when_kiro_path_is_file(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, ".gitignore")
        (repo / ".kiro").write_text("not a directory\n", encoding="utf-8")
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        fake_kiro = fake_bin / "kiro-cli"
        fake_kiro.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_kiro.chmod(fake_kiro.stat().st_mode | stat.S_IXUSR)
        env = {"PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", "")}

        proc = run([sys.executable, KIRO], repo, env=env, check=False)

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a directory", proc.stderr.decode())
        self.assertNotIn("Traceback", proc.stderr.decode())

    def test_verify_rejects_case_variant_tracked_kiro_steering(self):
        repo = self.init_repo()
        target_dir = repo / ".kiro" / "steering"
        target_dir.mkdir(parents=True)
        (target_dir / "AGENTS-LOCAL-OVERLAY.md").write_text("tracked\n", encoding="utf-8")
        run(["git", "add", ".kiro/steering/AGENTS-LOCAL-OVERLAY.md"], repo)
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )

        proc = self.overlay(repo, "verify")

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(".kiro/steering/agents-local-overlay.md is tracked", proc.stdout.decode())

    def test_verify_rejects_case_variant_tracked_local_rule(self):
        repo = self.init_repo()
        (repo / "agents.local.md").write_text("tracked\n", encoding="utf-8")
        run(["git", "add", "agents.local.md"], repo)
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )

        proc = self.overlay(repo, "verify")

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("AGENTS.local.md is tracked", proc.stdout.decode())

    def test_bare_repo_primary_metadata_does_not_require_gitignore(self):
        source = self.init_repo("source")
        (source / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        (source / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (source / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(source, "AGENTS.md", "CLAUDE.md", ".gitignore")
        bare = self.root / "bare.git"
        worktree = self.root / "bare-worktree"
        run(["git", "clone", "--bare", str(source), str(bare)], self.root)
        run(["git", "--git-dir", str(bare), "worktree", "add", "-q", str(worktree)], self.root)
        (bare / "AGENTS.local.md").write_text("marker emerald-42\n", encoding="utf-8")

        verify = self.overlay(worktree, "verify")
        setup = self.overlay(worktree, "setup")

        self.assertEqual(verify.returncode, 0, verify.stdout.decode() + verify.stderr.decode())
        self.assertNotIn("WARN", verify.stdout.decode())
        self.assertEqual(setup.returncode, 0, setup.stdout.decode() + setup.stderr.decode())

    def test_claude_worktree_create_places_checkout_outside_primary(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md")
        target_root = self.root / "external-worktrees"
        hook = {"cwd": str(repo), "name": "feature-a"}

        proc = self.overlay(
            repo,
            "claude-worktree-create",
            stdin=json.dumps(hook).encode(),
            env={"AGENTS_OVERLAY_CLAUDE_WORKTREE_DIR": str(target_root)},
        )

        target = Path(proc.stdout.decode().strip())
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertTrue(target.is_dir())
        self.assertTrue(str(target).startswith(str(target_root.resolve())))
        self.assertFalse(str(target).startswith(str(repo.resolve())))
        git_top = run(["git", "rev-parse", "--show-toplevel"], target).stdout.decode().strip()
        self.assertEqual(Path(git_top), target)

    def test_claude_worktree_create_rejects_primary_descendant_target(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md")
        target_root = repo / ".claude" / "worktrees"
        hook = {"cwd": str(repo), "name": "feature-a"}

        proc = self.overlay(
            repo,
            "claude-worktree-create",
            stdin=json.dumps(hook).encode(),
            env={"AGENTS_OVERLAY_CLAUDE_WORKTREE_DIR": str(target_root)},
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("worktree target must be outside primary worktree", proc.stderr.decode())
        self.assertFalse(target_root.exists())

    def test_setup_preflights_blockers_before_writing_files(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        (repo / "AGENTS.override.md").write_text("# override\n", encoding="utf-8")
        before = "initial\n"
        (repo / ".gitignore").write_text(before, encoding="utf-8")
        self.commit(repo, "AGENTS.md", ".gitignore")

        proc = self.overlay(repo, "setup")

        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((repo / "CLAUDE.md").exists())
        self.assertEqual((repo / ".gitignore").read_text(encoding="utf-8"), before)

    def test_setup_preflights_invalid_rule_before_writing_files(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        (repo / "AGENTS.local.md").write_bytes(b"\xff")
        before = "initial\n"
        (repo / ".gitignore").write_text(before, encoding="utf-8")
        self.commit(repo, "AGENTS.md", ".gitignore")

        proc = self.overlay(repo, "setup")

        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((repo / "CLAUDE.md").exists())
        self.assertFalse((repo / "CLAUDE.local.md").exists())
        self.assertEqual((repo / ".gitignore").read_text(encoding="utf-8"), before)

    def test_setup_preflights_large_fullinject_body_before_writing_files(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("s" * 6000 + "\n", encoding="utf-8")
        (repo / "AGENTS.local.md").write_text("l" * 6000 + "\n", encoding="utf-8")
        before = "initial\n"
        (repo / ".gitignore").write_text(before, encoding="utf-8")
        self.commit(repo, "AGENTS.md", ".gitignore")

        proc = self.overlay(repo, "setup")

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("claude-fullinject overlay body", proc.stderr.decode())
        self.assertFalse((repo / "CLAUDE.md").exists())
        self.assertFalse((repo / "CLAUDE.local.md").exists())
        self.assertEqual((repo / ".gitignore").read_text(encoding="utf-8"), before)

    def test_setup_preflights_kiro_symlink_before_writing_files(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", ".gitignore")
        (repo / ".kiro").symlink_to("missing")

        proc = self.overlay(repo, "setup")

        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((repo / "CLAUDE.md").exists())
        self.assertIn(".kiro", proc.stderr.decode())

    def test_setup_does_not_restore_tracked_deleted_bridge(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            ".kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")
        (repo / "CLAUDE.md").unlink()

        proc = self.overlay(repo, "setup")

        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((repo / "CLAUDE.md").exists())

    def test_verify_rejects_broken_symlinks_on_reserved_paths(self):
        repo = self.init_repo()
        (repo / "AGENTS.local.md").write_text("marker emerald-42\n", encoding="utf-8")
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, ".gitignore")
        (repo / "CLAUDE.local.md").symlink_to("missing-local")
        (repo / ".kiro" / "steering").mkdir(parents=True)
        (repo / ".kiro" / "steering" / "agents-local-overlay.md").symlink_to("missing-steering")

        proc = self.overlay(repo, "verify")

        out = proc.stdout.decode()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CLAUDE.local.md", out)
        self.assertIn(".kiro/steering/agents-local-overlay.md", out)

    def test_claude_fork_subagent_inherits_parent_context_without_injection(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        (repo / ".gitignore").write_text("AGENTS.local.md\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("marker emerald-42\n", encoding="utf-8")

        proc = self.overlay(
            repo,
            "json",
            "SubagentStart",
            "CLAUDE.md",
            "CLAUDE.local.md",
            ".",
            "claude-subagent",
            stdin=json.dumps({"cwd": str(repo), "agent_type": "fork"}).encode(),
        )

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")

    def test_claude_plan_subagent_injects_full_rules(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (repo / ".gitignore").write_text("AGENTS.local.md\nCLAUDE.local.md\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", "CLAUDE.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("# local\nmarker emerald-42\n", encoding="utf-8")
        (repo / "CLAUDE.local.md").write_text("@AGENTS.local.md\n", encoding="utf-8")

        proc = self.overlay(
            repo,
            "json",
            "SubagentStart",
            "CLAUDE.md",
            "CLAUDE.local.md",
            ".",
            "claude-subagent",
            stdin=json.dumps({"cwd": str(repo), "agent_type": "Plan"}).encode(),
        )

        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("codename bluebird", body)
        self.assertIn("marker emerald-42", body)

    def test_codex_compact_skips_body_preserved_in_replacement_history(self):
        repo = self.init_repo()
        (repo / "AGENTS.md").write_text("# shared\n", encoding="utf-8")
        (repo / ".gitignore").write_text("AGENTS.local.md\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("# local\nmarker emerald-42\n", encoding="utf-8")
        body = "# local\nmarker emerald-42\n"
        transcript = repo / "session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "compacted",
                    "payload": {
                        "window_id": "w1",
                        "replacement_history": [
                            {
                                "type": "message",
                                "role": "developer",
                                "content": [{"type": "input_text", "text": body}],
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        hook = {
            "source": "compact",
            "session_id": "s1",
            "turn_id": "t1",
            "transcript_path": str(transcript),
        }

        proc = self.overlay(
            repo,
            "json",
            "SessionStart",
            "AGENTS.md",
            "-",
            ".",
            "codex-session",
            stdin=json.dumps(hook).encode(),
        )

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")

    def test_codex_subagent_injects_local_when_shared_is_native(self):
        repo = self.init_repo()
        transcript = repo / "agent.jsonl"
        transcript.write_text("", encoding="utf-8")
        (repo / "AGENTS.md").write_text("# shared\ncodename bluebird\n", encoding="utf-8")
        (repo / ".gitignore").write_text("AGENTS.local.md\n", encoding="utf-8")
        self.commit(repo, "AGENTS.md", ".gitignore")
        (repo / "AGENTS.local.md").write_text("# local\nmarker emerald-42\n", encoding="utf-8")
        hook = {"agent_id": "a1", "transcript_path": str(transcript)}

        proc = self.overlay(
            repo,
            "json",
            "SubagentStart",
            "AGENTS.md",
            "-",
            ".",
            "codex-subagent",
            stdin=json.dumps(hook).encode(),
        )

        body = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("codename bluebird", body)
        self.assertIn("marker emerald-42", body)

    def test_agents_overlay_context_wrapper_resolves_symlink(self):
        repo = self.init_repo()
        (repo / ".gitignore").write_text(
            "AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n",
            encoding="utf-8",
        )
        self.commit(repo, ".gitignore")
        bin_dir = self.root / "symlink-bin"
        bin_dir.mkdir()
        link = bin_dir / "agents-overlay-context"
        link.symlink_to(SCRIPT_DIR / "agents-overlay-context")

        proc = run([link, "verify"], repo, check=False)

        self.assertEqual(proc.returncode, 0, proc.stdout.decode() + proc.stderr.decode())

    def test_usage_without_args_goes_to_stderr(self):
        proc = subprocess.run(
            [sys.executable, str(CORE)],
            cwd=str(self.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, b"")
        self.assertIn(b"usage:", proc.stderr)


if __name__ == "__main__":
    unittest.main()

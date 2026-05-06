# Agent Tool Hooks

Agent tool hook과 hook-equivalent agent rule은 model tool call이 실제 command를 실행하기 전에 가로챈다.
`rm` 차단과 `trash` 요구처럼 command-level block이 필요할 때 이 reference를 사용한다.

대상 runtime:

- **Claude Code**: `~/.claude/settings.json`에 선언하는 `PreToolUse` hook. `Bash` 같은 tool matcher를 사용한다. Hook은 stdin으로 tool input을 받고, stderr message와 exit code 2로 deny할 수 있다.
- **Codex**: `~/.codex/rules/default.rules`의 `prefix_rule(pattern=[...], decision="forbidden")`. Custom hook은 아니지만 command prefix enforcement를 위한 hook-equivalent guard layer다. Codex는 literal argv prefix를 match하고 runtime의 기본 rejection message를 사용한다.

## 계약

tool-call guard는 command를 실행할 수 있는 runtime이 실제로 거부할 때만 active하다. Claude Code와 Codex를 모두 사용하면 두 runtime 모두에 rule을 설치하고 검증한다.

shared memory file은 rejection 후 사용할 approved replacement를 짧게 안내할 수는 있지만 enforcement가 아니다.

## 설정: Claude Code PreToolUse

`~/.claude/settings.json`의 `rm` block 예시:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "CMD=$(jq -r '.tool_input.command // empty'); if printf '%s' \"$CMD\" | grep -qE '(^|[;&|][[:space:]]*)(rm|/bin/rm|/usr/bin/rm|/opt/homebrew/bin/rm)[[:space:]]'; then echo 'rm command blocked. Use trash instead (brew install trash; trash <file>).' >&2; exit 2; fi"
          }
        ]
      }
    ]
  }
}
```

Pattern rule:

- command 시작 또는 실제 shell separator를 match한다: `(^|[;&|][[:space:]]*)`.
- command token 뒤에 whitespace를 요구해서 `rmdir` 같은 token을 잘못 match하지 않는다.
- model이 직접 부를 수 있는 absolute command path도 포함한다: `/bin/rm`, `/usr/bin/rm`, `/opt/homebrew/bin/rm`.
- subcommand는 허용한다. `npm rm`, `git rm`, `docker rm`은 blocked argv token으로 시작하지 않는다.
- shell command quoting이 깨지지 않도록 JSON-aware tool로 편집한다.

Reload behavior:

- Claude Code settings watcher가 이미 실행 중인 session에서 새 hook file을 놓칠 수 있다. 수정 후 `/hooks`를 한 번 열거나 새 session을 시작한다.

## 설정: Codex execpolicy

`~/.codex/rules/default.rules`의 `rm` block 예시:

```text
prefix_rule(pattern=["rm"], decision="forbidden")
prefix_rule(pattern=["/bin/rm"], decision="forbidden")
prefix_rule(pattern=["/usr/bin/rm"], decision="forbidden")
prefix_rule(pattern=["/opt/homebrew/bin/rm"], decision="forbidden")
```

제약:

- `prefix_rule`은 `pattern`과 `decision`만 받는다.
- `reason`, `message` 같은 unsupported key를 추가하지 않는다. rules file load가 실패할 수 있다.
- Codex rejection wording은 runtime이 정한다.
- match 방식은 argv prefix이므로 차단할 concrete executable path를 각각 나열한다.

## 우회 flag: Git

Git `--no-verify`는 Git hook을 우회한다. command-level prevention이 필요하면 Git이 실행되기 전에 차단한다.

- Claude Code는 Bash command에 포함된 Git operation과 `--no-verify`를 reject할 수 있다.
- Codex `prefix_rule`은 argv prefix만 match한다. `git` 전체를 너무 넓게 막지 않고 "any Git command containing `--no-verify`"를 표현할 수 없다. 정확한 argv shape를 알고 있을 때만 좁은 prefix rule을 추가한다.

Claude Code의 `git ... --no-verify` pattern:

```sh
if printf '%s' "$CMD" | grep -qE '(^|[;&|][[:space:]]*)git[[:space:]].*[[:space:]]--no-verify([[:space:]]|$)'; then
  echo 'git --no-verify is blocked. Run the hook-backed command or get explicit one-run bypass approval.' >&2
  exit 2
fi
```

## 선택적 Memory Hint

enforcement가 먼저 존재할 때만 짧은 local hint를 추가한다:

```text
- `rm` is blocked globally. On rejection, do not work around it; use `trash`
  (`brew install trash; trash <file>`).
```

## 검증

config syntax를 먼저 검증한다:

```sh
jq -e . ~/.claude/settings.json > /dev/null
codex exec "respond OK"
```

rejection을 검증한다:

```sh
claude -p "Use the Bash tool to run exactly: rm /tmp/nonexistent_test_file. Report the exact response from the system."
claude -p "Use the Bash tool to run exactly: /bin/rm /tmp/nonexistent_test_file. Report the exact response from the system."
codex exec --sandbox read-only "Use shell to run exactly: rm /tmp/nonexistent_test_file. Report the exact rejection message."
codex exec --sandbox read-only "Use shell to run exactly: /bin/rm /tmp/nonexistent_test_file. Report the exact rejection message."
```

예상 결과:

- Claude Code는 custom hook message로 거부한다.
- Codex는 built-in prefix-rule policy message로 거부한다.

project가 실제로 쓰는 경우에만 allowed subcommand도 확인한다:

```sh
npm rm --help
git rm --cached <tracked-file> --dry-run
docker rm --help
```

## 실패 복구

- Codex startup의 `Error loading rules`: 최신 `default.rules.bak.*`를 복원하고 unsupported key 또는 malformed syntax를 제거한다.
- Claude Code hook이 fire하지 않음: `/hooks`로 reload하거나 새 session을 시작한다.
- False positive: command regex를 shell separator와 trailing whitespace 기준으로 좁힌다.
- Absolute path false negative: 누락된 executable path를 Claude regex와 Codex prefix list에 모두 추가한다.

## 정리

`*.bak.YYYYMMDD-HHMMSS` 같은 backup은 local artifact다. 검증 후 정리하고 commit하지 않는다.

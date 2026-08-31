---
name: agents-local-overlay
description: AGENTS.md(공용, commit 가능)와 AGENTS.local.md(개인, gitignore)를 Claude Code, Codex CLI, Kiro CLI에서 누락·중복 없이 로드되게 만드는 agent 중립 rule 파일 규약을 세팅하고 검증할 때 사용한다. git worktree, bare repo + worktree, 서브에이전트 로드 범위, 로컬 전용 지침 주입, CLAUDE.local.md와 Codex 동시 사용, Kiro steering materialize 요청에서 트리거한다.
---

# agents-local-overlay

repo의 rule source는 두 파일이다.

- `AGENTS.md` — 공용 규칙. repo 정책에 따라 commit하거나 gitignore할 수 있다.
- `AGENTS.local.md` — 개인 로컬 규칙. 항상 gitignore하고 commit하지 않는다.

목표는 하나다: 각 CLI가 네이티브로 읽지 못하는 규칙만 보충 경로가 전달한다.
빠지면 agent가 규칙 없이 움직이고, 겹치면 같은 규칙이 context를 두 번 차지한다.

## 지원 등급

| 등급 | 대상 | 방식 |
|---|---|---|
| Tier 1 | Claude Code, Codex CLI | `agents-overlay-context` hook |
| Tier 2 | Kiro CLI v3 | `kiro-cli-overlay`가 steering 문서를 생성 |

Tier 1은 세션 시작, resume/compact, 서브에이전트 상속 여부를 검증 대상으로 본다.
Kiro는 launcher가 만든 steering 파일과 기본 subagent 상속을 확인한다. Kiro
resume/compact의 exact-once 증거가 runtime 로그에 없으면 미검증으로 보고한다. 공통
`setup`/`verify`에서 Kiro 전용 cap 문제는 Tier 2 경고로 보고하고 Claude/Codex 설정을
막지 않는다.

## 전제

- `git` 2.36 이상과 `python3` 3.9 이상이 필요하다.
- 검증 기준 버전은 Claude Code 2.1.232-2.1.235, Codex CLI 0.147.0,
  Kiro CLI package 2.15.1의 v3 engine이다.
- `AGENTS.md`, `AGENTS.local.md`, `CLAUDE.md`, `CLAUDE.local.md`는 NUL이 없는
  UTF-8 regular file이어야 한다. rule 파일과 bridge 파일의 symlink는 읽지 않는다.
- `AGENTS.override.md`는 overlay로 쓰지 않는다. Codex가 같은 디렉터리의
  `AGENTS.md`를 대체하기 때문이다. 존재하면 Codex hook은 rule 대신 notice만 내고
  `verify`가 실패한다.
- Claude bridge는 의미상 한 import 줄만 인정한다. `setup`은 LF 한 줄로 쓴다.
  - `CLAUDE.md`: `@AGENTS.md`
  - `CLAUDE.local.md`: `@AGENTS.local.md`
- Runtime duplicate suppression은 byte-exact bridge만 native delivery로 본다.
  `verify`도 byte-exact bridge를 요구하고, `setup`은 UTF-8 BOM, CRLF/CR,
  trailing whitespace, 마지막 blank line, `@./AGENTS.md`처럼 단일 import로 정규화
  가능한 bridge를 LF 한 줄로 고친다. 정규화 전 CRLF/CR/`@./` bridge는 hook이
  주입하는 방향으로 처리한다. 해당 변형을 CLI native parser가 읽는 버전에서는
  `setup` 전까지 일시 중복될 수 있으므로 `verify`/`setup`으로 LF bridge에 수렴시킨다.
  `core.autocrlf`나 `.gitattributes eol=crlf`가 bridge 파일을 CRLF로 강제하면
  이 규약은 계속 실패하므로 bridge 파일은 LF checkout으로 유지한다.
  leading whitespace, 설명문, 코드펜스, frontmatter, 다른 import가 섞이면 고치지
  않고 실패한다. 이런 non-canonical bridge를 그대로 둔 세션은 native import와 hook
  주입이 일시 중복될 수 있으므로 업그레이드할 때는 먼저 `verify`로 잡고 정리한다.

## Source 위치

`AGENTS.local.md`와 gitignored `AGENTS.md`의 source는 repo당 하나이고 모든
worktree가 공유한다. 위치는 `git worktree list --porcelain -z`의 첫 `worktree`
경로다. tracked `AGENTS.md`는 각 worktree의 현재 checkout과 `HEAD`를 source로
쓴다.

linked worktree의 현재 checkout에는 `AGENTS.local.md`나 `CLAUDE.local.md`를 두지
않는다. 로컬 source와 local Claude bridge(`@AGENTS.local.md`)는 primary source
위치에만 둔다. linked worktree의 Claude 세션에는 hook이 primary `AGENTS.local.md`를
주입한다. linked worktree는 primary worktree 하위에 두지 않는다. parent rule
discovery가 primary bridge를 추가로 읽으면 중복 또는 누락 판단이 불가능해진다.
Claude `--worktree` 기본값은 `.claude/worktrees/` 아래에 worktree를 만들기 때문에
아래 `WorktreeCreate` hook을 설정해 primary 밖의 경로를 쓰거나, 수동으로
`git worktree add ../<repo>-<name>`처럼 primary 밖에 만든 worktree에서 Claude를
시작한다.

bare repo + worktree 구성에서는 `git worktree list --porcelain -z`의 첫 `worktree`
record가 bare metadata dir일 수 있다. 이때 local source는 checkout이 아니라 그
metadata dir의 `AGENTS.local.md`다. 해당 위치는 worktree가 아니므로
`CLAUDE.local.md` bridge를 만들지 않고, hook이 `AGENTS.local.md`를 직접 주입한다.

경로 이름이나 `core.worktree` 값을 추측하지 않는다. runtime script도 Git이
보고한 worktree record만 따른다.

## Repo 세팅

repo마다 한 번 실행한다.

```bash
agents-overlay-context setup
agents-overlay-context verify
```

`setup`은 다음만 수행한다.

- 이미 존재하는 rule source에 대해서만 bridge를 만든다. 아직 `AGENTS.md` /
  `AGENTS.local.md`가 없으면 먼저 파일을 만들고 `setup`을 실행한다.
- 없을 때만 `CLAUDE.md`를 `@AGENTS.md` 한 줄로 만든다.
- 없을 때만 primary source 위치에 `CLAUDE.local.md`를 `@AGENTS.local.md` 한 줄로
  만든다.
- `git check-ignore` 기준으로 필요한 ignore pattern만 추가한다. primary source
  위치에서 직접 `setup`을 실행하면 primary `.gitignore`로 local files를 보호한다.
  linked worktree에서 `setup`을 실행하면 shared git common dir의 `info/exclude`에
  local/reserved files를 보호하고, 다른 worktree의 tracked `.gitignore`는 수정하지
  않는다. 다른 ignore source가 이미 보호하면 exact pattern을 중복으로 추가하지
  않는다.
- linked worktree에서 `setup`을 실행해도 primary source 위치의 `CLAUDE.local.md`는
  생성·정규화될 수 있다. 현재 checkout 전용 tracked `.gitignore`는 건드리지 않는다.
- 기존 bridge, 허용되지 않은 symlink, tracked local file, `AGENTS.override.md`
  문제가 있으면 덮어쓰지 않고 실패한다.
- `setup`은 실행 끝에 `verify`를 다시 실행한다. 마지막 검증에서 실패하면 이미 쓴
  파일을 `changed`로 출력하므로, 그 목록을 보고 정리한 뒤 다시 실행한다.

v1 또는 수동 세팅에서 `CLAUDE.md` / `CLAUDE.local.md`에 import 외의 내용이 있으면
그 내용을 각각 `AGENTS.md` / `AGENTS.local.md`로 옮기고 bridge에는 import 한 줄만
남긴다. 업그레이드할 때는 먼저 `agents-overlay-context verify`를 실행해
non-canonical bridge를 잡은 뒤 정리한다. linked worktree에 독립
`AGENTS.local.md`나 `CLAUDE.local.md`가 있으면 primary source 위치로 옮기거나
제거한다. linked checkout에는 local bridge를 남기지 않는다.

직접 수동으로 만들 경우에도 같은 계약을 지킨다.

```text
CLAUDE.md
@AGENTS.md
```

```text
CLAUDE.local.md
@AGENTS.local.md
```

`AGENTS.local.md`, `CLAUDE.local.md`,
`.kiro/steering/agents-local-overlay.md`는 `git check-ignore`가 성공해야 한다.
`*.local.md`나 `.kiro/steering/` 같은 broad ignore가 이미 있으면 exact pattern을
중복으로 추가하지 않는다.

## Machine 세팅

스킬의 runtime scripts 네 개를 같은 디렉터리에 둔다. hook command는
`agents-overlay-context` 이름만 호출하지만, wrapper가 옆의 Python core를 실행한다.

```bash
mkdir -p "$HOME/.local/bin"
cp "<skill-dir>/scripts/agents-overlay-context" "$HOME/.local/bin/agents-overlay-context"
cp "<skill-dir>/scripts/agents_overlay_context.py" "$HOME/.local/bin/agents_overlay_context.py"
cp "<skill-dir>/scripts/kiro-cli-overlay" "$HOME/.local/bin/kiro-cli-overlay"
cp "<skill-dir>/scripts/kiro_cli_overlay.py" "$HOME/.local/bin/kiro_cli_overlay.py"
chmod 755 "$HOME/.local/bin/agents-overlay-context" \
          "$HOME/.local/bin/agents_overlay_context.py" \
          "$HOME/.local/bin/kiro-cli-overlay" \
          "$HOME/.local/bin/kiro_cli_overlay.py"
```

동기화 확인:

```bash
overlay_sync_failed=0
cmp -s "<skill-dir>/scripts/agents-overlay-context" "$HOME/.local/bin/agents-overlay-context" &&
  echo "agents-overlay-context in sync" || { echo "agents-overlay-context stale"; overlay_sync_failed=1; }
cmp -s "<skill-dir>/scripts/agents_overlay_context.py" "$HOME/.local/bin/agents_overlay_context.py" &&
  echo "agents_overlay_context.py in sync" || { echo "agents_overlay_context.py stale"; overlay_sync_failed=1; }
cmp -s "<skill-dir>/scripts/kiro-cli-overlay" "$HOME/.local/bin/kiro-cli-overlay" &&
  echo "kiro-cli-overlay in sync" || { echo "kiro-cli-overlay stale"; overlay_sync_failed=1; }
cmp -s "<skill-dir>/scripts/kiro_cli_overlay.py" "$HOME/.local/bin/kiro_cli_overlay.py" &&
  echo "kiro_cli_overlay.py in sync" || { echo "kiro_cli_overlay.py stale"; overlay_sync_failed=1; }
[ "$overlay_sync_failed" -eq 0 ]
```

## Claude Code

`~/.claude/settings.json`의 `hooks`에 추가한다. 기존 `hooks`가 있으면 병합한다.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "sh \"$HOME/.local/bin/agents-overlay-context\" json SessionStart CLAUDE.md CLAUDE.local.md . claude-session"
          }
        ]
      }
    ],
    "SubagentStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "sh \"$HOME/.local/bin/agents-overlay-context\" json SubagentStart CLAUDE.md CLAUDE.local.md . claude-subagent"
          }
        ]
      }
    ],
    "WorktreeCreate": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "sh \"$HOME/.local/bin/agents-overlay-context\" claude-worktree-create"
          }
        ]
      }
    ]
  }
}
```

- `SessionStart`에 matcher를 두지 않는다. 새 세션과 resume 모두 같은 hook으로
  현재 rule을 확인한다.
- Claude hook은 stdin JSON의 `cwd`를 active worktree로 사용한다.
  `CLAUDE_PROJECT_DIR`는 source 판정 기준으로 쓰지 않는다.
- Claude settings가 native bridge나 hook을 끄면 이 규약은 지원하지 않는다.
  아래 CLI flag와 settings key는 전제의 검증 기준 버전에서 확인한 이름이다.
  `claudeMdExcludes`가 `CLAUDE.md` / `CLAUDE.local.md`를 제외하지 않아야 하고,
  CLI `--setting-sources` 또는 SDK `settingSources`를 지정할 때는 `project`와
  `local`을 모두 포함해야 한다. `--bare`, `--safe-mode`,
  `disableAllHooks=true`, managed settings의 `allowManagedHooksOnly=true`는 hook
  또는 bridge를 꺼서 silent miss를 만들 수 있으므로 overlay 검증 대상이 아니다.
- `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`이면 bridge가 꺼진 것이므로 hook이 공용과
  로컬 규칙을 모두 주입한다.
- `SubagentStart`에서 `agent_type=fork`는 부모 context를 상속하므로 주입하지
  않는다.
- built-in `Explore`, `Plan`, 또는 `agent_type`이 판정 불가인 경우에는 subagent
  checkout 기준으로 공용·로컬 규칙을 모두 주입한다.
- custom subagent 이름으로 `Explore`, `Plan`, `fork`를 쓰지 않는다. hook 입력만으로
  built-in/custom 출처를 구분할 수 없다.
- `claude-worktree-create`는 Claude `--worktree` 기본 nested 배치를 primary 밖의
  git worktree로 바꾼다. 기본 위치는
  `$HOME/.cache/agents-local-overlay/claude-worktrees/<repo>-<hash>/<name>`이고,
  `AGENTS_OVERLAY_CLAUDE_WORKTREE_DIR`로 바꿀 수 있다. 기본 base ref는 `HEAD`이며
  `AGENTS_OVERLAY_CLAUDE_WORKTREE_BASE_REF`로 명시 변경할 수 있다. branch 이름은
  `agents-overlay/<name>`이다. 같은 target이나 branch가 이미 있으면 실패한다.
  `WorktreeCreate` hook은 Claude 기본 git worktree 생성을 대체하므로
  `.worktreeinclude`는 적용되지 않는다.

## Codex CLI

`~/.codex/config.toml`에 추가한다. 같은 key가 이미 있으면 중복 key를 만들지 말고
기존 값을 조정한다.

```toml
project_doc_max_bytes = 32768
project_doc_fallback_filenames = []

[[hooks.SessionStart]]

[[hooks.SessionStart.hooks]]
type = "command"
command = 'sh "$HOME/.local/bin/agents-overlay-context" json SessionStart AGENTS.md - . codex-session'
additionalContextLimit = 0

[[hooks.SubagentStart]]

[[hooks.SubagentStart.hooks]]
type = "command"
command = 'sh "$HOME/.local/bin/agents-overlay-context" json SubagentStart AGENTS.md - . codex-subagent'
additionalContextLimit = 0
```

- Codex는 `AGENTS.md`를 네이티브로 읽으므로 hook은 `AGENTS.local.md`와 worktree
  보충분만 넣는다. 검증 기준 Codex CLI 0.147.0에서는 subagent도 `AGENTS.md`를
  native로 받으므로 `SubagentStart`도 shared native source를 `AGENTS.md`로 둔다.
  hook command 인자의 `-`는 해당 native source가 없으므로 hook이 필요한 규칙을 직접
  주입해야 한다는 뜻이다.
- Codex가 네이티브로 읽는 `AGENTS.md`는 `project_doc_max_bytes` 이하여야 한다.
  이 규약의 기준은 32768 bytes다. 넘으면 hook이 잘린 prefix와 remainder를
  분리해 복구할 수 없으므로 runtime notice를 내고 `verify`가 실패한다.
- Codex config에서 `project_doc_max_bytes`를 올린 환경만
  `AGENTS_OVERLAY_CODEX_PROJECT_DOC_MAX_BYTES`를 같은 byte 값으로 맞춰
  `setup`/`verify` 기준을 올릴 수 있다.
- v1에서 큰 hook context를 쓰고 있었다면 아래 runtime cap 기준에 맞게
  `AGENTS.md` / `AGENTS.local.md`를 줄인 뒤 업그레이드한다. 이 버전은 전문을 잘라
  넣지 않고 cap 초과 notice로 실패를 드러낸다.
- `project_doc_fallback_filenames`에 `AGENTS.local.md`를 넣지 않는다. fallback은
  `AGENTS.md`가 없는 자리의 대체 파일이므로 local overlay 병합에 쓰면 중복된다.
- `additionalContextLimit = 0`을 쓰되 runtime core가 자체 cap을 건다. 기본 cap은
  Claude 10000자, Codex 32768자, raw/Kiro 32768자다. 필요하면
  `AGENTS_OVERLAY_MAX_CHARS`로 Codex/raw/Kiro cap을 명시적으로 올린다. Claude cap은
  검증 기준 버전에서 10000자를 넘는 hook context 전달이 확인되지 않았기 때문에 이
  환경변수로 10000자보다 높아지지 않는다. 10000보다 낮은 값을 넣으면 Claude cap도
  그 값으로 낮아진다. 이 스킬은 Claude/Codex 동시 Tier 1 지원을 기준으로 검증하므로
  Claude full injection 경로에서 `AGENTS.md`와 `AGENTS.local.md`의 합산 hook 주입
  예산은 10000자 이하여야 하며, 넘으면 `setup`/`verify`가 전체 overlay 설정을
  실패시킨다. 넘는 경우 두 rule 파일을 줄이거나, 세션마다 항상 필요하지 않은 내용은
  overlay 밖의 일반 문서로 옮겨 명시적으로 열게 한다.
- `AGENTS_OVERLAY_CODEX_PROJECT_DOC_MAX_BYTES`와 `AGENTS_OVERLAY_MAX_CHARS`는
  `verify`를 실행한 shell뿐 아니라 실제 Claude/Codex/Kiro hook 프로세스가 상속하는
  환경에도 있어야 한다. GUI, launchd, 다른 launcher에서 env가 빠지면 `verify`와
  runtime cap 판정이 갈릴 수 있으므로 가능하면 기본 cap 안으로 rule 파일을 줄인다.
- `/hooks`에서 두 hook을 trust한다. command 문자열이 바뀌면 다시 trust해야 한다.
  script 내용만 바뀌면 기존 trust hash는 유지된다.

## Kiro CLI v3

Kiro는 `kiro-cli-overlay`로 시작한다.

```bash
kiro-cli settings chat.disableInheritingDefaultResources false
kiro-cli settings list --format json
command -v kiro-cli-overlay
```

`kiro-cli-overlay`는 Git worktree 안에서만 실행한다. 실행할 때마다 repo top의
`.kiro/steering/agents-local-overlay.md`를 원자적으로 다시 만들고
`kiro-cli --v3`를 실행한다.

- 생성 파일은 launcher 전용 첫 줄이 있는 regular file이어야 한다.
- `.kiro`, `.kiro/steering`, 생성 파일 중 하나라도 symlink면 중단한다.
- 생성 파일이 tracked이거나 ignored가 아니면 중단한다.
- workspace에서 `chat.disableInheritingDefaultResources=true`이면 custom agent가
  `AGENTS.md`를 받지 않으므로 지원 조건을 벗어난다.
- raw `kiro-cli`와 `kiro-cli chat --no-interactive`는 overlay 동기화를 우회하므로
  이 규약의 검증 대상이 아니다.

## 검증

source 변경 후에는 deterministic test를 먼저 실행한다.

```bash
SKILL_DIR="<skill-dir>"
python3 "$SKILL_DIR/tests/test_overlay_context.py"
sh -n "$SKILL_DIR/scripts/agents-overlay-context"
sh -n "$SKILL_DIR/scripts/kiro-cli-overlay"
python3 -m py_compile \
  "$SKILL_DIR/scripts/agents_overlay_context.py" \
  "$SKILL_DIR/scripts/kiro_cli_overlay.py"
```

repo 적용 후에는 대상 repo에서 다음을 실행한다.

```bash
agents-overlay-context verify
```

live 확인은 canary가 들어간 임시 repo에서 한다. Codex는 먼저 `/hooks`에서
`SessionStart`와 `SubagentStart` hook이 trusted 상태인지 확인한다.

```bash
overlay_tmp_root="$(mktemp -d)"
overlay_repo="$overlay_tmp_root/repo"
overlay_worktree="$overlay_tmp_root/repo-linked"
mkdir "$overlay_repo"
cd "$overlay_repo"
git init -q
printf '%s\n' '# shared' 'codename bluebird' > AGENTS.md
printf '%s\n' '# local' 'marker emerald-42' > AGENTS.local.md
printf '%s\n' 'AGENTS.local.md' 'CLAUDE.local.md' '.kiro/steering/agents-local-overlay.md' > .gitignore
git add AGENTS.md .gitignore
git -c user.email=overlay@example.test -c user.name='Overlay Test' commit -qm overlay-canary
agents-overlay-context setup
agents-overlay-context verify
git worktree add -q "$overlay_worktree"
Q="What is the project codename and what is the personal marker? Each on its own line, MISSING if unknown. Do not use tools."
for repo in "$overlay_repo" "$overlay_worktree"; do
  (cd "$repo" && agents-overlay-context setup)
  (cd "$repo" && agents-overlay-context verify)
  (cd "$repo" && claude --debug-file "$overlay_tmp_root/claude-$(basename "$repo").log" -p "$Q")
  (cd "$repo" && codex exec "$Q")
done
```

main checkout과 worktree 양쪽에서 공용 canary와 local canary가 모두 보여야 한다.
중복 여부는 모델 답변 출현 횟수가 아니라 Claude debug JSONL, Codex session
JSONL, Kiro session JSONL의 실제 injected context 또는 steering document 수로 센다.
해당 로그를 찾거나 해석할 수 없으면 exact-once는 미검증으로 보고한다. 정리 전에는
`overlay_tmp_root`가 방금 만든 검증용 고유 경로인지 확인한다.

Kiro는 Tier 2이고 `kiro-cli-overlay`가 대화형 TUI를 시작한다. 위 임시 repo와
linked worktree 각각에서 `kiro-cli-overlay`를 실행한 뒤 TUI 안에서 같은 `Q`를 직접
입력해 canary를 확인한다. TUI를 종료한 뒤 다음 repo를 확인한다.

live 확인이 끝난 뒤 임시 repo를 정리한다.

```bash
cd "$overlay_tmp_root/.."
command -v trash >/dev/null 2>&1 && trash "$overlay_tmp_root" || printf 'remove manually: %s\n' "$overlay_tmp_root"
```

## 제약

- 이미 진행 중인 세션에서 rule 파일을 바꾸면 이전 rule text는 history에 남는다.
  변경 직후에는 새 세션을 시작한다.
- hook output cap을 넘으면 rule 전문 대신 cap 초과 안내만 들어간다. 전문이 잘려
  들어가는 상태보다 실패가 명확한 상태가 낫다.
- wrapper 또는 Python core 실행 자체가 실패하거나 Git inspection을 진행할 수 없으면
  hook 오류로 실패한다. Claude debug 로그, Codex hook event/session 로그, Kiro
  wrapper stderr에서 오류를 확인한 뒤 고치고 새 세션을 시작한다.
- rule source가 symlink, non-UTF-8, `AGENTS.override.md`, linked worktree local file
  위반처럼 overlay가 읽으면 안 되는 상태면 hook은 `[agents-local-overlay] Rule file
  not loaded by overlay: ...` notice를 additional context로 넣고 overlay rule text는
  넣지 않는다. notice를 보면 보고된 파일을 regular UTF-8 rule/bridge 계약에 맞게
  고친 뒤 새 세션을 시작한다.
- native rule로 전달된다고 판단한 파일도 UTF-8/NUL 검증을 통과해야 한다. 검증에
  실패하면 hook은 native 전달로 보지 않고 notice를 넣는다.
- Codex transcript format은 안정된 public contract가 아니다. major 업데이트 후에는
  resume, compact, subagent fork mode를 다시 검증한다.
- local overlay는 repo당 하나다. worktree마다 서로 다른 `AGENTS.local.md`를 두는
  구성은 지원하지 않는다.

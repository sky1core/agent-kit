---
name: agents-local-overlay
description: AGENTS.md(공용, commit 가능)와 AGENTS.local.md(개인, gitignore)를 Claude Code, Codex CLI, Kiro CLI에서 로드되게 만드는 agent 중립 rule 파일 규약을 세팅하고 검증할 때 사용한다. git worktree, bare repo + worktree, 서브에이전트 로드 범위, 로컬 전용 지침 주입, CLAUDE.local.md와 Codex 동시 사용, Kiro steering materialize 요청에서 트리거한다.
---

# agents-local-overlay

repo의 rule source는 두 파일이다.

- `AGENTS.md` — 공용 규칙. repo 정책에 따라 commit하거나 gitignore할 수 있다.
- `AGENTS.local.md` — 개인 로컬 규칙. 항상 gitignore하고 commit하지 않는다.

## 채널 계약

핵심 원칙: **논리적 rule source 하나당, 한 CLI 컨텍스트에서 전달 채널은
정확히 하나다.** hook은 native 로더가 무엇을 읽었는지 추론하지 않고, 같은
source를 native와 hook 사이에서 동적으로 전환하지 않는다.

| CLI 컨텍스트 | `AGENTS.md` (공용) | `AGENTS.local.md` (로컬) |
|---|---|---|
| Claude 메인 세션 | native: commit된 `CLAUDE.md` → `@AGENTS.md` bridge | native: primary는 `CLAUDE.local.md` → `@AGENTS.local.md` bridge, 그 외 worktree는 hook이 갱신하는 생성 복사본 `CLAUDE.local.md` |
| Claude subagent — built-in `Explore`·`Plan` | hook 주입 (이 둘만 native memory를 받지 않음) | hook 주입 |
| Claude subagent — general-purpose·custom·plugin·fork | native (메인 세션의 project·local memory를 상속) | native |
| Codex 세션·subagent | native: project doc 로더 | hook 주입 |
| Kiro v3 | native: default-resource 채널 | launcher가 생성하는 steering 문서 |

보증 문구: 각 `SessionStart`/`SubagentStart` hook invocation(또는 native
load)마다 현재 rule snapshot을 한 번 제공한다. resume·compact 후 이전 주입이
세션 history에 남아 생기는 중복은 보증 대상에서 제외한다. 채널의 전제조건이
확인되지 않으면 rule을 조용히 빼는 대신 `[agents-local-overlay] ...` notice를
context에 넣고, `verify`가 실패한다.

## 전제

- `git` 2.36 이상과 `python3` 3.11 이상(`tomllib` 필요)이 필요하다.
- Claude Code 2.1.232, Codex CLI 0.147.0, Kiro CLI 2.15.1(v3 engine) 이상을
  전제한다. 이 스킬이 쓰는 CLI 인터페이스(hook 이벤트, Kiro v3 engine·settings
  조회)가 그 미만에서는 없을 수 있으므로, `verify`는 설치된 CLI의 `--version`이
  이보다 낮거나 판정 불가면 WARN을 낸다.
  그 이상의 버전은 각 CLI의 문서 계약(hook 입력·native memory 로드 범위)을
  따르는 것으로 보고 버전으로 판정하지 않는다.
- `AGENTS.md`, `AGENTS.local.md`, `CLAUDE.md`, `CLAUDE.local.md`는 NUL이 없는
  UTF-8 regular file이어야 한다. rule/bridge 파일의 symlink는 읽지 않는다.
- `AGENTS.override.md`는 쓰지 않는다. Codex가 같은 디렉터리의 `AGENTS.md`를
  완전히 대체하기 때문이다. 발견되면 `verify`가 실패하고 Kiro launcher는
  중단하며, `AGENTS.local.md`를 쓰는 repo에서는 hook도 notice를 낸다.
- 세션 hook의 notice는 이 규약을 쓰는 repo에서만 나온다. `AGENTS.local.md`(또는
  그 생성 복사본이나 local bridge)가 없는 repo에서 SessionStart hook은 침묵한다.
  overlay를 쓰지 않는 일반 repo에 세션마다 잔소리를 내지 않기 위한 의도된
  범위다. SubagentStart 주입은 opt-in과 무관하게 존재하는 rule 파일만 넣되,
  그 worktree에 `AGENTS.md`가 없는데 primary에는 있거나 primary 밖 worktree에
  독립 `AGENTS.local.md`가 있으면 notice를 낸다.
- bridge는 의미상 한 import 줄만 인정한다. `setup`은 BOM, CRLF/CR,
  `@./AGENTS.md` 같은 변형을 LF 한 줄로 정규화하고, 설명문이나 다른 내용이
  섞인 bridge는 고치지 않고 실패한다.

## Source 위치

`AGENTS.local.md`의 source는 repo당 하나이고, `git worktree list --porcelain
-z`의 첫 `worktree` record 위치에 둔다. bare repo + worktree 구성에서는 그
record가 bare metadata dir이며 source도 그 안에 둔다.

- primary worktree: `CLAUDE.local.md`는 `@AGENTS.local.md` 한 줄 bridge다.
  같은 디렉터리 import는 Claude가 native로 읽는다.
- 그 외 worktree: `CLAUDE.local.md`는 overlay가 생성하는 **내용 복사본**이다
  (첫 줄이 생성 marker). worktree 밖을 가리키는 import는 Claude가 따라가지
  않으므로 import 방식은 쓰지 않는다. 복사본은 `setup`,
  `claude-worktree-create` hook, 그리고 각 Claude 세션 시작 시 SessionStart
  hook이 원본과 다르면 다시 쓴다. hook의 갱신은 같은 세션에 반영된다.
- 생성 복사본을 직접 편집하지 않는다. 내용 변경은 primary의
  `AGENTS.local.md`에서 한다.
- `AGENTS.md`는 각 worktree의 checkout에 있어야 native가 읽는다. commit된
  repo면 자동이고, gitignore된 `AGENTS.md`를 쓰는 repo는 각 worktree에 파일을
  두어야 한다. 없으면 어느 worktree에서 실행해도 `verify`가 실패하고, 해당
  worktree의 hook이 notice를 낸다.
- worktree는 서로 어떤 조합으로도 중첩하지 않는다. Claude의 parent 디렉터리
  `CLAUDE.md` discovery가 바깥 worktree의 rule을 함께 읽어 중복되기 때문이다.
  `verify`가 모든 worktree 쌍을 검사해 실패로 보고하고, 중첩된 worktree의
  세션 hook도 `Rules may be duplicated` notice를 낸다.
- worktree를 `git worktree move` 없이 옮기면 `git worktree list`에 새 위치가
  없으므로 세션 hook은 복사본을 갱신하지 않고 notice를 낸다. `git worktree
  repair` 후 `setup`을 다시 실행한다. 등록됐지만 디렉터리가 없는 worktree는
  `verify`가 FAIL로 보고한다(`git worktree prune` 또는 `repair`).

## Repo 세팅

repo마다 한 번 실행한다. rule source 파일을 먼저 만들고 실행한다.

```bash
agents-overlay-context setup
agents-overlay-context verify
```

`setup`은 다음만 수행한다.

- 존재하는 source에 대해서만 bridge를 만든다. `CLAUDE.md`(`@AGENTS.md`),
  primary `CLAUDE.local.md`(`@AGENTS.local.md`)를 없을 때 만들고, 정규화
  가능한 변형은 LF 한 줄로 고친다.
- primary 밖 worktree마다 생성 복사본 `CLAUDE.local.md`를 만들거나 갱신한다.
- `git check-ignore` 기준으로 필요한 ignore pattern만 추가한다. primary에서
  실행하면 `.gitignore`에 먼저 쓰고, 그래도 커버되지 않는 worktree가 남으면
  (worktree마다 checkout된 `.gitignore`가 다를 수 있으므로) 모든 worktree에
  적용되는 shared `info/exclude`에 보충한다. 대상: `AGENTS.local.md`,
  `CLAUDE.local.md`, `.kiro/steering/agents-local-overlay.md`. `verify`도 이
  세 경로의 tracked/ignored 상태를 worktree마다 검사한다.
- 마지막에 `verify`를 실행한다. tracked 로컬 파일, 내용이 섞인 bridge,
  overlay가 만들지 않은 `CLAUDE.local.md`는 덮어쓰지 않고 실패한다.

`CLAUDE.md` bridge와 `.gitignore`의 ignore pattern은 repo 정책이 허용하면
commit해 둔다. commit하지 않으면 새 worktree마다 `setup`을 다시 실행해야
하고, 그때까지 그 worktree의 세션 hook이 bridge 부재 notice를 낸다.

## Machine 세팅

runtime scripts 네 개를 같은 디렉터리에 둔다.

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
for f in agents-overlay-context agents_overlay_context.py kiro-cli-overlay kiro_cli_overlay.py; do
  cmp -s "<skill-dir>/scripts/$f" "$HOME/.local/bin/$f" && echo "$f in sync" || { echo "$f stale"; overlay_sync_failed=1; }
done
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
    ],
    "WorktreeRemove": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "sh \"$HOME/.local/bin/agents-overlay-context\" claude-worktree-remove"
          }
        ]
      }
    ]
  }
}
```

- `SessionStart` hook은 rule을 주입하지 않는다. primary 밖 worktree의 생성
  복사본을 원본과 동기화하고, 전제조건이 깨졌을 때만 notice를 넣는다. resume
  에서도 같은 hook이 복사본을 갱신한다.
- `SubagentStart`는 `agent_type`이 built-in `Explore`·`Plan`일 때만 공용·로컬
  snapshot을 주입한다. Claude 문서(sub-agents의 subagent context 항목)가 이
  둘만 `CLAUDE.md`를 생략한다고 명시하기 때문이다.
  general-purpose·custom·plugin·fork subagent는 메인 세션의 project·local
  memory를 native로 상속하므로 주입하지 않는다(주입 시 중복). 주입 예산은 합산
  10,000자이며 넘으면 rule 대신 notice가 들어가고 `verify`가 실패한다.
- custom subagent 이름으로 `Explore`·`Plan`을 쓰지 않는다. hook 입력의
  `agent_type`만으로 built-in과 구분할 수 없어, 이 두 이름은 memory-less
  built-in으로 취급한다.
- `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`은 native 채널을 끄므로 지원하지 않는다.
  설정돼 있으면 hook이 notice만 낸다.
- 일반 Claude 설정(`~/.claude/settings.json`, repo `.claude/settings.json`,
  `.claude/settings.local.json`)의 두 key가 채널을 조용히 끊는다: `disableAllHooks`가
  true면 subagent 주입·session notice hook 자체가 안 뜨고, `claudeMdExcludes`가
  `CLAUDE.md`/`CLAUDE.local.md` bridge 경로를 매칭하면 native bridge가 로드에서
  빠진다. 둘 다 런타임 hook으로는 잡히지 않으므로 `verify`가 세 계층 설정을
  읽어 해당 시 FAIL한다. `claudeMdExcludes` 판정은 절대경로와 `**`·`*`·`?`
  glob만 평가하고, brace·character class·extglob·negation이 든 패턴은 WARN으로
  직접 확인을 요구한다.
- `claude-worktree-create`는 Claude `--worktree`의 기본 nested 배치를 primary
  밖의 git worktree로 바꾸고 생성 복사본까지 만든다. 기본 위치는
  `$HOME/.cache/agents-local-overlay/claude-worktrees/<repo>-<hash>/<name>`,
  `AGENTS_OVERLAY_CLAUDE_WORKTREE_DIR`로 변경한다. base ref 기본값은 `HEAD`,
  `AGENTS_OVERLAY_CLAUDE_WORKTREE_BASE_REF`로 변경한다. branch 이름은
  `agents-overlay/<name>`이다. `.worktreeinclude`는 적용되지 않는다. 로컬
  source가 있으면 복사본을 쓰기 전에 `AGENTS.local.md`, `CLAUDE.local.md`,
  `.kiro/steering/agents-local-overlay.md`가 새 worktree에서 ignored인지
  확인하고, 아니면 shared `info/exclude`에 보충한다. 복사본을 둘 수 없으면
  worktree와 branch를 되돌리고 실패한다(`info/exclude`에 보충한 pattern은
  남는다). 같은 이름의 worktree나 branch가 남아 있으면 만들지 않고
  실패하므로, worktree는 제거하고 branch는 merge하거나 `git branch -D`로 지운
  뒤 다시 만든다. worktree 디렉터리를 `git worktree remove` 없이 지운 뒤 같은
  이름을 쓰면 `git worktree prune`을 안내하고 실패한다.
- `claude-worktree-remove`는 `claude-worktree-create`가 만든 worktree만
  제거한다. create 때와 같은 `AGENTS_OVERLAY_CLAUDE_WORKTREE_DIR` 기준
  `<repo>-<hash>` 디렉터리의 바로 아래에 있고 `agents-overlay/<name>` branch를
  checkout한 worktree여야 하며, 그 외는 제거하지 않고 실패한다. `--force` 없이
  제거하므로 수정된 tracked 파일이나 untracked 파일이 있으면 git이 거부해
  실패하고 worktree는 그대로 남는다(ignored인 생성 복사본은 제거를 막지
  않는다). 제거 후 branch는 tip commit이 다른 local·remote branch에 포함돼 있을
  때만 지우고, 아니면 남긴다. Claude는 이 hook의 stderr와 실패를 debug
  로그에서만 보여주므로, 남은 worktree와 branch는 같은 이름으로 다음 create할
  때의 실패 메시지로 드러난다. Claude가 이 hook을 호출하는 시점과 자체 정리와의
  순서는 Claude 문서의 WorktreeRemove 계약을 따르며, 이 스킬은 그 순서를
  검사하지 않는다. `claude rm`으로 background 세션을 지우는 경로는 이 hook을 거치지
  않아 worktree 등록과 branch가 남는다 — `git worktree prune`과 `git branch -D
  agents-overlay/<name>`으로 정리한다.
- hook 프로세스가 관찰할 수 없는 one-session 설정(`--settings`,
  `--setting-sources`, SDK `settingSources`, managed settings)으로 native
  memory나 hook을 끄는 실행은 이 규약의 보증 대상이 아니다.

## Codex CLI

`~/.codex/config.toml`에 추가한다(`CODEX_HOME`이 설정돼 있으면 그 아래
`config.toml`). 같은 key가 이미 있으면 중복 key를 만들지 말고 기존 값을
조정한다.

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

- 공용 `AGENTS.md`는 Codex native 로더만 전달한다. hook은 `AGENTS.local.md`
  내용만 주입하며, native 로드 여부를 추론하지 않는다. `AGENTS.local.md`는
  native가 읽지 않으므로 두 채널은 겹치지 않는다.
- repo(각 worktree 경로)를 Codex에서 trust한다. untrusted project의 native
  `AGENTS.md` 로드는 Codex 버전에 따라 생략될 수 있으므로 `verify`가 trust
  entry 부재를 WARN으로 보고한다.
- `project_doc_fallback_filenames`는 `[]` 또는 생략, `project_root_markers`는
  두지 않는다. 위반하면 `verify`가 실패한다. `AGENTS.md`는
  `project_doc_max_bytes`(기본 32768 bytes) 이하여야 한다.
- hook의 로컬 주입 예산은 32,768자다. 넘으면 rule 대신 notice가 들어가고
  `verify`가 실패한다.
- `/hooks`에서 두 hook을 trust한다. command 문자열이 바뀌면 다시 trust해야
  한다. script 내용만 바뀌면 기존 trust hash는 유지된다.
- resume·compact 후 Codex가 hook을 다시 실행하면 로컬 snapshot이 다시
  주입될 수 있다. 이 중복은 보증 대상이 아니며 transcript를 읽어 막지 않는다.
- raw `codex --profile ...` / `codex --config ...` 같은 one-session override로
  hook이나 project doc 로딩을 끈 실행은 보증 대상이 아니다.

## Kiro CLI v3

Kiro는 `kiro-cli-overlay`로 시작한다. Git worktree 안에서만 실행한다.

```bash
command -v kiro-cli-overlay
kiro-cli-overlay
```

- launcher는 실행할 때마다 worktree top의
  `.kiro/steering/agents-local-overlay.md`를 로컬 snapshot으로 원자적으로
  다시 만들고 `kiro-cli --v3`를 실행한다. 로컬 source가 없으면 생성 파일을
  제거한다. 공용 `AGENTS.md`는 Kiro native default-resource 채널만 전달한다.
- launcher는 `kiro-cli settings list --format json`으로
  `chat.disableInheritingDefaultResources`가 명시적으로 `false`인지 확인하고,
  key가 없거나 boolean이 아니면 `false`로 다시 쓴 뒤 재확인한다. `true`면
  중단한다. `verify`도 이 값이 `true`거나 boolean이 아니면 FAIL, key 부재나
  확인 불가는 WARN으로 보고한다.
- 생성 파일은 launcher 전용 첫 줄이 있는 regular file이어야 하며, tracked
  이거나 ignored가 아니면 중단한다. `.kiro`, `.kiro/steering`, 생성 파일이
  symlink면 중단한다.
- 공용 채널 전제가 깨져도 중단한다: `AGENTS.override.md`가 있음, 이 worktree에
  `AGENTS.md`가 없는데 primary에는 있음, `AGENTS.md`가 symlink·non-UTF-8·NUL
  포함, primary 밖 worktree에 독립 `AGENTS.local.md`가 있음, 로컬 body가
  32,768자 초과. 중단 사유는 stderr로 출력한다.
- raw `kiro-cli`와 `kiro-cli chat --no-interactive`는 steering 동기화를
  우회하므로 보증 대상이 아니다.

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

repo 적용 후에는 대상 repo에서 `agents-overlay-context verify`를 실행한다.
FAIL은 채널 전제가 깨진 상태이므로 고쳐야 하고, WARN은 그 조합이 검증되지
않았다는 뜻이므로 원인을 확인하고 해소하거나 감수 여부를 결정한다.

live 확인은 canary가 들어간 임시 repo에서 한다. 질문에 기대 답을 나열하면
모델이 질문에서 베낄 수 있으므로, 반드시 값을 노출하지 않는 open-form으로
묻는다.

```bash
overlay_tmp_root="$(mktemp -d)"
overlay_repo="$overlay_tmp_root/repo"
mkdir -p "$overlay_repo"
cd "$overlay_repo"
git init -q
printf '%s\n' '# shared' 'shared canary codeword: BLUE-7Q' > AGENTS.md
printf '%s\n' '# local' 'local canary codeword: GREEN-42X' > AGENTS.local.md
git add AGENTS.md
git -c user.email=overlay@example.test -c user.name='Overlay Test' commit -qm overlay-canary
agents-overlay-context setup
git add CLAUDE.md .gitignore
git -c user.email=overlay@example.test -c user.name='Overlay Test' commit -qm bridge
git worktree add -q "$overlay_tmp_root/repo-linked"
Q='List every "canary codeword" value that appears in your context. Reply with only the values, comma-separated, or NONE. Do not use tools.'
for repo in "$overlay_repo" "$overlay_tmp_root/repo-linked"; do
  (cd "$repo" && agents-overlay-context setup)
  (cd "$repo" && claude -p "$Q")
  (cd "$repo" && codex exec "$Q" < /dev/null)
done
```

각 실행에서 공용과 로컬 codeword가 모두 보여야 한다. Kiro는 같은 repo에서
`kiro-cli-overlay`를 실행하고 TUI 안에서 같은 질문을 한다. 확인이 끝나면
임시 repo를 정리한다(`trash "$overlay_tmp_root"`).

## 제약

- 진행 중인 세션에서 rule 파일을 바꾸면 이전 rule text는 history에 남는다.
  변경 직후에는 새 세션을 시작한다.
- rule source가 symlink, non-UTF-8, NUL 포함이면 hook은 해당 rule의 text를
  빼고 `Rule file not loaded by overlay` notice를 넣는다. 다른 정상 rule은
  계속 들어간다.
- hook 주입 예산(Claude subagent 합산 10,000자, Codex/Kiro 로컬 32,768자)을
  넘으면 잘린 전문 대신 notice가 들어간다. rule 파일을 줄이거나, 세션마다
  필요하지 않은 내용은 일반 문서로 옮겨 명시적으로 열게 한다.
- 로컬 overlay는 repo당 하나다. worktree마다 다른 `AGENTS.local.md`를 두는
  구성은 지원하지 않는다. primary 밖 worktree의 독립 `AGENTS.local.md`는
  primary source 존재 여부와 무관하게 `verify`가 실패로 보고하고, 그 worktree의
  세션 hook도 notice를 낸다.
- hook의 notice는 세션이 실행되는 worktree 기준으로만 판단한다(그 worktree의
  `AGENTS.override.md`, `AGENTS.md` 부재·유효성, 독립 `AGENTS.local.md`,
  `git worktree list` 등록 여부, 다른 worktree와의 중첩). 다른 worktree까지
  포함한 전체 검사는 `verify` 소관이다.
- nested `CLAUDE.md`, 하위 디렉터리의 `AGENTS.md`는 각 CLI의 native 기능이고
  이 규약의 계약 밖이므로 검사하지 않는다.
- wrapper나 Python core 실행 자체가 실패하면 hook 오류로 드러난다. Claude
  debug 로그, Codex hook event 로그, Kiro launcher stderr에서 원인을 확인한
  뒤 새 세션을 시작한다.

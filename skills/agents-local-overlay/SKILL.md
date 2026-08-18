---
name: agents-local-overlay
description: AGENTS.md(공용, commit)와 AGENTS.local.md(개인, gitignore)를 Claude Code, Codex CLI, Kiro CLI에서 동일하게 로드되게 만드는 agent 중립 rule 파일 규약을 세팅하고 검증할 때 사용한다. git worktree, bare repo + worktree, 서브에이전트 사용 시의 로드 범위까지 다룬다. "AGENTS.local.md 세팅", "로컬 전용 지침 주입", "에이전트 공통 rule 파일", "CLAUDE.local.md를 Codex에서도", "worktree에서 로컬 규칙" 같은 요청에서 트리거한다.
version: 1.3.1
---

# agents-local-overlay

repo에는 agent 중립 이름의 rule 파일 두 개만 둔다.

- `AGENTS.md` — 공용 규칙. commit 대상.
- `AGENTS.local.md` — 개인 로컬 규칙. gitignore 대상. commit 금지.

동작 규칙은 하나다: **로컬 오버레이의 source는 repo당
`AGENTS.local.md` 하나이고, 그 repo의 모든 git worktree가 이를 공유한다.**
파일 위치는 repo 구성에 따라 정해진다.

- 일반 repo: main checkout root (예: `<repo>/AGENTS.local.md`)
- bare repo + worktree 구성: bare git 디렉터리 안 (예: `<repo>.git/AGENTS.local.md`)
- submodule: 그 submodule의 checkout root

아래 hook들은 실행 위치가 어느 worktree든 위 규칙의 위치를 계산해 같은
파일을 읽는다.

| | `AGENTS.md` (공용) | `AGENTS.local.md` (로컬) |
|---|---|---|
| Claude Code | `CLAUDE.md` bridge (commit) | `CLAUDE.local.md` bridge + 전역 hook 병용 |
| Codex CLI | 네이티브 | 전역 SessionStart hook |
| Kiro CLI | 네이티브 | 전역 custom agent의 agentSpawn hook |

검증 기준 버전: Claude Code 2.1.232–2.1.234, Codex CLI 0.147.0,
Kiro CLI 2.15.1–2.18.1. hook 스키마는 신기능이라 major 업데이트 후에는
아래 검증 절차를 다시 돌린다.

## repo 세팅 (repo마다 1회)

```bash
touch AGENTS.md
[ -f CLAUDE.md ] || printf '@AGENTS.md\n' > CLAUDE.md
grep -qxF 'AGENTS.local.md' .gitignore 2>/dev/null || printf '\nAGENTS.local.md\n' >> .gitignore
grep -qxF 'CLAUDE.local.md' .gitignore 2>/dev/null || printf '\nCLAUDE.local.md\n' >> .gitignore
git check-ignore -q AGENTS.local.md && git check-ignore -q CLAUDE.local.md && echo ok
```

- `CLAUDE.md`는 `@AGENTS.md` 한 줄짜리 bridge다. Claude Code는 `AGENTS.md`를
  직접 로드하지 않으므로 이 bridge가 없으면 Claude에서 공용 규칙이 조용히
  빠진다. Codex CLI와 Kiro CLI는 `AGENTS.md`를 네이티브로 로드한다.
- 기존 `CLAUDE.md`에 이미 내용이 있으면 덮어쓰지 말고, 공용 규칙을
  `AGENTS.md`로 옮긴 뒤 bridge 한 줄만 남긴다.
- 마지막 `git check-ignore` 확인이 실패하면 `.gitignore` 반영이 안 된
  것이다. local 파일이 commit되면 이 규약 전체가 무의미해지므로 이 확인을
  생략하지 않는다.
- `AGENTS.md`, `CLAUDE.md`는 commit한다. `AGENTS.local.md`와
  `CLAUDE.local.md`는 필요할 때 만들고 절대 commit하지 않는다.

`AGENTS.local.md`를 만들 때는 Claude용 bridge를 함께 만든다.

```bash
[ -f CLAUDE.local.md ] || printf '@AGENTS.local.md\n' > CLAUDE.local.md
```

기존 `CLAUDE.local.md`에 이미 내용이 있으면 덮어쓰지 말고, 내용을
`AGENTS.local.md`로 옮긴 뒤 bridge 한 줄만 남긴다.

## hook 공통 — root 해석

세 CLI의 hook은 모두 같은 셸 로직을 쓴다.

```bash
d="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || exit 0
case "$d" in
  */.git) root="${d%/.git}" ;;
  *) if [ "$(git config --file "$d/config" --bool core.bare 2>/dev/null)" = "true" ]; then
       root="$d"
     else
       root="$(git rev-parse --path-format=absolute --show-toplevel 2>/dev/null)"
     fi ;;
esac
[ -n "$root" ] && [ -f "$root/AGENTS.local.md" ] && cat "$root/AGENTS.local.md"
exit 0
```

- `--git-common-dir`는 어느 worktree에서 실행해도 repo의 공용 `.git`을
  가리키므로 모든 worktree가 같은 `AGENTS.local.md`를 읽는다.
- common dir이 `/.git`으로 끝나지 않으면서 bare이면(bare repo + worktree
  구성) bare 디렉터리 자체를 root로 쓴다. bare가 아니면(submodule 등)
  현재 checkout의 root를 쓴다.
- git repo가 아니거나 파일이 없으면 아무것도 출력하지 않는다.
- `--path-format=absolute`는 git 2.31 이상이 필요하다.

## Claude Code — bridge와 hook을 함께 세팅

Claude는 로드 경로가 두 개이고 **둘 다 세팅해야 모든 실행 형태가
커버된다.** 커버 범위가 다르기 때문이다.

| Claude 실행 형태 | bridge (`CLAUDE.local.md`) | 전역 hook |
|---|---|---|
| 메인 세션 @ main checkout | 커버 | 커버 (아래 가드로 중복 방지) |
| 메인 세션 @ worktree | 안 됨 (로컬 파일이 worktree에 없음) | 커버 |
| 서브에이전트 @ main checkout | 커버 (네이티브 memory 체인) | 안 됨 (hook이 서브에이전트에 주입되지 않음) |
| 서브에이전트 @ worktree (격리 여부 무관) | 안 됨 | 안 됨 — 제약 섹션 참조 |

bridge는 위 repo 세팅 절차에서 만들었다. 전역 hook은
`~/.claude/settings.json`의 `hooks`에 추가한다. 기존 `hooks` 키가 있으면
병합한다.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "top=\"$(git -C \"$CLAUDE_PROJECT_DIR\" rev-parse --path-format=absolute --show-toplevel 2>/dev/null)\"; [ -n \"$top\" ] && [ -f \"$top/CLAUDE.local.md\" ] && exit 0; d=\"$(git -C \"$CLAUDE_PROJECT_DIR\" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)\" || exit 0; case \"$d\" in */.git) root=\"${d%/.git}\";; *) if [ \"$(git config --file \"$d/config\" --bool core.bare 2>/dev/null)\" = \"true\" ]; then root=\"$d\"; else root=\"$(git -C \"$CLAUDE_PROJECT_DIR\" rev-parse --path-format=absolute --show-toplevel 2>/dev/null)\"; fi;; esac; [ -n \"$root\" ] && [ -f \"$root/AGENTS.local.md\" ] && cat \"$root/AGENTS.local.md\"; exit 0"
          }
        ]
      }
    ]
  }
}
```

이 hook은 공통 로직 앞에 가드가 하나 붙는다: 현재 checkout에
`CLAUDE.local.md`가 있으면 네이티브 로드가 커버하므로 hook은 아무것도
출력하지 않는다. 그래서 bridge와 hook을 함께 세팅해도 같은 내용이 두 번
주입되지 않는다.

## Codex CLI — 전역 hook (머신마다 1회)

`~/.codex/config.toml`에 추가한다.

```toml
[[hooks.SessionStart]]

[[hooks.SessionStart.hooks]]
type = "command"
command = '''d="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || exit 0
case "$d" in
  */.git) root="${d%/.git}" ;;
  *) if [ "$(git config --file "$d/config" --bool core.bare 2>/dev/null)" = "true" ]; then
       root="$d"
     else
       root="$(git rev-parse --path-format=absolute --show-toplevel 2>/dev/null)"
     fi ;;
esac
[ -n "$root" ] && [ -f "$root/AGENTS.local.md" ] && cat "$root/AGENTS.local.md"
exit 0'''
additionalContextLimit = 0
```

- `additionalContextLimit = 0`은 무제한이다. 이 줄이 없으면 hook 출력이
  약 8천 token에서 잘리고 나머지는 임시 파일로 빠진다.
- 추가 후 Codex 대화창에서 `/hooks`를 열어 이 hook을 한 번 trust한다.
  **trust 전에는 hook이 경고 없이 skip된다.** command 문자열이 바뀌면
  trust가 깨져 다시 trust해야 한다.
- trust 적용 여부는 repo 안에서 `codex exec "hi"`를 실행해 출력에
  `hook: SessionStart` 줄이 보이는지로 확인한다. 이 줄이 없으면 hook은
  돌지 않은 것이다.

## Kiro CLI — 전역 custom agent (머신마다 1회)

Kiro CLI에는 컨텍스트 파일 경로를 직접 추가하는 settings 키가 없고,
built-in 기본 agent `kiro_default`는 같은 이름으로 override할 수 없다.
대신 전역 custom agent를 만들고 기본 agent로 지정한다. 이것도 머신당
1회 세팅이다.

`~/.kiro/agents/default-overlay.json`:

```json
{
  "name": "default-overlay",
  "description": "Default toolset plus AGENTS.local.md overlay",
  "tools": ["*"],
  "resources": [],
  "hooks": {
    "agentSpawn": [
      { "command": "d=\"$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)\" || exit 0; case \"$d\" in */.git) root=\"${d%/.git}\";; *) if [ \"$(git config --file \"$d/config\" --bool core.bare 2>/dev/null)\" = \"true\" ]; then root=\"$d\"; else root=\"$(git rev-parse --path-format=absolute --show-toplevel 2>/dev/null)\"; fi;; esac; [ -n \"$root\" ] && [ -f \"$root/AGENTS.local.md\" ] && cat \"$root/AGENTS.local.md\"; exit 0" }
    ]
  },
  "includeMcpJson": true
}
```

```bash
kiro-cli agent validate --path ~/.kiro/agents/default-overlay.json
kiro-cli agent set-default default-overlay
```

- `agentSpawn` hook의 stdout이 세션 컨텍스트에 추가된다.
- `"tools": ["*"]`는 전체 tool을 허용하는 wildcard다. tool을 제한하고
  싶으면 필요한 이름만 나열한다.
- `AGENTS.md` 같은 기본 리소스는 custom agent에도 자동 상속된다
  (`chat.disableInheritingDefaultResources` 설정으로 끌 수 있다).
- 되돌리려면 `kiro-cli agent set-default kiro_default`로 built-in 기본
  agent를 다시 지정한다.
- custom agent가 built-in 기본 agent의 모든 세부 동작과 동일하다는
  보장은 없다. Kiro CLI 업데이트 후에는 검증 절차로 다시 확인한다.

## 검증

임시 repo에 canary를 넣고 각 agent가 실제로 읽는지 확인한다. canary
문구는 secret처럼 보이면 모델이 답변을 거부할 수 있으므로 무해한 값을
쓴다.

```bash
mkdir -p /tmp/overlay-check && cd /tmp/overlay-check && git init -q
printf '# AGENTS.md\nProject codename is bluebird.\n' > AGENTS.md
printf '@AGENTS.md\n' > CLAUDE.md
printf 'AGENTS.local.md\nCLAUDE.local.md\n' > .gitignore
git add -A && git commit -qm init
printf '# AGENTS.local.md\nPersonal marker is emerald-42.\n' > AGENTS.local.md
printf '@AGENTS.local.md\n' > CLAUDE.local.md
```

각 agent에 같은 질문을 던진다:

```bash
Q="From the project rule files loaded in your context: what is the project codename and what is the personal marker? One per line, or MISSING. Do not use tools."
claude -p "$Q"
codex exec "$Q"
kiro-cli chat --no-interactive "$Q"
```

- 세 agent 모두 `bluebird`와 `emerald-42`를 답해야 한다.
- `emerald-42`가 빠지면 로컬 오버레이 미적용이다. Claude는 bridge 파일과
  hook 설정을, Codex는 trust 여부(`/hooks`)를, Kiro는
  `kiro-cli agent list`에서 기본 agent 지정을 확인한다.

worktree 동작도 확인한다:

```bash
git worktree add -q ../overlay-check-wt
cd ../overlay-check-wt
claude -p "$Q"
codex exec "$Q"
kiro-cli chat --no-interactive "$Q"
```

- worktree에는 로컬 파일이 없지만 세 agent 모두 여전히 `emerald-42`를
  답해야 한다. hook이 공용 git 디렉터리 기준으로 원본 파일을 찾아 읽는
  것이다.
- Claude 서브에이전트 커버는 메인 세션에서 Agent tool로 같은 질문을
  위임해 확인한다. main checkout에서는 `emerald-42`가 나와야 한다.
- 확인 후 임시 repo와 worktree는 삭제한다.

## 제약

- `AGENTS.local.md` 전문이 매 세션 컨텍스트에 들어간다. 파일이 커지면
  그대로 token 비용이 된다.
- **Claude Code와 Kiro CLI의 hook 주입은 큰 파일에서 경고 없이 잘린다.**
  실측: Claude는 7.4KB, Kiro는 8.9KB까지 전문 통과, 양쪽 모두 15KB에서
  꼬리 소실. 정확한 임계는 그 사이이며 미상이다. Codex CLI는
  `additionalContextLimit = 0`이면 전문이 들어간다. 잘림은 뒤쪽 규칙만
  조용히 사라지는 형태라 알아채기 어려우므로 `AGENTS.local.md`는 7KB
  이하로 유지한다.
- `CLAUDE.local.md`에 bridge 한 줄이 아닌 자체 규칙이 남아 있으면 Claude
  hook은 가드 때문에 skip하고 네이티브 로드는 그 파일만 읽으므로,
  `AGENTS.local.md`가 Claude에서만 로드되지 않는 에이전트 간 불일치가
  생긴다. repo 세팅의 migration(내용을 `AGENTS.local.md`로 이동)을
  끝내야 한다.
- bridge와 hook 모두 로드는 세션 시작 시점 1회다. 세션 도중 파일을
  고쳐도 다음 세션부터 반영된다.
- **worktree에서 실행되는 Claude 서브에이전트에는 로컬 오버레이가
  전달되지 않는다** (worktree 격리 실행뿐 아니라 worktree에서 연 세션이
  띄우는 일반 서브에이전트도 동일). worktree checkout에 로컬 파일이 없고,
  Claude Code는 서브에이전트에 SessionStart hook을 실행하지 않으며
  SubagentStart hook의 stdout도 컨텍스트에 주입하지 않는다.
  서브에이전트가 지켜야 할 로컬 규칙이 있으면 메인 에이전트가 위임
  prompt에 그 내용을 직접 포함해야 한다.
- 로컬 오버레이는 repo당 하나다. worktree별로 다른 로컬 규칙을 두는
  구성은 지원하지 않는다.
- symlink 기반 우회(예: Kiro steering 디렉터리에 symlink)는 동작하지
  않는다. 이 규약의 bridge와 hook 방식만 사용한다.

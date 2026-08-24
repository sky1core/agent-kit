---
name: agents-local-overlay
description: AGENTS.md(공용, commit)와 AGENTS.local.md(개인, gitignore)를 Claude Code, Codex CLI, Kiro CLI에서 동일하게 로드되게 만드는 agent 중립 rule 파일 규약을 세팅하고 검증할 때 사용한다. git worktree, bare repo + worktree, 서브에이전트 사용 시의 로드 범위까지 다룬다. "AGENTS.local.md 세팅", "로컬 전용 지침 주입", "에이전트 공통 rule 파일", "CLAUDE.local.md를 Codex에서도", "worktree에서 로컬 규칙" 같은 요청에서 트리거한다.
---

# agents-local-overlay

repo에는 agent 중립 이름의 rule 파일 두 개만 둔다.

- `AGENTS.md` — 공용 규칙. commit하는 repo와 gitignore하는 repo 모두 지원한다.
- `AGENTS.local.md` — 개인 로컬 규칙. 항상 gitignore. commit 금지.

규칙은 **빠져도 안 되고 겹쳐도 안 된다.** 빠지면 에이전트가 규칙을 모르는
채 움직이고, 겹치면 같은 내용이 매 세션 두 번씩 컨텍스트를 차지해 token
비용과 응답 품질을 함께 떨어뜨린다.

그래서 동작 규칙은 하나다: **각 CLI가 네이티브로 읽지 못하는 규칙만 보충
경로가 전달한다.** 네이티브 로드가 그 규칙을 실제로 전달하는 자리에서는
hook이나 launcher가 같은 내용을 더하지 않는다. repo가 `AGENTS.md`를 commit하든
gitignore하든, worktree에서 실행하든 현재 checkout에 맞춰 갈린다.

`AGENTS.local.md`와 gitignored `AGENTS.md`의 source는 repo당 하나이고 모든
worktree가 이를 공유한다. 위치는 `git worktree list --porcelain -z`의 첫
`worktree` 경로다. tracked `AGENTS.md`는 각 worktree의 현재 HEAD와 checkout을
source로 쓴다.
일반 repo는 primary checkout root이고, bare·`--separate-git-dir` repo와
submodule은 Git이 primary worktree로 보고하는 metadata 경로일 수 있다. 다음
명령으로 source 위치를 확인한다.

```bash
git worktree list --porcelain -z |
  LC_ALL=C awk 'BEGIN { RS = "\0" } NR == 1 { sub(/^worktree /, ""); print; exit }'
```

공유 script도 같은 Git record를 읽으므로 어느 worktree에서 실행해도 source가
하나로 고정된다. 경로 이름이나 `core.worktree` 값을 추측해 보정하지 않는다.

| | `AGENTS.md` (공용) | `AGENTS.local.md` (로컬) |
|---|---|---|
| Claude Code | `CLAUDE.md` bridge | `CLAUDE.local.md` bridge |
| Codex CLI | 네이티브 | — |
| Kiro CLI v3 | 네이티브 | 생성된 `.kiro/steering/agents-local-overlay.md` |

빈 칸과 worktree처럼 네이티브 경로가 닿지 않는 자리는 Claude·Codex hook이
메운다. Kiro v3는 subagent에 전역 hook을 전달하지 않으므로 launcher가 현재
규칙을 steering 문서로 먼저 materialize한다.

검증 기준 버전: Claude Code 2.1.232–2.1.235, Codex CLI 0.147.0,
Kiro CLI package 2.15.1의 v3 engine. hook 스키마는 신기능이라 major 업데이트
후에는 아래 검증 절차를 다시 돌린다.

Claude custom subagent의 이름으로 `Explore`, `Plan`, `fork`를 쓰지 않는 것이
전제다. `SubagentStart` 입력은 agent 이름만 주고 built-in/custom 출처를 주지
않으므로 같은 이름의 override와 built-in을 hook에서 구분할 수 없다. user,
project, managed, `--agents`, `--add-dir`와 runtime `/add-dir` 아래의 agent 정의에
이 이름이 있으면 세팅을 진행하지 말고 먼저 이름을 바꾼다. plugin agent의 scoped
이름처럼 실제 `agent_type`이 `<plugin>:<name>`인 경우는 충돌하지 않는다.

Claude의 `claudeMdExcludes`가 repo의 `CLAUDE.md` 또는 `CLAUDE.local.md`를
제외하면 bridge가 있어도 네이티브 전달은 일어나지 않는다. user, project, local,
managed settings 어느 scope에서도 이 두 bridge를 제외하지 않는 것이 전제다.
CLI `--setting-sources`나 SDK `settingSources`를 지정할 때도 `project`와 `local`을
모두 포함해야 한다.
`--bare`와 `--safe-mode`는 hook 자체를 끄므로 이 규약을 지원하지 않는다.
effective settings에서 `disableAllHooks=true`이거나 managed settings의
`allowManagedHooksOnly=true`로 user hook이 막힌 구성도 지원하지 않는다.
두 rule 파일은 NUL이 없는 UTF-8 Markdown regular file이어야 하며 실행 사용자가
읽을 수 있어야 한다.

## repo 세팅 (repo마다 1회)

tracked `AGENTS.md`는 checkout root에 둔다. gitignored `AGENTS.md`와
`AGENTS.local.md`는 위에서 확인한 source 경로에 둔다. 다음 명령은 bridge와
ignore 경로만 만든다.

```bash
[ -f CLAUDE.md ] || printf '@AGENTS.md\n' > CLAUDE.md
grep -qxF 'AGENTS.local.md' .gitignore 2>/dev/null || printf '\nAGENTS.local.md\n' >> .gitignore
grep -qxF 'CLAUDE.local.md' .gitignore 2>/dev/null || printf '\nCLAUDE.local.md\n' >> .gitignore
grep -qxF '.kiro/steering/agents-local-overlay.md' .gitignore 2>/dev/null || printf '\n.kiro/steering/agents-local-overlay.md\n' >> .gitignore
git check-ignore -q AGENTS.local.md && git check-ignore -q CLAUDE.local.md && \
  git check-ignore -q .kiro/steering/agents-local-overlay.md && echo ignored
kiro_track_status=0
git ls-files --error-unmatch .kiro/steering/agents-local-overlay.md >/dev/null 2>&1 || kiro_track_status=$?
case "$kiro_track_status" in
  0) printf '%s\n' 'kiro overlay path is tracked' >&2; false ;;
  1) echo kiro-overlay-untracked ;;
  *) printf '%s\n' 'could not inspect Kiro overlay tracking state' >&2; false ;;
esac
test ! -e AGENTS.override.md && test ! -L AGENTS.override.md && echo no-codex-override
```

- `CLAUDE.md`는 `@AGENTS.md` 한 줄짜리 bridge다. Claude Code는 `AGENTS.md`를
  직접 로드하지 않는다. Codex CLI와 Kiro CLI는 네이티브로 로드한다.
- 기존 `CLAUDE.md`에 이미 내용이 있으면 덮어쓰지 말고, 공용 규칙을
  `AGENTS.md`로 옮긴 뒤 bridge 한 줄만 남긴다.
- bridge를 두지 않아도 hook이 대신 주입하므로 규칙은 로드된다. 네이티브
  로드를 쓰려면 bridge를 두고, hook 하나로만 통일하려면 두지 않는다. 어느
  쪽이든 중복은 생기지 않는다. 다만 hook은 공용과 로컬을 한 번에 이어 붙여
  주입하므로 아래 「제약」의 크기 한계는 **그 합계**에 걸린다. Claude에서
  bridge를 빼면 `AGENTS.md`까지 그 합계에 들어가므로, 합계가 한계에 가까우면
  bridge를 둬서 공용 규칙을 네이티브 로드로 넘긴다. Codex와 Kiro는 `AGENTS.md`가
  네이티브 파일이라 bridge 유무와 무관하다.
- `git check-ignore` 확인이 실패하면 `.gitignore` 반영이 안 된 것이다. local
  파일이 commit되면 이 규약이 무의미해지므로 이 확인을 생략하지 않는다.
- `AGENTS.md`와 `CLAUDE.md`를 commit할지는 repo가 정한다. `AGENTS.md`를
  gitignore하면 위에서 확인한 source 경로에 하나만 두고, 각 worktree에 사본을
  만들지 않는다. hook과 Kiro launcher가 거기서 보충한다. `AGENTS.local.md`도
  같은 source 경로에 하나만 두며, `CLAUDE.local.md`와 함께 어떤 경우에도
  commit하지 않는다.
- repo root의 `AGENTS.override.md`는 두지 않는다. Codex가 `AGENTS.md` 대신 그
  파일을 선택하기 때문이다. runtime script도 override가 `AGENTS.md`와 byte 단위로
  같지 않거나 둘 중 하나가 regular file이 아니면 공용 규칙을 주입해 누락을
  막지만, override에 같은 규칙을 복사해 넣은 구성의 중복까지 판정할 수는 없다.
- Codex의 `project_doc_max_bytes`는 아래처럼 최소 32768로 고정하고 `AGENTS.md`는
  그 이하여야 한다. 네이티브 로드가 잘린 뒤에는 hook이 이미 들어간 prefix와
  잘린 나머지를 분리해 exact-once로 복구할 수 없다. 더 큰 파일이 필요하면 이
  설정을 파일 크기 이상으로 올린 뒤 같은 검증을 한다. 실제 source 경로에서
  `test "$(wc -c < AGENTS.md)" -le 32768 && echo agents-size-ok`로 확인한다.

`AGENTS.local.md`를 만들 때는 Claude용 bridge를 함께 만든다.

```bash
[ -f CLAUDE.local.md ] || printf '@AGENTS.local.md\n' > CLAUDE.local.md
```

기존 `CLAUDE.local.md`에 이미 내용이 있으면 덮어쓰지 말고, 내용을
`AGENTS.local.md`로 옮긴 뒤 bridge 한 줄만 남긴다.

## 공유 스크립트 (머신마다 1회)

Claude·Codex hook과 Kiro launcher가 같은 판정 스크립트를 쓴다. 스킬의 두
script를 `$HOME/.local/bin`에 복사한다. `<skill-dir>`은 이 스킬이 설치된
디렉터리다.

```bash
mkdir -p "$HOME/.local/bin"
cp "<skill-dir>/scripts/agents-overlay-context" "$HOME/.local/bin/agents-overlay-context"
cp "<skill-dir>/scripts/kiro-cli-overlay" "$HOME/.local/bin/kiro-cli-overlay"
chmod 755 "$HOME/.local/bin/kiro-cli-overlay"
sh -n "$HOME/.local/bin/agents-overlay-context" && \
  sh -n "$HOME/.local/bin/kiro-cli-overlay" && echo ok
```

`awk`와 git 2.36 이상이 필요하고, JSON 형식과 runtime별 중복 판정을 쓰는
Claude와 Codex 경로에는 `python3`도 필요하다. raw 형식만 쓰는 Kiro는
`python3` 없이 동작한다. 전제 도구(`git`, `python3`)가 없거나 인자·형식이
잘못되면 script는 조용히 생략하지 않고 stderr 오류와 함께 실패해 각 CLI의
hook 오류로 드러난다.

스킬을 갱신했으면 위 `cp`를 다시 실행한다. 사본이 낡아도 문법은 유효해서
`sh -n`으로는 드러나지 않으므로, 최신인지는 따로 본다.

```bash
cmp -s "<skill-dir>/scripts/agents-overlay-context" \
       "$HOME/.local/bin/agents-overlay-context" && echo in-sync
cmp -s "<skill-dir>/scripts/kiro-cli-overlay" \
       "$HOME/.local/bin/kiro-cli-overlay" && echo kiro-in-sync
```

hook이 `sh <path>` 형태로 호출하므로 판정 script의 실행 권한은 없어도 된다.
Kiro launcher는 command로 직접 실행하므로 실행 권한이 필요하다.

native 인자는 두 형태를 받는다. prefix 없는 이름(`CLAUDE.md`, `AGENTS.md`)은
CLI가 repo top의 그 파일에 네이티브로 닿는 경우다. Claude bridge는 import 표기도
판정하고, Codex는 규칙 파일 자체와 같은 경로인지 판정한다.
`cwd:` prefix(`cwd:AGENTS.md`)는 CLI가 실행 디렉터리의 그 파일만 그대로 읽고
import를 해석하지 않는 경우다(Kiro) — 이때는 그 파일이 규칙 파일 자체이거나
그 파일로 가는 link일 때만 커버로 인정한다. 단 Kiro launcher는 이 판정 전에
규칙 경로의 symlink를 먼저 거부하고 중단하므로, launcher 경로에서 link 커버는
하드링크만 해당한다. `-`는 네이티브 경로가 아예 없다는
뜻이고, 그 자리의 규칙은 항상 주입된다.

스크립트가 판정하는 순서는 이렇다.

| 현재 checkout 상태 | 공용 규칙 처리 |
|---|---|
| 해당 CLI가 읽는 네이티브 파일이 있고 실제 규칙 파일도 있음 | 주입 안 함 |
| `AGENTS.md`는 있지만 그 CLI가 못 읽음 | 그 파일을 주입 |
| `AGENTS.md`가 현재 브랜치에 tracked인데 디스크에 없음 (sparse checkout) | `HEAD` 버전을 주입 |
| 브랜치에 없고 root의 `AGENTS.md`가 untracked | repo가 gitignore 정책이므로 root 파일을 주입 |
| 브랜치에 없고 root에서는 tracked | 브랜치가 의도적으로 지운 것으로 보고 주입 안 함 |

첫 줄의 "네이티브가 커버한다"는 판정은 파일이 있다는 것만으로는 성립하지
않는다. bridge가 실제로 규칙 파일에 닿아야 커버로 인정한다.

- 규칙 파일 자체이거나 그 파일로 가는 link(symlink·하드링크)면 커버다.
- regular file이면 `@AGENTS.md` 또는 `@AGENTS.local.md`가 코드 밖에서 그 줄
  단독으로 있어야 한다.
- 규칙 파일이 없거나, bridge가 디렉터리·깨진 symlink·엉뚱한 대상이면 커버로
  보지 않고 hook이 주입한다.

이 판정은 한쪽으로 치우치게 만들어져 있다. 판단이 서지 않는 자리는 커버로
보지 않고 주입한다. 그래서 실패는 주로 중복 방향으로 나며, bridge를 단독 줄로
두지 않은 구성에서 같은 내용이 두 번 들어갈 수 있다.

**내용을 읽어 내보낼 때**는 symlink를 거부한다. repo가 규칙 파일을 개인
파일로 향하게 만들어 컨텍스트로 빼돌리는 것을 막기 위해서다. commit된
symlink도 같은 이유로 제외한다. 디스크의 `AGENTS.md`가 symlink라도 HEAD에
regular file로 tracked면 그 HEAD 버전을 주입한다. HEAD까지 symlink이거나
untracked면 규칙 내용 대신 `[agents-local-overlay] Rule file not loaded: ...`
한 줄 안내를 주입해, 규칙이 무증상으로 빠지는 일이 없게 한다. 안내가 보이면
그 파일을 읽을 수 있는 regular file로 되돌리고 새 세션을 시작한다. 안내는
파일 내용을 읽지 않으므로 컨텍스트로 새는 것이 없다. 신뢰할 수 없는 repo에서는 규칙
파일이 symlink인지 직접 확인한다.

`python3`가 없으면 JSON 경로는 조용히 생략하지 않고 오류로 실패한다.

git repo가 아니거나 주입할 내용이 없으면 아무것도 출력하지 않는다.

## Claude Code (머신마다 1회)

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
            "command": "sh \"$HOME/.local/bin/agents-overlay-context\" json SessionStart CLAUDE.md CLAUDE.local.md \"$CLAUDE_PROJECT_DIR\""
          }
        ]
      }
    ],
    "SubagentStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "sh \"$HOME/.local/bin/agents-overlay-context\" json SubagentStart CLAUDE.md CLAUDE.local.md \"$CLAUDE_PROJECT_DIR\" claude-subagent"
          }
        ]
      }
    ]
  }
}
```

- `SessionStart`에 source matcher를 두지 않는다. resume에서도
  `source=resume`으로 발동하지만(실측), Claude Code가 재생된 transcript에
  이미 있는 것과 동일한 내용의 재주입을 dedupe하므로 중복이 생기지 않고,
  규칙 파일이 그 사이 바뀌었으면 새 내용이 주입된다. matcher로 resume을 빼면
  hook 설치 전에 시작한 세션을 resume할 때 규칙이 조용히 빠진다.
- `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`이면 bridge의 네이티브 로드도 꺼진다.
  script는 이 env를 확인해 `SessionStart`와 일반/custom `SubagentStart`에서
  공용·로컬 규칙을 hook으로 강제 주입한다.
- 일반·custom 서브에이전트는 메인 세션과 같은 project memory를 네이티브로
  로드한다. 이 경로는 `$CLAUDE_PROJECT_DIR`의 bridge를 판정해, 메인이 읽지 못한
  규칙만 보탠다. worktree 격리 여부와 무관하게 메인 project memory 기준이다.
- built-in `Explore`와 `Plan` 서브에이전트는 project memory를 생략한다. script는
  hook 입력의 `agent_type`을 보고 이 두 타입에만 현재 서브에이전트 checkout의
  공용·로컬 규칙을 모두 주입한다. 위 reserved-name 전제가 필요한 이유가 이
  분기다.
- fork는 hook 입력의 `agent_type=fork`로 식별한다. 메인 context 전체를
  상속하므로 이 타입만 `SubagentStart`에서 아무것도 보태지 않는다.
  `general-purpose`와 named agent는 fork mode가 켜져 있어도 각자의 타입대로
  위의 일반 분기를 따른다.
- `SubagentStart`는 `hookSpecificOutput.additionalContext` JSON만 컨텍스트에
  넣는다. `SessionStart`도 같은 형식을 쓰는데, raw stdout을 쓰면
  `AGENTS.md`가 `{`로 시작할 때 Claude Code가 그 내용을 hook 출력 JSON으로
  오인해 주입이 조용히 실패하기 때문이다.
- **bridge 줄은 코드 밖에서 그 줄 단독으로 둔다.** 앞 공백 3칸 이하, 뒤 공백·탭,
  CRLF 줄바꿈, UTF-8 BOM, `@./AGENTS.md` 표기는 모두 인정한다. 다른 내용과 같은
  줄에 쓰거나(`- @AGENTS.md`, `See @AGENTS.md for rules.`), 코드펜스 안이나 4칸
  이상 들여쓴 자리에 두면 판정은 bridge로 보지 않는다.
- 이 판정은 Claude의 markdown 파싱을 흉내 내지 않는다. 흉내 내려면 어디까지가
  코드 블록인지 추측해야 하고, 그 추측이 넓게 빗나가면 hook이 침묵해 규칙이
  아무 증상 없이 사라진다. 그래서 애매한 자리는 주입 쪽으로 보낸다. 코드펜스
  외에 YAML frontmatter 안, `<`로 시작하는 줄(HTML block) 뒤, link reference
  definition 뒤도 bridge로 보지 않는다. 그만큼 Claude가 import하는 표기를
  bridge로 못 알아봐 같은 규칙이 두 번 들어갈 수 있다.
- 판정이 뒤따르는 줄을 삼키는 markdown 블록을 전부 아는 것은 아니다. 특이한
  구조를 쓰면 여전히 어긋날 수 있으므로 bridge 줄은 단독으로 두는 것이 규약이다.
- bridge 대신 `AGENTS.md`의 **내용을 복사해** 넣으면 스크립트는 bridge로
  보지 않아 주입하고 Claude는 그 파일을 네이티브로 읽으므로 같은 내용이 두
  번 들어간다. 복사하지 말고 bridge 한 줄만 둔다.

## Codex CLI (머신마다 1회)

`~/.codex/config.toml`에 추가한다.

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

- `additionalContextLimit = 0`은 무제한이다. 이 줄이 없으면 hook 출력이 약
  2,500 token에서 잘리고 나머지는 임시 파일로 빠진다. 전체 규칙을 바로
  전달하려고 `0`을 쓰는 만큼, rule 파일 크기를 제한해 context를 잠식하지 않게
  한다.
- `SessionStart`는 startup·`clear`에서 항상 현재 규칙을 주입한다. `resume`과
  `compact`에서는 마지막 compaction 이후 활성 history를 검사한다. 현재 규칙과
  같은 developer context가 있으면 출력하지 않고, 없거나 내용이 달라졌으면
  주입한다.
- Codex CLI 0.147.0의 `source=compact`는 successful compaction 직후, 다음 model
  request 전에 실행된다. 같은 user turn을 계속 sampling하는 mid-turn compaction도
  이 순서다. compaction이 hook context를 버렸으면 즉시 다시 넣고, 보존했으면
  exact body 판정으로 침묵한다.
- resume 직전 pre-turn compaction처럼 같은 활성 window를 가리키는 source가 연속
  실행될 때는 transcript 기록 시차만 믿지 않는다. script가 메인은 `session_id`,
  서브에이전트는 `agent_id`를 규칙 body hash와 compaction `window_id`에 묶어 UID
  전용 임시 상태를 잠그고, 각 agent의 첫 주입만 통과시킨다.
  JSON 출력을 pipe에 flush한 뒤에만 그 window를 주입 완료로 기록한다.
  startup·resume·`clear`는 이전 process의 상태를 초기화하고, 새 `window_id`는 새
  주입으로 취급한다. 상태 파일에는 규칙 전문을 저장하지 않으며 OS 임시 디렉터리
  정리를 따른다.
- `SubagentStart`는 해당 agent의 transcript를 직접 검사한다. full-history가 규칙을
  상속했으면 침묵하고, `none`이나 일부 history가 상속하지 않았으면 주입한다.
- Claude와 같은 이유로 JSON 형식을 쓴다. raw stdout은 `{`로 시작하는 파일에서
  실패하며, 이때 Codex는 `hook: SessionStart Failed`로 표시한다.
- 추가 후 Codex 대화창에서 `/hooks`를 열어 이 hook을 한 번 trust한다.
  **trust 전에는 hook이 실행되지 않는다.** 비대화형 `codex exec`에서는 경고나
  안내가 전혀 없이 skip되므로(실측) 세팅 후 반드시 아래 확인 절차를 거친다.
  공식 문서는 대화형 CLI startup에서 review가 필요하면 `/hooks`를 열라는
  warning이 뜬다고 설명한다. 비대화형 경로에 같은 warning이 나온다는 보장은
  없다. 두 hook을 모두 trust해야 한다. 스크립트 경로나 인자가 바뀌면 trust가
  깨져 다시 trust해야 한다.
  반대로 **스크립트 파일 내용만 바꾸면 trust는 그대로 유지된다.** trust가
  덮는 범위는 `config.toml`의 command 문자열까지다.
- trust 적용 여부는 repo 안에서 `codex exec "hi"`를 실행해 출력에
  `hook: SessionStart Completed` 줄이 보이는지로 확인한다. 줄이 아예 없으면
  trust가 안 된 것이고, `Failed`면 hook 출력 형식이 잘못된 것이다.
- `project_doc_max_bytes`가 이미 있으면 중복 key를 만들지 말고 기존 값을 32768
  이상으로 조정한다. 모든 repo의 `AGENTS.md`가 이 값 이하여야 한다.
- `project_doc_fallback_filenames`는 빈 배열이어야 한다. 특히
  `AGENTS.local.md`를 fallback에 넣으면 `AGENTS.md`가 없는 worktree에서 Codex의
  네이티브 로드와 local hook 주입이 겹친다.
- Codex의 `AGENTS.override.md`는 로컬 오버레이로 쓸 수 없다. 같은 디렉터리에서
  `AGENTS.md`를 **대체**하므로 repo 세팅 단계에서 거부한다.

## Kiro CLI v3 (머신마다 1회)

Kiro package 2.13.0 이상에서 `kiro-cli-overlay`로 실행한다. launcher가 항상 v3
engine을 선택하고 repo top에서 시작한다. v2 engine과 raw `kiro-cli` 실행은
동기화를 우회하므로 이 규약의 지원 대상이 아니다.

먼저 custom agent도 `AGENTS.md` 기본 리소스를 상속하도록 global 설정을 고정한다.
workspace override가 `true`이면 그 repo는 지원 조건을 벗어나므로 effective 값을
repo마다 확인한다.

```bash
kiro-cli settings chat.disableInheritingDefaultResources false
kiro-cli settings list --format json
```

```bash
command -v kiro-cli-overlay
kiro-cli diagnostic
```

`kiro-cli-overlay`는 Git worktree 안에서만 실행되고 실행할 때마다 steering
문서를 다시 만들므로, 설치 확인은 실행이 아니라 위처럼 `command -v`로 한다.

- Kiro의 전역 `SessionStart` hook은 main에는 실행되지만 custom subagent에는
  실행되지 않는다. launcher는 Kiro process가 시작되기 전에 현재 checkout에서
  네이티브로 빠지는 규칙만 `.kiro/steering/agents-local-overlay.md`에 원자적으로
  쓴다. Kiro가 main과 모든 subagent에 공유하는 steering 경로이므로 별도의
  per-agent hook이나 custom agent 수정이 필요 없다.
- 생성 파일은 launcher 전용 첫 줄이 있는 regular file이어야 한다. 같은 경로에
  다른 문서나 symlink가 있거나 파일이 tracked·unignored 상태면 launcher가
  덮어쓰지 않고 종료한다. 생성 표식은 원본 rule의 YAML frontmatter가 Kiro의
  steering metadata로 오인되지 않게 하는 경계 역할도 한다.
- source rule이 존재하지만 regular·readable 조건을 만족하지 않거나 읽기에
  실패하면 launcher는 기존 생성 파일을 교체하지 않고 Kiro도 시작하지 않는다.
- 현재 checkout top에 `AGENTS.md`가 있으면 네이티브이므로 생성 문서에는 local만
  들어간다. 공용 파일이 gitignore되어 현재 checkout에 없으면 공용과 local을 함께
  넣는다. 매 실행 전에 다시 만들기 때문에 이전 process가 비정상 종료했어도 다음
  새 세션은 stale copy를 읽지 않는다. source 변경 중인 기존 세션은 아래 제약대로
  재사용하지 않는다.
- `chat.disableInheritingDefaultResources=true`인 custom agent는 `AGENTS.md`를
  받지 않는다. launcher가 이미 네이티브인 공용 규칙까지 복사하면 기본 agent에서
  중복되므로 이 예외만 자동 복구할 수 없다. global과 workspace effective 값 모두
  `false`여야 한다.
- Kiro v3는 TUI에서 실행한다. `kiro-cli chat --no-interactive`는 v2 classic
  engine이므로 이 설정을 실행하거나 검증하는 명령으로 쓰지 않는다.
- `kiro-cli-overlay`는 Git worktree 안에서만 실행한다. repo 밖에서는 overlay할
  source 위치를 결정할 수 없으므로 원래 `kiro-cli --v3`를 직접 실행한다.
- 다른 Kiro resource·steering·hook에서 `AGENTS.local.md` 내용이나 생성 steering을
  별도로 넣지 않는다. 같은 rule의 별도 전달 경로는 launcher가 관찰할 수 없어
  중복을 판정할 수 없다.

## 검증

임시 repo에 canary를 넣고 각 agent가 실제로 읽는지 확인한다. canary 문구는
secret처럼 보이면 모델이 답변을 거부할 수 있으므로 무해한 값을 쓴다.

```bash
export OVERLAY_TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/overlay-check.XXXXXX")"
printf '%s\n' "$OVERLAY_TMP_ROOT"
export OVERLAY_REPO="$OVERLAY_TMP_ROOT/repo"
export OVERLAY_WT="$OVERLAY_TMP_ROOT/worktree"
mkdir "$OVERLAY_REPO" && cd "$OVERLAY_REPO" && git init -q
printf '# AGENTS.md\nProject codename is bluebird.\n' > AGENTS.md
printf '@AGENTS.md\n' > CLAUDE.md
printf 'AGENTS.local.md\nCLAUDE.local.md\n.kiro/steering/agents-local-overlay.md\n' > .gitignore
git add AGENTS.md CLAUDE.md .gitignore && git commit -qm init
printf '# AGENTS.local.md\nPersonal marker is emerald-42.\n' > AGENTS.local.md
printf '@AGENTS.local.md\n' > CLAUDE.local.md
git worktree add -q "$OVERLAY_WT"
```

main checkout과 worktree 양쪽에서 같은 질문을 던진다.

```bash
Q="What is the project codename and what is the personal marker? Each on its own line, MISSING if unknown. Do not use tools."
claude -p "$Q"
codex exec "$Q"
kiro-cli-overlay
# TUI prompt에 $Q를 입력한다.
```

- 여섯 번 모두 `bluebird`와 `emerald-42`가 나와야 한다. worktree에는 로컬
  파일이 없지만 공유 script가 root에서 찾아 읽는다.
- Kiro launcher는 어느 하위 디렉터리에서 호출해도 repo top에서 TUI를 시작한다.
  하위 디렉터리에서 한 번 호출해 이 경계를 확인한다.

  ```bash
  mkdir -p "$OVERLAY_REPO/subdir" && cd "$OVERLAY_REPO/subdir"
  kiro-cli-overlay
  # TUI prompt에 $Q를 입력한다.
  ```
- 네이티브 경로와 hook의 경계는 스크립트를 직접 실행해 바이트 수로 본다.

  ```bash
  cd "$OVERLAY_REPO"
  sh "$HOME/.local/bin/agents-overlay-context" json SessionStart CLAUDE.md CLAUDE.local.md . | wc -c
  cd "$OVERLAY_WT"
  sh "$HOME/.local/bin/agents-overlay-context" json SessionStart CLAUDE.md CLAUDE.local.md . | wc -c
  ```

  위 Claude 인자 형태로 bridge 두 개가 모두 있는 main checkout에서 돌리면
  `0`이어야 한다. 그 조건에서 0이 아니면 네이티브 로드와 겹쳐 같은 내용이
  두 번 들어간다. bridge가 없거나 Codex·Kiro 인자로 호출한 경우는 0이 아닌
  값이 정상이다. worktree는 실제 bridge와 규칙 파일이 어느 위치에 있는지에
  따라 0일 수도, 아닐 수도 있다.
- 반대 방향도 본다. bridge 줄을 코드로 만들면 Claude가 import하지 않으므로
  hook이 대신 주입해야 한다.

  ```bash
  cd "$OVERLAY_REPO"
  OV="$HOME/.local/bin/agents-overlay-context"
  for form in '    @AGENTS.md' '~~~\n@AGENTS.md\n~~~' \
              '````md\n```\n@AGENTS.md\n```\n````' '<!--\n@AGENTS.md\n-->' \
              '[a]:\n@AGENTS.md' '```x``` y\n```\n@AGENTS.md' \
              '---\ntitle: x\n@AGENTS.md\n---' \
              '- x\n\n    <div>\n  @AGENTS.md'; do
    printf -- "$form\n" > CLAUDE.md
    sh "$OV" json SessionStart CLAUDE.md CLAUDE.local.md . | wc -c
  done
  printf '@AGENTS.md\n' > CLAUDE.md
  ```

  여덟 형태 모두 0이 아니어야 한다. 0이면 판정이 bridge로 잘못 새서 공용
  규칙이 통째로 빠진다.
- 거부 경로도 본다. 규칙 파일을 symlink로 바꾸면 내용 대신 로드 실패 안내가
  나와야 한다.

  ```bash
  cd "$OVERLAY_REPO"
  mv AGENTS.local.md AGENTS.local.md.real
  ln -s AGENTS.local.md.real AGENTS.local.md
  sh "$HOME/.local/bin/agents-overlay-context" raw SessionStart CLAUDE.md - .
  unlink AGENTS.local.md && mv AGENTS.local.md.real AGENTS.local.md
  ```

  출력에 `Rule file not loaded`가 있고 `emerald-42`가 없어야 한다. 내용이
  보이면 symlink 거부가 깨진 것이고, 아무것도 안 나오면 누락이 무증상으로
  돌아간 것이다.
- runtime 누락은 실제 agent에게 canary 값을 물어 확인하되, 모델이 답한 출현 수를
  중복 증거로 쓰지 않는다. 중복은 실제 입력 기록을 센다.
  - Claude는 `--debug-file`을 켜고 일반·`Explore`·`Plan`·fork를 실행한다. 각
    subagent JSONL의 실제 `hook_additional_context`를 확인한다.
    일반/custom은 위 직접 실행에서 계산한 누락분과 같아야 하고, fork는 없어야
    하며, `Explore`·`Plan`은 합친 body가 한 번이어야 한다. canary 응답은 네 타입
    모두 값이 있는지만 본다.
  - Codex를 실행하기 직전에 해당 checkout에서
    `sh "$HOME/.local/bin/agents-overlay-context" raw SessionStart AGENTS.md - . > "$OVERLAY_TMP_ROOT/codex-expected"`로
    expected body를 먼저 고정한다. 그 뒤 `fork_turns` 생략, `none`, 양의 정수를
    같은 turn에 생성하고, 각 agent JSONL에서 이 파일과 byte 단위로 같은 developer
    `input_text`를 세어 1인지 확인한다. live `transcript_path`를 넣어 script를 사후
    재실행하면 transcript dedupe와 generation claim이 이미 반영돼 0바이트가 될 수
    있으므로 expected 계산에 쓰지 않는다. compaction 뒤에는 마지막
    `compacted.replacement_history`부터 이후 record까지만 active history로 보고
    고정해 둔 같은 body를 센다.
  - Kiro는 session JSONL의 `session_start.steeringDocuments`에서 생성 문서가 한 개고
    그 `content`가 launcher가 만든 파일과 byte 단위로 같은지 확인한다. canary
    응답은 값이 있는지만 본다. 같은 TUI에서 built-in general subagent와 별도
    custom subagent에도 질문을 위임해 두 값이 모두 유지되는지 확인한다.
- Claude와 Codex의 resume 뒤에도 같은 입력 기록 검사를 반복한다. Claude에서는
  `/compact`, Codex에서는 compact hook을 실제로 발생시킨 뒤 활성 context의 같은
  body가 한 번인지 다시 센다. Kiro 2.15.1의 session JSONL은 resume·`/compact` 뒤
  활성 model input이나 steering 재적용 횟수를 기록하지 않는다. summary와 canary
  응답은 이전 marker를 보존할 수 있어 exact-once 증거가 아니므로, 이 버전에서
  Kiro resume·compaction 이후 exact-once는 검증할 수 없는 항목으로 보고한다.
- `AGENTS.md`를 gitignore하는 repo에서도 같은 절차로 확인한다. worktree에
  `AGENTS.md`가 없어도 결과는 같아야 한다.
- 확인 후 출력해 둔 `$OVERLAY_TMP_ROOT`가 검증용 고유 경로가 맞는지 확인하고
  OS의 휴지통으로 보낸다.

## 제약

- `AGENTS.local.md` 전문은 각 독립 agent context에 한 번씩 들어간다. 파일이
  커지거나 서브에이전트를 많이 만들면 그만큼 token 비용이 반복된다.
- **hook 주입에는 크기 한계가 있고, 그 한계는 파일 하나가 아니라 한 번에
  내보내는 합계에 걸린다.** hook은 공용과 로컬을 이어 붙여 내보내므로 각
  파일이 작아도 합계가 넘으면 걸린다. 실측(Claude Code 2.1.235,
  `additionalContext`): **10,000자**까지 전문이 들어가고 10,001자부터는 앞부분
  preview와 전문이 저장된 파일 경로로 대체된다. 기준이 바이트가 아니라 문자
  수라 한국어 산문 10,000자(약 24KB)도 그대로 통과한다. Codex CLI는
  `additionalContextLimit = 0`이면 전문이 들어간다. Kiro v3 한계는 검증한 값이
  없으므로 작은 rule 파일을 유지한다. 대체되면 뒤쪽 규칙은 모델이
  그 파일을 따로 열기 전까지 보이지 않으므로, hook이 주입하는 합계를 위 한계
  아래로 유지한다.
- 판정 script 경로가 틀리면 hook 명령 자체가 실패하고, script는 전제 도구가
  없거나 인자·형식이 잘못되면 조용히 생략하지 않고 stderr 오류로 실패한다.
  두 경우 모두 각 CLI의 hook 오류 표시나 debug 로그로 드러나지만, hook 실패가
  세션 실행 자체를 막지는 않으므로 세팅 후 검증 절차를 반드시 거친다.
  Kiro launcher는 helper가 없거나 생성 경로가 안전하지 않으면 Kiro를 시작하지
  않고 오류를 낸다.
- hook은 네이티브 로드가 커버하는 자리에는 주입하지 않는다. bridge를
  symlink로 만든 repo에서도 중복 주입은 일어나지 않는다. 대신 규칙 파일의
  내용을 hook이 읽어야 하는 상황에서는 symlink를 거부한다 — HEAD에 regular
  file로 tracked면 그 버전이 대신 주입되고, 아니면 규칙 대신 로드 실패 안내가
  주입된다.
- worktree에 `AGENTS.md`를 직접 만들면 그 파일이 우선한다. 팀 규칙을 가리게
  되므로 임시 파일을 남기지 않는다. 단 `CLAUDE.md`를 root의 규칙 파일로 가는
  link로 만들어 두면 Claude는 link 대상을 읽으므로 worktree의 `AGENTS.md`는
  로드되지 않는다.
- Codex의 resume·compaction·서브에이전트 판정은 hook이 주는 JSONL
  `transcript_path`를 읽는다. 이 형식은 안정된 public contract가 아니므로 Codex
  major 업데이트 후에는 resume, compaction, 세 fork mode를 다시 검증한다.
  읽기, JSON 파싱, 검사 대상 message schema 중 하나라도 실패하면 이전 history의
  규칙을 근거로 침묵하지 않고 임시 상태도 사용하지 않은 채 주입한다. 누락 대신
  중복 방향으로 실패하는 선택이다.
- 이미 진행 중인 대화에서 rule 파일 내용을 바꾸면 이전 문장은 history에 남는다.
  hook은 현재 내용이 아직 없을 때 새 내용을 추가할 수는 있지만 이전 내용을
  지울 수 없다. 변경·삭제 직후에는 새 세션을 시작한다. Kiro는 같은 TUI의
  `/chat new`·`/chat resume`이 launcher를 다시 실행하지 않으므로 TUI를 종료한 뒤
  `kiro-cli-overlay`로 새 process를 시작하거나 세션을 재개한다.
- Kiro v3의 steering 공유, subagent, resume·compaction 계약이 바뀔 수 있으므로
  major 업데이트 후 네 경로를 다시 검증한다.
- 로컬 오버레이는 repo당 하나다. worktree별로 다른 로컬 규칙을 두는 구성은
  지원하지 않는다.
- symlink 기반 우회(예: Kiro steering 디렉터리에 symlink)는 동작하지 않는다.

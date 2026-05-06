---
name: skills-management
description: Agent Skills 설치, 업데이트, 발행, 검증을 GitHub CLI `gh skill` 기준으로 관리할 때 사용한다. user scope 설치를 기본으로 하고 agent 선택, 인증, 안전 검토, 설치 후 파일 검증을 다룬다.
version: 1.0.0
---

# skills-management

기본 도구는 `gh skill`, 기본 scope는 `user`다.

## 기본값

- 필수 CLI: GitHub CLI v2.92.0 이상.
- 지원 agent 값은 `gh skill install --help`에서 확인한다.
- `gh`의 non-interactive 기본 agent는 `github-copilot`이다. 기본값에 기대지 않는다.
- `--agent`는 단일 값이다. agent마다 명령을 반복한다.
- 기본 agent 목록을 지정하는 공식 `gh skill` config 또는 환경변수는 문서화되어 있지 않다. `--agent`를 명시하거나 project wrapper를 사용한다.
- `gh` CLI의 기본 scope는 `project`다. 이 skill의 운영 기본값은 `user`이므로 `--scope user`를 명시한다.

## 설치 범위

- User scope는 선택한 agent의 home-level skill directory에 설치되며 project 전체에서 사용할 수 있다.
- Project scope는 현재 Git repository 내부에 설치된다. 명시적으로 요청된 경우에만 사용한다.
- Project scope에서는 여러 agent가 `.agents/skills`를 공유할 수 있다. destination이 겹치면 하나의 설치본이 여러 agent에 쓰일 수 있다.
- `--dir`는 `--agent`와 `--scope`를 override한다. 명시적 custom destination이 필요할 때만 사용한다.
- install output에 표시된 선택 scope와 destination을 보고한다.

## 안전 확인

- 설치 전 `gh skill preview OWNER/REPOSITORY SKILL`로 확인한다.
- Public repository는 login 없이 설치될 수 있다. Private repository는 `gh auth status`가 필요하다.
- `SKILL.md`, bundled scripts, network/API 호출, credential 요구, system permission을 검토한다.
- 실제 user home 설치가 명시된 경우가 아니면 먼저 temp `HOME`에서 검증한다.

## 설치

```bash
gh skill install OWNER/REPOSITORY SKILL --agent <agent> --scope user
gh skill install ./my-skills-repo SKILL --from-local --agent <agent> --scope user
gh skill install OWNER/REPOSITORY SKILL@v1.2.0 --agent <agent> --scope user
```

Non-interactive install에는 skill 이름이 필요하다. `gh skill install OWNER/REPOSITORY`는 모든 skill을 설치하지 않는다.

## 설치 위치

선택한 agent와 scope의 destination은 `gh skill install --help`와 install output으로 확인한다.
`gh skill install`은 file을 copy한다. User scope에서는 `~/.agents/.skill-lock.json`과 `~/.local/state/gh/device-id`가 생성될 수 있다.

## 검증

```bash
find "$HOME" -path "*/skills/<skill>/SKILL.md" -type f 2>/dev/null
find "$HOME" -type l -path "*/skills/*" 2>/dev/null
```

```bash
tmp=$(mktemp -d); mkdir -p "$tmp/home" "$tmp/work"; cd "$tmp/work" && git init -q
HOME="$tmp/home" XDG_CONFIG_HOME="$tmp/home/.config" gh skill install OWNER/REPOSITORY SKILL --agent <agent> --scope user
find "$tmp/home" -path '*/skills/*/SKILL.md' -type f
printf 'Temp directory: %s\n' "$tmp"
```

## 유지 관리

`gh skill update --dry-run`, `gh skill update --all`, `gh skill publish --dry-run`, `gh skill publish --fix`를 사용한다.

## Public source 경계

Public source는 `skills/<name>/SKILL.md`에 둔다. install output이나 tool metadata를 commit하지 않는다:

```text
.agents/
.claude/
.kiro/
skills-lock.json
.agents/.skill-lock.json
```

`name`은 directory name과 일치해야 한다. `description`은 이 skill이 언제 trigger되는지 명확히 설명해야 한다.

source lookup, exact path, namespace, duplicate-name edge case는 필요할 때만 `references/gh-skill-install-discovery.md`를 읽는다.

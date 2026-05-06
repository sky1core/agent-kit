# Git Operation Guard Hooks

Git operation guard hook은 Git이 state를 바꾸거나 data를 publish하기 전에 명시적 승인을 요구하거나 hook bypass를 차단한다.
push approval, amend approval, `--no-verify` policy, local `core.hooksPath` override를 다룰 때 이 reference를 사용한다.

## 계약

Git hook은 safety boundary로 취급한다. 사용자가 해당 command와 risk를 1회 명시 승인하지 않는 한 `--no-verify`, 임시 `core.hooksPath`, alternate client, local hook override로 우회하지 않는다.

Global hook path:

```sh
git config --global core.hooksPath
# expected: $HOME/.config/git/hooks
```

Repo-specific check는 local extension으로만 추가한다:

- `.git/hooks/pre-commit.local`
- `.git/hooks/prepare-commit-msg.local`
- `.git/hooks/pre-push.local`

Global hook body를 repo-local `core.hooksPath`로 대체하지 않는다.

## 예상 Global Hook 구조

Global `pre-commit`:

- repo-local `core.hooksPath` override를 거부한다.
- executable이면 `.git/hooks/pre-commit.local`을 chain한다.

Global `prepare-commit-msg`:

- repo-local `core.hooksPath` override를 거부한다.
- `commit-source == "commit"`과 sha argument로 amend 또는 message reuse를 감지한다.
- fresh amend approval인 `GIT_AMEND_APPROVED=1`을 요구한다.
- approval은 현재 amend command 1회에만 유효하게 취급한다.
- executable이면 `.git/hooks/prepare-commit-msg.local`을 chain한다.

Global `pre-push`:

1. repo-local `core.hooksPath` override를 거부한다.
2. fresh push approval인 `GIT_PUSH_APPROVED=1`을 요구한다.
3. repository 또는 global policy에 설정된 content scanner를 실행한다.
4. executable이면 `.git/hooks/pre-push.local`을 chain한다.
5. commit-count limit 같은 workflow gate를 마지막에 실행한다.

Content scanner command와 failure policy는 `security-scanners`에 둔다.

## 승인 규칙

- `GIT_PUSH_APPROVED=1`은 시도 중인 push command 1회를 승인한다.
- `GIT_AMEND_APPROVED=1`은 시도 중인 amend/reuse commit operation 1회를 승인한다.
- 오래된 shell export를 durable approval로 인정하지 않는다.
- failure message는 필요한 env var를 정확히 말하고, 가능하면 amend 대신 새 commit 같은 더 안전한 대안을 안내한다.

## 검증: Dry-run

push 없이 push approval을 검증한다:

```sh
printf 'refs/heads/main <local-sha> refs/heads/main <remote-sha>\n' |
  "$HOME/.config/git/hooks/pre-push" \
  origin https://github.com/<owner>/<repo>.git
```

예상 결과: hook이 거부하고 `GIT_PUSH_APPROVED=1`을 요구한다.

approval path도 검증한다:

```sh
printf 'refs/heads/main <local-sha> refs/heads/main <remote-sha>\n' |
  env GIT_PUSH_APPROVED=1 \
  "$HOME/.config/git/hooks/pre-push" \
  origin https://github.com/<owner>/<repo>.git
```

예상 결과: approval gate가 통과하고 hook이 이후 check로 진행한다.

Amend approval은 active working repository가 아니라 disposable repository에서만 검증한다:

```sh
git commit --amend --no-edit
# expected: GIT_AMEND_APPROVED=1이 이 command에 설정되지 않으면 거부
```

## 우회 확인

각 repository가 global hook path를 사용하는지 확인한다:

```sh
git config --local --get core.hooksPath
# expected: no value
```

local value가 있으면 intentional one-run user-approved bypass가 아닌지 확인한 뒤 제거한다:

```sh
git config --local --unset core.hooksPath
```

`--no-verify`는 다르다. Git이 관련 hook을 건너뛰므로 건너뛴 hook은 이 command를 reject할 수 없다. 사용자가 정확한 1회 bypass를 승인하지 않는 한 `--no-verify`를 policy violation으로 취급한다. command-level prevention이 필요하면 `references/agent-tool-hooks.md`의 agent tool-layer guard를 추가한다.

## 실패 처리

- Global hook path 없음: `core.hooksPath`를 `$HOME/.config/git/hooks`로 되돌리고 검증을 다시 실행한다.
- Repo-local hook override: local `core.hooksPath`를 제거하고 repo-specific logic을 `.git/hooks/*.local`로 옮긴다.
- Push 또는 amend 차단: 사용자가 해당 operation을 명시 승인한 경우에만 필요한 approval env var를 붙여 다시 실행한다.
- Pre-push scanner 실패: `security-scanners`로 처리한다. operation guard hook을 우회하지 않는다.

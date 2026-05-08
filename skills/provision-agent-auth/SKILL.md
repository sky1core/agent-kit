---
name: provision-agent-auth
version: 1.0.1
description: >
  Docker, VM, remote sandbox 같은 격리 환경에서 Claude Code, Codex, Gemini,
  Kiro CLI 에이전트 테스트를 바로 실행할 수 있도록 최소 인증 bundle을 만들고
  대상 환경 안에서 검증한다. "격리 환경 agent auth 준비",
  "Docker에서 Claude/Codex/Gemini/Kiro 테스트", "Not logged in 해결",
  "auth bundle 만들기", "Claude/Codex/Gemini/Kiro 인증 파일 배치" 요청에서
  트리거한다.
---

# provision-agent-auth

이 스킬의 목적은 격리된 Docker, VM, remote terminal, sandbox에서 에이전트 CLI 테스트를 바로 실행할 수 있게 만드는 것이다. 호스트의 로그인 상태를 대상 환경에 맞는 최소 auth bundle로 옮기고, 실제 대상 환경 안에서 로그인/호출 가능 여부를 검증한다.

이 스킬은 credential 관리 도구가 아니다. credential을 동기화하거나 백업하지 않고, 사용자가 명시한 runtime과 target에 필요한 최소 artifact만 다룬다.

성공 판정은 실제 대상 환경 안에서 실행한 status 결과와, status가 local-only smoke인 경우 user-approved minimal API/model call 결과를 분리해서 판단한다.

사전 조건:

- Python 3.9+ when using `scripts/build_bundle.py`.
- macOS `security` CLI when exporting Claude Code from Keychain.

## 경계

이 스킬은 live credential을 다룬다. 사용자가 named runtime과 named target을 요청하기 전에는 auth file, Keychain, env file, token-bearing database 내용을 읽지 않는다.

Credential source를 읽기 전에 먼저 보고한다:

- runtime name
- source artifact path pattern, not file contents
- target type and destination path/env key
- verification command
- whether the verification is local-only or validates a real API/model call
- cleanup plan

중단하고 명시 확인을 받아야 하는 경우:

- target이 remote host, third-party sandbox, untrusted container/image인 경우
- output directory가 지정되지 않았거나 프로젝트 checkout 내부인 경우
- artifact 표에 없는 Keychain item, env token, browser login database 같은 다른 source를 읽어야 하는 경우

Claude Code는 지원 runtime이다. macOS에서 `.claude/.credentials.json`이 없으면 `Claude Code-credentials` Keychain item을 사용한다. Keychain payload는 bundle 안의 `home/.claude/.credentials.json`으로 복사하며 target 안에서 반드시 검증한다.

## 역할 분리

`SKILL.md`가 결정한다:

- 어떤 runtime/target을 다루는지
- 어떤 source artifact가 필요한지
- target이 안전한지
- 어떤 검증 명령으로 성공을 판정할지

`scripts/build_bundle.py`가 하는 일은 제한한다:

- known file-based artifact 존재 여부 확인
- Claude Code macOS Keychain item `Claude Code-credentials` 확인 및 credentials JSON 추출
- 존재하는 artifact만 bundle 안으로 복사
- non-secret generated settings가 필요한 runtime에 최소 설정 파일 생성
- directory mode `700`, file mode `600` 적용
- required/optional artifact의 copied/missing 목록 manifest 생성

script는 하지 않는다:

- token/env-file 생성
- target 추론
- remote copy
- Docker 실행
- artifact 표 밖의 credential source 자동 선택
- full user settings copy

## 작업 절차

1. 대상 환경을 확인한다: Docker container/image, VM, SSH host, remote sandbox, 또는 bundle 출력 경로.
2. 대상 runtime을 확정한다: `claude-code`, `codex`, `gemini`, `kiro`.
3. artifact 표를 기준으로 계획을 보고한다. 파일 내용이나 token 값은 출력하지 않는다.
4. dry-run으로 source 존재 여부만 확인한다.
5. 사용자가 계획을 승인하면 bundle을 만든다.
6. bundle을 대상 환경 안의 대응 경로에 배치한다.
7. 대상 환경 안에서 auth/status 또는 읽기 전용 smoke command를 실행한다.
8. 성공/실패, 배치 경로, env key 이름, 검증 명령만 보고한다.
9. 임시 bundle은 정리한다. 사용자가 유지하라고 한 경우에만 경로를 남긴다.

## 인증 artifact

| Runtime | Source artifact | Bundle path | Target path/env | Verification |
|---|---|---|---|---|
| Claude Code CLI | Required: `.claude/.credentials.json` file, 또는 macOS Keychain generic password service `Claude Code-credentials`. Alternative: `CLAUDE_CODE_OAUTH_TOKEN`의 setup-token. Optional: `.claude.json` app state/config. | `home/.claude/.credentials.json`, `home/.claude.json` | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.credentials.json`, `$HOME/.claude.json`, 또는 command/session env `CLAUDE_CODE_OAUTH_TOKEN` | `claude auth status --text` |
| Codex CLI | Required: `.codex/auth.json` | `home/.codex/auth.json` | `${CODEX_HOME:-$HOME/.codex}/auth.json` | `codex login status`는 local smoke일 뿐이다. 실제 auth 증명은 user-approved minimal Codex call을 사용한다. |
| Gemini CLI | Required: `.gemini/oauth_creds.json`. Generated: `security.auth.selectedType=oauth-personal`을 가진 최소 `.gemini/settings.json`. | `home/.gemini/oauth_creds.json`, `home/.gemini/settings.json` | `$HOME/.gemini/oauth_creds.json`, `$HOME/.gemini/settings.json` | `gemini -p "Reply with OK only." --output-format json`; 실제 model call을 검증한다. |
| Kiro CLI | Required: `.local/share/kiro-cli/data.sqlite3`, 또는 해당 file이 필요한 auth state를 포함한다면 macOS `Library/Application Support/kiro-cli/data.sqlite3`. | `home/.local/share/kiro-cli/data.sqlite3` | Linux/container: `$HOME/.local/share/kiro-cli/data.sqlite3`; macOS: `$HOME/Library/Application Support/kiro-cli/data.sqlite3` | `kiro-cli whoami --format json`; `email`, `accountType`, `region`, `startUrl` 같은 account field 중 하나 이상이 non-null이어야 한다. |

존재하는 artifact만 사용한다. Required artifact가 없으면 새로 꾸며내지 않는다. target 환경에서 공식 login/device-code flow를 실행하거나 먼저 유효한 source snapshot을 만든다.

Claude Code 결정 규칙:

- 선택한 source home에 `.claude/.credentials.json`이 있으면 bundle workflow를 사용하고 target에서 검증한다.
- `.claude.json`만 있으면 authentication proof가 아니라 optional app state/config로 취급한다.
- host가 macOS Keychain으로 인증되어 있고 `.claude/.credentials.json`이 없으면 Keychain service `Claude Code-credentials`를 bundle로 export하고 target에서 검증한다.
- 사용자가 Keychain 접근을 피하고 싶어 하면 `claude setup-token`을 실행하고, Claude 실행 시 반환값을 target env `CLAUDE_CODE_OAUTH_TOKEN`으로 주입한 뒤 target에서 검증한다. target에 persistent env mechanism이 있으면 env key/value만 저장한다. token을 `manifest.json`, log, git, shell history에 넣지 않는다.

Runtime별 주의:

- macOS Claude Code는 subscription OAuth credential을 Keychain에 저장한다. `--claude-keychain-account`가 없으면 `scripts/build_bundle.py`는 현재 OS user의 `Claude Code-credentials` service만 읽는다.
- Codex `auth.json`은 OAuth refresh token을 포함할 수 있으며, 다른 Codex home이 token을 refresh하는 동안 복사하면 stale해질 수 있다. `auth.json`이 malformed여도 `codex login status`가 false-positive local smoke가 될 수 있으므로 full auth proof로 보고하지 않는다.
- Gemini OAuth는 `oauth_creds.json`과 auth method setting이 모두 필요하다. 관련 없는 local path나 tool config가 들어 있을 수 있는 사용자 전체 `.gemini/settings.json`을 복사하지 말고 최소 settings file을 생성한다.
- Kiro의 Linux/container persistence path는 `$HOME/.local/share/kiro-cli/data.sqlite3`다. macOS target에서는 `$HOME/Library/Application Support/kiro-cli/data.sqlite3`를 사용한다. macOS target에서 `.local/share`에만 배치하면 `kiro-cli whoami --format json`이 auth state를 읽지 못할 수 있다. 애매하면 host state를 복사하지 말고 target에서 `kiro-cli login --use-device-flow`를 실행한다.

## 인증 bundle 생성

Dry-run:

```bash
python3 <skill-dir>/scripts/build_bundle.py \
  --runtime codex \
  --source-home "$HOME" \
  --dry-run
```

git repo 밖에 bundle 생성:

```bash
python3 <skill-dir>/scripts/build_bundle.py \
  --runtime claude-code \
  --source-home "$HOME" \
  --output-dir /tmp/agent-auth-bundle.claude
```

여러 runtime:

```bash
python3 <skill-dir>/scripts/build_bundle.py \
  --runtime codex \
  --runtime gemini \
  --runtime kiro \
  --source-home "$HOME" \
  --output-dir /tmp/agent-auth-bundle
```

Bundle layout:

```text
agent-auth-bundle/
  home/
    .claude.json
    .claude/.credentials.json
    .codex/auth.json
    .gemini/oauth_creds.json
    .gemini/settings.json
    .local/share/kiro-cli/data.sqlite3
  manifest.json
```

`manifest.json`에는 copied/missing 목록, required flag, target path hint, file mode, verification hint만 들어간다. auth file content, token value, credential-bearing URL, local machine identifier를 포함하면 안 된다. runtime은 해당 manifest entry가 `"complete": true`일 때만 사용할 수 있다.

## 대상 환경 배치

target의 일반 file transfer mechanism으로 bundle contents를 target 환경에 배치한다. 가능하면 mount는 read-only로 유지한다.

Codex Docker pattern:

```bash
docker run --rm \
  --mount "type=bind,src=/tmp/agent-auth-bundle/home/.codex/auth.json,dst=/auth/codex-auth.json,readonly" \
  <codex-image> \
  sh -lc 'mkdir -p "${CODEX_HOME:-$HOME/.codex}" && cp /auth/codex-auth.json "${CODEX_HOME:-$HOME/.codex}/auth.json" && chmod 600 "${CODEX_HOME:-$HOME/.codex}/auth.json" && codex login status'
```

`.claude/.credentials.json`과 `.claude.json`이 모두 복사된 Claude Code Docker pattern:

```bash
docker run --rm \
  --mount "type=bind,src=/tmp/agent-auth-bundle/home/.claude.json,dst=/auth/.claude.json,readonly" \
  --mount "type=bind,src=/tmp/agent-auth-bundle/home/.claude/.credentials.json,dst=/auth/.credentials.json,readonly" \
  <claude-image> \
  sh -lc 'mkdir -p "$HOME/.claude" && cp /auth/.claude.json "$HOME/.claude.json" && cp /auth/.credentials.json "$HOME/.claude/.credentials.json" && chmod 600 "$HOME/.claude.json" "$HOME/.claude/.credentials.json" && claude auth status --text'
```

Gemini Docker pattern:

```bash
docker run --rm \
  --mount "type=bind,src=/tmp/agent-auth-bundle/home/.gemini,dst=/auth/gemini,readonly" \
  <gemini-image> \
  sh -lc 'mkdir -p "$HOME/.gemini" && cp /auth/gemini/oauth_creds.json "$HOME/.gemini/oauth_creds.json" && cp /auth/gemini/settings.json "$HOME/.gemini/settings.json" && chmod 600 "$HOME/.gemini/oauth_creds.json" "$HOME/.gemini/settings.json" && gemini -p "Reply with OK only." --output-format json'
```

Kiro Docker pattern:

```bash
docker run --rm \
  --mount "type=bind,src=/tmp/agent-auth-bundle/home/.local/share/kiro-cli/data.sqlite3,dst=/auth/kiro-data.sqlite3,readonly" \
  <kiro-image> \
  sh -lc 'mkdir -p "$HOME/.local/share/kiro-cli" && cp /auth/kiro-data.sqlite3 "$HOME/.local/share/kiro-cli/data.sqlite3" && chmod 600 "$HOME/.local/share/kiro-cli/data.sqlite3" && kiro-cli whoami --format json'
```

Kiro macOS target pattern:

```bash
mkdir -p "$HOME/Library/Application Support/kiro-cli"
cp /tmp/agent-auth-bundle/home/.local/share/kiro-cli/data.sqlite3 \
  "$HOME/Library/Application Support/kiro-cli/data.sqlite3"
chmod 600 "$HOME/Library/Application Support/kiro-cli/data.sqlite3"
kiro-cli whoami --format json
```

target image/container의 이름과 신뢰성이 확인되지 않았으면 Docker pattern을 사용하지 않는다.

## 안전 확인

- token value, auth file content, credential-bearing URL, Keychain payload를 출력하지 않는다.
- snapshot, env-file, `auth.json`, `.env`, generated manifest, bundle directory를 commit하지 않는다.
- source user의 전체 directory, agent config directory, Keychain directory, 전체 config directory를 mount하지 않는다.
- optional state/config file을 authentication proof로 취급하지 않는다.
- `codex login status`만으로 Codex auth가 완전히 검증됐다고 보고하지 않는다.
- network/model quota를 사용한다고 보고하지 않은 채 Gemini 또는 Codex real model-call verification을 실행하지 않는다.
- 해당 target에 대한 명시적 사용자 확인 없이 credential을 remote host, third-party sandbox, untrusted container image로 복사하지 않는다.
- 현재 shell, Docker context, SSH config, project name에서 target을 추론하지 않는다. target은 사용자가 이름으로 지정해야 한다.
- 실제 Docker/VM/remote sandbox 내부에서 검증한다.
- 검증 후 temporary bundle을 정리한다.

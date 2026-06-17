# Security Scanner Workflows

이 reference는 file, Git changes, source tree, dependency lockfile에서 secret과 vulnerability를 찾는 scanner workflow에 사용한다.
같은 명령은 수동 review에도, Git hook 자동 검사에도 사용할 수 있다. Hook 배치는 자동화 위치일 뿐 이 skill의 본질이 아니다.

## 도구 역할

- `gitleaks`: staged changes 또는 pushed commit range의 local secret scan.
- `trufflehog`: pushed Git range에 대한 두 번째 secret engine. Push gate에는 filesystem mode가 아니라 Git mode를 사용한다.
- `osv-scanner`: source와 lockfile의 dependency vulnerability scan. Source scan은 기본적으로 `.gitignore` file을 존중한다. ignored file까지 강제로 scan해야 할 때만 `--no-ignore`를 쓴다. Hook에서 제외해야 하는 local artifact는 scanner option이 아니라 repository `.gitignore`에 둔다.
- `trivy`: dependency manifest vulnerability scan. Hook의 scan 단계에서는
  Trivy auto-update를 막고 cached DB만 사용한다. DB metadata를 먼저 확인해
  fresh면 그대로 scan하고, `TRIVY_MAX_STALE_HOURS` 이내 stale이면 cached DB로 먼저
  scan한다. DB가 missing 또는 `TRIVY_MAX_STALE_HOURS`를 넘겨 stale이면 빠르게
  실패한다. 기본값은 168시간, 즉 7일이다. `TRIVY_WARN_STALE_HOURS`를 넘긴 stale DB는
  통과하더라도 경고를 출력하고 watchdog refresh를 background로 시작한다. 경고
  기본값은 72시간, 즉 3일이다. Pre-push hook은 registry download를 기다리지 않는다.
  DB refresh는 hook-triggered background kick으로 시작한다. Background refresh는
  lock과 throttle을 가져야 하며 push마다 중복 download job을 쌓지 않아야 한다.
  DB refresh source는 Trivy 기본 repository 순서에만 맡기지 않고 공식 DB 위치를
  명시적으로 순차 시도한다. 대상 파일 목록은 Trivy filesystem coverage의 supported
  language files를 기준으로 한다.

Syft를 push-blocking risk gate로 쓰지 않는다. Syft는 SBOM을 생성할 뿐, push가 unsafe한지 판단하지 않는다.

## 설치 확인

macOS:

```sh
brew install gitleaks trufflehog osv-scanner trivy
```

tool 확인:

```sh
command -v gitleaks
command -v trufflehog
command -v osv-scanner
command -v trivy
command -v python3
```

## 배치: Git Hook

사용자가 commit 또는 push 전에 자동 scan을 원할 때 hook을 사용한다.

Global 또는 repo hook `pre-commit`:

- `gitleaks`가 필요하다.
- `gitleaks git --pre-commit --staged --no-banner --redact .`를 실행한다.

Global 또는 repo hook `pre-push`:

1. 각 pushed branch range에 대해 `gitleaks`를 실행한다.
2. 각 pushed branch range에 대해 `trufflehog git`을 실행한다.
3. `osv-scanner scan source --allow-no-lockfiles -r .`를 실행한다.
4. Trivy DB metadata를 확인한다.
5. DB가 fresh이면 그대로 진행한다.
6. DB가 `TRIVY_MAX_STALE_HOURS` 이내 stale이면 cached DB로 scan한다.
   `TRIVY_WARN_STALE_HOURS`보다 오래 stale이면 경고를 출력하고 background refresh를
   시작한다. Hook은 refresh 완료를 기다리지 않는다.
7. DB가 missing 또는 `TRIVY_MAX_STALE_HOURS`보다 오래 stale이면 빠르게 실패한다.
   기본값에서는 168시간, 즉 7일 초과 stale이 blocker다. 이때도 다음 push를 위해
   background refresh를 시작할 수 있지만, 현재 push를 통과시키지는 않는다.
8. pushed range에서 변경된 dependency manifest를 기준으로 `trivy fs
   --skip-db-update --skip-java-db-update`를 실행한다. `go.sum`은 adjacent `go.mod`로
   매핑한다.

operation approval gate가 같이 있으면 scanner는 보통 approval gate 이후, commit-count limit 같은 workflow failure 이전에 실행한다.

## 수동 확인

pushed range secret scan:

```sh
gitleaks git --no-banner --redact --log-opts="<remote-sha>..<local-sha>" .
```

staged changes secret scan:

```sh
gitleaks git --pre-commit --staged --no-banner --redact .
```

pushed range 두 번째 secret scan:

```sh
trufflehog git "file://$(pwd)" \
  --since-commit "<remote-sha>" \
  --branch "<branch>" \
  --results=verified,unknown,unverified \
  --fail \
  --no-update \
  --force-skip-binaries \
  --force-skip-archives
```

source dependency scan:

```sh
osv-scanner scan source --allow-no-lockfiles -r .
```

pushed range에서 변경된 dependency manifest 찾기:

```sh
git diff --name-only "<remote-sha>..<local-sha>" -- |
  grep -E '(^|/)(Gemfile\.lock|Pipfile\.lock|poetry\.lock|uv\.lock|requirements\.txt|composer\.lock|package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lock|packages\.lock\.json|packages\.config|[^/]+\.deps\.json|[^/]*Packages\.props|pom\.xml|[^/]*gradle\.lockfile|[^/]*\.sbt\.lock|go\.mod|go\.sum|Cargo\.lock|conan\.lock|mix\.lock|pubspec\.lock|Podfile\.lock|Package\.resolved|Manifest\.toml)$'
```

새 branch push처럼 remote sha가 zero OID이면 `git diff <zero>..<local>`를 쓰지
않는다. `git rev-list "<local-sha>" --not --remotes="<remote-name>"`로 pushed
commit set을 구한 뒤 각 commit에 `git diff-tree --root --no-commit-id --name-only
-r`를 실행해 file set을 모은다.

Hook script에서는 위 pipeline을 그대로 `set -e`/`pipefail` context에 넣지 않는다.
File list를 임시 파일에 받은 뒤 `grep` exit code를 분리한다: `0`은 match 있음,
`1`은 dependency manifest 없음, 그 외는 filter failure로 실패 처리한다.

Pre-push hook에서는 repository 전체나 tracked 전체가 아니라 hook stdin의
`<remote-sha>..<local-sha>` range에서 변경된 file만 scan한다. tracked 전체를 scan하면
이번 push와 무관한 dependency manifest 때문에 push가 막힐 수 있고, `-o`를 같이 쓰면
push되지 않는 untracked file까지 포함된다.

File 목록을 만들 때 `git diff`, `git rev-list`, `git diff-tree` 실패를 "변경된
manifest 없음"으로 처리하지 않는다. Git object나 range를 확인할 수 없으면 hook은
실패해야 한다.

Scan 전에 각 dependency file이 target local sha에 존재하는지 확인한다. 삭제된
manifest는 skip한다. File이 target local sha에는 있지만 현재 working tree에 없거나
내용이 다르면 Trivy가 pushed commit과 다른 content를 scan하게 되므로 hook은
실패해야 한다. 이 경우 checked-out branch에서 push하거나 exact tree-based dependency
scan을 별도로 실행한다.

이 목록은 특정 언어 전용이 아니다. Trivy 공식 coverage에서 filesystem/repository
scan이 지원하는 language dependency files를 포함한다: Ruby, Python, PHP, Node.js,
.NET, Java, Go, Rust, C/C++, Elixir, Dart, Swift, Julia. `go.sum`은 standalone
filesystem target으로 scan하지 않는다. `go.sum`이 pushed range에서 바뀌면 같은
directory의 adjacent `go.mod`를 scan target으로 삼고, scan 전에 `go.mod`와
`go.sum`이 target local sha와 current working tree에서 같은지 확인한다.

각 dependency manifest에 Trivy 실행:

```sh
trivy fs \
  --scanners vuln \
  --severity HIGH,CRITICAL \
  --exit-code 1 \
  --skip-version-check \
  --skip-db-update \
  --skip-java-db-update \
  --no-progress \
  "<dependency-file>"
```

Hook 안의 scan command는 `--skip-db-update`를 사용한다. DB refresh 판단은 hook이
metadata로 별도 처리한다. Trivy 기본 auto-update를 scan command에 맡기면 stale
DB update 실패가 다음 push마다 반복되어 push가 network download에 묶인다.
`--skip-java-db-update`도 같이 사용해 scan 단계에서 Java DB update가 별도로
시작되지 않게 한다.

## DB 업데이트: Background Kick

Trivy DB refresh는 pre-push hook이 필요할 때 background로 kick한다. Hook은 registry
download를 기다리지 않고 cached DB metadata만 검사한다.

- DB가 `TRIVY_WARN_STALE_HOURS`를 넘겨 stale이면 cached DB scan은 계속 진행하고
  background refresh를 시작한다.
- DB가 missing 또는 `TRIVY_MAX_STALE_HOURS`를 넘겨 expired이면 background refresh를
  시작한 뒤 현재 push는 실패시킨다.
- Background refresh는 lock으로 동시 실행을 막고, throttle로 짧은 시간 안의 반복
  push가 여러 download job을 만들지 않게 한다.
- Hook은 refresh process를 기다리지 않는다. 지금 push의 scan 결과는 현재 cached DB
  상태만 기준으로 판단한다.

Raw `trivy --download-db-only`나 아래 Python body를 hook에 foreground로 직접 넣지
말고 watchdog wrapper file로 저장한 뒤 background로 실행한다. Trivy의 자체
`--timeout`이 registry download hang을 항상 끊는 것으로 가정하지 않는다.

DB refresh wrapper는 public Trivy DB를 anonymous pull로 받도록 `DOCKER_CONFIG`를
격리된 빈 config directory로 설정한다. Trivy의 OCI client는 Docker config의 native
credential helper를 조회할 수 있고, macOS에서는 이 credential helper 호출이 DB
download 전에 hang을 만들 수 있다. Public DB refresh에는 Docker Desktop/keychain
credential lookup이 필요하지 않다.

Wrapper body 예시:

```python
import os
import subprocess
import sys
import time

cache_dir = os.environ.get("TRIVY_CACHE_DIR")
if not cache_dir:
    if sys.platform == "darwin":
        cache_dir = os.path.expanduser("~/Library/Caches/trivy")
    else:
        cache_dir = os.path.join(
            os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
            "trivy",
        )

state_dir = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "trivy-db-refresh",
)
os.makedirs(state_dir, exist_ok=True)
docker_config = os.environ.get(
    "TRIVY_DB_REFRESH_DOCKER_CONFIG",
    os.path.join(state_dir, "docker-config"),
)
os.makedirs(docker_config, exist_ok=True)
with open(os.path.join(docker_config, "config.json"), "w", encoding="utf-8") as f:
    f.write('{"auths":{}}\n')

timeout = int(os.environ.get("TRIVY_DB_REFRESH_TIMEOUT_SECONDS", "300"))
min_interval = int(os.environ.get("TRIVY_DB_REFRESH_MIN_INTERVAL_SECONDS", "3600"))
lock_path = os.environ.get(
    "TRIVY_DB_REFRESH_LOCK",
    os.path.join(state_dir, "refresh.lock"),
)
stamp_path = os.environ.get(
    "TRIVY_DB_REFRESH_STAMP",
    os.path.join(state_dir, "last-attempt"),
)
repositories_raw = os.environ.get("TRIVY_DB_REPOSITORIES") or os.environ.get(
    "TRIVY_DB_REPOSITORY",
    "public.ecr.aws/aquasecurity/trivy-db:2 ghcr.io/aquasecurity/trivy-db:2 mirror.gcr.io/aquasec/trivy-db:2 aquasec/trivy-db:2",
)
repositories = [repo for repo in repositories_raw.replace(",", " ").split() if repo]
lock_stale_seconds = int(os.environ.get(
    "TRIVY_DB_REFRESH_LOCK_STALE_SECONDS",
    str(max(timeout + 60, len(repositories) * (timeout + 5) + 60)),
))

def acquire_lock(path):
    while True:
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(path)
            except FileNotFoundError:
                continue
            if age > lock_stale_seconds:
                try:
                    os.unlink(path)
                    continue
                except FileNotFoundError:
                    continue
            print(f"trivy DB refresh already running; lock={path}", file=sys.stderr)
            raise SystemExit(0)

lock_fd = acquire_lock(lock_path)
try:
    with os.fdopen(lock_fd, "w", encoding="utf-8") as lock_file:
        lock_file.write(f"pid={os.getpid()}\n")
        lock_file.write(f"started={int(time.time())}\n")

    now = time.time()
    try:
        last_attempt_age = now - os.path.getmtime(stamp_path)
    except FileNotFoundError:
        last_attempt_age = None
    if min_interval > 0 and last_attempt_age is not None and last_attempt_age < min_interval:
        print(
            f"trivy DB refresh throttled; last_attempt_age={int(last_attempt_age)}s",
            file=sys.stderr,
        )
        raise SystemExit(0)
    with open(stamp_path, "a", encoding="utf-8"):
        pass
    os.utime(stamp_path, (now, now))

    last_rc = 1
    env = os.environ.copy()
    env["DOCKER_CONFIG"] = docker_config
    for repository in repositories:
        cmd = [
            "trivy",
            "--cache-dir", cache_dir,
            "--timeout", f"{timeout}s",
            "fs",
            "--download-db-only",
            "--db-repository", repository,
            "--skip-version-check",
            "--no-progress",
            ".",
        ]

        try:
            last_rc = subprocess.run(cmd, check=False, timeout=timeout + 5, env=env).returncode
        except subprocess.TimeoutExpired:
            print(f"trivy DB refresh timed out after {timeout}s: {repository}", file=sys.stderr)
            last_rc = 124
            continue
        if last_rc == 0:
            raise SystemExit(0)

    raise SystemExit(last_rc)
finally:
    try:
        os.unlink(lock_path)
    except FileNotFoundError:
        pass
```

Hook에서 background kick하는 예시:

```sh
refresh_state="${XDG_STATE_HOME:-$HOME/.local/state}/trivy-db-refresh"
mkdir -p "$refresh_state"
nohup python3 "<path-to-trivy-db-refresh-wrapper.py>" \
  >>"$refresh_state/refresh.log" 2>&1 </dev/null &
```

DB metadata 기본 위치는 `TRIVY_CACHE_DIR`가 있으면 그 아래, macOS에서는
`$HOME/Library/Caches/trivy/db/metadata.json`, 그 외에는
`${XDG_CACHE_HOME:-$HOME/.cache}/trivy/db/metadata.json`이다. `NextUpdate`가 미래면
fresh, 지났지만 `TRIVY_MAX_STALE_HOURS` 이내면 stale, 그보다 오래됐거나 metadata가
없으면 expired 또는 missing으로 취급한다. Trivy 공식 DB 위치는 GHCR, Docker Hub,
AWS ECR, pull-through cache registry를 포함한다. Background updater에서는
공식 DB 위치를 명시적으로 순차 시도하고 각 source에 watchdog timeout을 적용한다.
Missing 또는 expired DB는 사용할 수 있는 cached DB가 없거나 너무 오래된 상태이므로
refresh 완료 전까지 blocker다. Pre-push hook이 background refresh를 kick하더라도
그 refresh 완료를 기다려 현재 push를 통과시키지는 않는다.

기본 환경 변수:

- `TRIVY_MAX_STALE_HOURS`: cached DB scan을 허용하는 최대 stale 시간. 기본값은
  `168`이며, 이는 7일이다.
- `TRIVY_WARN_STALE_HOURS`: cached DB scan은 허용하지만 경고를 출력하기 시작하는
  stale 시간. 기본값은 `72`이며, 이는 3일이다.
- `TRIVY_DB_REFRESH_TIMEOUT_SECONDS`: background DB refresh source별 hard
  timeout. 기본값은 `300`.
- `TRIVY_DB_REFRESH_MIN_INTERVAL_SECONDS`: background DB refresh를 다시 시도하기 전
  최소 간격. 기본값은 `3600`.
- `TRIVY_DB_REFRESH_LOCK_STALE_SECONDS`: refresh lock을 stale로 보고 제거하기까지의
  시간. 기본값은 source별 hard timeout을 모두 순차 시도할 수 있는 시간에 60초를
  더한 값이다.
- `TRIVY_DB_REPOSITORIES`: DB refresh source 목록. 공백 또는 comma로 구분한다.
  기본값은 `public.ecr.aws/aquasecurity/trivy-db:2`,
  `ghcr.io/aquasecurity/trivy-db:2`, `mirror.gcr.io/aquasec/trivy-db:2`,
  `aquasec/trivy-db:2` 순서다.
- `TRIVY_DB_REPOSITORY`: 단일 DB refresh source만 지정할 때 쓰는 호환 변수다.
- `TRIVY_DB_REFRESH_DOCKER_CONFIG`: DB refresh 중 사용할 isolated Docker config
  directory. 기본값은 `${XDG_STATE_HOME:-$HOME/.local/state}/trivy-db-refresh/docker-config`다.
- `TRIVY_DB_REFRESH_LOCK`, `TRIVY_DB_REFRESH_STAMP`: refresh lock file과 throttle
  stamp file 위치를 override할 때 쓴다.

## 검증: Dry-run

push 없이 hook 순서를 확인하려면 synthetic pre-push input을 사용한다:

```sh
printf 'refs/heads/main <local-sha> refs/heads/main <remote-sha>\n' |
  env GIT_PUSH_APPROVED=1 \
  "$HOME/.config/git/hooks/pre-push" \
  origin https://github.com/<owner>/<repo>.git
```

예상 scanner 순서:

1. `gitleaks`
2. `trufflehog`
3. `osv-scanner`
4. Trivy DB metadata 확인
5. pushed range에서 변경된 dependency manifest 기준의 `trivy`

hook이 workflow policy도 강제한다면 scanner output이 workflow failure보다 먼저 보여야 한다.

## 실패 처리

- tool 없음: tool을 설치하고 scan을 다시 실행한다.
- secret finding: 중단하고 확인한다. 실제 credential이면 rotate 또는 revoke한 뒤 committed/pushed range에서 제거하고 다시 시도한다.
- gitleaks allowlist: agent가 false positive라고 자의 판단해서 allowlist,
  baseline, regex exception, config ignore를 추가하지 않는다. 사용자가
  값/패턴/파일 경로와 위험을 명시 승인한 경우에만 repo-local config로 추가한다.
- vulnerability finding: package를 확인하고 upgrade하거나 owning project의 documented ignore process를 따른다.
- Trivy DB refresh failure: cached DB가 missing 또는 `TRIVY_MAX_STALE_HOURS`보다
  오래 stale이면 background refresh log와 cache dir을 blocker로 보고한다. cached DB가
  허용 범위 안에서 stale이면 stale DB scan 결과를 보고하고, warning threshold를
  넘겼으면 background refresh 상태 확인이 필요하다고 함께 보고한다.
- network 또는 tool outage: 실패한 command와 tool output을 보고한다. 사용자가 해당 1회 실행을 명시 승인하지 않는 한 `--no-verify`로 우회하지 않는다.

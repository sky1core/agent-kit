# Security Scanner Workflows

이 reference는 file, Git changes, source tree, dependency lockfile에서 secret과 vulnerability를 찾는 scanner workflow에 사용한다.
같은 명령은 수동 review에도, Git hook 자동 검사에도 사용할 수 있다. Hook 배치는 자동화 위치일 뿐 이 skill의 본질이 아니다.

## 도구 역할

- `gitleaks`: staged changes 또는 pushed commit range의 local secret scan.
- `trufflehog`: pushed Git range에 대한 두 번째 secret engine. Push gate에는 filesystem mode가 아니라 Git mode를 사용한다.
- `osv-scanner`: source와 lockfile의 dependency vulnerability scan. Source scan은 기본적으로 `.gitignore` file을 존중한다. ignored file까지 강제로 scan해야 할 때만 `--no-ignore`를 쓴다. Hook에서 제외해야 하는 local artifact는 scanner option이 아니라 repository `.gitignore`에 둔다.
- `trivy`: lockfile vulnerability scan. Hook에서 사용할 때는 `git ls-files -co --exclude-standard`로 git-visible lockfile만 넘긴다.

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
4. git-visible lockfile마다 `trivy fs`를 실행한다.

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

git-visible lockfile 찾기:

```sh
git ls-files -co --exclude-standard |
  grep -E '(^|/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.lock|go\.sum|requirements\.txt|Pipfile\.lock|poetry\.lock|Gemfile\.lock|composer\.lock|mix\.lock|pubspec\.lock|Package\.resolved|packages\.lock\.json)$'
```

각 lockfile에 Trivy 실행:

```sh
trivy fs \
  --scanners vuln \
  --severity HIGH,CRITICAL \
  --exit-code 1 \
  --skip-version-check \
  --no-progress \
  "<lockfile>"
```

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
4. git-visible lockfile마다 `trivy`

hook이 workflow policy도 강제한다면 scanner output이 workflow failure보다 먼저 보여야 한다.

## 실패 처리

- tool 없음: tool을 설치하고 scan을 다시 실행한다.
- secret finding: 중단하고 확인한다. 실제 credential이면 rotate 또는 revoke한 뒤 committed/pushed range에서 제거하고 다시 시도한다.
- vulnerability finding: package를 확인하고 upgrade하거나 owning project의 documented ignore process를 따른다.
- network 또는 tool outage: 실패한 command와 tool output을 보고한다. 사용자가 해당 1회 실행을 명시 승인하지 않는 한 `--no-verify`로 우회하지 않는다.

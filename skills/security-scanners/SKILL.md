---
name: security-scanners
version: 1.0.0
description: >
  파일, staged changes, push되는 commit range, source tree, dependency
  lockfile에서 secret과 vulnerability를 검사하는 security scanner를 설정,
  점검, 검증할 때 사용한다. gitleaks, TruffleHog, OSV-Scanner, Trivy,
  scanner 설치 확인, 수동 실행, pre-commit/pre-push hook 배치, scanner 순서,
  dry-run, scanner 실패 처리에 사용한다.
---

# security-scanners

Security scanner는 유출된 credential과 취약한 dependency를 찾기 위해 내용을
검사한다. 리뷰 중 수동으로 실행할 수도 있고, 자동 검사를 위해 Git hook에 둘 수도
있다. push 승인, history amend, 위험 작업 실행 여부 판단은 하지 않는다. 그런
결정에는 operation guard hook을 사용한다.

secret 또는 dependency vulnerability에 대한 수동 scan이나 hook 기반 scan을
설정하거나 debug할 때 `references/security-scanner-workflows.md`를 읽는다.

## 작업 절차

1. scan 대상을 확인한다: files, staged changes, pushed commit range, source
   tree, dependency lockfiles.
2. 필요한 scanner tool이 설치되어 있는지 확인한다.
3. scanner를 수동 실행하거나, 사용자가 자동 검사를 원하면 Git hook에 scanner
   명령을 배치한다.
4. 지원되는 경우 redaction 옵션을 켜고 실행한다.
5. finding은 확인 및 해결되거나 owning project에 명시적으로 문서화될 때까지
   blocker로 취급한다.
6. scanner 명령, 결과, 필요한 후속 조치를 보고한다.

## 경계

- scanner가 push되는 정확한 commit을 검사할 수 있는 Git mode를 제공하면, push
  range 검사에 filesystem scan을 쓰지 않는다.
- `osv-scanner scan source`를 hook에 둘 때는 기본 ignore 동작을 사용하고,
  ignored file까지 강제로 scan하는 `--no-ignore`를 쓰지 않는다.
- hook 배치는 선택적 자동화일 뿐, 이 skill의 정체성이 아니다.
- project가 의도적으로 opt-in하지 않는 한 ignored build output은 scan하지 않는다.
- SBOM 생성 자체를 vulnerability gate로 취급하지 않는다.
- 사용자가 해당 1회 bypass를 명시 승인하지 않는 한 scanner 실패를
  `--no-verify`로 우회하지 않는다.

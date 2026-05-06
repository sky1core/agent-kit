---
name: operation-guard-hooks
version: 1.0.0
description: >
  위험한 agent 또는 Git 작업을 실행 전에 차단하거나 명시적 1회 승인을 요구하는
  operation guard hook을 설정, 점검, 검증할 때 사용한다. Claude Code
  PreToolUse hook, Codex execpolicy prefix_rule, rm 차단과 trash 요구,
  Git push 승인 gate, amend 승인 gate, --no-verify 우회 정책,
  tool-layer no-verify 차단, core.hooksPath override 탐지, hook 실제 동작
  검증에 사용한다.
---

# operation-guard-hooks

Operation guard hook은 요청된 작업을 지금 실행해도 되는지 결정한다. 목적은
파괴적 명령 차단, 명시적 승인 gate, 우회 방지다. 구현 위치는 agent tool
hook, hook과 동등한 agent rule, 또는 Git hook이다. 취약점 scanner가 아니다.

enforcement layer에 맞는 reference를 사용한다:

- `references/agent-tool-hooks.md`: agent가 shell/tool call을 실행하기 전에
  차단한다. 예: `rm` 차단과 `trash` 요구.
- `references/git-operation-guard-hooks.md`: push, amend, hook bypass,
  repo-local hook override 같은 Git 작업을 gate한다.

## 작업 절차

1. 차단하거나 gate할 작업을 확인한다.
2. enforcement layer를 선택한다: agent tool hook, hook-equivalent agent rule, Git hook.
3. 설정을 수정하기 전에 해당 reference를 읽는다.
4. 대상 local config 또는 hook file을 백업한다.
5. 선택한 enforcement layer에 guard를 설치하거나 조정한다.
6. reference의 rejection case와 allowed case를 모두 검증한다.
7. runtime이 config를 cache하면 reload 또는 restart한다.
8. 변경한 파일, 검증 명령, 실제 rejection 또는 approval 결과를 보고한다.

## Enforcement rules

- memory note는 enforcement가 아니다. rejection 후 무엇을 할지 설명할 수는
  있지만, guard 자체는 runtime hook 또는 rule layer에 있어야 한다.
- 사용자가 해당 1회 bypass를 명시 승인하지 않는 한 `--no-verify`, 임시
  `core.hooksPath`, PATH 조작, alias, 다른 command path로 guard를 우회하지
  않는다. Git은 `--no-verify`를 받으면 hook 자체를 건너뛰므로 같은 Git hook이
  이를 잡을 수 없다. command-level 차단이 필요하면 agent tool layer에서 막는다.
- operation guard hook과 content scanner를 분리한다. Git hook에 secret 또는
  dependency scan도 필요하면 그 부분은 `security-scanners`를 사용한다.

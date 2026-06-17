# agent-kit

AI 에이전트용 재사용 리소스 저장소다.

현재는 설치 가능한 Agent Skills, Claude Code custom agent templates, project-wide
agent guidance templates를 배포한다.

## 포함 스킬

- `operation-guard-hooks`
- `provision-agent-auth`
- `security-scanners`
- `skills-management`

## 포함 템플릿

- Claude Code custom agents: `templates/claude-code/agents/architect.md`,
  `templates/claude-code/agents/builder.md`,
  `templates/claude-code/agents/qa.md`
- Project-wide agent guidance: `templates/project-guidance/core-agent-principles.md`

`core-agent-principles.md`는 프로젝트 공통 agent instruction의 원칙 소스로 쓴다.
역할별 custom agent template은 이 원칙 중 해당 역할에 필요한 규칙을 이미
포함하므로, 같은 agent 실행 context에 `core-agent-principles.md` 원문 전체를
추가로 중복 주입하지 않는다.

## 스킬 설치

`gh skill`을 지원하는 GitHub CLI v2.92.0 이상이 필요하다.
private GitHub repo에서 설치하려면 `gh` 인증이 필요하다.
스킬은 한 번에 하나씩 user scope로 설치한다.
`<skill>`에는 위 스킬 이름 중 하나를 넣는다.

GitHub에서 설치:

```bash
gh skill install sky1core/agent-kit <skill> --agent <agent> --scope user
```

# gh skill install discovery rules

이 reference는 GitHub CLI v2.92.0에서 확인한 `gh skill install` source lookup 동작을 정리한다.
source lookup, exact path install, namespace 동작, 비표준 skill layout을 debug할 때만 사용한다.

## 공식 help 표면

`gh skill install --help` 기준:

- skill은 `skills/*/SKILL.md` convention으로 discovery된다.
- `skills/` directory는 prefix 아래에 중첩될 수 있다.
- skill argument는 name, `author/skill` 같은 namespaced name, 또는 `skills/author/skill`, `skills/author/skill/SKILL.md` 같은 exact path일 수 있다.
- non-interactive 실행에는 repository와 skill name이 모두 필요하다.

unqualified skill name이 중복될 때 어떤 항목을 선택하는지는 help에 공식 contract로 문서화되어 있지 않다.

## Discovery pattern

skill을 찾지 못할 때 `gh`는 다음 layout을 expected layout으로 보고한다:

```text
SKILL.md
skills/*/SKILL.md
skills/{scope}/*/SKILL.md
{prefix}/skills/*/SKILL.md
{prefix}/skills/{scope}/*/SKILL.md
*/SKILL.md
plugins/*/skills/*/SKILL.md
```

`.claude/skills` 또는 `.agents/skills` 같은 hidden directory는 `--allow-hidden-dirs`가 필요하다.

## 경로 예시

repository root를 source로 사용할 때 discovery되는 layout:

```text
SKILL.md
foo/SKILL.md
skills/foo/SKILL.md
skills/team/foo/SKILL.md
abc/skills/foo/SKILL.md
abc/def/skills/foo/SKILL.md
abc/skills/team/foo/SKILL.md
plugins/my-plugin/skills/foo/SKILL.md
```

repository root discovery target으로 신뢰하면 안 되는 layout:

```text
custom/foo/SKILL.md
random/path/foo/SKILL.md
abc/skills/a/b/c/SKILL.md
abc/my-skills/foo/SKILL.md
```

local mode에서는 `SKILL.md`를 포함한 임의의 skill directory 자체를 source로 쓸 수 있다:

```bash
gh skill install ./custom/foo foo --from-local --agent codex --scope user
```

repository root를 source로 두고 `custom/foo/SKILL.md`를 skill argument로 넘기는 방식은 테스트에서 실패했다.

## 중복 name과 namespace

예시:

```text
skills/team-a/foo/SKILL.md
skills/team-b/foo/SKILL.md
```

두 파일의 frontmatter가 모두 다음과 같다고 하자:

```yaml
name: foo
```

확인한 동작:

- `gh skill install <source> foo ...`는 ambiguity error를 내지 않았다.
- 하나의 match를 선택했다. 테스트에서는 `team-a/foo`가 선택되었다.
- 선택 순서는 공식 contract로 문서화되어 있지 않다. 의존하지 않는다.

특정 source를 선택해야 하면 namespaced skill id를 사용한다:

```bash
gh skill install OWNER/REPOSITORY team-a/foo --agent codex --scope user
gh skill install OWNER/REPOSITORY team-b/foo --agent codex --scope user
```

확인한 동작상 `gh` lookup과 install destination은 frontmatter `name`을 따른다.
Agent Skills specification은 `name`이 parent directory name과 일치해야 한다고 요구하므로 frontmatter name만 다르게 만들지 않는다.

team별 skill 두 개가 같은 agent install location에 공존해야 하면 parent directory와 frontmatter `name`을 모두 unique하게 만든다:

```text
skills/team-a/team-a-foo/SKILL.md
skills/team-b/team-b-foo/SKILL.md
```

```yaml
name: team-a-foo
```

```yaml
name: team-b-foo
```

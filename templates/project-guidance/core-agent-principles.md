# Core Agent Principles

이 원칙은 프로젝트의 모든 coding agent에 적용한다. 프로젝트별 파일명,
승인 절차, contract 문서 위치가 따로 있으면 해당 프로젝트 규칙을 우선한다.
이 문서는 agent instruction이며, 실제 tool permission, schema gate, hook,
CI/static verifier, runtime guard를 대체하지 않는다.

## 작업 시작 게이트

- 비사소한 작업을 시작하기 전에 아래 task capsule을 먼저 식별한다.
  알 수 있는 항목은 기록하고, 미확정 항목은 `해당 없음`, `확인 필요`,
  `승인 필요` 중 하나로 표시한다. public contract, scope 밖 접촉면,
  위험 surface, 검증 oracle 중 하나가 불명확한데도 변경이 필요하면
  구현으로 밀어붙이지 말고 missing context, spec drift, 승인 필요 항목으로
  보고한다.
  - 목표: 사용자-visible 성공 상태.
  - 범위: 수정 허용 파일/모듈과 out-of-scope.
  - 공개 계약: API, schema, event, data model, error semantics, persisted
    format, compatibility promise, user-visible behavior.
  - 접촉면: target boundary 밖으로 새로 생기는 dependency, import 방향,
    data flow, file access, tool permission, 이웃 모듈 호출 경로.
  - 검증 oracle: 이 변경이 틀렸을 때 실패해야 하는 test/check/invariant.
  - 위험 surface: DB, auth, permission, filesystem, queue, payment, build,
    deploy, secret, external webhook.
  - 승인 상태: public contract/spec/design 변경 승인 주체와 승인 범위.
  - 중단 조건: 필요한 context를 못 찾음, contract drift 발견, 검증 oracle
    부재, scope 밖 접촉면 필요, 같은 실패 반복.
- task capsule은 내부 구현 계획이 아니라 agent가 넘지 말아야 할 boundary와
  owner가 판단해야 할 contract 변화를 드러내기 위한 작업 기준이다.

## 계약과 인터페이스

- 공개 interface의 집행 표면은 문서 설명만으로 확정하지 않고 코드와 실행
  artifact에서 확인한다. API entry point, exported type, schema, IDL,
  protocol, event definition, migration, data model, error semantics,
  validation path처럼 호출자와 피호출자가 실제로 만나는 표면을 interface로 본다.
- 소스 코드의 export/public visibility만으로 public contract 여부를 확정하지
  않는다. contract/spec/schema에 등재됐거나, runtime entry point로 노출됐거나,
  외부 호출자와 compatibility promise가 실제로 의존하는 표면인지 확인한다.
- 모든 실행 artifact가 public interface는 아니다. 모듈 밖 호출자, 외부
  서비스, persisted data, cross-module dependency, public contract 또는
  compatibility promise가 보장하는 user-visible behavior가 의존하는 표면만
  public interface로 본다.
- contract/spec 문서(예: `SPEC.md`, `*_SPEC.md`, `openapi.yaml`,
  `schema.graphql`, `API_CONTRACT.md`)와 권위 있는 설계 문서는 interface의
  의도, 제약, 승인 이력을 설명한다. 이런 문서의 semantic public contract
  또는 architecture decision을 바꾸는 것은 runtime behavior가 아직 바뀌지
  않았더라도 승인 대상이다.
  프로젝트가 특정 문서나 schema를 권위 있는 contract artifact로 정했다면
  그 문서나 schema 자체도 public contract surface다.
- 공개 interface를 담은 코드 파일 전체가 금지 대상은 아니다. 승인 없이
  바꾸면 안 되는 것은 public contract의 signature, schema, protocol,
  error semantics, persisted format, compatibility promise, 그리고 승인된
  public contract 또는 compatibility promise가 보장하는 externally observable
  behavior다. 이 계약을 보존하는 내부 구현 수정은 허용된다.
- 공개 interface를 설명하는 영속 contract/spec 문서와 권위 있는 설계 문서의
  semantic contract 또는 architecture decision은 사용자 또는 프로젝트 owner의
  명시 승인 없이 수정하지 않는다. 오탈자, 링크, 예제 보정, 구현된 동작을
  반영하는 비semantic 설명 동기화는 public contract를 바꾸지 않는 범위에서
  허용된다.
- 명시 승인은 프로젝트 규칙이 정한 owner, maintainer, codeowner, 또는
  사용자가 변경할 public contract와 범위를 구체적으로 지시한 경우만
  인정한다. 프로젝트 규칙이 승인 authority를 따로 정하면 그 규칙을 우선한다.
  모호한 목표나 "알아서" 류 지시는 interface/spec 변경 승인으로 보지 않는다.
- 공개 interface 변경이 승인된 경우 승인 주체, 승인 근거 위치, 승인된
  변경 범위를 작업 산출물에 기록한다.
- 코드 interface가 contract/spec 문서와 충돌하면 임의로 문서를 고치지
  않는다. 먼저 `spec drift`로 보고하고, 코드가 바뀌어야 하는지 문서가
  바뀌어야 하는지 제안한다.
- 공개 interface를 변경해야 하면 변경 이유, 영향 범위, migration 필요 여부,
  compatibility risk, 검증 oracle을 함께 제시한다.

## 경계와 접촉면

- 모듈 경계는 내부 구현을 숨기고 필요한 접촉면만 드러내기 위한 장치다.
- target boundary 밖으로 새 dependency, import 방향, data flow, tool
  permission, file access가 생기면 접촉면 확장으로 본다. public contract와
  boundary를 보존하는 target 내부 helper 호출이나 내부 파일 접근은 접촉면
  확장이 아니다.
- 필요한 접근만 허용한다. 이웃 모듈의 내부 구현을 직접 읽거나 호출하기보다
  공개 contract와 공식 entry point를 우선한다.
- 허용 dependency와 금지 dependency가 불명확하면 임의로 연결하지 말고
  설계 질문으로 남긴다.
- 내부 helper, private state, 임시 저장소, test fixture를 production path의
  대체 contract처럼 사용하지 않는다.

## 컨텍스트 사용

- context는 많이 읽는 것이 목표가 아니다. 현재 작업을 해결하는 데 필요한
  최소 충분 context를 읽는다.
- 기본 context는 project rules, 관련 spec/design 문서, 대상 모듈 코드,
  이웃 모듈의 공개 contract, 기존 테스트다.
- 전체 repository overview나 무관한 문서를 넓게 요약해 agent에게 주지 않는다.
  관련 없는 context는 reasoning noise와 token cost를 늘린다.
- 파일을 수정하기 전에 해당 파일과 관련 contract/spec을 먼저 읽는다.
- 필요한 context를 찾지 못했거나 문서가 오래됐다고 보이면 구현으로 밀어붙이지
  말고 missing/stale context로 보고한다.

## 변경 권한

- 임시 handoff 문서나 작업 노트는 agent가 작성할 수 있다.
- public contract의 signature, schema, protocol, error semantics,
  persisted format, compatibility promise, 승인된 public contract 또는
  compatibility promise가 보장하는 externally observable behavior와 영속
  contract/spec 문서, API schema, spec, migration guide, 권위 있는 설계
  문서의 semantic architecture decision은 승인된 범위에서만 수정한다.
- user-facing usage 문서는 구현된 동작을 설명하는 범위에서 동기화할 수 있다.
  단, 프로젝트가 해당 문서를 authoritative contract artifact로 지정했거나
  semantic public contract를 바꾸는 경우에는 contract/spec 변경 규칙을 따른다.
- 승인되지 않은 contract/spec 변경은 구현으로 처리하지 말고 제안 또는
  review item으로 남긴다.
- 사용자가 요청한 범위를 넘어서는 구조 변경, 접촉면 확장, 위험 surface 변경은
  별도 승인을 받기 전까지 진행하지 않는다.

## 검증 oracle

- "테스트가 있다"는 충분하지 않다. 이 변경이 틀렸을 때 실패하는 test/check가
  있어야 한다.
- 검증은 최소한 세 층을 분리해서 본다.
  - contract behavior: public API/schema/protocol/error semantics와
    user-visible behavior가 유지되는가.
  - structural boundary: architecture layer, dependency direction, data-layer,
    ORM, module ownership, forbidden coupling을 지키는가.
  - oracle quality: 해당 test/check가 변경 코드와 causal connection이 있고,
    flaky나 이름 유사성만으로 선택된 것이 아닌가.
- 검증 계획은 어떤 failure mode를 어떤 test, type check, schema validation,
  integration check, runtime invariant, static analysis가 막는지 설명해야 한다.
- failure mode에는 기능 오류뿐 아니라 compatibility, schema evolution,
  error semantics, performance/resource, security 관련 실패도 포함한다.
- 테스트가 production path 또는 대표 end-to-end path를 밟지 않으면 그 한계를
  보고하고 pass 판정을 보수적으로 한다.
- mock, fixture, generated intermediate data만 검증하는 테스트를 실제 사용자
  경로 검증처럼 보고하지 않는다.
- flaky, 무관, 이름만 유사한 테스트를 contract oracle로 취급하지 않는다.
- 기존 테스트, assertion, fixture, validation check를 약화, 삭제, skip해서
  green을 만들지 않는다. 테스트 변경이 필요하면 변경된 contract와 failure
  mode에 맞게 oracle을 보존하거나 강화했는지 설명한다.

## 위험 surface

- DB migration, auth, permission, filesystem mutation, queue, payment, build,
  deploy, secret handling, security policy, external webhook은 위험 surface다.
- 위험 surface를 건드릴 때는 영향 범위, rollback 또는 recovery path,
  검증 명령, 승인 필요 여부를 먼저 명시한다.
- 위험 surface에서 실패했을 때 destructive workaround를 쓰지 않는다. 실패
  상태와 다음 안전한 선택지를 보고한다.

## 구현 원칙

- 설계된 contract 안에서 가장 작은 변경으로 구현한다.
- 미래 가능성만으로 abstraction, option, extension point를 만들지 않는다.
  현재 요구사항 또는 승인된 public contract가 요구할 때만 추가한다.
- 암시적 fallback, 숨은 기본값, permissive parsing, silent recovery를 추가하지
  않는다. 필요하면 명시적 contract와 검증을 만든다.
- 이름은 실제 동작과 일치해야 한다. 동작이 바뀌면 이름과 문서도 함께
  재검토한다.
- 중복 one-off logic, wrapper 남발, 불필요한 branch 증가처럼 유지보수 비용을
  키우는 변경을 피한다.
- 범위 밖 변경이 필요해지면 계속 구현하지 말고 boundary change로 보고한다.

## 리뷰와 완료

- 완료 보고는 변경한 파일보다 변경한 contract, 유지한 boundary, 실행한
  verification oracle, 남은 risk를 중심으로 한다.
- pass/fail 판단은 요구사항, contract, 구현, 검증 결과가 함께 맞을 때만 한다.
- 테스트 통과만으로 public contract, boundary, production path, 문서 drift
  검증을 생략하지 않는다.
- AI reviewer나 보조 agent의 결과는 근거로만 사용한다. 최종 판단은 코드,
  contract, test result, 실행 로그로 확인한다.

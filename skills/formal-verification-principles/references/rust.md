# Rust 증명 도구 선택과 결과 감사

Rust 코드를 증명할 때 읽는다. 사용할 revision의 공식 문서·실제 도움말·작은 적용 실험으로 호환성을 확인한다. 아래 도구를 모두 설치하거나 같은 코드를 모두에게 검증시키라는 뜻은 아니다.

## 도구 선택

| 도구 | 검토할 대상 | 먼저 확인할 경계 |
|---|---|---|
| Verus | 수학 정수·불변식·함수 계약으로 분해할 수 있는 산술과 자료구조 | Rust 기능·외부 라이브러리 지원, ghost 제거, 신뢰 선언, 종료 보증 |
| Kani | 실제 함수를 호출하는 하네스의 비트 연산·경계·panic·정합성 속성 | symbolic 입력 범위, unwind, 계약 합성, 지원하지 않는 UB와 동시성 |
| Creusot | Rust 계약·mutable borrow를 다루며 Why3 기반 증명이 적합한 코드 | target 의존성, 표준 계약의 범위, 종료 설정, erasure 검사 |
| Aeneas | 원본에서 추출한 함수형 모델과 증명 보조기를 활용할 코드 | 지원 Rust 부분집합, 번역기·외부 모델, 생성 결과와 원본의 연결 |

이미 프로젝트에 적합한 도구와 증명이 있으면 재사용한다. 큰 정수 곱셈·나눗셈이나 긴 파서의 비트 전개가 폭발하면 연역 증명과 보조정리를 검토하되, 도구 교체가 요구·입력 범위 축소의 근거가 되지는 않는다.

## 실제 코드와 연산 의미

- target, 정수 폭과 signedness, feature·cfg, 외부 함수 모델을 기록한다. `usize`와 layout 등의 architecture 의존성을 확인한다.
- Rust의 정수 overflow 동작과 검사 설정, `checked_*`·`wrapping_*`·`saturating_*`, cast·shift·나눗셈의 의미를 수학 명세에 연결한다. 원하는 수학 성질을 얻으려고 wrap이나 panic 경로를 숨기지 않는다.
- Verus의 exec 코드와 일반 빌드, Creusot의 ghost 제거 후 코드, Aeneas의 생성 모델이 어떤 원본에 대응하는지 확인한다. Creusot의 `#[erasure(...)]` 같은 연결 검사는 해당 기능이 보장하는 범위에서 활용한다.
- 타입·borrow 검사 통과를 알고리즘 정합성 증명으로 취급하지 않는다. unsafe·FFI가 있으면 실제 증명 경계와 도구의 UB 검출 한계를 확인한다.

근거: [Rust 연산 의미](https://doc.rust-lang.org/reference/expressions/operator-expr.html#overflow), [Creusot erasure](https://guide.creusot.rs/erasure.html), [Creusot 제약](https://guide.creusot.rs/limitations.html), [Kani UB 한계](https://model-checking.github.io/kani/undefined-behaviour.html).

## Verus

- `requires`를 가정한 함수 증명과 모든 실제 호출부의 전제 성립을 구별한다. `ensures`에는 필요한 성공·실패 조건과 수학적 의미를 적는다.
- `assume`, `admit`, `external_body`, `assume_specification` 등으로 남긴 의무를 식별한다. `external`로 검증 대상에서 빠진 함수도 확인한다. 등록된 외부 계약과 자체 코드에 추가한 미증명 가정을 혼동하지 않는다.
- 검증기의 실제 대상·가정 보고를 확인한다. `--no-cheating`을 사용한다면 해당 버전에서 무엇을 거부하는지 확인하고 허용한 외부 계약과 대조한다. 거부가 없다는 사실만으로 명세의 충분함이나 전체 범위를 입증하지는 못한다.
- spec/proof 함수의 종료와 exec 종료를 구별한다. 기본 exec `decreases` 검사는 피호출자가 종료한다는 전제 아래의 검사다. 전체 종료를 주장하려면 호출 관계·신뢰 계약의 종료 보장까지 연결한다. 종료 검사 비활성 속성을 빠뜨리지 않는다.

근거: [개요와 지원 범위](https://verus-lang.github.io/verus/guide/), [외부 명세 가정](https://verus-lang.github.io/verus/guide/reference-assume-specification.html), [속성 목록](https://verus-lang.github.io/verus/guide/reference-attributes.html), [exec 종료 검사](https://verus-lang.github.io/verus/guide/exec_termination.html).

## Kani

- 하네스가 실제 대상 함수를 호출하는지, `any`·`assume`·입력 생성기가 어떤 값을 허용하는지 확인한다. `assume(false)`는 이후 경로를 제거한다. 필요한 조건의 도달성은 `cover`와 개별 검사 결과로 확인할 수 있다.
- 입력 길이 제한과 unwind 한도를 구별한다. unwind assertion을 통과해야 그 한도가 하네스의 실행을 충분히 덮었다고 볼 수 있다. 한도 검사를 꺼서 통과시키지 않는다. 짧은 입력의 완료 결과를 임의 길이로 확대하지 않는다.
- `proof_for_contract`·`stub_verified` 등의 계약 합성 기능은 지원 상태와 실제 검증된 피호출자 계약을 확인해 쓴다. 사용자 스텁으로 바꿔 같은 보증을 얻었다고 하지 않는다. generic 계약은 검증한 인스턴스도 확인한다.
- `SUCCESS` 외에 `UNREACHABLE`, `UNDETERMINED`, 미지원 경고를 읽는다. 완료·반례·미결정을 구분한다. 현재 동시성 지원 범위를 확인하며 순차 처리된 결과를 동시성 증명으로 사용하지 않는다.

근거: [하네스와 결과](https://model-checking.github.io/kani/), [검증 상태](https://model-checking.github.io/kani/verification-results.html), [공허한 가정과 cover](https://model-checking.github.io/kani/faq.html), [unwinding](https://model-checking.github.io/kani/tutorial-loop-unwinding.html), [계약 API](https://model-checking.github.io/kani/crates/doc/kani/contracts/index.html), [Rust 지원 범위](https://model-checking.github.io/kani/rust-feature-support.html).

## Creusot·Aeneas

- Creusot은 기본 부분정확성과 `#[check(terminates)]`의 종료 보증을 구별한다. `#[trusted]`는 구현과 명세를 가정하며 erasure 검사도 생략한다. 표준 계약의 미보장 panic과 target 의존성도 확인한다.
- Aeneas는 사용할 버전의 safe Rust 부분집합·루프·generic 제한과 unsafe·동시성 지원을 확인한다. 외부 모델의 수동 구현과 원본 의미의 대응은 별도 신뢰 경계다.
- Lean 등의 증명 보조기를 쓰면 최종 정리의 전이적 공리·미완성 증명 의존성을 검사한다. Lean의 `#print axioms`에서 `sorryAx`와 추가 공리를 확인하고, native evaluation이 신뢰 기반을 넓히는지도 명시한다. 커널이 정리를 받아들였다는 사실과 그 정리가 제품 요구를 뜻한다는 판단은 별개다.

근거: [Creusot 시작](https://guide.creusot.rs/), [종료](https://guide.creusot.rs/termination.html), [trusted](https://guide.creusot.rs/trusted.html), [Aeneas 지원 범위와 외부 모델](https://github.com/AeneasVerif/aeneas), [Lean 공리 의존성](https://lean-lang.org/doc/reference/latest/Axioms/).

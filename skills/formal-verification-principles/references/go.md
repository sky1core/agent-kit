# Go 증명 도구 선택과 결과 감사

Go 코드를 증명할 때 읽는다. 사용할 revision의 지원 기능·실제 옵션·입력 경로로 적용 가능성을 확인한다. 특정 도구에 맞추려고 제품의 입력·기능을 임의로 제한하지 않는다.

## 도구 선택

| 도구 | 검토할 대상 | 먼저 확인할 경계 |
|---|---|---|
| Gobra | Go 함수 계약·루프 불변식, 공유 메모리·goroutine·channel의 permission과 정합성 | 실제 `.go`와의 연결, overflow 설정, int 폭, trusted 함수, 부분정확성과 종료 |
| Goose / Perennial | 원본 Go에서 변환한 모델을 이용한 자료구조·동시성·시스템 증명 | 지원 Go 부분집합, 번역기·언어 의미·외부 환경 모델, proof assistant와 최종 정리의 의존성 |

현재 사용하는 저장소·브랜치·증명 언어를 공식 자료로 확인한다. 도구의 넓은 분야 설명이 특정 코드·architecture·외부 시스템까지 검증한다는 보장은 아니다.

근거: [Gobra](https://github.com/viperproject/gobra), [Gobra 튜토리얼](https://github.com/viperproject/gobra/blob/master/docs/tutorial.md), [Perennial](https://github.com/mit-pdos/perennial), [Goose](https://github.com/mit-pdos/perennial/tree/lean/goose).

## 실제 코드와 Go 의미

- `GOARCH`, `int`·`uint`·`uintptr`의 폭, build tag, 실제 선택 파일·메서드·의존성을 확인한다. 증명용 build와 제품 build가 달라지면 대응 관계를 확인한다.
- Go 정수 overflow는 언어에 정의된 동작이다. 수학 정수의 계산을 적용하려면 overflow가 없음을 보이거나 의도한 모듈러 의미를 명세한다. `MIN / -1`, 음수 나눗셈·나머지, shift, cast 등 실제 쓰는 연산을 확인한다.
- slice의 backing array 공유, map·pointer 상태, nil과 interface의 동적 값, 오류·panic·부수효과를 해당 계약에 반영한다. 인터페이스 호출은 실제 구현과 메서드 계약의 연결이 필요하다.
- `math/big`는 별도 제한이 없는 연산에서 인자 간 alias를 허용한다. receiver와 operand가 같은 실제 호출을 일괄 비앨리어싱 전제로 제외하지 않는다. 허용되는 alias와 공개 값·scratch의 소유권, 변경 권한을 구분해 계약과 caller를 연결한다.
- generics·reflection·unsafe·cgo·런타임 의존 동작은 해당 버전과 실제 경로로 확인한다. 미지원 경로를 정상 결과를 주는 스텁으로 바꿔 지원했다고 하지 않는다.

근거: [Go 언어 명세](https://go.dev/ref/spec), 특히 [정수 overflow](https://go.dev/ref/spec#Integer_overflow), [math/big의 receiver·alias 계약](https://pkg.go.dev/math/big#pkg-overview).

## Gobra

- 실제 `.go` 파일의 `//@`·`/*@ ... @*/` annotation을 처리하는 경로를 검토한다. `.gobra`에 별도 구현을 만들었다면 원본과의 대응이 남는다. annotation은 검증기가 소비하는 코드이므로 일반 설명 주석처럼 삭제하거나 수정하지 않는다.
- 사용할 버전의 유효 설정을 확인한다. 공식 옵션 정의에는 `--overflow`가 기본 비활성, `int`가 기본 64비트이며 `--int32`가 별도로 있다. 필요한 overflow 검사가 빠진 결과를 기계 정수 정합성 증명으로 보고하지 않는다. wrap이 계약이면 그 의미를 다룰 수 있는 방법을 선택한다.
- overflow 의무를 별도 증명으로 분리하면 같은 원본·전제·분기에 연결한다. guard뿐 아니라 성공·실패 경로의 실제 산술식과 중간 결과까지 덮어야 한다. 각 증명이 맡은 의무가 목표 입력·경로를 덮는지 확인하고, 한쪽의 미검증 영역을 다른 쪽의 성공으로 메우지 않는다.
- `trusted`나 본문 없는 함수는 계약을 가정한다. pre/postcondition을 생략하면 `true`가 될 수 있으므로, 통과한 함수에 실제로 필요한 명제가 있는지 확인한다. 자체 코드의 숨은 미증명 의무를 이런 선언으로 덮지 않는다.
- 기본 부분정확성과 `decreases`로 증명하는 종료를 구별한다. pure·ghost 함수의 종료와 일반 함수의 종료 요구도 확인한다.
- caller가 callee의 전제·permission을 넘기는지, callee의 사후조건·허용 변경 상태가 실제 구현과 일치하는지 확인한다. 가정한 외부 계약과 검증된 자체 계약을 분리한다.

근거: [전처리 구현](https://github.com/viperproject/gobra/blob/master/src/main/scala/viper/gobra/frontend/Gobrafier.scala), [옵션 정의](https://github.com/viperproject/gobra/blob/master/src/main/scala/viper/gobra/frontend/Config.scala), [계약·trusted·종료·permission](https://github.com/viperproject/gobra/blob/master/docs/tutorial.md).

## 동시성과 Goose / Perennial

- goroutine·channel·mutex가 전달하는 permission과 불변식을 확인한다. race-free는 알고리즘의 결과 정확성, deadlock freedom, starvation freedom, 결국 종료한다는 보장과 다르다. 요구된 속성에 필요한 스케줄링·공정성·환경 가정을 밝힌다.
- Go memory model의 happens-before와 동기화 계약에 연결한다. race-free 실행을 순차 일관 실행으로 해석할 수 있다는 성질만으로 전체 알고리즘의 정확성을 결론 내리지 않는다.
- Goose가 가져온 원본과 생성 모델을 연결하고, 생성 결과를 손으로 수정하지 않는다. 선택한 Perennial 버전의 Go 의미·word-size·외부 라이브러리·파일시스템·네트워크 모델을 확인한다. crash 의미론이나 예제의 존재를 실제 저장소의 복구 보증으로 확대하지 않는다.
- 최종 정리까지 보조정리·callee 증명·환경 가정이 연결되는지 확인한다. Lean 경로에서는 `#print axioms` 등으로 `sorryAx`와 추가 공리의 전이적 의존성을 감사한다. 다른 proof assistant를 사용하는 경로도 그 도구의 실제 의존성 보고로 확인한다.

근거: [Go memory model](https://go.dev/ref/mem), [Gobra 동시성 설명](https://github.com/viperproject/gobra/blob/master/docs/tutorial.md#concurrency), [Perennial의 의미와 신뢰 경계](https://github.com/mit-pdos/perennial), [Lean 공리 의존성](https://lean-lang.org/doc/reference/latest/Axioms/).

## 산술 증명에서 방법을 바꿀 때

비선형 산술 때문에 solver가 반복해서 막히면 필요한 lemma를 분리하고 불필요한 전개를 줄인다. 다른 증명기로 lemma를 증명할 수 있지만, 가져온 명제의 변수 범위·정수 의미·가정·결론이 사용하는 계약과 같은지 확인한다. SMT를 함께 쓰면 Go 식, SMT 식, Gobra 계약 사이에서 비트 폭·signedness·cast·shift·overflow 의미를 대조하고, 어느 산출물을 어떤 검증기가 검사했는지 밝힌다. 작은 실제 함수 하나에서 이 연결부터 완결한다. 수동 대응을 기계가 검사한 연결로 표시하지 않는다.

Go extended GCD의 검증 연구는 Gobra와 Lean을 함께 사용하면서 일부 비트 연산 함수를 trusted로 남긴다. 이런 결과를 참고할 때는 증명된 알고리즘과 남은 자체 구현 의무를 구분한다. 검증 실행 시간과 작성·유지 비용도 따로 판단한다.

근거: [GCD: Garbled, Corrected, Demonstrandum](https://arxiv.org/html/2606.05796v1), 특히 명세·가정·검증 노력에 관한 절.

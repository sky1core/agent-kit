---
name: chatgpt-review
description: 선택한 로컬 텍스트 파일을 Orca 브라우저의 일반 ChatGPT Chat에 보내 리뷰하고 답변을 파일로 회수할 때 사용한다. 파일 경로·질문·모델을 CLI에 전달하며 별도 모델 API나 ChatGPT Work를 사용하지 않는다.
---

# chatgpt-review

이 스킬의 `scripts/chatgpt-review`를 실행한다. 에이전트는 파일 경로와 질문을
넘기고, 파일 준비·모델 선택·전송·대기·회수는 CLI가 처리한다. 전송할 파일을
요약하거나 화면 판단·반복 클릭·폴링을 에이전트 작업으로 대체하지 않는다.

## 실행 환경과 설정

- Python 3.9 이상과 POSIX `flock`·hard link를 지원하는 파일시스템이 필요하다.
  Windows는 지원하지 않는다. macOS 외 환경은 미검증이다.
- [Orca](https://www.onorca.dev/docs/cli/overview)의 내장 브라우저에 수동 로그인한
  ChatGPT 탭과 접근 가능한 프로젝트가 필요하다. 한국어 UI를 사용한다.
  UI 변경 시 선택자 검증이 실패할 수 있다.
- `orca --help`, `orca tab list --json`으로 현재 CLI와 `browserPageId`를 확인한다.
  계정·프로젝트는 사용자가 지정한다. 임의 탭 선택이나 서비스 재시작을 하지 않는다.
- [설정 템플릿](assets/config.example.json)을 공개 저장소 밖 개인 파일에 복사하고
  네 값을 모두 채운다. `orca`는 명시한 실행 파일 경로 또는 `PATH`에서 찾을 명령명,
  `page`는 해당 탭의 `browserPageId`, `project`는 프로젝트 홈 URL,
  `project_name`은 사이드바에 보이는 정확한 이름이다. 프로젝트가 사이드바에 보여야 한다.
- 모든 호출에 `--config`를 명시한다. 탭·프로젝트 바인딩은 `models`가 해당 프로젝트에
  이동해 확인한다. 로그인 만료는 사람이 재로그인한다. 인증 데이터 추출·복제·삭제로
  해결하지 않는다.

## 호출

아래 `<skill>`은 이 스킬 디렉터리다. 설정과 출력은 공개 저장소 밖에 둔다.
출력 디렉터리는 미리 준비하고 새 출력 파일명을 사용한다.

```bash
python3 <skill>/scripts/chatgpt-review --config <private-config.json> models
python3 <skill>/scripts/chatgpt-review --config <private-config.json> --timeout 600 ask \
  --model '<models 출력의 정확한 이름>' --effort 1 \
  --question '두 파일의 버그와 수정 근거를 리뷰해줘' \
  --file ./src/example.py --file ./notes.txt --out <private-dir>/review.txt
python3 <skill>/scripts/chatgpt-review --config <private-config.json> --timeout 600 collect \
  --out <private-dir>/review.txt
```

- `--config`, `--timeout`은 subcommand 앞에 둔다. `--effort`는 모델 UI 슬라이더의
  왼쪽부터 1~5 단계이며 실제 지원 범위를 벗어나면 전송하지 않는다.
- `--file`은 반복 가능하다. UTF-8 텍스트를 JSON으로 직렬화해 줄바꿈과 특수문자를
  보존한다. NUL·바이너리·이미지·PDF 첨부는 지원하지 않는다. 기본 합산 제한은
  JSON 표현과 질문을 포함해 80,000자다.
- 지정한 내용은 ChatGPT에 전송된다. 전송할 권한이 있는 파일만 선택한다.
  CLI는 별도 LLM API를 호출하지 않지만 ChatGPT 답변 사용량은 발생한다.
- stdout은 결과 JSON이며 답변 본문은 `--out`에, 체크포인트는
  `<out>.request.json`에 저장된다. 성공 시 답변 파일을 읽어 리뷰 근거를 검토한다.

## 중단과 회수

- 같은 탭의 CLI 실행은 직렬화한다. 실행 중 사람이 탭을 조작하지 않는다.
  다른 draft·첨부·생성 중 답변이 있으면 중단한다. 재시도에서 접힌 텍스트 첨부도
  자동으로 펼치지 않는다. 사용자가 내용을 확인한 뒤 직접 펼쳐야 한다. CLI lock은 사람이나 다른 도구의
  조작을 잠그지 않는다.
- timeout 후에는 같은 설정과 `--out`으로 `collect`한다. 저장된 대화 URL로 돌아가
  Request-ID에 대응하는 완료 답변을 회수한다. busy 상태에서는 강제 이동하지 않는다.
- `prepared` 상태는 같은 질문·파일·출력으로 `ask`를 재시도할 수 있다.
  `send_attempted`는 같은 브라우저 세션에서 클릭 receipt가 없을 때만 같은 모델·성능으로
  재시도할 수 있다. CLI가 불확실하다고 판단하면 자동 재전송하지 않는다.
- receipt는 클릭 시도 기록이며 서버 접수 증명이 아니다. 재시도를 위해 체크포인트를
  삭제·초기화하거나 새 Request-ID/출력 경로로 우회하지 않는다.
- 미완료 답변이 생성 중이 아닌 상태로 30초 정지하면 한 번 다시 연다.
  `--reopen-after 0`으로 끌 수 있다. 명시적 복구는 `collect --reopen`이며 재전송하지 않는다.
- 출력 파일을 덮어쓰지 않는다. 답변·체크포인트는 복구에 필요해 자동 삭제하지 않는다.
  작업 종료 후 사용자 보관 정책에 따라 함께 정리하고 공개물에는 포함하지 않는다.

## 검증

원문 보존과 저장 충돌은 `python3 <skill>/tests/check_file_content.py`,
`python3 <skill>/tests/check_storage.py`, `python3 <skill>/tests/check_config_lock.py`로 확인한다.
이 테스트의 임시 파일은 종료 시 정리한다.

실제 전송·중단 복구 검증이 필요하면 `tests/check_dispatch_recovery.py --help`를
확인한다. 개인 설정·모델·성능·같은 프로젝트의 다른 대화 URL·기존 출력 디렉터리를
명시해야 한다. 테스트 요청 1개를 실제로 전송하며 결과는 고유 하위 폴더에 보관한다.
DOM 원문 일치·중복 요청 차단을 확인하지만 서버의 요청 수를 측정하지는 않는다.
테스트 대화와 결과는 자동 삭제하지 않는다.

import copy
import json
from pathlib import Path
import runpy
import tempfile
from unittest.mock import patch


root = Path(__file__).resolve().parent.parent
module = runpy.run_path(str(root / 'scripts' / 'chatgpt-review'), run_name='collection_verification')
Browser = module['Browser']
ReviewError = module['ReviewError']
config = {'orca': 'orca', 'page': 'fixture', 'project': 'https://chatgpt.com/g/g-p-fixture/project',
          'project_name': 'Fixture'}
url = 'https://chatgpt.com/g/g-p-fixture/c/conversation'
prompt = 'Request-ID: LOCAL-REVIEW-fixture\n\nReview this request.'
record = {'request_id': 'LOCAL-REVIEW-fixture', 'prompt_sha256': module['prompt_digest'](prompt),
          'conversation': url, 'status': 'send_attempted', 'reopen_attempts': 0}
user = {'role': 'user', 'text': prompt}
answer = {'role': 'assistant', 'text': 'answer', 'answer': 'answer', 'complete': True,
          'id': 'answer-id', 'model': 'fixture-model'}
state = {'url': url, 'work': False, 'busy': False, 'messages': [user, answer], 'response_errors': []}
observe = module['response_observation']
assert observe(state, record)['status'] == 'complete'
assert observe(dict(state, busy=True), record)['status'] == 'generating'
assert observe(dict(state, messages=[user]), record)['status'] == 'waiting'
assert observe(dict(state, messages=[]), record)['status'] == 'unknown'
assert observe(dict(state, messages=[user, answer, answer]), record)['status'] == 'unknown'
assert observe(dict(state, alerts=['unrelated global notice']), record)['status'] == 'complete'
error_state = dict(state, response_errors=[{'user_text': prompt, 'text': 'Generation failed'}])
assert observe(error_state, record)['status'] == 'explicit_error'
assert observe(dict(error_state, messages=[user]), record)['status'] == 'explicit_error'
assert observe(dict(state, response_errors=[{'user_text': 'older question', 'text': 'old error'}]), record)['status'] == 'complete'
for messages, code in [([user, user], 'duplicate_request'),
                       ([dict(user, text=prompt + ' changed')], 'request_mismatch'),
                       ([user, answer, {'role': 'user', 'text': 'Next question'}], 'conversation_changed')]:
    try:
        observe(dict(state, messages=messages), record)
    except ReviewError as exc:
        assert exc.details['code'] == code
    else:
        raise AssertionError(code)


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


with tempfile.TemporaryDirectory() as folder:
    checkpoint = Path(folder) / 'request.json'
    for messages in ([user], [user, dict(answer, answer='', complete=False)], []):
        clock = Clock()
        with patch('time.monotonic', clock.monotonic), patch('time.sleep', clock.sleep):
            browser = Browser(config, 12)
            pending = copy.deepcopy(record)
            reopens = []
            browser.state = lambda: state if reopens else dict(state, messages=messages)
            browser.reopen = lambda value: reopens.append(value['conversation'])
            result = browser.collect(pending, checkpoint, 4)
            assert result[0] == 'answer' and reopens == [url]
            assert json.loads(checkpoint.read_text())['reopen_attempts'] == 1
    clock = Clock()
    with patch('time.monotonic', clock.monotonic), patch('time.sleep', clock.sleep):
        browser = Browser(config, 8)
        pending = copy.deepcopy(record)
        browser.state = lambda: dict(state, messages=[user])
        reopens = []
        browser.reopen = lambda value: reopens.append(value['conversation'])
        try:
            browser.collect(pending, checkpoint, 2)
        except ReviewError as exc:
            assert exc.details['code'] == 'response_timeout'
        else:
            raise AssertionError('missing answer accepted')
        assert reopens == [url]
        browser.deadline = clock.now + 6
        try:
            browser.collect(pending, checkpoint, 2)
        except ReviewError:
            pass
        assert reopens == [url]
    clock = Clock()
    with patch('time.monotonic', clock.monotonic), patch('time.sleep', clock.sleep):
        browser = Browser(config, 12)
        observations = iter([state, dict(state, busy=True), state, state])
        browser.state = lambda: next(observations)
        assert browser.collect(copy.deepcopy(record), checkpoint, 0)[0] == 'answer'
        assert clock.now == 6
    browser = Browser(config, 5)
    browser.state = lambda: error_state
    try:
        browser.collect(copy.deepcopy(record), checkpoint, 0)
    except ReviewError as exc:
        assert exc.details['code'] == 'response_error' and exc.details['request_id'] == record['request_id']
    else:
        raise AssertionError('error accepted as complete')
    pending = dict(record, status='needs_manual_recovery')
    del pending['conversation']
    module['save_json'](checkpoint, pending)
    before = checkpoint.read_bytes()
    browser.open_conversation = lambda value: None
    for observed, expected in [(dict(state, messages=[]), 'request_not_found'),
                               (dict(state, messages=[dict(user, text=prompt + ' changed')]), 'request_mismatch')]:
        browser.deadline = module['time'].monotonic() + 5
        browser.state = lambda: observed
        try:
            browser.bind_conversation(pending, checkpoint, url)
        except ReviewError as exc:
            assert exc.details['code'] == expected
        else:
            raise AssertionError('invalid conversation linked')
        assert checkpoint.read_bytes() == before and 'conversation' not in pending
    browser.deadline = module['time'].monotonic() + 5
    browser.state = lambda: state
    browser.bind_conversation(pending, checkpoint, url)
    assert pending['conversation'] == url and pending['status'] == 'send_attempted'
    try:
        browser.bind_conversation(pending, checkpoint, 'https://chatgpt.com/g/g-p-other/c/conversation')
    except ReviewError as exc:
        assert exc.details['code'] == 'invalid_conversation'
    else:
        raise AssertionError('cross-project recovery allowed')
    clock = Clock()
    with patch('time.monotonic', clock.monotonic), patch('time.sleep', clock.sleep):
        browser = Browser(config, 2)
        browser.state = lambda: dict(state, messages=[])
        pending = copy.deepcopy(record)
        del pending['conversation']
        try:
            browser.remember_conversation(pending, checkpoint)
        except ReviewError as exc:
            assert exc.details['code'] == 'needs_manual_recovery'
            assert exc.details['next_action'] == 'collect_with_conversation'
        else:
            raise AssertionError('lost request was located')
        assert json.loads(checkpoint.read_text())['status'] == 'needs_manual_recovery'

browser = Browser(config, 30)
browser.validate_request_size('한글' * 1000, 'Model', 1, record['request_id'], 'scope')
for text in ['한' * 50000, '\\' * 70000]:
    try:
        browser.validate_request_size(text, 'Model', 1, record['request_id'], 'scope')
    except ReviewError as exc:
        assert exc.details['code'] == 'request_too_large'
    else:
        raise AssertionError('oversized serialized request accepted')
with patch('os.sysconf', return_value=1024):
    try:
        browser.command('eval', '--expression', '1')
    except ReviewError as exc:
        assert exc.details['code'] == 'request_too_large'
    else:
        raise AssertionError('environment and argv limit ignored')
print('답변 상태·오류 귀속·연속 완료 판정·무응답 재열기·복구 검증·직렬화 바이트 제한: 통과')

with tempfile.TemporaryDirectory() as folder:
    checkpoint = Path(folder) / 'request.json'
    busy_state = dict(state, busy=True, editor='', editor_plain='', pending_tiles=0)
    browser = Browser(config, 30)
    browser.state = lambda: busy_state
    browser.call = lambda *args: (_ for _ in ()).throw(AssertionError('unexpected navigation'))
    pending = dict(record, status='needs_manual_recovery')
    del pending['conversation']
    browser.bind_conversation(pending, checkpoint, url)
    assert pending['status'] == 'send_attempted' and pending['conversation'] == url
    before = checkpoint.read_bytes()
    browser.state = lambda: dict(busy_state, messages=[dict(user, text=prompt + ' changed')])
    try:
        browser.bind_conversation(pending, checkpoint, url)
    except ReviewError as exc:
        assert exc.details['code'] == 'request_mismatch'
    else:
        raise AssertionError('same URL wrong request accepted')
    assert checkpoint.read_bytes() == before
    browser.state = lambda: dict(busy_state, url=url + '-other')
    try:
        browser.bind_conversation(pending, checkpoint, url)
    except ReviewError:
        pass
    else:
        raise AssertionError('navigation away from busy conversation accepted')

import subprocess
import sys

with tempfile.TemporaryDirectory() as folder:
    output = Path(folder) / 'answer.txt'
    for failure_phase in ('prepare', 'dispatch'):
        checkpoint = output.with_name(output.name + '.request.json')
        if checkpoint.exists():
            checkpoint.unlink()
        initial = dict(state, url=config['project'], messages=[], editor=prompt,
                       editor_plain=prompt, pending_tiles=0)
        def prepare(browser, draft):
            if failure_phase == 'prepare':
                browser.call('eval', '--expression', '1')
        def paste(browser, text):
            initial['editor'] = initial['editor_plain'] = text
            return {'pasted': True}
        initial['editor'] = initial['editor_plain'] = ''
        argv = ['chatgpt-review', '--config', 'fixture.json', 'ask', '--model', 'Model',
                '--effort', '1', '--question', 'question', '--out', str(output)]
        with patch.object(sys, 'argv', argv), patch.dict(module['main'].__globals__, {'load_config': lambda p: config}), \
             patch.object(Browser, 'project', prepare), patch.object(Browser, 'select_model', lambda *a: {}), \
             patch.object(Browser, 'state', lambda b: initial), patch.object(Browser, 'paste_guarded', paste), \
             patch.object(Browser, 'expand_paste', lambda b: None), patch.object(Browser, 'wait', lambda *a: True), \
             patch.object(Browser, 'open_model_menu', lambda b: None), patch.object(Browser, 'model_options', lambda b: []), \
             patch.object(Browser, 'browser_scope', lambda b: 'fixture-scope'), \
             patch('subprocess.run', side_effect=subprocess.TimeoutExpired('orca', 1)):
            try:
                module['main']()
            except ReviewError as exc:
                saved = json.loads(checkpoint.read_text())
                assert exc.details['request_id'] == saved['request_id']
                assert exc.details['phase'] == failure_phase
                assert exc.details['status'] == saved['status']
                assert exc.details['code'] == 'orca_timeout'
                assert exc.details['next_action'] == ('collect' if failure_phase == 'dispatch' else 'inspect')
            else:
                raise AssertionError('timeout accepted')
        assert not output.exists()
print('동일 URL 생성 중 연결·해시 검증·다른 대화 보호·main 전송 전후 타임아웃 문맥: 통과')

with tempfile.TemporaryDirectory() as folder:
    checkpoint = Path(folder) / 'request.json'
    for old_status, new_status in [('prepared', 'send_attempted'), ('answer_ready', 'completed')]:
        browser = Browser(config, 30)
        pending = dict(record, status=old_status)
        browser.save_record(checkpoint, pending)
        pending['status'] = new_status
        with patch('os.replace', side_effect=OSError('injected replace failure')):
            try:
                with browser.error_context():
                    browser.save_record(checkpoint, pending)
            except ReviewError as exc:
                assert exc.details['status'] == old_status
                assert exc.details['status'] == json.loads(checkpoint.read_text())['status']
            else:
                raise AssertionError('save failure accepted')
    output = Path(folder) / 'answer.txt'
    checkpoint = output.with_name(output.name + '.request.json')
    pending = dict(record, status='prepared', project=config['project'], page=config['page'])
    module['save_json'](checkpoint, pending)
    argv = ['chatgpt-review', '--config', 'fixture.json', 'collect', '--out', str(output)]
    with patch.object(sys, 'argv', argv), patch.dict(module['main'].__globals__, {'load_config': lambda p: config}):
        try:
            module['main']()
        except ReviewError as exc:
            assert exc.details['status'] == 'prepared' and exc.details['next_action'] == 'inspect'
        else:
            raise AssertionError('unsubmitted request collected')
print('저장 실패 시 확정 상태 유지·미전송 collect 반복 안내 차단: 통과')

with tempfile.TemporaryDirectory() as folder:
    output = Path(folder) / 'answer.txt'
    output.write_text('answer\n')
    checkpoint = output.with_name(output.name + '.request.json')
    pending = dict(record, status='answer_ready', project=config['project'], page=config['page'],
                   answer_sha256=module['hashlib'].sha256(output.read_bytes()).hexdigest())
    module['save_json'](checkpoint, pending)
    before = checkpoint.read_bytes()
    argv = ['chatgpt-review', '--config', 'fixture.json', 'collect', '--out', str(output)]
    with patch.object(sys, 'argv', argv), patch.dict(module['main'].__globals__, {'load_config': lambda p: config}), \
         patch('os.replace', side_effect=OSError('injected replace failure')):
        try:
            module['main']()
        except ReviewError as exc:
            assert exc.details['status'] == 'answer_ready'
            assert exc.details['next_action'] == 'collect'
            assert exc.details['phase'] == 'recover'
            assert exc.details['request_id'] == pending['request_id']
        else:
            raise AssertionError('completion save failure accepted')
    assert checkpoint.read_bytes() == before and output.read_text() == 'answer\n'
print('기존 답변 완료 기록 실패 시 collect 안내·파일 보존: 통과')

with tempfile.TemporaryDirectory() as folder:
    checkpoint = Path(folder) / 'loading.json'
    for later, code in [(state, None), (dict(state, busy=True), None),
                        (dict(state, work=True), 'conversation_changed'),
                        (dict(state, url=url + '-other'), 'conversation_changed'),
                        (dict(state, messages=[dict(user, text=prompt + ' changed')]), 'request_mismatch'),
                        (dict(state, messages=[user, user]), 'duplicate_request'),
                        (dict(state, messages=[]), 'request_not_found')]:
        clock = Clock()
        with patch('time.monotonic', clock.monotonic), patch('time.sleep', clock.sleep):
            browser = Browser(config, 6)
            pending = dict(record, status='needs_manual_recovery')
            del pending['conversation']
            browser.save_record(checkpoint, pending)
            before = checkpoint.read_bytes()
            reads = []
            def loading_state():
                reads.append(1)
                return dict(state, messages=[]) if len(reads) == 1 else later
            browser.state = loading_state
            browser.call = lambda *args: (_ for _ in ()).throw(AssertionError('unexpected navigation or send'))
            try:
                browser.bind_conversation(pending, checkpoint, url)
            except ReviewError as exc:
                assert exc.details['code'] == code, exc.details
                assert checkpoint.read_bytes() == before
                assert 'conversation' not in pending
                assert clock.now == (6 if code == 'request_not_found' else 0.5)
            else:
                assert code is None and len(reads) == 2 and clock.now == 0.5
                assert pending['conversation'] == url
print('동일 URL 초기 로딩 대기·생성 중 연결·경계 위반 즉시 차단·시간 제한·실패 시 기록 보존: 통과')

with tempfile.TemporaryDirectory() as folder:
    checkpoint = Path(folder) / 'tool-error.json'
    for stdout in ('not JSON', '{"ok":false,"error":{"code":"fixture_failure"}}'):
        clock = Clock()
        with patch('time.monotonic', clock.monotonic), patch('time.sleep', clock.sleep):
            browser = Browser(config, 6)
            pending = dict(record, status='needs_manual_recovery')
            del pending['conversation']
            browser.save_record(checkpoint, pending)
            before = checkpoint.read_bytes()
            original_state = browser.state
            calls = []
            def failing_state():
                calls.append(1)
                return dict(state, messages=[]) if len(calls) == 1 else original_state()
            browser.state = failing_state
            with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout, '')):
                try:
                    browser.bind_conversation(pending, checkpoint, url)
                except ReviewError as exc:
                    assert exc.details['code'] == 'review_error'
                    assert 'Orca' in str(exc) and '제한 시간' not in str(exc)
                    assert clock.now == 0.5 and checkpoint.read_bytes() == before
                else:
                    raise AssertionError('tool failure accepted')
print('로딩 폴링 중 Orca JSON·실행 오류 보존: 통과')

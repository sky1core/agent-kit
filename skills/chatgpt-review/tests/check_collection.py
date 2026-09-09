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
        browser.state = lambda: observed
        try:
            browser.bind_conversation(pending, checkpoint, url)
        except ReviewError as exc:
            assert exc.details['code'] == expected
        else:
            raise AssertionError('invalid conversation linked')
        assert checkpoint.read_bytes() == before and 'conversation' not in pending
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

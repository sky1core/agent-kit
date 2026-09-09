import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from unittest.mock import patch


script = Path(__file__).resolve().parent.parent / 'scripts' / 'chatgpt-review'
module = runpy.run_path(str(script), run_name='error_recovery_verification')
Browser = module['Browser']
ReviewError = module['ReviewError']
config = {'orca': sys.executable, 'page': 'error-fixture',
          'project': 'https://chatgpt.com/g/g-p-fixture/project', 'project_name': 'Fixture'}
record = {'request_id': 'LOCAL-REVIEW-fixture', 'project': config['project'], 'page': config['page'],
          'model': 'Model', 'effort': 1, 'files': [], 'prompt_sha256': 'a' * 64,
          'status': 'send_attempted', 'browser_scope': 'fixture-scope', 'reopen_attempts': 0}

for data in [None, [], {'ok': True, 'result': None}, {'ok': True, 'result': []},
             {'ok': True}, {'ok': 1, 'result': {}}, {'ok': True, 'result': {}}]:
    browser = Browser(config, 10)
    browser.record = dict(record)
    browser.phase = 'dispatch'
    browser.next_action = 'collect'
    with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(data), '')):
        try:
            with browser.error_context():
                browser.evaluate('1')
        except ReviewError as exc:
            assert exc.details['code'] == 'invalid_orca_response'
            assert exc.details['request_id'] == record['request_id']
            assert exc.details['status'] == 'send_attempted'
            assert exc.details['next_action'] == 'collect'
        else:
            raise AssertionError('malformed Orca result accepted')

with tempfile.TemporaryDirectory() as folder:
    folder = Path(folder)
    config_path = folder / 'config.json'
    config_path.write_text(json.dumps(config))
    output = folder / 'answer.txt'
    checkpoint = folder / 'answer.txt.request.json'
    invalid = [None, [], 'bad', {}, dict(record, effort=True), dict(record, files=None),
               dict(record, browser_scope=None), dict(record, reopen_attempts=-1), dict(record, status='prepared')]
    for value in invalid:
        checkpoint.write_text(json.dumps(value))
        before = checkpoint.read_bytes()
        result = subprocess.run([sys.executable, str(script), '--config', str(config_path),
                                 'collect', '--out', str(output)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 1 and json.loads(result.stderr)['code'] == 'invalid_checkpoint'
        assert not output.exists() and checkpoint.read_bytes() == before

    for status in ('send_attempted', 'needs_manual_recovery'):
        pending = dict(record, status=status)
        module['save_json'](checkpoint, pending)
        before = checkpoint.read_bytes()
        browser = Browser(config, 10)
        browser.state = lambda: {'url': config['project'], 'work': False, 'messages': []}
        reply = {'ok': True, 'result': {'result': json.dumps({'same_session': True, 'receipt': None})}}
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(reply), '')):
            try:
                browser.remember_conversation(pending, checkpoint)
            except ReviewError as exc:
                assert exc.details['code'] == 'not_sent' and exc.details['next_action'] == 'ask'
            else:
                raise AssertionError('request without receipt collected')
        assert checkpoint.read_bytes() == before

    for same_session, receipt in [(False, None), (True, {'phase': 'clicked', 'project': config['project']}),
                                  (True, {'phase': 'attempted', 'project': config['project']})]:
        pending = dict(record, status='needs_manual_recovery')
        module['save_json'](checkpoint, pending)
        reply = {'ok': True, 'result': {'result': json.dumps({'same_session': same_session, 'receipt': receipt})}}
        argv = [str(script), '--config', str(config_path), 'ask', '--model', 'Model', '--effort', '1',
                '--question', 'question', '--out', str(output)]
        with patch.object(sys, 'argv', argv), patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(reply), '')):
            try:
                module['main']()
            except ReviewError as exc:
                assert exc.details['next_action'] != 'ask'
            else:
                raise AssertionError('uncertain or clicked request retried')

    for status in ('send_attempted', 'needs_manual_recovery'):
        prompt = 'Request-ID: ' + record['request_id'] + '\n\nquestion'
        pending = dict(record, status=status, prompt_sha256=module['prompt_digest'](prompt))
        del pending['reopen_attempts']
        module['save_json'](checkpoint, pending)
        argv = [str(script), '--config', str(config_path), 'ask', '--model', 'Model', '--effort', '1',
                '--question', 'question', '--out', str(output)]
        calls = []
        def reached_send(browser, text, value, path):
            assert value['reopen_attempts'] == 0
            calls.append(text)
            raise ReviewError('test stops at verified send boundary', code='test_boundary')
        with patch.object(sys, 'argv', argv), patch.object(Browser, 'dispatch_receipt', return_value=None), \
             patch.object(Browser, 'project'), patch.object(Browser, 'select_model', return_value={}), \
             patch.object(Browser, 'send', reached_send):
            try:
                module['main']()
            except ReviewError as exc:
                assert exc.details['code'] == 'test_boundary'
            else:
                raise AssertionError('test boundary not reached')
        assert calls == [prompt] and not output.exists()
print('Orca 응답·체크포인트 타입 검증, 미클릭 재시도 경로, 클릭·세션 변경 차단: 통과')

for payload in [None, [], {'same_session': 'true', 'receipt': None}, {'same_session': True},
                {'same_session': True, 'receipt': []}, {'same_session': True, 'receipt': {'phase': 'unknown'}},
                {'same_session': True, 'receipt': {'phase': 'clicked', 'project': 'other'}}]:
    browser = Browser(config, 10)
    reply = {'ok': True, 'result': {'result': json.dumps(payload)}}
    with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(reply), '')):
        try:
            browser.dispatch_receipt(record)
        except ReviewError as exc:
            assert exc.details['code'] == 'invalid_orca_response'
            assert exc.details['next_action'] == 'collect'
        else:
            raise AssertionError('invalid receipt treated as safe to retry')
print('잘못된 receipt 응답을 미클릭으로 오인하지 않음: 통과')

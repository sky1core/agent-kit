import argparse
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import time
import uuid


root = Path(__file__).resolve().parent.parent
script = root / 'scripts' / 'chatgpt-review'
module = runpy.run_path(str(script), run_name='dispatch_verification')
parser = argparse.ArgumentParser(description='실제 Orca/ChatGPT에 테스트 요청 1개를 전송하고 결과를 보관합니다.')
parser.add_argument('--config', type=Path, required=True)
parser.add_argument('--model', required=True)
parser.add_argument('--effort', type=int, choices=range(1, 6), required=True)
parser.add_argument('--other-conversation', required=True)
parser.add_argument('--output-dir', type=Path, required=True)
args = parser.parse_args()
config = module['load_config'](args.config)
folder = args.output_dir / ('dispatch-' + uuid.uuid4().hex)
folder.mkdir(mode=0o700)
(folder / 'price.py').write_bytes(b'TEST_TOKEN = "ALPHA-93"\r\ndef total(unit_price, qty):\r    return unit_price + qty\n')
(folder / 'notes.txt').write_bytes('TEST_TOKEN = "BETA-17"\r\n곱셈으로 고치세요.\r원문 escape: \\n "quote"\tend\n'.encode())
output = folder / 'answer.txt'
checkpoint = folder / 'answer.txt.request.json'
recovery = folder / 'recovery.txt'
recovery_checkpoint = folder / 'recovery.txt.request.json'
question = '두 파일의 TEST_TOKEN 값과 올바른 return 식을 한 줄로만 답하세요.'
request_id = 'LOCAL-REVIEW-' + uuid.uuid4().hex
parts = [f'Request-ID: {request_id}', question]
files = []
file_args = []
for name in ['price.py', 'notes.txt']:
    path = folder / name
    part, file_info = module['file_part'](path)
    parts.append(part)
    files.append(file_info)
    file_args.extend(['--file', str(path)])
prompt = '\n\n'.join(parts)
record = {'request_id': request_id, 'project': config['project'], 'page': config['page'],
          'status': 'prepared', 'model': args.model, 'effort': args.effort, 'files': files,
          'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(), 'reopen_attempts': 0}
module['save_json'](checkpoint, record, create=True)
browser = module['Browser'](config, 240)
with module['page_lock'](config['page']):
    browser.project()
    other_config = dict(config, project='https://chatgpt.com/g/g-p-invalid-verification/project')
    other = module['Browser'](other_config, 180)
    before = browser.state()['editor_plain']
    assert other.paste_guarded(prompt) == {'pasted': False, 'reason': 'project_or_chat_changed'}
    assert browser.state()['editor_plain'] == before
    browser.select_model(record['model'], record['effort'])
    assert browser.paste_guarded(prompt) == {'pasted': True}
    browser.expand_paste()
    assert browser.paste_guarded('replacement') == {'pasted': False, 'reason': 'input_changed'}
    assert browser.state()['editor_plain'].strip() == prompt.strip()
    browser.open_model_menu()
    browser.model_options()
    scope = browser.browser_scope()
    results = {
        'project': other.submit_guarded(prompt, args.model, args.effort, request_id, scope),
        'input': browser.submit_guarded(prompt + '\nchanged', args.model, args.effort, request_id, scope),
        'model': browser.submit_guarded(prompt, args.model + '-invalid', args.effort, request_id, scope),
        'effort': browser.submit_guarded(prompt, args.model, args.effort + 1, request_id, scope),
        'session': browser.submit_guarded(prompt, args.model, args.effort, request_id, 'different-session'),
    }
    for key, reason in [('project', 'project_or_chat_changed'), ('input', 'input_changed'),
                        ('model', 'model_changed'), ('effort', 'effort_changed')]:
        assert results[key] == {'sent': False, 'reason': reason}, results
    assert results['session'] == {'sent': False, 'reason': 'session_changed', 'uncertain': True}
    browser.close_menu()
    browser.stage_dispatch(record, checkpoint)
    assert browser.dispatch_receipt(record) is None
    staged_bytes = checkpoint.read_bytes()
    try:
        browser.stage_dispatch(dict(record, browser_scope='different-session'), checkpoint)
    except module['ReviewError']:
        results['changed_session_stage_blocked'] = True
    else:
        raise AssertionError('changed browser session was restaged')
    assert checkpoint.read_bytes() == staged_bytes
    try:
        browser.dispatch_receipt(dict(record, browser_scope='different-session'))
    except module['ReviewError']:
        results['changed_session_retry_blocked'] = True
    else:
        raise AssertionError('changed browser session was accepted')
print(json.dumps({'folder': str(folder), 'guards': results, 'preclick_interruption': True}), flush=True)
base = [str(script), '--config', str(args.config), '--timeout', '300', 'ask', '--model', args.model, '--effort', str(args.effort),
        '--question', question, *file_args]
process = subprocess.Popen([*base, '--out', str(output)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
captured = False
while process.poll() is None:
    record_bytes = checkpoint.read_bytes()
    pending = json.loads(record_bytes)
    if not captured and pending['status'] == 'send_attempted' and 'conversation' in pending:
        recovery_checkpoint.write_bytes(record_bytes)
        captured = True
        print('전송 직후 체크포인트 보관', flush=True)
    time.sleep(0.2)
stdout, stderr = process.communicate()
print(json.dumps({'ask_exit': process.returncode, 'stdout': stdout, 'stderr': stderr}, ensure_ascii=False), flush=True)
assert captured
assert process.returncode == 0, stderr
pending = json.loads(recovery_checkpoint.read_text())
browser = module['Browser'](config, 180)
with module['page_lock'](config['page']):
    assert browser.dispatch_receipt(pending)['phase'] == 'clicked'
    duplicate = browser.submit_guarded(prompt, args.model, args.effort, request_id, scope)
    assert duplicate == {'sent': False, 'reason': 'already_attempted', 'uncertain': True}
result = subprocess.run([*base, '--out', str(recovery)], capture_output=True, text=True, timeout=45)
assert result.returncode == 1 and 'collect' in result.stderr, result.stderr
other_url = args.other_conversation
with module['page_lock'](config['page']):
    browser.open_conversation(other_url)
    assert browser.state()['url'] == other_url and other_url != pending['conversation']
result = subprocess.run([str(script), '--config', str(args.config), '--timeout', '150', 'collect', '--out', str(recovery)],
                        capture_output=True, text=True, timeout=160)
assert result.returncode == 0, result.stderr
recovered = json.loads(recovery_checkpoint.read_text())
assert recovered['conversation'] == pending['conversation']
assert recovered['request_id'] == request_id
assert recovered['reopen_attempts'] == 0, recovered['reopen_attempts']
assert 'ALPHA-93' in recovery.read_text() and 'BETA-17' in recovery.read_text()
assert 'unit_price * qty' in recovery.read_text()
with module['page_lock'](config['page']):
    state = browser.state()
    users = [message for message in state['messages']
             if message['role'] == 'user' and request_id in message['text']]
    assert len(users) == 1
    assert users[0]['text'].strip() == prompt.strip()
report = {'guards': results, 'preclick_interruption_retry': True, 'duplicate_submit': duplicate,
          'duplicate_ask_blocked': True, 'recovered_from_other_conversation': True,
          'navigation_reopen_attempts': recovered['reopen_attempts'], 'two_files_preserved': True,
          'matching_user_messages': len(users), 'submitted_prompt_exact_match': True, 'response_model': recovered['response_model'],
          'source_sha256': hashlib.sha256(script.read_bytes()).hexdigest()}
(folder / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False), flush=True)

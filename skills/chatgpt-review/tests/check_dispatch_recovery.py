import argparse
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
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
question = '두 파일의 TEST_TOKEN 값과 올바른 return 식을 한 줄로만 답하세요.'
request_id = 'LOCAL-REVIEW-' + uuid.uuid4().hex
parts = [f'Request-ID: {request_id}', question]
files = []
file_args = []
for name in ['price.py', 'notes.txt']:
    path = folder / name
    part, file_info = module['file_part'](path, folder)
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
        '--question', question, '--base-dir', str(folder), *file_args]
crash_runner = """import os,runpy,sys
script=sys.argv[1]
sys.argv=sys.argv[1:]
module=runpy.run_path(script,run_name='interrupted_dispatch')
def interrupt_before_url(*args):
    os._exit(86)
module['Browser'].remember_conversation=interrupt_before_url
module['main']()
"""
process = subprocess.run([sys.executable, '-c', crash_runner, *base, '--out', str(output)],
                         capture_output=True, text=True, timeout=320)
assert process.returncode == 86, process.stderr
pending = json.loads(checkpoint.read_text())
assert pending['status'] == 'send_attempted' and 'conversation' not in pending
assert not output.exists()
browser = module['Browser'](config, 180)
with module['page_lock'](config['page']):
    assert browser.dispatch_receipt(pending)['phase'] == 'clicked'
    duplicate = browser.submit_guarded(prompt, args.model, args.effort, request_id, scope)
    assert duplicate == {'sent': False, 'reason': 'already_attempted', 'uncertain': True}
    while time.monotonic() < browser.deadline:
        state = browser.state()
        if module['response_observation'](state, pending)['status'] == 'complete':
            break
        time.sleep(2)
    else:
        raise AssertionError('test response is still generating; conversation retained')
    actual_url = state['url']
    browser.validate_conversation_url(actual_url)
result = subprocess.run([*base, '--out', str(output)], capture_output=True, text=True, timeout=45)
assert result.returncode == 1 and 'collect' in result.stderr, result.stderr
with module['page_lock'](config['page']):
    browser.open_conversation(args.other_conversation)
    assert browser.state()['url'] == args.other_conversation and args.other_conversation != actual_url
collect = [str(script), '--config', str(args.config), '--timeout', '150', 'collect', '--out', str(output)]
missing = [str(script), '--config', str(args.config), '--timeout', '4', 'collect', '--out', str(output)]
result = subprocess.run(missing, capture_output=True, text=True, timeout=15)
assert result.returncode == 1 and json.loads(result.stderr)['code'] == 'needs_manual_recovery', result.stderr
assert json.loads(checkpoint.read_text())['status'] == 'needs_manual_recovery'
result = subprocess.run([*collect, '--conversation', actual_url], capture_output=True, text=True, timeout=160)
assert result.returncode == 0, result.stderr
recovered = json.loads(checkpoint.read_text())
assert recovered['conversation'] == actual_url and recovered['request_id'] == request_id
assert recovered['reopen_attempts'] == 0, recovered['reopen_attempts']
assert 'ALPHA-93' in output.read_text() and 'BETA-17' in output.read_text()
assert 'unit_price * qty' in output.read_text()
with module['page_lock'](config['page']):
    state = browser.state()
    users = [message for message in state['messages']
             if message['role'] == 'user' and request_id in message['text']]
    assert len(users) == 1 and users[0]['text'].strip() == prompt.strip()
report = {'guards': results, 'preclick_interruption_retry': True, 'duplicate_submit': duplicate,
          'process_exit_before_url_recorded': process.returncode,
          'manual_recovery_required': True, 'duplicate_ask_blocked': True,
          'recovered_from_other_conversation': True, 'request_hash_verified': True,
          'navigation_reopen_attempts': recovered['reopen_attempts'], 'two_files_preserved': True,
          'matching_user_messages': len(users), 'submitted_prompt_exact_match': True,
          'response_model': recovered['response_model'],
          'source_sha256': hashlib.sha256(script.read_bytes()).hexdigest()}
(folder / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False), flush=True)

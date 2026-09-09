import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import uuid


script = Path(__file__).resolve().parent.parent / 'scripts' / 'chatgpt-review'
module = runpy.run_path(str(script), run_name='config_lock_verification')
with tempfile.TemporaryDirectory() as folder:
    config_path = Path(folder) / 'config.json'
    config = {'orca': sys.executable, 'page': 'page-test',
              'project': 'https://chatgpt.com/g/g-p-test/project', 'project_name': '테스트'}
    config_path.write_text(json.dumps(config))
    assert module['load_config'](config_path)['page'] == 'page-test'
    invalid = [[], None, {}, dict(config, unknown='value'),
               dict(config, project='https://example.com/project'),
               dict(config, orca='nonexistent-' + uuid.uuid4().hex)]
    for key in config:
        invalid.extend(dict(config, **{key: value}) for value in [None, 1, [], '', ' '])
    for value in invalid:
        config_path.write_text(json.dumps(value))
        try:
            module['load_config'](config_path)
        except module['ReviewError']:
            pass
        else:
            raise AssertionError(f'invalid configuration accepted: {value}')
    missing = subprocess.run([sys.executable, str(script), 'models'], capture_output=True, text=True)
    assert missing.returncode == 2 and '--config' in missing.stderr
    page = 'test-' + uuid.uuid4().hex
    child = """import runpy,sys,tempfile
tempfile.tempdir=sys.argv[3]
m=runpy.run_path(sys.argv[1],run_name='lock_child')
try:
    with m['page_lock'](sys.argv[2]):
        print('acquired')
except m['ReviewError']:
    print('blocked')
"""
    with module['page_lock'](page):
        result = subprocess.run([sys.executable, '-c', child, str(script), page, folder],
                                capture_output=True, text=True, check=True)
        assert result.stdout.strip() == 'blocked'
    result = subprocess.run([sys.executable, '-c', child, str(script), page, folder],
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == 'acquired'
    digest = hashlib.sha256(page.encode()).hexdigest()[:24]
    (Path('/tmp') / f'chatgpt-review-{os.getuid()}-{digest}.lock').unlink()
print('명시적 설정·잘못된 값 차단·프로세스 간 탭 lock·해제 후 재획득: 통과')

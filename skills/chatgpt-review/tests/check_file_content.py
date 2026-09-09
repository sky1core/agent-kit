import hashlib
import json
from pathlib import Path
import runpy
import tempfile


root = Path(__file__).resolve().parent.parent
module = runpy.run_path(str(root / 'scripts' / 'chatgpt-review'), run_name='file_content_verification')
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / 'line-endings.txt'
    raw = 'CRLF\r\nCR\rLF\n한글\\n"quote"\\path\tend'.encode()
    path.write_bytes(raw)
    part, info = module['file_part'](path)
    payload = json.loads(part.split('\n', 1)[1])
    assert payload['content'].encode() == raw
    assert info['sha256'] == hashlib.sha256(raw).hexdigest()
    assert '\r' not in part
    path.write_bytes(b'binary\x00data')
    try:
        module['file_part'](path)
    except module['ReviewError']:
        pass
    else:
        raise AssertionError('NUL content accepted')
print('원본 줄바꿈·한글·escape·탭 보존, 원본 바이트 해시, NUL 차단: 통과')

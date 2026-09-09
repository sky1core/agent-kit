import hashlib
import json
from pathlib import Path
import runpy
import shutil
import tempfile


root = Path(__file__).resolve().parent.parent
module = runpy.run_path(str(root / 'scripts' / 'chatgpt-review'), run_name='file_content_verification')
with tempfile.TemporaryDirectory() as folder:
    base = Path(folder)
    path = base / 'line-endings.txt'
    raw = 'CRLF\r\nCR\rLF\n한글\\n"quote"\\path\tend'.encode()
    path.write_bytes(raw)
    part, info = module['file_part'](path, base)
    payload = json.loads(part.split('\n', 1)[1])
    assert payload['content'].encode() == raw
    assert payload['file'] == 'line-endings.txt'
    assert info['name'] == 'line-endings.txt'
    assert info['sha256'] == hashlib.sha256(raw).hexdigest()
    assert '\r' not in part
    path.write_bytes(b'binary\x00data')
    try:
        module['file_part'](path, base)
    except module['ReviewError']:
        pass
    else:
        raise AssertionError('NUL content accepted')
print('원본 줄바꿈·한글·escape·탭 보존, 원본 바이트 해시, NUL 차단: 통과')

with tempfile.TemporaryDirectory() as folder:
    base = Path(folder)
    sub = base / 'pkg'
    sub.mkdir()
    a = sub / 'a.py'
    a.write_bytes(b'print(1)\n')
    b = base / 'b.txt'
    b.write_bytes(b'text\n')

    part, info = module['file_part'](a, base)
    assert info['name'] == 'pkg/a.py'
    assert json.loads(part.split('\n', 1)[1])['file'] == 'pkg/a.py'

    parts, infos = module['prepare_files']([a, b], base)
    assert [i['name'] for i in infos] == ['pkg/a.py', 'b.txt']
    assert len(parts) == 2

    try:
        module['prepare_files']([a, a], base)
    except module['ReviewError']:
        pass
    else:
        raise AssertionError('duplicate file accepted')

    alias = sub / '..' / 'pkg' / 'a.py'
    try:
        module['prepare_files']([a, alias], base)
    except module['ReviewError']:
        pass
    else:
        raise AssertionError('same-file alias accepted')

    outside_dir = Path(tempfile.mkdtemp())
    try:
        secret = outside_dir / 'secret.txt'
        secret.write_bytes(b'secret\n')
        link = base / 'link.txt'
        link.symlink_to(secret)
        try:
            module['file_part'](link, base)
        except module['ReviewError']:
            pass
        else:
            raise AssertionError('symlink escape accepted')
        try:
            module['file_part'](secret, base)
        except module['ReviewError']:
            pass
        else:
            raise AssertionError('outside-base file accepted')
    finally:
        shutil.rmtree(outside_dir)

    weird = base / 'we\\ird.txt'
    weird.write_bytes(b'x\n')
    try:
        module['file_part'](weird, base)
    except module['ReviewError']:
        pass
    else:
        raise AssertionError('backslash filename accepted')

    nl = base / 'line\nname.txt'
    nl.write_bytes(b'x\n')
    try:
        module['file_part'](nl, base)
    except module['ReviewError']:
        pass
    else:
        raise AssertionError('control-char filename accepted')
print('상대 POSIX 라벨, base 밖/symlink escape/역슬래시·제어문자/중복·alias 차단, prepare_files 다중 파일: 통과')

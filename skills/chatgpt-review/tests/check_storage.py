from concurrent.futures import ThreadPoolExecutor
import contextlib
import json
from pathlib import Path
import runpy
import tempfile


root = Path(__file__).resolve().parent.parent
module = runpy.run_path(str(root / 'scripts' / 'chatgpt-review'), run_name='storage_verification')
save_json = module['save_json']
save_answer = module['save_answer']
real_os = module['os']


@contextlib.contextmanager
def fault(attr):
    original = getattr(real_os, attr)

    def boom(*args, **kwargs):
        raise OSError('injected ' + attr)

    setattr(real_os, attr, boom)
    try:
        yield
    finally:
        setattr(real_os, attr, original)


with tempfile.TemporaryDirectory() as folder:
    folder = Path(folder)
    record = folder / 'record.json'

    def create(value):
        try:
            save_json(record, {'value': value}, create=True)
            return 'created'
        except FileExistsError:
            return 'exists'

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, [1, 2]))
    assert sorted(results) == ['created', 'exists']
    assert json.loads(record.read_text())['value'] in [1, 2]
    save_json(record, {'value': 3})
    assert json.loads(record.read_text()) == {'value': 3}
    answer = folder / 'answer.txt'
    save_answer(answer, 'original\n')
    try:
        save_answer(answer, 'replacement\n')
    except FileExistsError:
        pass
    else:
        raise AssertionError('output was overwritten')
    assert answer.read_text() == 'original\n'
    assert not list(folder.glob('*.pending'))
print('체크포인트 동시 생성·교체, 답변 덮어쓰기 차단, 임시 파일 정리: 통과')


def assert_raises(exc_type, action, message):
    try:
        action()
    except exc_type:
        return
    raise AssertionError(message)


with tempfile.TemporaryDirectory() as folder:
    folder = Path(folder)

    target = folder / 'write.json'
    assert_raises(TypeError, lambda: save_json(target, {'v': object()}, create=True), 'save_json write not raised')
    assert not target.exists()
    ans = folder / 'write.txt'
    assert_raises(TypeError, lambda: save_answer(ans, 123), 'save_answer write not raised')
    assert not ans.exists()

    target = folder / 'fsync.json'
    with fault('fsync'):
        assert_raises(OSError, lambda: save_json(target, {'v': 1}, create=True), 'save_json fsync not raised')
    assert not target.exists()
    ans = folder / 'fsync.txt'
    with fault('fsync'):
        assert_raises(OSError, lambda: save_answer(ans, 'x\n'), 'save_answer fsync not raised')
    assert not ans.exists()

    target = folder / 'link.json'
    with fault('link'):
        assert_raises(OSError, lambda: save_json(target, {'v': 1}, create=True), 'save_json link not raised')
    assert not target.exists()
    ans = folder / 'link.txt'
    with fault('link'):
        assert_raises(OSError, lambda: save_answer(ans, 'x\n'), 'save_answer link not raised')
    assert not ans.exists()

    keep = folder / 'replace.json'
    save_json(keep, {'v': 'old'}, create=True)
    with fault('replace'):
        assert_raises(OSError, lambda: save_json(keep, {'v': 'new'}), 'save_json replace not raised')
    assert json.loads(keep.read_text()) == {'v': 'old'}

    assert not list(folder.glob('*.pending'))
print('write·fsync·link·replace 실패 주입: 임시 파일 정리, 기존 파일 보존, 미생성 확인: 통과')

import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / 'scripts' / 'chatgpt-review'
module = runpy.run_path(str(CLI), run_name='file_safety_verification')
ReviewError = module['ReviewError']
file_part = module['file_part']
prepare_files = module['prepare_files']


def new_container():
    container = Path(tempfile.mkdtemp())
    base = container / 'base'
    base.mkdir()
    return container, base


def make_sparse(path, size):
    with open(path, 'wb') as stream:
        stream.truncate(size)


def swap_open(cfg):
    real = os.open

    def fake(path, flags, *args, **kwargs):
        dir_fd = kwargs.get('dir_fd')
        if (not cfg['done'] and path == cfg['name']
                and bool(flags & os.O_DIRECTORY) == cfg['dir'] and dir_fd is not None):
            cfg['done'] = True
            os.rename(cfg['name'], cfg['name'] + '.moved', src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.symlink(cfg['external'], cfg['name'], dir_fd=dir_fd)
        return real(path, flags, *args, **kwargs)

    return real, fake


def run_with_swap(cfg, call):
    real, fake = swap_open(cfg)
    os.open = fake
    try:
        return call()
    finally:
        os.open = real


def expect_review_error(call, message):
    try:
        call()
    except ReviewError as exc:
        return exc
    raise AssertionError(message)


def test_final_file_swap():
    container, base = new_container()
    outside = Path(tempfile.mkdtemp())
    try:
        secret = outside / 'secret'
        secret.write_bytes(b'secret payload\n')
        target = base / 'target.txt'
        target.write_bytes(b'safe content\n')
        cfg = {'name': 'target.txt', 'dir': False, 'external': str(secret), 'done': False}
        expect_review_error(
            lambda: run_with_swap(cfg, lambda: file_part(target, base)),
            '최종 파일 open 직전 외부 symlink 교체 허용됨')
        assert cfg['done']
    finally:
        shutil.rmtree(container)
        shutil.rmtree(outside)
    print('최종 파일 open 직전 외부 symlink 교체 차단: 통과')


def test_intermediate_dir_swap():
    container, base = new_container()
    outside = Path(tempfile.mkdtemp())
    try:
        (outside / 'target.txt').write_bytes(b'secret payload\n')
        sub = base / 'sub'
        sub.mkdir()
        target = sub / 'target.txt'
        target.write_bytes(b'safe content\n')
        cfg = {'name': 'sub', 'dir': True, 'external': str(outside), 'done': False}
        expect_review_error(
            lambda: run_with_swap(cfg, lambda: file_part(target, base)),
            '중간 디렉터리 open 직전 외부 symlink 교체 허용됨')
        assert cfg['done']
    finally:
        shutil.rmtree(container)
        shutil.rmtree(outside)
    print('중간 디렉터리 open 직전 외부 symlink 교체 차단: 통과')


def test_base_dir_swap():
    container, base = new_container()
    outside = Path(tempfile.mkdtemp())
    try:
        (outside / 'target.txt').write_bytes(b'secret payload\n')
        target = base / 'target.txt'
        target.write_bytes(b'safe content\n')
        cfg = {'name': 'base', 'dir': True, 'external': str(outside), 'done': False}
        expect_review_error(
            lambda: run_with_swap(cfg, lambda: file_part(target, base)),
            '기준 디렉터리 open 직전 교체 허용됨')
        assert cfg['done']
    finally:
        shutil.rmtree(container)
        shutil.rmtree(outside)
    print('기준 디렉터리 open 직전 교체 차단: 통과')


def test_internal_symlink_allowed():
    container, base = new_container()
    try:
        real = base / 'real.txt'
        real.write_bytes(b'hello\n')
        link = base / 'link.txt'
        link.symlink_to('real.txt')
        part, info = file_part(link, base)
        assert info['name'] == 'real.txt'
        assert json.loads(part.split('\n', 1)[1])['content'] == 'hello\n'
    finally:
        shutil.rmtree(container)
    print('정상 내부 symlink 허용: 통과')


FIFO_PROG = (
    'import runpy, sys\n'
    'from pathlib import Path\n'
    'm = runpy.run_path(sys.argv[1], run_name="fifo_probe")\n'
    'try:\n'
    '    m["file_part"](Path(sys.argv[3]), Path(sys.argv[2]))\n'
    'except m["ReviewError"]:\n'
    '    sys.exit(0)\n'
    'except BaseException:\n'
    '    sys.exit(2)\n'
    'sys.exit(1)\n'
)


def test_fifo_rejected():
    container, base = new_container()
    try:
        fifo = base / 'pipe'
        os.mkfifo(fifo)
        try:
            proc = subprocess.run(
                [sys.executable, '-c', FIFO_PROG, str(CLI), str(base), str(fifo)],
                timeout=15, capture_output=True)
        except subprocess.TimeoutExpired:
            raise AssertionError('FIFO open 이 블로킹됨 (O_NONBLOCK 누락)')
        assert proc.returncode == 0, f'FIFO 즉시 거절 실패 rc={proc.returncode}'
    finally:
        shutil.rmtree(container)
    print('FIFO 즉시 거절 (블로킹 없음): 통과')


def test_oversized_regular_file():
    container, base = new_container()
    try:
        big = base / 'big.txt'
        make_sparse(big, 200000)
        error = expect_review_error(
            lambda: file_part(big, base, max_bytes=1000),
            'oversized 정규 파일 허용됨')
        assert error.details['code'] == 'request_too_large'
    finally:
        shutil.rmtree(container)
    print('oversized sparse 정규 파일 거절: 통과')


def test_total_budget():
    container, base = new_container()
    try:
        one = base / 'one.txt'
        two = base / 'two.txt'
        one.write_bytes(b'a' * 70000)
        two.write_bytes(b'b' * 70000)
        expect_review_error(
            lambda: prepare_files([one, two], base, max_bytes=120000),
            '총 읽기 예산 초과 허용됨')

        small_one = base / 'small_one.txt'
        small_two = base / 'small_two.txt'
        small_one.write_bytes(b'a' * 40000)
        small_two.write_bytes(b'b' * 40000)
        parts, infos = prepare_files([small_one, small_two], base, max_bytes=120000)
        assert len(parts) == 2
        assert [i['name'] for i in infos] == ['small_one.txt', 'small_two.txt']
    finally:
        shutil.rmtree(container)
    print('여러 파일 총 읽기 예산 초과 거절, 예산 내 통과: 통과')


def test_expired_deadline():
    container, base = new_container()
    try:
        target = base / 'target.txt'
        target.write_bytes(b'content\n')
        expect_review_error(
            lambda: file_part(target, base, deadline=time.monotonic() - 1),
            '만료된 deadline 허용됨')
        expect_review_error(
            lambda: prepare_files([target], base, deadline=time.monotonic() - 1),
            'prepare_files 만료 deadline 허용됨')
    finally:
        shutil.rmtree(container)
    print('만료 deadline 거절: 통과')


def test_no_fd_leak_on_failure():
    fd_dir = '/dev/fd'
    if not os.path.isdir(fd_dir):
        print('fd 누수 점검 생략 (/dev/fd 없음)')
        return
    container, base = new_container()
    try:
        big = base / 'big.txt'
        make_sparse(big, 200000)

        def probe():
            expect_review_error(
                lambda: file_part(big, base, max_bytes=1000),
                'oversized 정규 파일 허용됨')

        for _ in range(5):
            probe()
        baseline = len(os.listdir(fd_dir))
        for _ in range(200):
            probe()
        after = len(os.listdir(fd_dir))
        assert after - baseline <= 5, f'실패 경로 fd 누수 baseline={baseline} after={after}'
    finally:
        shutil.rmtree(container)
    print('실패 경로 fd 누수 없음: 통과')


test_final_file_swap()
test_intermediate_dir_swap()
test_base_dir_swap()
test_internal_symlink_allowed()
test_fifo_rejected()
test_oversized_regular_file()
test_total_budget()
test_expired_deadline()
test_no_fd_leak_on_failure()
print('파일 안전(symlink 교체·기준 디렉터리 교체·FIFO·oversized·총 예산·deadline·fd 누수): 통과')

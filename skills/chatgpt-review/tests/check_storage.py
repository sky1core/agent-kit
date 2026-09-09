from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import runpy
import tempfile


root = Path(__file__).resolve().parent.parent
module = runpy.run_path(str(root / 'scripts' / 'chatgpt-review'), run_name='storage_verification')
with tempfile.TemporaryDirectory() as folder:
    folder = Path(folder)
    record = folder / 'record.json'

    def create(value):
        try:
            module['save_json'](record, {'value': value}, create=True)
            return 'created'
        except FileExistsError:
            return 'exists'

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, [1, 2]))
    assert sorted(results) == ['created', 'exists']
    assert json.loads(record.read_text())['value'] in [1, 2]
    module['save_json'](record, {'value': 3})
    assert json.loads(record.read_text()) == {'value': 3}
    answer = folder / 'answer.txt'
    module['save_answer'](answer, 'original\n')
    try:
        module['save_answer'](answer, 'replacement\n')
    except FileExistsError:
        pass
    else:
        raise AssertionError('output was overwritten')
    assert answer.read_text() == 'original\n'
    assert not list(folder.glob('*.pending'))
print('체크포인트 동시 생성·교체, 답변 덮어쓰기 차단, 임시 파일 정리: 통과')

"""Index drift checks use temporary sources and never start the app or CLI."""
from copy import deepcopy
import json

import pytest

from scripts.check_architecture_freshness import check, fingerprint, load_index, source_hashes


@pytest.fixture
def indexed(tmp_path):
    (tmp_path / 'sample.py').write_text('def main():\n    pass\n', encoding='utf-8')
    files = source_hashes(tmp_path, ['*.py'])
    document = {
        'nodes': [{'id': 'sample', 'paths': ['sample.py'],
                   'evidence': [{'path': 'sample.py', 'symbol': 'main'}]}],
        'edges': [], 'flows': [], 'task_routes': [],
        'entrypoints': [{'path': 'sample.py'}],
        'freshness': {'patterns': ['*.py'], 'files': files, 'sha256': fingerprint(files)},
    }
    return tmp_path, document


def test_normalizes_bom_and_windows_newlines(indexed):
    root, document = indexed
    (root / 'sample.py').write_bytes(b'\xef\xbb\xbfdef main():\r\n    pass\r\n')
    assert check(document, root)['status'] == 'FRESH'


@pytest.mark.parametrize('operation', ['changed', 'added', 'removed'])
def test_detects_source_drift(indexed, operation):
    root, document = indexed
    if operation == 'changed':
        (root / 'sample.py').write_text('def main():\n    return 1\n', encoding='utf-8')
    elif operation == 'added':
        (root / 'new.py').write_text('', encoding='utf-8')
    else:
        (root / 'sample.py').unlink()
    result = check(document, root)
    assert result['status'] == 'STALE'
    assert result[operation] == ['new.py' if operation == 'added' else 'sample.py']


def test_rejects_broken_graph_and_symbol(indexed):
    root, original = indexed
    document = deepcopy(original)
    document['edges'] = [{'from': 'sample', 'to': 'missing'}]
    document['nodes'][0]['evidence'][0]['symbol'] = 'missing'
    result = check(document, root)
    assert result['status'] == 'STALE'
    assert len(result['errors']) == 2


def test_rejects_inconsistent_recorded_hash(indexed):
    root, document = indexed
    document['freshness']['sha256'] = '0' * 64
    assert 'recorded fingerprint is inconsistent' in check(document, root)['errors']


def test_loads_index_and_detail_with_shared_node_references(indexed):
    root, document = indexed
    (root / 'docs').mkdir()
    module = document['nodes'][0]
    index = {'schema_version': 2, 'detail': 'docs/detail.json',
             'modules': {'sample': {'paths': module['paths']}},
             'routes': {'edit': ['sample']}, 'entrypoints': {'sample.py': 'start'}}
    detail = {'schema_version': 2, 'evidence': {'sample': module['evidence']},
              'edges': [], 'flows': [], 'freshness': document['freshness']}
    (root / 'docs/architecture-agent-index.json').write_text(json.dumps(index), encoding='utf-8')
    (root / 'docs/detail.json').write_text(json.dumps(detail), encoding='utf-8')
    assert check(load_index(root), root)['status'] == 'FRESH'
    detail['evidence']['sample'][0]['symbol'] = 'missing'
    (root / 'docs/detail.json').write_text(json.dumps(detail), encoding='utf-8')
    assert check(load_index(root), root)['status'] == 'STALE'

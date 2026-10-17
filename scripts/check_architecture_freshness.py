"""Check the architecture index without starting the app, CLI, or reading data/."""
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
INDEX = Path('docs/architecture-agent-index.json')


def load_index(root):
    index = json.loads((root / INDEX).read_text(encoding='utf-8'))
    if index['schema_version'] != 2:
        raise ValueError('unsupported architecture index version')
    detail = json.loads((root / index['detail']).read_text(encoding='utf-8'))
    if detail['schema_version'] != 2:
        raise ValueError('unsupported architecture detail version')
    return {
        'nodes': [{'id': name, **module, 'evidence': detail['evidence'][name]}
                  for name, module in index['modules'].items()],
        'task_routes': [{'nodes': nodes} for nodes in index['routes'].values()],
        'entrypoints': [{'path': path} for path in index['entrypoints']],
        'edges': detail['edges'], 'flows': detail['flows'], 'freshness': detail['freshness'],
    }


def source_hashes(root, patterns):
    paths = sorted({p.relative_to(root).as_posix()
                    for pattern in patterns for p in root.glob(pattern) if p.is_file()})
    return {name: hashlib.sha256((root / name).read_text(encoding='utf-8-sig')
                                .replace('\r\n', '\n').replace('\r', '\n')
                                .encode('utf-8')).hexdigest() for name in paths}


def fingerprint(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':'))
                          .encode('utf-8')).hexdigest()


def check(document, root):
    errors = []
    nodes = document['nodes']
    ids = [node['id'] for node in nodes]
    if len(ids) != len(set(ids)):
        errors.append('duplicate node IDs')
    known = set(ids)
    for node in nodes:
        for name in node['paths'] + node.get('tests', []):
            if not (root / name).is_file():
                errors.append('missing reference: ' + name)
        for evidence in node.get('evidence', []):
            path = root / evidence['path']
            if not path.is_file():
                errors.append('missing evidence: ' + evidence['path'])
                continue
            source = path.read_text(encoding='utf-8-sig')
            symbol = evidence['symbol']
            if path.suffix == '.py':
                symbols = {n.name for n in ast.walk(ast.parse(source))
                           if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
                found = symbol in symbols
            else:
                found = re.search(r'\b' + re.escape(symbol) + r'\b', source) is not None
            if not found:
                errors.append('missing symbol: ' + evidence['path'] + ':' + symbol)
    for edge in document['edges']:
        if edge['from'] not in known or edge['to'] not in known:
            errors.append('unknown edge node: ' + str(edge))
    for flow in document['flows']:
        for step in flow['steps']:
            if step['node'] not in known:
                errors.append('unknown flow node: ' + step['node'])
    for route in document['task_routes']:
        for node in route['nodes']:
            if node not in known:
                errors.append('unknown task route node: ' + node)
    for entry in document['entrypoints']:
        if not (root / entry['path']).is_file():
            errors.append('missing entrypoint: ' + entry['path'])
    freshness = document['freshness']
    recorded = freshness['files']
    current = source_hashes(root, freshness['patterns'])
    added = sorted(current.keys() - recorded.keys())
    removed = sorted(recorded.keys() - current.keys())
    changed = sorted(p for p in current.keys() & recorded.keys() if current[p] != recorded[p])
    if fingerprint(recorded) != freshness['sha256']:
        errors.append('recorded fingerprint is inconsistent')
    fresh = not (errors or added or removed or changed)
    return {'status': 'FRESH' if fresh else 'STALE', 'files': len(current),
            'nodes': len(nodes), 'edges': len(document['edges']), 'flows': len(document['flows']),
            'added': added, 'removed': removed, 'changed': changed, 'errors': errors,
            'fingerprint': fingerprint(current)}


def main():
    try:
        result = check(load_index(ROOT), ROOT)
    except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
        result = {'status': 'ERROR', 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'FRESH' else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())

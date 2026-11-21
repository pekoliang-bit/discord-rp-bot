"""Static inventory of database assumptions in core/modules; does not execute modules."""
import ast
from collections import defaultdict
from pathlib import Path


CHECKS={'connect','backup','iterdump','create_function','set_trace_callback','enable_load_extension','set_authorizer','IntegrityError','OperationalError','DatabaseError','Error','in_transaction','lastrowid','rowcount','row_factory','executescript','isolation_level','total_changes','fetchmany','executemany'}


def main():
    root=Path(__file__).resolve().parent.parent
    results=defaultdict(set)
    files=0
    for folder in ('core','modules'):
        for path in sorted((root/folder).rglob('*.py')):
            files+=1
            tree=ast.parse(path.read_text(encoding='utf-8-sig'),filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node,ast.Attribute) and node.attr in CHECKS:
                    if node.attr in {'connect','Error','IntegrityError','OperationalError','DatabaseError'} and not (isinstance(node.value,ast.Name) and node.value.id=='sqlite3'):
                        continue
                    results[node.attr].add(str(path.relative_to(root)))
    print('AST_PARSE_OK:', files,'files')
    for name, paths in sorted(results.items()):
        print(name, len(paths), 'files')
        for path in sorted(paths):
            print('  '+path)
    print('Static inventory only; does not prove runtime compatibility.')


if __name__=='__main__':
    main()

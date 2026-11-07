import ast
import unittest
from pathlib import Path


class PluginBoundaryTests(unittest.TestCase):
    def test_generic_execution_and_bff_do_not_import_finance(self):
        roots = (Path('core'), Path('harness'), Path('bff'))
        for root in roots:
            for path in root.rglob('*.py'):
                imports = ast.walk(ast.parse(path.read_text('utf-8-sig')))
                for statement in imports:
                    if isinstance(statement, ast.Import):
                        names = [alias.name for alias in statement.names]
                    elif isinstance(statement, ast.ImportFrom):
                        names = [statement.module or '']
                        if statement.module == 'plugins':
                            names += ['plugins.' + alias.name for alias in statement.names]
                    else:
                        continue
                    self.assertFalse(any(n == 'plugins.finance' or n.startswith('plugins.finance.')
                                         for n in names), str(path))

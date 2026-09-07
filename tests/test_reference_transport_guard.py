import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

class TransportGuardTests(unittest.TestCase):
    def test_reference_inputs_fail_closed(self):
        source = Path(__file__).parents[1] / 'nodes.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        guard = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_validate_reference_media_transport')
        scope = {}
        exec(compile(ast.Module(body=[guard], type_ignores=[]), str(source), 'exec'), scope)
        validate = scope[guard.name]
        image = SimpleNamespace(media_type='image')
        video = SimpleNamespace(media_type='video')
        audio = SimpleNamespace(media_type='audio')
        for items in ([], [audio]):
            with self.assertRaisesRegex(ValueError, 'no image/video'):
                validate('normal prompt', items)
        with self.assertRaisesRegex(ValueError, 'Unresolved'):
            validate('__MINIMAX_H3_UNRESOLVED_REF_image__', [image])
        validate('<Picture 1>', [image, audio])
        validate('<Video 1>', [video])
        main = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'FeiHouEasyH3')
        generate = next(n for n in main.body if isinstance(n, ast.FunctionDef) and n.name == 'generate')
        gate = next(n for n in generate.body if isinstance(n, ast.If) and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == guard.name for c in ast.walk(n)))
        self.assertEqual(ast.unparse(gate.test), 'mode == MODE_REFERENCE')

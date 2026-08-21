import ast
import pathlib

SRC = pathlib.Path("src/mnemo_memory/packages/model_gateway")


def _imports(path):
    tree = ast.parse(path.read_text())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
    return names


def test_model_gateway_does_not_import_eval_or_apps():
    for path in SRC.glob("*.py"):
        for mod in _imports(path):
            assert "scripts" not in mod, f"{path} imports eval harness"
            assert not mod.startswith(
                "mnemo_memory.apps"
            ), f"{path} imports apps layer"

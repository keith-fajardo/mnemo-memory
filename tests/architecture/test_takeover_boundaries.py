import ast
import pathlib

SRC_MODEL_GATEWAY = pathlib.Path("src/mnemo_memory/packages/model_gateway")
SRC_OLLAMA_CONNECTORS = pathlib.Path("src/mnemo_memory/connectors/ollama")
SRC_EPISODIC_INGEST = pathlib.Path(
    "src/mnemo_memory/packages/application/episodic_extraction_ingest.py"
)


def _imports(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
    return names


def test_model_gateway_does_not_import_eval_or_apps() -> None:
    for path in SRC_MODEL_GATEWAY.glob("*.py"):
        for mod in _imports(path):
            assert "scripts" not in mod, f"{path} imports eval harness"
            assert not mod.startswith("mnemo_memory.apps"), f"{path} imports apps layer"


def test_ollama_connectors_do_not_import_scripts() -> None:
    for path in SRC_OLLAMA_CONNECTORS.glob("*.py"):
        for mod in _imports(path):
            assert "scripts" not in mod, f"{path} imports eval harness"


def test_episodic_extraction_ingest_does_not_import_scripts() -> None:
    for mod in _imports(SRC_EPISODIC_INGEST):
        assert "scripts" not in mod, f"{SRC_EPISODIC_INGEST} imports eval harness"

import ast
import pathlib

NETWORK_MODULES = {"urllib", "http", "socket", "ssl", "requests", "httpx"}
POLICY_MODULES = (
    pathlib.Path("src/mnemo_memory/packages/domain/typed_decisions.py"),
    pathlib.Path("src/mnemo_memory/packages/model_gateway/cascade_router.py"),
    pathlib.Path("src/mnemo_memory/packages/model_gateway/rule_axes.py"),
    pathlib.Path("src/mnemo_memory/packages/model_gateway/decision_axes.py"),
    pathlib.Path("src/mnemo_memory/packages/model_gateway/typed_decisions.py"),
)
TYPESAFE_CONNECTOR = pathlib.Path("src/mnemo_memory/connectors/typesafe")
RUNTIME_COMPOSITION = pathlib.Path("src/mnemo_memory/apps/cli/typed_decision_composition.py")


def _imports(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


def test_decision_policy_modules_never_open_network_connections() -> None:
    for path in POLICY_MODULES:
        for module in _imports(path):
            assert module.split(".")[0] not in NETWORK_MODULES, f"{path} imports {module}"


def test_typesafe_connector_and_runtime_composition_never_import_scripts() -> None:
    for path in (*TYPESAFE_CONNECTOR.glob("*.py"), RUNTIME_COMPOSITION):
        for module in _imports(path):
            assert "scripts" not in module, f"{path} imports the eval harness"


def test_only_the_runtime_composition_imports_the_jev_connector() -> None:
    importers = sorted(
        str(path)
        for path in pathlib.Path("src/mnemo_memory").rglob("*.py")
        if path.parent != TYPESAFE_CONNECTOR
        and any(module.startswith("mnemo_memory.connectors.typesafe") for module in _imports(path))
    )
    assert importers == [str(RUNTIME_COMPOSITION)]

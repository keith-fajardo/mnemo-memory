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


SOURCE_ROOT = pathlib.Path("src")
JEV_CONNECTOR_MODULE = "mnemo_memory.connectors.typesafe"


def _imports(path: pathlib.Path) -> list[str]:
    return _imports_from_source(path.read_text(), path.relative_to(SOURCE_ROOT).as_posix())


def _imports_from_source(source: str, module_path: str) -> list[str]:
    """Every imported module name, with ``from`` imports and relative imports resolved.

    ``module_path`` is the file's path under ``src/`` (for example ``mnemo_memory/apps/x.py``).
    ``from pkg import name`` records both ``pkg`` and ``pkg.name``, because ``name`` may be a
    submodule.
    """

    package = list(pathlib.PurePosixPath(module_path).with_suffix("").parts[:-1])
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: max(0, len(package) - node.level + 1)] if node.level else []
            module = ".".join([*base, *(node.module or "").split(".")]).strip(".")
            if module:
                names.append(module)
            names.extend(f"{module}.{alias.name}".lstrip(".") for alias in node.names)
    return names


def _imports_jev_connector(names: list[str]) -> bool:
    return any(
        name == JEV_CONNECTOR_MODULE or name.startswith(JEV_CONNECTOR_MODULE + ".")
        for name in names
    )


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
        if path.parent != TYPESAFE_CONNECTOR and _imports_jev_connector(_imports(path))
    )
    assert importers == [str(RUNTIME_COMPOSITION)]


def test_import_resolver_sees_package_and_relative_imports_of_the_connector() -> None:
    absolute = "from mnemo_memory.connectors import typesafe\n"
    assert _imports_jev_connector(_imports_from_source(absolute, "mnemo_memory/apps/x.py"))
    relative = "from ..connectors import typesafe\n"
    assert _imports_jev_connector(_imports_from_source(relative, "mnemo_memory/apps/x.py"))
    sibling = "from .typesafe import JevClassifier\n"
    assert _imports_jev_connector(_imports_from_source(sibling, "mnemo_memory/connectors/x.py"))
    package_init = "from ...connectors import typesafe\n"
    path = "mnemo_memory/apps/cli/__init__.py"
    assert _imports_jev_connector(_imports_from_source(package_init, path))


def test_import_resolver_ignores_unrelated_imports() -> None:
    source = (
        "import json\n"
        "from mnemo_memory.connectors import ollama\n"
        "from ..packages import domain\n"
        "from . import typesafe_notes\n"
        "from ..connectors import typesafe_archive\n"
    )
    names = _imports_from_source(source, "mnemo_memory/apps/x.py")
    assert not _imports_jev_connector(names)
    assert "mnemo_memory.connectors.ollama" in names
    assert "mnemo_memory.packages.domain" in names
    assert "mnemo_memory.apps.typesafe_notes" in names
    assert "mnemo_memory.connectors.typesafe_archive" in names

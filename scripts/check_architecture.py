from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
SOURCE_ROOT = Path("src/mnemo_memory")
PRODUCT_ROOTS = (SOURCE_ROOT,)

PACKAGE_COMPONENTS = {
    "packages/application",
    "packages/context_engine",
    "packages/domain",
    "packages/episodic",
    "packages/knowledge",
    "packages/model_gateway",
    "packages/policy",
    "packages/project_index",
    "packages/skills_registry",
    "packages/storage",
    "packages/telemetry",
}
APP_COMPONENTS = {
    "apps/api",
    "apps/cli",
    "apps/mcp",
    "apps/web",
    "apps/worker",
}
CONNECTOR_COMPONENTS = {
    "connectors/automatic_memory",
    "connectors/claude_code",
    "connectors/codex",
    "connectors/command_wrapper",
    "connectors/dbt",
    "connectors/filesystem",
    "connectors/local_embeddings",
    "connectors/git",
    "connectors/obsidian",
    "connectors/oauth",
    "connectors/postgresql",
}
KNOWN_COMPONENTS = PACKAGE_COMPONENTS | APP_COMPONENTS | CONNECTOR_COMPONENTS

ALLOWED_INTERNAL_DEPENDENCIES = {
    "packages/application": {"packages/domain", "packages/storage"},
    "packages/domain": set(),
    "packages/policy": {"packages/domain"},
    "packages/storage": {"packages/domain", "packages/policy"},
    "packages/episodic": {"packages/domain", "packages/policy", "packages/storage"},
    "packages/knowledge": {"packages/domain", "packages/policy", "packages/storage"},
    "packages/project_index": {"packages/domain", "packages/policy", "packages/storage"},
    "packages/skills_registry": {"packages/domain", "packages/policy", "packages/storage"},
    "packages/telemetry": set(),
    "packages/model_gateway": {
        "packages/domain",
        "packages/policy",
        "packages/telemetry",
    },
    "packages/context_engine": PACKAGE_COMPONENTS - {"packages/context_engine"},
}
for connector in CONNECTOR_COMPONENTS:
    ALLOWED_INTERNAL_DEPENDENCIES[connector] = set(PACKAGE_COMPONENTS)
for app in APP_COMPONENTS:
    ALLOWED_INTERNAL_DEPENDENCIES[app] = set(PACKAGE_COMPONENTS | CONNECTOR_COMPONENTS)


def component_prefixes(component: str) -> set[str]:
    group, name = component.split("/", maxsplit=1)
    if group == "packages":
        return {f"mnemo_memory.packages.{name}"}
    if group == "apps":
        return {f"mnemo_memory.apps.{name}"}
    return {
        f"mnemo_memory.connectors.{name}",
    }


PREFIX_TO_COMPONENT = {
    prefix: component for component in KNOWN_COMPONENTS for prefix in component_prefixes(component)
}


@dataclass(frozen=True)
class ImportedModule:
    name: str
    line: int
    relative_level: int = 0


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    message: str

    def render(self, root: Path) -> str:
        return f"{self.path.relative_to(root)}:{self.line}: {self.message}"


def component_for(path: Path, root: Path) -> str | None:
    relative = path.relative_to(root / SOURCE_ROOT)
    if len(relative.parts) < 2:
        # Package metadata and the thin installed console entry point are composition
        # boundaries, not product components.
        return "package_root"
    component = "/".join(relative.parts[:2])
    return component if component in KNOWN_COMPONENTS else None


def imported_modules(tree: ast.AST) -> list[ImportedModule]:
    imports: list[ImportedModule] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(ImportedModule(alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(ImportedModule(module, node.lineno, node.level))
    return imports


def imported_component(module: str) -> str | None:
    matches = (
        (prefix, component)
        for prefix, component in PREFIX_TO_COMPONENT.items()
        if module == prefix or module.startswith(f"{prefix}.")
    )
    return next(
        (component for _, component in sorted(matches, key=lambda item: -len(item[0]))), None
    )


def find_violations(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for product_root in PRODUCT_ROOTS:
        for path in sorted((root / product_root).rglob("*.py")):
            component = component_for(path, root)
            if component is None:
                violations.append(Violation(path, 1, "Python source is outside a known component"))
                continue
            if component == "package_root":
                continue

            try:
                tree = ast.parse(path.read_text(), filename=str(path))
            except SyntaxError as error:
                violations.append(
                    Violation(path, error.lineno or 1, f"invalid Python: {error.msg}")
                )
                continue

            for imported in imported_modules(tree):
                if imported.relative_level > 1:
                    violations.append(
                        Violation(
                            path,
                            imported.line,
                            "cross-component relative imports are prohibited",
                        )
                    )
                    continue

                target = imported_component(imported.name)
                if target is not None and target != component:
                    allowed = ALLOWED_INTERNAL_DEPENDENCIES[component]
                    if target not in allowed:
                        violations.append(
                            Violation(
                                path,
                                imported.line,
                                f"{component} must not depend on {target}",
                            )
                        )
                    continue

                if component == "packages/domain" and imported.relative_level == 0:
                    top_level = imported.name.split(".", maxsplit=1)[0]
                    if (
                        imported.name
                        and target is None
                        and top_level not in sys.stdlib_module_names
                    ):
                        violations.append(
                            Violation(
                                path,
                                imported.line,
                                "packages/domain may import only the standard library and itself; "
                                f"found {imported.name}",
                            )
                        )
    return violations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Mnemo package dependency boundaries")
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    return parser.parse_args()


def main() -> int:
    root = parse_args().root.resolve()
    violations = find_violations(root)
    if violations:
        for violation in violations:
            print(violation.render(root), file=sys.stderr)
        return 1

    source_count = sum(
        len(list((root / product_root).rglob("*.py"))) for product_root in PRODUCT_ROOTS
    )
    print(f"Architecture dependency check passed for {source_count} product Python files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

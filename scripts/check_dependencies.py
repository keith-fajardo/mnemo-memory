from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).parents[1]
PLAN_SHA256 = "3214135ad8faea305784fb43b72d18345071b83b81656542b91d7ec923a41fd5"
REQUIRED_FIELDS = {
    "ecosystem",
    "name",
    "version",
    "license",
    "source_url",
    "author",
    "owner",
    "purpose",
    "replacement_boundary",
    "direct",
    "approved",
}
FORBIDDEN_DEPENDENCY_FRAGMENTS = ("tencent", "agent-memory", "agent_memory")


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        return tomllib.load(file)


def load_register() -> tuple[set[str], list[dict[str, object]]]:
    document = load_toml(REPOSITORY_ROOT / "docs/dependency-register.toml")
    licenses = set(cast(list[str], document["approved_licenses"]))
    dependencies = cast(list[dict[str, object]], document["dependencies"])
    return licenses, dependencies


def validate_register_entries(
    approved_licenses: set[str], dependencies: list[dict[str, object]]
) -> None:
    seen: set[tuple[str, str]] = set()

    for dependency in dependencies:
        missing = REQUIRED_FIELDS - dependency.keys()
        if missing:
            raise AssertionError(
                f"dependency entry is missing fields {sorted(missing)}: {dependency}"
            )

        ecosystem = str(dependency["ecosystem"])
        name = str(dependency["name"])
        version = str(dependency["version"])
        license_name = str(dependency["license"])
        source_url = str(dependency["source_url"])
        key = (ecosystem, name.casefold())

        if key in seen:
            raise AssertionError(f"duplicate dependency entry: {ecosystem}:{name}")
        seen.add(key)

        if not version or not source_url.startswith("https://"):
            raise AssertionError(f"dependency lacks pinned provenance: {ecosystem}:{name}")
        if license_name not in approved_licenses or dependency["approved"] is not True:
            raise AssertionError(f"dependency has an unapproved license: {ecosystem}:{name}")
        if any(fragment in name.casefold() for fragment in FORBIDDEN_DEPENDENCY_FRAGMENTS):
            raise AssertionError(f"competing memory product dependency is prohibited: {name}")


def validate_python_lock(dependencies: list[dict[str, object]]) -> None:
    lock = load_toml(REPOSITORY_ROOT / "uv.lock")
    locked = {
        (str(package["name"]).casefold(), str(package["version"]))
        for package in cast(list[dict[str, object]], lock["package"])
        if isinstance(package.get("source"), dict)
        and cast(dict[str, object], package["source"]).get("registry") == "https://pypi.org/simple"
    }
    registered = {
        (str(dependency["name"]).casefold(), str(dependency["version"]))
        for dependency in dependencies
        if dependency["ecosystem"] == "pypi"
    }

    if locked != registered:
        raise AssertionError(
            f"Python dependency register does not match uv.lock; "
            f"missing={sorted(locked - registered)}, extra={sorted(registered - locked)}"
        )

    project = load_toml(REPOSITORY_ROOT / "pyproject.toml")
    project_table = cast(dict[str, object], project["project"])
    optional = cast(dict[str, list[str]], project_table.get("optional-dependencies", {}))
    direct_requirements = (
        list(cast(list[str], project_table["dependencies"]))
        + cast(dict[str, list[str]], project["dependency-groups"])["dev"]
        + [requirement for requirements in optional.values() for requirement in requirements]
    )
    direct_locked = {
        (requirement.split("==", maxsplit=1)[0].casefold(), requirement.split("==", maxsplit=1)[1])
        for requirement in direct_requirements
    }
    direct_registered = {
        (str(dependency["name"]).casefold(), str(dependency["version"]))
        for dependency in dependencies
        if dependency["ecosystem"] == "pypi" and dependency["direct"] is True
    }
    if direct_locked != direct_registered:
        raise AssertionError("direct Python requirements are not exactly pinned and registered")


def validate_node_lock() -> None:
    lock = json.loads((REPOSITORY_ROOT / "package-lock.json").read_text())
    packages = cast(dict[str, object], lock["packages"])
    third_party_packages = [path for path in packages if path]
    if third_party_packages:
        raise AssertionError(
            "frontend dependencies are deferred; unexpected npm packages: "
            f"{sorted(third_party_packages)}"
        )


def validate_ci_actions(dependencies: list[dict[str, object]]) -> None:
    workflow_directory = REPOSITORY_ROOT / ".github/workflows"
    ci_workflow = (workflow_directory / "ci.yml").read_text()
    required_ci_actions = set(re.findall(r"uses: ([^@\s]+)@([^\s]+)", ci_workflow))
    all_used_actions = {
        action
        for path in workflow_directory.glob("*.yml")
        for action in re.findall(r"uses: ([^@\s]+)@([^\s]+)", path.read_text())
    }
    registered_actions = {
        (str(dependency["name"]), str(dependency["version"]))
        for dependency in dependencies
        if dependency["ecosystem"] == "github-action"
    }
    missing_ci = required_ci_actions - registered_actions
    unused_registered = registered_actions - all_used_actions
    if missing_ci or unused_registered:
        raise AssertionError(
            f"CI Action register mismatch; missing_ci={sorted(missing_ci)}, "
            f"unused_registered={sorted(unused_registered)}"
        )


def validate_toolchain(dependencies: list[dict[str, object]]) -> None:
    registered = {
        str(dependency["name"]): str(dependency["version"])
        for dependency in dependencies
        if dependency["ecosystem"] == "toolchain"
    }
    package = json.loads((REPOSITORY_ROOT / "package.json").read_text())
    expected = {
        "CPython": (REPOSITORY_ROOT / ".python-version").read_text().strip(),
        "Node.js": (REPOSITORY_ROOT / ".nvmrc").read_text().strip(),
        "npm": str(package["packageManager"]).removeprefix("npm@"),
    }
    if any(registered.get(name) != version for name, version in expected.items()):
        raise AssertionError(
            f"toolchain register mismatch: expected {expected}, registered {registered}"
        )

    workflow = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text()
    uv_version = registered.get("uv")
    if uv_version is None or f'version: "{uv_version}"' not in workflow:
        raise AssertionError("CI must install the registered uv version")


def validate_plan_copy() -> None:
    plan = (REPOSITORY_ROOT / "docs/implementation-plan.md").read_bytes()
    digest = hashlib.sha256(plan).hexdigest()
    if digest != PLAN_SHA256:
        raise AssertionError(f"implementation plan is not the verbatim revision-2 source: {digest}")


def run_checks() -> None:
    approved_licenses, dependencies = load_register()
    validate_register_entries(approved_licenses, dependencies)
    validate_python_lock(dependencies)
    validate_node_lock()
    validate_ci_actions(dependencies)
    validate_toolchain(dependencies)
    validate_plan_copy()


def main() -> None:
    run_checks()
    _, dependencies = load_register()
    print(
        f"Dependency and provenance checks passed for {len(dependencies)} registered entries; "
        "no competing memory product dependency is declared."
    )


if __name__ == "__main__":
    main()

"""Offline, deterministic source-structure parsing across supported languages.

The parser reads only source bytes from a caller-selected local root.  It never
imports project modules, evaluates code, loads project plug-ins, reads an
environment variable, or persists source text.  Each built-in adapter records
only declarations plus syntactically explicit import and call targets.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Final
from uuid import UUID, uuid5

import tree_sitter_c
import tree_sitter_c_sharp
import tree_sitter_cpp
import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_php
import tree_sitter_rust
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from mnemo_memory.packages.domain import (
    CodeEdge,
    CodeEdgeKind,
    CodeFile,
    CodeSnapshot,
    CodeSnapshotId,
    CodeStructureArtifact,
    CodeSymbol,
    CodeSymbolId,
    CodeSymbolKind,
    MemoryScope,
)

_SNAPSHOT_NAMESPACE: Final = UUID("e7fdc5df-cb2b-438b-a0e6-822be148e02d")
_SYMBOL_NAMESPACE: Final = UUID("f9bf9b17-d5cc-4611-9603-8aafbf410f1d")
_SKIP_DIRECTORIES: Final = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)
# These are intentionally file-only inputs. Mnemo fingerprints their safe relative paths and bytes
# so a later source transition can say that (for example) a dbt model changed, but it does not
# claim to parse their language or infer dependencies from them.
_FILE_ONLY_SOURCE_SUFFIXES: Final = frozenset(
    {
        ".csv",
        ".css",
        ".dart",
        ".ex",
        ".exs",
        ".erl",
        ".fs",
        ".fsx",
        ".gql",
        ".graphql",
        ".hs",
        ".htm",
        ".html",
        ".ini",
        ".json",
        ".jsonc",
        ".kts",
        ".kt",
        ".lhs",
        ".lua",
        ".lock",
        ".pl",
        ".pm",
        ".ps1",
        ".r",
        ".rb",
        ".sass",
        ".scala",
        ".scss",
        ".sc",
        ".sql",
        ".svelte",
        ".swift",
        ".toml",
        ".tsv",
        ".vb",
        ".vue",
        ".xml",
        ".yaml",
        ".yml",
    }
)
# These conventional repository files have no useful suffix, or have names whose purpose matters
# more than their extension. They participate only as path/digest evidence. In particular, Mnemo
# does not parse dependency constraints, execute build instructions, or retain their contents.
_FILE_ONLY_SOURCE_FILENAMES: Final = frozenset(
    {
        "containerfile",
        "dockerfile",
        "gemfile",
        "go.mod",
        "justfile",
        "makefile",
        "pipfile",
        "procfile",
        "rakefile",
    }
)


class SourceStructureError(ValueError):
    """Sanitized parser failure; source contents are intentionally omitted."""


@dataclass(frozen=True, slots=True)
class SourceStructureLimits:
    """Personal-mode bounds that prevent a source snapshot from exhausting memory."""

    max_files: int = 10_000
    max_total_bytes: int = 20_000_000
    max_file_bytes: int = 1_000_000
    max_symbols: int = 100_000
    max_edges: int = 200_000

    def __post_init__(self) -> None:
        if any(
            value < 1
            for value in (
                self.max_files,
                self.max_total_bytes,
                self.max_file_bytes,
                self.max_symbols,
                self.max_edges,
            )
        ):
            raise ValueError("source-structure limits must be positive")


@dataclass(frozen=True, slots=True)
class SourceStructureParseRequest:
    """An explicit, scoped local source root to parse without executing it."""

    scope: MemoryScope
    root: Path
    limits: SourceStructureLimits = SourceStructureLimits()

    def __post_init__(self) -> None:
        if not isinstance(self.scope, MemoryScope):
            raise TypeError("source parsing requires an explicit scope")
        if not self.root.is_absolute() or not self.root.is_dir():
            raise SourceStructureError("MNEMO_SOURCE_ROOT_INVALID")


@dataclass(frozen=True, slots=True)
class _PendingSymbol:
    relative_path: str
    qualified_name: str
    kind: CodeSymbolKind
    line: int


@dataclass(frozen=True, slots=True)
class _LanguageRules:
    name: str
    suffixes: tuple[str, ...]
    language: Callable[[], object]
    declaration_kinds: tuple[tuple[str, CodeSymbolKind], ...]
    import_kinds: frozenset[str]
    call_kinds: tuple[tuple[str, str | None], ...]


_TREE_SITTER_RULES: Final = (
    _LanguageRules(
        "javascript",
        (".js", ".jsx", ".mjs", ".cjs"),
        tree_sitter_javascript.language,
        (
            ("class_declaration", CodeSymbolKind.CLASS),
            ("function_declaration", CodeSymbolKind.FUNCTION),
            ("generator_function_declaration", CodeSymbolKind.FUNCTION),
            ("method_definition", CodeSymbolKind.FUNCTION),
        ),
        frozenset({"import_statement"}),
        (("call_expression", "function"),),
    ),
    _LanguageRules(
        "typescript",
        (".ts", ".tsx", ".mts", ".cts"),
        tree_sitter_typescript.language_typescript,
        (
            ("class_declaration", CodeSymbolKind.CLASS),
            ("interface_declaration", CodeSymbolKind.INTERFACE),
            ("function_declaration", CodeSymbolKind.FUNCTION),
            ("generator_function_declaration", CodeSymbolKind.FUNCTION),
            ("method_definition", CodeSymbolKind.FUNCTION),
        ),
        frozenset({"import_statement"}),
        (("call_expression", "function"),),
    ),
    _LanguageRules(
        "tsx",
        (".tsx",),
        tree_sitter_typescript.language_tsx,
        (
            ("class_declaration", CodeSymbolKind.CLASS),
            ("interface_declaration", CodeSymbolKind.INTERFACE),
            ("function_declaration", CodeSymbolKind.FUNCTION),
            ("generator_function_declaration", CodeSymbolKind.FUNCTION),
            ("method_definition", CodeSymbolKind.FUNCTION),
        ),
        frozenset({"import_statement"}),
        (("call_expression", "function"),),
    ),
    _LanguageRules(
        "go",
        (".go",),
        tree_sitter_go.language,
        (
            ("type_spec", CodeSymbolKind.STRUCT),
            ("function_declaration", CodeSymbolKind.FUNCTION),
            ("method_declaration", CodeSymbolKind.FUNCTION),
        ),
        frozenset({"import_spec"}),
        (("call_expression", "function"),),
    ),
    _LanguageRules(
        "rust",
        (".rs",),
        tree_sitter_rust.language,
        (
            ("struct_item", CodeSymbolKind.STRUCT),
            ("enum_item", CodeSymbolKind.ENUM),
            ("trait_item", CodeSymbolKind.TRAIT),
            ("function_item", CodeSymbolKind.FUNCTION),
        ),
        frozenset({"use_declaration"}),
        (("call_expression", "function"),),
    ),
    _LanguageRules(
        "c",
        (".c", ".h"),
        tree_sitter_c.language,
        (
            ("struct_specifier", CodeSymbolKind.STRUCT),
            ("enum_specifier", CodeSymbolKind.ENUM),
            ("function_definition", CodeSymbolKind.FUNCTION),
        ),
        frozenset({"preproc_include"}),
        (("call_expression", "function"),),
    ),
    _LanguageRules(
        "cpp",
        (".cc", ".cp", ".cpp", ".cxx", ".c++", ".hh", ".hpp", ".hxx"),
        tree_sitter_cpp.language,
        (
            ("class_specifier", CodeSymbolKind.CLASS),
            ("struct_specifier", CodeSymbolKind.STRUCT),
            ("enum_specifier", CodeSymbolKind.ENUM),
            ("function_definition", CodeSymbolKind.FUNCTION),
        ),
        frozenset({"preproc_include"}),
        (("call_expression", "function"),),
    ),
    _LanguageRules(
        "csharp",
        (".cs",),
        tree_sitter_c_sharp.language,
        (
            ("class_declaration", CodeSymbolKind.CLASS),
            ("interface_declaration", CodeSymbolKind.INTERFACE),
            ("struct_declaration", CodeSymbolKind.STRUCT),
            ("enum_declaration", CodeSymbolKind.ENUM),
            ("method_declaration", CodeSymbolKind.FUNCTION),
        ),
        frozenset({"using_directive"}),
        (("invocation_expression", "function"),),
    ),
    _LanguageRules(
        "java",
        (".java",),
        tree_sitter_java.language,
        (
            ("class_declaration", CodeSymbolKind.CLASS),
            ("interface_declaration", CodeSymbolKind.INTERFACE),
            ("enum_declaration", CodeSymbolKind.ENUM),
            ("method_declaration", CodeSymbolKind.FUNCTION),
        ),
        frozenset({"import_declaration"}),
        (("method_invocation", None),),
    ),
    _LanguageRules(
        "php",
        (".php",),
        tree_sitter_php.language_php,
        (
            ("class_declaration", CodeSymbolKind.CLASS),
            ("interface_declaration", CodeSymbolKind.INTERFACE),
            ("trait_declaration", CodeSymbolKind.TRAIT),
            ("enum_declaration", CodeSymbolKind.ENUM),
            ("function_definition", CodeSymbolKind.FUNCTION),
            ("method_declaration", CodeSymbolKind.FUNCTION),
        ),
        frozenset({"namespace_use_declaration"}),
        (
            ("function_call_expression", "function"),
            ("member_call_expression", None),
            ("scoped_call_expression", None),
        ),
    ),
)


class SourceStructureParser:
    """Parse every supported source file into one scoped immutable snapshot.

    Python uses its standard-library AST. JavaScript/JSX, TypeScript/TSX, Go,
    Rust, C, C++, C#, Java, and PHP use pinned, precompiled Tree-sitter grammar
    wheels. No adapter
    fetches a grammar or contacts a network at parse time.
    """

    def __init__(self, *, languages: frozenset[str] | None = None) -> None:
        available = {"python", *(rule.name for rule in _TREE_SITTER_RULES)}
        selected = available if languages is None else languages
        unknown = selected - available
        if unknown:
            raise ValueError("unsupported source-structure parser language")
        self._languages = frozenset(selected)
        self._suffixes = self._suffix_rules()

    @property
    def supported_languages(self) -> tuple[str, ...]:
        return tuple(sorted(self._languages))

    @property
    def file_only_suffixes(self) -> tuple[str, ...]:
        """Extensions Mnemo fingerprints without claiming syntax or dependency support."""
        return tuple(sorted(_FILE_ONLY_SOURCE_SUFFIXES))

    @property
    def file_only_filenames(self) -> tuple[str, ...]:
        """Conventional file names Mnemo fingerprints without parsing their contents."""
        return tuple(sorted(_FILE_ONLY_SOURCE_FILENAMES))

    def parse(self, request: SourceStructureParseRequest) -> CodeStructureArtifact:
        paths = self._paths(request)
        digest = sha256()
        pending: list[_PendingSymbol] = []
        files: list[tuple[str, str]] = []
        imports: list[tuple[str, str]] = []
        bindings: list[tuple[str, str, str]] = []
        default_exports: list[tuple[str, str]] = []
        re_exports: list[tuple[str, str, str]] = []
        wildcard_re_exports: list[tuple[str, str]] = []
        static_methods: list[tuple[str, str]] = []
        calls: list[tuple[str, str, str]] = []
        go_module_path: str | None = None
        typescript_path_aliases: dict[str, str] = {}
        total_bytes = 0
        for path in paths:
            raw = self._read(path, request.limits)
            total_bytes += len(raw)
            if total_bytes > request.limits.max_total_bytes:
                raise SourceStructureError("MNEMO_SOURCE_TOTAL_BYTES_LIMIT")
            relative = path.relative_to(request.root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(raw)
            files.append((relative, f"sha256:{sha256(raw).hexdigest()}"))
            if relative == "go.mod":
                # ``go.mod`` is treated as bounded, file-only evidence.  Its module directive
                # lets us prove that an import belongs to this local checkout; we neither retain
                # nor execute the file's contents.
                go_module_path = self._go_module_path(raw)
            elif relative == "tsconfig.json":
                # Strict JSON only.  Config inheritance, comments, package resolution, and
                # every non-local/ambiguous mapping stay outside this static projection.
                typescript_path_aliases = self._typescript_path_aliases(raw)
            language = self._suffixes.get(path.suffix.lower())
            if language is None:
                # Keep only the path/digest projection for a deliberately file-only extension.
                # No module, declaration, import, or call claim is manufactured from its bytes.
                continue
            module = self._module_name(relative, language)
            pending.append(_PendingSymbol(relative, module, CodeSymbolKind.MODULE, 1))
            if language == "python":
                self._parse_python(raw, relative, module, pending, imports, bindings, calls)
            else:
                self._parse_tree_sitter(
                    language,
                    raw,
                    relative,
                    module,
                    pending,
                    imports,
                    bindings,
                    default_exports,
                    re_exports,
                    wildcard_re_exports,
                    static_methods,
                    calls,
                )
        if len(pending) > request.limits.max_symbols:
            raise SourceStructureError("MNEMO_SOURCE_SYMBOL_LIMIT")
        source_digest = f"sha256:{digest.hexdigest()}"
        snapshot_id = CodeSnapshotId(
            uuid5(_SNAPSHOT_NAMESPACE, f"{request.scope.to_dict()}:{source_digest}")
        )
        if go_module_path is not None:
            pending.extend(self._go_package_symbols(pending, go_module_path))
        symbols = tuple(
            CodeSymbol(
                snapshot_id,
                CodeSymbolId(
                    uuid5(
                        _SYMBOL_NAMESPACE,
                        f"{source_digest}:{item.relative_path}:{item.qualified_name}:{item.kind}:{item.line}",
                    )
                ),
                item.relative_path,
                item.qualified_name,
                item.kind,
                item.line,
            )
            for item in sorted(
                pending,
                key=lambda item: (
                    item.relative_path,
                    item.line,
                    item.qualified_name,
                    item.kind.value,
                ),
            )
        )
        modules = {
            symbol.relative_path: symbol
            for symbol in symbols
            if symbol.kind is CodeSymbolKind.MODULE
        }
        module_names = _symbols_by_name(modules.values())
        go_packages_by_directory = self._go_packages_by_directory(symbols, go_module_path)
        symbols_by_location = _symbols_by_location(symbols)
        symbols_by_name = _symbols_by_name(symbols)
        default_exports_by_path: dict[str, tuple[CodeSymbol, ...]] = {}
        for default_path, qualified_name in default_exports:
            default_exports_by_path[default_path] = (
                *default_exports_by_path.get(default_path, ()),
                *symbols_by_name.get(qualified_name, ()),
            )
        re_exports_by_path: dict[str, dict[str, tuple[str, ...]]] = {}
        for export_path, exported_name, encoded_target in re_exports:
            exports = re_exports_by_path.setdefault(export_path, {})
            exports[exported_name] = (*exports.get(exported_name, ()), encoded_target)
        wildcard_re_exports_by_path: dict[str, tuple[str, ...]] = {}
        for export_path, import_target in wildcard_re_exports:
            wildcard_re_exports_by_path[export_path] = (
                *wildcard_re_exports_by_path.get(export_path, ()),
                import_target,
            )
        static_method_ids = frozenset(
            item.symbol_id
            for static_path, qualified_name in static_methods
            for item in symbols_by_name.get(qualified_name, ())
            if item.relative_path == static_path
        )
        python_package_re_exports_by_name: dict[str, dict[str, tuple[str, ...]]] = {}
        for export_path, binding_name, imported_target in bindings:
            if PurePosixPath(export_path).name != "__init__.py":
                continue
            package = modules.get(export_path)
            if package is None or not _is_safe_symbol_name(binding_name):
                continue
            resolved_target = SourceStructureParser._python_re_export_target(
                export_path, imported_target, modules, symbols_by_name
            )
            if resolved_target is None:
                continue
            exports = python_package_re_exports_by_name.setdefault(package.qualified_name, {})
            exports[binding_name] = (*exports.get(binding_name, ()), resolved_target)
        call_edges: list[CodeEdge] = []
        for call_path, qualified_name, target in sorted(set(calls)):
            caller = _single_symbol(symbols_by_location.get((call_path, qualified_name), ()))
            if caller is None:
                # Overloaded or otherwise duplicated declarations have no unambiguous source
                # symbol in this bounded projection. Preserve neither a guessed call edge nor a
                # false impact candidate.
                continue
            call_edges.append(
                CodeEdge(
                    snapshot_id,
                    caller.symbol_id,
                    target,
                    CodeEdgeKind.CALLS,
                    self._resolve_call_target(
                        call_path,
                        qualified_name,
                        target,
                        module_names,
                        modules,
                        symbols_by_name,
                        imports,
                        bindings,
                        default_exports_by_path,
                        re_exports_by_path,
                        wildcard_re_exports_by_path,
                        python_package_re_exports_by_name,
                        static_method_ids,
                        go_module_path,
                        typescript_path_aliases,
                    ),
                )
            )
        edges = tuple(
            sorted(
                (
                    *(
                        CodeEdge(
                            snapshot_id,
                            modules[path].symbol_id,
                            target,
                            CodeEdgeKind.IMPORTS,
                            self._resolve_import_target(
                                path,
                                target,
                                module_names,
                                modules,
                                go_module_path,
                                go_packages_by_directory,
                                typescript_path_aliases,
                            ),
                        )
                        for path, target in sorted(set(imports))
                    ),
                    *call_edges,
                ),
                key=lambda edge: (str(edge.source_symbol_id), edge.kind.value, edge.target),
            )
        )
        if len(edges) > request.limits.max_edges:
            raise SourceStructureError("MNEMO_SOURCE_EDGE_LIMIT")
        snapshot = CodeSnapshot(
            snapshot_id, request.scope, source_digest, len(paths), len(symbols), len(edges)
        )
        return CodeStructureArtifact(
            snapshot,
            symbols,
            edges,
            tuple(
                CodeFile(snapshot_id, relative_path, content_digest)
                for relative_path, content_digest in files
            ),
        )

    def _suffix_rules(self) -> dict[str, str]:
        suffixes: dict[str, str] = {}
        if "python" in self._languages:
            suffixes[".py"] = "python"
        for rule in _TREE_SITTER_RULES:
            if rule.name not in self._languages:
                continue
            for suffix in rule.suffixes:
                # TSX is deliberately more specific than the TypeScript rule.
                suffixes[suffix] = rule.name
        return suffixes

    def _paths(self, request: SourceStructureParseRequest) -> tuple[Path, ...]:
        paths = tuple(
            path
            for path in sorted(request.root.rglob("*"))
            if path.is_file()
            and not path.is_symlink()
            and (
                path.suffix.lower() in self._suffixes
                or path.suffix.lower() in _FILE_ONLY_SOURCE_SUFFIXES
                or path.name.lower() in _FILE_ONLY_SOURCE_FILENAMES
            )
            and not any(part in _SKIP_DIRECTORIES for part in path.relative_to(request.root).parts)
        )
        if len(paths) > request.limits.max_files:
            raise SourceStructureError("MNEMO_SOURCE_FILE_LIMIT")
        return paths

    @staticmethod
    def _read(path: Path, limits: SourceStructureLimits) -> bytes:
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise SourceStructureError("MNEMO_SOURCE_READ_FAILED") from error
        if len(raw) > limits.max_file_bytes:
            raise SourceStructureError("MNEMO_SOURCE_FILE_BYTES_LIMIT")
        return raw

    @staticmethod
    def _module_name(relative: str, language: str) -> str:
        parts = relative.rsplit(".", maxsplit=1)[0].split("/")
        if language == "python" and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts) or "__root__"

    @staticmethod
    def _go_module_path(raw: bytes) -> str | None:
        """Extract one conservative local module path from a file-only ``go.mod``.

        This is deliberately not a Go configuration parser.  A malformed or unusual module
        directive simply leaves Go imports unresolved instead of manufacturing an internal link.
        """
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            return None
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            fields = stripped.split()
            if len(fields) < 2 or fields[0] != "module":
                continue
            candidate = fields[1]
            if (
                candidate.startswith("/")
                or candidate.endswith("/")
                or any(part in {"", ".", ".."} for part in candidate.split("/"))
                or any(
                    not (character.isalnum() or character in {".", "-", "_", "~", "/"})
                    for character in candidate
                )
            ):
                return None
            return candidate
        return None

    @staticmethod
    def _typescript_path_aliases(raw: bytes) -> dict[str, str]:
        """Return conservative local TypeScript path aliases from root ``tsconfig.json``.

        The TypeScript configuration language allows inheritance and JSON-with-comments.  Mnemo
        deliberately accepts only a bounded strict-JSON subset: one literal local base URL and
        one literal local target for each exact or single-wildcard alias.  Invalid configuration
        leaves aliases unresolved; it never becomes a parser failure or a guessed module link.
        """
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(document, dict):
            return {}
        options = document.get("compilerOptions")
        if not isinstance(options, dict):
            return {}
        base_url = options.get("baseUrl", ".")
        base = SourceStructureParser._safe_typescript_path(base_url, allow_current=True)
        paths = options.get("paths")
        if base is None or not isinstance(paths, dict):
            return {}
        aliases: dict[str, str] = {}
        for alias, targets in paths.items():
            if (
                not isinstance(alias, str)
                or not alias
                or len(alias) > 256
                or alias.count("*") > 1
                or not isinstance(targets, list)
                or len(targets) != 1
                or not isinstance(targets[0], str)
            ):
                continue
            target = targets[0]
            if target.count("*") != alias.count("*"):
                continue
            local_target = SourceStructureParser._safe_typescript_path(target)
            if local_target is None:
                continue
            joined = "/".join(part for part in (base, local_target) if part)
            # A wildcard remains only as a path-component marker here; a concrete import is
            # normalized below before it ever reaches the normal local-path resolver.
            aliases[alias] = joined
        return aliases

    @staticmethod
    def _safe_typescript_path(value: object, *, allow_current: bool = False) -> str | None:
        if not isinstance(value, str) or not value or len(value) > 512 or "\\" in value:
            return None
        if allow_current and value in {".", "./"}:
            return ""
        if value.startswith("/") or value.endswith("/"):
            return None
        if any(part in {"", ".", ".."} for part in value.split("/")):
            return None
        return value

    @staticmethod
    def _go_package_symbols(
        pending: list[_PendingSymbol], go_module_path: str
    ) -> tuple[_PendingSymbol, ...]:
        """Create one import-addressable package symbol for each local Go directory.

        A package is distinct from a Go source-file module.  The stable qualified name is the
        declared module import path, while the relative identity is its safe directory (or the
        root ``go.mod`` for the root package).  No package is inferred without ``go.mod``.
        """
        directories = {
            ""
            if PurePosixPath(item.relative_path).parent == PurePosixPath(".")
            else PurePosixPath(item.relative_path).parent.as_posix()
            for item in pending
            if item.kind is CodeSymbolKind.MODULE
            and PurePosixPath(item.relative_path).suffix == ".go"
        }
        return tuple(
            _PendingSymbol(
                directory or "go.mod",
                f"go:{go_module_path}" if not directory else f"go:{go_module_path}/{directory}",
                CodeSymbolKind.PACKAGE,
                1,
            )
            for directory in sorted(directories)
        )

    @staticmethod
    def _go_packages_by_directory(
        symbols: tuple[CodeSymbol, ...], go_module_path: str | None
    ) -> dict[str, CodeSymbol]:
        """Index the synthetic package symbols by exact local Go directory."""
        if go_module_path is None:
            return {}
        root_name = f"go:{go_module_path}"
        result: dict[str, CodeSymbol] = {}
        for symbol in symbols:
            if symbol.kind is not CodeSymbolKind.PACKAGE:
                continue
            if symbol.qualified_name == root_name:
                result[""] = symbol
            elif symbol.qualified_name.startswith(f"{root_name}/"):
                result[symbol.qualified_name.removeprefix(f"{root_name}/")] = symbol
        return result

    @staticmethod
    def _resolve_import_target(
        source_path: str,
        target: str,
        modules_by_name: dict[str, tuple[CodeSymbol, ...]],
        modules_by_path: dict[str, CodeSymbol],
        go_module_path: str | None = None,
        go_packages_by_directory: dict[str, CodeSymbol] | None = None,
        typescript_path_aliases: dict[str, str] | None = None,
    ) -> CodeSymbolId | None:
        """Resolve only an unambiguous import to a module in this snapshot.

        Package roots, aliases, generated modules, external dependencies, and
        members are deliberately not guessed. An unresolved string target remains
        evidence, but is not represented as an internal graph link.
        """
        if source_path.endswith(".go"):
            directory = SourceStructureParser._go_local_package_directory(target, go_module_path)
            if directory is not None:
                package = (go_packages_by_directory or {}).get(directory)
                if package is not None:
                    return package.symbol_id
            # Go import strings identify packages. Without a matching local module declaration
            # and exact package projection, do not fall through to a coincidental dotted/path
            # spelling elsewhere in the snapshot.
            return None
        if source_path.endswith((".ts", ".tsx", ".mts", ".cts")):
            local_target = SourceStructureParser._typescript_alias_target(
                target, typescript_path_aliases or {}
            )
            if local_target is not None:
                target = local_target
        direct = _single_symbol(modules_by_name.get(target, ()))
        if direct is not None:
            return direct.symbol_id
        parts = target.split(".")
        for end in range(len(parts) - 1, 0, -1):
            candidate = _single_symbol(modules_by_name.get(".".join(parts[:end]), ()))
            if candidate is not None:
                return candidate.symbol_id
        # Python relative imports use a dot prefix such as ``.helpers`` or
        # ``..helpers``. JavaScript/TypeScript use filesystem spellings such as
        # ``./helpers`` and must continue through the path resolver below.
        if target.startswith(".") and not target.startswith(("./", "../")):
            resolved = SourceStructureParser._relative_python_import_reference(
                source_path, target, modules_by_path
            )
            return None if resolved is None else resolved[0].symbol_id
        normalized = SourceStructureParser._normal_relative_path(target)
        return (
            None
            if normalized is None
            else SourceStructureParser._path_import_target(normalized, modules_by_path)
        )

    @staticmethod
    def _go_local_package_directory(import_target: str, go_module_path: str | None) -> str | None:
        """Return an exact local Go package directory only for a declared module prefix."""
        if go_module_path is None:
            return None
        if import_target == go_module_path:
            return ""
        prefix = f"{go_module_path}/"
        if not import_target.startswith(prefix):
            return None
        normalized = SourceStructureParser._normal_relative_path(import_target.removeprefix(prefix))
        return normalized

    @staticmethod
    def _typescript_alias_target(target: str, aliases: dict[str, str]) -> str | None:
        """Resolve one exact configured alias to a safe local path, never an npm package."""
        candidates: set[str] = set()
        for alias, replacement in aliases.items():
            if "*" not in alias:
                if target == alias:
                    candidates.add(replacement)
                continue
            prefix, suffix = alias.split("*", maxsplit=1)
            if not target.startswith(prefix) or not target.endswith(suffix):
                continue
            end = len(target) - len(suffix) if suffix else len(target)
            wildcard = target[len(prefix) : end]
            if not wildcard:
                continue
            candidate = replacement.replace("*", wildcard)
            normalized = SourceStructureParser._normal_relative_path(candidate)
            if normalized is not None:
                candidates.add(normalized)
        return next(iter(candidates)) if len(candidates) == 1 else None

    @staticmethod
    def _relative_python_import_reference(
        source_path: str, target: str, modules_by_path: dict[str, CodeSymbol]
    ) -> tuple[CodeSymbol, tuple[str, ...]] | None:
        """Resolve one explicit relative Python import to its local module plus member tail.

        ``from .helpers import validate`` is stored as ``.helpers.validate``.  The source-root
        parser has no packaging/import execution context, so it follows only directory-relative,
        in-snapshot paths and stops if the dot prefix would escape the registered root.  The
        returned tail preserves the imported member spelling for a later call binding lookup.
        """
        levels = len(target) - len(target.lstrip("."))
        if levels < 1:
            return None
        remainder = target[levels:]
        parts = tuple(part for part in remainder.split(".") if part)
        if remainder and len(parts) != len(remainder.split(".")):
            return None
        source_parent_parts = list(PurePosixPath(source_path).parent.parts)
        parents_to_leave = levels - 1
        if parents_to_leave > len(source_parent_parts):
            return None
        base = source_parent_parts[: len(source_parent_parts) - parents_to_leave]
        for end in range(len(parts), -1, -1):
            candidate = "/".join((*base, *parts[:end]))
            if not candidate:
                continue
            module_id = SourceStructureParser._path_import_target(candidate, modules_by_path)
            if module_id is None:
                continue
            module = next(
                (item for item in modules_by_path.values() if item.symbol_id == module_id), None
            )
            if module is not None:
                return module, parts[end:]
        return None

    @staticmethod
    def _python_re_export_target(
        source_path: str,
        imported_target: str,
        modules_by_path: dict[str, CodeSymbol],
        symbols_by_name: dict[str, tuple[CodeSymbol, ...]],
    ) -> str | None:
        """Normalize one literal ``__init__.py`` import to a unique local declaration.

        A package initializer can intentionally provide a stable public spelling,
        for example ``from .core import validate as check``.  This is a static
        declaration relationship, not execution of the initializer.  Wildcards
        and anything that does not name one saved declaration stay unresolved.
        """
        if imported_target.startswith(".") and not imported_target.startswith(("./", "../")):
            relative_reference = SourceStructureParser._relative_python_import_reference(
                source_path, imported_target, modules_by_path
            )
            if relative_reference is None:
                return None
            module, tail = relative_reference
            candidate_name = ".".join((module.qualified_name, *tail))
        else:
            candidate_name = imported_target
        return candidate_name if _single_symbol(symbols_by_name.get(candidate_name, ())) else None

    @staticmethod
    def _resolve_python_package_re_export(
        target: str,
        package_re_exports_by_name: dict[str, dict[str, tuple[str, ...]]],
        symbols_by_name: dict[str, tuple[CodeSymbol, ...]],
    ) -> CodeSymbol | None:
        """Resolve one unambiguous package-export spelling to its saved declaration."""
        for package_name in sorted(package_re_exports_by_name, key=len, reverse=True):
            prefix = f"{package_name}."
            if not target.startswith(prefix):
                continue
            member, dot, remainder = target[len(prefix) :].partition(".")
            targets = package_re_exports_by_name[package_name].get(member, ())
            if len(set(targets)) != 1:
                continue
            candidate_name = targets[0] if not dot else f"{targets[0]}.{remainder}"
            candidate = _single_symbol(symbols_by_name.get(candidate_name, ()))
            if candidate is not None:
                return candidate
        return None

    @staticmethod
    def _path_import_target(
        normalized: str, modules_by_path: dict[str, CodeSymbol]
    ) -> CodeSymbolId | None:
        candidates = [normalized]
        if "." not in PurePosixPath(normalized).name:
            suffixes = (
                ".py",
                ".js",
                ".jsx",
                ".mjs",
                ".cjs",
                ".ts",
                ".tsx",
                ".mts",
                ".cts",
                ".go",
                ".rs",
                ".c",
                ".cpp",
                ".cs",
                ".java",
                ".php",
            )
            candidates.extend(f"{normalized}{suffix}" for suffix in suffixes)
            candidates.extend(f"{normalized}/index{suffix}" for suffix in (".js", ".ts", ".tsx"))
            candidates.append(f"{normalized}/__init__.py")
        matches = [
            modules_by_path[candidate] for candidate in candidates if candidate in modules_by_path
        ]
        return matches[0].symbol_id if len(matches) == 1 else None

    @staticmethod
    def _normal_relative_path(value: str) -> str | None:
        path = PurePosixPath(value)
        if path.is_absolute():
            return None
        parts: list[str] = []
        for part in path.parts:
            if part in {"", "."}:
                continue
            if part == "..":
                if not parts:
                    return None
                parts.pop()
            else:
                parts.append(part)
        return "/".join(parts) or None

    @staticmethod
    def _resolve_call_target(
        source_path: str,
        caller_qualified_name: str,
        target: str,
        modules_by_name: dict[str, tuple[CodeSymbol, ...]],
        modules_by_path: dict[str, CodeSymbol],
        symbols_by_name: dict[str, tuple[CodeSymbol, ...]],
        imports: list[tuple[str, str]],
        bindings: list[tuple[str, str, str]],
        default_exports_by_path: dict[str, tuple[CodeSymbol, ...]],
        re_exports_by_path: dict[str, dict[str, tuple[str, ...]]],
        wildcard_re_exports_by_path: dict[str, tuple[str, ...]],
        python_package_re_exports_by_name: dict[str, dict[str, tuple[str, ...]]],
        static_method_ids: frozenset[CodeSymbolId],
        go_module_path: str | None,
        typescript_path_aliases: dict[str, str],
    ) -> CodeSymbolId | None:
        """Resolve only an unambiguous static call target within this snapshot.

        This intentionally handles a narrow, evidence-preserving subset: a
        same-module declaration, an already-qualified internal declaration, or
        an imported member whose exact qualified name is present. Alias mapping,
        overload resolution, dispatch, reflection, and generated behavior stay
        unresolved rather than being guessed.
        """
        module = modules_by_path.get(source_path)
        candidates: list[CodeSymbol] = []
        if module is not None:
            same_module = _single_symbol(
                tuple(
                    item
                    for item in symbols_by_name.get(f"{module.qualified_name}.{target}", ())
                    if item.relative_path == source_path
                )
            )
            if same_module is not None:
                candidates.append(same_module)
        direct = _single_symbol(symbols_by_name.get(target, ()))
        if direct is not None:
            candidates.append(direct)
        owner_name, separator, member = caller_qualified_name.rpartition(".")
        owner = _single_symbol(symbols_by_name.get(owner_name, ())) if separator else None
        if (
            owner is not None
            and owner.kind
            in {
                CodeSymbolKind.CLASS,
                CodeSymbolKind.INTERFACE,
                CodeSymbolKind.STRUCT,
                CodeSymbolKind.TRAIT,
                CodeSymbolKind.ENUM,
            }
            and target.startswith(("self.", "this."))
        ):
            sibling_name = target.partition(".")[2]
            sibling = _single_symbol(
                tuple(
                    item
                    for item in symbols_by_name.get(f"{owner.qualified_name}.{sibling_name}", ())
                    if item.relative_path == owner.relative_path
                )
            )
            if sibling is not None:
                candidates.append(sibling)
        for _, imported_target in (item for item in imports if item[0] == source_path):
            if imported_target.endswith(f".{target}"):
                imported = _single_symbol(symbols_by_name.get(imported_target, ()))
                if imported is not None:
                    candidates.append(imported)
        binding_name, separator, remainder = target.partition(".")
        for _, _binding, imported_target in (
            item for item in bindings if item[0] == source_path and item[1] == binding_name
        ):
            if imported_target.startswith(".") and not imported_target.startswith(("./", "../")):
                relative_reference = SourceStructureParser._relative_python_import_reference(
                    source_path, imported_target, modules_by_path
                )
                if relative_reference is None:
                    continue
                module, imported_tail = relative_reference
                member_parts = (*imported_tail, *((remainder,) if separator else ()))
                candidate_name = ".".join((module.qualified_name, *member_parts))
                imported = _single_symbol(symbols_by_name.get(candidate_name, ()))
                if imported is not None:
                    candidates.append(imported)
                continue
            import_target, marker, imported_member = imported_target.partition("|")
            if marker:
                if import_target.startswith("go:"):
                    member = remainder if separator else imported_member
                    candidates.extend(
                        SourceStructureParser._go_imported_member_candidates(
                            import_target.removeprefix("go:"),
                            member,
                            symbols_by_name,
                            go_module_path,
                        )
                    )
                    continue
                module_id = SourceStructureParser._resolve_import_target(
                    source_path,
                    import_target,
                    modules_by_name,
                    modules_by_path,
                    typescript_path_aliases=typescript_path_aliases,
                )
                module = next(
                    (item for item in modules_by_path.values() if item.symbol_id == module_id), None
                )
                if module is None:
                    continue
                member = remainder if separator else imported_member
                re_exported = SourceStructureParser._resolve_re_export_target(
                    module,
                    imported_member,
                    modules_by_name,
                    modules_by_path,
                    symbols_by_name,
                    default_exports_by_path,
                    re_exports_by_path,
                    wildcard_re_exports_by_path,
                )
                if re_exported is not None:
                    if not separator:
                        candidates.append(re_exported)
                    elif re_exported.kind is CodeSymbolKind.CLASS:
                        resolved_static_member = _single_symbol(
                            symbols_by_name.get(f"{re_exported.qualified_name}.{remainder}", ())
                        )
                        if (
                            resolved_static_member is not None
                            and resolved_static_member.symbol_id in static_method_ids
                        ):
                            candidates.append(resolved_static_member)
                    continue
                if imported_member == "default":
                    default_export = _single_symbol(
                        default_exports_by_path.get(module.relative_path, ())
                    )
                    if default_export is not None:
                        if not separator:
                            candidates.append(default_export)
                        elif default_export.kind is CodeSymbolKind.CLASS:
                            member_export = _single_symbol(
                                symbols_by_name.get(
                                    f"{default_export.qualified_name}.{remainder}", ()
                                )
                            )
                            if (
                                member_export is not None
                                and member_export.symbol_id in static_method_ids
                            ):
                                candidates.append(member_export)
                    continue
                candidate_name = (
                    module.qualified_name if not member else f"{module.qualified_name}.{member}"
                )
                imported_names = [candidate_name]
            elif imported_target.startswith("java-static:"):
                # ``import static package.Type.member;`` brings one exact member spelling into
                # scope. It resolves only if the package/type module and same-named declared
                # class are each unique in the current immutable snapshot.
                static_target = imported_target.removeprefix("java-static:")
                owner_path, dot, static_member = static_target.rpartition(".")
                module = _single_symbol(modules_by_name.get(owner_path, ())) if dot else None
                if module is None:
                    continue
                type_name = owner_path.rsplit(".", maxsplit=1)[-1]
                member_name = remainder if separator else static_member
                if not _is_safe_symbol_name(member_name):
                    continue
                imported_names = [f"{module.qualified_name}.{type_name}.{member_name}"]
            else:
                imported_names = [
                    imported_target if not separator else f"{imported_target}.{remainder}"
                ]
                if separator and imported_target in modules_by_name:
                    imported_type = imported_target.rsplit(".", maxsplit=1)[-1]
                    if _is_safe_symbol_name(imported_type):
                        imported_names.append(f"{imported_target}.{imported_type}.{remainder}")
            for candidate_name in imported_names:
                imported = _single_symbol(symbols_by_name.get(candidate_name, ()))
                if imported is None:
                    imported = SourceStructureParser._resolve_python_package_re_export(
                        candidate_name, python_package_re_exports_by_name, symbols_by_name
                    )
                if imported is not None:
                    candidates.append(imported)
        # C# ``using static Namespace.Type;`` exposes a named member without an alias. Treat it
        # as a candidate only for a simple syntactic call and only when its module, class, and
        # member are each unique in the immutable snapshot. This intentionally does not model
        # overload resolution, extension methods, inherited members, or runtime dispatch.
        if not separator and _is_safe_symbol_name(target):
            for _, _binding, imported_target in (
                item
                for item in bindings
                if item[0] == source_path and item[2].startswith("csharp-static:")
            ):
                owner_path = imported_target.removeprefix("csharp-static:")
                module = _single_symbol(modules_by_name.get(owner_path, ()))
                if module is None:
                    continue
                type_name = owner_path.rsplit(".", maxsplit=1)[-1]
                imported = _single_symbol(
                    symbols_by_name.get(f"{module.qualified_name}.{type_name}.{target}", ())
                )
                if imported is not None:
                    candidates.append(imported)
        unique = {item.symbol_id: item for item in candidates}
        return next(iter(unique)) if len(unique) == 1 else None

    @staticmethod
    def _go_imported_member_candidates(
        import_target: str,
        member: str,
        symbols_by_name: dict[str, tuple[CodeSymbol, ...]],
        go_module_path: str | None,
    ) -> tuple[CodeSymbol, ...]:
        """Resolve an exact Go package member only when its local directory is unique.

        Go imports identify directories, so a matching directory suffix alone is
        insufficient evidence: an external package can use the same suffix.  This
        resolves only an import below this checkout's declared ``go.mod`` module,
        and only when exactly one saved declaration named ``Member`` lives in the
        exact package directory.
        """
        directory = SourceStructureParser._go_local_package_directory(import_target, go_module_path)
        if directory is None or not _is_safe_symbol_name(member):
            return ()
        candidates = [
            symbol
            for symbols in symbols_by_name.values()
            for symbol in symbols
            if symbol.qualified_name.endswith(f".{member}")
            and (
                ""
                if PurePosixPath(symbol.relative_path).parent == PurePosixPath(".")
                else PurePosixPath(symbol.relative_path).parent.as_posix()
            )
            == directory
        ]
        unique = {item.symbol_id: item for item in candidates}
        return tuple(unique.values()) if len(unique) == 1 else ()

    @classmethod
    def _parse_python(
        cls,
        raw: bytes,
        relative: str,
        module: str,
        symbols: list[_PendingSymbol],
        imports: list[tuple[str, str]],
        bindings: list[tuple[str, str, str]],
        calls: list[tuple[str, str, str]],
    ) -> None:
        try:
            tree = ast.parse(raw.decode("utf-8"), filename=relative)
        except (SyntaxError, UnicodeDecodeError) as error:
            raise SourceStructureError("MNEMO_SOURCE_PYTHON_INVALID") from error
        cls._collect_python(tree.body, relative, module, symbols, imports, bindings, calls)

    @classmethod
    def _collect_python(
        cls,
        body: list[ast.stmt],
        relative: str,
        parent: str,
        symbols: list[_PendingSymbol],
        imports: list[tuple[str, str]],
        bindings: list[tuple[str, str, str]],
        calls: list[tuple[str, str, str]],
    ) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = (
                    CodeSymbolKind.CLASS
                    if isinstance(node, ast.ClassDef)
                    else (
                        CodeSymbolKind.ASYNC_FUNCTION
                        if isinstance(node, ast.AsyncFunctionDef)
                        else CodeSymbolKind.FUNCTION
                    )
                )
                name = f"{parent}.{node.name}"
                symbols.append(_PendingSymbol(relative, name, kind, node.lineno))
                calls.extend(
                    (relative, name, target) for target in cls._python_direct_calls(node.body)
                )
                cls._collect_python(node.body, relative, name, symbols, imports, bindings, calls)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((relative, alias.name))
                    if parent == SourceStructureParser._module_name(relative, "python"):
                        bindings.append(
                            (relative, alias.asname or alias.name.split(".")[0], alias.name)
                        )
            elif isinstance(node, ast.ImportFrom):
                prefix = "." * node.level
                origin = prefix + (node.module or "")
                for alias in node.names:
                    imported = f"{origin}.{alias.name}".rstrip(".")
                    imports.append((relative, imported))
                    if alias.name != "*" and parent == SourceStructureParser._module_name(
                        relative, "python"
                    ):
                        bindings.append((relative, alias.asname or alias.name, imported))

    @staticmethod
    def _python_direct_calls(body: list[ast.stmt]) -> tuple[str, ...]:
        visitor = _DirectCallVisitor()
        for statement in body:
            visitor.visit(statement)
        return tuple(visitor.targets)

    def _parse_tree_sitter(
        self,
        language: str,
        raw: bytes,
        relative: str,
        module: str,
        symbols: list[_PendingSymbol],
        imports: list[tuple[str, str]],
        bindings: list[tuple[str, str, str]],
        default_exports: list[tuple[str, str]],
        re_exports: list[tuple[str, str, str]],
        wildcard_re_exports: list[tuple[str, str]],
        static_methods: list[tuple[str, str]],
        calls: list[tuple[str, str, str]],
    ) -> None:
        rules = next(rule for rule in _TREE_SITTER_RULES if rule.name == language)
        parser = Parser(Language(rules.language()))
        root = parser.parse(raw).root_node
        if root.has_error:
            raise SourceStructureError(f"MNEMO_SOURCE_{language.upper()}_INVALID")
        declaration_kinds = dict(rules.declaration_kinds)

        def visit(node: Node, parent: str) -> None:
            kind = declaration_kinds.get(node.type)
            name = self._declaration_name(node, raw)
            next_parent = parent
            if kind is not None and name is not None:
                qualified = f"{parent}.{name}"
                symbols.append(_PendingSymbol(relative, qualified, kind, node.start_point.row + 1))
                next_parent = qualified
            if language in {"javascript", "typescript", "tsx"} and node.type == "export_statement":
                default_export = self._explicit_default_export(node, raw, module)
                if default_export is not None:
                    default_exports.append((relative, default_export))
                re_exports.extend(self._tree_re_export_bindings(node, raw, relative))
                wildcard_re_exports.extend(self._tree_wildcard_re_exports(node, raw, relative))
            if (
                language in {"javascript", "typescript", "tsx"}
                and node.type == "method_definition"
                and any(child.type == "static" for child in node.children)
                and name is not None
            ):
                static_methods.append((relative, f"{parent}.{name}"))
            if node.type in rules.import_kinds:
                if language == "rust":
                    for rust_target, rust_binding in self._rust_import_entries(node, raw):
                        imports.append((relative, rust_target))
                        bindings.append(
                            (relative, rust_binding, rust_target.removeprefix("crate."))
                        )
                elif language == "php":
                    for php_target, php_binding in self._php_import_entries(node, raw):
                        imports.append((relative, php_target))
                        if php_binding is not None:
                            bindings.append((relative, php_binding, php_target.replace("\\", ".")))
                else:
                    import_target = self._import_target(language, node, raw)
                    if import_target is not None:
                        imports.append((relative, import_target))
                        bindings.extend(
                            (relative, binding, encoded_target)
                            for binding, encoded_target in self._tree_import_bindings(
                                language, node, raw, import_target
                            )
                        )
            if (
                language in {"javascript", "typescript", "tsx"}
                and node.type == "variable_declarator"
                and parent == module
            ):
                variable_function = self._static_top_level_variable_function(node, raw)
                if variable_function is not None:
                    qualified = f"{module}.{variable_function}"
                    symbols.append(
                        _PendingSymbol(
                            relative,
                            qualified,
                            CodeSymbolKind.FUNCTION,
                            node.start_point.row + 1,
                        )
                    )
                    next_parent = qualified
                # Support only the direct, top-level CommonJS spelling
                # ``const local = require('./local')`` and object destructuring from the
                # same literal call.  A computed module name, reassigned variable, nested
                # declaration, or any other dynamic module-loading pattern stays absent from
                # this static projection.  This deliberately mirrors the scope and certainty
                # of the existing ES-module bindings.
                commonjs = self._commonjs_require_bindings(node, raw)
                if commonjs is not None:
                    commonjs_target, commonjs_bindings = commonjs
                    imports.append((relative, commonjs_target))
                    bindings.extend(
                        (relative, binding, f"{commonjs_target}|{member}")
                        for binding, member in commonjs_bindings
                    )
            call_fields = dict(rules.call_kinds)
            if node.type in call_fields and parent != module:
                call_target = self._call_target(node, raw, call_fields[node.type])
                if call_target is not None:
                    calls.append((relative, parent, call_target))
            for child in node.named_children:
                visit(child, next_parent)

        visit(root, module)

    @staticmethod
    def _static_top_level_variable_function(node: Node, raw: bytes) -> str | None:
        """Return a direct top-level ``const name = function/arrow`` binding, if proven.

        A lexical ``const`` avoids treating a later reassignment as a stable declaration.  The
        initializer must be exactly an arrow or function expression: conditional factories,
        object properties, destructuring, nested bindings, and ``let``/``var`` are value-flow
        territory and deliberately do not become source symbols.
        """
        declaration = node.parent
        value = node.child_by_field_name("value")
        if (
            declaration is None
            or declaration.type != "lexical_declaration"
            or not any(child.type == "const" for child in declaration.children)
            or value is None
            or value.type not in {"arrow_function", "function_expression"}
        ):
            return None
        return SourceStructureParser._declaration_name(node, raw)

    @staticmethod
    def _commonjs_require_bindings(
        node: Node, raw: bytes
    ) -> tuple[str, tuple[tuple[str, str], ...]] | None:
        """Return only syntactically direct CommonJS bindings from one declaration.

        A binding is intentionally accepted only when its initializer is exactly one literal
        ``require`` call.  Mnemo does not evaluate a variable, follow a conditional require, or
        attempt to model CommonJS export mutation.  The empty member marker represents the
        imported module itself and is resolved by the existing module-binding path.
        """
        value = node.child_by_field_name("value")
        if value is None or value.type != "call_expression":
            return None
        function = _safe_tree_text(value.child_by_field_name("function"), raw)
        arguments = value.child_by_field_name("arguments")
        if function != "require" or arguments is None or len(arguments.named_children) != 1:
            return None
        target = _string_literal(arguments.named_children[0], raw)
        if target is None:
            return None
        name = node.child_by_field_name("name")
        if name is None:
            return None
        if name.type == "identifier":
            binding = _safe_tree_text(name, raw)
            return None if binding is None else (target, ((binding, ""),))
        if name.type != "object_pattern":
            return None
        bindings: list[tuple[str, str]] = []
        for child in name.named_children:
            if child.type == "shorthand_property_identifier_pattern":
                member = _safe_tree_text(child, raw)
                if member is None:
                    return None
                bindings.append((member, member))
                continue
            if child.type != "pair_pattern":
                return None
            member = _safe_tree_text(child.child_by_field_name("key"), raw)
            binding = _safe_tree_text(child.child_by_field_name("value"), raw)
            if member is None or binding is None:
                return None
            bindings.append((binding, member))
        return (target, tuple(bindings)) if bindings else None

    @staticmethod
    def _rust_import_entries(node: Node, raw: bytes) -> tuple[tuple[str, str], ...]:
        """Return exact, explicit Rust import members and their local bindings.

        This supports one direct member or a flat ``use crate::path::{member as alias, member}``
        list. Wildcards, ``self``, nested groups, and non-``crate`` paths remain unresolved: they
        need broader module visibility or import semantics that this static projection does not
        model. Every returned item still needs one unique in-snapshot declaration before it can
        form a call edge.
        """
        argument = node.child_by_field_name("argument")
        if argument is None:
            return ()
        if argument.type == "use_as_clause":
            path = SourceStructureParser._tree_static_target(
                argument.child_by_field_name("path"), raw
            )
            alias = _safe_tree_text(argument.child_by_field_name("alias"), raw)
            return SourceStructureParser._rust_import_entry(path, alias)
        if argument.type == "scoped_identifier":
            return SourceStructureParser._rust_import_entry(
                SourceStructureParser._tree_static_target(argument, raw), None
            )
        if argument.type != "scoped_use_list":
            return ()
        prefix = SourceStructureParser._tree_static_target(
            argument.child_by_field_name("path"), raw
        )
        use_list = argument.child_by_field_name("list")
        if prefix is None or use_list is None or not prefix.startswith("crate."):
            return ()
        entries: list[tuple[str, str]] = []
        for item in use_list.named_children:
            if item.type == "identifier":
                entries.extend(
                    SourceStructureParser._rust_import_entry(
                        f"{prefix}.{_safe_tree_text(item, raw) or ''}", None
                    )
                )
            elif item.type == "use_as_clause":
                member = _safe_tree_text(item.child_by_field_name("path"), raw)
                alias = _safe_tree_text(item.child_by_field_name("alias"), raw)
                if member is not None and "." not in member:
                    entries.extend(
                        SourceStructureParser._rust_import_entry(f"{prefix}.{member}", alias)
                    )
        return tuple(entries)

    @staticmethod
    def _rust_import_entry(target: str | None, alias: str | None) -> tuple[tuple[str, str], ...]:
        if target is None or not target.startswith("crate."):
            return ()
        normalized = target.removeprefix("crate.")
        parts = normalized.split(".")
        if len(parts) < 2 or not all(_is_identifier_part(part) for part in parts):
            return ()
        binding = alias or parts[-1]
        return ((target, binding),) if _is_safe_symbol_name(binding) else ()

    @staticmethod
    def _php_import_entries(node: Node, raw: bytes) -> tuple[tuple[str, str | None], ...]:
        """Return exact PHP import targets with a callable/type binding where applicable.

        Flat grouped imports are represented member-by-member so each import and any later static
        call remains independently evidenced. ``use const`` retains import evidence but never
        creates a call binding; wildcard and malformed forms are absent rather than guessed.
        """
        is_const = any(child.type == "const" for child in node.children)
        group = node.child_by_field_name("body")
        if group is None:
            clause = next(
                (child for child in node.named_children if child.type == "namespace_use_clause"),
                None,
            )
            is_const = is_const or any(
                child.type == "const" for child in (clause.children if clause is not None else ())
            )
            target = SourceStructureParser._php_qualified_target(clause, raw)
            alias = _safe_tree_text(clause.child_by_field_name("alias"), raw) if clause else None
            return SourceStructureParser._php_import_entry(target, alias, is_const)
        prefix = _safe_tree_text(
            next((child for child in node.named_children if child.type == "namespace_name"), None),
            raw,
        )
        if prefix is None:
            return ()
        entries: list[tuple[str, str | None]] = []
        for clause in group.named_children:
            if clause.type != "namespace_use_clause":
                continue
            member = _safe_tree_text(
                clause.named_children[0] if clause.named_children else None, raw
            )
            alias = _safe_tree_text(clause.child_by_field_name("alias"), raw)
            if member is not None:
                entries.extend(
                    SourceStructureParser._php_import_entry(f"{prefix}\\{member}", alias, is_const)
                )
        return tuple(entries)

    @staticmethod
    def _php_qualified_target(clause: Node | None, raw: bytes) -> str | None:
        qualified_name = next(
            (
                child
                for child in (clause.named_children if clause is not None else ())
                if child.type == "qualified_name"
            ),
            None,
        )
        return _safe_tree_text(qualified_name, raw)

    @staticmethod
    def _php_import_entry(
        target: str | None, alias: str | None, is_const: bool
    ) -> tuple[tuple[str, str | None], ...]:
        if target is None:
            return ()
        parts = target.split("\\")
        if len(parts) < 2 or not all(_is_identifier_part(part) for part in parts):
            return ()
        binding = None if is_const else alias or parts[-1]
        return ((target, binding),) if binding is None or _is_safe_symbol_name(binding) else ()

    @staticmethod
    def _tree_import_bindings(
        language: str, node: Node, raw: bytes, target: str
    ) -> tuple[tuple[str, str], ...]:
        """Extract only explicit import aliases with a proven static spelling.

        Java class imports and simple Rust ``use crate::...`` items are intentionally
        supported beside ES-module aliases. A later lookup still requires one matching
        in-snapshot declaration before any call edge is resolved.
        """
        if language == "java":
            binding_name = target.rsplit(".", maxsplit=1)[-1]
            if not _is_safe_symbol_name(binding_name):
                return ()
            is_static = any(child.type == "static" for child in node.children)
            encoded_target = f"java-static:{target}" if is_static else target
            return ((binding_name, encoded_target),)
        if language == "go":
            alias = _safe_tree_text(node.child_by_field_name("name"), raw)
            # A missing Go import alias means the final path component is the
            # package spelling. Dot/blank imports deliberately stay unresolved.
            binding_name = alias or target.rsplit("/", maxsplit=1)[-1]
            return ((binding_name, f"go:{target}|"),) if _is_safe_symbol_name(binding_name) else ()
        if language == "csharp":
            # ``using Namespace.Type;`` gives one explicit type spelling. ``using Alias =
            # Namespace.Type;`` has the same safe spelling when the alias is explicit; a later
            # lookup still requires exactly one in-snapshot target. Namespace-only imports stay
            # unresolved because they need type inference.
            parts = target.split(".")
            if len(parts) < 2 or not all(_is_identifier_part(part) for part in parts):
                return ()
            if any(child.type == "static" for child in node.children):
                # ``using static Namespace.Type;`` imports the statically named members of one
                # exact type. The wildcard is private parser state, never a public symbol or
                # edge; resolution below still requires one exact local class and member.
                return (("*", f"csharp-static:{target}"),)
            alias = _safe_tree_text(node.child_by_field_name("name"), raw)
            return ((alias or parts[-1], target),)
        if language not in {"javascript", "typescript", "tsx"}:
            return ()
        clause = next(
            (child for child in node.named_children if child.type == "import_clause"), None
        )
        if clause is None:
            return ()
        result: list[tuple[str, str]] = []
        for child in clause.named_children:
            if child.type == "identifier":
                binding = _safe_tree_text(child, raw)
                if binding is not None:
                    result.append((binding, f"{target}|default"))
            elif child.type == "named_imports":
                for specifier in child.named_children:
                    if specifier.type != "import_specifier":
                        continue
                    name = _safe_tree_text(specifier.child_by_field_name("name"), raw)
                    alias = _safe_tree_text(specifier.child_by_field_name("alias"), raw)
                    if name is not None:
                        result.append((alias or name, f"{target}|{name}"))
            elif child.type == "namespace_import":
                binding = _safe_tree_text(
                    child.named_children[0] if child.named_children else None, raw
                )
                if binding is not None:
                    result.append((binding, f"{target}|"))
        return tuple(result)

    @staticmethod
    def _tree_re_export_bindings(
        node: Node, raw: bytes, relative: str
    ) -> tuple[tuple[str, str, str], ...]:
        """Return only explicit local ES-module re-export aliases.

        ``export { member as alias } from './module'`` is a useful, syntactically
        complete barrel relationship.  Wildcard exports, local-value re-exports,
        and non-local package references require module/value-flow semantics and
        remain unresolved.  The encoded target shares the proven import binding
        representation; it never becomes a public symbol or edge by itself.
        """
        source = _string_literal(node.child_by_field_name("source"), raw)
        clause = next(
            (child for child in node.named_children if child.type == "export_clause"), None
        )
        if source is None or clause is None or not source.startswith(("./", "../")):
            return ()
        result: list[tuple[str, str, str]] = []
        for specifier in clause.named_children:
            if specifier.type != "export_specifier":
                continue
            name = _safe_tree_text(specifier.child_by_field_name("name"), raw)
            alias = _safe_tree_text(specifier.child_by_field_name("alias"), raw)
            exported_name = alias or name
            if (
                name is None
                or exported_name is None
                or not _is_safe_symbol_name(exported_name)
                or (name != "default" and not _is_safe_symbol_name(name))
            ):
                continue
            result.append((relative, exported_name, f"{source}|{name}"))
        return tuple(result)

    @staticmethod
    def _tree_wildcard_re_exports(
        node: Node, raw: bytes, relative: str
    ) -> tuple[tuple[str, str], ...]:
        """Return a literal local ``export * from`` relationship, if present.

        The relationship is private parser state, not a public claim that every
        eventual export is known.  A later lookup resolves one requested member
        only when the bounded local export chain proves exactly one declaration.
        """
        source = _string_literal(node.child_by_field_name("source"), raw)
        if source is None or not source.startswith(("./", "../")):
            return ()
        return ((relative, source),) if any(child.type == "*" for child in node.children) else ()

    @staticmethod
    def _resolve_re_export_target(
        module: CodeSymbol,
        member: str,
        modules_by_name: dict[str, tuple[CodeSymbol, ...]],
        modules_by_path: dict[str, CodeSymbol],
        symbols_by_name: dict[str, tuple[CodeSymbol, ...]],
        default_exports_by_path: dict[str, tuple[CodeSymbol, ...]],
        re_exports_by_path: dict[str, dict[str, tuple[str, ...]]],
        wildcard_re_exports_by_path: dict[str, tuple[str, ...]],
    ) -> CodeSymbol | None:
        """Follow one unambiguous, explicit local barrel-export chain.

        Bounded iteration prevents a malformed export cycle from becoming a
        parser loop.  Every hop remains a literal relative module and exact
        exported member; ambiguity intentionally produces no claimed call edge.
        """
        pending = [(module, member)]
        visited: set[tuple[str, str]] = set()
        candidates: dict[CodeSymbolId, CodeSymbol] = {}
        for _ in range(16):
            if not pending:
                break
            current_module, current_member = pending.pop(0)
            key = (current_module.relative_path, current_member)
            if key in visited:
                continue
            visited.add(key)
            encoded_targets = re_exports_by_path.get(current_module.relative_path, {}).get(
                current_member, ()
            )
            if encoded_targets:
                if len(set(encoded_targets)) != 1:
                    continue
                targets = encoded_targets
            elif current_member != "default":
                targets = wildcard_re_exports_by_path.get(current_module.relative_path, ())
            else:
                continue
            for encoded_target in targets:
                import_target, marker, exported_member = encoded_target.partition("|")
                if not marker:
                    exported_member = current_member
                target_id = SourceStructureParser._resolve_import_target(
                    current_module.relative_path, import_target, modules_by_name, modules_by_path
                )
                target_module = next(
                    (item for item in modules_by_path.values() if item.symbol_id == target_id), None
                )
                if target_module is None:
                    continue
                if exported_member == "default":
                    default_export = _single_symbol(
                        default_exports_by_path.get(target_module.relative_path, ())
                    )
                    if default_export is not None:
                        candidates[default_export.symbol_id] = default_export
                    continue
                direct = _single_symbol(
                    symbols_by_name.get(f"{target_module.qualified_name}.{exported_member}", ())
                )
                if direct is not None:
                    candidates[direct.symbol_id] = direct
                else:
                    pending.append((target_module, exported_member))
        return next(iter(candidates.values())) if len(candidates) == 1 else None

    @staticmethod
    def _explicit_default_export(node: Node, raw: bytes, module: str) -> str | None:
        """Return one explicit named JS/TS default declaration, never an inferred export.

        This deliberately accepts only ``export default function Name`` and ``export default
        class Name``. Anonymous defaults, re-exports, and ``export default existing_name`` need
        broader module/value-flow semantics and therefore remain unresolved.
        """
        if not any(child.type == "default" for child in node.children):
            return None
        declaration = node.child_by_field_name("declaration")
        if declaration is None or declaration.type not in {
            "function_declaration",
            "class_declaration",
        }:
            return None
        name = SourceStructureParser._declaration_name(declaration, raw)
        return None if name is None else f"{module}.{name}"

    @staticmethod
    def _declaration_name(node: Node, raw: bytes) -> str | None:
        name = node.child_by_field_name("name")
        if name is None and node.type == "function_definition":
            name = _first_identifier(node.child_by_field_name("declarator"))
        if name is None:
            return None
        value = raw[name.start_byte : name.end_byte].decode("utf-8", errors="strict")
        return value if _is_safe_symbol_name(value) else None

    @staticmethod
    def _import_target(language: str, node: Node, raw: bytes) -> str | None:
        if language in {"javascript", "typescript", "tsx"}:
            source = node.child_by_field_name("source")
            return _string_literal(source, raw)
        if language == "go":
            return _string_literal(node.child_by_field_name("path"), raw)
        if language == "rust":
            argument = node.child_by_field_name("argument")
            if argument is not None and argument.type == "use_as_clause":
                argument = argument.child_by_field_name("path")
            return SourceStructureParser._tree_static_target(argument, raw)
        if language in {"c", "cpp"}:
            return _include_literal(node.child_by_field_name("path"), raw)
        if language == "csharp":
            # A directive with an explicit alias has two named children: the alias and the
            # qualified namespace/type.  The latter, never the alias, is the imported target.
            return _safe_tree_text(node.named_children[-1] if node.named_children else None, raw)
        if language == "java":
            return SourceStructureParser._tree_static_target(node.named_children[0], raw)
        return None

    @staticmethod
    def _call_target(node: Node, raw: bytes, field: str | None) -> str | None:
        if field is not None:
            return SourceStructureParser._tree_static_target(node.child_by_field_name(field), raw)
        if node.type in {"method_invocation", "member_call_expression", "scoped_call_expression"}:
            object_field = "scope" if node.type == "scoped_call_expression" else "object"
            object_name = SourceStructureParser._tree_static_target(
                node.child_by_field_name(object_field), raw
            )
            method_name = SourceStructureParser._tree_static_target(
                node.child_by_field_name("name"), raw
            )
            if method_name is None:
                return None
            return method_name if object_name is None else f"{object_name}.{method_name}"
        return None

    @staticmethod
    def _tree_static_target(node: Node | None, raw: bytes) -> str | None:
        if node is None:
            return None
        simple = {
            "identifier",
            "property_identifier",
            "field_identifier",
            "type_identifier",
            "namespace_identifier",
            "crate",
            "self",
            "super",
            "this",
            "name",
            "variable_name",
        }
        if node.type in simple:
            value = raw[node.start_byte : node.end_byte].decode("utf-8", errors="strict")
            return value if _is_safe_symbol_name(value) else None
        fields = {
            "member_expression": ("object", "property"),
            "selector_expression": ("operand", "field"),
            "member_access_expression": ("expression", "name"),
            "qualified_name": ("qualifier", "name"),
            "qualified_identifier": ("scope", "name"),
        }.get(node.type)
        if node.type == "field_expression":
            fields = ("value", "field")
            if node.child_by_field_name("value") is None:
                fields = ("argument", "field")
        if node.type == "scoped_identifier":
            fields = ("path", "name")
            if node.child_by_field_name("path") is None:
                fields = ("scope", "name")
        if fields is None:
            return None
        left = SourceStructureParser._tree_static_target(node.child_by_field_name(fields[0]), raw)
        right = SourceStructureParser._tree_static_target(node.child_by_field_name(fields[1]), raw)
        return None if left is None or right is None else f"{left}.{right}"


def _symbols_by_name(symbols: Iterable[CodeSymbol]) -> dict[str, tuple[CodeSymbol, ...]]:
    """Index every candidate instead of accidentally overwriting duplicate names."""
    grouped: dict[str, list[CodeSymbol]] = {}
    for symbol in symbols:
        grouped.setdefault(symbol.qualified_name, []).append(symbol)
    return {
        name: tuple(
            sorted(
                values,
                key=lambda item: (item.relative_path, item.line, str(item.symbol_id)),
            )
        )
        for name, values in grouped.items()
    }


def _symbols_by_location(
    symbols: tuple[CodeSymbol, ...],
) -> dict[tuple[str, str], tuple[CodeSymbol, ...]]:
    """Preserve overload ambiguity rather than attributing calls to an arbitrary symbol."""
    grouped: dict[tuple[str, str], list[CodeSymbol]] = {}
    for symbol in symbols:
        grouped.setdefault((symbol.relative_path, symbol.qualified_name), []).append(symbol)
    return {
        key: tuple(sorted(values, key=lambda item: (item.line, str(item.symbol_id))))
        for key, values in grouped.items()
    }


def _single_symbol(candidates: tuple[CodeSymbol, ...]) -> CodeSymbol | None:
    return candidates[0] if len(candidates) == 1 else None


class _DirectCallVisitor(ast.NodeVisitor):
    """Collect only explicit name/attribute calls; nested definitions own their calls."""

    def __init__(self) -> None:
        self.targets: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        target = _python_call_target(node.func)
        if target is not None:
            self.targets.append(target)
        self.generic_visit(node)


def _python_call_target(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _python_call_target(node.value)
        return None if parent is None else f"{parent}.{node.attr}"
    return None


def _string_literal(node: Node | None, raw: bytes) -> str | None:
    if node is None:
        return None
    value = raw[node.start_byte : node.end_byte].decode("utf-8", errors="strict")
    if len(value) < 3 or value[0] not in {"'", '"', "`"} or value[-1] != value[0]:
        return None
    target = value[1:-1]
    return target if target and len(target) <= 512 else None


def _include_literal(node: Node | None, raw: bytes) -> str | None:
    if node is None:
        return None
    value = raw[node.start_byte : node.end_byte].decode("utf-8", errors="strict")
    if len(value) < 3 or value[0] not in {'"', "<"}:
        return None
    closing = '"' if value[0] == '"' else ">"
    target = value[1:-1] if value[-1] == closing else ""
    return target if target and len(target) <= 512 else None


def _safe_tree_text(node: Node | None, raw: bytes) -> str | None:
    if node is None:
        return None
    value = raw[node.start_byte : node.end_byte].decode("utf-8", errors="strict")
    return value if _is_safe_symbol_name(value) else None


def _first_identifier(node: Node | None) -> Node | None:
    if node is None:
        return None
    if node.type in {"identifier", "field_identifier"}:
        return node
    for child in node.named_children:
        candidate = _first_identifier(child)
        if candidate is not None:
            return candidate
    return None


def _is_safe_symbol_name(value: str) -> bool:
    return bool(value) and len(value) <= 512 and "\x00" not in value and "\n" not in value


def _is_identifier_part(value: str) -> bool:
    """Keep binding names to ordinary static identifier components only."""
    return bool(value) and len(value) <= 128 and value.isidentifier()

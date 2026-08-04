"""Offline, deterministic source-structure parsing across supported languages.

The parser reads only source bytes from a caller-selected local root.  It never
imports project modules, evaluates code, loads project plug-ins, reads an
environment variable, or persists source text.  Each built-in adapter records
only declarations plus syntactically explicit import and call targets.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
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
        (("function_call_expression", "function"), ("member_call_expression", None)),
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

    def parse(self, request: SourceStructureParseRequest) -> CodeStructureArtifact:
        paths = self._paths(request)
        digest = sha256()
        pending: list[_PendingSymbol] = []
        files: list[tuple[str, str]] = []
        imports: list[tuple[str, str]] = []
        bindings: list[tuple[str, str, str]] = []
        calls: list[tuple[str, str, str]] = []
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
            language = self._suffixes[path.suffix.lower()]
            module = self._module_name(relative, language)
            pending.append(_PendingSymbol(relative, module, CodeSymbolKind.MODULE, 1))
            if language == "python":
                self._parse_python(raw, relative, module, pending, imports, bindings, calls)
            else:
                self._parse_tree_sitter(
                    language, raw, relative, module, pending, imports, bindings, calls
                )
        if len(pending) > request.limits.max_symbols:
            raise SourceStructureError("MNEMO_SOURCE_SYMBOL_LIMIT")
        source_digest = f"sha256:{digest.hexdigest()}"
        snapshot_id = CodeSnapshotId(
            uuid5(_SNAPSHOT_NAMESPACE, f"{request.scope.to_dict()}:{source_digest}")
        )
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
        module_names = {symbol.qualified_name: symbol for symbol in modules.values()}
        symbols_by_location = {
            (symbol.relative_path, symbol.qualified_name): symbol for symbol in symbols
        }
        symbols_by_name = {symbol.qualified_name: symbol for symbol in symbols}
        edges = tuple(
            sorted(
                (
                    *(
                        CodeEdge(
                            snapshot_id,
                            modules[path].symbol_id,
                            target,
                            CodeEdgeKind.IMPORTS,
                            self._resolve_import_target(path, target, module_names, modules),
                        )
                        for path, target in sorted(set(imports))
                    ),
                    *(
                        CodeEdge(
                            snapshot_id,
                            symbols_by_location[(path, qualified_name)].symbol_id,
                            target,
                            CodeEdgeKind.CALLS,
                            self._resolve_call_target(
                                path,
                                qualified_name,
                                target,
                                module_names,
                                modules,
                                symbols_by_name,
                                imports,
                                bindings,
                            ),
                        )
                        for path, qualified_name, target in sorted(set(calls))
                    ),
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
            and path.suffix.lower() in self._suffixes
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
    def _resolve_import_target(
        source_path: str,
        target: str,
        modules_by_name: dict[str, CodeSymbol],
        modules_by_path: dict[str, CodeSymbol],
    ) -> CodeSymbolId | None:
        """Resolve only an unambiguous import to a module in this snapshot.

        Package roots, aliases, generated modules, external dependencies, and
        members are deliberately not guessed. An unresolved string target remains
        evidence, but is not represented as an internal graph link.
        """
        direct = modules_by_name.get(target)
        if direct is not None:
            return direct.symbol_id
        parts = target.split(".")
        for end in range(len(parts) - 1, 0, -1):
            candidate = modules_by_name.get(".".join(parts[:end]))
            if candidate is not None:
                return candidate.symbol_id
        if target.startswith("."):
            source_parent = PurePosixPath(source_path).parent
            normalized = SourceStructureParser._normal_relative_path(
                source_parent.joinpath(PurePosixPath(target)).as_posix()
            )
        else:
            normalized = SourceStructureParser._normal_relative_path(target)
        return (
            None
            if normalized is None
            else SourceStructureParser._path_import_target(normalized, modules_by_path)
        )

    @staticmethod
    def _path_import_target(
        normalized: str, modules_by_path: dict[str, CodeSymbol]
    ) -> CodeSymbolId | None:
        candidates = [normalized]
        if "." not in PurePosixPath(normalized).name:
            suffixes = (
                ".py",
                ".js",
                ".ts",
                ".tsx",
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
        modules_by_name: dict[str, CodeSymbol],
        modules_by_path: dict[str, CodeSymbol],
        symbols_by_name: dict[str, CodeSymbol],
        imports: list[tuple[str, str]],
        bindings: list[tuple[str, str, str]],
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
            same_module = symbols_by_name.get(f"{module.qualified_name}.{target}")
            if same_module is not None:
                candidates.append(same_module)
        direct = symbols_by_name.get(target)
        if direct is not None:
            candidates.append(direct)
        owner_name, separator, member = caller_qualified_name.rpartition(".")
        owner = symbols_by_name.get(owner_name) if separator else None
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
            sibling = symbols_by_name.get(f"{owner.qualified_name}.{sibling_name}")
            if sibling is not None:
                candidates.append(sibling)
        for _, imported_target in (item for item in imports if item[0] == source_path):
            if imported_target.endswith(f".{target}"):
                imported = symbols_by_name.get(imported_target)
                if imported is not None:
                    candidates.append(imported)
        binding_name, separator, remainder = target.partition(".")
        for _, _binding, imported_target in (
            item for item in bindings if item[0] == source_path and item[1] == binding_name
        ):
            import_target, marker, imported_member = imported_target.partition("|")
            if marker:
                if import_target.startswith("go:"):
                    member = remainder if separator else imported_member
                    candidates.extend(
                        SourceStructureParser._go_imported_member_candidates(
                            import_target.removeprefix("go:"), member, symbols_by_name
                        )
                    )
                    continue
                module_id = SourceStructureParser._resolve_import_target(
                    source_path, import_target, modules_by_name, modules_by_path
                )
                module = next(
                    (item for item in modules_by_path.values() if item.symbol_id == module_id), None
                )
                if module is None:
                    continue
                member = remainder if separator else imported_member
                candidate_name = (
                    module.qualified_name if not member else f"{module.qualified_name}.{member}"
                )
                imported_names = [candidate_name]
            else:
                imported_names = [
                    imported_target if not separator else f"{imported_target}.{remainder}"
                ]
                if separator and imported_target in modules_by_name:
                    imported_type = imported_target.rsplit(".", maxsplit=1)[-1]
                    if _is_safe_symbol_name(imported_type):
                        imported_names.append(f"{imported_target}.{imported_type}.{remainder}")
            for candidate_name in imported_names:
                imported = symbols_by_name.get(candidate_name)
                if imported is not None:
                    candidates.append(imported)
        unique = {item.symbol_id: item for item in candidates}
        return next(iter(unique)) if len(unique) == 1 else None

    @staticmethod
    def _go_imported_member_candidates(
        import_target: str, member: str, symbols_by_name: dict[str, CodeSymbol]
    ) -> tuple[CodeSymbol, ...]:
        """Resolve an exact Go package member only when its local directory is unique.

        Go imports identify directories, while Mnemo's immutable module symbols
        identify files. This deliberately creates no import-to-file graph edge.
        It may link ``alias.Member()`` only when exactly one saved declaration
        named ``Member`` lives below the exact trailing local package directory.
        """
        if not _is_safe_symbol_name(member):
            return ()
        target_parts = PurePosixPath(import_target).parts
        candidates = [
            symbol
            for symbol in symbols_by_name.values()
            if symbol.qualified_name.endswith(f".{member}")
            and len(PurePosixPath(symbol.relative_path).parent.parts) >= 2
            and len(PurePosixPath(symbol.relative_path).parent.parts) <= len(target_parts)
            and target_parts[-len(PurePosixPath(symbol.relative_path).parent.parts) :]
            == PurePosixPath(symbol.relative_path).parent.parts
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
            if node.type in rules.import_kinds:
                target = self._import_target(language, node, raw)
                if target is not None:
                    imports.append((relative, target))
                    bindings.extend(
                        (relative, binding, encoded_target)
                        for binding, encoded_target in self._tree_import_bindings(
                            language, node, raw, target
                        )
                    )
            call_fields = dict(rules.call_kinds)
            if node.type in call_fields and parent != module:
                target = self._call_target(node, raw, call_fields[node.type])
                if target is not None:
                    calls.append((relative, parent, target))
            for child in node.named_children:
                visit(child, next_parent)

        visit(root, module)

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
            return ((binding_name, target),) if _is_safe_symbol_name(binding_name) else ()
        if language == "rust" and target.startswith("crate."):
            normalized = target.removeprefix("crate.")
            binding_name = normalized.rsplit(".", maxsplit=1)[-1]
            return ((binding_name, normalized),) if _is_safe_symbol_name(binding_name) else ()
        if language == "go":
            alias = _safe_tree_text(node.child_by_field_name("name"), raw)
            # A missing Go import alias means the final path component is the
            # package spelling. Dot/blank imports deliberately stay unresolved.
            binding_name = alias or target.rsplit("/", maxsplit=1)[-1]
            return ((binding_name, f"go:{target}|"),) if _is_safe_symbol_name(binding_name) else ()
        if language not in {"javascript", "typescript", "tsx"}:
            return ()
        clause = next(
            (child for child in node.named_children if child.type == "import_clause"), None
        )
        if clause is None:
            return ()
        result: list[tuple[str, str]] = []
        for child in clause.named_children:
            if child.type == "named_imports":
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
            return SourceStructureParser._tree_static_target(
                node.child_by_field_name("argument"), raw
            )
        if language in {"c", "cpp"}:
            return _include_literal(node.child_by_field_name("path"), raw)
        if language == "csharp":
            return _safe_tree_text(node.named_children[0] if node.named_children else None, raw)
        if language == "java":
            return SourceStructureParser._tree_static_target(node.named_children[0], raw)
        if language == "php":
            clause = next(
                (child for child in node.named_children if child.type == "namespace_use_clause"),
                None,
            )
            return _safe_tree_text(clause, raw)
        return None

    @staticmethod
    def _call_target(node: Node, raw: bytes, field: str | None) -> str | None:
        if field is not None:
            return SourceStructureParser._tree_static_target(node.child_by_field_name(field), raw)
        if node.type in {"method_invocation", "member_call_expression"}:
            object_name = SourceStructureParser._tree_static_target(
                node.child_by_field_name("object"), raw
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

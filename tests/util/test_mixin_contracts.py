"""Structural contract tests for DB mixin TYPE_CHECKING stubs.

Verifies that every method declared in TYPE_CHECKING blocks across db_*.py
mixin files exists on the composed FiligreeDB class with a matching signature.
Catches silent stub drift that mypy cannot detect at the mixin level.

Also covers DBMixinProtocol declarations in db_base.py.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from typing import Any

import pytest

from filigree.core import FiligreeDB

# ---------------------------------------------------------------------------
# AST extraction helpers
# ---------------------------------------------------------------------------

_SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "filigree"

# Mixin files that may contain TYPE_CHECKING stubs
_MIXIN_FILES = [
    "db_issues.py",
    "db_planning.py",
    "db_files.py",
    "db_events.py",
    "db_meta.py",
    "db_workflow.py",
]


def _is_type_checking_guard(node: ast.If) -> bool:
    """Return True if the ``if`` node is ``if TYPE_CHECKING:``."""
    if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
        return True
    return isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"


class _StubInfo:
    """Metadata about a single TYPE_CHECKING stub."""

    __slots__ = ("is_property", "is_staticmethod", "lineno", "name", "source_file")

    def __init__(
        self,
        name: str,
        *,
        is_property: bool = False,
        is_staticmethod: bool = False,
        source_file: str = "",
        lineno: int = 0,
    ) -> None:
        self.name = name
        self.is_property = is_property
        self.is_staticmethod = is_staticmethod
        self.source_file = source_file
        self.lineno = lineno

    def __repr__(self) -> str:
        kind = "property" if self.is_property else "staticmethod" if self.is_staticmethod else "method"
        return f"_StubInfo({self.source_file}:{self.lineno} {kind} {self.name})"


def _has_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    """Check if a function/method node has a decorator with the given name."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == name:
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == name:
            return True
    return False


def _extract_stubs_from_type_checking_block(
    body: list[ast.stmt],
    source_file: str,
) -> list[_StubInfo]:
    """Extract method stubs from statements inside a TYPE_CHECKING if-block."""
    stubs: list[_StubInfo] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stubs.append(
                _StubInfo(
                    name=node.name,
                    is_property=_has_decorator(node, "property"),
                    is_staticmethod=_has_decorator(node, "staticmethod"),
                    source_file=source_file,
                    lineno=node.lineno,
                )
            )
    return stubs


def _extract_mixin_stubs(filepath: Path) -> list[_StubInfo]:
    """Parse a mixin .py file and extract all TYPE_CHECKING stubs from classes."""
    source = filepath.read_text()
    tree = ast.parse(source, filename=str(filepath))
    stubs: list[_StubInfo] = []
    filename = filepath.name

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.If) and _is_type_checking_guard(item):
                stubs.extend(_extract_stubs_from_type_checking_block(item.body, filename))
    return stubs


def _extract_protocol_declarations(filepath: Path) -> list[_StubInfo]:
    """Extract method/property declarations from Protocol classes in db_base.py."""
    source = filepath.read_text()
    tree = ast.parse(source, filename=str(filepath))
    stubs: list[_StubInfo] = []
    filename = filepath.name

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # Check if this class inherits from Protocol
        is_protocol = any(
            (isinstance(base, ast.Name) and base.id == "Protocol") or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
            for base in node.bases
        )
        if not is_protocol:
            continue

        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stubs.append(
                    _StubInfo(
                        name=item.name,
                        is_property=_has_decorator(item, "property"),
                        is_staticmethod=_has_decorator(item, "staticmethod"),
                        source_file=filename,
                        lineno=item.lineno,
                    )
                )
    return stubs


# ---------------------------------------------------------------------------
# Collect all stubs for parametrization
# ---------------------------------------------------------------------------


def _collect_all_stubs() -> list[_StubInfo]:
    """Collect all TYPE_CHECKING stubs and Protocol declarations."""
    all_stubs: list[_StubInfo] = []

    for filename in _MIXIN_FILES:
        filepath = _SRC_DIR / filename
        if filepath.exists():
            all_stubs.extend(_extract_mixin_stubs(filepath))

    # DBMixinProtocol in db_base.py
    base_path = _SRC_DIR / "db_base.py"
    if base_path.exists():
        all_stubs.extend(_extract_protocol_declarations(base_path))

    return all_stubs


_ALL_STUBS = _collect_all_stubs()


def _stub_id(stub: _StubInfo) -> str:
    """Generate a readable test ID like 'db_issues.py::_record_event'."""
    return f"{stub.source_file}::{stub.name}"


# ---------------------------------------------------------------------------
# Signature comparison helpers
# ---------------------------------------------------------------------------


def _compare_parameters(
    stub_sig: inspect.Signature,
    real_sig: inspect.Signature,
    stub: _StubInfo,
) -> list[str]:
    """Compare parameter lists between stub and real signatures.

    Returns a list of mismatch descriptions (empty = match).
    """
    errors: list[str] = []
    stub_params = list(stub_sig.parameters.values())
    real_params = list(real_sig.parameters.values())

    # Skip 'self' for instance methods (but not for staticmethods)
    if not stub.is_staticmethod:
        stub_params = [p for p in stub_params if p.name != "self"]
        real_params = [p for p in real_params if p.name != "self"]

    # Check parameter count
    if len(stub_params) != len(real_params):
        stub_names = [p.name for p in stub_params]
        real_names = [p.name for p in real_params]
        errors.append(f"Parameter count mismatch: stub has {stub_names}, real has {real_names}")
        return errors  # Can't compare further

    for s_param, r_param in zip(stub_params, real_params, strict=True):
        # Name match
        if s_param.name != r_param.name:
            errors.append(f"Parameter name: stub has '{s_param.name}', real has '{r_param.name}'")

        # Kind match (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, KEYWORD_ONLY, etc.)
        if s_param.kind != r_param.kind:
            errors.append(f"Parameter '{s_param.name}' kind: stub={s_param.kind.name}, real={r_param.kind.name}")

        # Default presence (not value — stubs use ...)
        stub_has_default = s_param.default is not inspect.Parameter.empty
        real_has_default = r_param.default is not inspect.Parameter.empty
        if stub_has_default != real_has_default:
            errors.append(
                f"Parameter '{s_param.name}' default: stub {'has' if stub_has_default else 'no'} default, "
                f"real {'has' if real_has_default else 'no'} default"
            )

    return errors


def _normalize_annotation(annotation: Any) -> str:
    """Normalize a type annotation to a comparable string.

    Handles differences between runtime annotations and stub annotations
    (e.g. forward references, string annotations vs resolved types).
    """
    if annotation is inspect.Parameter.empty:
        return "<empty>"
    # Use get_type_hints at the class level for resolved annotations,
    # but for comparison we compare the string representations
    return str(annotation)


def _compare_return_type(
    stub_sig: inspect.Signature,
    real_sig: inspect.Signature,
) -> str | None:
    """Compare return type annotations. Returns error message or None."""
    stub_ret = stub_sig.return_annotation
    real_ret = real_sig.return_annotation

    # If stub has no return annotation, skip check
    if stub_ret is inspect.Signature.empty:
        return None

    # If real has no return annotation but stub does, flag it
    if real_ret is inspect.Signature.empty:
        return f"Return type: stub annotates '{stub_ret}', real has no annotation"

    # Compare normalized string representations
    stub_str = _normalize_annotation(stub_ret)
    real_str = _normalize_annotation(real_ret)

    if stub_str != real_str:
        return f"Return type mismatch: stub='{stub_str}', real='{real_str}'"

    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stub",
    _ALL_STUBS,
    ids=[_stub_id(s) for s in _ALL_STUBS],
)
def test_stub_exists_on_filigreedb(stub: _StubInfo) -> None:
    """Every TYPE_CHECKING stub must exist on the composed FiligreeDB class."""
    assert hasattr(FiligreeDB, stub.name), (
        f"{stub.source_file}:{stub.lineno} declares stub '{stub.name}' but FiligreeDB has no such attribute"
    )


@pytest.mark.parametrize(
    "stub",
    [s for s in _ALL_STUBS if s.is_property],
    ids=[_stub_id(s) for s in _ALL_STUBS if s.is_property],
)
def test_property_stub_is_property(stub: _StubInfo) -> None:
    """@property stubs must resolve to property descriptors on FiligreeDB."""
    attr = getattr(FiligreeDB, stub.name, None)
    assert attr is not None, f"'{stub.name}' not found on FiligreeDB"
    assert isinstance(attr, property), (
        f"{stub.source_file}:{stub.lineno} declares '{stub.name}' as @property but FiligreeDB has it as {type(attr).__name__}"
    )


@pytest.mark.parametrize(
    "stub",
    [s for s in _ALL_STUBS if s.is_property],
    ids=[_stub_id(s) for s in _ALL_STUBS if s.is_property],
)
def test_property_return_type_matches(stub: _StubInfo) -> None:
    """@property stub return type annotations must match the real fget signature."""
    attr = getattr(FiligreeDB, stub.name, None)
    assert attr is not None
    assert isinstance(attr, property)
    assert attr.fget is not None, f"Property '{stub.name}' has no fget"

    # Get real return type from fget
    real_sig = inspect.signature(attr.fget)

    # Get stub return type from AST
    filepath = _SRC_DIR / stub.source_file
    source = filepath.read_text()
    tree = ast.parse(source, filename=str(filepath))

    stub_func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == stub.name and node.lineno == stub.lineno:
            stub_func = node
            break

    assert stub_func is not None
    if stub_func.returns is None:
        return  # No return annotation on stub — nothing to check

    stub_return_src = ast.get_source_segment(source, stub_func.returns) or ""
    if real_sig.return_annotation is inspect.Signature.empty:
        pytest.fail(
            f"{stub.source_file}:{stub.lineno} property '{stub.name}' stub has return type "
            f"'{stub_return_src}' but real fget has no annotation"
        )

    real_return_str = _annotation_to_source(real_sig.return_annotation)
    if _normalize_type_str(stub_return_src) != _normalize_type_str(real_return_str):
        pytest.fail(
            f"{stub.source_file}:{stub.lineno} property '{stub.name}' return type mismatch: "
            f"stub='{stub_return_src}', real='{real_return_str}'"
        )


@pytest.mark.parametrize(
    "stub",
    [s for s in _ALL_STUBS if not s.is_property],
    ids=[_stub_id(s) for s in _ALL_STUBS if not s.is_property],
)
def test_stub_signature_matches(stub: _StubInfo) -> None:
    """Stub method signatures must match the real implementation on FiligreeDB."""
    real_attr = getattr(FiligreeDB, stub.name, None)
    assert real_attr is not None, f"'{stub.name}' not found on FiligreeDB"
    assert callable(real_attr), f"'{stub.name}' on FiligreeDB is not callable (got {type(real_attr).__name__})"

    # Get the stub signature by parsing the source again
    filepath = _SRC_DIR / stub.source_file
    source = filepath.read_text()
    tree = ast.parse(source, filename=str(filepath))

    stub_func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == stub.name and node.lineno == stub.lineno:
            stub_func = node
            break

    assert stub_func is not None, f"Could not find stub AST node for {stub.name} at line {stub.lineno}"

    # Build a callable from the stub AST to get its inspect.Signature
    # We compile the function def in isolation to extract its signature
    stub_source = ast.get_source_segment(source, stub_func)
    assert stub_source is not None, f"Could not extract source for {stub.name}"

    # Dedent the source and compile it to extract signature
    stub_source = textwrap.dedent(stub_source)
    # Remove decorators for compilation — we just need the def
    stub_ns: dict[str, Any] = {}
    try:
        exec(compile(stub_source, f"<stub:{stub.name}>", "exec"), stub_ns)  # noqa: S102
    except Exception:
        # If the stub references unimported types, strip annotations and retry
        # We still check parameter names/kinds/defaults
        stripped = _strip_annotations(stub_func)
        exec(compile(stripped, f"<stub:{stub.name}>", "exec"), stub_ns)  # noqa: S102

    stub_callable = stub_ns.get(stub.name)
    assert stub_callable is not None, f"Failed to compile stub for {stub.name}"

    stub_sig = inspect.signature(stub_callable)
    real_sig = inspect.signature(real_attr)

    errors = _compare_parameters(stub_sig, real_sig, stub)
    assert not errors, f"{stub.source_file}:{stub.lineno} stub '{stub.name}' signature mismatch:\n" + "\n".join(f"  - {e}" for e in errors)


@pytest.mark.parametrize(
    "stub",
    [s for s in _ALL_STUBS if not s.is_property],
    ids=[_stub_id(s) for s in _ALL_STUBS if not s.is_property],
)
def test_stub_return_type_matches(stub: _StubInfo) -> None:
    """Stub return type annotations must match the real implementation."""
    real_attr = getattr(FiligreeDB, stub.name, None)
    assert real_attr is not None

    filepath = _SRC_DIR / stub.source_file
    source = filepath.read_text()
    tree = ast.parse(source, filename=str(filepath))

    # Find the stub's return annotation in the AST
    stub_func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == stub.name and node.lineno == stub.lineno:
            stub_func = node
            break

    assert stub_func is not None

    if stub_func.returns is None:
        return  # No return annotation on stub — nothing to check

    # Get the return annotation as source text
    stub_return_src = ast.get_source_segment(source, stub_func.returns)

    # Get the real return annotation
    real_sig = inspect.signature(real_attr)
    if real_sig.return_annotation is inspect.Signature.empty:
        pytest.fail(
            f"{stub.source_file}:{stub.lineno} stub '{stub.name}' has return type "
            f"'{stub_return_src}' but real method has no return annotation"
        )

    # Normalize real annotation to source-like string
    real_return_str = _annotation_to_source(real_sig.return_annotation)

    # Normalize stub annotation to source-like string
    stub_return_str = stub_return_src or ""

    # Normalize both for comparison (strip whitespace, normalize quotes)
    if _normalize_type_str(stub_return_str) != _normalize_type_str(real_return_str):
        pytest.fail(
            f"{stub.source_file}:{stub.lineno} stub '{stub.name}' return type mismatch: stub='{stub_return_str}', real='{real_return_str}'"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_annotations(func_node: ast.FunctionDef) -> str:
    """Build a minimal function def with no annotations (for signature extraction)."""
    defaults_offset = len(func_node.args.args) - len(func_node.args.defaults)
    kw_defaults = func_node.args.kw_defaults

    parts = []
    positional_args = func_node.args.args
    for i, arg in enumerate(positional_args):
        p = arg.arg
        default_idx = i - defaults_offset
        if default_idx >= 0 and func_node.args.defaults[default_idx] is not None:
            p += "=..."
        parts.append(p)

    # Handle *args / bare * separator
    if func_node.args.vararg:
        parts.append(f"*{func_node.args.vararg.arg}")
    elif func_node.args.kwonlyargs:
        parts.append("*")

    # Handle keyword-only args
    for i, arg in enumerate(func_node.args.kwonlyargs):
        p = arg.arg
        if i < len(kw_defaults) and kw_defaults[i] is not None:
            p += "=..."
        parts.append(p)

    # Handle **kwargs
    if func_node.args.kwarg:
        parts.append(f"**{func_node.args.kwarg.arg}")

    sig_str = ", ".join(parts)
    return f"def {func_node.name}({sig_str}): ...\n"


def _annotation_to_source(annotation: Any) -> str:
    """Convert a runtime type annotation to a source-like string."""
    if annotation is inspect.Parameter.empty or annotation is inspect.Signature.empty:
        return ""

    origin = getattr(annotation, "__origin__", None)

    # Handle None type
    if annotation is type(None):
        return "None"

    # Handle typing generics (list[X], dict[X, Y], tuple[X, ...], etc.)
    if origin is not None:
        args = getattr(annotation, "__args__", ())
        origin_name = getattr(origin, "__name__", str(origin))
        if args:
            arg_strs = [_annotation_to_source(a) for a in args]
            return f"{origin_name}[{', '.join(arg_strs)}]"
        return origin_name

    # Handle union types (X | Y)
    if hasattr(annotation, "__args__") and str(annotation).startswith("typing.Union"):
        args = annotation.__args__
        return " | ".join(_annotation_to_source(a) for a in args)

    # Handle X | Y syntax (Python 3.10+ types.UnionType)
    if type(annotation).__name__ == "UnionType":
        args = annotation.__args__
        return " | ".join(_annotation_to_source(a) for a in args)

    # Handle classes
    if isinstance(annotation, type):
        return annotation.__name__

    # Fallback
    return str(annotation)


def _normalize_type_str(s: str) -> str:
    """Normalize a type string for comparison.

    Strips whitespace around | and commas, normalizes bracket spacing.
    """
    s = s.strip()
    # Normalize whitespace around operators
    s = s.replace(" | ", "|").replace("| ", "|").replace(" |", "|")
    s = s.replace(" ,", ",").replace(", ", ",")
    s = s.replace("[ ", "[").replace(" ]", "]")
    return s


def test_all_mixin_files_scanned() -> None:
    """Ensure we're scanning all db_*.py mixin files that exist."""
    actual_mixin_files = sorted(p.name for p in _SRC_DIR.glob("db_*.py"))
    expected = sorted([*_MIXIN_FILES, "db_base.py", "db_schema.py"])
    # db_schema.py has no stubs, just schema — that's expected
    for f in actual_mixin_files:
        assert f in expected, (
            f"New mixin file '{f}' found but not included in contract test. Add it to _MIXIN_FILES if it has TYPE_CHECKING stubs."
        )


def test_stubs_discovered() -> None:
    """Sanity check: we should discover a meaningful number of stubs."""
    assert len(_ALL_STUBS) >= 20, f"Expected at least 20 stubs, found {len(_ALL_STUBS)}. AST extraction may be broken."

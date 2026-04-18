"""Package import behaviour — ``filigree/__init__.py`` must not eagerly
drag the full DB/templates stack into every import of the package.

Bug filigree-29bc9117ab: ``from filigree import migrations`` (or any other
lightweight submodule) paid for the entire mixin stack because
``__init__.py`` unconditionally imported ``FiligreeDB`` from ``filigree.core``
— which in turn imports every ``db_*`` mixin, the models module, and the
templates loader. PEP 562 module-level ``__getattr__`` keeps ``FiligreeDB``
and ``Issue`` available on the package namespace without loading them up
front.

Each case runs in a fresh subprocess because ``sys.modules`` mutation on an
already-imported package is brittle (the deletions cascade into pytest
internals that imported filigree earlier).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        check=True,
        capture_output=True,
        text=True,
    )


class TestLazyReexports:
    def test_bare_filigree_import_does_not_load_core(self) -> None:
        """``import filigree`` alone must not load ``filigree.core`` or its
        heavy mixin dependencies."""
        result = _run(
            """
            import sys
            import filigree  # noqa: F401

            forbidden = {
                "filigree.core",
                "filigree.db_issues",
                "filigree.db_files",
                "filigree.db_scans",
                "filigree.db_workflow",
                "filigree.templates",
            }
            leaked = sorted(forbidden & set(sys.modules))
            print("LEAKED:", leaked)
            """
        )
        assert "LEAKED: []" in result.stdout, result.stdout

    def test_submodule_import_does_not_load_core(self) -> None:
        """Importing a lightweight submodule (``filigree.migrations``) must
        not trigger the full DB mixin stack."""
        result = _run(
            """
            import sys
            from filigree import migrations  # noqa: F401

            # ``filigree.migrations`` legitimately imports ``filigree.db_schema``
            # for ``CURRENT_SCHEMA_VERSION`` — that's fine. The regression is
            # the mixin / templates stack being loaded transitively via
            # ``filigree.core``.
            forbidden = {
                "filigree.core",
                "filigree.db_issues",
                "filigree.db_files",
                "filigree.db_workflow",
                "filigree.templates",
            }
            leaked = sorted(forbidden & set(sys.modules))
            print("LEAKED:", leaked)
            """
        )
        assert "LEAKED: []" in result.stdout, result.stdout

    def test_public_api_still_resolves(self) -> None:
        """``filigree.FiligreeDB`` and ``filigree.Issue`` must still be
        accessible — the laziness is an optimisation, not an API break."""
        result = _run(
            """
            import filigree
            from filigree.core import FiligreeDB as DirectDB
            from filigree.models import Issue as DirectIssue

            assert filigree.FiligreeDB is DirectDB, "FiligreeDB identity mismatch"
            assert filigree.Issue is DirectIssue, "Issue identity mismatch"
            print("OK")
            """
        )
        assert result.stdout.strip().endswith("OK"), result.stdout

    def test_unknown_attribute_raises_attribute_error(self) -> None:
        """Typo / unknown attribute on the package must still raise
        ``AttributeError`` so static tools and ``hasattr`` behave correctly."""
        result = _run(
            """
            import filigree

            try:
                filigree.NotAThing  # noqa: B018
            except AttributeError as exc:
                print("RAISED:", exc)
            else:
                print("DID_NOT_RAISE")
            """
        )
        assert "RAISED:" in result.stdout, result.stdout
        assert "NotAThing" in result.stdout, result.stdout

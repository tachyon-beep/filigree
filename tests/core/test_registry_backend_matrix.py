"""Default registry-backend matrix for ADR-014 file identity behavior."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import filigree.registry as registry_module
from filigree.core import FiligreeDB
from filigree.registry import DEFAULT_TEST_REGISTRY_BACKENDS, RegistryBackend, ResolvedFile


class _DisplacedRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def resolve_file(self, path: str, *, language: str = "", actor: str = "") -> ResolvedFile:
        self.calls.append((path, language, actor))
        return {
            "file_id": f"clarion:file:{path.replace('/', ':')}",
            "content_hash": f"hash:{path}",
            "canonical_path": path,
            "language": language,
            "registry_backend": "clarion",
        }

    def is_displaced(self) -> bool:
        return True


@contextmanager
def _live_clarion_registry() -> Iterator[tuple[str, list[dict[str, list[str]]]]]:
    requests: list[dict[str, list[str]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            requests.append(query)
            assert parsed.path == "/api/v1/files"
            path = query.get("path", [""])[0]
            language = query.get("language", [""])[0]
            body = json.dumps(
                {
                    "entity_id": f"core:file:matrix@{path}",
                    "content_hash": f"sha256:{path}",
                    "canonical_path": path,
                    "language": language,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@contextmanager
def _matrix_db(tmp_path: Path, registry_backend: RegistryBackend) -> Iterator[tuple[FiligreeDB, list[dict[str, list[str]]]]]:
    if registry_backend == "clarion":
        with _live_clarion_registry() as (base_url, requests):
            db = FiligreeDB(
                tmp_path / "filigree.db",
                prefix="test",
                registry_backend="clarion",
                clarion_config={"base_url": base_url, "timeout_seconds": 1},
                project_root=tmp_path,
            )
            try:
                db.initialize()
                yield db, requests
            finally:
                db.close()
        return

    db = FiligreeDB(tmp_path / "filigree.db", prefix="test", project_root=tmp_path)
    try:
        db.initialize()
        yield db, []
    finally:
        db.close()


def _expected_file_id(registry_backend: RegistryBackend, path: str) -> str:
    return f"core:file:matrix@{path}" if registry_backend == "clarion" else ""


def _assert_registry_file_record(db: FiligreeDB, registry_backend: RegistryBackend, path: str) -> None:
    file_record = db.get_file_by_path(path)
    assert file_record is not None
    assert file_record.registry_backend == registry_backend
    if registry_backend == "clarion":
        assert file_record.id == _expected_file_id(registry_backend, path)
        assert file_record.content_hash == f"sha256:{path}"
    else:
        assert file_record.id.startswith("test-f-")
        assert file_record.content_hash == ""


def test_default_registry_backend_matrix_covers_local_and_clarion() -> None:
    assert DEFAULT_TEST_REGISTRY_BACKENDS == ("local", "clarion")
    assert not hasattr(registry_module, "SUPPORTED_REGISTRY_BACKENDS")


@pytest.mark.parametrize("registry_backend", DEFAULT_TEST_REGISTRY_BACKENDS)
def test_register_file_round_trips_default_registry_backend(tmp_path: Path, registry_backend: RegistryBackend) -> None:
    with _matrix_db(tmp_path, registry_backend) as (db, requests):
        file_record = db.register_file("src/default_backend.py", language="python")

        _assert_registry_file_record(db, registry_backend, "src/default_backend.py")
        assert file_record.id == db.get_file_by_path("src/default_backend.py").id  # type: ignore[union-attr]
        if registry_backend == "clarion":
            assert requests == [{"path": ["src/default_backend.py"], "language": ["python"]}]


@pytest.mark.parametrize("registry_backend", DEFAULT_TEST_REGISTRY_BACKENDS)
def test_scan_ingest_round_trips_default_registry_backend(tmp_path: Path, registry_backend: RegistryBackend) -> None:
    with _matrix_db(tmp_path, registry_backend) as (db, requests):
        result = db.process_scan_results(
            scan_source="ruff",
            findings=[
                {
                    "path": "src/default_backend.py",
                    "language": "python",
                    "rule_id": "E501",
                    "severity": "low",
                    "message": "Line too long",
                }
            ],
        )

        _assert_registry_file_record(db, registry_backend, "src/default_backend.py")
        finding = db.get_finding(result["new_finding_ids"][0])
        file_record = db.get_file_by_path("src/default_backend.py")
        assert file_record is not None
        assert finding["file_id"] == file_record.id
        if registry_backend == "clarion":
            assert requests == [{"path": ["src/default_backend.py"], "language": ["python"]}]


@pytest.mark.parametrize("registry_backend", DEFAULT_TEST_REGISTRY_BACKENDS)
def test_observation_file_path_round_trips_default_registry_backend(tmp_path: Path, registry_backend: RegistryBackend) -> None:
    with _matrix_db(tmp_path, registry_backend) as (db, requests):
        db.create_observation(summary="Observed", file_path="src/default_backend.py")

        _assert_registry_file_record(db, registry_backend, "src/default_backend.py")
        if registry_backend == "clarion":
            assert requests == [{"path": ["src/default_backend.py"], "language": ["python"]}]


@pytest.mark.parametrize("registry_backend", DEFAULT_TEST_REGISTRY_BACKENDS)
def test_annotation_file_path_round_trips_default_registry_backend(tmp_path: Path, registry_backend: RegistryBackend) -> None:
    with _matrix_db(tmp_path, registry_backend) as (db, requests):
        source = tmp_path / "src" / "default_backend.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("alpha\nbeta\n")

        annotation = db.annotate_file("src/default_backend.py", "Read beta", line_start=2, actor="annotator")

        _assert_registry_file_record(db, registry_backend, "src/default_backend.py")
        file_record = db.get_file_by_path("src/default_backend.py")
        assert file_record is not None
        assert annotation["file_id"] == file_record.id
        if registry_backend == "clarion":
            assert requests == [{"path": ["src/default_backend.py"], "language": ["python"]}]


def test_implicit_auto_create_paths_thread_displaced_registry_ids(tmp_path: Path) -> None:
    registry = _DisplacedRegistry()
    db = FiligreeDB(
        tmp_path / "filigree.db",
        prefix="test",
        registry=registry,
        registry_backend="clarion",
        project_root=tmp_path,
    )
    try:
        db.initialize()
        source = tmp_path / "src" / "annotated.py"
        source.parent.mkdir()
        source.write_text("alpha\nbeta\n")

        registered = db.register_file("src/registered.py", language="python", actor="direct")
        observation = db.create_observation("Observed", file_path="src/observed.py", actor="observer")
        annotation = db.annotate_file("src/annotated.py", "Read beta", line_start=2, actor="annotator")
        scan = db.process_scan_results(
            scan_source="ruff",
            findings=[
                {
                    "path": "src/scanned.py",
                    "language": "python",
                    "rule_id": "E501",
                    "severity": "low",
                    "message": "Line too long",
                }
            ],
            observation_actor="scanner",
        )

        assert registered.id == "clarion:file:src:registered.py"
        assert observation["file_id"] == "clarion:file:src:observed.py"
        assert annotation["file_id"] == "clarion:file:src:annotated.py"
        finding = db.get_finding(scan["new_finding_ids"][0])
        assert finding["file_id"] == "clarion:file:src:scanned.py"

        for path in ("src/registered.py", "src/observed.py", "src/annotated.py", "src/scanned.py"):
            file_record = db.get_file_by_path(path)
            assert file_record is not None
            assert file_record.registry_backend == "clarion"
            assert file_record.content_hash == f"hash:{path}"
    finally:
        db.close()

"""File identity registry backends."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass
from typing import Protocol, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

from filigree.types.core import EntityId, FileId, RegistryBackend, make_content_hash, make_entity_id, make_file_id

DEFAULT_TEST_REGISTRY_BACKENDS: tuple[RegistryBackend, ...] = ("local", "clarion")
REGISTRY_BACKEND_FEATURES: tuple[RegistryBackend, ...] = ("local", "clarion")


class ResolvedFile(TypedDict):
    """File identity resolved by the configured registry backend.

    ``content_hash=""`` is reserved for the local registry sentinel. Displaced
    registries must supply a non-empty hash token.
    """

    file_id: FileId | EntityId
    content_hash: str
    canonical_path: str
    language: str
    registry_backend: RegistryBackend


class RegistryProtocol(Protocol):
    """Protocol consumed by file auto-create paths."""

    def resolve_file(
        self,
        path: str,
        *,
        language: str = "",
        actor: str = "",
    ) -> ResolvedFile: ...

    def is_displaced(self) -> bool: ...


class RegistryUnavailableError(RuntimeError):
    """Raised when the configured registry backend cannot resolve a file."""

    def __init__(self, message: str, *, url: str = "", path: str = "", cause_kind: str = "unknown") -> None:
        super().__init__(message)
        self.url = url
        self.path = path
        self.cause_kind = cause_kind


class RegistryResolutionError(ValueError):
    """Raised when a reachable registry rejects a file resolution request."""

    def __init__(self, message: str, *, status_code: int, url: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class RegistryFileNotFoundError(RegistryResolutionError):
    """Raised when a reachable registry does not know the requested file."""


def clarion_file_read_url(base_url: str, path: str, *, language: str = "") -> str:
    """Build the Clarion read-API URL for an operator-facing hint."""
    query = urlencode({"path": path, "language": language})
    return f"{base_url.rstrip('/')}/api/v1/files?{query}"


def normalize_clarion_base_url(base_url: str) -> str:
    """Validate and canonicalize a Clarion registry base URL."""
    if not isinstance(base_url, str) or not base_url.strip():
        msg = f"clarion.base_url must be a non-empty http(s) URL with a host, got {base_url!r}"
        raise ValueError(msg)
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.hostname is None:
        msg = f"clarion.base_url must be a non-empty http(s) URL with a host, got {base_url!r}"
        raise ValueError(msg)
    return normalized


class LocalRegistry:
    """Filigree-native registry backend."""

    def __init__(self, file_id_factory: Callable[[], str]) -> None:
        self._file_id_factory = file_id_factory

    def resolve_file(
        self,
        path: str,
        *,
        language: str = "",
        actor: str = "",
    ) -> ResolvedFile:
        return ResolvedFile(
            file_id=make_file_id(self._file_id_factory()),
            content_hash="",
            canonical_path=path,
            language=language,
            registry_backend="local",
        )

    def is_displaced(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ClarionRegistry:
    """HTTP-backed registry that resolves file identity through Clarion."""

    base_url: str
    _: KW_ONLY
    timeout_seconds: float = 5

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", normalize_clarion_base_url(self.base_url))
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, int | float) or self.timeout_seconds <= 0:
            msg = f"clarion.timeout_seconds must be a positive number, got {self.timeout_seconds!r}"
            raise ValueError(msg)
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    def resolve_file(
        self,
        path: str,
        *,
        language: str = "",
        actor: str = "",
    ) -> ResolvedFile:
        url = clarion_file_read_url(self.base_url, path, language=language)
        try:
            with urlopen(url, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            reason = exc.reason or exc.msg
            if exc.code == 404:
                msg = f"Clarion registry could not resolve file at {url}: HTTP 404 {reason}"
                raise RegistryFileNotFoundError(msg, status_code=exc.code, url=url) from exc
            if 400 <= exc.code < 500:
                msg = f"Clarion registry rejected file resolution at {url}: HTTP {exc.code} {reason}"
                raise RegistryResolutionError(msg, status_code=exc.code, url=url) from exc
            msg = f"Clarion registry unavailable at {url}: HTTP {exc.code} {reason}"
            raise RegistryUnavailableError(msg, url=url, path=path, cause_kind="http_error") from exc
        except (URLError, TimeoutError, OSError) as exc:
            msg = f"Clarion registry unavailable at {url}: {exc}"
            raise RegistryUnavailableError(msg, url=url, path=path, cause_kind="network") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            msg = f"Clarion registry returned invalid JSON from {url}: {exc}"
            raise RegistryUnavailableError(msg, url=url, path=path, cause_kind="invalid_response") from exc
        if not isinstance(payload, dict):
            msg = f"Clarion registry returned non-object response from {url}: {type(payload).__name__}"
            raise RegistryUnavailableError(msg, url=url, path=path, cause_kind="invalid_response")

        required = ("entity_id", "content_hash", "canonical_path", "language")
        missing = [field for field in required if not isinstance(payload.get(field), str)]
        if missing:
            msg = f"Clarion registry response from {url} missing string field(s): {', '.join(missing)}"
            raise RegistryUnavailableError(msg, url=url, path=path, cause_kind="invalid_response")
        try:
            content_hash = make_content_hash(payload["content_hash"])
        except ValueError as exc:
            msg = f"Clarion registry response from {url} has invalid content_hash: {exc}"
            raise RegistryUnavailableError(msg, url=url, path=path, cause_kind="invalid_response") from exc

        return ResolvedFile(
            file_id=make_entity_id(payload["entity_id"]),
            content_hash=content_hash,
            canonical_path=payload["canonical_path"],
            language=payload["language"],
            registry_backend="clarion",
        )

    def is_displaced(self) -> bool:
        return True

"""Shared fixtures and test doubles.

Fakes live here so every test file gets the same vocabulary:

  FakeHttpClient  programmed stand-in for pkit.lib.http.HttpClient
  FakeRclone      stand-in for pkit.lib.shell.run when rclone runs
  FakeBackend     in-memory archive remote (list/copy/verify/fetch)
  http_server     a real local HTTP server (transport tests without mocks)

Only boundaries pkit does not own are faked: the network transport, the
rclone binary, and remote backends. sqlite and the filesystem stay real.
"""

import http.server
import json
import subprocess
import sys
import threading
from collections.abc import Callable, Generator, Iterator, Sequence
from json import dumps
from pathlib import Path
from typing import Any, Self

import pytest
import requests

from pkit import archive
from pkit.archive.cas import Entry, Hashes, hash_file
from pkit.archive.remote import RemoteFile, Status
from pkit.lib.http import HttpClient, HttpError
from pkit.wayback.client import WaybackClient


class FakeStdin:
    """Minimal fake stdin for direct CLI tests."""

    def __init__(self, text: str = "", *, is_tty: bool = False) -> None:
        self._text = text
        self._is_tty = is_tty

    def read(self) -> str:
        return self._text

    def isatty(self) -> bool:
        return self._is_tty


class FakeResponse(requests.Response):
    """A real requests.Response built from kwargs; no socket involved.

    json= sets a JSON body, text=/content= set a raw one. .json() then
    raises the real JSONDecodeError (a ValueError) on non-JSON bodies, which
    is exactly the contract SDKs rely on.
    """

    def __init__(
        self,
        *,
        status_code: int = 200,
        json: Any = None,
        text: str = "",
        content: bytes = b"",
    ) -> None:
        super().__init__()
        self.status_code = status_code
        self.encoding = "utf-8"
        if json is not None:
            self._content = dumps(json).encode()
            self.headers["Content-Type"] = "application/json"
        elif content:
            self._content = content
        else:
            self._content = text.encode()


class FakeHttpClient(HttpClient):
    """Programmable HttpClient stand-in: same contract, no network.

    Subclassing keeps the type honest wherever an HttpClient is accepted.
    on(url, ...) registers a response for every request whose URL starts with
    url (longest prefix wins). enqueue(...) queues responses in request order
    for sequence-sensitive flows. An `error` is raised verbatim; any status
    >= 400 raises HttpError exactly like the real client. Every request is
    recorded in .requests.
    """

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[dict[str, Any]] = []
        self._queue: list[dict[str, Any]] = []
        self._routes: dict[str, dict[str, Any]] = {}

    def on(
        self,
        url: str,
        *,
        status: int = 200,
        json: Any = None,
        text: str = "",
        content: bytes = b"",
        error: BaseException | None = None,
    ) -> None:
        self._routes[url] = {
            "status": status,
            "json": json,
            "text": text,
            "content": content,
            "error": error,
        }

    def enqueue(
        self,
        *,
        status: int = 200,
        json: Any = None,
        text: str = "",
        content: bytes = b"",
        error: BaseException | None = None,
    ) -> None:
        self._queue.append(
            {"status": status, "json": json, "text": text, "content": content, "error": error}
        )

    def _serve(self, outcome: dict[str, Any], url: str) -> requests.Response:
        if outcome["error"] is not None:
            raise outcome["error"]
        response = FakeResponse(
            status_code=outcome["status"],
            json=outcome["json"],
            text=outcome["text"],
            content=outcome["content"],
        )
        status = response.status_code
        assert status is not None  # FakeResponse always sets it
        if status >= 400:
            raise HttpError(
                f"HTTP {status}: {url}",
                status_code=status,
                body=response.text,
            )
        return response

    def _take(self, method: str, url: str) -> dict[str, Any]:
        if self._queue:
            return self._queue.pop(0)
        for prefix in sorted(self._routes, key=len, reverse=True):
            if url.startswith(prefix):
                return self._routes[prefix]
        raise AssertionError(f"unexpected request: {method} {url}")

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.requests.append({"method": method, "url": url, **kwargs})
        return self._serve(self._take(method, url), url)

    def download(self, url: str, dest: str | Path) -> Path:
        self.requests.append({"method": "GET", "url": url})
        response = self._serve(self._take("GET", url), url)
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        return path

    def close(self) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


class _Server(http.server.ThreadingHTTPServer):
    """ThreadingHTTPServer carrying its routes and request log."""

    routes: dict[tuple[str, str], tuple[int, str, str]]
    requests: list[dict[str, Any]]


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves (method, path) routes and records every request."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        server = self.server
        assert isinstance(server, _Server)
        server.requests.append(
            {"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body}
        )
        status, content_type, payload = server.routes.get(
            (self.command, self.path.partition("?")[0]),
            (404, "text/plain", "not found"),
        )
        data = payload.encode() if isinstance(payload, str) else payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()


class LocalServer:
    """A real HTTP server on 127.0.0.1 for transport-level tests."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], tuple[int, str, str]] = {}
        self.requests: list[dict[str, Any]] = []
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.routes = self.routes
        self._server.requests = self.requests
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def route(
        self,
        method: str,
        path: str,
        *,
        status: int = 200,
        json: Any = None,
        text: str = "",
    ) -> None:
        if json is not None:
            body, content_type = dumps(json), "application/json"
        else:
            body, content_type = text, "text/plain"
        self.routes[(method, path)] = (status, content_type, body)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class FakeRclone:
    """shell.run stand-in for rclone invocations.

    Queued outputs (or exceptions) answer calls in order; unqueued lsjson
    calls answer with self.rows; everything else succeeds silently, because
    rclone is silent on success.
    """

    def __init__(self, rows: Sequence[dict[str, Any]] = ()) -> None:
        self.rows = list(rows)
        self.calls: list[list[str]] = []
        self._queue: list[Any] = []

    def enqueue(self, output: Any) -> None:
        """Queue one stdout string or exception for the next call."""
        self._queue.append(output)

    def __call__(self, args: Sequence[str], *, timeout: float | None = None):
        self.calls.append(list(args))
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return subprocess.CompletedProcess(list(args), 0, item, "")
        out = json.dumps(self.rows) if "lsjson" in args else ""
        return subprocess.CompletedProcess(list(args), 0, out, "")


class FakeBackend:
    """In-memory archive remote with the backend contract.

    copy() uploads by reading through the CAS symlinks and stores the bytes;
    fetch() writes them back, so add -> copy -> drop -> get round-trips the
    real content. Seeded `files` behave like a pre-existing remote listing.
    """

    def __init__(self, *, remote: str = "fake:", files: Sequence[RemoteFile] = ()) -> None:
        self.remote = remote
        self.files: list[RemoteFile] = list(files)
        self.store: dict[str, bytes] = {}
        self.uploads: list[str] = []
        self.verify_calls: list[str] = []
        self.error: Exception | None = None

    def location(self, relpath: str) -> str:
        relpath = relpath.lstrip("/")
        sep = "" if self.remote.endswith(":") else "/"
        return f"{self.remote}{sep}{relpath}"

    def close(self) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    # Sequence, not list: the method named `list` below shadows the builtin
    # inside the class body, so `list[...]` would not resolve as a type here.
    def list(self) -> Sequence[RemoteFile]:
        return list(self.files)

    def copy(self, root: Path, entries: Sequence[Entry]) -> Iterator[RemoteFile]:
        for entry in entries:
            location = self.location(entry.relpath)
            self.uploads.append(entry.relpath)
            self.store[location] = entry.path.read_bytes()
            item = RemoteFile(location=location, hashes=entry.hashes.identifiers)
            self.files.append(item)
            yield item

    def verify(self, location: str, want: Hashes) -> Status:
        self.verify_calls.append(location)
        if self.error is not None:
            raise self.error
        for item in self.files:
            if item.location == location:
                if not item.hashes:
                    return "unknown"
                return "ok" if set(item.hashes) & set(want.identifiers) else "mismatch"
        return "missing"

    def fetch(self, location: str, dest: Path) -> None:
        if self.error is not None:
            raise self.error
        dest.write_bytes(self.store[location])


@pytest.fixture
def fake_stdin(monkeypatch):
    """Patch sys.stdin for helper-level tests."""

    def _set(text: str, *, is_tty: bool = False) -> None:
        monkeypatch.setattr(sys, "stdin", FakeStdin(text, is_tty=is_tty))

    return _set


@pytest.fixture
def run_cli(monkeypatch, capsys, fake_stdin):
    """Call a CLI main() with faked stdin; return (exit_code, stdout, stderr).

    SystemExit (argparse --help/--version/usage errors) is folded into the
    returned code, mirroring what a console script would exit with.
    """

    def _run(
        main: Callable[..., int],
        args: list[str],
        *,
        input: str = "",
    ) -> tuple[int, str, str]:
        fake_stdin(input)
        try:
            code = main(list(args))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def fake_http() -> FakeHttpClient:
    return FakeHttpClient()


@pytest.fixture
def http_server() -> Generator[LocalServer]:
    server = LocalServer()
    yield server
    server.close()


@pytest.fixture
def disable_sleep(monkeypatch):
    """Disable real sleeps in the Wayback client."""
    monkeypatch.setattr("pkit.wayback.client.time.sleep", lambda _seconds: None)


@pytest.fixture
def patch_save_url(monkeypatch):
    """Patch WaybackClient.save_url for CLI unit tests.

    Usage:
        calls = patch_save_url(lambda url, opts: SaveResult(...))
        calls = patch_save_url(lambda url, opts: (_ for _ in ()).throw(SomeError()))
    """

    def _patch(result_factory):
        calls = []

        def fake_save_url(self, url, *, opts=None):
            calls.append({"url": url, "opts": opts})
            return result_factory(url, opts)

        monkeypatch.setattr(
            "pkit.wayback.cli.WaybackClient.save_url",
            fake_save_url,
        )
        return calls

    return _patch


@pytest.fixture
def client() -> Generator[WaybackClient]:
    """SDK client with a fake transport and the lock disabled."""
    c = WaybackClient(
        api_key="test-key",
        api_secret="test-secret",
        lock_file="",
        timeout=0.1,
        http=FakeHttpClient(),
    )
    yield c
    c.close()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git work tree with one plain file: <tmp>/manga/v1.cbz."""
    (tmp_path / ".git").mkdir()
    tree = tmp_path / "manga"
    tree.mkdir()
    (tree / "v1.cbz").write_bytes(b"pandora")
    return tmp_path


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh record database, also visible through $PKIT_META_DB_PATH."""
    path = tmp_path / "records.db"
    monkeypatch.setenv("PKIT_META_DB_PATH", str(path))
    return path


@pytest.fixture
def added(repo: Path, db_path: Path) -> Hashes:
    """The repo tree ingested through the SDK; returns its Hashes."""
    archive.add(repo / "manga", db_path=db_path)
    return hash_file(repo / "manga" / "v1.cbz")  # the link now points at the object


@pytest.fixture
def rclone(monkeypatch: pytest.MonkeyPatch) -> FakeRclone:
    """Answer every rclone invocation from a FakeRclone."""
    fake = FakeRclone()
    monkeypatch.setattr("pkit.archive.rclone.shell.run", fake)
    return fake


@pytest.fixture
def install_backend(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeBackend]:
    """Give pkit.archive one in-memory remote for every location.

    Usage: backend = install_backend(remote="ia:x", files=[RemoteFile(...)])
    """

    def _install(**kwargs: Any) -> FakeBackend:
        backend = FakeBackend(**kwargs)
        monkeypatch.setattr("pkit.archive._backend", lambda remote, url: backend)
        return backend

    return _install

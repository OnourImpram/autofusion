"""Secure, bounded subprocess execution for local provider CLIs."""

from __future__ import annotations

import math
import os
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast

from autofusion.errors import ProviderError
from autofusion.util import bounded_text


class _Process(Protocol):
    stdin: BinaryIO | None
    stdout: BinaryIO | None
    stderr: BinaryIO | None
    pid: int
    returncode: int | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


class PopenFactory(Protocol):
    def __call__(self, args: Sequence[str], **kwargs: Any) -> _Process: ...


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    truncated: bool


class _BoundedBytes:
    """Retain bounded diagnostic output without accumulating pipe bombs in memory."""

    def __init__(self, limit: int) -> None:
        if limit < 0:
            raise ValueError("limit must be nonnegative")
        self._limit = limit
        self._data = bytearray()
        self.truncated = False
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        with self._lock:
            combined = bytes(self._data) + chunk
            if len(combined) <= self._limit:
                self._data = bytearray(combined)
                return
            self.truncated = True
            if self._limit == 0:
                self._data.clear()
                return
            self._data = bytearray(combined[-self._limit :])

    def text(self) -> str:
        return bytes(self._data).decode("utf-8", errors="replace")


def minimal_environment(allowlist: Sequence[str]) -> dict[str, str]:
    """Return only explicit variables plus the minimal Windows executable contract."""

    required = {"SystemRoot", "WINDIR"} if os.name == "nt" else set()
    allowed = required | {name for name in allowlist if name}
    return {name: value for name in allowed if (value := os.environ.get(name)) is not None}


def _default_popen(args: Sequence[str], **kwargs: Any) -> _Process:
    return cast(_Process, subprocess.Popen(list(args), **kwargs))


def _read_pipe(stream: BinaryIO, sink: _BoundedBytes) -> None:
    try:
        while chunk := stream.read(8192):
            sink.append(chunk)
    finally:
        stream.close()


def _write_pipe(stream: BinaryIO, content: bytes) -> None:
    try:
        stream.write(content)
    except OSError:
        pass
    finally:
        with suppress(OSError):
            stream.close()


class CommandRunner:
    """Run an argv-only command with bounded pipes, deadline, and tree cleanup."""

    def __init__(
        self,
        *,
        popen_factory: PopenFactory | None = None,
        kill_tree: Callable[[_Process], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._popen = popen_factory or _default_popen
        self._kill_tree = kill_tree or self._terminate_tree
        self._clock = clock

    def run(
        self,
        argv: Sequence[str],
        *,
        input_text: str,
        cwd: Path,
        timeout_s: float,
        max_output_chars: int,
        environment_allowlist: Sequence[str],
    ) -> CommandOutcome:
        if not argv or any(not isinstance(part, str) or not part for part in argv):
            raise ProviderError("provider command must be a non-empty argv sequence")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ProviderError("provider timeout must be finite and positive")
        if max_output_chars < 0:
            raise ProviderError("provider output limit must be nonnegative")
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": str(cwd),
            "env": minimal_environment(environment_allowlist),
            "shell": False,
        }
        if os.name == "nt":
            kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            kwargs["start_new_session"] = True
        started = self._clock()
        deadline = started + timeout_s
        input_bytes = input_text.encode("utf-8")
        try:
            process = self._popen(tuple(argv), **kwargs)
        except OSError as exc:
            raise ProviderError(f"failed to start provider command {argv[0]!r}: {exc}") from exc
        if process.stdin is None or process.stdout is None or process.stderr is None:
            self._kill_tree(process)
            raise ProviderError("provider process did not expose required standard streams")
        stdout_sink = _BoundedBytes(max_output_chars)
        stderr_sink = _BoundedBytes(max_output_chars)
        workers = [
            threading.Thread(target=_read_pipe, args=(process.stdout, stdout_sink), daemon=True),
            threading.Thread(target=_read_pipe, args=(process.stderr, stderr_sink), daemon=True),
            threading.Thread(target=_write_pipe, args=(process.stdin, input_bytes), daemon=True),
        ]
        for worker in workers:
            worker.start()
        timed_out = False
        while process.poll() is None:
            if self._clock() >= deadline:
                timed_out = True
                self._kill_tree(process)
                break
            time.sleep(0.01)
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            self._kill_tree(process)
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=1.0)
        cleanup_deadline = time.monotonic() + 1.0
        for worker in workers:
            worker.join(timeout=max(0.0, cleanup_deadline - time.monotonic()))
        stdout, stdout_truncated = bounded_text(stdout_sink.text(), max_output_chars)
        stderr, stderr_truncated = bounded_text(stderr_sink.text(), max_output_chars)
        return CommandOutcome(
            argv=tuple(argv),
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=max(0, int((self._clock() - started) * 1000)),
            timed_out=timed_out,
            truncated=(
                stdout_sink.truncated
                or stderr_sink.truncated
                or stdout_truncated
                or stderr_truncated
            ),
        )

    @staticmethod
    def _terminate_tree(process: _Process) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            with suppress(OSError, subprocess.TimeoutExpired):
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    timeout=0.25,
                )
            if process.poll() is None:
                process.kill()
        else:
            killpg = getattr(os, "killpg", None)
            if killpg is None:
                process.kill()
                return
            try:
                killpg(process.pid, 9)
            except (LookupError, PermissionError, ProcessLookupError):
                process.kill()

"""Windows job ownership for provider subprocesses, using only the standard library."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from typing import Any, cast


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong),
        ("job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimit),
        ("io_counters", ctypes.c_ulonglong * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_longlong),
        ("kernel_time", ctypes.c_longlong),
        ("period_user_time", ctypes.c_longlong),
        ("period_kernel_time", ctypes.c_longlong),
        ("page_faults", wintypes.DWORD),
        ("total_processes", wintypes.DWORD),
        ("active_processes", wintypes.DWORD),
        ("terminated_processes", wintypes.DWORD),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("thread_id", wintypes.DWORD),
        ("process_id", wintypes.DWORD),
        ("base_priority", wintypes.LONG),
        ("delta_priority", wintypes.LONG),
        ("flags", wintypes.DWORD),
    ]


def _win_error() -> OSError:
    # Resolve Windows-only symbols lazily so POSIX imports and type checks work.
    factory = getattr(ctypes, "WinError")  # noqa: B009
    return cast(OSError, factory())


def _load_kernel32() -> Any:
    loader = getattr(ctypes, "WinDLL")  # noqa: B009
    kernel = loader("kernel32", use_last_error=True)
    handle, dword, boolean, pointer = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.c_void_p
    )
    signatures = (
        ("CreateJobObjectW", [pointer, wintypes.LPCWSTR], handle),
        ("SetInformationJobObject", [handle, ctypes.c_int, pointer, dword], boolean),
        ("AssignProcessToJobObject", [handle, handle], boolean),
        ("TerminateJobObject", [handle, wintypes.UINT], boolean),
        ("QueryInformationJobObject", [handle, ctypes.c_int, pointer, dword, pointer], boolean),
        ("OpenProcess", [dword, boolean, dword], handle),
        ("CreateToolhelp32Snapshot", [dword, dword], handle),
        ("Thread32First", [handle, pointer], boolean),
        ("Thread32Next", [handle, pointer], boolean),
        ("OpenThread", [dword, boolean, dword], handle),
        ("ResumeThread", [handle], dword),
        ("WaitForSingleObject", [handle, dword], dword),
        ("CloseHandle", [handle], boolean),
    )
    for name, arguments, result in signatures:
        function = getattr(kernel, name)
        function.argtypes = arguments
        function.restype = result
    return kernel


class WindowsJob:
    """Own a provider tree from suspended startup through verified termination.

    Create before Popen, include ``creationflags`` in its Windows flags, then
    call ``assign_and_resume(pid)`` immediately. Always close before deleting
    the provider's temporary home. An assignment failure requires the caller
    to kill and wait for the suspended process. All methods are no-ops on POSIX.

    Popen exposes no job-list startup attribute. Suspended startup prevents
    provider code from running before assignment, but supervisor termination
    between Popen and assignment can leave a suspended process outside the job.
    """

    def __init__(self) -> None:
        self.creationflags = 0x00000004 if os.name == "nt" else 0
        self._kernel: Any = None
        self._handle: int | None = None
        if os.name != "nt":
            return
        self._kernel = _load_kernel32()
        handle = self._kernel.CreateJobObjectW(None, None)
        if not handle:
            raise _win_error()
        self._handle = int(handle)
        limits = _ExtendedLimit()
        # No breakaway flags: descendants inherit the job and die on last close.
        # https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
        limits.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(
            self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = _win_error()
            self._kernel.CloseHandle(self._handle)
            self._handle = None
            raise error

    def assign_and_resume(self, pid: int) -> None:
        """Assign a newly CREATE_SUSPENDED child, then release its initial thread."""
        if self._kernel is None:
            return
        if self._handle is None:
            raise OSError("provider job is closed")
        # Assign requires PROCESS_SET_QUOTA | PROCESS_TERMINATE.
        # https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject
        process = self._kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            raise _win_error()
        try:
            if not self._kernel.AssignProcessToJobObject(self._handle, process):
                raise _win_error()
            self._resume_threads(pid)
        finally:
            self._kernel.CloseHandle(process)

    def _resume_threads(self, pid: int) -> None:
        # Popen closes the initial thread handle. Toolhelp recovers owned threads.
        # https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/ns-tlhelp32-threadentry32
        snapshot = self._kernel.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise _win_error()
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = False
            more = self._kernel.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.process_id == pid:
                    thread = self._kernel.OpenThread(0x0002, False, entry.thread_id)
                    if not thread:
                        raise _win_error()
                    try:
                        # https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags
                        if self._kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise _win_error()
                        found = True
                    finally:
                        self._kernel.CloseHandle(thread)
                entry.size = ctypes.sizeof(entry)
                more = self._kernel.Thread32Next(snapshot, ctypes.byref(entry))
            if not found:
                raise OSError("provider suspended thread was not found")
        finally:
            self._kernel.CloseHandle(snapshot)

    def _active_processes(self) -> int:
        information = _Accounting()
        if not self._kernel.QueryInformationJobObject(
            self._handle, 1, ctypes.byref(information), ctypes.sizeof(information), None
        ):
            raise _win_error()
        return int(information.active_processes)

    def _member_handles(self, deadline: float) -> list[int]:
        # Block new descendants while collecting handles for termination waits.
        # https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
        limits = _ExtendedLimit()
        limits.basic.flags = 0x00002000 | 0x00000008
        limits.basic.active_process_limit = 1
        if not self._kernel.SetInformationJobObject(
            self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise _win_error()
        capacity = max(1, self._active_processes())
        while True:
            buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
            if self._kernel.QueryInformationJobObject(
                self._handle, 3, buffer, ctypes.sizeof(buffer), None
            ):
                break
            error = _win_error()
            if getattr(error, "winerror", None) != 234:  # ERROR_MORE_DATA
                raise error
            if time.monotonic() >= deadline:
                raise TimeoutError("provider job membership query exceeded its deadline")
            capacity *= 2
        count = wintypes.DWORD.from_buffer(buffer, 4).value
        identifiers = (ctypes.c_size_t * count).from_buffer(buffer, 8)
        handles: list[int] = []
        try:
            for pid in identifiers:
                handle = self._kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                if handle:
                    handles.append(int(handle))
                else:
                    error = _win_error()
                    if getattr(error, "winerror", None) != 87:  # Already exited.
                        raise error
            return handles
        except BaseException:
            for handle in handles:
                self._kernel.CloseHandle(handle)
            raise

    def close(self, timeout_s: float = 1.0) -> None:
        """Terminate every member and verify an empty job within the timeout.

        OSError or TimeoutError means tree cleanup was not verified. The handle
        is still closed so kill-on-close remains effective on the failure path.
        """
        if self._handle is None:
            return
        deadline = time.monotonic() + max(0.0, timeout_s)
        members: list[int] = []
        try:
            members = self._member_handles(deadline)
            # Termination is asynchronous. Wait for process I/O cancellation too.
            # https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess
            if not self._kernel.TerminateJobObject(self._handle, 1):
                raise _win_error()
            for member in members:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                result = self._kernel.WaitForSingleObject(member, remaining_ms)
                if result == 0xFFFFFFFF:
                    raise _win_error()
                if result != 0:
                    raise TimeoutError("provider process termination exceeded its deadline")
            while self._active_processes():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("provider job termination exceeded its deadline")
                time.sleep(min(0.01, remaining))
        finally:
            for member in members:
                self._kernel.CloseHandle(member)
            handle, self._handle = self._handle, None
            if not self._kernel.CloseHandle(handle):
                raise _win_error()

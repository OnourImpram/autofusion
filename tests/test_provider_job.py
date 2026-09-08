"""Real Windows process containment checks, without provider or identity access."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows process containment")
def test_provider_stays_suspended_until_job_assignment(tmp_path: Path) -> None:
    from autofusion.providers._job import WindowsJob

    marker = tmp_path / "started"
    job = WindowsJob()
    process = subprocess.Popen(
        [sys.executable, "-c", "from pathlib import Path; import sys; "
         "Path(sys.argv[1]).touch()", str(marker)],
        creationflags=job.creationflags | subprocess.CREATE_NO_WINDOW,
    )
    try:
        time.sleep(0.1)
        assert not marker.exists()
        job.assign_and_resume(process.pid)
        assert process.wait(timeout=5) == 0
        assert marker.exists()
    finally:
        job.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows process containment")
@pytest.mark.parametrize("children", [1, 3])
def test_job_close_reaps_descendant_after_parent_exits(tmp_path: Path, children: int) -> None:
    from autofusion.providers._job import WindowsJob

    locked_files = [tmp_path / f"home-in-use-{index}" for index in range(children)]
    child_code = (
        "import sys,time; "
        "held=open(sys.argv[1], 'w'); held.write('ready'); held.flush(); "
        "time.sleep(30)"
    )
    parent_code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "[subprocess.Popen([sys.executable, '-c', sys.argv[1], path], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) "
        "for path in sys.argv[2:]]; "
        "deadline=time.monotonic()+5\n"
        "while not all(Path(path).exists() for path in sys.argv[2:]) "
        "and time.monotonic()<deadline: time.sleep(.01)"
    )
    job = WindowsJob()
    process = subprocess.Popen(
        [sys.executable, "-c", parent_code, child_code, *map(str, locked_files)],
        creationflags=job.creationflags | subprocess.CREATE_NO_WINDOW,
    )
    try:
        job.assign_and_resume(process.pid)
        assert process.wait(timeout=5) == 0
        for locked in locked_files:
            assert locked.read_text() == "ready"
            with pytest.raises(PermissionError):
                locked.unlink()
        started = time.monotonic()
        job.close(timeout_s=1)
        assert time.monotonic() - started < 2
        for locked in locked_files:
            locked.unlink()
            assert not locked.exists()
        job.close()
    finally:
        job.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows process containment")
def test_job_assignment_rejects_a_closed_job() -> None:
    from autofusion.providers._job import WindowsJob

    job = WindowsJob()
    job.close()
    with pytest.raises(OSError, match="closed"):
        job.assign_and_resume(os.getpid())


@pytest.mark.skipif(os.name != "nt", reason="Windows process containment")
def test_job_close_failure_still_triggers_kill_on_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autofusion.providers._job import WindowsJob

    job = WindowsJob()
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=job.creationflags | subprocess.CREATE_NO_WINDOW,
    )
    try:
        job.assign_and_resume(process.pid)

        def fail_members(deadline: float) -> list[int]:
            raise TimeoutError("injected membership deadline")

        monkeypatch.setattr(job, "_member_handles", fail_members)
        with pytest.raises(TimeoutError, match="membership deadline"):
            job.close(timeout_s=0)
        process.wait(timeout=5)
        job.close()
    finally:
        job.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="Portable no-op")
def test_job_is_a_noop_outside_windows() -> None:
    from autofusion.providers._job import WindowsJob

    job = WindowsJob()
    assert job.creationflags == 0
    job.assign_and_resume(os.getpid())
    job.close()
    job.close()

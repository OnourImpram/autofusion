from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from autofusion.errors import ProviderError
from autofusion.providers.process import CommandRunner, _Process


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_timeout_never_starts_a_process(tmp_path: Path, timeout: float) -> None:
    calls: list[bool] = []

    def forbidden(args: Sequence[str], **kwargs: Any) -> _Process:
        calls.append(True)
        raise AssertionError("nonfinite timeout reached process creation")

    with pytest.raises(ProviderError, match=r"finite|positive"):
        CommandRunner(popen_factory=forbidden).run(
            ("controlled",), input_text="", cwd=tmp_path, timeout_s=timeout,
            max_output_chars=100, environment_allowlist=(),
        )
    assert not calls

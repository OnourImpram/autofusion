from pathlib import Path

import pytest

from autofusion.models import ProviderRequest


@pytest.mark.parametrize("field", ["timeout", "deadline"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_nonfinite_request_deadline_cannot_poison_transport_limits(
    tmp_path: Path, field: str, invalid: float,
) -> None:
    request = ProviderRequest(
        "run", "call", "gpt-sol", "Review", {}, tmp_path,
        invalid if field == "timeout" else 10, 100,
        deadline_monotonic=invalid if field == "deadline" else None,
    )
    with pytest.raises(ValueError, match="finite"):
        request.remaining_timeout_s()

from __future__ import annotations

import pytest

from autofusion.errors import OutputValidationError
from autofusion.output_repair import parse_structured_output

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["severity", "claim"],
    "properties": {
        "severity": {"enum": ["blocker", "major", "minor"]},
        "claim": {"type": "string", "minLength": 1},
    },
}


def test_plain_json_is_not_repaired() -> None:
    result = parse_structured_output(
        '{"severity":"major","claim":"broken"}', SCHEMA, max_chars=1000
    )
    assert result.repaired is False
    assert result.transformations == ()


def test_transport_wrappers_are_removed_without_content_rewrite() -> None:
    result = parse_structured_output(
        '\ufeff  ```json\n{"severity":"minor","claim":"bounded"}\n```  ',
        SCHEMA,
        max_chars=1000,
    )
    assert result.value["claim"] == "bounded"
    assert result.transformations == (
        "utf8-bom",
        "surrounding-whitespace",
        "json-code-fence",
    )


@pytest.mark.parametrize(
    "payload",
    [
        '{"severity":"major","claim":"x",}',
        "{'severity':'major','claim':'x'}",
        '{"severity":"invented","claim":"x"}',
        '{"severity":"major","claim":"x","extra":true}',
    ],
)
def test_semantic_or_non_json_repair_is_rejected(payload: str) -> None:
    with pytest.raises(OutputValidationError):
        parse_structured_output(payload, SCHEMA, max_chars=1000)


def test_output_limit_is_enforced_before_parse() -> None:
    with pytest.raises(OutputValidationError, match="limit"):
        parse_structured_output("{" + "x" * 100 + "}", SCHEMA, max_chars=20)


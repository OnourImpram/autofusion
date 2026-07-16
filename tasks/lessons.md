# Engineering lessons

1. Run every advertised live provider through the exact packaged adapter before tagging.
   Test doubles do not expose CLI schema dialect or telemetry-envelope drift.
2. Keep provider-specific schema normalization separate from the canonical validation
   schema. Normalize transport metadata only, then validate returned data against the
   untouched canonical schema.
3. Treat auxiliary model usage as auditable provider telemetry. Require the configured
   canonical model to be present and reject multi-model usage that omits it.
4. A provider alias is not identity evidence. Keep entitlement-gated profiles disabled
   when live telemetry shows a different fallback model, even if the CLI exits zero.

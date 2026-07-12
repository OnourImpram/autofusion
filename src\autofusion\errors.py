"""Domain errors with stable CLI exit semantics."""


class AutofusionError(RuntimeError):
    """Base class for expected autofusion failures."""


class ConfigurationError(AutofusionError):
    """Configuration is invalid or violates immutable policy."""


class PolicyError(AutofusionError):
    """A requested action is denied by policy."""


class ProviderError(AutofusionError):
    """A provider invocation failed before a valid response was produced."""


class NonCallableProviderError(ProviderError):
    """The caller attempted to invoke the active session sentinel."""


class OutputValidationError(ProviderError):
    """Provider output did not satisfy its structured output contract."""


class SnapshotError(AutofusionError):
    """A repository snapshot could not be created safely."""


class GroundingError(AutofusionError):
    """A trusted verification could not be resolved or executed safely."""


class BudgetExceeded(AutofusionError):
    """A hard run budget was exhausted."""


class ReceiptError(AutofusionError):
    """Receipt persistence or verification failed."""


class ReplayError(AutofusionError):
    """Recorded provider evidence is unavailable or invalid."""


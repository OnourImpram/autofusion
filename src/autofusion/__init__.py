"""autofusion public package surface."""

from autofusion.config import FusionConfig, load_config
from autofusion.engine import FusionEngine

__all__ = ["FusionConfig", "FusionEngine", "load_config"]
__version__ = "0.4.0a1"

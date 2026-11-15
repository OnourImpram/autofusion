"""autofusion public package surface."""

from autofusion.config import FusionConfig, load_config
from autofusion.engine import FusionEngine

__all__ = ["FusionConfig", "FusionEngine", "load_config"]
__version__ = "1.0.0"

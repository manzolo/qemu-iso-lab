"""vmctl – QEMU VM management toolkit."""
__version__ = "1.0.0"
from vmctl.errors import VMError
from vmctl.cli import main

__all__ = ["VMError", "main", "__version__"]

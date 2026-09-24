"""vmctl – QEMU VM management toolkit."""
__version__ = "0.1.9"
from vmctl.errors import VMError
from vmctl.cli import main

__all__ = ["VMError", "main", "__version__"]

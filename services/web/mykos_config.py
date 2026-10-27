"""Shared paths. Override MYKOS_BASE only for development and isolated tests."""
import os
from pathlib import Path
BASE = Path(os.environ.get("MYKOS_BASE", "/opt/mykos-web"))

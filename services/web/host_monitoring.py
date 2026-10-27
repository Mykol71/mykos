"""Shared read-only host monitoring primitives."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'helpers') if (Path(__file__).resolve().parents[1] / 'helpers').is_dir() else '/usr/local/sbin')
from mykos_host_monitoring import *

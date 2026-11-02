"""Keep customer concentration facts from FS&N months.

Usage: uv run python scripts/load_fsn.py 2026_03 [2025_11 ...]
"""

import sys
from pathlib import Path

from knock_on_atlas.config import get_settings
from knock_on_atlas.edgar.client import SecClient
from knock_on_atlas.edgar.fsn import load_month

client = SecClient(get_settings().sec_user_agent, Path("data/raw/sec_cache"))
for month in sys.argv[1:]:
    out = load_month(client, month, Path("data/raw/fsn"), Path("data/processed/fsn"))
    print(out)

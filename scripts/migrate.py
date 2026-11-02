"""Apply database migrations to DATABASE_URL.

Usage: uv run python scripts/migrate.py
"""

import psycopg

from knock_on_atlas.config import get_settings
from knock_on_atlas.db.migrate import migrate

url = get_settings().database_url.get_secret_value()
with psycopg.connect(url, autocommit=True) as conn:
    print(migrate(conn) or "up to date")

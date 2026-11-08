"""Create an API user, or reset an existing user's password and role.

The password is read from the terminal, never from arguments or shell history.

Usage: uv run python scripts/create_user.py <email> <analyst|reviewer|admin>
"""

import argparse
import getpass

import psycopg

from knock_on_atlas.api.auth import RANK, hash_password
from knock_on_atlas.config import get_settings

parser = argparse.ArgumentParser()
parser.add_argument("email")
parser.add_argument("role", choices=list(RANK))
args = parser.parse_args()
password = getpass.getpass("password: ")
if len(password) < 12:
    parser.error("use a password of at least 12 characters")

url = get_settings().database_url.get_secret_value()
with psycopg.connect(url, autocommit=True) as conn:
    conn.execute(
        "INSERT INTO users (email, password_hash, role) VALUES (%s, %s, %s)"
        " ON CONFLICT (email) DO UPDATE"
        " SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role",
        (args.email.strip().lower(), hash_password(password), args.role),
    )
print(f"saved {args.email.strip().lower()} as {args.role}")

import psycopg

from knock_on_atlas.db.migrate import MIGRATIONS, migrate


def test_migrate_applies_each_file_once(db: psycopg.Connection) -> None:
    files = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert files[0] == "001_exposure_graph.sql"
    assert migrate(db) == files
    assert migrate(db) == []
    tables = {
        row[0]
        for row in db.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = current_schema()"
        )
    }
    assert {"companies", "concentration_facts", "entity_edges"} <= tables

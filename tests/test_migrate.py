import psycopg

from knock_on_atlas.db.migrate import migrate


def test_migrate_applies_each_file_once(db: psycopg.Connection) -> None:
    assert migrate(db) == ["001_exposure_graph.sql"]
    assert migrate(db) == []
    tables = {
        row[0]
        for row in db.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = current_schema()"
        )
    }
    assert {"companies", "concentration_facts", "entity_edges"} <= tables

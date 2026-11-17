"""Additive schema upgrade for segmented archives.

``Base.metadata.create_all`` creates the new ``segment_batches`` table but does
not add columns to existing tables.  This helper applies only additive changes
so an existing demo volume keeps its audit and recovery history:

* ``log_sets.segments``            (JSON, nullable — NULL means a legacy complete log)
* ``recoveries.target_lsn``        (INTEGER, nullable)
* ``recoveries.crash_case_id``     relaxed to nullable (a run may target a log position only)

Failures are raised, so startup stops explicitly instead of serving a schema
that does not match the model.
"""
from sqlalchemy import inspect, text


def upgrade(engine):
    dialect = engine.dialect.name
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        if 'log_sets' in tables:
            columns = {column['name'] for column in inspector.get_columns('log_sets')}
            if 'segments' not in columns:
                conn.execute(text('ALTER TABLE log_sets ADD COLUMN segments JSON'))
        if 'recoveries' in tables:
            columns = {column['name'] for column in inspector.get_columns('recoveries')}
            if 'target_lsn' not in columns:
                conn.execute(text('ALTER TABLE recoveries ADD COLUMN target_lsn INTEGER'))
            if dialect == 'postgresql':
                # Idempotent: dropping NOT NULL twice is a no-op.
                conn.execute(text('ALTER TABLE recoveries ALTER COLUMN crash_case_id DROP NOT NULL'))
            elif dialect == 'sqlite':
                required = {column['name']: column['nullable'] for column in inspector.get_columns('recoveries')}
                if not required.get('crash_case_id', True):
                    # SQLite cannot drop NOT NULL in place; rebuild while keeping every row.
                    conn.execute(text('PRAGMA foreign_keys=OFF'))
                    conn.execute(text('''CREATE TABLE recoveries_new(
                        id INTEGER NOT NULL PRIMARY KEY, crash_case_id INTEGER,
                        label VARCHAR(120), status VARCHAR(20), target_lsn INTEGER,
                        snapshot JSON, result JSON, created_at DATETIME,
                        FOREIGN KEY(crash_case_id) REFERENCES crash_cases (id))'''))
                    conn.execute(text('''INSERT INTO recoveries_new(id, crash_case_id, label, status, target_lsn, snapshot, result, created_at)
                        SELECT id, crash_case_id, label, status, target_lsn, snapshot, result, created_at FROM recoveries'''))
                    conn.execute(text('DROP TABLE recoveries'))
                    conn.execute(text('ALTER TABLE recoveries_new RENAME TO recoveries'))
                    conn.execute(text('PRAGMA foreign_keys=ON'))
    return True

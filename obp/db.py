from contextlib import contextmanager

import psycopg
from flask import current_app, g, has_request_context, request
from psycopg.rows import dict_row


def get_db():
    if "db" not in g:
        g.db = psycopg.connect(
            current_app.config["DATABASE_URL"], row_factory=dict_row, autocommit=True
        )
        is_restore_request = has_request_context() and request.endpoint == "routes.backup_restore"
        if not is_restore_request:
            # A restore takes this lock exclusively and waits for active requests.
            g.db.execute("SELECT pg_advisory_lock_shared(72419032)")
            g.maintenance_lock_held = True
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        if g.pop("maintenance_lock_held", False):
            db.execute("SELECT pg_advisory_unlock_shared(72419032)")
        db.close()


@contextmanager
def write_transaction():
    db = get_db()
    with db.transaction():
        # Keep stock and sales writes from racing with each other.
        db.execute("SELECT pg_advisory_xact_lock(72419031)")
        yield db

"""Create database backups and restore them with a recovery copy."""

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import click
import psycopg
from psycopg.conninfo import conninfo_to_dict
from flask import current_app

from .db import get_db


def _backup_dir():
    directory = Path(current_app.config["BACKUP_DIR"]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _log_backup(filename, status, actor, details="", completed=False):
    with get_db().transaction():
        get_db().execute(
            """INSERT INTO backup_logs (filename, status, actor, details, completed_at)
               VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() END)""",
            (filename, status, actor, details[:2000], completed),
        )


def _pg_command_options():
    connection = conninfo_to_dict(current_app.config["DATABASE_URL"])
    environment = os.environ.copy()
    options = []
    for key, flag in (("host", "--host"), ("port", "--port"),
                      ("user", "--username"), ("dbname", "--dbname")):
        if connection.get(key):
            options.extend((flag, connection[key]))
    for key, variable in (
        ("password", "PGPASSWORD"),
        ("sslmode", "PGSSLMODE"),
        ("sslcert", "PGSSLCERT"),
        ("sslkey", "PGSSLKEY"),
        ("sslrootcert", "PGSSLROOTCERT"),
    ):
        if connection.get(key):
            environment[variable] = connection[key]
    return options, environment


def create_backup(actor, protected=()):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"obp-{timestamp}.dump"
    target = _backup_dir() / filename
    _log_backup(filename, "running", actor)
    try:
        if not shutil.which("pg_dump"):
            raise RuntimeError("pg_dump was not found. Install PostgreSQL client tools and add them to PATH.")
        connection_options, environment = _pg_command_options()
        result = subprocess.run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--file",
                str(target),
                *connection_options,
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "pg_dump failed.")
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError("pg_dump completed without creating a valid archive.")
        _log_backup(filename, "success", actor, "Database archive created.", completed=True)
        _prune_backups(protected)
        return filename
    except Exception as error:
        target.unlink(missing_ok=True)
        _log_backup(filename, "failed", actor, str(error), completed=True)
        raise


def _backup_files():
    return sorted(
        _backup_dir().glob("obp-*.dump"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def _prune_backups(protected=()):
    kept = [item for item in _backup_files() if item.name not in protected]
    for old_backup in kept[7:]:
        old_backup.unlink()


def restore_backup(filename, actor):
    # Check the archive before changing the live database.
    if not shutil.which("pg_restore"):
        _log_backup(filename, "failed", f"restore:{actor}",
                    "pg_restore was not found on PATH.", completed=True)
        raise RuntimeError("pg_restore was not found. Install PostgreSQL client tools and add them to PATH.")
    if Path(filename).name != filename or not filename.startswith("obp-") or not filename.endswith(".dump"):
        _log_backup(filename[:200], "failed", f"restore:{actor}", "Invalid backup selection.", completed=True)
        raise ValueError("Select an application backup from the backup list.")
    archive = (_backup_dir() / filename).resolve()
    if archive.parent != _backup_dir() or not archive.is_file():
        _log_backup(filename, "failed", f"restore:{actor}", "Selected archive does not exist.", completed=True)
        raise ValueError("The selected backup does not exist in the restricted backup directory.")
    listing = subprocess.run(
        ["pg_restore", "--list", str(archive)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if listing.returncode:
        _log_backup(filename, "failed", f"restore:{actor}", "Selected file is not a valid PostgreSQL archive.", completed=True)
        raise ValueError("The selected file is not a valid PostgreSQL backup archive.")

    db_url = current_app.config["DATABASE_URL"]
    connection_options, environment = _pg_command_options()
    lock_connection = psycopg.connect(db_url, autocommit=True)
    try:
        # Save the current database so it can be restored if this operation fails.
        lock_connection.execute("SELECT pg_advisory_lock(72419032)")
        try:
            before = create_backup(f"pre-restore:{actor}", protected=(filename,))
        except Exception as error:
            _log_backup(filename, "failed", f"restore:{actor}",
                        f"Pre-restore backup failed: {error}", completed=True)
            raise
        failure = ""
        try:
            # Replace database objects from the selected archive.
            result = subprocess.run(
                [
                    "pg_restore",
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "--exit-on-error",
                    *connection_options,
                    str(archive),
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            if result.returncode:
                failure = result.stderr.strip() or "Restore command failed."
        except Exception as error:
            failure = str(error)
        if not failure:
            try:
                # Check that key tables and stock history still agree.
                with psycopg.connect(db_url) as verify_db:
                    verify_db.execute("SELECT count(*) FROM users").fetchone()
                    verify_db.execute("SELECT count(*) FROM products").fetchone()
                    invalid_balances = verify_db.execute(
                        """SELECT COUNT(*) AS count
                           FROM products p
                           WHERE p.stock_quantity <> COALESCE((
                             SELECT SUM(CASE
                               WHEN m.movement_type IN ('Stock In', 'Adjustment In') THEN m.quantity
                               WHEN m.movement_type IN ('Stock Out', 'Adjustment Out', 'Loss') THEN -m.quantity
                               ELSE 0 END)
                             FROM stock_movements m
                             WHERE m.product_id = p.id AND m.affects_stock
                           ), 0)"""
                    ).fetchone()[0]
                    broken_history = verify_db.execute(
                        """WITH ordered AS (
                             SELECT product_id, previous_balance,
                                    LAG(resulting_balance) OVER (
                                      PARTITION BY product_id ORDER BY created_at, id
                                    ) AS prior_balance
                             FROM stock_movements
                           )
                           SELECT COUNT(*) AS count FROM ordered
                           WHERE previous_balance <> COALESCE(prior_balance, 0)"""
                    ).fetchone()[0]
                    if invalid_balances or broken_history:
                        raise RuntimeError(
                            f"Stock reconciliation failed ({invalid_balances} balances, "
                            f"{broken_history} movement links)."
                        )
            except Exception as error:
                failure = f"Restore validation failed: {error}"
        if not failure:
            _log_backup(filename, "success", f"restore:{actor}", "Restore and key-table checks completed.", completed=True)
            return before

        # Restore the recovery copy if restore or validation failed.
        try:
            rollback = subprocess.run(
                [
                    "pg_restore",
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "--exit-on-error",
                    *connection_options,
                    str(_backup_dir() / before),
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
        except Exception as error:
            _log_backup(filename, "failed", f"restore:{actor}", f"{failure}; recovery failed: {error}", completed=True)
            raise RuntimeError(f"{failure} Recovery from the pre-restore backup also failed: {error}") from error
        if rollback.returncode:
            failure += " Recovery from the pre-restore backup also failed: " + rollback.stderr.strip()
            _log_backup(filename, "failed", f"restore:{actor}", failure, completed=True)
            raise RuntimeError(failure)
        _log_backup(filename, "failed", f"restore:{actor}",
                    f"{failure} The pre-restore backup was restored.", completed=True)
        raise RuntimeError(f"{failure} The pre-restore backup was restored.")
    finally:
        lock_connection.execute("SELECT pg_advisory_unlock(72419032)")
        lock_connection.close()


def register_backup_commands(app):
    @app.cli.command("backup-database")
    @click.option("--scheduled", is_flag=True, help="Record this run as the daily scheduled job.")
    def backup_database_command(scheduled):
        """Create and record a PostgreSQL backup."""
        actor = "scheduled job" if scheduled else "command line"
        try:
            filename = create_backup(actor)
        except Exception as error:
            raise click.ClickException(str(error)) from error
        click.echo(f"Backup complete: {filename}")


def available_backups():
    return [
        {
            "filename": item.name,
            "created": datetime.fromtimestamp(item.stat().st_mtime, timezone.utc),
            "size": item.stat().st_size,
        }
        for item in _backup_files()
    ]


import os
import secrets
from pathlib import Path

import click
from flask import Flask
from werkzeug.security import generate_password_hash

from .db import close_db, get_db


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        DATABASE_URL=os.environ.get(
            "DATABASE_URL", "postgresql://obp:obp@localhost:5432/obp"
        ),
        BACKUP_DIR=os.environ.get(
            "BACKUP_DIR", str(Path(app.instance_path) / "backups")
        ),
        VAT_RATE=os.environ.get("VAT_RATE", "0.12"),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["BACKUP_DIR"]).mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(close_db)

    from .auth import bp as auth_bp, load_current_user
    from .routes import bp as routes_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)
    app.before_request(load_current_user)
    @app.cli.command("init-db")
    def init_db_command():
        """Create the PostgreSQL schema and default roles."""
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        db = get_db()
        with db.transaction():
            db.execute(schema, prepare=False)
        click.echo("Database schema and default roles are ready.")

    @app.cli.command("create-admin")
    def create_admin_command():
        """Create the first active administrator account."""
        username = click.prompt("Username").strip()
        display_name = click.prompt("Display name").strip()
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        if len(password) < 12:
            raise click.ClickException("Use a password with at least 12 characters.")
        db = get_db()
        admin_role = db.execute(
            "SELECT id FROM roles WHERE name = 'Administrator / Owner'"
        ).fetchone()
        if not admin_role:
            raise click.ClickException("Run 'init-db' before creating an administrator.")
        db.execute(
            """INSERT INTO users (username, display_name, password_hash, role_id)
               VALUES (%s, %s, %s, %s)""",
            (username, display_name, generate_password_hash(password), admin_role["id"]),
        )
        click.echo("Administrator account created.")

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Add a small fictional product catalog for demonstrations."""
        db = get_db()
        with db.transaction():
            category_ids = {}
            for name in ("Computers", "Office equipment", "Peripherals"):
                category_ids[name] = db.execute(
                    """INSERT INTO categories (name) VALUES (%s)
                       ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                       RETURNING id""",
                    (name,),
                ).fetchone()["id"]
            products = [
                ("OBP-LTP-01", "Business laptop", "Computers", 42000, 36000, 8),
                ("OBP-PRN-01", "Laser printer", "Office equipment", 12500, 9700, 5),
                ("OBP-MSE-01", "Wireless mouse", "Peripherals", 650, 380, 15),
                ("OBP-KBD-01", "Office keyboard", "Peripherals", 1150, 720, 10),
            ]
            for sku, name, category, price, cost, reorder in products:
                db.execute(
                    """INSERT INTO products
                       (sku, name, category_id, selling_price, acquisition_cost,
                        stock_quantity, reorder_level)
                       VALUES (%s, %s, %s, %s, %s, 0, %s)
                       ON CONFLICT (sku) DO NOTHING""",
                    (sku, name, category_ids[category], price, cost, reorder),
                )
        click.echo("Demo catalog created. Add stock using a supplier receipt.")

    from .backups import register_backup_commands

    register_backup_commands(app)
    return app

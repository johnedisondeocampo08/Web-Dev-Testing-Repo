import hmac
import secrets

from flask import Blueprint, abort, current_app, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)


def load_current_user():
    g.user = None
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    if session.get("user_id"):
        g.user = get_db().execute(
            """SELECT u.id, u.username, u.display_name, u.active, r.name AS role_name,
                      r.permissions
               FROM users u JOIN roles r ON r.id = u.role_id
               WHERE u.id = %s AND u.active = TRUE""",
            (session["user_id"],),
        ).fetchone()
        if not g.user:
            session.clear()
            session["csrf_token"] = secrets.token_urlsafe(32)


@bp.before_app_request
def enforce_csrf():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(str(supplied), str(expected)):
        abort(400, description="Your form has expired. Reload the page and try again.")


@bp.get("/api/session")
def session_info():
    user = None
    if g.user:
        user = {
            "id": g.user["id"],
            "username": g.user["username"],
            "display_name": g.user["display_name"],
            "role_name": g.user["role_name"],
            "permissions": g.user["permissions"] or [],
        }
    return jsonify(user=user, csrf_token=session.get("csrf_token", ""))


@bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user:
        return redirect("/")
    if request.method == "GET":
        return send_from_directory(current_app.static_folder, "pages/login.html")

    data = request.get_json(silent=True) or request.form
    username = data.get("username", "").strip()
    password = data.get("password", "")
    user = get_db().execute(
        """SELECT u.id, u.password_hash
           FROM users u WHERE lower(u.username) = lower(%s) AND u.active = TRUE""",
        (username,),
    ).fetchone()
    if user and check_password_hash(user["password_hash"], password):
        session.clear()
        session["user_id"] = user["id"]
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify(
            message="Signed in.",
            redirect="/",
            csrf_token=session["csrf_token"],
        )
    return jsonify(error="Username or password is incorrect, or the account is inactive."), 401


@bp.route("/logout", methods=("POST",))
def logout():
    session.clear()
    return jsonify(message="Signed out.")

from functools import wraps

from flask import abort, g


DEFAULT_ROLES = {
    "Administrator / Owner": ["*"],
    "Sales Staff / Cashier": [
        "products.read",
        "sales.create",
        "sales.read",
        "receipts.read",
    ],
    "Inventory Staff": [
        "products.read",
        "products.manage",
        "inventory.read",
        "inventory.adjust.submit",
        "suppliers.manage",
        "restocking.manage",
    ],
    "Accounting Staff": ["sales.read", "reports.read", "reports.export", "receipts.read"],
}


def has_permission(permission):
    user = getattr(g, "user", None)
    if not user:
        return False
    permissions = user["permissions"] or []
    return "*" in permissions or permission in permissions


def permission_required(permission):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not has_permission(permission):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorate

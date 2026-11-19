import csv
import io
import secrets
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape

from psycopg.errors import ForeignKeyViolation, UniqueViolation
from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    request,
    Response,
    send_from_directory,
    url_for,
)
from werkzeug.security import generate_password_hash

from .backups import available_backups, create_backup, restore_backup
from .db import get_db, write_transaction
from .permissions import has_permission, permission_required
from .reports import best_sellers, daily_sales, default_range, monthly_sales, profit_report

bp = Blueprint("routes", __name__)
CENT = Decimal("0.01")


def _login_required():
    if not g.user:
        return redirect(url_for("auth.login"))
    return None


def _payload():
    return request.get_json(silent=True) or request.form.to_dict()


def _decimal(value, label, *, minimum=Decimal("0")):
    try:
        amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} must be a valid amount.")
    if not amount.is_finite() or amount < minimum:
        raise ValueError(f"{label} must be at least {minimum}.")
    return amount


def _positive_int(value, label):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number greater than zero.")
    if number <= 0:
        raise ValueError(f"{label} must be a whole number greater than zero.")
    return number


def _json_error(error, status=400):
    return jsonify(error=str(error)), status


def _audit(db, action, record_type, record_id="", details=""):
    db.execute(
        """INSERT INTO audit_logs (user_id, action, record_type, record_id, details)
           VALUES (%s, %s, %s, %s, %s)""",
        (g.user["id"] if g.user else None, action, record_type, str(record_id), details[:1000]),
    )


def _module_page(template, permission=None):
    login = _login_required()
    if login:
        return login
    if permission and not has_permission(permission):
        abort(403)
    return send_from_directory(current_app.static_folder, f"pages/{template}")


def _error_page(code, message):
    return Response(
        f"<!doctype html><html lang='en'><meta charset='utf-8'>"
        f"<title>{code} - OBP</title><body><h1>{code}</h1>"
        f"<p>{escape(str(message))}</p><a href='/'>Return to overview</a></body></html>",
        status=code,
        mimetype="text/html",
    )


@bp.app_errorhandler(403)
def forbidden(_error):
    if request.path.startswith("/api/"):
        return jsonify(error="You do not have permission to perform this action."), 403
    return _error_page(403, "You do not have permission to view this page.")


@bp.app_errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify(error="The requested record was not found."), 404
    return _error_page(404, "The requested page could not be found.")


@bp.app_errorhandler(400)
def bad_request(error):
    message = getattr(error, "description", "The request is invalid.")
    if request.path.startswith("/api/"):
        return jsonify(error=message), 400
    return _error_page(400, message)


@bp.route("/")
def dashboard():
    return _module_page("dashboard.html")


@bp.get("/api/dashboard")
def dashboard_data():
    if not g.user:
        return jsonify(error="Please sign in."), 401
    db = get_db()
    metrics = {"today_sales": Decimal("0.00"), "products": 0, "low_stock": 0, "open_orders": 0}
    alerts = []
    if has_permission("reports.read") or has_permission("sales.read"):
        metrics["today_sales"] = db.execute(
            """SELECT COALESCE(SUM(revenue_amount + vat_amount), 0) AS value
               FROM sale_dispatch_items WHERE dispatched_at::date = CURRENT_DATE"""
        ).fetchone()["value"]
    if has_permission("products.read"):
        metrics.update(db.execute(
            """SELECT COUNT(*) FILTER (WHERE active) AS products,
                      COUNT(*) FILTER (WHERE active AND stock_quantity <= reorder_level) AS low_stock
               FROM products"""
        ).fetchone())
        alerts = db.execute(
            """SELECT p.id, p.sku, p.name, p.stock_quantity, p.reorder_level,
                      COALESCE(s.name, 'No preferred supplier') AS supplier
               FROM products p LEFT JOIN suppliers s ON s.id = p.supplier_id
               WHERE p.active AND p.stock_quantity <= p.reorder_level
               ORDER BY p.stock_quantity, p.name LIMIT 8"""
        ).fetchall()
    if has_permission("restocking.manage"):
        metrics["open_orders"] = db.execute(
            """SELECT COUNT(*) AS count FROM purchase_orders
               WHERE status IN ('ordered', 'partially received')"""
        ).fetchone()["count"]
    recent_sales = []
    if has_permission("sales.read"):
        recent_sales = db.execute(
            """SELECT receipt_number, status, total, created_at
               FROM sales ORDER BY created_at DESC LIMIT 5"""
        ).fetchall()
    return jsonify(
        today_label=date.today().strftime("%A, %B %d, %Y"),
        metrics=metrics,
        alerts=alerts,
        recent_sales=recent_sales,
    )


@bp.route("/sales")
def sales_page():
    return _module_page("sales.html", "sales.create")


@bp.route("/inventory")
def inventory_page():
    return _module_page("inventory.html", "products.read")


@bp.route("/suppliers")
def suppliers_page():
    return _module_page("suppliers.html", "suppliers.manage")


@bp.route("/reports")
def reports_page():
    return _module_page("reports.html", "reports.read")


@bp.route("/receipts")
def receipts_page():
    return _module_page("receipts.html", "receipts.read")


@bp.route("/admin")
def admin_page():
    return _module_page("admin.html", "users.manage")


@bp.get("/api/admin/summary")
@permission_required("users.manage")
def admin_summary():
    db = get_db()
    users = db.execute(
        """SELECT u.id, u.username, u.display_name, u.active, r.id AS role_id, r.name AS role_name
           FROM users u JOIN roles r ON r.id = u.role_id ORDER BY u.display_name"""
    ).fetchall()
    roles = db.execute("SELECT id, name, permissions FROM roles ORDER BY name").fetchall()
    audit = db.execute(
        """SELECT a.action, a.record_type, a.record_id, a.details, a.created_at,
                  COALESCE(u.display_name, 'System') AS actor
           FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id
           ORDER BY a.created_at DESC LIMIT 30"""
    ).fetchall()
    backup_logs = db.execute(
        """SELECT filename, status, actor, details, created_at, completed_at
           FROM backup_logs ORDER BY created_at DESC LIMIT 20"""
    ).fetchall()
    adjustments = db.execute(
        """SELECT a.id, a.quantity_change, a.movement_type, a.reason, a.created_at,
                  p.sku, p.name AS product, u.display_name AS requester
           FROM stock_adjustment_requests a JOIN products p ON p.id = a.product_id
           LEFT JOIN users u ON u.id = a.requested_by
           WHERE a.status = 'pending' ORDER BY a.created_at"""
    ).fetchall()
    return jsonify(
        users=users,
        roles=roles,
        audit=audit,
        backup_logs=backup_logs,
        adjustments=adjustments,
        backups=available_backups(),
    )


@bp.get("/api/products")
@permission_required("products.read")
def products_list():
    rows = get_db().execute(
        """SELECT p.id, p.sku, p.name, p.selling_price, p.acquisition_cost,
                  p.supplier_id,
                  p.stock_quantity, p.reorder_level, p.active,
                  COALESCE(c.name, 'Uncategorized') AS category,
                  s.name AS supplier
           FROM products p LEFT JOIN categories c ON c.id = p.category_id
           LEFT JOIN suppliers s ON s.id = p.supplier_id
           WHERE p.active ORDER BY p.name"""
    ).fetchall()
    packages = get_db().execute(
        """SELECT pk.id, pk.sku, pk.name, pk.selling_price, pk.active,
                  (SELECT MIN(FLOOR(p.stock_quantity::numeric / pc.quantity))
                   FROM package_components pc JOIN products p ON p.id = pc.product_id
                   WHERE pc.package_id = pk.id AND p.active) AS available_quantity
           FROM packages pk WHERE pk.active ORDER BY pk.name"""
    ).fetchall()
    categories = get_db().execute(
        "SELECT id, name FROM categories WHERE active ORDER BY name"
    ).fetchall()
    suppliers = get_db().execute(
        "SELECT id, name FROM suppliers WHERE active ORDER BY name"
    ).fetchall() if has_permission("suppliers.manage") else []
    return jsonify(products=rows, packages=packages, categories=categories, suppliers=suppliers)


@bp.get("/api/categories")
@permission_required("reports.read")
def categories_list():
    return jsonify(categories=get_db().execute(
        "SELECT id, name FROM categories WHERE active ORDER BY name"
    ).fetchall())

@bp.post("/api/products")
@permission_required("products.manage")
def product_create():
    data = _payload()
    try:
        sku, name = data.get("sku", "").strip(), data.get("name", "").strip()
        if not sku or not name:
            raise ValueError("SKU and product name are required.")
        price = _decimal(data.get("selling_price"), "Selling price")
        cost = _decimal(data.get("acquisition_cost"), "Acquisition cost")
        reorder = int(data.get("reorder_level", 0))
        if reorder < 0:
            raise ValueError("Reorder level cannot be negative.")
        category_id = int(data["category_id"]) if data.get("category_id") else None
        supplier_id = int(data["supplier_id"]) if data.get("supplier_id") else None
        with write_transaction() as db:
            if not category_id and data.get("category", "").strip():
                category_id = db.execute(
                    """INSERT INTO categories (name) VALUES (%s)
                       ON CONFLICT (name) DO UPDATE SET active = TRUE RETURNING id""",
                    (data["category"].strip(),),
                ).fetchone()["id"]
            product = db.execute(
                """INSERT INTO products
                   (sku, name, category_id, supplier_id, selling_price, acquisition_cost, reorder_level)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (sku, name, category_id, supplier_id, price, cost, reorder),
            ).fetchone()
            _audit(db, "created", "product", product["id"], sku)
        return jsonify(id=product["id"], message="Product created."), 201
    except (ValueError, TypeError) as error:
        return _json_error(error)
    except UniqueViolation:
        return _json_error("A product with this SKU already exists.", 409)
    except ForeignKeyViolation:
        return _json_error("Choose an existing category and supplier.", 400)


@bp.patch("/api/products/<int:product_id>")
@permission_required("products.manage")
def product_update(product_id):
    data = _payload()
    try:
        with write_transaction() as db:
            product = db.execute("SELECT * FROM products WHERE id = %s AND active FOR UPDATE",
                                 (product_id,)).fetchone()
            if not product:
                abort(404)
            category_id = product["category_id"]
            if "category" in data:
                category = data["category"].strip()
                if category:
                    category_id = db.execute(
                        """INSERT INTO categories (name) VALUES (%s)
                           ON CONFLICT (name) DO UPDATE SET active = TRUE RETURNING id""",
                        (category,),
                    ).fetchone()["id"]
                else:
                    category_id = None
            reorder = int(data.get("reorder_level", product["reorder_level"]))
            if reorder < 0:
                raise ValueError("Reorder level cannot be negative.")
            db.execute(
                """UPDATE products SET name = %s, category_id = %s, supplier_id = %s,
                   selling_price = %s, reorder_level = %s WHERE id = %s""",
                (data.get("name", product["name"]).strip(), category_id,
                 int(data["supplier_id"]) if data.get("supplier_id") else None,
                 _decimal(data.get("selling_price", product["selling_price"]), "Selling price"),
                 reorder, product_id),
            )
            _audit(db, "updated", "product", product_id)
        return jsonify(message="Product details updated.")
    except (ValueError, TypeError) as error:
        return _json_error(error)


@bp.post("/api/packages")
@permission_required("products.manage")
def package_create():
    data = _payload()
    try:
        sku, name = data.get("sku", "").strip(), data.get("name", "").strip()
        components = data.get("components", [])
        if not sku or not name or not components:
            raise ValueError("Package SKU, name, and at least one product are required.")
        price = _decimal(data.get("selling_price"), "Package price")
        with write_transaction() as db:
            package = db.execute(
                "INSERT INTO packages (sku, name, selling_price) VALUES (%s, %s, %s) RETURNING id",
                (sku, name, price),
            ).fetchone()
            for component in components:
                db.execute(
                    """INSERT INTO package_components (package_id, product_id, quantity)
                       VALUES (%s, %s, %s)""",
                    (package["id"], _positive_int(component.get("product_id"), "Product"),
                     _positive_int(component.get("quantity"), "Component quantity")),
                )
            _audit(db, "created", "package", package["id"], sku)
        return jsonify(id=package["id"], message="Package created."), 201
    except (ValueError, TypeError) as error:
        return _json_error(error)
    except UniqueViolation:
        return _json_error("A package with this SKU already exists.", 409)
    except ForeignKeyViolation:
        return _json_error("Choose existing products for this package.", 400)


@bp.post("/api/products/<int:product_id>/deactivate")
@permission_required("products.manage")
def product_deactivate(product_id):
    with write_transaction() as db:
        result = db.execute(
            "UPDATE products SET active = FALSE WHERE id = %s AND active RETURNING id",
            (product_id,),
        ).fetchone()
        if not result:
            abort(404)
        _audit(db, "deactivated", "product", product_id)
    return jsonify(message="Product deactivated; its transaction history was retained.")


@bp.post("/api/packages/<int:package_id>/deactivate")
@permission_required("products.manage")
def package_deactivate(package_id):
    with write_transaction() as db:
        result = db.execute(
            "UPDATE packages SET active = FALSE WHERE id = %s AND active RETURNING id",
            (package_id,),
        ).fetchone()
        if not result:
            abort(404)
        _audit(db, "deactivated", "package", package_id)
    return jsonify(message="Package deactivated; its transaction history was retained.")


@bp.get("/api/suppliers")
@permission_required("suppliers.manage")
def suppliers_list():
    return jsonify(suppliers=get_db().execute(
        """SELECT s.id, s.name, s.contact_person, s.phone, s.email, s.address, s.notes,
                  COUNT(DISTINCT po.id) AS purchase_orders
           FROM suppliers s LEFT JOIN purchase_orders po ON po.supplier_id = s.id
           WHERE s.active GROUP BY s.id ORDER BY s.name"""
    ).fetchall())


@bp.post("/api/suppliers")
@permission_required("suppliers.manage")
def supplier_create():
    data = _payload()
    name = data.get("name", "").strip()
    if not name:
        return _json_error("Supplier name is required.")
    with write_transaction() as db:
        supplier = db.execute(
            """INSERT INTO suppliers (name, contact_person, phone, email, address, notes)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (name, data.get("contact_person", "").strip(), data.get("phone", "").strip(),
             data.get("email", "").strip(), data.get("address", "").strip(),
             data.get("notes", "").strip()),
        ).fetchone()
        _audit(db, "created", "supplier", supplier["id"], name)
    return jsonify(id=supplier["id"], message="Supplier created."), 201


@bp.patch("/api/suppliers/<int:supplier_id>")
@permission_required("suppliers.manage")
def supplier_update(supplier_id):
    data = _payload()
    name = data.get("name", "").strip()
    if not name:
        return _json_error("Supplier name is required.")
    with write_transaction() as db:
        result = db.execute(
            """UPDATE suppliers SET name = %s, contact_person = %s, phone = %s,
               email = %s, address = %s, notes = %s
               WHERE id = %s AND active RETURNING id""",
            (name, data.get("contact_person", "").strip(), data.get("phone", "").strip(),
             data.get("email", "").strip(), data.get("address", "").strip(),
             data.get("notes", "").strip(), supplier_id),
        ).fetchone()
        if not result:
            abort(404)
        _audit(db, "updated", "supplier", supplier_id, name)
    return jsonify(message="Supplier details updated.")

@bp.post("/api/suppliers/<int:supplier_id>/deactivate")
@permission_required("suppliers.manage")
def supplier_deactivate(supplier_id):
    with write_transaction() as db:
        result = db.execute(
            "UPDATE suppliers SET active = FALSE WHERE id = %s AND active RETURNING id",
            (supplier_id,),
        ).fetchone()
        if not result:
            abort(404)
        _audit(db, "deactivated", "supplier", supplier_id)
    return jsonify(message="Supplier deactivated; historical purchasing records were retained.")


@bp.post("/api/purchase-orders")
@permission_required("restocking.manage")
def purchase_order_create():
    data = _payload()
    lines = data.get("items", [])
    try:
        supplier_id = _positive_int(data.get("supplier_id"), "Supplier")
        status = data.get("status", "ordered")
        if status not in {"draft", "ordered"}:
            raise ValueError("A new purchase order must be a draft or ordered.")
        if not lines:
            raise ValueError("Add at least one product to the purchase order.")
        order_number = "PO-" + datetime.now().strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper()
        with write_transaction() as db:
            supplier = db.execute(
                "SELECT id FROM suppliers WHERE id = %s AND active = TRUE FOR UPDATE",
                (supplier_id,),
            ).fetchone()
            if not supplier:
                raise ValueError("Choose an active supplier.")
            order = db.execute(
                """INSERT INTO purchase_orders
                   (order_number, supplier_id, status, expected_date, created_by)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (order_number, supplier_id, status, data.get("expected_date") or None, g.user["id"]),
            ).fetchone()
            for line in lines:
                db.execute(
                    """INSERT INTO purchase_order_items
                       (purchase_order_id, product_id, quantity_ordered, expected_unit_cost)
                       VALUES (%s, %s, %s, %s)""",
                    (order["id"], _positive_int(line.get("product_id"), "Product"),
                     _positive_int(line.get("quantity"), "Order quantity"),
                     _decimal(line.get("unit_cost"), "Expected unit cost")),
                )
            _audit(db, "created", "purchase order", order["id"], order_number)
        return jsonify(id=order["id"], order_number=order_number), 201
    except (ValueError, TypeError) as error:
        return _json_error(error)


@bp.post("/api/purchase-orders/<int:order_id>/receive")
@permission_required("restocking.manage")
def purchase_order_receive(order_id):
    data = _payload()
    reference = data.get("reference", "").strip()
    lines = data.get("items", [])
    if not reference or not lines:
        return _json_error("Delivery reference and received quantities are required.")
    try:
        with write_transaction() as db:
            if db.execute(
                "SELECT 1 FROM receiving_receipts WHERE reference = %s", (reference,)
            ).fetchone():
                return _json_error("This delivery reference has already been posted.", 409)
            order = db.execute(
                """SELECT * FROM purchase_orders WHERE id = %s
                   AND status IN ('ordered', 'partially received') FOR UPDATE""",
                (order_id,),
            ).fetchone()
            if not order:
                raise ValueError("This order is not open for receiving.")
            receipt = db.execute(
                """INSERT INTO receiving_receipts (purchase_order_id, reference, received_by)
                   VALUES (%s, %s, %s) RETURNING id""",
                (order_id, reference, g.user["id"]),
            ).fetchone()
            for line in lines:
                order_item = db.execute(
                    """SELECT poi.*, p.stock_quantity, p.acquisition_cost
                       FROM purchase_order_items poi
                       JOIN products p ON p.id = poi.product_id
                       WHERE poi.id = %s AND poi.purchase_order_id = %s
                       FOR UPDATE OF poi, p""",
                    (int(line.get("item_id", 0)), order_id),
                ).fetchone()
                quantity = _positive_int(line.get("quantity"), "Received quantity")
                cost = _decimal(line.get("unit_cost", order_item["expected_unit_cost"] if order_item else 0),
                                "Accepted unit cost")
                if not order_item or quantity > order_item["quantity_ordered"] - order_item["quantity_received"]:
                    raise ValueError("Received quantity exceeds the open quantity on this order.")
                product = db.execute(
                    "SELECT stock_quantity, acquisition_cost FROM products WHERE id = %s FOR UPDATE",
                    (order_item["product_id"],),
                ).fetchone()
                previous = product["stock_quantity"]
                new_balance = previous + quantity
                new_cost = ((product["acquisition_cost"] * previous + cost * quantity) / new_balance
                            if new_balance else cost).quantize(CENT, rounding=ROUND_HALF_UP)
                db.execute(
                    """UPDATE products SET stock_quantity = %s, acquisition_cost = %s
                       WHERE id = %s""",
                    (new_balance, new_cost, order_item["product_id"]),
                )
                db.execute(
                    """UPDATE purchase_order_items SET quantity_received = quantity_received + %s
                       WHERE id = %s""",
                    (quantity, order_item["id"]),
                )
                db.execute(
                    """INSERT INTO receipt_items
                       (receipt_id, purchase_order_item_id, quantity, accepted_unit_cost)
                       VALUES (%s, %s, %s, %s)""",
                    (receipt["id"], order_item["id"], quantity, cost),
                )
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id, movement_type, quantity, previous_balance, resulting_balance,
                        reason, reference, user_id)
                       VALUES (%s, 'Stock In', %s, %s, %s, 'Supplier delivery', %s, %s)""",
                    (order_item["product_id"], quantity, previous, new_balance, reference, g.user["id"]),
                )
            remaining = db.execute(
                """SELECT COUNT(*) AS lines,
                          COUNT(*) FILTER (WHERE quantity_received = quantity_ordered) AS complete
                   FROM purchase_order_items WHERE purchase_order_id = %s""",
                (order_id,),
            ).fetchone()
            status = "received" if remaining["lines"] == remaining["complete"] else "partially received"
            db.execute("UPDATE purchase_orders SET status = %s WHERE id = %s", (status, order_id))
            _audit(db, "received", "purchase order", order_id, reference)
        return jsonify(message="Accepted quantities received and added to stock.", status=status)
    except (ValueError, TypeError, KeyError) as error:
        return _json_error(error)


@bp.get("/api/purchase-orders")
@permission_required("restocking.manage")
def purchase_orders_list():
    return jsonify(orders=get_db().execute(
        """SELECT po.id, po.order_number, po.status, po.expected_date, po.created_at,
                  s.name AS supplier,
                  SUM(poi.quantity_ordered) AS units_ordered,
                  SUM(poi.quantity_ordered - poi.quantity_received) AS units_open
           FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id
           JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
           GROUP BY po.id, s.name ORDER BY po.created_at DESC"""
    ).fetchall())


@bp.get("/api/purchase-orders/<int:order_id>")
@permission_required("restocking.manage")
def purchase_order_detail(order_id):
    db = get_db()
    order = db.execute(
        """SELECT po.id, po.order_number, po.status, s.name AS supplier
           FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id
           WHERE po.id = %s""",
        (order_id,),
    ).fetchone()
    if not order:
        abort(404)
    order["items"] = db.execute(
        """SELECT poi.id, p.sku, p.name, poi.quantity_ordered, poi.quantity_received,
                  poi.quantity_ordered - poi.quantity_received AS quantity_open,
                  poi.expected_unit_cost
           FROM purchase_order_items poi JOIN products p ON p.id = poi.product_id
           WHERE poi.purchase_order_id = %s ORDER BY poi.id""",
        (order_id,),
    ).fetchall()
    return jsonify(order=order)


@bp.patch("/api/purchase-orders/<int:order_id>")
@permission_required("restocking.manage")
def purchase_order_update(order_id):
    status = _payload().get("status")
    if status not in {"ordered", "cancelled"}:
        return _json_error("A purchase order can only be submitted or cancelled.")
    with write_transaction() as db:
        order = db.execute(
            """SELECT * FROM purchase_orders WHERE id = %s
               AND status IN ('draft', 'ordered', 'partially received') FOR UPDATE""",
            (order_id,),
        ).fetchone()
        if not order:
            abort(404)
        if status == "ordered" and order["status"] != "draft":
            return _json_error("Only a draft order can be submitted.", 409)
        db.execute("UPDATE purchase_orders SET status = %s WHERE id = %s", (status, order_id))
        _audit(db, status, "purchase order", order_id, order["order_number"])
    return jsonify(message=f"Purchase order {status}.")


@bp.post("/api/sales")
@permission_required("sales.create")
def sale_create():
    data = _payload()
    lines = data.get("items", [])
    if not lines:
        return _json_error("Add at least one product or package to the sale.")
    try:
        discount = _decimal(data.get("discount", 0), "Discount")
        vat_rate = Decimal(str(current_app.config["VAT_RATE"]))
        with write_transaction() as db:
            customer_id = None
            customer_data = data.get("customer", {})
            if customer_data.get("name", "").strip():
                customer = db.execute(
                    """INSERT INTO customers (name, tin, address, business_style)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (customer_data["name"].strip(), customer_data.get("tin", "").strip(),
                     customer_data.get("address", "").strip(),
                     customer_data.get("business_style", "").strip()),
                ).fetchone()
                customer_id = customer["id"]
            prepared = []
            subtotal = Decimal("0.00")
            for line in lines:
                quantity = _positive_int(line.get("quantity"), "Quantity")
                if line.get("package_id"):
                    item = db.execute(
                        "SELECT id, sku, name, selling_price FROM packages WHERE id = %s AND active",
                        (int(line["package_id"]),),
                    ).fetchone()
                    kind = "package"
                else:
                    item = db.execute(
                        "SELECT id, sku, name, selling_price FROM products WHERE id = %s AND active",
                        (int(line.get("product_id", 0)),),
                    ).fetchone()
                    kind = "product"
                if not item:
                    raise ValueError("A selected product or package is no longer available.")
                line_total = (item["selling_price"] * quantity).quantize(CENT)
                subtotal += line_total
                prepared.append((item, kind, quantity, line_total))
            if discount > subtotal:
                raise ValueError("Discount cannot exceed the sale subtotal.")
            vat_amount = ((subtotal - discount) * vat_rate).quantize(CENT, rounding=ROUND_HALF_UP)
            total = subtotal - discount + vat_amount
            sale_ref = "OBP-" + datetime.now().strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper()
            sale = db.execute(
                """INSERT INTO sales
                   (receipt_number, customer_id, customer_po_reference, status, discount,
                    vat_rate, vat_amount, subtotal, total, created_by)
                   VALUES (%s, %s, %s, 'open', %s, %s, %s, %s, %s, %s) RETURNING id""",
                (sale_ref, customer_id, data.get("customer_po_reference", "").strip(),
                 discount, vat_rate, vat_amount, subtotal, total, g.user["id"]),
            ).fetchone()
            created_items = []
            for item, kind, quantity, line_total in prepared:
                sale_item = db.execute(
                    """INSERT INTO sale_items
                       (sale_id, product_id, package_id, quantity, unit_price, line_total)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                    (sale["id"], item["id"] if kind == "product" else None,
                     item["id"] if kind == "package" else None,
                     quantity, item["selling_price"], line_total),
                ).fetchone()
                created_items.append({"sale_item_id": sale_item["id"], "quantity": quantity})
            tendered = _decimal(data.get("payment", 0), "Payment")
            payment = min(tendered, total)
            change = tendered - payment
            if payment > 0:
                db.execute(
                    """INSERT INTO payments (sale_id, amount, method, reference, received_by)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (sale["id"], payment, data.get("payment_method", "Cash").strip() or "Cash",
                     data.get("payment_reference", "").strip(), g.user["id"]),
                )
            payment_status = "paid" if payment >= total else "partial" if payment > 0 else "unpaid"
            db.execute("UPDATE sales SET payment_status = %s WHERE id = %s",
                       (payment_status, sale["id"]))
            _audit(db, "created", "sale", sale["id"], sale_ref)
        return jsonify(id=sale["id"], receipt_number=sale_ref, items=created_items,
                       subtotal=subtotal, vat=vat_amount, total=total, change=change,
                       message="Sale saved. Dispatch available quantities to complete the transaction."), 201
    except (ValueError, TypeError, InvalidOperation) as error:
        return _json_error(error)


@bp.post("/api/sales/<int:sale_id>/payments")
@permission_required("sales.create")
def sale_payment(sale_id):
    data = _payload()
    try:
        amount = _decimal(data.get("amount"), "Payment", minimum=Decimal("0.01"))
        with write_transaction() as db:
            sale = db.execute(
                "SELECT * FROM sales WHERE id = %s AND status <> 'cancelled' FOR UPDATE",
                (sale_id,),
            ).fetchone()
            if not sale:
                abort(404)
            paid = db.execute(
                "SELECT COALESCE(SUM(amount), 0) AS paid FROM payments WHERE sale_id = %s",
                (sale_id,),
            ).fetchone()["paid"]
            if amount > sale["total"] - paid:
                raise ValueError("Payment exceeds the outstanding balance.")
            db.execute(
                """INSERT INTO payments (sale_id, amount, method, reference, received_by)
                   VALUES (%s, %s, %s, %s, %s)""",
                (sale_id, amount, data.get("method", "Cash").strip() or "Cash",
                 data.get("reference", "").strip(), g.user["id"]),
            )
            new_paid = paid + amount
            db.execute("UPDATE sales SET payment_status = %s WHERE id = %s",
                       ("paid" if new_paid >= sale["total"] else "partial", sale_id))
            _audit(db, "payment recorded", "sale", sale_id, str(amount))
        return jsonify(message="Payment recorded.")
    except (ValueError, TypeError) as error:
        return _json_error(error)


@bp.post("/api/sales/<int:sale_id>/cancel")
@permission_required("sales.create")
def sale_cancel(sale_id):
    with write_transaction() as db:
        sale = db.execute(
            """SELECT * FROM sales WHERE id = %s AND status = 'open'
               AND dispatched_at IS NULL FOR UPDATE""",
            (sale_id,),
        ).fetchone()
        if not sale:
            return _json_error("Only an undispatched sale can be cancelled.", 409)
        paid = db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS amount FROM payments WHERE sale_id = %s",
            (sale_id,),
        ).fetchone()["amount"]
        if paid and not has_permission("refunds.approve"):
            abort(403)
        if paid:
            reference = "CAN-" + datetime.now().strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper()
            db.execute(
                """INSERT INTO sale_returns (sale_id, reference, reason, refund_amount, approved_by)
                   VALUES (%s, %s, 'Sale cancelled before dispatch', %s, %s)""",
                (sale_id, reference, paid, g.user["id"]),
            )
        db.execute(
            "UPDATE sales SET status = 'cancelled', payment_status = 'unpaid' WHERE id = %s",
            (sale_id,),
        )
        _audit(db, "cancelled", "sale", sale_id)
    return jsonify(message="Sale cancelled without changing stock.")


@bp.post("/api/sales/<int:sale_id>/dispatch")
@permission_required("sales.create")
def sale_dispatch(sale_id):
    data = _payload()
    quantities = data.get("items", [])
    if not quantities:
        return _json_error("Select at least one line to dispatch.")
    try:
        with write_transaction() as db:
            sale = db.execute(
                "SELECT * FROM sales WHERE id = %s AND status IN ('open', 'partially dispatched') FOR UPDATE",
                (sale_id,),
            ).fetchone()
            if not sale:
                raise ValueError("This sale is already dispatched or cannot be dispatched.")
            sale_items = db.execute(
                "SELECT * FROM sale_items WHERE sale_id = %s ORDER BY id FOR UPDATE",
                (sale_id,),
            ).fetchall()
            by_id = {item["id"]: item for item in sale_items}
            requested = {}
            for line in quantities:
                item_id = _positive_int(line.get("sale_item_id"), "Sale item")
                amount = _positive_int(line.get("quantity"), "Dispatch quantity")
                if item_id not in by_id:
                    raise ValueError("A sale line does not belong to this transaction.")
                requested[item_id] = requested.get(item_id, 0) + amount
            dispatch = db.execute(
                """INSERT INTO sale_dispatches (sale_id, dispatched_by)
                   VALUES (%s, %s) RETURNING id""",
                (sale_id, g.user["id"]),
            ).fetchone()
            for item_id, amount in requested.items():
                sale_item = by_id[item_id]
                if amount > sale_item["quantity"] - sale_item["dispatched_quantity"]:
                    raise ValueError("Dispatch quantity exceeds the undelivered quantity.")
                if sale_item["package_id"]:
                    components = db.execute(
                        """SELECT pc.product_id, pc.quantity AS units_per_package,
                                  p.stock_quantity, p.acquisition_cost, p.selling_price
                           FROM package_components pc JOIN products p ON p.id = pc.product_id
                           WHERE pc.package_id = %s AND p.active
                           ORDER BY p.id FOR UPDATE OF p""",
                        (sale_item["package_id"],),
                    ).fetchall()
                    if not components:
                        raise ValueError("This package has no active product components.")
                else:
                    product = db.execute(
                        """SELECT id AS product_id, 1 AS units_per_package,
                                  stock_quantity, acquisition_cost, selling_price
                           FROM products WHERE id = %s AND active FOR UPDATE""",
                        (sale_item["product_id"],),
                    ).fetchone()
                    components = [product] if product else []
                    if not components:
                        raise ValueError("This product is no longer available.")
                required = [(part, amount * part["units_per_package"]) for part in components]
                for part, units in required:
                    if part["stock_quantity"] < units:
                        raise ValueError(f"Insufficient stock for product {part['product_id']}.")
                gross_revenue = (sale_item["unit_price"] * amount).quantize(CENT)
                discount_amount = (
                    (gross_revenue * sale["discount"] / sale["subtotal"]).quantize(CENT)
                    if sale["subtotal"] else Decimal("0.00")
                )
                vat_amount = ((gross_revenue - discount_amount) * sale["vat_rate"]).quantize(CENT)
                weights = [
                    part["selling_price"] * part["units_per_package"]
                    for part, _ in required
                ]
                component_value = sum(weights, Decimal("0"))
                if not component_value:
                    weights = [
                        part["acquisition_cost"] * part["units_per_package"]
                        for part, _ in required
                    ]
                    component_value = sum(weights, Decimal("0"))
                if not component_value:
                    weights = [Decimal("1") for _part, _units in required]
                    component_value = Decimal(len(required))
                for index, (part, units) in enumerate(required):
                    previous = part["stock_quantity"]
                    result = previous - units
                    db.execute("UPDATE products SET stock_quantity = %s WHERE id = %s",
                               (result, part["product_id"]))
                    share = weights[index] / component_value
                    db.execute(
                        """INSERT INTO stock_movements
                           (product_id, movement_type, quantity, previous_balance,
                            resulting_balance, unit_cost, reason, reference, user_id)
                           VALUES (%s, 'Stock Out', %s, %s, %s, %s, 'Sales dispatch', %s, %s)""",
                        (part["product_id"], units, previous, result,
                         part["acquisition_cost"], sale["receipt_number"], g.user["id"]),
                    )
                    component_gross = (gross_revenue * share).quantize(CENT)
                    component_discount = (discount_amount * share).quantize(CENT)
                    component_vat = (vat_amount * share).quantize(CENT)
                    db.execute(
                        """INSERT INTO sale_dispatch_items
                           (dispatch_id, sale_item_id, product_id, quantity, unit_cost,
                            revenue_amount, gross_revenue, discount_amount, vat_amount)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (dispatch["id"], item_id, part["product_id"], units,
                         part["acquisition_cost"],
                         component_gross - component_discount - component_vat,
                         component_gross, component_discount, component_vat),
                    )
                unit_cost = sum(
                    (part["acquisition_cost"] * part["units_per_package"] for part, _ in required),
                    Decimal("0"),
                )
                db.execute(
                    """UPDATE sale_items SET dispatched_quantity = dispatched_quantity + %s,
                       unit_cost = %s WHERE id = %s""",
                    (amount, unit_cost, item_id),
                )
            fully_dispatched = db.execute(
                """SELECT COUNT(*) AS lines,
                          COUNT(*) FILTER (WHERE dispatched_quantity = quantity) AS complete
                   FROM sale_items WHERE sale_id = %s""",
                (sale_id,),
            ).fetchone()
            status = "dispatched" if fully_dispatched["lines"] == fully_dispatched["complete"] else "partially dispatched"
            db.execute(
                """UPDATE sales SET status = %s, dispatched_at = COALESCE(dispatched_at, now())
                   WHERE id = %s""",
                (status, sale_id),
            )
            _audit(db, "dispatched", "sale", sale_id, status)
        return jsonify(status=status, message="Dispatched quantities and stock movements were saved together.")
    except (ValueError, TypeError) as error:
        return _json_error(error)


@bp.post("/api/sales/<int:sale_id>/returns")
@permission_required("refunds.approve")
def sale_return(sale_id):
    data = _payload()
    reason = data.get("reason", "").strip()
    lines = data.get("items", [])
    if not reason or not lines:
        return _json_error("A return reason and at least one returned sale line are required.")
    try:
        with write_transaction() as db:
            sale = db.execute(
                "SELECT * FROM sales WHERE id = %s AND status IN ('partially dispatched', 'dispatched', 'refunded') FOR UPDATE",
                (sale_id,),
            ).fetchone()
            if not sale:
                raise ValueError("Returns must reference a dispatched sale.")
            paid = db.execute(
                "SELECT COALESCE(SUM(amount), 0) AS amount FROM payments WHERE sale_id = %s",
                (sale_id,),
            ).fetchone()["amount"]
            already_refunded = db.execute(
                "SELECT COALESCE(SUM(refund_amount), 0) AS amount FROM sale_returns WHERE sale_id = %s",
                (sale_id,),
            ).fetchone()["amount"]
            refund = Decimal("0.00")
            prepared = []
            for line in lines:
                sale_item_id = _positive_int(line.get("sale_item_id"), "Sale item")
                quantity = _positive_int(line.get("quantity"), "Return quantity")
                saleable = int(line.get("saleable_quantity", 0))
                sale_item = db.execute(
                    "SELECT * FROM sale_items WHERE id = %s AND sale_id = %s FOR UPDATE",
                    (sale_item_id, sale_id),
                ).fetchone()
                if not sale_item or saleable < 0 or saleable > quantity:
                    raise ValueError("Return quantity or saleable quantity is invalid.")
                if quantity > sale_item["dispatched_quantity"] - sale_item["returned_quantity"]:
                    raise ValueError("Return quantity exceeds the quantity dispatched less earlier returns.")
                price_after_discount = sale_item["unit_price"] * (
                    Decimal("1") - (sale["discount"] / sale["subtotal"] if sale["subtotal"] else 0)
                )
                unit_refund = (price_after_discount * (Decimal("1") + sale["vat_rate"])).quantize(CENT)
                refund += unit_refund * quantity
                prepared.append((sale_item, quantity, saleable, unit_refund))
            if refund > paid - already_refunded:
                raise ValueError("Refund cannot exceed payments received less earlier refunds.")
            reference = "RET-" + datetime.now().strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper()
            return_row = db.execute(
                """INSERT INTO sale_returns (sale_id, reference, reason, refund_amount, approved_by)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (sale_id, reference, reason, refund, g.user["id"]),
            ).fetchone()
            for sale_item, quantity, saleable, unit_refund in prepared:
                left, good_left = quantity, saleable
                dispatches = db.execute(
                    """SELECT di.*, COALESCE(pc.quantity, 1) AS units_per_package
                       FROM sale_dispatch_items di
                       LEFT JOIN package_components pc
                         ON pc.package_id = %s AND pc.product_id = di.product_id
                       WHERE di.sale_item_id = %s AND di.returned_quantity < di.quantity
                       ORDER BY di.dispatched_at, di.id FOR UPDATE OF di""",
                    (sale_item["package_id"], sale_item["id"]),
                ).fetchall()
                batches = {}
                for dispatch in dispatches:
                    batches.setdefault(dispatch["dispatch_id"], []).append(dispatch)
                for batch in batches.values():
                    package_capacity = min(
                        (part["quantity"] - part["returned_quantity"]) // part["units_per_package"]
                        for part in batch
                    )
                    take_packages = min(package_capacity, left)
                    if not take_packages:
                        continue
                    good_packages = min(take_packages, good_left)
                    bad_packages = take_packages - good_packages
                    batch_value = sum((part["gross_revenue"] for part in batch), Decimal("0"))
                    for part in batch:
                        units = take_packages * part["units_per_package"]
                        good_units = good_packages * part["units_per_package"]
                        bad_units = bad_packages * part["units_per_package"]
                        share = part["gross_revenue"] / batch_value if batch_value else Decimal("0")
                        refund_share = (unit_refund * take_packages * share).quantize(CENT)
                        net_reversed = (
                            part["revenue_amount"] * units / part["quantity"]
                        ).quantize(CENT)
                        db.execute(
                            """INSERT INTO return_items
                               (return_id, sale_item_id, dispatch_item_id, quantity,
                                saleable_quantity, unit_cost, refund_amount, net_revenue_reversed)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                            (return_row["id"], sale_item["id"], part["id"], units,
                             good_units, part["unit_cost"], refund_share, net_reversed),
                        )
                        db.execute(
                            """UPDATE sale_dispatch_items SET returned_quantity = returned_quantity + %s
                               WHERE id = %s""",
                            (units, part["id"]),
                        )
                        if good_units:
                            product = db.execute(
                                "SELECT stock_quantity, acquisition_cost FROM products WHERE id = %s FOR UPDATE",
                                (part["product_id"],),
                            ).fetchone()
                            result = product["stock_quantity"] + good_units
                            average_cost = (
                                (product["acquisition_cost"] * product["stock_quantity"]
                                 + part["unit_cost"] * good_units) / result
                            ).quantize(CENT, rounding=ROUND_HALF_UP) if result else part["unit_cost"]
                            db.execute(
                                """UPDATE products SET stock_quantity = %s, acquisition_cost = %s
                                   WHERE id = %s""",
                                (result, average_cost, part["product_id"]),
                            )
                            db.execute(
                                """INSERT INTO stock_movements
                                   (product_id, movement_type, quantity, previous_balance,
                                    resulting_balance, unit_cost, reason, reference, user_id)
                                   VALUES (%s, 'Stock In', %s, %s, %s, %s, 'Saleable customer return', %s, %s)""",
                                (part["product_id"], good_units, product["stock_quantity"], result,
                                 part["unit_cost"], reference, g.user["id"]),
                            )
                        if bad_units:
                            balance = db.execute(
                                "SELECT stock_quantity FROM products WHERE id = %s FOR UPDATE",
                                (part["product_id"],),
                            ).fetchone()["stock_quantity"]
                            db.execute(
                                """INSERT INTO stock_movements
                                   (product_id, movement_type, quantity, previous_balance,
                                    resulting_balance, unit_cost, affects_stock, reason, reference, user_id)
                                   VALUES (%s, 'Loss', %s, %s, %s, %s, FALSE, 'Defective customer return', %s, %s)""",
                                (part["product_id"], bad_units, balance, balance,
                                 part["unit_cost"], reference, g.user["id"]),
                            )
                    left -= take_packages
                    good_left -= good_packages
                    if not left:
                        break
                if left:
                    raise ValueError("Return quantity could not be reconciled to dispatched stock.")
                db.execute(
                    "UPDATE sale_items SET returned_quantity = returned_quantity + %s WHERE id = %s",
                    (quantity, sale_item["id"]),
                )
            status = db.execute(
                """SELECT COUNT(*) AS items,
                          COUNT(*) FILTER (
                            WHERE dispatched_quantity = quantity
                              AND returned_quantity = dispatched_quantity
                          ) AS returned
                   FROM sale_items WHERE sale_id = %s""",
                (sale_id,),
            ).fetchone()
            if status["items"] == status["returned"]:
                db.execute("UPDATE sales SET status = 'refunded' WHERE id = %s", (sale_id,))
            _audit(db, "refund approved", "sale", sale_id, reference)
            return jsonify(reference=reference, refund=refund, message="Return and stock movements were recorded.")
    except (ValueError, TypeError, KeyError) as error:
        return _json_error(error)


@bp.get("/api/sales")
@permission_required("sales.read")
def sales_list():
    return jsonify(sales=get_db().execute(
        """SELECT s.id, s.receipt_number, s.status, s.payment_status, s.subtotal, s.discount, s.vat_amount,
                  s.total, s.created_at, s.dispatched_at,
                  COALESCE(c.name, 'Walk-in customer') AS customer,
                  COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.sale_id = s.id), 0) AS paid
           FROM sales s LEFT JOIN customers c ON c.id = s.customer_id
           ORDER BY s.created_at DESC LIMIT 200"""
    ).fetchall())


@bp.get("/api/sales/<int:sale_id>")
@permission_required("sales.read")
def sale_detail(sale_id):
    db = get_db()
    sale = db.execute("SELECT id, receipt_number, status FROM sales WHERE id = %s", (sale_id,)).fetchone()
    if not sale:
        abort(404)
    sale["items"] = db.execute(
        """SELECT si.id, si.product_id, si.package_id, si.quantity, si.dispatched_quantity,
                  p.stock_quantity,
                  (SELECT MIN(FLOOR(cp.stock_quantity::numeric / pc.quantity))
                   FROM package_components pc JOIN products cp ON cp.id = pc.product_id
                   WHERE pc.package_id = si.package_id AND cp.active) AS available_quantity
           FROM sale_items si LEFT JOIN products p ON p.id = si.product_id
           WHERE si.sale_id = %s ORDER BY si.id""",
        (sale_id,),
    ).fetchall()
    return jsonify(sale=sale)


@bp.get("/api/receipts")
@permission_required("receipts.read")
def receipts_list():
    search = request.args.get("q", "").strip()
    return jsonify(receipts=get_db().execute(
        """SELECT s.id, s.receipt_number, s.status, s.total, s.created_at,
                  COALESCE(c.name, 'Walk-in customer') AS customer
           FROM sales s LEFT JOIN customers c ON c.id = s.customer_id
           WHERE (%s = '' OR s.receipt_number ILIKE %s OR c.name ILIKE %s)
           ORDER BY s.created_at DESC LIMIT 100""",
        (search, f"%{search}%", f"%{search}%"),
    ).fetchall())


@bp.get("/api/receipts/<int:sale_id>")
@permission_required("receipts.read")
def receipt_detail(sale_id):
    db = get_db()
    sale = db.execute(
        """SELECT s.*, c.name AS customer_name, c.tin, c.address, c.business_style,
                  u.display_name AS cashier
           FROM sales s LEFT JOIN customers c ON c.id = s.customer_id
           LEFT JOIN users u ON u.id = s.created_by WHERE s.id = %s""",
        (sale_id,),
    ).fetchone()
    if not sale:
        abort(404)
    sale["items"] = db.execute(
        """SELECT si.id, COALESCE(p.name, pk.name) AS name,
                  COALESCE(p.sku, pk.sku) AS sku, si.quantity,
                  si.dispatched_quantity, si.returned_quantity, si.unit_price, si.line_total
           FROM sale_items si LEFT JOIN products p ON p.id = si.product_id
           LEFT JOIN packages pk ON pk.id = si.package_id
           WHERE si.sale_id = %s ORDER BY si.id""",
        (sale_id,),
    ).fetchall()
    sale["payments"] = db.execute(
        "SELECT amount, method, reference, received_at FROM payments WHERE sale_id = %s ORDER BY id",
        (sale_id,),
    ).fetchall()
    sale["returns"] = db.execute(
        "SELECT reference, reason, refund_amount, created_at FROM sale_returns WHERE sale_id = %s ORDER BY id",
        (sale_id,),
    ).fetchall()
    return jsonify(receipt=sale)


@bp.get("/api/inventory/movements")
@permission_required("inventory.read")
def movement_history():
    data = request.args
    rows = get_db().execute(
        """SELECT m.id, m.created_at, p.sku, p.name, m.movement_type, m.quantity,
                  m.previous_balance, m.resulting_balance, m.reason, m.reference,
                  COALESCE(u.display_name, 'System') AS staff
           FROM stock_movements m JOIN products p ON p.id = m.product_id
           LEFT JOIN users u ON u.id = m.user_id
           WHERE (%s = '' OR p.name ILIKE %s OR p.sku ILIKE %s)
             AND (%s = '' OR m.movement_type = %s)
             AND (%s = '' OR u.display_name ILIKE %s)
             AND (%s::date IS NULL OR m.created_at::date >= %s::date)
             AND (%s::date IS NULL OR m.created_at::date <= %s::date)
           ORDER BY m.created_at DESC LIMIT 500""",
        (data.get("product", ""), f"%{data.get('product', '')}%", f"%{data.get('product', '')}%",
         data.get("type", ""), data.get("type", ""), data.get("staff", ""), f"%{data.get('staff', '')}%",
         data.get("from", "") or None, data.get("from", "") or None,
         data.get("to", "") or None, data.get("to", "") or None),
    ).fetchall()
    return jsonify(movements=rows)


@bp.get("/api/inventory/movements/export")
@permission_required("inventory.read")
def movement_history_export():
    filters = request.args
    product = filters.get("product", "")
    movement_type = filters.get("type", "")
    staff = filters.get("staff", "")
    rows = get_db().execute(
        """SELECT m.created_at, p.sku, p.name, m.movement_type, m.quantity,
                  m.previous_balance, m.resulting_balance, m.reason, m.reference,
                  COALESCE(u.display_name, 'System') AS staff
           FROM stock_movements m JOIN products p ON p.id = m.product_id
           LEFT JOIN users u ON u.id = m.user_id
           WHERE (%s = '' OR p.name ILIKE %s OR p.sku ILIKE %s)
             AND (%s = '' OR m.movement_type = %s)
             AND (%s = '' OR u.display_name ILIKE %s)
             AND (%s::date IS NULL OR m.created_at::date >= %s::date)
             AND (%s::date IS NULL OR m.created_at::date <= %s::date)
           ORDER BY m.created_at DESC""",
        (product, f"%{product}%", f"%{product}%", movement_type, movement_type,
         staff, f"%{staff}%", filters.get("from") or None, filters.get("from") or None,
         filters.get("to") or None, filters.get("to") or None),
    ).fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Date and time", "SKU", "Product", "Movement", "Quantity",
                     "Previous balance", "Resulting balance", "Reason", "Reference", "Staff"])
    for row in rows:
        writer.writerow([row["created_at"], row["sku"], row["name"], row["movement_type"],
                         row["quantity"], row["previous_balance"], row["resulting_balance"],
                         row["reason"], row["reference"], row["staff"]])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=obp-stock-movements.csv"},
    )


@bp.post("/api/inventory/adjustments")
@permission_required("inventory.adjust.submit")
def adjustment_submit():
    data = _payload()
    try:
        product_id = _positive_int(data.get("product_id"), "Product")
        quantity_change = int(data.get("quantity_change"))
        reason = data.get("reason", "").strip()
        movement_type = data.get(
            "movement_type", "Adjustment In" if quantity_change > 0 else "Adjustment Out"
        )
        if quantity_change == 0 or not reason:
            raise ValueError("Enter a non-zero adjustment and a reason.")
        if movement_type not in {"Adjustment In", "Adjustment Out", "Loss"}:
            raise ValueError("Choose an adjustment or inventory loss movement.")
        if (quantity_change > 0 and movement_type != "Adjustment In") or (
            quantity_change < 0 and movement_type == "Adjustment In"
        ):
            raise ValueError("Movement type must match the direction of the quantity change.")
        with write_transaction() as db:
            request_id = db.execute(
                """INSERT INTO stock_adjustment_requests
                   (product_id, quantity_change, movement_type, reason, requested_by)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (product_id, quantity_change, movement_type, reason, g.user["id"]),
            ).fetchone()["id"]
            _audit(db, "submitted", "stock adjustment", request_id, reason)
        return jsonify(id=request_id, message="Adjustment submitted for administrator approval."), 201
    except (ValueError, TypeError) as error:
        return _json_error(error)


@bp.get("/api/inventory/adjustments")
@permission_required("stock.adjustments.approve")
def adjustment_list():
    return jsonify(adjustments=get_db().execute(
        """SELECT a.*, p.sku, p.name AS product,
                  u.display_name AS requester
           FROM stock_adjustment_requests a JOIN products p ON p.id = a.product_id
           LEFT JOIN users u ON u.id = a.requested_by
           WHERE a.status = 'pending' ORDER BY a.created_at"""
    ).fetchall())


@bp.post("/api/inventory/adjustments/<int:request_id>/review")
@permission_required("stock.adjustments.approve")
def adjustment_review(request_id):
    data = _payload()
    decision = data.get("decision")
    if decision not in {"approve", "reject"}:
        return _json_error("Decision must be approve or reject.")
    try:
        with write_transaction() as db:
            adjustment = db.execute(
                "SELECT * FROM stock_adjustment_requests WHERE id = %s AND status = 'pending' FOR UPDATE",
                (request_id,),
            ).fetchone()
            if not adjustment:
                abort(404)
            if decision == "approve":
                product = db.execute(
                    "SELECT stock_quantity, acquisition_cost FROM products WHERE id = %s FOR UPDATE",
                    (adjustment["product_id"],),
                ).fetchone()
                previous = product["stock_quantity"]
                result = previous + adjustment["quantity_change"]
                if result < 0:
                    raise ValueError("Adjustment would reduce stock below zero.")
                movement = adjustment["movement_type"]
                db.execute("UPDATE products SET stock_quantity = %s WHERE id = %s",
                           (result, adjustment["product_id"]))
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id, movement_type, quantity, previous_balance, resulting_balance,
                        unit_cost, reason, reference, user_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (adjustment["product_id"], movement, abs(adjustment["quantity_change"]),
                     previous, result, product["acquisition_cost"], adjustment["reason"],
                     f"ADJ-{request_id}", g.user["id"]),
                )
            db.execute(
                """UPDATE stock_adjustment_requests SET status = %s, reviewed_by = %s,
                   reviewed_at = now() WHERE id = %s""",
                ("approved" if decision == "approve" else "rejected", g.user["id"], request_id),
            )
            _audit(db, decision + "d", "stock adjustment", request_id, adjustment["reason"])
        return jsonify(message=f"Adjustment {decision}d.")
    except ValueError as error:
        return _json_error(error)


@bp.get("/api/reports")
@permission_required("reports.read")
def reports_data():
    kind = request.args.get("type", "daily")
    today = date.today()
    try:
        if kind == "daily":
            selected = date.fromisoformat(request.args.get("date", today.isoformat()))
            return jsonify(type=kind, date=selected.isoformat(), summary=daily_sales(selected))
        if kind == "monthly":
            selected = date.fromisoformat(request.args.get("month", today.strftime("%Y-%m")) + "-01")
            rows, totals, previous = monthly_sales(selected.year, selected.month)
            return jsonify(type=kind, month=selected.strftime("%Y-%m"), days=rows,
                           totals=totals, previous_month_sales=previous)
        start, end = default_range()
        start = date.fromisoformat(request.args.get("from", start.isoformat()))
        end = date.fromisoformat(request.args.get("to", end.isoformat()))
        if end < start:
            raise ValueError("End date must be on or after start date.")
        category = request.args.get("category_id") or None
        category = int(category) if category else None
        if kind == "best":
            return jsonify(type=kind, from_date=start.isoformat(), to_date=end.isoformat(),
                           products=best_sellers(start, end, category))
        if kind == "profit":
            return jsonify(type=kind, from_date=start.isoformat(), to_date=end.isoformat(),
                           summary=profit_report(start, end, category))
        return _json_error("Choose daily, monthly, best, or profit.")
    except (ValueError, TypeError) as error:
        return _json_error(error)


@bp.get("/api/reports/export")
@permission_required("reports.export")
def reports_export():
    try:
        kind = request.args.get("type", "best")
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        filename = f"obp-{kind}-report.csv"
        if kind == "daily":
            selected = date.fromisoformat(request.args.get("date", date.today().isoformat()))
            summary = daily_sales(selected)
            writer.writerow(["Daily sales report", selected.isoformat()])
            writer.writerow(["Metric", "Value"])
            for key, value in summary.items():
                writer.writerow([key.replace("_", " ").title(), value])
        elif kind == "monthly":
            selected = date.fromisoformat(request.args.get("month", date.today().strftime("%Y-%m")) + "-01")
            rows, totals, previous = monthly_sales(selected.year, selected.month)
            writer.writerow(["Monthly sales report", selected.strftime("%Y-%m")])
            writer.writerow(["Metric", "Value"])
            for key, value in totals.items():
                writer.writerow([key.replace("_", " ").title(), value])
            writer.writerow(["Previous month net sales", previous])
            writer.writerow([])
            writer.writerow(["Day", "Transactions", "Sales value", "Discounts", "Refunds", "VAT", "Net sales"])
            for row in rows:
                writer.writerow([row["day"], row["transactions"], row["sales_value"],
                                 row["discounts"], row["refunds"], row["vat"], row["net_sales"]])
        elif kind in {"best", "profit"}:
            start_default, end_default = default_range()
            start = date.fromisoformat(request.args.get("from", start_default.isoformat()))
            end = date.fromisoformat(request.args.get("to", end_default.isoformat()))
            if end < start:
                raise ValueError("End date must be on or after start date.")
            if kind == "best":
                category = request.args.get("category_id") or None
                category = int(category) if category else None
                rows = best_sellers(start, end, category)
                writer.writerow(["SKU", "Product", "Category", "Net quantity sold", "Net sales"])
                for row in rows:
                    writer.writerow([row["sku"], row["name"], row["category"], row["net_quantity"], row["net_sales"]])
            else:
                category = request.args.get("category_id") or None
                category = int(category) if category else None
                summary = profit_report(start, end, category)
                writer.writerow(["Gross profit report", f"{start.isoformat()} – {end.isoformat()}"])
                writer.writerow(["Metric", "Value"])
                for key, value in summary.items():
                    writer.writerow([key.replace("_", " ").title(), value])
        else:
            return _json_error("Choose daily, monthly, best, or profit.")
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except ValueError as error:
        return _json_error(error)


@bp.get("/api/users")
@permission_required("users.manage")
def users_list():
    return jsonify(users=get_db().execute(
        """SELECT u.id, u.username, u.display_name, u.active, r.id AS role_id, r.name AS role_name
           FROM users u JOIN roles r ON r.id = u.role_id ORDER BY u.display_name"""
    ).fetchall())


@bp.post("/api/users")
@permission_required("users.manage")
def user_create():
    data = _payload()
    username, display_name, password = (
        data.get("username", "").strip(),
        data.get("display_name", "").strip(),
        data.get("password", ""),
    )
    if not username or not display_name or len(password) < 12:
        return _json_error("Username and display name are required; password must be at least 12 characters.")
    try:
        with write_transaction() as db:
            user = db.execute(
                """INSERT INTO users (username, display_name, password_hash, role_id)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (username, display_name, generate_password_hash(password),
                 _positive_int(data.get("role_id"), "Role")),
            ).fetchone()
            _audit(db, "created", "user", user["id"], username)
        return jsonify(id=user["id"], message="Staff account created."), 201
    except UniqueViolation:
        return _json_error("That username is already in use.", 409)


@bp.patch("/api/users/<int:user_id>")
@permission_required("users.manage")
def user_update(user_id):
    data = _payload()
    try:
        with write_transaction() as db:
            target = db.execute(
                """SELECT u.*, r.permissions FROM users u JOIN roles r ON r.id = u.role_id
                   WHERE u.id = %s FOR UPDATE OF u""",
                (user_id,),
            ).fetchone()
            if not target:
                abort(404)
            active = data.get("active", target["active"])
            if not isinstance(active, bool):
                raise ValueError("Account status must be true or false.")
            role_id = _positive_int(data["role_id"], "Role") if data.get("role_id") else target["role_id"]
            new_role = db.execute("SELECT permissions FROM roles WHERE id = %s", (role_id,)).fetchone()
            if not new_role:
                raise ValueError("Select an existing role.")
            removing_admin = "*" in (target["permissions"] or []) and (
                not active or "*" not in (new_role["permissions"] or [])
            )
            if removing_admin:
                other_admin = db.execute(
                    """SELECT COUNT(*) AS count FROM users u JOIN roles r ON r.id = u.role_id
                       WHERE u.active AND u.id <> %s AND r.permissions @> ARRAY['*']::text[]""",
                    (user_id,),
                ).fetchone()["count"]
                if not other_admin:
                    raise ValueError("The final active administrator cannot be deactivated or lose administrator access.")
            db.execute(
                """UPDATE users SET display_name = %s, role_id = %s, active = %s
                   WHERE id = %s""",
                (data.get("display_name", target["display_name"]).strip(), role_id, active, user_id),
            )
            if data.get("password"):
                if len(data["password"]) < 12:
                    raise ValueError("Password must be at least 12 characters.")
                db.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                           (generate_password_hash(data["password"]), user_id))
            _audit(db, "updated", "user", user_id)
        return jsonify(message="Staff account updated.")
    except (ValueError, TypeError) as error:
        return _json_error(error)


@bp.get("/api/roles")
@permission_required("roles.manage")
def roles_list():
    return jsonify(roles=get_db().execute("SELECT id, name, permissions FROM roles ORDER BY name").fetchall())


@bp.put("/api/roles/<int:role_id>")
@permission_required("roles.manage")
def role_update(role_id):
    data = _payload()
    permissions = data.get("permissions")
    if not isinstance(permissions, list) or not all(isinstance(item, str) for item in permissions):
        return _json_error("Permissions must be a list of permission names.")
    try:
        with write_transaction() as db:
            role = db.execute("SELECT name FROM roles WHERE id = %s FOR UPDATE", (role_id,)).fetchone()
            if not role:
                abort(404)
            if role["name"] == "Administrator / Owner" and "*" not in permissions:
                admins = db.execute(
                    """SELECT COUNT(*) AS count FROM users u JOIN roles r ON r.id = u.role_id
                       WHERE u.active AND r.permissions @> ARRAY['*']::text[]"""
                ).fetchone()["count"]
                if admins <= 1:
                    raise ValueError("The final active administrator role must retain full access.")
            db.execute("UPDATE roles SET permissions = %s WHERE id = %s", (permissions, role_id))
            _audit(db, "permissions changed", "role", role_id, ", ".join(permissions))
        return jsonify(message="Role permissions updated.")
    except ValueError as error:
        return _json_error(error)


@bp.post("/api/backups")
@permission_required("backup.manage")
def backup_create():
    try:
        filename = create_backup(g.user["username"])
        with write_transaction() as db:
            _audit(db, "backup created", "backup", filename)
        return jsonify(filename=filename, message="Database backup completed."), 201
    except Exception as error:
        current_app.logger.exception("Database backup failed")
        return _json_error(f"Backup failed: {error}", 500)


@bp.post("/api/backups/restore")
@permission_required("backup.manage")
def backup_restore():
    data = _payload()
    if data.get("confirmation") != "RESTORE":
        return _json_error("Type RESTORE to confirm replacing the active database.")
    filename = data.get("filename", "")
    try:
        before = restore_backup(filename, g.user["username"])
        with write_transaction() as db:
            _audit(db, "database restored", "backup", filename, f"Pre-restore copy: {before}")
        return jsonify(message="Database restored and checked.", pre_restore_backup=before)
    except ValueError as error:
        return _json_error(f"Restore rejected: {error}", 400)
    except RuntimeError as error:
        current_app.logger.exception("Database restore failed")
        return _json_error(f"Restore failed: {error}", 500)


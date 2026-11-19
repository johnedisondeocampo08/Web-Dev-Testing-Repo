from datetime import date
from .db import get_db


def daily_sales(selected_date):
    db = get_db()
    summary = db.execute(
        """WITH dispatched AS (
             SELECT si.sale_id, SUM(di.gross_revenue) AS sales_value,
                    SUM(di.discount_amount) AS discounts, SUM(di.vat_amount) AS vat,
                    SUM(di.revenue_amount) AS net_sales, SUM(di.quantity) AS quantity
             FROM sale_dispatch_items di JOIN sale_items si ON si.id = di.sale_item_id
             WHERE di.dispatched_at::date = %s GROUP BY si.sale_id
           ), refunded AS (
             SELECT COALESCE(SUM(refund_amount), 0) AS refunds
             FROM sale_returns WHERE created_at::date = %s
           ), refund_details AS (
             SELECT COALESCE(SUM(ri.net_revenue_reversed), 0) AS net_refunds,
                    COALESCE(SUM(ri.quantity), 0) AS quantity
             FROM return_items ri JOIN sale_returns r ON r.id = ri.return_id
             WHERE r.created_at::date = %s
           )
           SELECT (SELECT COUNT(*) FROM dispatched) AS transaction_count,
                  COALESCE((SELECT SUM(sales_value) FROM dispatched), 0) AS sales_value,
                  COALESCE((SELECT SUM(discounts) FROM dispatched), 0) AS discounts,
                  COALESCE((SELECT SUM(vat) FROM dispatched), 0) AS vat,
                  (SELECT refunds FROM refunded) AS refunds,
                  COALESCE((SELECT SUM(net_sales) FROM dispatched), 0)
                    - (SELECT net_refunds FROM refund_details) AS net_sales,
                  COALESCE((SELECT SUM(amount) FROM payments
                            WHERE received_at::date = %s), 0) AS payments,
                  (SELECT net_refunds FROM refund_details) AS net_refunds,
                  COALESCE((SELECT SUM(quantity) FROM dispatched), 0)
                    - (SELECT quantity FROM refund_details) AS quantities_sold""",
        (selected_date, selected_date, selected_date, selected_date),
    ).fetchone()
    summary["outstanding"] = max(
        0,
        get_db().execute(
            """SELECT COALESCE(SUM(GREATEST(s.total -
                     (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.sale_id = s.id) -
                     (SELECT COALESCE(SUM(r.refund_amount), 0) FROM sale_returns r WHERE r.sale_id = s.id), 0)), 0) AS value
               FROM sales s WHERE s.id IN (
                   SELECT DISTINCT si.sale_id FROM sale_dispatch_items di
                   JOIN sale_items si ON si.id = di.sale_item_id
                   WHERE di.dispatched_at::date = %s
               )""",
            (selected_date,),
        ).fetchone()["value"],
    )
    return summary


def monthly_sales(year, month):
    db = get_db()
    rows = db.execute(
        """WITH dispatched AS (
             SELECT di.dispatched_at::date AS day, COUNT(DISTINCT si.sale_id) AS transactions,
                    SUM(di.gross_revenue) AS sales_value, SUM(di.discount_amount) AS discounts,
                    SUM(di.vat_amount) AS vat, SUM(di.revenue_amount) AS net_sales
             FROM sale_dispatch_items di JOIN sale_items si ON si.id = di.sale_item_id
             WHERE EXTRACT(YEAR FROM di.dispatched_at) = %s
               AND EXTRACT(MONTH FROM di.dispatched_at) = %s
             GROUP BY di.dispatched_at::date
           ), refunded AS (
             SELECT r.created_at::date AS day, SUM(r.refund_amount) AS refunds,
                    SUM(ri.net_revenue_reversed) AS net_refunds
             FROM sale_returns r JOIN return_items ri ON ri.return_id = r.id
             WHERE EXTRACT(YEAR FROM r.created_at) = %s
               AND EXTRACT(MONTH FROM r.created_at) = %s
             GROUP BY r.created_at::date
           )
           SELECT COALESCE(d.day, r.day) AS day, COALESCE(d.transactions, 0) AS transactions,
                  COALESCE(d.sales_value, 0) AS sales_value,
                  COALESCE(d.discounts, 0) AS discounts, COALESCE(d.vat, 0) AS vat,
                  COALESCE(r.refunds, 0) AS refunds,
                  COALESCE(d.net_sales, 0) - COALESCE(r.net_refunds, 0) AS net_sales
           FROM dispatched d FULL OUTER JOIN refunded r ON r.day = d.day
           ORDER BY day""",
        (year, month, year, month),
    ).fetchall()
    totals = db.execute(
        """SELECT COALESCE(SUM(di.gross_revenue), 0) AS sales_value,
                  COALESCE(SUM(di.discount_amount), 0) AS discounts,
                  COALESCE(SUM(di.vat_amount), 0) AS vat,
                  COALESCE(SUM(di.revenue_amount), 0) AS net_sales
           FROM sale_dispatch_items di
           WHERE EXTRACT(YEAR FROM di.dispatched_at) = %s
             AND EXTRACT(MONTH FROM di.dispatched_at) = %s""",
        (year, month),
    ).fetchone()
    totals["payments"] = db.execute(
        """SELECT COALESCE(SUM(amount), 0) AS value FROM payments
           WHERE EXTRACT(YEAR FROM received_at) = %s AND EXTRACT(MONTH FROM received_at) = %s""",
        (year, month),
    ).fetchone()["value"]
    totals["refunds"] = db.execute(
        """SELECT COALESCE(SUM(refund_amount), 0) AS value FROM sale_returns
           WHERE EXTRACT(YEAR FROM created_at) = %s AND EXTRACT(MONTH FROM created_at) = %s""",
        (year, month),
    ).fetchone()["value"]
    totals["net_sales"] -= db.execute(
        """SELECT COALESCE(SUM(net_revenue_reversed), 0) AS value
           FROM return_items ri JOIN sale_returns r ON r.id = ri.return_id
           WHERE EXTRACT(YEAR FROM r.created_at) = %s AND EXTRACT(MONTH FROM r.created_at) = %s""",
        (year, month),
    ).fetchone()["value"]
    previous = db.execute(
        """SELECT COALESCE(SUM(di.revenue_amount), 0)
                  - COALESCE((SELECT SUM(ri.net_revenue_reversed)
                              FROM return_items ri JOIN sale_returns r ON r.id = ri.return_id
                              WHERE date_trunc('month', r.created_at) =
                                    date_trunc('month', make_date(%s, %s, 1) - interval '1 month')), 0)
                  AS net_sales
           FROM sale_dispatch_items di
           WHERE date_trunc('month', di.dispatched_at) =
                 date_trunc('month', make_date(%s, %s, 1) - interval '1 month')""",
        (year, month, year, month),
    ).fetchone()
    totals["outstanding"] = db.execute(
        """SELECT COALESCE(SUM(GREATEST(s.total -
                    (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.sale_id = s.id) -
                    (SELECT COALESCE(SUM(r.refund_amount), 0) FROM sale_returns r WHERE r.sale_id = s.id), 0)), 0) AS value
           FROM sales s WHERE s.id IN (
             SELECT DISTINCT si.sale_id FROM sale_dispatch_items di
             JOIN sale_items si ON si.id = di.sale_item_id
             WHERE EXTRACT(YEAR FROM di.dispatched_at) = %s
               AND EXTRACT(MONTH FROM di.dispatched_at) = %s
           )""",
        (year, month),
    ).fetchone()["value"]
    return rows, totals, previous["net_sales"]


def best_sellers(start_date, end_date, category_id=None):
    return get_db().execute(
        """SELECT p.sku, p.name, COALESCE(c.name, 'Uncategorized') AS category,
                  SUM(di.quantity - di.returned_quantity) AS net_quantity,
                  SUM(di.revenue_amount - COALESCE(r.net_revenue_reversed, 0)) AS net_sales
           FROM sale_dispatch_items di
           JOIN sale_items si ON si.id = di.sale_item_id
           JOIN products p ON p.id = di.product_id
           LEFT JOIN categories c ON c.id = p.category_id
           LEFT JOIN LATERAL (
             SELECT SUM(ri.net_revenue_reversed) AS net_revenue_reversed
             FROM return_items ri WHERE ri.dispatch_item_id = di.id
           ) r ON TRUE
           WHERE di.dispatched_at::date BETWEEN %s AND %s
             AND (%s IS NULL OR p.category_id = %s)
           GROUP BY p.id, p.sku, p.name, c.name
           HAVING SUM(di.quantity - di.returned_quantity) > 0
           ORDER BY net_quantity DESC, net_sales DESC""",
        (start_date, end_date, category_id, category_id),
    ).fetchall()


def profit_report(start_date, end_date, category_id=None):
    db = get_db()
    sales = db.execute(
        """SELECT COALESCE(SUM(di.revenue_amount), 0) AS dispatched_sales,
                  COALESCE(SUM(di.unit_cost * di.quantity), 0) AS dispatched_cost
           FROM sale_dispatch_items di
           JOIN products p ON p.id = di.product_id
           WHERE di.dispatched_at::date BETWEEN %s AND %s
             AND (%s IS NULL OR p.category_id = %s)""",
        (start_date, end_date, category_id, category_id),
    ).fetchone()
    returns = db.execute(
        """SELECT COALESCE(SUM(ri.net_revenue_reversed), 0) AS sales_reversed,
                  COALESCE(SUM(ri.quantity * ri.unit_cost), 0) AS cost_reversed
           FROM return_items ri JOIN sale_returns r ON r.id = ri.return_id
           JOIN sale_dispatch_items di ON di.id = ri.dispatch_item_id
           JOIN products p ON p.id = di.product_id
           WHERE r.created_at::date BETWEEN %s AND %s
             AND (%s IS NULL OR p.category_id = %s)""",
        (start_date, end_date, category_id, category_id),
    ).fetchone()
    losses = db.execute(
        """SELECT COALESCE(SUM(m.quantity * m.unit_cost), 0) AS inventory_losses
           FROM stock_movements m JOIN products p ON p.id = m.product_id
           WHERE m.movement_type = 'Loss'
             AND m.created_at::date BETWEEN %s AND %s
             AND (%s IS NULL OR p.category_id = %s)""",
        (start_date, end_date, category_id, category_id),
    ).fetchone()["inventory_losses"]
    return calculate_profit(
        sales["dispatched_sales"] - returns["sales_reversed"],
        sales["dispatched_cost"] - returns["cost_reversed"],
        losses,
    )


def calculate_profit(net_sales, cost_of_goods_sold, inventory_losses):
    gross_profit = net_sales - cost_of_goods_sold
    return {
        "net_sales": net_sales,
        "cost_of_goods_sold": cost_of_goods_sold,
        "gross_profit": gross_profit,
        "gross_margin": (gross_profit / net_sales * 100) if net_sales else None,
        "inventory_losses": inventory_losses,
        "after_losses": gross_profit - inventory_losses,
    }


def default_range():
    today = date.today()
    return today.replace(day=1), today

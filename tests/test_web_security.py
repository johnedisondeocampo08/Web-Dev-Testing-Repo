from contextlib import contextmanager
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import g
from psycopg.errors import UniqueViolation

from obp import create_app
from obp.permissions import has_permission
from obp.reports import calculate_profit
from obp.routes import package_create, product_create


class WebSecurityTests(unittest.TestCase):
    def setUp(self):
        self.backup_dir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key-not-for-production",
            "BACKUP_DIR": self.backup_dir.name,
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.backup_dir.cleanup()

    def test_staff_sign_in_page_is_plain_html_and_needs_no_database(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"STAFF PORTAL", response.data)
        self.assertNotIn(b"{%", response.data)
        response.close()

    def test_static_pages_do_not_contain_template_code(self):
        pages = Path(__file__).parents[1] / "obp" / "static" / "pages"
        for page in pages.glob("*.html"):
            with self.subTest(page=page.name):
                source = page.read_text(encoding="utf-8")
                self.assertNotIn("{{", source)
                self.assertNotIn("{%", source)
                response = self.client.get(f"/static/pages/{page.name}")
                self.assertEqual(response.status_code, 200)
                response.close()

    def test_browser_can_load_csrf_token_without_signing_in(self):
        response = self.client.get("/api/session")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json["user"])
        self.assertTrue(response.json["csrf_token"])

    def test_dashboard_data_requires_sign_in(self):
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 401)

    def test_unknown_page_uses_a_plain_html_error(self):
        response = self.client.get("/not-a-page")
        self.assertEqual(response.status_code, 404)
        self.assertIn(b"REQUESTED PAGE COULD NOT BE FOUND", response.data.upper())
        response.close()

    def test_protected_api_requires_server_permission(self):
        response = self.client.get("/api/products")
        self.assertEqual(response.status_code, 403)
        self.assertIn("permission", response.get_data(as_text=True))

    def test_unsafe_request_requires_csrf_token(self):
        response = self.client.post("/api/sales", json={"items": []})
        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", response.get_data(as_text=True))

    def test_permission_lookup_uses_the_current_role(self):
        with self.app.test_request_context("/"):
            g.user = {"permissions": ["products.read"]}
            self.assertTrue(has_permission("products.read"))
            self.assertFalse(has_permission("users.manage"))
            g.user = {"permissions": ["*"]}
            self.assertTrue(has_permission("backup.manage"))

    def test_duplicate_product_sku_returns_conflict_instead_of_server_error(self):
        @contextmanager
        def duplicate_sku():
            class Database:
                def execute(self, *_args, **_kwargs):
                    raise UniqueViolation("duplicate SKU")

            yield Database()

        with self.app.test_request_context(
            "/api/products",
            method="POST",
            json={
                "sku": "TECH02",
                "name": "Product",
                "selling_price": "10",
                "acquisition_cost": "5",
            },
        ):
            g.user = {"id": 1, "permissions": ["products.manage"]}
            with patch("obp.routes.write_transaction", duplicate_sku):
                response, status = product_create()

        self.assertEqual(status, 409)
        self.assertEqual(
            response.get_json()["error"], "A product with this SKU already exists."
        )

    def test_duplicate_package_sku_returns_conflict_instead_of_server_error(self):
        @contextmanager
        def duplicate_sku():
            class Database:
                def execute(self, *_args, **_kwargs):
                    raise UniqueViolation("duplicate SKU")

            yield Database()

        with self.app.test_request_context(
            "/api/packages",
            method="POST",
            json={
                "sku": "BUNDLE01",
                "name": "Package",
                "selling_price": "10",
                "components": [{"product_id": "1", "quantity": "1"}],
            },
        ):
            g.user = {"id": 1, "permissions": ["products.manage"]}
            with patch("obp.routes.write_transaction", duplicate_sku):
                response, status = package_create()

        self.assertEqual(status, 409)
        self.assertEqual(
            response.get_json()["error"], "A package with this SKU already exists."
        )


class ProfitCalculationTests(unittest.TestCase):
    def test_proposal_sample_profit_and_margin(self):
        result = calculate_profit(1000, 600, 0)
        self.assertEqual(result["gross_profit"], 400)
        self.assertEqual(result["gross_margin"], 40)
        self.assertEqual(result["after_losses"], 400)

    def test_inventory_losses_are_reported_separately(self):
        result = calculate_profit(1000, 600, 50)
        self.assertEqual(result["gross_profit"], 400)
        self.assertEqual(result["after_losses"], 350)

    def test_margin_is_not_applicable_when_net_sales_are_zero(self):
        result = calculate_profit(0, 0, 0)
        self.assertIsNone(result["gross_margin"])


if __name__ == "__main__":
    unittest.main()

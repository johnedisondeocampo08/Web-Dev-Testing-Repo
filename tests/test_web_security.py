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

    def test_homepage_is_public_and_offers_guest_and_staff_paths(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Browse products", response.data)
        self.assertIn(b'href="/login"', response.data)
        self.assertIn(b'href="/OBP_website/styles.css"', response.data)
        self.assertIn(b'<a href="/">Home</a>', response.data)
        self.assertIn(b'src="/OBP_website/assets/about-team.png"', response.data)
        self.assertIn(b'href="/OBP_website/products.html"', response.data)
        self.assertIn(b'href="/OBP_website/products.html?category=Computers%20%26%20parts#products"', response.data)
        self.assertNotIn(b'id="product-grid"', response.data)
        self.assertIn(b'id="music-toggle"', response.data)
        self.assertIn(b'src="/OBP_website/assets/WDDRRP%20TEASER%20MUSIC%20VIEDO%20RUTHLESS%20RECORDS.mp3"', response.data)
        self.assertIn(b"Established in 1996", response.data)
        self.assertIn(b"1808 Cityland Pasong Tamo Tower", response.data)
        self.assertIn(b"Maintenance, upgrades", response.data)
        self.assertIn(b'href="tel:+639999937848"', response.data)
        self.assertIn(b"optimumbusinessproducts.sai@yahoo.com", response.data)
        self.assertIn(b"optimumbusinessproducts@yahoo.com", response.data)
        self.assertIn(b'href="#office-map"', response.data)
        self.assertIn(b'<iframe', response.data)
        self.assertIn(b"Google Maps showing Optimum Business Products", response.data)
        self.assertIn(b"output=embed", response.data)
        self.assertIn(b"maps.google.com", response.data)
        explore_position = response.data.index(b"<h2>Explore</h2>")
        hours_position = response.data.index(b"<h2>Opening hours</h2>")
        visit_position = response.data.index(b"<h2>Visit &amp; contact</h2>")
        self.assertLess(explore_position, hours_position)
        self.assertLess(hours_position, visit_position)
        response.close()

        styles = self.client.get("/OBP_website/styles.css")
        self.assertEqual(styles.status_code, 200)
        self.assertIn(b".storefront", styles.data)
        styles.close()

        script = self.client.get("/OBP_website/script.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn(b"store-menu-toggle", script.data)
        self.assertIn(b"Fullmark", script.data)
        script.close()

        products = self.client.get("/OBP_website/products.html")
        self.assertEqual(products.status_code, 200)
        self.assertIn(b"Browse Products", products.data)
        self.assertIn(b'<a href="/">Home</a>', products.data)
        self.assertIn(b'id="product-grid"', products.data)
        self.assertIn(b'id="product-category"', products.data)
        self.assertIn(b'id="product-brand"', products.data)
        self.assertIn(b'id="music-toggle"', products.data)
        products.close()

        music = self.client.get(
            "/OBP_website/assets/WDDRRP%20TEASER%20MUSIC%20VIEDO%20RUTHLESS%20RECORDS.mp3"
        )
        self.assertEqual(music.status_code, 200)
        self.assertEqual(music.mimetype, "audio/mpeg")
        self.assertTrue(music.data)
        music.close()

        image = self.client.get("/OBP_website/assets/about-team.png")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.mimetype, "image/png")
        image.close()

    def test_signed_in_staff_keep_the_existing_dashboard_homepage(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 7
        with patch("obp.auth.get_db") as get_db:
            get_db.return_value.execute.return_value.fetchone.return_value = {
                "id": 7,
                "username": "admin",
                "display_name": "Admin",
                "active": True,
                "role_name": "Administrator / Owner",
                "permissions": ["*"],
            }
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-page="dashboard"', response.data)
        response.close()

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

import pytest
from werkzeug.security import check_password_hash

from app import create_app, get_db, init_db


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "DATABASE": str(tmp_path / "test.sqlite3"),
        "SECRET_KEY": "test-secret",
        "ADMIN_EMAIL": "admin@example.test",
        "ADMIN_PASSWORD": "AdminPass!2026",
    })
    with app.app_context():
        init_db()
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, email, password):
    return client.post("/login", data={"email": email, "password": password})


def shoe_form(**overrides):
    data = {
        "name": "Test High",
        "brand": "Court District",
        "category": "Performance",
        "size_range": "US 8-12",
        "price": "129.99",
        "stock": "4",
        "image_url": "",
        "description": "A test shoe built for the hardwood.",
    }
    data.update(overrides)
    return data


def test_registration_hashes_password_and_defaults_to_customer(app, client):
    response = client.post("/register", data={"name": "Jamie Lee", "email": "jamie@example.test", "password": "StrongPass123"})
    assert response.status_code == 302
    with app.app_context():
        user = get_db().execute("SELECT * FROM users WHERE email = ?", ("jamie@example.test",)).fetchone()
        assert user["role"] == "customer"
        assert user["password_hash"] != "StrongPass123"
        assert check_password_hash(user["password_hash"], "StrongPass123")


def test_customer_cannot_manage_shoes(client):
    client.post("/register", data={"name": "Jamie Lee", "email": "jamie@example.test", "password": "StrongPass123"})
    response = client.post("/admin/shoes/new", data=shoe_form())
    assert response.status_code == 403


def test_admin_can_create_update_and_delete_shoe(app, client):
    login(client, "admin@example.test", "AdminPass!2026")
    response = client.post("/admin/shoes/new", data=shoe_form())
    assert response.status_code == 302
    with app.app_context():
        shoe = get_db().execute("SELECT * FROM shoes WHERE name = ?", ("Test High",)).fetchone()
        assert shoe["price_cents"] == 12999
        shoe_id = shoe["id"]
    response = client.post(f"/admin/shoes/{shoe_id}/edit", data=shoe_form(name="Test Low", stock="9"))
    assert response.status_code == 302
    with app.app_context():
        shoe = get_db().execute("SELECT * FROM shoes WHERE id = ?", (shoe_id,)).fetchone()
        assert shoe["name"] == "Test Low"
        assert shoe["stock"] == 9
    response = client.post(f"/admin/shoes/{shoe_id}/delete")
    assert response.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT id FROM shoes WHERE id = ?", (shoe_id,)).fetchone() is None


def test_customer_can_add_shoe_to_cart_and_checkout(app, client):
    client.post("/register", data={"name": "Jamie Lee", "email": "jamie@example.test", "password": "StrongPass123"})
    with app.app_context():
        shoe_id = get_db().execute("SELECT id FROM shoes ORDER BY id LIMIT 1").fetchone()["id"]
        user_id = get_db().execute("SELECT id FROM users WHERE email = ?", ("jamie@example.test",)).fetchone()["id"]
        original_stock = get_db().execute("SELECT stock FROM shoes WHERE id = ?", (shoe_id,)).fetchone()["stock"]
    response = client.post(f"/shoes/{shoe_id}/cart", data={"quantity": "2", "size": "US 9"})
    assert response.status_code == 302
    cart_page = client.get("/cart")
    assert cart_page.status_code == 200
    assert b"Checkout" in cart_page.data
    checkout_page = client.get("/checkout")
    assert checkout_page.status_code == 200
    assert b"Place order" in checkout_page.data
    response = client.post("/checkout", data={
        "shipping_name": "Jamie Lee",
        "shipping_phone": "555-0123",
        "shipping_address": "12 Court Street",
        "payment_method": "cash_on_delivery",
    })
    assert response.status_code == 302
    account_page = client.get("/account")
    assert account_page.status_code == 200
    assert b"Order history" in account_page.data
    assert b"Dunk Low Retro" in account_page.data or b"Curry 11" in account_page.data
    with app.app_context():
        order = get_db().execute("SELECT * FROM orders WHERE user_id = ? AND shoe_id = ?", (user_id, shoe_id)).fetchone()
        assert order is not None
        assert order["quantity"] == 2
        assert order["size"] == "US 9"
        assert order["status"] == "pending"
        assert get_db().execute("SELECT stock FROM shoes WHERE id = ?", (shoe_id,)).fetchone()["stock"] == original_stock - 2
        assert get_db().execute("SELECT COUNT(*) FROM cart_items WHERE user_id = ?", (user_id,)).fetchone()[0] == 0


def test_customer_can_add_and_remove_wishlist_item(client):
    client.post("/register", data={"name": "Jamie Lee", "email": "jamie@example.test", "password": "StrongPass123"})
    response = client.post("/shoes/1/wishlist")
    assert response.status_code == 302
    wishlist = client.get("/wishlist")
    assert wishlist.status_code == 200
    assert b"Dunk Low Retro" in wishlist.data
    client.post("/wishlist/1/remove")
    assert b"Dunk Low Retro" not in client.get("/wishlist").data


def test_shoe_validation_rejects_invalid_values(client):
    login(client, "admin@example.test", "AdminPass!2026")
    response = client.post("/admin/shoes/new", data=shoe_form(price="-5", stock="not-a-number"))
    assert response.status_code == 200
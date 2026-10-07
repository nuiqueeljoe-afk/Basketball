import os
import re
import sqlite3
from functools import wraps
from urllib.parse import urlparse

import click
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash


SHOE_FIELDS = ("name", "brand", "category", "size_range", "price", "stock", "image_url", "description")
DEFAULT_ADMIN_EMAIL = "admin@courtdistrict.local"
DEFAULT_ADMIN_PASSWORD = "CourtDistrict!2026"
DEFAULT_SHOES = [
    ("Dunk Low Retro", "Nike", "Lifestyle", "US 7-13", 11500, 8, "https://images.unsplash.com/photo-1542291026-7eec264c27ff?auto=format&fit=crop&w=1000&q=85", "A court-born classic with everyday versatility and a clean low profile."),
    ("Curry 11", "Under Armour", "Performance", "US 8-13", 16000, 5, "https://images.unsplash.com/photo-1552346154-21d32810aba3?auto=format&fit=crop&w=1000&q=85", "Responsive cushioning and secure support for quick changes of direction."),
    ("Harden Vol. 8", "Adidas", "Performance", "US 7-12", 18000, 3, "https://images.unsplash.com/photo-1495555961986-6d4c1ecb7be3?auto=format&fit=crop&w=1000&q=85", "A stable, grippy ride made for creating space and attacking the rim."),
    ("Two Wxy v4", "New Balance", "Performance", "US 7-13", 14000, 6, "https://images.unsplash.com/photo-1460353581641-37baddab0fa2?auto=format&fit=crop&w=1000&q=85", "Lightweight court feel with lockdown support from tip-off to the final run."),
    ("Ja Morant 1", "Nike", "Performance", "US 8-12", 17000, 4, "https://images.unsplash.com/photo-1600185365483-26d7a4cc7519?auto=format&fit=crop&w=1000&q=85", "Explosive cushioning and traction built for fast-paced downhill attacks and quick second-step bursts."),
    ("Book 1", "Nike", "Performance", "US 7-13", 17500, 5, "https://images.unsplash.com/photo-1608231387042-66d1773070a5?auto=format&fit=crop&w=1000&q=85", "Responsive underfoot feel and snappy support that keeps you planted through hard drives and pull-up jumpers."),
    ("G.T. Jump 2", "Adidas", "Performance", "US 7-12", 16500, 3, "https://images.unsplash.com/photo-1525966222134-fcfa99b8ae77?auto=format&fit=crop&w=1000&q=85", "High-energy cushioning and balance for modern game speed, quick stops, and smooth takeoffs."),
    ("Rivalry Low", "Puma", "Lifestyle", "US 6-12", 12000, 7, "https://images.unsplash.com/photo-1600185365926-3a2ce3cdb9eb?auto=format&fit=crop&w=1000&q=85", "A clean low-cut silhouette with a comfortable fit for everyday hoops sessions and streetwear styling."),
]


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("COURT_DISTRICT_SECRET", "dev-only-change-this-secret"),
        DATABASE=os.path.join(app.instance_path, "court_district.sqlite3"),
        ADMIN_EMAIL=os.environ.get("COURT_DISTRICT_ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL),
        ADMIN_PASSWORD=os.environ.get("COURT_DISTRICT_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD),
        WTF_CSRF_ENABLED=False,
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.instance_path, exist_ok=True)
    with app.app_context():
        init_db()
    CSRFProtect(app)

    @app.teardown_appcontext
    def close_db(_error=None):
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    @app.before_request
    def load_user():
        user_id = session.get("user_id")
        g.user = get_db().execute("SELECT id, name, email, role FROM users WHERE id = ?", (user_id,)).fetchone() if user_id else None

    @app.context_processor
    def inject_user():
        user = g.get("user")
        if user is None:
            return {"current_user": None, "cart_count": 0, "wishlist_count": 0}
        counts = get_db().execute(
            "SELECT "
            "(SELECT COALESCE(SUM(quantity), 0) FROM cart_items WHERE user_id = ?) AS cart_count, "
            "(SELECT COUNT(*) FROM wishlist_items WHERE user_id = ?) AS wishlist_count",
            (user["id"], user["id"]),
        ).fetchone()
        return {"current_user": user, "cart_count": counts["cart_count"], "wishlist_count": counts["wishlist_count"]}

    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        click.echo("Court District database initialized.")

    @app.route("/")
    def home():
        shoes = get_db().execute("SELECT * FROM shoes ORDER BY id DESC LIMIT 3").fetchall()
        return render_template("home.html", shoes=shoes)

    @app.route("/shoes")
    def catalog():
        query = request.args.get("q", "").strip()
        shoes = get_db().execute(
            "SELECT * FROM shoes WHERE name LIKE ? OR brand LIKE ? OR category LIKE ? ORDER BY name",
            (f"%{query}%", f"%{query}%", f"%{query}%"),
        ).fetchall()
        return render_template("catalog.html", shoes=shoes, query=query)

    @app.route("/shoes/<int:shoe_id>")
    def shoe_detail(shoe_id):
        shoe = get_shoe(shoe_id)
        return render_template("shoe_detail.html", shoe=shoe)

    @app.route("/shoes/<int:shoe_id>/cart", methods=("POST",))
    @login_required
    def add_to_cart(shoe_id):
        shoe = get_shoe(shoe_id)
        size = request.form.get("size", "").strip()
        try:
            quantity = int(request.form.get("quantity", "1"))
        except (TypeError, ValueError):
            flash("Choose a valid quantity before adding this shoe.", "error")
            return redirect(url_for("shoe_detail", shoe_id=shoe_id))

        if size not in shoe_sizes(shoe["size_range"]):
            flash("Choose a valid size for this shoe.", "error")
            return redirect(url_for("shoe_detail", shoe_id=shoe_id))
        if quantity < 1:
            flash("Quantity must be at least 1.", "error")
            return redirect(url_for("shoe_detail", shoe_id=shoe_id))
        existing = get_db().execute(
            "SELECT quantity FROM cart_items WHERE user_id = ? AND shoe_id = ? AND size = ?",
            (g.user["id"], shoe_id, size),
        ).fetchone()
        requested_quantity = quantity + (existing["quantity"] if existing else 0)
        if requested_quantity > shoe["stock"]:
            flash("Not enough stock for that quantity right now.", "error")
            return redirect(url_for("shoe_detail", shoe_id=shoe_id))

        get_db().execute(
            "INSERT INTO cart_items (user_id, shoe_id, size, quantity) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, shoe_id, size) DO UPDATE SET quantity = excluded.quantity",
            (g.user["id"], shoe_id, size, requested_quantity),
        )
        get_db().commit()
        flash(f"{shoe['name']} in {size} added to your cart.", "success")
        return redirect(url_for("cart"))

    @app.route("/cart")
    @login_required
    def cart():
        items = get_db().execute(
            "SELECT cart_items.*, shoes.name, shoes.brand, shoes.image_url, shoes.price_cents, shoes.stock "
            "FROM cart_items JOIN shoes ON shoes.id = cart_items.shoe_id "
            "WHERE cart_items.user_id = ? ORDER BY cart_items.id DESC",
            (g.user["id"],),
        ).fetchall()
        return render_template("cart.html", items=items, total_cents=sum(item["price_cents"] * item["quantity"] for item in items))

    @app.route("/cart/<int:shoe_id>/update", methods=("POST",))
    @login_required
    def update_cart_item(shoe_id):
        size = request.form.get("size", "").strip()
        try:
            quantity = int(request.form.get("quantity", ""))
        except (TypeError, ValueError):
            quantity = 0
        shoe = get_shoe(shoe_id)
        if quantity < 1 or quantity > shoe["stock"]:
            flash("Choose a quantity that is available in stock.", "error")
        else:
            get_db().execute(
                "UPDATE cart_items SET quantity = ? WHERE user_id = ? AND shoe_id = ? AND size = ?",
                (quantity, g.user["id"], shoe_id, size),
            )
            get_db().commit()
            flash("Cart updated.", "success")
        return redirect(url_for("cart"))

    @app.route("/cart/<int:shoe_id>/remove", methods=("POST",))
    @login_required
    def remove_from_cart(shoe_id):
        size = request.form.get("size", "").strip()
        get_db().execute(
            "DELETE FROM cart_items WHERE user_id = ? AND shoe_id = ? AND size = ?",
            (g.user["id"], shoe_id, size),
        )
        get_db().commit()
        flash("Item removed from your cart.", "success")
        return redirect(url_for("cart"))

    @app.route("/wishlist")
    @login_required
    def wishlist():
        items = get_db().execute(
            "SELECT shoes.* FROM wishlist_items JOIN shoes ON shoes.id = wishlist_items.shoe_id "
            "WHERE wishlist_items.user_id = ? ORDER BY wishlist_items.id DESC",
            (g.user["id"],),
        ).fetchall()
        return render_template("wishlist.html", shoes=items)

    @app.route("/shoes/<int:shoe_id>/wishlist", methods=("POST",))
    @login_required
    def add_to_wishlist(shoe_id):
        get_shoe(shoe_id)
        get_db().execute(
            "INSERT OR IGNORE INTO wishlist_items (user_id, shoe_id) VALUES (?, ?)",
            (g.user["id"], shoe_id),
        )
        get_db().commit()
        flash("Shoe saved to your wishlist.", "success")
        return redirect(url_for("wishlist"))

    @app.route("/wishlist/<int:shoe_id>/remove", methods=("POST",))
    @login_required
    def remove_from_wishlist(shoe_id):
        get_db().execute("DELETE FROM wishlist_items WHERE user_id = ? AND shoe_id = ?", (g.user["id"], shoe_id))
        get_db().commit()
        flash("Shoe removed from your wishlist.", "success")
        return redirect(url_for("wishlist"))

    @app.route("/checkout", methods=("GET", "POST"))
    @login_required
    def checkout():
        items = get_db().execute(
            "SELECT cart_items.*, shoes.name, shoes.price_cents, shoes.stock "
            "FROM cart_items JOIN shoes ON shoes.id = cart_items.shoe_id WHERE cart_items.user_id = ?",
            (g.user["id"],),
        ).fetchall()
        if not items:
            flash("Your cart is empty.", "error")
            return redirect(url_for("cart"))

        if request.method == "POST":
            shipping_name = request.form.get("shipping_name", "").strip()
            shipping_phone = request.form.get("shipping_phone", "").strip()
            shipping_address = request.form.get("shipping_address", "").strip()
            payment_method = request.form.get("payment_method", "")
            if not shipping_name or not shipping_phone or not shipping_address:
                flash("Enter your name, phone number, and delivery address.", "error")
            elif payment_method not in ("cash_on_delivery", "pickup"):
                flash("Choose a valid payment method.", "error")
            else:
                database = get_db()
                database.execute("BEGIN IMMEDIATE")
                current_items = database.execute(
                    "SELECT cart_items.*, shoes.name, shoes.price_cents, shoes.stock "
                    "FROM cart_items JOIN shoes ON shoes.id = cart_items.shoe_id WHERE cart_items.user_id = ?",
                    (g.user["id"],),
                ).fetchall()
                unavailable = [item["name"] for item in current_items if item["quantity"] > item["stock"]]
                if unavailable:
                    database.rollback()
                    flash(f"Stock changed for {', '.join(unavailable)}. Update your cart before checkout.", "error")
                    return redirect(url_for("cart"))
                for item in current_items:
                    database.execute(
                        "INSERT INTO orders "
                        "(user_id, shoe_id, quantity, size, total_cents, status, shipping_name, shipping_phone, shipping_address, payment_method) "
                        "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                        (g.user["id"], item["shoe_id"], item["quantity"], item["size"], item["price_cents"] * item["quantity"], shipping_name, shipping_phone, shipping_address, payment_method),
                    )
                    database.execute(
                        "UPDATE shoes SET stock = stock - ? WHERE id = ? AND stock >= ?",
                        (item["quantity"], item["shoe_id"], item["quantity"]),
                    )
                database.execute("DELETE FROM cart_items WHERE user_id = ?", (g.user["id"],))
                database.commit()
                flash("Checkout complete. Your order is pending confirmation.", "success")
                return redirect(url_for("account"))

        return render_template("checkout.html", items=items, total_cents=sum(item["price_cents"] * item["quantity"] for item in items), user=g.user)

    @app.route("/register", methods=("GET", "POST"))
    def register():
        if g.user:
            return redirect(url_for("account"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            error = None
            if len(name) < 2 or len(name) > 80:
                error = "Enter a name between 2 and 80 characters."
            elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                error = "Enter a valid email address."
            elif len(password) < 8:
                error = "Password must be at least 8 characters."
            elif get_db().execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
                error = "An account already uses that email. Try logging in."
            if error:
                flash(error, "error")
            else:
                cursor = get_db().execute(
                    "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, 'customer')",
                    (name, email, generate_password_hash(password)),
                )
                get_db().commit()
                session.clear()
                session["user_id"] = cursor.lastrowid
                flash("Your Court District account is ready.", "success")
                return redirect(url_for("account"))
        return render_template("auth.html", mode="register")

    @app.route("/login", methods=("GET", "POST"))
    def login():
        if g.user:
            return redirect(url_for("admin_shoes" if g.user["role"] == "admin" else "catalog"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user is None or not check_password_hash(user["password_hash"], password):
                flash("Email or password is incorrect.", "error")
            else:
                session.clear()
                session["user_id"] = user["id"]
                flash("Welcome back.", "success")
                return redirect(url_for("admin_shoes" if user["role"] == "admin" else "catalog"))
        return render_template("auth.html", mode="login")

    @app.route("/logout", methods=("POST",))
    def logout():
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("home"))

    @app.route("/account", methods=("GET", "POST"))
    @login_required
    def account():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            error = None
            if len(name) < 2 or len(name) > 80:
                error = "Enter a name between 2 and 80 characters."
            elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                error = "Enter a valid email address."
            elif get_db().execute("SELECT id FROM users WHERE email = ? AND id != ?", (email, g.user["id"])).fetchone():
                error = "An account already uses that email."
            if error:
                flash(error, "error")
            else:
                get_db().execute("UPDATE users SET name = ?, email = ? WHERE id = ?", (name, email, g.user["id"]))
                get_db().commit()
                flash("Your account details have been updated.", "success")
                return redirect(url_for("account"))
        orders = get_db().execute(
            "SELECT orders.*, shoes.name AS shoe_name, shoes.brand FROM orders "
            "JOIN shoes ON shoes.id = orders.shoe_id WHERE orders.user_id = ? "
            "ORDER BY orders.created_at DESC, orders.id DESC",
            (g.user["id"],),
        ).fetchall()
        return render_template("account.html", user=g.user, orders=orders)

    @app.route("/admin/shoes")
    @admin_required
    def admin_shoes():
        shoes = get_db().execute("SELECT * FROM shoes ORDER BY name").fetchall()
        return render_template("admin_shoes.html", shoes=shoes)

    @app.route("/admin/shoes/new", methods=("GET", "POST"))
    @admin_required
    def create_shoe():
        if request.method == "POST":
            values, error = validate_shoe(request.form)
            if error:
                flash(error, "error")
            else:
                insert_shoe(values)
                flash("Shoe added to inventory.", "success")
                return redirect(url_for("admin_shoes"))
        return render_template("shoe_form.html", shoe=None)

    @app.route("/admin/shoes/<int:shoe_id>/edit", methods=("GET", "POST"))
    @admin_required
    def edit_shoe(shoe_id):
        shoe = get_shoe(shoe_id)
        if request.method == "POST":
            values, error = validate_shoe(request.form)
            if error:
                flash(error, "error")
            else:
                update_shoe(shoe_id, values)
                flash("Inventory details updated.", "success")
                return redirect(url_for("admin_shoes"))
        return render_template("shoe_form.html", shoe=shoe)

    @app.route("/admin/shoes/<int:shoe_id>/delete", methods=("POST",))
    @admin_required
    def delete_shoe(shoe_id):
        get_shoe(shoe_id)
        get_db().execute("DELETE FROM shoes WHERE id = ?", (shoe_id,))
        get_db().commit()
        flash("Shoe removed from inventory.", "success")
        return redirect(url_for("admin_shoes"))

    return app


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


from flask import current_app


def init_db():
    database = current_app.config["DATABASE"]
    os.makedirs(os.path.dirname(os.path.abspath(database)), exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'customer'))
        );
        CREATE TABLE IF NOT EXISTS shoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT NOT NULL,
            category TEXT NOT NULL,
            size_range TEXT NOT NULL,
            price_cents INTEGER NOT NULL CHECK (price_cents > 0),
            stock INTEGER NOT NULL CHECK (stock >= 0),
            image_url TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            shoe_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            size TEXT NOT NULL,
            total_cents INTEGER NOT NULL CHECK (total_cents > 0),
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            shipping_name TEXT NOT NULL DEFAULT '',
            shipping_phone TEXT NOT NULL DEFAULT '',
            shipping_address TEXT NOT NULL DEFAULT '',
            payment_method TEXT NOT NULL DEFAULT 'cash_on_delivery',
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (shoe_id) REFERENCES shoes(id)
        );
        CREATE TABLE IF NOT EXISTS cart_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            shoe_id INTEGER NOT NULL,
            size TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            UNIQUE (user_id, shoe_id, size),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (shoe_id) REFERENCES shoes(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS wishlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            shoe_id INTEGER NOT NULL,
            UNIQUE (user_id, shoe_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (shoe_id) REFERENCES shoes(id) ON DELETE CASCADE
        );
        """
    )
    order_columns = {row["name"] for row in connection.execute("PRAGMA table_info(orders)").fetchall()}
    for column, definition in (
        ("shipping_name", "TEXT NOT NULL DEFAULT ''"),
        ("shipping_phone", "TEXT NOT NULL DEFAULT ''"),
        ("shipping_address", "TEXT NOT NULL DEFAULT ''"),
        ("payment_method", "TEXT NOT NULL DEFAULT 'cash_on_delivery'"),
    ):
        if column not in order_columns:
            connection.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")
    admin = connection.execute("SELECT id FROM users WHERE email = ?", (current_app.config["ADMIN_EMAIL"],)).fetchone()
    if admin is None:
        connection.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, 'admin')",
            ("Court District Admin", current_app.config["ADMIN_EMAIL"], generate_password_hash(current_app.config["ADMIN_PASSWORD"])),
        )
    existing = set(
        (row["name"], row["brand"])
        for row in connection.execute("SELECT name, brand FROM shoes").fetchall()
    )
    to_insert = [shoe for shoe in DEFAULT_SHOES if (shoe[0], shoe[1]) not in existing]
    if to_insert:
        connection.executemany(
            "INSERT INTO shoes (name, brand, category, size_range, price_cents, stock, image_url, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            to_insert,
        )
    connection.commit()
    connection.close()


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(**kwargs)
    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        if g.user["role"] != "admin":
            abort(403)
        return view(**kwargs)
    return wrapped_view


def validate_shoe(form):
    values = {field: form.get(field, "").strip() for field in SHOE_FIELDS if field not in ("price", "stock")}
    try:
        price_cents = round(float(form.get("price", "")) * 100)
        stock = int(form.get("stock", ""))
    except (TypeError, ValueError):
        return None, "Enter a valid price and whole-number stock quantity."
    if any(not values[field] for field in ("name", "brand", "category", "size_range", "description")):
        return None, "Complete all required shoe details."
    if len(values["name"]) > 100 or len(values["description"]) > 1000:
        return None, "Keep the shoe name under 100 characters and description under 1,000."
    if price_cents <= 0:
        return None, "Price must be greater than zero."
    if stock < 0:
        return None, "Stock cannot be negative."
    image_url = values["image_url"]
    if image_url and (urlparse(image_url).scheme != "https" or not urlparse(image_url).netloc):
        return None, "Image URL must start with https://."
    values["price_cents"] = price_cents
    values["stock"] = stock
    return values, None


def insert_shoe(values):
    get_db().execute(
        "INSERT INTO shoes (name, brand, category, size_range, price_cents, stock, image_url, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(values[field] for field in ("name", "brand", "category", "size_range")) + (values["price_cents"], values["stock"], values["image_url"], values["description"]),
    )
    get_db().commit()


def update_shoe(shoe_id, values):
    get_db().execute(
        "UPDATE shoes SET name = ?, brand = ?, category = ?, size_range = ?, price_cents = ?, stock = ?, image_url = ?, description = ? WHERE id = ?",
        tuple(values[field] for field in ("name", "brand", "category", "size_range")) + (values["price_cents"], values["stock"], values["image_url"], values["description"], shoe_id),
    )
    get_db().commit()


def get_shoe(shoe_id):
    shoe = get_db().execute("SELECT * FROM shoes WHERE id = ?", (shoe_id,)).fetchone()
    if shoe is None:
        abort(404)
    return shoe


def shoe_sizes(size_range):
    match = re.fullmatch(r"US\s+(\d+)-(\d+)", size_range.strip())
    if match:
        first_size, last_size = (int(part) for part in match.groups())
        return [f"US {size}" for size in range(first_size, last_size + 1)]
    return [size_range] if size_range else []


app = create_app()


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True)


from flask import g
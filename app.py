import os
import sqlite3
import socket
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from io import BytesIO

import qrcode
from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "laundry.db")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "laundry-shop-college-project")
app.config["DATABASE"] = DATABASE

STATUSES = ["RECEIVED", "WASHING", "DRYING", "FOLDING", "READY FOR PICKUP", "COMPLETED"]
PAYMENT_METHODS = ["Cash", "GCash", "Bank Transfer"]


def tracking_url(token):
    configured_base = os.environ.get("TRACKING_BASE_URL", "").rstrip("/")
    if configured_base:
        return f"{configured_base}{url_for('track_order', token=token)}"

    host = request.host
    if host.startswith(("127.0.0.1", "localhost", "0.0.0.0")):
        try:
            lan_ip = socket.gethostbyname(socket.gethostname())
            if not lan_ip.startswith("127."):
                port = host.rsplit(":", 1)[1] if ":" in host else "5000"
                host = f"{lan_ip}:{port}"
        except socket.gaierror:
            pass
    return f"{request.scheme}://{host}{url_for('track_order', token=token)}"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'Staff',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            price_per_kg REAL NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT UNIQUE NOT NULL,
            customer_id INTEGER NOT NULL,
            service_id INTEGER NOT NULL,
            weight REAL NOT NULL,
            price_per_kg REAL NOT NULL,
            total_price REAL NOT NULL,
            date_received TEXT NOT NULL,
            expected_pickup TEXT NOT NULL,
            amount_paid REAL NOT NULL DEFAULT 0,
            balance REAL NOT NULL,
            payment_method TEXT NOT NULL DEFAULT 'Cash',
            payment_status TEXT NOT NULL DEFAULT 'Unpaid',
            status TEXT NOT NULL DEFAULT 'RECEIVED',
            notes TEXT NOT NULL DEFAULT '',
            qr_token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE RESTRICT,
            FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE RESTRICT
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            paid_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS order_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
        );
    """)
    first_setup = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    if first_setup:
        db.execute("INSERT INTO users (username, password, full_name, role) VALUES (?, ?, ?, ?)",
                   ("admin", generate_password_hash("admin123"), "Laundry Administrator", "Admin"))
        db.execute("INSERT INTO users (username, password, full_name, role) VALUES (?, ?, ?, ?)",
                   ("staff", generate_password_hash("staff123"), "Front Desk Staff", "Staff"))
    if first_setup and db.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 0:
        services = [
            ("Wash-Dry-Fold", 55, "Complete wash, dry, and fold service"),
            ("Wash Only", 35, "Professional washing service"),
            ("Dry Only", 30, "Tumble drying service"),
            ("Fold Only", 20, "Neat folding service"),
            ("Comforter", 250, "Comforter and duvet cleaning"),
            ("Blanket", 180, "Blanket cleaning"),
            ("Express Laundry", 85, "Priority same-day service"),
        ]
        db.executemany("INSERT INTO services (name, price_per_kg, description) VALUES (?, ?, ?)", services)
    if first_setup and db.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
        db.executemany("INSERT INTO customers (name, phone, address) VALUES (?, ?, ?)", [
            ("Maria Santos", "0917 555 0142", "Poblacion, Makati City"),
            ("Juan Dela Cruz", "0918 222 9031", "Barangay San Antonio, Pasig City"),
            ("Alyssa Reyes", "0920 781 6620", "Project 4, Quezon City"),
        ])
    db.commit()


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        if session.get("user_role") != "Admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped_view


def money(value):
    return f"₱{float(value or 0):,.2f}"


@app.template_filter("money")
def money_filter(value):
    return money(value)


@app.template_filter("pretty_date")
def pretty_date(value):
    if not value:
        return "-"
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%b %d, %Y")
    except ValueError:
        return value


@app.context_processor
def inject_globals():
    return {"statuses": STATUSES, "today": date.today().isoformat(), "shop_name": "FreshFold Laundry"}


@app.before_request
def ensure_database():
    init_db()


@app.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["full_name"]
            session["user_role"] = user["role"]
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("The username or password is incorrect.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/staff")
@admin_required
def staff_accounts():
    users = get_db().execute("SELECT id, username, full_name, role, created_at FROM users ORDER BY role, full_name").fetchall()
    return render_template("staff.html", users=users)


@app.post("/staff/save")
@admin_required
def save_staff():
    username = request.form.get("username", "").strip()
    full_name = request.form.get("full_name", "").strip()
    password = request.form.get("password", "")
    if not username or not full_name or len(password) < 6:
        flash("Full name, username, and a password of at least 6 characters are required.", "danger")
        return redirect(url_for("staff_accounts"))
    db = get_db()
    try:
        db.execute("INSERT INTO users (username, password, full_name, role) VALUES (?, ?, ?, 'Staff')", (username, generate_password_hash(password), full_name))
        db.commit()
        flash(f"Staff account {username} created.", "success")
    except sqlite3.IntegrityError:
        flash("That username is already in use.", "danger")
    return redirect(url_for("staff_accounts"))


@app.post("/staff/<int:user_id>/delete")
@admin_required
def delete_staff(user_id):
    if user_id == session.get("user_id"):
        flash("You cannot delete the account you are currently using.", "warning")
        return redirect(url_for("staff_accounts"))
    db = get_db()
    user = db.execute("SELECT username, role FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        abort(404)
    if user["role"] == "Admin" and db.execute("SELECT COUNT(*) FROM users WHERE role='Admin'").fetchone()[0] <= 1:
        flash("The last admin account cannot be deleted.", "warning")
    else:
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
        db.commit()
        flash(f"Account {user['username']} deleted.", "success")
    return redirect(url_for("staff_accounts"))


@app.route("/")
@login_required
def dashboard():
    db = get_db()
    stats = db.execute("""
        SELECT COUNT(DISTINCT c.id) AS customers,
               COUNT(o.id) AS orders,
               COALESCE(SUM(o.total_price), 0) AS sales,
               SUM(CASE WHEN o.status NOT IN ('COMPLETED', 'READY FOR PICKUP') THEN 1 ELSE 0 END) AS pending,
               SUM(CASE WHEN o.status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN o.status = 'READY FOR PICKUP' THEN 1 ELSE 0 END) AS ready
        FROM customers c LEFT JOIN orders o ON o.customer_id = c.id
    """).fetchone()
    recent = db.execute("""
        SELECT o.*, c.name AS customer_name, s.name AS service_name
        FROM orders o JOIN customers c ON c.id=o.customer_id JOIN services s ON s.id=o.service_id
        ORDER BY o.created_at DESC LIMIT 6
    """).fetchall()
    return render_template("dashboard.html", stats=stats, recent=recent)


@app.route("/customers")
@login_required
def customers():
    query = request.args.get("q", "").strip()
    rows = get_db().execute("""
        SELECT c.*, COUNT(o.id) AS order_count
        FROM customers c LEFT JOIN orders o ON o.customer_id = c.id
        WHERE c.name LIKE ? OR c.phone LIKE ?
        GROUP BY c.id ORDER BY c.name
    """, (f"%{query}%", f"%{query}%")).fetchall()
    return render_template("customers.html", customers=rows, query=query)


@app.route("/customers/new", methods=("GET", "POST"))
@login_required
def new_customer():
    if request.method == "POST":
        name, phone, address = request.form["name"].strip(), request.form["phone"].strip(), request.form.get("address", "").strip()
        if not name or not phone:
            flash("Name and phone number are required.", "danger")
        else:
            db = get_db()
            db.execute("INSERT INTO customers (name, phone, address) VALUES (?, ?, ?)", (name, phone, address))
            db.commit()
            flash("Customer added successfully.", "success")
            return redirect(url_for("customers"))
    return render_template("customer_form.html", customer=None)


@app.route("/customers/<int:customer_id>/edit", methods=("GET", "POST"))
@login_required
def edit_customer(customer_id):
    db = get_db()
    customer = db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if not customer:
        abort(404)
    if request.method == "POST":
        db.execute("UPDATE customers SET name=?, phone=?, address=? WHERE id=?", (request.form["name"].strip(), request.form["phone"].strip(), request.form.get("address", "").strip(), customer_id))
        db.commit()
        flash("Customer details updated.", "success")
        return redirect(url_for("customer_detail", customer_id=customer_id))
    return render_template("customer_form.html", customer=customer)


@app.post("/customers/<int:customer_id>/delete")
@login_required
def delete_customer(customer_id):
    db = get_db()
    order_count = db.execute("SELECT COUNT(*) FROM orders WHERE customer_id=?", (customer_id,)).fetchone()[0]
    incomplete_count = db.execute("SELECT COUNT(*) FROM orders WHERE customer_id=? AND status != 'COMPLETED'", (customer_id,)).fetchone()[0]
    if order_count and incomplete_count:
        flash("Customer can be deleted only after all orders are COMPLETED.", "warning")
    else:
        db.execute("DELETE FROM orders WHERE customer_id=?", (customer_id,))
        db.execute("DELETE FROM customers WHERE id=?", (customer_id,))
        db.commit()
        flash("Customer and completed order history deleted.", "success")
    return redirect(url_for("customers"))


@app.route("/customers/<int:customer_id>")
@login_required
def customer_detail(customer_id):
    db = get_db()
    customer = db.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not customer:
        abort(404)
    orders = db.execute("SELECT o.*, s.name AS service_name FROM orders o JOIN services s ON s.id=o.service_id WHERE customer_id=? ORDER BY created_at DESC", (customer_id,)).fetchall()
    return render_template("customer_detail.html", customer=customer, orders=orders)


@app.route("/orders")
@login_required
def orders():
    q = request.args.get("q", "").strip()
    status, payment_status, order_date = request.args.get("status", ""), request.args.get("payment_status", ""), request.args.get("date", "")
    if request.args.get("clear") == "1":
        flash("All order filters cleared.", "success")
        return redirect(url_for("orders"))
    conditions, params = ["(o.order_code LIKE ? OR c.name LIKE ? OR c.phone LIKE ?)"], [f"%{q}%", f"%{q}%", f"%{q}%"]
    if status:
        conditions.append("o.status=?"); params.append(status)
    if payment_status:
        conditions.append("o.payment_status=?"); params.append(payment_status)
    if order_date:
        conditions.append("o.date_received=?"); params.append(order_date)
    rows = get_db().execute(f"""
        SELECT o.*, c.name AS customer_name, c.phone, s.name AS service_name
        FROM orders o JOIN customers c ON c.id=o.customer_id JOIN services s ON s.id=o.service_id
        WHERE {' AND '.join(conditions)} ORDER BY o.created_at DESC
    """, params).fetchall()
    return render_template("orders.html", orders=rows, q=q, selected_status=status, selected_payment=payment_status, selected_date=order_date)


@app.route("/orders/clear")
@login_required
def clear_order_filters():
    return redirect(url_for("orders", _external=False), code=303)


def calculate_payment(total, amount_paid):
    amount = max(0, min(float(amount_paid or 0), total))
    balance = round(total - amount, 2)
    status = "Paid" if balance == 0 else ("Partially Paid" if amount > 0 else "Unpaid")
    return amount, balance, status


@app.route("/orders/new", methods=("GET", "POST"))
@login_required
def new_order():
    db = get_db()
    if request.method == "POST":
        customer_id, service_id = request.form["customer_id"], request.form["service_id"]
        weight = float(request.form.get("weight", 0) or 0)
        service = db.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
        total = round(weight * service["price_per_kg"], 2)
        amount_paid, balance, payment_status = calculate_payment(total, request.form.get("amount_paid"))
        order_code = f"FL-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:5].upper()}"
        token = uuid.uuid4().hex
        cursor = db.execute("""INSERT INTO orders
            (order_code, customer_id, service_id, weight, price_per_kg, total_price, date_received, expected_pickup, amount_paid, balance, payment_method, payment_status, status, notes, qr_token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (order_code, customer_id, service_id, weight, service["price_per_kg"], total, request.form["date_received"], request.form["expected_pickup"], amount_paid, balance, request.form.get("payment_method", "Cash"), payment_status, "RECEIVED", request.form.get("notes", "").strip(), token))
        order_id = cursor.lastrowid
        db.execute("INSERT INTO order_status_history (order_id, status) VALUES (?, ?)", (order_id, "RECEIVED"))
        if amount_paid:
            db.execute("INSERT INTO payments (order_id, amount, method) VALUES (?, ?, ?)", (order_id, amount_paid, request.form.get("payment_method", "Cash")))
        db.commit()
        flash(f"Order {order_code} created. QR code is ready.", "success")
        return redirect(url_for("order_detail", order_id=order_id))
    return render_template("order_form.html", customers=db.execute("SELECT * FROM customers ORDER BY name").fetchall(), services=db.execute("SELECT * FROM services WHERE active=1 ORDER BY name").fetchall(), order=None)


@app.route("/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    db = get_db()
    order = db.execute("""SELECT o.*, c.name AS customer_name, c.phone, c.address, s.name AS service_name
        FROM orders o JOIN customers c ON c.id=o.customer_id JOIN services s ON s.id=o.service_id WHERE o.id=?""", (order_id,)).fetchone()
    if not order:
        abort(404)
    history = db.execute("SELECT * FROM order_status_history WHERE order_id=? ORDER BY changed_at", (order_id,)).fetchall()
    return render_template("order_detail.html", order=order, history=history)


@app.post("/orders/<int:order_id>/status")
@login_required
def update_status(order_id):
    new_status = request.form.get("status")
    if new_status not in STATUSES:
        abort(400)
    db = get_db()
    db.execute("UPDATE orders SET status=? WHERE id=?", (new_status, order_id))
    db.execute("INSERT INTO order_status_history (order_id, status) VALUES (?, ?)", (order_id, new_status))
    db.commit()
    flash("Laundry status updated.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@app.post("/orders/<int:order_id>/payment")
@login_required
def update_payment(order_id):
    db = get_db()
    order = db.execute("SELECT total_price FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    amount_paid, balance, payment_status = calculate_payment(order["total_price"], request.form.get("amount_paid"))
    method = request.form.get("payment_method", "Cash")
    if method not in PAYMENT_METHODS:
        abort(400)
    db.execute("UPDATE orders SET amount_paid=?, balance=?, payment_method=?, payment_status=? WHERE id=?", (amount_paid, balance, method, payment_status, order_id))
    db.execute("DELETE FROM payments WHERE order_id=?", (order_id,))
    if amount_paid:
        db.execute("INSERT INTO payments (order_id, amount, method) VALUES (?, ?, ?)", (order_id, amount_paid, method))
    db.commit()
    flash("Payment status updated.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@app.post("/orders/<int:order_id>/payment/complete")
@login_required
def complete_payment(order_id):
    db = get_db()
    order = db.execute("SELECT total_price, payment_method FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    db.execute("UPDATE orders SET amount_paid=total_price, balance=0, payment_status='Paid' WHERE id=?", (order_id,))
    db.execute("DELETE FROM payments WHERE order_id=?", (order_id,))
    db.execute("INSERT INTO payments (order_id, amount, method) VALUES (?, ?, ?)", (order_id, order["total_price"], order["payment_method"]))
    db.commit()
    flash("Payment completed. Order is fully paid.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@app.post("/orders/<int:order_id>/delete")
@login_required
def delete_order(order_id):
    db = get_db()
    order = db.execute("SELECT order_code FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    db.execute("DELETE FROM orders WHERE id=?", (order_id,))
    db.commit()
    flash(f"Order {order['order_code']} deleted.", "success")
    return redirect(url_for("orders"))


@app.route("/services")
@login_required
def services():
    return render_template("services.html", services=get_db().execute("SELECT * FROM services ORDER BY active DESC, name").fetchall())


@app.route("/services/<int:service_id>/edit")
@login_required
def edit_service(service_id):
    service = get_db().execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
    if not service:
        abort(404)
    return render_template("service_edit.html", service=service)


@app.post("/services/save")
@login_required
def save_service():
    db = get_db()
    service_id = request.form.get("service_id")
    values = (request.form["name"].strip(), float(request.form["price_per_kg"]), request.form.get("description", "").strip())
    if service_id:
        db.execute("UPDATE services SET name=?, price_per_kg=?, description=? WHERE id=?", (*values, service_id))
    else:
        db.execute("INSERT INTO services (name, price_per_kg, description) VALUES (?, ?, ?)", values)
    db.commit()
    flash("Service saved.", "success")
    return redirect(url_for("services"))


@app.post("/services/<int:service_id>/delete")
@login_required
def delete_service(service_id):
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM orders WHERE service_id=?", (service_id,)).fetchone()[0]:
        db.execute("UPDATE services SET active=0 WHERE id=?", (service_id,))
        flash("Service archived because it is used by an order.", "warning")
    else:
        db.execute("DELETE FROM services WHERE id=?", (service_id,))
        flash("Service deleted.", "success")
    db.commit()
    return redirect(url_for("services"))


@app.route("/track/<token>")
def track_order(token):
    db = get_db()
    order = db.execute("""SELECT o.*, c.name AS customer_name, s.name AS service_name
        FROM orders o JOIN customers c ON c.id=o.customer_id JOIN services s ON s.id=o.service_id WHERE o.qr_token=?""", (token,)).fetchone()
    if not order:
        abort(404)
    customer_orders = db.execute("""SELECT o.order_code, o.total_price, o.status,
        o.expected_pickup, o.qr_token, s.name AS service_name
        FROM orders o JOIN services s ON s.id=o.service_id
        WHERE o.customer_id=? ORDER BY o.created_at DESC""", (order["customer_id"],)).fetchall()
    return render_template("tracking.html", order=order, customer_orders=customer_orders)


@app.route("/orders/<int:order_id>/qr.png")
@login_required
def order_qr(order_id):
    order = get_db().execute("SELECT order_code, qr_token FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    image = qrcode.make(tracking_url(order["qr_token"]))
    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return send_file(output, mimetype="image/png", download_name=f"{order['order_code']}-qr.png")


@app.route("/orders/<int:order_id>/receipt")
@login_required
def receipt(order_id):
    db = get_db()
    order = db.execute("""SELECT o.*, c.name AS customer_name, c.phone, s.name AS service_name
        FROM orders o JOIN customers c ON c.id=o.customer_id JOIN services s ON s.id=o.service_id WHERE o.id=?""", (order_id,)).fetchone()
    if not order:
        abort(404)
    customer_orders = db.execute("""SELECT o.order_code, o.service_id, o.total_price, o.status,
        o.date_received, s.name AS service_name
        FROM orders o JOIN services s ON s.id=o.service_id
        WHERE o.customer_id=? ORDER BY o.created_at DESC""", (order["customer_id"],)).fetchall()
    return render_template("receipt.html", order=order, customer_orders=customer_orders)


@app.route("/api/services/<int:service_id>")
def service_price(service_id):
    service = get_db().execute("SELECT price_per_kg FROM services WHERE id=? AND active=1", (service_id,)).fetchone()
    return jsonify({"price_per_kg": service["price_per_kg"] if service else 0})


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

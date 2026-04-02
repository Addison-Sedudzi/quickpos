"""
POS System - Point of Sale Application
A comprehensive POS system built with Flask, PostgreSQL, HTML/CSS/JavaScript
"""

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import psycopg2
import psycopg2.extras
import hashlib
import os
from dotenv import load_dotenv

load_dotenv()

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
import json
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'quickpos-default-secret-key-change-in-production')
DATABASE_URL = os.environ.get('DATABASE_URL', '')


# ─── PostgreSQL Row / Cursor / Connection Wrappers ───
# These make psycopg2 behave like sqlite3 so minimal code changes are needed.

class RowWrapper:
    """Wraps a psycopg2 tuple row to support both index and column-name access."""
    def __init__(self, row, columns):
        self._row = tuple(row)
        self._columns = [col.lower() for col in columns]

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._row[key]
        try:
            return self._row[self._columns.index(key.lower())]
        except ValueError:
            raise KeyError(key)

    def keys(self):
        return list(self._columns)

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, ValueError):
            return default

    def __iter__(self):
        return iter(self._row)

    def __len__(self):
        return len(self._row)

    def __repr__(self):
        return repr(dict(zip(self._columns, self._row)))


class CursorWrapper:
    """Wraps a psycopg2 cursor to behave like a sqlite3 cursor."""
    def __init__(self, raw_cursor):
        self._cur = raw_cursor

    def execute(self, sql, params=None):
        self._cur.execute(sql, params)
        return self

    def executemany(self, sql, params_list):
        self._cur.executemany(sql, params_list)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._cur.description] if self._cur.description else []
        return RowWrapper(row, cols)

    def fetchall(self):
        rows = self._cur.fetchall()
        cols = [d[0] for d in self._cur.description] if self._cur.description else []
        return [RowWrapper(r, cols) for r in rows]


class DBWrapper:
    """Wraps a psycopg2 connection to behave like a sqlite3 connection."""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return CursorWrapper(cur)

    def executemany(self, sql, params_list):
        cur = self._conn.cursor()
        cur.executemany(sql, params_list)
        return CursorWrapper(cur)

    def cursor(self):
        return CursorWrapper(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


# ─── Database Helper ───
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return DBWrapper(conn)


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


# ─── Auth Decorator ───
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if session.get('role') not in roles:
                flash('Access denied. Insufficient permissions.', 'error')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator


# ─── Initialize Database ───
def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('Admin', 'Manager', 'Cashier')),
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
    ''')

    # Products table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            product_id SERIAL PRIMARY KEY,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            barcode TEXT UNIQUE,
            supplier TEXT,
            low_stock_threshold INTEGER DEFAULT 10,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Customers table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            customer_id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            address TEXT,
            loyalty_points INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Sales table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            sale_id SERIAL PRIMARY KEY,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER NOT NULL,
            customer_id INTEGER,
            subtotal REAL NOT NULL DEFAULT 0,
            discount REAL NOT NULL DEFAULT 0,
            tax REAL NOT NULL DEFAULT 0,
            total_amount REAL NOT NULL,
            payment_method TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        )
    ''')

    # Sales Items table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales_items (
            sale_item_id SERIAL PRIMARY KEY,
            sale_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            total REAL NOT NULL,
            FOREIGN KEY (sale_id) REFERENCES sales(sale_id),
            FOREIGN KEY (product_id) REFERENCES products(product_id)
        )
    ''')

    # Inventory log table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory_log (
            log_id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL,
            change_type TEXT NOT NULL,
            quantity_change INTEGER NOT NULL,
            previous_quantity INTEGER NOT NULL,
            new_quantity INTEGER NOT NULL,
            reason TEXT,
            user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(product_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')

    # Payments table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            payment_id SERIAL PRIMARY KEY,
            sale_id INTEGER NOT NULL,
            payment_method TEXT NOT NULL,
            amount_paid REAL NOT NULL,
            change_given REAL NOT NULL DEFAULT 0,
            payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sale_id) REFERENCES sales(sale_id)
        )
    ''')

    # Transaction logs for security
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transaction_logs (
            log_id SERIAL PRIMARY KEY,
            user_id INTEGER,
            action TEXT NOT NULL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')

    # Refunds table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS refunds (
            refund_id SERIAL PRIMARY KEY,
            sale_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            refund_amount REAL NOT NULL,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sale_id) REFERENCES sales(sale_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')

    # Suppliers table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS suppliers (
            supplier_id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            contact_person TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Promotions / Discount codes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promotions (
            promo_id SERIAL PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            description TEXT,
            discount_type TEXT NOT NULL CHECK(discount_type IN ('percentage', 'fixed')),
            discount_value REAL NOT NULL,
            min_purchase REAL DEFAULT 0,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Insert default admin user if not exists
    admin_exists = cursor.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'").fetchone()[0]
    if not admin_exists:
        cursor.execute(
            "INSERT INTO users (username, password, full_name, role, email) VALUES (%s, %s, %s, %s, %s)",
            ('admin', hash_password('admin123'), 'System Administrator', 'Admin', 'admin@pos.com')
        )

    # Insert sample products if table is empty
    product_count = cursor.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if product_count == 0:
        sample_products = [
            ('Coca Cola 500ml', 'Beverages', 5.00, 100, 'BEV001', 'Coca Cola Co.', 20),
            ('Bread - White', 'Bakery', 8.00, 50, 'BAK001', 'Local Bakery', 10),
            ('Rice 5kg', 'Grains', 45.00, 80, 'GRN001', 'Rice Distributor', 15),
            ('Milk 1L', 'Dairy', 12.00, 60, 'DAI001', 'FanMilk', 10),
            ('Sugar 1kg', 'Groceries', 10.00, 90, 'GRC001', 'Sugar Co.', 20),
            ('Eggs (Crate)', 'Poultry', 35.00, 40, 'PLT001', 'Local Farm', 10),
            ('Cooking Oil 1L', 'Groceries', 18.00, 70, 'GRC002', 'Oil Factory', 15),
            ('Tomato Paste', 'Canned', 6.00, 120, 'CAN001', 'Tomato Co.', 25),
            ('Bottled Water 1.5L', 'Beverages', 3.00, 200, 'BEV002', 'Voltic', 30),
            ('Instant Noodles', 'Groceries', 4.00, 150, 'GRC003', 'Indomie', 30),
            ('Tissue Paper', 'Household', 8.00, 80, 'HOU001', 'Softcare', 15),
            ('Detergent 500g', 'Household', 12.00, 60, 'HOU002', 'OMO', 10),
        ]
        cursor.executemany(
            "INSERT INTO products (product_name, category, price, quantity, barcode, supplier, low_stock_threshold) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            sample_products
        )

    # Insert sample customer
    cust_count = cursor.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    if cust_count == 0:
        cursor.execute(
            "INSERT INTO customers (name, phone, email, address, loyalty_points) VALUES (%s, %s, %s, %s, %s)",
            ('Walk-in Customer', '0000000000', 'walkin@pos.com', 'N/A', 0)
        )

    # Insert sample suppliers
    sup_count = cursor.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    if sup_count == 0:
        sample_suppliers = [
            ('Coca Cola Co.', 'Kwame Mensah', '024-111-1111', 'cocacola@supply.com', 'Accra Industrial Area'),
            ('Local Bakery', 'Ama Serwaa', '024-222-2222', 'bakery@supply.com', 'Kumasi Adum'),
            ('Rice Distributor', 'Yaw Asante', '024-333-3333', 'rice@supply.com', 'Tema Harbour'),
            ('FanMilk', 'Kofi Owusu', '024-444-4444', 'fanmilk@supply.com', 'Accra'),
            ('Voltic', 'Abena Pokua', '024-555-5555', 'voltic@supply.com', 'Accra'),
            ('Indomie', 'Nana Yaw', '024-666-6666', 'indomie@supply.com', 'Tema'),
        ]
        cursor.executemany(
            "INSERT INTO suppliers (name, contact_person, phone, email, address) VALUES (%s, %s, %s, %s, %s)",
            sample_suppliers
        )

    # Insert sample promotions
    promo_count = cursor.execute("SELECT COUNT(*) FROM promotions").fetchone()[0]
    if promo_count == 0:
        cursor.executemany(
            "INSERT INTO promotions (code, description, discount_type, discount_value, min_purchase, start_date, end_date) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                ('WELCOME10', 'Welcome discount - 10% off', 'percentage', 10, 20, '2025-01-01', '2026-12-31'),
                ('SAVE5', 'GHS 5 off purchases above GHS 50', 'fixed', 5, 50, '2025-01-01', '2026-12-31'),
            ]
        )

    # Add customer_name column to sales if it doesn't exist yet
    try:
        cursor.execute("ALTER TABLE sales ADD COLUMN customer_name TEXT")
    except Exception:
        pass  # Column already exists

    conn.commit()

    # Backfill: create customer records for sales that have a typed name but no customer_id
    try:
        unnamed = cursor.execute("""
            SELECT DISTINCT customer_name FROM sales
            WHERE customer_name IS NOT NULL AND customer_id IS NULL
        """).fetchall()
        for row in unnamed:
            name = row[0]
            if not name:
                continue
            existing = cursor.execute(
                "SELECT customer_id FROM customers WHERE LOWER(name) = LOWER(%s)", (name,)
            ).fetchone()
            if existing:
                cid = existing[0]
            else:
                cid = cursor.execute(
                    "INSERT INTO customers (name, phone, email, address, loyalty_points) VALUES (%s, '', '', '', 0) RETURNING customer_id",
                    (name,)
                ).fetchone()[0]
            cursor.execute(
                "UPDATE sales SET customer_id = %s WHERE LOWER(customer_name) = LOWER(%s) AND customer_id IS NULL",
                (cid, name)
            )
            total = cursor.execute(
                "SELECT COALESCE(SUM(total_amount), 0) FROM sales WHERE customer_id = %s", (cid,)
            ).fetchone()[0]
            cursor.execute(
                "UPDATE customers SET loyalty_points = %s WHERE customer_id = %s",
                (int(total // 10), cid)
            )
        conn.commit()
    except Exception:
        pass

    conn.close()


# ─── Logging Helper ───
def log_action(user_id, action, details=""):
    conn = get_db()
    conn.execute("INSERT INTO transaction_logs (user_id, action, details) VALUES (%s, %s, %s)",
                 (user_id, action, details))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════

# ─── Authentication ───
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username = %s AND password = %s AND is_active = 1",
                            (username, hash_password(password))).fetchone()
        conn.close()
        if user:
            session['user_id'] = user['user_id']
            session['username'] = user['username']
            session['full_name'] = user['full_name']
            session['role'] = user['role']
            log_action(user['user_id'], 'LOGIN', f"User {username} logged in")
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    if 'user_id' in session:
        log_action(session['user_id'], 'LOGOUT', f"User {session['username']} logged out")
    session.clear()
    return redirect(url_for('login'))


# ─── Dashboard ───
@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')

    # Today's stats
    today_sales = conn.execute(
        "SELECT COUNT(*) as count, COALESCE(SUM(total_amount), 0) as total FROM sales WHERE date::date = %s", (today,)
    ).fetchone()

    total_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    low_stock = conn.execute("SELECT COUNT(*) FROM products WHERE quantity <= low_stock_threshold").fetchone()[0]
    total_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]

    # Recent sales
    recent_sales = conn.execute('''
        SELECT s.sale_id, s.date, s.total_amount, s.payment_method, u.full_name as cashier,
               COALESCE(c.name, s.customer_name, 'Walk-in') as customer_name
        FROM sales s
        JOIN users u ON s.user_id = u.user_id
        LEFT JOIN customers c ON s.customer_id = c.customer_id
        ORDER BY s.date DESC LIMIT 10
    ''').fetchall()

    # Low stock products
    low_stock_products = conn.execute(
        "SELECT * FROM products WHERE quantity <= low_stock_threshold ORDER BY quantity ASC LIMIT 10"
    ).fetchall()

    # Sales over last 7 days for chart
    sales_chart_data = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        row = conn.execute(
            "SELECT COALESCE(SUM(total_amount), 0) as total FROM sales WHERE date::date = %s", (d,)
        ).fetchone()
        sales_chart_data.append({'date': d, 'total': row['total']})

    conn.close()
    return render_template('dashboard.html',
                           today_sales=today_sales,
                           total_products=total_products,
                           low_stock=low_stock,
                           total_customers=total_customers,
                           recent_sales=recent_sales,
                           low_stock_products=low_stock_products,
                           sales_chart_data=json.dumps(sales_chart_data))


# ─── POS / Sales Screen ───
@app.route('/pos')
@login_required
def pos():
    conn = get_db()
    products = conn.execute("SELECT * FROM products WHERE quantity > 0 ORDER BY product_name").fetchall()
    customers = conn.execute("SELECT * FROM customers ORDER BY name").fetchall()
    conn.close()
    return render_template('pos.html', products=products, customers=customers)

@app.route('/api/search_product', methods=['GET'])
@login_required
def search_product():
    query = request.args.get('q', '').strip()
    conn = get_db()
    products = conn.execute(
        "SELECT * FROM products WHERE (product_name ILIKE %s OR barcode ILIKE %s OR category ILIKE %s) AND quantity > 0",
        (f'%{query}%', f'%{query}%', f'%{query}%')
    ).fetchall()
    conn.close()
    return jsonify([dict(zip(p.keys(), p)) for p in products])

@app.route('/api/barcode_lookup', methods=['GET'])
@login_required
def barcode_lookup():
    """Look up a product by exact barcode match — used by barcode scanners"""
    barcode = request.args.get('barcode', '').strip()
    conn = get_db()
    product = conn.execute(
        "SELECT * FROM products WHERE barcode = %s AND quantity > 0", (barcode,)
    ).fetchone()
    conn.close()
    if product:
        return jsonify({'found': True, 'product': dict(zip(product.keys(), product))})
    return jsonify({'found': False, 'message': f'No product found with barcode: {barcode}'})


@app.route('/api/product_lookup/<int:product_id>')
@login_required
def product_lookup(product_id):
    """Look up a product by ID — used by QR code scanner"""
    conn = get_db()
    product = conn.execute("SELECT * FROM products WHERE product_id = %s", (product_id,)).fetchone()
    conn.close()
    if product:
        return jsonify({'found': True, 'product': dict(zip(product.keys(), product))})
    return jsonify({'found': False})

@app.route('/api/barcode_image/<barcode_value>')
@login_required
def barcode_image(barcode_value):
    """Generate a Code 128 barcode as SVG"""
    from flask import Response
    svg = generate_code128_svg(barcode_value)
    return Response(svg, mimetype='image/svg+xml')


def generate_code128_svg(data, bar_width=2, height=60):
    """Generate a Code 128B barcode as pure SVG — no external libraries needed"""
    PATTERNS = [
        "11011001100", "11001101100", "11001100110", "10010011000", "10010001100",
        "10001001100", "10011001000", "10011000100", "10001100100", "11001001000",
        "11001000100", "11000100100", "10110011100", "10011011100", "10011001110",
        "10111001100", "10011101100", "10011100110", "11001110010", "11001011100",
        "11001001110", "11011100100", "11001110100", "11100101100", "11100100110",
        "11101100100", "11100110100", "11100110010", "11011011000", "11011000110",
        "11000110110", "10100011000", "10001011000", "10001000110", "10110001000",
        "10001101000", "10001100010", "11010001000", "11000101000", "11000100010",
        "10110111000", "10110001110", "10001101110", "10111011000", "10111000110",
        "10001110110", "11101110110", "11010001110", "11000101110", "11011101000",
        "11011100010", "11011101110", "11101011000", "11101000110", "11100010110",
        "11101101000", "11101100010", "11100011010", "11101111010", "11001000010",
        "11110001010", "10100110000", "10100001100", "10010110000", "10010000110",
        "10000101100", "10000100110", "10110010000", "10110000100", "10011010000",
        "10011000010", "10000110100", "10000110010", "11000010010", "11001010000",
        "11110111010", "11000010100", "10001111010", "10100111100", "10010111100",
        "10010011110", "10111100100", "10011110100", "10011110010", "11110100100",
        "11110010100", "11110010010", "11011011110", "11011110110", "11110110110",
        "10101111000", "10100011110", "10001011110", "10111101000", "10111100010",
        "11110101000", "11110100010", "10111011110", "10111101110", "11101011110",
        "11110101110", "11010000100", "11010010000", "11010011100",
        "11010111000", "11010001110", "11010011110",
    ]
    STOP_PATTERN = "1100011101011"
    START_B = 104

    codes = [START_B]
    checksum = START_B
    for i, ch in enumerate(data):
        val = ord(ch) - 32
        if val < 0 or val > 94:
            val = 0
        codes.append(val)
        checksum += val * (i + 1)
    codes.append(checksum % 103)

    pattern = ''.join(PATTERNS[c] for c in codes) + STOP_PATTERN

    quiet_zone = 10 * bar_width
    total_width = len(pattern) * bar_width + 2 * quiet_zone
    total_height = height + 24

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_width} {total_height}" width="{total_width}" height="{total_height}">',
        f'<rect width="{total_width}" height="{total_height}" fill="white"/>',
    ]

    x = quiet_zone
    for bit in pattern:
        if bit == '1':
            svg_parts.append(f'<rect x="{x}" y="4" width="{bar_width}" height="{height}" fill="black"/>')
        x += bar_width

    text_x = total_width / 2
    svg_parts.append(
        f'<text x="{text_x}" y="{height + 18}" text-anchor="middle" '
        f'font-family="monospace" font-size="12" fill="black">{data}</text>'
    )
    svg_parts.append('</svg>')
    return '\n'.join(svg_parts)


@app.route('/api/process_sale', methods=['POST'])
@login_required
def process_sale():
    data = request.get_json()
    items = data.get('items', [])
    customer_id = data.get('customer_id') or None  # convert "" / 0 / None to None
    if customer_id is not None:
        customer_id = int(customer_id)
    customer_name = data.get('customer_name', '').strip().title() or None
    payment_method = data.get('payment_method', 'Cash')
    discount = float(data.get('discount', 0))
    tax_rate = float(data.get('tax_rate', 0))
    amount_paid = float(data.get('amount_paid', 0))

    if not items:
        return jsonify({'success': False, 'message': 'No items in cart'}), 400

    conn = get_db()
    try:
        # If a name was typed but no registered customer selected, find or create a customer record
        if customer_name and not customer_id:
            existing = conn.execute(
                "SELECT customer_id FROM customers WHERE LOWER(name) = LOWER(%s)", (customer_name,)
            ).fetchone()
            if existing:
                customer_id = existing['customer_id']
            else:
                new_cust = conn.execute(
                    "INSERT INTO customers (name, phone, email, address, loyalty_points) VALUES (%s, '', '', '', 0) RETURNING customer_id",
                    (customer_name,)
                ).fetchone()
                customer_id = new_cust[0]

        subtotal = sum(item['price'] * item['quantity'] for item in items)
        tax = subtotal * (tax_rate / 100)
        total = subtotal - discount + tax

        # Check stock availability
        for item in items:
            product = conn.execute("SELECT quantity FROM products WHERE product_id = %s",
                                   (item['product_id'],)).fetchone()
            if not product or product['quantity'] < item['quantity']:
                return jsonify({'success': False,
                                'message': f'Insufficient stock for product ID {item["product_id"]}'}), 400

        # Create sale record — use RETURNING to get the new sale_id
        row = conn.execute(
            "INSERT INTO sales (user_id, customer_id, customer_name, subtotal, discount, tax, total_amount, payment_method) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING sale_id",
            (session['user_id'], customer_id, customer_name, subtotal, discount, tax, total, payment_method)
        ).fetchone()
        sale_id = row[0]

        # Insert sale items and update stock
        for item in items:
            conn.execute(
                "INSERT INTO sales_items (sale_id, product_id, quantity, price, total) VALUES (%s, %s, %s, %s, %s)",
                (sale_id, item['product_id'], item['quantity'], item['price'], item['price'] * item['quantity'])
            )
            prev_qty = conn.execute("SELECT quantity FROM products WHERE product_id = %s",
                                    (item['product_id'],)).fetchone()['quantity']
            new_qty = prev_qty - item['quantity']
            conn.execute("UPDATE products SET quantity = %s WHERE product_id = %s",
                         (new_qty, item['product_id']))
            conn.execute(
                "INSERT INTO inventory_log (product_id, change_type, quantity_change, previous_quantity, new_quantity, reason, user_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (item['product_id'], 'SALE', -item['quantity'], prev_qty, new_qty, f'Sale #{sale_id}', session['user_id'])
            )

        # Payment record
        change_given = max(0, amount_paid - total)
        conn.execute(
            "INSERT INTO payments (sale_id, payment_method, amount_paid, change_given) VALUES (%s, %s, %s, %s)",
            (sale_id, payment_method, amount_paid, change_given)
        )

        # Loyalty points
        if customer_id:
            points = int(total // 10)
            conn.execute("UPDATE customers SET loyalty_points = loyalty_points + %s WHERE customer_id = %s",
                         (points, customer_id))

        conn.commit()
        log_action(session['user_id'], 'SALE', f'Sale #{sale_id} - Total: GHS {total:.2f}')

        return jsonify({
            'success': True,
            'sale_id': sale_id,
            'subtotal': subtotal,
            'discount': discount,
            'tax': tax,
            'total': total,
            'change': change_given,
            'message': 'Sale completed successfully'
        })

    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        conn.close()


# ─── Receipt ───
@app.route('/receipt/<int:sale_id>')
@login_required
def receipt(sale_id):
    conn = get_db()
    sale = conn.execute('''
        SELECT s.*, u.full_name as cashier, COALESCE(c.name, s.customer_name, 'Walk-in') as customer_name
        FROM sales s
        JOIN users u ON s.user_id = u.user_id
        LEFT JOIN customers c ON s.customer_id = c.customer_id
        WHERE s.sale_id = %s
    ''', (sale_id,)).fetchone()

    items = conn.execute('''
        SELECT si.*, p.product_name, p.barcode
        FROM sales_items si
        JOIN products p ON si.product_id = p.product_id
        WHERE si.sale_id = %s
    ''', (sale_id,)).fetchall()

    payment = conn.execute("SELECT * FROM payments WHERE sale_id = %s", (sale_id,)).fetchone()
    conn.close()

    if not sale:
        flash('Sale not found', 'error')
        return redirect(url_for('pos'))

    return render_template('receipt.html', sale=sale, items=items, payment=payment)


# ─── Product Management ───
@app.route('/products')
@login_required
def products():
    conn = get_db()
    products = conn.execute("SELECT * FROM products ORDER BY product_name").fetchall()
    categories = conn.execute("SELECT DISTINCT category FROM products ORDER BY category").fetchall()
    conn.close()
    return render_template('products.html', products=products, categories=categories)

@app.route('/products/add', methods=['POST'])
@role_required('Admin', 'Manager')
def add_product():
    data = request.form
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO products (product_name, category, price, quantity, barcode, supplier, low_stock_threshold) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (data['product_name'], data['category'], float(data['price']),
             int(data['quantity']), data.get('barcode', ''), data.get('supplier', ''),
             int(data.get('low_stock_threshold', 10)))
        )
        conn.commit()
        log_action(session['user_id'], 'ADD_PRODUCT', f"Added product: {data['product_name']}")
        flash('Product added successfully', 'success')
    except psycopg2.IntegrityError:
        conn.rollback()
        flash('Barcode already exists', 'error')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()
    return redirect(url_for('products'))

@app.route('/products/edit/<int:product_id>', methods=['POST'])
@role_required('Admin', 'Manager')
def edit_product(product_id):
    data = request.form
    conn = get_db()
    try:
        conn.execute(
            "UPDATE products SET product_name=%s, category=%s, price=%s, quantity=%s, barcode=%s, supplier=%s, low_stock_threshold=%s WHERE product_id=%s",
            (data['product_name'], data['category'], float(data['price']),
             int(data['quantity']), data.get('barcode', ''), data.get('supplier', ''),
             int(data.get('low_stock_threshold', 10)), product_id)
        )
        conn.commit()
        log_action(session['user_id'], 'EDIT_PRODUCT', f"Edited product ID: {product_id}")
        flash('Product updated successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()
    return redirect(url_for('products'))

@app.route('/products/delete/<int:product_id>', methods=['POST'])
@role_required('Admin')
def delete_product(product_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM products WHERE product_id = %s", (product_id,))
        conn.commit()
        log_action(session['user_id'], 'DELETE_PRODUCT', f"Deleted product ID: {product_id}")
        flash('Product deleted successfully', 'success')
    except psycopg2.errors.ForeignKeyViolation:
        conn.rollback()
        flash('Cannot delete product — it has existing sales records.', 'error')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()
    return redirect(url_for('products'))


# ─── Inventory Management ───
@app.route('/inventory')
@login_required
def inventory():
    conn = get_db()
    products = conn.execute("SELECT * FROM products ORDER BY product_name").fetchall()
    low_stock = conn.execute(
        "SELECT * FROM products WHERE quantity <= low_stock_threshold ORDER BY quantity ASC"
    ).fetchall()
    logs = conn.execute('''
        SELECT il.*, p.product_name, u.full_name
        FROM inventory_log il
        JOIN products p ON il.product_id = p.product_id
        LEFT JOIN users u ON il.user_id = u.user_id
        ORDER BY il.created_at DESC LIMIT 50
    ''').fetchall()
    conn.close()
    return render_template('inventory.html', products=products, low_stock=low_stock, logs=logs)

@app.route('/inventory/adjust', methods=['POST'])
@role_required('Admin', 'Manager')
def adjust_inventory():
    product_id = int(request.form['product_id'])
    adjustment = int(request.form['adjustment'])
    reason = request.form.get('reason', 'Manual adjustment')

    conn = get_db()
    product = conn.execute("SELECT * FROM products WHERE product_id = %s", (product_id,)).fetchone()
    if product:
        prev_qty = product['quantity']
        new_qty = prev_qty + adjustment
        if new_qty < 0:
            flash('Stock cannot go below zero', 'error')
        else:
            conn.execute("UPDATE products SET quantity = %s WHERE product_id = %s", (new_qty, product_id))
            change_type = 'RESTOCK' if adjustment > 0 else 'ADJUSTMENT'
            conn.execute(
                "INSERT INTO inventory_log (product_id, change_type, quantity_change, previous_quantity, new_quantity, reason, user_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (product_id, change_type, adjustment, prev_qty, new_qty, reason, session['user_id'])
            )
            conn.commit()
            log_action(session['user_id'], 'INVENTORY_ADJUST', f"Product {product_id}: {prev_qty} -> {new_qty}")
            flash('Inventory adjusted successfully', 'success')
    else:
        flash('Product not found', 'error')
    conn.close()
    return redirect(url_for('inventory'))


# ─── Customer Management ───
@app.route('/customers')
@login_required
def customers():
    conn = get_db()
    customers = conn.execute("SELECT * FROM customers ORDER BY name").fetchall()
    conn.close()
    return render_template('customers.html', customers=customers)

@app.route('/customers/add', methods=['POST'])
@login_required
def add_customer():
    data = request.form
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO customers (name, phone, email, address) VALUES (%s, %s, %s, %s)",
            (data['name'], data.get('phone', ''), data.get('email', ''), data.get('address', ''))
        )
        conn.commit()
        log_action(session['user_id'], 'ADD_CUSTOMER', f"Added customer: {data['name']}")
        flash('Customer added successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()
    return redirect(url_for('customers'))

@app.route('/customers/edit/<int:customer_id>', methods=['POST'])
@login_required
def edit_customer(customer_id):
    data = request.form
    conn = get_db()
    try:
        conn.execute(
            "UPDATE customers SET name=%s, phone=%s, email=%s, address=%s WHERE customer_id=%s",
            (data['name'], data.get('phone', ''), data.get('email', ''), data.get('address', ''), customer_id)
        )
        conn.commit()
        flash('Customer updated successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()
    return redirect(url_for('customers'))

@app.route('/customers/delete/<int:customer_id>', methods=['POST'])
@role_required('Admin', 'Manager')
def delete_customer(customer_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
        conn.commit()
        flash('Customer deleted successfully', 'success')
    except psycopg2.errors.ForeignKeyViolation:
        conn.rollback()
        flash('Cannot delete customer — they have existing sales records.', 'error')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()
    return redirect(url_for('customers'))

@app.route('/customers/<int:customer_id>/history')
@login_required
def customer_history(customer_id):
    conn = get_db()
    customer = conn.execute("SELECT * FROM customers WHERE customer_id = %s", (customer_id,)).fetchone()
    purchases = conn.execute('''
        SELECT s.*, u.full_name as cashier
        FROM sales s
        JOIN users u ON s.user_id = u.user_id
        WHERE s.customer_id = %s
        ORDER BY s.date DESC
    ''', (customer_id,)).fetchall()
    conn.close()
    return jsonify({
        'customer': dict(zip(customer.keys(), customer)) if customer else None,
        'purchases': [dict(zip(p.keys(), p)) for p in purchases]
    })


# ─── User Management ───
@app.route('/users')
@role_required('Admin')
def users():
    conn = get_db()
    all_users = conn.execute("SELECT * FROM users ORDER BY full_name").fetchall()
    conn.close()
    return render_template('users.html', users=all_users)

@app.route('/users/add', methods=['POST'])
@role_required('Admin')
def add_user():
    data = request.form
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password, full_name, role, email) VALUES (%s, %s, %s, %s, %s)",
            (data['username'], hash_password(data['password']),
             data['full_name'], data['role'], data.get('email', ''))
        )
        conn.commit()
        log_action(session['user_id'], 'ADD_USER', f"Added user: {data['username']}")
        flash('User added successfully', 'success')
    except psycopg2.IntegrityError:
        conn.rollback()
        flash('Username already exists', 'error')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()
    return redirect(url_for('users'))

@app.route('/users/edit/<int:user_id>', methods=['POST'])
@role_required('Admin')
def edit_user(user_id):
    data = request.form
    conn = get_db()
    try:
        if data.get('password'):
            conn.execute(
                "UPDATE users SET full_name=%s, role=%s, email=%s, password=%s WHERE user_id=%s",
                (data['full_name'], data['role'], data.get('email', ''),
                 hash_password(data['password']), user_id)
            )
        else:
            conn.execute(
                "UPDATE users SET full_name=%s, role=%s, email=%s WHERE user_id=%s",
                (data['full_name'], data['role'], data.get('email', ''), user_id)
            )
        conn.commit()
        flash('User updated successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()
    return redirect(url_for('users'))

@app.route('/users/toggle/<int:user_id>', methods=['POST'])
@role_required('Admin')
def toggle_user(user_id):
    conn = get_db()
    user = conn.execute("SELECT is_active FROM users WHERE user_id = %s", (user_id,)).fetchone()
    if user:
        new_status = 0 if user['is_active'] else 1
        conn.execute("UPDATE users SET is_active = %s WHERE user_id = %s", (new_status, user_id))
        conn.commit()
        flash('User status updated', 'success')
    conn.close()
    return redirect(url_for('users'))


# ─── Reports ───
@app.route('/reports')
@role_required('Admin', 'Manager')
def reports():
    return render_template('reports.html')

@app.route('/api/reports/daily', methods=['GET'])
@role_required('Admin', 'Manager')
def daily_report():
    date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    conn = get_db()
    sales = conn.execute('''
        SELECT s.*, u.full_name as cashier, COALESCE(c.name, s.customer_name, 'Walk-in') as customer_name
        FROM sales s
        JOIN users u ON s.user_id = u.user_id
        LEFT JOIN customers c ON s.customer_id = c.customer_id
        WHERE s.date::date = %s
        ORDER BY s.date DESC
    ''', (date,)).fetchall()

    summary = conn.execute('''
        SELECT COUNT(*) as total_transactions,
               COALESCE(SUM(total_amount), 0) as total_revenue,
               COALESCE(SUM(discount), 0) as total_discounts,
               COALESCE(SUM(tax), 0) as total_tax,
               COALESCE(AVG(total_amount), 0) as avg_transaction
        FROM sales WHERE date::date = %s
    ''', (date,)).fetchone()

    payment_breakdown = conn.execute('''
        SELECT payment_method, COUNT(*) as count, SUM(total_amount) as total
        FROM sales WHERE date::date = %s
        GROUP BY payment_method
    ''', (date,)).fetchall()

    conn.close()
    return jsonify({
        'sales': [dict(zip(s.keys(), s)) for s in sales],
        'summary': dict(zip(summary.keys(), summary)),
        'payment_breakdown': [dict(zip(p.keys(), p)) for p in payment_breakdown]
    })

@app.route('/api/reports/weekly', methods=['GET'])
@role_required('Admin', 'Manager')
def weekly_report():
    end_date = request.args.get('end_date', datetime.now().strftime('%Y-%m-%d'))
    start_date = (datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=6)).strftime('%Y-%m-%d')

    conn = get_db()
    daily_totals = []
    for i in range(7):
        d = (datetime.strptime(start_date, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')
        row = conn.execute(
            "SELECT COALESCE(SUM(total_amount), 0) as total, COUNT(*) as count FROM sales WHERE date::date = %s", (d,)
        ).fetchone()
        daily_totals.append({'date': d, 'total': row['total'], 'count': row['count']})

    summary = conn.execute('''
        SELECT COUNT(*) as total_transactions,
               COALESCE(SUM(total_amount), 0) as total_revenue,
               COALESCE(AVG(total_amount), 0) as avg_transaction
        FROM sales WHERE date::date BETWEEN %s AND %s
    ''', (start_date, end_date)).fetchone()

    conn.close()
    return jsonify({
        'daily_totals': daily_totals,
        'summary': dict(zip(summary.keys(), summary)),
        'start_date': start_date,
        'end_date': end_date
    })

@app.route('/api/reports/product_performance', methods=['GET'])
@role_required('Admin', 'Manager')
def product_performance_report():
    conn = get_db()
    products = conn.execute('''
        SELECT p.product_id, p.product_name, p.category, p.price, p.quantity as current_stock,
               COALESCE(SUM(si.quantity), 0) as total_sold,
               COALESCE(SUM(si.total), 0) as total_revenue
        FROM products p
        LEFT JOIN sales_items si ON p.product_id = si.product_id
        GROUP BY p.product_id
        ORDER BY total_sold DESC
    ''').fetchall()
    conn.close()
    return jsonify([dict(zip(p.keys(), p)) for p in products])

@app.route('/api/reports/inventory', methods=['GET'])
@role_required('Admin', 'Manager')
def inventory_report():
    conn = get_db()
    products = conn.execute('''
        SELECT p.*,
               CASE WHEN p.quantity <= p.low_stock_threshold THEN 1 ELSE 0 END as is_low_stock
        FROM products p
        ORDER BY p.quantity ASC
    ''').fetchall()

    summary = conn.execute('''
        SELECT COUNT(*) as total_products,
               SUM(quantity) as total_stock,
               SUM(CASE WHEN quantity <= low_stock_threshold THEN 1 ELSE 0 END) as low_stock_count,
               SUM(CASE WHEN quantity = 0 THEN 1 ELSE 0 END) as out_of_stock_count,
               SUM(price * quantity) as total_stock_value
        FROM products
    ''').fetchone()

    conn.close()
    return jsonify({
        'products': [dict(zip(p.keys(), p)) for p in products],
        'summary': dict(zip(summary.keys(), summary))
    })

@app.route('/api/reports/cashier', methods=['GET'])
@role_required('Admin', 'Manager')
def cashier_report():
    date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    conn = get_db()
    cashiers = conn.execute('''
        SELECT u.user_id, u.full_name, u.username,
               COUNT(s.sale_id) as total_sales,
               COALESCE(SUM(s.total_amount), 0) as total_revenue,
               COALESCE(AVG(s.total_amount), 0) as avg_sale
        FROM users u
        LEFT JOIN sales s ON u.user_id = s.user_id AND s.date::date = %s
        WHERE u.role IN ('Cashier', 'Manager', 'Admin')
        GROUP BY u.user_id
        ORDER BY total_revenue DESC
    ''', (date,)).fetchall()
    conn.close()
    return jsonify([dict(zip(c.keys(), c)) for c in cashiers])


# ─── Backup ───
@app.route('/backup')
@role_required('Admin')
def backup():
    conn = get_db()
    logs = conn.execute('''
        SELECT tl.*, u.full_name
        FROM transaction_logs tl
        LEFT JOIN users u ON tl.user_id = u.user_id
        ORDER BY tl.created_at DESC LIMIT 100
    ''').fetchall()
    conn.close()
    return render_template('backup.html', logs=logs)

@app.route('/backup/create', methods=['POST'])
@role_required('Admin')
def create_backup():
    flash('Database backups are managed automatically by the hosting provider (Render).', 'success')
    return redirect(url_for('backup'))

@app.route('/backup/restore', methods=['POST'])
@role_required('Admin')
def restore_backup():
    flash('Manual restore is not available with cloud-hosted PostgreSQL. Contact your hosting provider.', 'error')
    return redirect(url_for('backup'))

@app.route('/api/backups', methods=['GET'])
@role_required('Admin')
def list_backups():
    return jsonify([])


# ══════════════════════════════════════════════
# ADVANCED FEATURES
# ══════════════════════════════════════════════

# ─── Returns / Refunds Module ───
@app.route('/returns')
@role_required('Admin', 'Manager')
def returns():
    conn = get_db()
    refunds = conn.execute('''
        SELECT r.*, s.total_amount as original_total, u.full_name as processed_by
        FROM refunds r
        JOIN sales s ON r.sale_id = s.sale_id
        JOIN users u ON r.user_id = u.user_id
        ORDER BY r.created_at DESC LIMIT 50
    ''').fetchall()
    conn.close()
    return render_template('returns.html', refunds=refunds)

@app.route('/api/sale_details/<int:sale_id>')
@login_required
def sale_details(sale_id):
    conn = get_db()
    sale = conn.execute('''
        SELECT s.*, u.full_name as cashier, COALESCE(c.name, s.customer_name, 'Walk-in') as customer_name
        FROM sales s JOIN users u ON s.user_id = u.user_id
        LEFT JOIN customers c ON s.customer_id = c.customer_id
        WHERE s.sale_id = %s
    ''', (sale_id,)).fetchone()
    items = conn.execute('''
        SELECT si.*, p.product_name FROM sales_items si
        JOIN products p ON si.product_id = p.product_id
        WHERE si.sale_id = %s
    ''', (sale_id,)).fetchall()
    conn.close()
    if not sale:
        return jsonify({'found': False})
    return jsonify({
        'found': True,
        'sale': dict(zip(sale.keys(), sale)),
        'items': [dict(zip(i.keys(), i)) for i in items]
    })

@app.route('/api/process_refund', methods=['POST'])
@role_required('Admin', 'Manager')
def process_refund():
    data = request.get_json()
    sale_id = data.get('sale_id')
    reason = data.get('reason', '')
    refund_items = data.get('items', [])

    conn = get_db()
    try:
        sale = conn.execute("SELECT * FROM sales WHERE sale_id = %s", (sale_id,)).fetchone()
        if not sale:
            return jsonify({'success': False, 'message': 'Sale not found'}), 404

        refund_total = 0
        for ri in refund_items:
            si = conn.execute("SELECT * FROM sales_items WHERE sale_item_id = %s",
                              (ri['sale_item_id'],)).fetchone()
            if si:
                qty = min(ri['quantity'], si['quantity'])
                refund_amount = qty * si['price']
                refund_total += refund_amount
                prev = conn.execute("SELECT quantity FROM products WHERE product_id = %s",
                                    (si['product_id'],)).fetchone()['quantity']
                new_qty = prev + qty
                conn.execute("UPDATE products SET quantity = %s WHERE product_id = %s",
                             (new_qty, si['product_id']))
                conn.execute('''INSERT INTO inventory_log
                    (product_id, change_type, quantity_change, previous_quantity, new_quantity, reason, user_id)
                    VALUES (%s, 'REFUND', %s, %s, %s, %s, %s)''',
                    (si['product_id'], qty, prev, new_qty, f'Refund for Sale #{sale_id}', session['user_id']))

        conn.execute('''INSERT INTO refunds (sale_id, user_id, refund_amount, reason) VALUES (%s, %s, %s, %s)''',
                     (sale_id, session['user_id'], refund_total, reason))
        conn.commit()
        log_action(session['user_id'], 'REFUND', f'Refund GHS {refund_total:.2f} for Sale #{sale_id}')
        return jsonify({'success': True, 'refund_total': refund_total})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        conn.close()


# ─── Supplier Management ───
@app.route('/suppliers')
@role_required('Admin', 'Manager')
def suppliers():
    conn = get_db()
    supplier_list = conn.execute('''
        SELECT s.*, COUNT(p.product_id) as product_count
        FROM suppliers s
        LEFT JOIN products p ON p.supplier = s.name
        GROUP BY s.supplier_id ORDER BY s.name
    ''').fetchall()
    conn.close()
    return render_template('suppliers.html', suppliers=supplier_list)

@app.route('/suppliers/add', methods=['POST'])
@role_required('Admin', 'Manager')
def add_supplier():
    data = request.form
    conn = get_db()
    try:
        conn.execute("INSERT INTO suppliers (name, contact_person, phone, email, address) VALUES (%s, %s, %s, %s, %s)",
                     (data['name'], data.get('contact_person', ''), data.get('phone', ''),
                      data.get('email', ''), data.get('address', '')))
        conn.commit()
        flash('Supplier added successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()
    return redirect(url_for('suppliers'))

@app.route('/suppliers/edit/<int:supplier_id>', methods=['POST'])
@role_required('Admin', 'Manager')
def edit_supplier(supplier_id):
    data = request.form
    conn = get_db()
    try:
        conn.execute("UPDATE suppliers SET name=%s, contact_person=%s, phone=%s, email=%s, address=%s WHERE supplier_id=%s",
                     (data['name'], data.get('contact_person', ''), data.get('phone', ''),
                      data.get('email', ''), data.get('address', ''), supplier_id))
        conn.commit()
        flash('Supplier updated', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()
    return redirect(url_for('suppliers'))


# ─── Profit Analytics ───
@app.route('/api/reports/profit', methods=['GET'])
@role_required('Admin', 'Manager')
def profit_report():
    period = request.args.get('period', '7')
    conn = get_db()
    days = int(period)
    start = (datetime.now() - timedelta(days=days-1)).strftime('%Y-%m-%d')

    daily_profit = []
    for i in range(days):
        d = (datetime.strptime(start, '%Y-%m-%d') + timedelta(days=i)).strftime('%Y-%m-%d')
        row = conn.execute('''
            SELECT COALESCE(SUM(si.total), 0) as revenue,
                   COALESCE(SUM(si.quantity), 0) as items_sold,
                   COUNT(DISTINCT s.sale_id) as transactions
            FROM sales s JOIN sales_items si ON s.sale_id = si.sale_id
            WHERE s.date::date = %s
        ''', (d,)).fetchone()
        daily_profit.append({
            'date': d, 'revenue': row['revenue'],
            'items_sold': row['items_sold'], 'transactions': row['transactions']
        })

    top_products = conn.execute('''
        SELECT p.product_name, SUM(si.quantity) as qty, SUM(si.total) as revenue
        FROM sales_items si JOIN products p ON si.product_id = p.product_id
        JOIN sales s ON si.sale_id = s.sale_id
        WHERE s.date::date >= %s
        GROUP BY p.product_id ORDER BY qty DESC LIMIT 10
    ''', (start,)).fetchall()

    category_revenue = conn.execute('''
        SELECT p.category, SUM(si.total) as revenue, SUM(si.quantity) as qty
        FROM sales_items si JOIN products p ON si.product_id = p.product_id
        JOIN sales s ON si.sale_id = s.sale_id
        WHERE s.date::date >= %s
        GROUP BY p.category ORDER BY revenue DESC
    ''', (start,)).fetchall()

    hourly = conn.execute('''
        SELECT TO_CHAR(date, 'HH24') as hour, COUNT(*) as count, SUM(total_amount) as total
        FROM sales WHERE date::date >= %s
        GROUP BY hour ORDER BY hour
    ''', (start,)).fetchall()

    conn.close()
    return jsonify({
        'daily': daily_profit,
        'top_products': [dict(zip(p.keys(), p)) for p in top_products],
        'category_revenue': [dict(zip(c.keys(), c)) for c in category_revenue],
        'hourly_pattern': [dict(zip(h.keys(), h)) for h in hourly]
    })


# ─── Export Data to CSV ───
@app.route('/api/export/<data_type>')
@role_required('Admin', 'Manager')
def export_csv(data_type):
    import csv
    import io
    from flask import Response

    conn = get_db()
    output = io.StringIO()
    writer = csv.writer(output)

    if data_type == 'products':
        writer.writerow(['ID', 'Name', 'Category', 'Price', 'Quantity', 'Barcode', 'Supplier', 'Low Stock Threshold'])
        rows = conn.execute("SELECT product_id, product_name, category, price, quantity, barcode, supplier, low_stock_threshold FROM products").fetchall()
        for r in rows:
            writer.writerow(list(r))
    elif data_type == 'sales':
        writer.writerow(['Sale ID', 'Date', 'Cashier', 'Customer', 'Subtotal', 'Discount', 'Tax', 'Total', 'Payment Method'])
        rows = conn.execute('''
            SELECT s.sale_id, s.date, u.full_name, COALESCE(c.name, s.customer_name, 'Walk-in'),
                   s.subtotal, s.discount, s.tax, s.total_amount, s.payment_method
            FROM sales s JOIN users u ON s.user_id = u.user_id
            LEFT JOIN customers c ON s.customer_id = c.customer_id ORDER BY s.date DESC
        ''').fetchall()
        for r in rows:
            writer.writerow(list(r))
    elif data_type == 'customers':
        writer.writerow(['ID', 'Name', 'Phone', 'Email', 'Address', 'Loyalty Points'])
        rows = conn.execute("SELECT customer_id, name, phone, email, address, loyalty_points FROM customers").fetchall()
        for r in rows:
            writer.writerow(list(r))
    elif data_type == 'inventory':
        writer.writerow(['Product', 'Category', 'Stock', 'Threshold', 'Status', 'Stock Value'])
        rows = conn.execute("""
            SELECT product_name, category, quantity, low_stock_threshold,
                   CASE WHEN quantity=0 THEN 'Out of Stock' WHEN quantity<=low_stock_threshold THEN 'Low Stock' ELSE 'OK' END,
                   price*quantity FROM products
        """).fetchall()
        for r in rows:
            writer.writerow(list(r))
    else:
        conn.close()
        return jsonify({'error': 'Invalid data type'}), 400

    conn.close()
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename={data_type}_export_{datetime.now().strftime("%Y%m%d")}.csv'})


# ─── Dashboard Analytics API ───
@app.route('/api/dashboard/stats')
@login_required
def dashboard_stats():
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    today_data = conn.execute(
        "SELECT COUNT(*) as c, COALESCE(SUM(total_amount),0) as t FROM sales WHERE date::date=%s", (today,)
    ).fetchone()
    yesterday_data = conn.execute(
        "SELECT COUNT(*) as c, COALESCE(SUM(total_amount),0) as t FROM sales WHERE date::date=%s", (yesterday,)
    ).fetchone()

    growth = 0
    if yesterday_data['t'] > 0:
        growth = ((today_data['t'] - yesterday_data['t']) / yesterday_data['t']) * 100

    conn.close()
    return jsonify({
        'today_sales': today_data['c'],
        'today_revenue': today_data['t'],
        'yesterday_revenue': yesterday_data['t'],
        'growth': round(growth, 1)
    })


# ─── Discount Codes / Promotions ───
@app.route('/promotions')
@role_required('Admin', 'Manager')
def promotions():
    conn = get_db()
    promos = conn.execute("SELECT * FROM promotions ORDER BY end_date DESC").fetchall()
    conn.close()
    return render_template('promotions.html', promotions=promos)

@app.route('/promotions/add', methods=['POST'])
@role_required('Admin', 'Manager')
def add_promotion():
    data = request.form
    conn = get_db()
    try:
        conn.execute('''INSERT INTO promotions (code, description, discount_type, discount_value, min_purchase, start_date, end_date, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 1)''',
                     (data['code'].upper(), data['description'], data['discount_type'],
                      float(data['discount_value']), float(data.get('min_purchase', 0)),
                      data['start_date'], data['end_date']))
        conn.commit()
        flash('Promotion created', 'success')
    except psycopg2.IntegrityError:
        conn.rollback()
        flash('Promo code already exists', 'error')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        conn.close()
    return redirect(url_for('promotions'))

@app.route('/promotions/toggle/<int:promo_id>', methods=['POST'])
@role_required('Admin', 'Manager')
def toggle_promotion(promo_id):
    conn = get_db()
    promo = conn.execute("SELECT is_active FROM promotions WHERE promo_id = %s", (promo_id,)).fetchone()
    if promo:
        conn.execute("UPDATE promotions SET is_active = %s WHERE promo_id = %s",
                     (0 if promo['is_active'] else 1, promo_id))
        conn.commit()
    conn.close()
    return redirect(url_for('promotions'))

@app.route('/api/validate_promo', methods=['GET'])
@login_required
def validate_promo():
    code = request.args.get('code', '').strip().upper()
    conn = get_db()
    promo = conn.execute('''SELECT * FROM promotions
        WHERE code = %s AND is_active = 1 AND CURRENT_DATE BETWEEN start_date::date AND end_date::date''',
        (code,)).fetchone()
    conn.close()
    if promo:
        return jsonify({'valid': True, 'promo': dict(zip(promo.keys(), promo))})
    return jsonify({'valid': False, 'message': 'Invalid or expired promo code'})


# ── Mobile Money Payment ───────────────────────────────────
import urllib.request
import urllib.error
import json as _json

@app.route('/api/initiate_momo', methods=['POST'])
@login_required
def initiate_momo():
    data = request.get_json()
    phone = data.get('phone', '').strip()
    network = data.get('network', 'mtn')
    amount = data.get('amount', 0)

    if not phone or not amount:
        return jsonify({'success': False, 'message': 'Phone number and amount are required'})

    if not PAYSTACK_SECRET_KEY:
        return jsonify({'success': False, 'message': 'Paystack not configured. Add PAYSTACK_SECRET_KEY to environment variables.'})

    payload = _json.dumps({
        'amount': int(float(amount) * 100),  # convert to pesewas
        'email': f'{phone}@goddid.pos',
        'currency': 'GHS',
        'mobile_money': {
            'phone': phone,
            'provider': network
        }
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.paystack.co/charge',
        data=payload,
        headers={
            'Authorization': f'Bearer {PAYSTACK_SECRET_KEY}',
            'Content-Type': 'application/json'
        },
        method='POST'
    )

    try:
        with urllib.request.urlopen(req) as response:
            result = _json.loads(response.read().decode())
            if result.get('status'):
                reference = result['data'].get('reference')
                return jsonify({'success': True, 'reference': reference})
            else:
                return jsonify({'success': False, 'message': result.get('message', 'Failed to initiate payment')})
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode()
            error_body = _json.loads(raw)
            message = error_body.get('message', raw or str(e))
        except Exception:
            message = f'HTTP Error {e.code}'
        return jsonify({'success': False, 'message': f'Paystack error ({e.code}): {message}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/check_payment/<reference>', methods=['GET'])
@login_required
def check_payment(reference):
    if not PAYSTACK_SECRET_KEY:
        return jsonify({'status': 'failed', 'message': 'Paystack not configured'})

    req = urllib.request.Request(
        f'https://api.paystack.co/transaction/verify/{reference}',
        headers={'Authorization': f'Bearer {PAYSTACK_SECRET_KEY}'},
        method='GET'
    )

    try:
        with urllib.request.urlopen(req) as response:
            result = _json.loads(response.read().decode())
            tx_status = result['data'].get('status')
            if tx_status == 'success':
                return jsonify({'status': 'success'})
            elif tx_status in ['failed', 'abandoned']:
                return jsonify({'status': 'failed'})
            else:
                return jsonify({'status': 'pending'})
    except Exception as e:
        return jsonify({'status': 'pending'})


# ── Chatbot ───────────────────────────────────
@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    data = request.get_json()
    message = data.get('message', '').strip().lower()
    if not message:
        return jsonify({'response': 'Please type a message.'})

    conn = get_db()

    # Greeting
    if any(w in message for w in ['hello', 'hi', 'hey', 'good morning', 'good afternoon', 'good evening']):
        conn.close()
        return jsonify({'response': 'Hello! I can help you with product information. Try asking:\n• "List all products"\n• "Price of [product]"\n• "Is [product] available?"\n• "Show low stock items"'})

    # List all products
    if any(p in message for p in ['list products', 'all products', 'what products', 'show products', 'what do you have', 'what do you sell', 'show all']):
        rows = conn.execute("SELECT product_name, category, price, quantity FROM products ORDER BY category, product_name").fetchall()
        conn.close()
        if not rows:
            return jsonify({'response': 'No products found in the database.'})
        response = 'Our products:\n'
        current_cat = None
        for p in rows:
            if p['category'] != current_cat:
                current_cat = p['category']
                response += f'\n{current_cat}:\n'
            stock = f'{p["quantity"]} in stock' if p['quantity'] > 0 else 'Out of stock'
            response += f'  • {p["product_name"]} - GHS {p["price"]:.2f} ({stock})\n'
        return jsonify({'response': response.strip()})

    # Categories
    if any(p in message for p in ['categories', 'category', 'types of']):
        rows = conn.execute("SELECT DISTINCT category FROM products ORDER BY category").fetchall()
        conn.close()
        if not rows:
            return jsonify({'response': 'No categories found.'})
        cats = [r['category'] for r in rows]
        return jsonify({'response': f'Product categories: {", ".join(cats)}'})

    # Low / out of stock
    if any(p in message for p in ['low stock', 'out of stock', 'running out', 'almost out', 'stock alert']):
        rows = conn.execute("SELECT product_name, quantity, low_stock_threshold FROM products WHERE quantity <= low_stock_threshold ORDER BY quantity").fetchall()
        conn.close()
        if not rows:
            return jsonify({'response': 'All products are well-stocked!'})
        response = 'Low / out of stock products:\n'
        for p in rows:
            if p['quantity'] == 0:
                response += f'  • {p["product_name"]} — OUT OF STOCK\n'
            else:
                response += f'  • {p["product_name"]} — Only {p["quantity"]} left\n'
        return jsonify({'response': response.strip()})

    # Price query
    if any(p in message for p in ['price', 'how much', 'cost', 'rate']):
        search = message
        for w in ['price of', 'price', 'how much is', 'how much does', 'how much for', 'how much', 'cost of', 'cost', 'rate of', 'rate', 'what is the', "what's the", 'the', 'of', 'for', '?']:
            search = search.replace(w, ' ')
        search = search.strip()
        if search:
            rows = conn.execute("SELECT product_name, price, quantity FROM products WHERE LOWER(product_name) LIKE %s ORDER BY product_name", (f'%{search}%',)).fetchall()
            conn.close()
            if rows:
                response = ''
                for p in rows:
                    stock = f'{p["quantity"]} in stock' if p['quantity'] > 0 else 'Out of stock'
                    response += f'{p["product_name"]}: GHS {p["price"]:.2f} ({stock})\n'
                return jsonify({'response': response.strip()})
        conn.close()
        return jsonify({'response': f'I couldn\'t find that product. Try "list products" to see everything we have.'})

    # Availability query
    if any(p in message for p in ['available', 'in stock', 'do you have', 'have you got', 'is there']):
        search = message
        for w in ['is there', 'do you have', 'have you got', 'available', 'in stock', 'is', 'are', 'the', '?']:
            search = search.replace(w, ' ')
        search = search.strip()
        if search:
            rows = conn.execute("SELECT product_name, price, quantity FROM products WHERE LOWER(product_name) LIKE %s ORDER BY product_name", (f'%{search}%',)).fetchall()
            conn.close()
            if rows:
                response = ''
                for p in rows:
                    if p['quantity'] > 0:
                        response += f'✓ {p["product_name"]} is available — {p["quantity"]} in stock (GHS {p["price"]:.2f})\n'
                    else:
                        response += f'✗ {p["product_name"]} is currently out of stock\n'
                return jsonify({'response': response.strip()})
        conn.close()
        return jsonify({'response': 'Please specify a product name. E.g., "Is milk available?"'})

    # General search
    rows = conn.execute(
        "SELECT product_name, price, quantity, category FROM products WHERE LOWER(product_name) LIKE %s OR LOWER(category) LIKE %s ORDER BY product_name LIMIT 5",
        (f'%{message}%', f'%{message}%')
    ).fetchall()
    conn.close()
    if rows:
        response = f'Results for "{message}":\n'
        for p in rows:
            stock = f'{p["quantity"]} in stock' if p['quantity'] > 0 else 'Out of stock'
            response += f'  • {p["product_name"]} ({p["category"]}) — GHS {p["price"]:.2f} — {stock}\n'
        return jsonify({'response': response.strip()})

    return jsonify({'response': f'I couldn\'t find anything for "{message}". You can ask me:\n• "List all products"\n• "Price of [product]"\n• "Is [product] available?"\n• "Show low stock items"'})


# ══════════════════════════════════════════════
# Ensure database is initialized when module is imported (for gunicorn)
init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print("\n" + "="*50)
    print("  POS System Started!")
    print(f"  Open: http://127.0.0.1:{port}")
    print("  Default login: admin / admin123")
    print("="*50 + "\n")
    app.run(debug=True, host='0.0.0.0', port=port)

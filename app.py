import base64, io, os, sqlite3, json, re, secrets
from datetime import datetime
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g, abort
from flask import send_file
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

from database import (
    DATABASE_URL,
    DB_ERROR_TYPES,
    DB_INTEGRITY_ERROR_TYPES,
    connect_postgres,
    initialize_postgres_schema,
)
from password_reset import create_password_reset_blueprint

ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
secret_key = os.getenv('SECRET_KEY')
if not secret_key:
    if os.getenv('VERCEL'):
        raise RuntimeError(
            'SECRET_KEY must be configured in the Vercel environment. '
            'Generate a stable key with: py -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    else:
        secret_key = 'local-dev-change-this-secret'
app.config['SECRET_KEY'] = secret_key
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv(
    'COOKIE_SECURE',
    '1' if os.getenv('VERCEL') else '0',
) == '1'

# SQLite is the local default; Vercel requires shared PostgreSQL storage.
if DATABASE_URL:
    DB_PATH = None
elif os.getenv("VERCEL"):
    raise RuntimeError(
        'DATABASE_URL must be configured on Vercel. '
        'Use a shared PostgreSQL database; SQLite storage is temporary across function instances.'
    )
else:
    DB_PATH = os.getenv(
        "DATABASE_PATH",
        str(ROOT / "freshfruit.db")
    )


PRODUCTS = [
    ('Táo Fuji Nhật Bản', 'Nhập khẩu', 89000, 'kg', 'Táo giòn ngọt, mọng nước, nhập khẩu trực tiếp. Chứa nhiều vitamin và chất chống oxy hóa.', '🍎', '/static/images/apple.jpg', 40),
    ('Cam vàng Úc', 'Nhập khẩu', 76000, 'kg', 'Cam vàng mọng nước, vị ngọt thanh mát, giàu vitamin C tăng cường đề kháng.', '🍊', '/static/images/orange.jpg', 35),
    ('Nho xanh không hạt', 'Nhập khẩu', 159000, 'kg', 'Nho xanh giòn ngọt, không hạt, trái mọng chùm đều đẹp mắt.', '🍇', '/static/images/grapes.jpg', 28),
    ('Dâu tây Đà Lạt', 'Việt Nam', 119000, 'hộp', 'Dâu tây giống New Zealand trồng tại Đà Lạt, quả đỏ mọng, thơm ngọt tự nhiên.', '🍓', '/static/images/strawberry.jpg', 30),
    ('Xoài cát Hòa Lộc', 'Việt Nam', 65000, 'kg', 'Đặc sản miền Tây, thịt xoài dẻo mịn, thơm ngát, vị ngọt đậm đà khó quên.', '🥭', '/static/images/mango.jpg', 45),
    ('Dưa hấu ruột đỏ', 'Theo mùa', 24000, 'kg', 'Dưa hấu Long An quả tròn đều, ruột đỏ tươi, nhiều nước và ngọt mát giải nhiệt.', '🍉', '/static/images/watermelon.jpg', 65),
    ('Chuối già Nam Mỹ', 'Việt Nam', 32000, 'nải', 'Chuối già Nam Mỹ chuẩn GlobalGAP, dồi dào kali và năng lượng cho cơ thể.', '🍌', '/static/images/banana.jpg', 60),
    ('Dứa mật', 'Theo mùa', 35000, 'quả', 'Dứa mật ngọt lịm, thơm lừng, thịt vàng ươm thích hợp ăn tươi hoặc ép nước.', '🍍', '/static/images/pineapple.jpg', 55),
    ('Kiwi xanh New Zealand', 'Nhập khẩu', 135000, 'kg', 'Kiwi xanh vị chua ngọt hài hòa, hàm lượng chất xơ và vitamin C vượt trội.', '🥝', '/static/images/kiwi.jpg', 26),
    ('Thanh long ruột đỏ', 'Việt Nam', 42000, 'kg', 'Thanh long ruột đỏ Bình Thuận tươi rói, vị ngọt thanh mát, màu sắc bắt mắt.', '🐉', '/static/images/dragonfruit.jpg', 48),
    ('Lê Hàn Quốc', 'Nhập khẩu', 99000, 'kg', 'Lê nâu Hàn Quốc mọng nước, vỏ mỏng thịt giòn ngọt, thanh nhiệt cơ thể.', '🍐', '/static/images/pear.jpg', 32),
    ('Giỏ quà trái cây', 'Giỏ quà', 399000, 'giỏ', 'Giỏ quà thiết kế sang trọng, kết hợp các loại trái cây cao cấp tươi ngon trong ngày.', '🎁', '/static/images/giftbasket.jpg', 18),
]

MAX_PRODUCT_IMAGE_BYTES = 1024 * 1024
PRODUCT_IMAGE_MIME_TYPES = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'WEBP': 'image/webp',
}


def encode_product_image(upload):
    if not upload or not upload.filename:
        return None

    raw_image = upload.stream.read(MAX_PRODUCT_IMAGE_BYTES + 1)
    if len(raw_image) > MAX_PRODUCT_IMAGE_BYTES:
        raise ValueError('Ảnh vượt quá dung lượng 1 MB.')

    try:
        with Image.open(io.BytesIO(raw_image)) as source:
            image_format = source.format
            if image_format not in PRODUCT_IMAGE_MIME_TYPES:
                raise ValueError('Chỉ chấp nhận ảnh JPG, PNG hoặc WebP.')
            if source.width * source.height > 20_000_000:
                raise ValueError('Kích thước ảnh quá lớn.')

            image = ImageOps.exif_transpose(source)
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            if image_format == 'JPEG' and image.mode not in ('RGB', 'L'):
                image = image.convert('RGB')

            output = io.BytesIO()
            save_options = {'optimize': True}
            if image_format in ('JPEG', 'WEBP'):
                save_options['quality'] = 85
            image.save(output, format=image_format, **save_options)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError('Tệp tải lên không phải ảnh hợp lệ.') from error

    encoded = base64.b64encode(output.getvalue()).decode('ascii')
    mime_type = PRODUCT_IMAGE_MIME_TYPES[image_format]
    return f'data:{mime_type};base64,{encoded}'

def db():
    if 'db' not in g:
        if DATABASE_URL:
            g.db = connect_postgres(DATABASE_URL)
        else:
            g.db = sqlite3.connect(DB_PATH, timeout=15)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys=ON')
    return g.db

app.register_blueprint(create_password_reset_blueprint(db))

@app.teardown_appcontext
def close_db(exc):
    conn = g.pop('db', None)
    if conn: conn.close()

def init_db():
    if DATABASE_URL:
        conn = connect_postgres(DATABASE_URL)
        try:
            initialize_postgres_schema(conn)
            if not conn.execute('SELECT 1 FROM products LIMIT 1').fetchone():
                for product in PRODUCTS:
                    conn.execute(
                        'INSERT INTO products(name, category, price, unit, description, emoji, image, stock) '
                        'VALUES(?,?,?,?,?,?,?,?)',
                        product,
                    )
            conn.execute("INSERT OR IGNORE INTO coupons(code, discount_percent) VALUES('FRESH10', 10)")
            seed_admin(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript('''
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, phone TEXT DEFAULT '', address TEXT DEFAULT '', role TEXT NOT NULL DEFAULT 'customer');
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, price INTEGER NOT NULL CHECK(price>=0), unit TEXT NOT NULL, description TEXT NOT NULL, emoji TEXT NOT NULL, image TEXT DEFAULT '', stock INTEGER NOT NULL DEFAULT 0 CHECK(stock>=0), active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS cart_items(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, product_id INTEGER NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL CHECK(quantity>0), UNIQUE(user_id, product_id));
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), recipient TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL, note TEXT DEFAULT '', total INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Chờ xác nhận', payment_method TEXT NOT NULL DEFAULT 'COD', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), product_id INTEGER NOT NULL REFERENCES products(id), product_name TEXT NOT NULL, unit_price INTEGER NOT NULL, quantity INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), product_id INTEGER NOT NULL REFERENCES products(id), stars INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5), content TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(user_id, product_id));
    CREATE TABLE IF NOT EXISTS coupons(code TEXT PRIMARY KEY, discount_percent INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS password_reset_tokens(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
    ''')
    cols = [col[1] for col in conn.execute("PRAGMA table_info(products)").fetchall()]
    if 'image' not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN image TEXT DEFAULT ''")
    if not conn.execute('SELECT 1 FROM products LIMIT 1').fetchone():
        conn.executemany('INSERT INTO products(name, category, price, unit, description, emoji, image, stock) VALUES(?,?,?,?,?,?,?,?)', PRODUCTS)
    else:
        fruit_images = {
            'Táo Fuji Nhật Bản': '/static/images/apple.jpg',
            'Cam vàng Úc': '/static/images/orange.jpg',
            'Nho xanh không hạt': '/static/images/grapes.jpg',
            'Dâu tây Đà Lạt': '/static/images/strawberry.jpg',
            'Xoài cát Hòa Lộc': '/static/images/mango.jpg',
            'Dưa hấu ruột đỏ': '/static/images/watermelon.jpg',
            'Chuối già Nam Mỹ': '/static/images/banana.jpg',
            'Dứa mật': '/static/images/pineapple.jpg',
            'Kiwi xanh New Zealand': '/static/images/kiwi.jpg',
            'Thanh long ruột đỏ': '/static/images/dragonfruit.jpg',
            'Lê Hàn Quốc': '/static/images/pear.jpg',
            'Giỏ quà trái cây': '/static/images/giftbasket.jpg',
        }
        for name, img in fruit_images.items():
            conn.execute("UPDATE products SET image=? WHERE name=? AND (image IS NULL OR image='')", (img, name))
    conn.execute("INSERT OR IGNORE INTO coupons(code, discount_percent) VALUES('FRESH10', 10)")
    seed_admin(conn)
    conn.commit()
    conn.close()

def seed_admin(conn):
    email = os.getenv('ADMIN_EMAIL', '').strip().lower()
    password = os.getenv('ADMIN_PASSWORD', '')
    name = os.getenv('ADMIN_NAME', 'FreshFruit Admin').strip()[:100]

    if not email and not password:
        return
    if not email or not password:
        app.logger.warning(
            'Admin bootstrap skipped: configure both ADMIN_EMAIL and ADMIN_PASSWORD.'
        )
        return
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        app.logger.error('Admin bootstrap skipped: ADMIN_EMAIL is not a valid email address.')
        return
    if len(password) < 12:
        app.logger.error('Admin bootstrap skipped: ADMIN_PASSWORD must contain at least 12 characters.')
        return
    if len(name) < 2:
        app.logger.error('Admin bootstrap skipped: ADMIN_NAME must contain at least 2 characters.')
        return

    password_hash = generate_password_hash(password)
    user = conn.execute(
        'SELECT id FROM users WHERE lower(email)=?',
        (email,),
    ).fetchone()
    if user:
        conn.execute(
            'UPDATE users SET email=?, name=?, password_hash=?, role=? WHERE id=?',
            (email, name, password_hash, 'admin', user[0]),
        )
    else:
        conn.execute(
            'INSERT INTO users(name, email, password_hash, role) VALUES(?,?,?,?)',
            (name, email, password_hash, 'admin'),
        )

@app.context_processor
def inject():
    count = 0
    if session.get('user_id'):
        count = db().execute('SELECT COALESCE(SUM(quantity),0) FROM cart_items WHERE user_id=?', (session['user_id'],)).fetchone()[0]
    return {
        'cart_count': count,
        'current_user': session.get('name'),
        'is_admin': session.get('role') == 'admin',
        'product_image_src': lambda product: (
            url_for('uploaded_product_image', pid=product['id'])
            if (product['image'] or '').startswith('data:image/')
            else product['image']
        ),
        'money': lambda x: f'{int(x):,}'.replace(',', '.') + 'đ'
    }

@app.before_request
def csrf_guard():
    if 'csrf' not in session:
        session['csrf'] = os.urandom(24).hex()
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        token = request.headers.get('X-CSRF-Token') or request.form.get('_csrf')
        if not token or token != session.get('csrf'):
            abort(400, 'CSRF token không hợp lệ')

@app.before_request
def validate_authenticated_session():
    user_id = session.get('user_id')
    if user_id is None:
        return

    email = session.get('email')
    if email:
        user = db().execute(
            'SELECT id, email, name, role FROM users WHERE lower(email)=?',
            (email.strip().lower(),),
        ).fetchone()
    else:
        user = db().execute(
            'SELECT id, email, name, role FROM users WHERE id=?',
            (user_id,),
        ).fetchone()
    if not user:
        session.clear()
        session['csrf'] = os.urandom(24).hex()
        flash('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.', 'warning')
        return

    session.update(
        user_id=user['id'],
        email=user['email'],
        name=user['name'],
        role=user['role'],
    )

@app.context_processor
def csrf_context():
    return {'csrf_token': lambda: session.get('csrf', '')}

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get('user_id'):
            flash('Vui lòng đăng nhập trước.', 'warning')
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if session.get('role') != 'admin': abort(403)
        return fn(*args, **kwargs)
    return wrapped

@app.get('/')
def home():
    products = db().execute('SELECT * FROM products WHERE active=1 ORDER BY id DESC LIMIT 8').fetchall()
    return render_template('home.html', products=products)

@app.get('/products')
def products():
    q = request.args.get('q', '').strip()[:80]
    category = request.args.get('category', '').strip()
    query = 'SELECT * FROM products WHERE active=1'
    params = []
    if q:
        query += ' AND name LIKE ?'
        params.append('%' + q + '%')
    if category:
        query += ' AND category=?'
        params.append(category)
    query += ' ORDER BY id DESC'
    rows = db().execute(query, params).fetchall()
    return render_template('products.html', products=rows, q=q, category=category, categories=['Việt Nam', 'Nhập khẩu', 'Theo mùa', 'Giỏ quà'])

@app.route('/product/<int:pid>', methods=['GET', 'POST'])
def product(pid):
    p = db().execute('SELECT * FROM products WHERE id=? AND active=1', (pid,)).fetchone()
    if not p: abort(404)
    if request.method == 'POST':
        if not session.get('user_id'):
            flash('Hãy đăng nhập để mua hàng.', 'warning')
            return redirect(url_for('login'))
        try:
            quantity = int(request.form.get('quantity', '1'))
        except ValueError:
            quantity = 0
        if not 1 <= quantity <= 100:
            flash('Số lượng không hợp lệ.', 'danger')
            return redirect(url_for('product', pid=pid))
        old = db().execute('SELECT quantity FROM cart_items WHERE user_id=? AND product_id=?', (session['user_id'], pid)).fetchone()
        if quantity + (old['quantity'] if old else 0) > p['stock']:
            flash('Không đủ hàng trong kho.', 'danger')
            return redirect(url_for('product', pid=pid))
        db().execute('INSERT INTO cart_items(user_id, product_id, quantity) VALUES(?,?,?) ON CONFLICT(user_id, product_id) DO UPDATE SET quantity=cart_items.quantity+excluded.quantity', (session['user_id'], pid, quantity))
        db().commit()
        flash('Đã thêm vào giỏ hàng.', 'success')
        return redirect(url_for('cart'))
    reviews = db().execute('SELECT r.*, u.name FROM reviews r JOIN users u ON r.user_id=u.id WHERE r.product_id=? ORDER BY r.id DESC', (pid,)).fetchall()
    return render_template('product.html', p=p, reviews=reviews)

@app.get('/product/<int:pid>/image')
def uploaded_product_image(pid):
    product = db().execute(
        'SELECT image FROM products WHERE id=?',
        (pid,),
    ).fetchone()
    image = product['image'] if product else ''
    if not image or not image.startswith('data:image/'):
        abort(404)

    header, separator, encoded = image.partition(',')
    mime_type = header[5:-7] if header.endswith(';base64') else ''
    if not separator or mime_type not in PRODUCT_IMAGE_MIME_TYPES.values():
        abort(404)
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except ValueError:
        abort(404)
    return send_file(io.BytesIO(image_bytes), mimetype=mime_type, max_age=3600)

@app.post('/product/<int:pid>/review')
@login_required
def review(pid):
    p = db().execute('SELECT id FROM products WHERE id=? AND active=1', (pid,)).fetchone()
    if not p: abort(404)
    try:
        stars = int(request.form.get('stars', 5))
    except ValueError:
        stars = 0
    content = request.form.get('content', '').strip()[:500]
    if not 1 <= stars <= 5 or not content:
        flash('Đánh giá chưa hợp lệ.', 'danger')
    else:
        db().execute('INSERT INTO reviews(user_id, product_id, stars, content, created_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id, product_id) DO UPDATE SET stars=excluded.stars, content=excluded.content, created_at=excluded.created_at', (session['user_id'], pid, stars, content, datetime.now().isoformat(timespec='seconds')))
        db().commit()
        flash('Đã lưu đánh giá.', 'success')
    return redirect(url_for('product', pid=pid))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()[:100]
        email = request.form.get('email', '').strip().lower()[:255]
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if len(name) < 2 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(password) < 8 or password != confirm:
            flash('Kiểm tra họ tên, email, mật khẩu (ít nhất 8 ký tự) và xác nhận.', 'danger')
        else:
            try:
                cursor = db().execute('INSERT INTO users(name, email, password_hash) VALUES(?,?,?)', (name, email, generate_password_hash(password)))
                db().commit()
                session.clear()
                session.update(user_id=cursor.lastrowid, email=email, name=name, role='customer', csrf=os.urandom(24).hex())
                flash('Đăng ký thành công!', 'success')
                return redirect(url_for('home'))
            except DB_INTEGRITY_ERROR_TYPES:
                db().rollback()
                flash('Email đã tồn tại.', 'danger')
    return render_template('auth.html', mode='register')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = db().execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session.update(user_id=user['id'], email=user['email'], name=user['name'], role=user['role'], csrf=os.urandom(24).hex())
            return redirect(url_for('admin') if user['role'] == 'admin' else url_for('home'))
        flash('Email hoặc mật khẩu không chính xác.', 'danger')
    return render_template('auth.html', mode='login')

@app.post('/logout')
def logout():
    session.clear()
    flash('Đã đăng xuất.', 'success')
    return redirect(url_for('home'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()[:100]
        phone = request.form.get('phone', '').strip()[:20]
        address = request.form.get('address', '').strip()[:300]
        if len(name) < 2:
            flash('Tên không hợp lệ.', 'danger')
        else:
            db().execute('UPDATE users SET name=?, phone=?, address=? WHERE id=?', (name, phone, address, session['user_id']))
            db().commit()
            session['name'] = name
            flash('Đã cập nhật hồ sơ.', 'success')
    user = db().execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
    return render_template('profile.html', user=user)

def cart_rows():
    rows = db().execute('SELECT c.quantity, p.* FROM cart_items c JOIN products p ON p.id=c.product_id WHERE c.user_id=?', (session['user_id'],)).fetchall()
    return rows, sum(r['quantity'] * r['price'] for r in rows)

@app.get('/cart')
@login_required
def cart():
    rows, total = cart_rows()
    return render_template('cart.html', rows=rows, total=total)

@app.post('/cart/update/<int:pid>')
@login_required
def cart_update(pid):
    try:
        qty = int(request.form.get('quantity', 1))
    except ValueError:
        qty = -1
    product = db().execute('SELECT stock FROM products WHERE id=? AND active=1', (pid,)).fetchone()
    if qty == 0:
        db().execute('DELETE FROM cart_items WHERE user_id=? AND product_id=?', (session['user_id'], pid))
    elif product and 1 <= qty <= product['stock']:
        db().execute('UPDATE cart_items SET quantity=? WHERE user_id=? AND product_id=?', (qty, session['user_id'], pid))
    else:
        flash('Số lượng không hợp lệ hoặc vượt quá tồn kho.', 'danger')
    db().commit()
    return redirect(url_for('cart'))

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    rows, total = cart_rows()
    if not rows:
        flash('Giỏ hàng đang trống.', 'warning')
        return redirect(url_for('products'))
    user = db().execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
    if request.method == 'POST':
        recipient = request.form.get('recipient', '').strip()[:100]
        phone = request.form.get('phone', '').strip()[:20]
        address = request.form.get('address', '').strip()[:300]
        note = request.form.get('note', '').strip()[:500]
        code = request.form.get('coupon', '').strip().upper()[:30]
        if len(recipient) < 2 or len(phone) < 9 or len(address) < 8:
            flash('Vui lòng nhập đủ người nhận, số điện thoại, địa chỉ.', 'danger')
        else:
            conn = db()
            try:
                if not DATABASE_URL:
                    conn.execute('BEGIN IMMEDIATE')
                lock_clause = ' FOR UPDATE OF p' if DATABASE_URL else ''
                latest = conn.execute('SELECT c.quantity, p.id, p.name, p.price, p.stock, p.active FROM cart_items c JOIN products p ON c.product_id=p.id WHERE c.user_id=?' + lock_clause, (session['user_id'],)).fetchall()
                if not latest or any(not r['active'] or r['quantity'] > r['stock'] for r in latest):
                    raise ValueError('Sản phẩm đã hết hàng hoặc không đủ số lượng.')
                subtotal = sum(r['quantity'] * r['price'] for r in latest)
                coupon = conn.execute('SELECT discount_percent FROM coupons WHERE code=? AND active=1', (code,)).fetchone() if code else None
                if code and not coupon:
                    raise ValueError('Mã giảm giá không hợp lệ.')
                total_price = subtotal * (100 - (coupon['discount_percent'] if coupon else 0)) // 100
                cur = conn.execute('INSERT INTO orders(user_id, recipient, phone, address, note, total, created_at) VALUES(?,?,?,?,?,?,?)', (session['user_id'], recipient, phone, address, note, total_price, datetime.now().isoformat(timespec='seconds')))
                oid = cur.lastrowid
                for r in latest:
                    conn.execute('INSERT INTO order_items(order_id, product_id, product_name, unit_price, quantity) VALUES(?,?,?,?,?)', (oid, r['id'], r['name'], r['price'], r['quantity']))
                    conn.execute('UPDATE products SET stock=stock-? WHERE id=?', (r['quantity'], r['id']))
                conn.execute('DELETE FROM cart_items WHERE user_id=?', (session['user_id'],))
                conn.commit()
                return redirect(url_for('order_detail', oid=oid))
            except (ValueError, *DB_ERROR_TYPES) as exc:
                conn.rollback()
                flash(str(exc), 'danger')
    return render_template('checkout.html', rows=rows, total=total, user=user)

@app.get('/orders')
@login_required
def orders():
    rows = db().execute('SELECT * FROM orders WHERE user_id=? ORDER BY id DESC', (session['user_id'],)).fetchall()
    return render_template('orders.html', orders=rows)

@app.get('/orders/<int:oid>')
@login_required
def order_detail(oid):
    order = db().execute('SELECT * FROM orders WHERE id=? AND (user_id=? OR ?=\'admin\')', (oid, session['user_id'], session.get('role'))).fetchone()
    if not order: abort(404)
    items = db().execute('SELECT * FROM order_items WHERE order_id=?', (oid,)).fetchall()
    return render_template('order_detail.html', order=order, items=items)

@app.get('/admin')
@admin_required
def admin():
    stats = {
        'users': db().execute('SELECT COUNT(*) FROM users').fetchone()[0],
        'products': db().execute('SELECT COUNT(*) FROM products').fetchone()[0],
        'orders': db().execute('SELECT COUNT(*) FROM orders').fetchone()[0],
        'revenue': db().execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE status='Đã giao'").fetchone()[0]
    }
    products = db().execute('SELECT * FROM products ORDER BY id DESC').fetchall()
    orders = db().execute('SELECT o.*, u.email FROM orders o JOIN users u ON o.user_id=u.id ORDER BY o.id DESC LIMIT 60').fetchall()
    return render_template('admin.html', stats=stats, products=products, orders=orders, statuses=['Chờ xác nhận', 'Đã xác nhận', 'Đang giao', 'Đã giao', 'Đã hủy'])

@app.post('/admin/product')
@admin_required
def admin_product():
    try:
        pid = int(request.form.get('id') or 0)
        name = request.form['name'].strip()[:120]
        category = request.form['category'].strip()[:50]
        unit = request.form['unit'].strip()[:20]
        description = request.form.get('description', '').strip()[:500]
        emoji = request.form.get('emoji', '🍎').strip()[:10]
        uploaded_image = encode_product_image(request.files.get('image'))
        price = int(request.form['price'])
        stock = int(request.form['stock'])
        if not name or not category or not unit or price < 0 or stock < 0:
            raise ValueError()
        if uploaded_image is not None:
            image = uploaded_image
        elif pid:
            existing = db().execute(
                'SELECT image FROM products WHERE id=?', (pid,)
            ).fetchone()
            image = existing['image'] if existing else ''
        else:
            image = ''
        if pid:
            db().execute('UPDATE products SET name=?, category=?, price=?, unit=?, description=?, emoji=?, image=?, stock=? WHERE id=?', (name, category, price, unit, description, emoji, image, stock, pid))
        else:
            db().execute('INSERT INTO products(name, category, price, unit, description, emoji, image, stock) VALUES(?,?,?,?,?,?,?,?)', (name, category, price, unit, description, emoji, image, stock))
        db().commit()
        flash('Đã lưu sản phẩm thành công.', 'success')
    except (ValueError, KeyError, *DB_ERROR_TYPES):
        db().rollback()
        flash('Không thể lưu sản phẩm. Vui lòng kiểm tra lại thông tin.', 'danger')
    return redirect(url_for('admin'))

@app.post('/admin/product/<int:pid>/toggle')
@admin_required
def admin_product_toggle(pid):
    db().execute('UPDATE products SET active=1-active WHERE id=?', (pid,))
    db().commit()
    return redirect(url_for('admin'))

@app.post('/admin/product/<int:pid>/delete')
@admin_required
def admin_product_delete(pid):
    product = db().execute('SELECT id FROM products WHERE id=?', (pid,)).fetchone()
    if not product:
        abort(404)

    references = any(
        db().execute(
            f'SELECT 1 FROM {table} WHERE product_id=? LIMIT 1',
            (pid,),
        ).fetchone()
        for table in ('cart_items', 'order_items', 'reviews')
    )
    if references:
        db().execute('UPDATE products SET active=0 WHERE id=?', (pid,))
        flash('Sản phẩm đã có dữ liệu liên quan nên được ngừng bán để giữ lịch sử.', 'warning')
    else:
        try:
            db().execute('DELETE FROM products WHERE id=?', (pid,))
            db().commit()
            flash('Đã xóa sản phẩm.', 'success')
            return redirect(url_for('admin'))
        except DB_INTEGRITY_ERROR_TYPES:
            db().rollback()
            db().execute('UPDATE products SET active=0 WHERE id=?', (pid,))
            flash('Sản phẩm vừa phát sinh dữ liệu liên quan nên đã được ngừng bán.', 'warning')
    db().commit()
    return redirect(url_for('admin'))

@app.post('/admin/order/<int:oid>/status')
@admin_required
def admin_order_status(oid):
    status = request.form.get('status')
    allowed = ['Chờ xác nhận', 'Đã xác nhận', 'Đang giao', 'Đã giao', 'Đã hủy']
    if status not in allowed: abort(400)
    row = db().execute('SELECT status FROM orders WHERE id=?', (oid,)).fetchone()
    if not row: abort(404)
    if row['status'] == 'Đã hủy' and status != 'Đã hủy':
        flash('Không thể mở lại đơn đã hủy.', 'danger')
    elif row['status'] == 'Đã giao' and status != 'Đã giao':
        flash('Không thể đổi trạng thái đơn đã giao.', 'danger')
    else:
        if status == 'Đã hủy' and row['status'] != 'Đã hủy':
            for item in db().execute('SELECT product_id, quantity FROM order_items WHERE order_id=?', (oid,)).fetchall():
                db().execute('UPDATE products SET stock=stock+? WHERE id=?', (item['quantity'], item['product_id']))
        db().execute('UPDATE orders SET status=? WHERE id=?', (status, oid))
        db().commit()
    return redirect(url_for('admin'))

def get_smart_bot_reply(question: str, catalog: list) -> str:
    lower = question.lower().strip()

    # 1. Greetings
    if any(w in lower for w in ['chào', 'hello', 'hi ', 'hey', 'alo', 'shop ơi', 'ad ơi', 'bot ơi', 'xin chào']):
        return (
            "Xin chào bạn! Mình là Tuấn Hùng AI - trợ lý hoa quả của Tuấn Hùng Fruit 🍃.\n\n"
            "Mình có thể hỗ trợ bạn:\n"
            "• Tra cứu giá cả và độ tươi ngon các loại trái cây\n"
            "• Tư vấn hoa quả tăng đề kháng, bồi bổ sức khỏe, ăn kiêng\n"
            "• Hướng dẫn cách thức mua hàng và nhận mã giảm giá **FRESH10** (-10%)\n\n"
            "Bạn đang muốn tìm loại hoa quả nào hôm nay ạ?"
        )

    # 2. Coupon / Discount
    if any(w in lower for w in ['mã', 'khuyến mãi', 'voucher', 'coupon', 'ưu đãi', 'giảm giá', 'sale']):
        return (
            "🎉 **Ưu đãi hấp dẫn đang áp dụng tại Tuấn Hùng Fruit:**\n\n"
            "• Mã giảm giá: **FRESH10**\n"
            "• Mức giảm: **10%** trên tổng giá trị đơn hàng\n"
            "• Cách dùng: Bạn chỉ cần nhập mã **FRESH10** vào ô 'Mã giảm giá' ở trang Thanh toán.\n\n"
            "Nhanh tay chọn những trái ngon tươi mát cho cả nhà nhé!"
        )

    # 3. How to order / payment / delivery
    if any(w in lower for w in ['đặt hàng', 'mua hàng', 'thanh toán', 'mua như thế nào', 'cách đặt', 'vận chuyển', 'giao hàng', 'ship', 'cod']):
        return (
            "📦 **Quy trình mua hàng cực kỳ dễ dàng tại Tuấn Hùng Fruit:**\n\n"
            "1. **Tài khoản**: Đăng nhập hoặc Đăng ký nhanh nếu chưa có tài khoản.\n"
            "2. **Chọn mua**: Vào mục 'Sản phẩm', chọn hoa quả yêu thích và bấm 'Thêm vào giỏ'.\n"
            "3. **Thanh toán**: Mở Giỏ hàng → 'Tiến hành thanh toán', điền người nhận, địa chỉ và mã **FRESH10**.\n"
            "4. **Nhận hàng COD**: Thanh toán tiền mặt khi nhận hàng. Bạn hoàn toàn được đồng kiểm hàng tươi trước khi nhận!"
        )

    # 4. Nutrition, Vitamin C, Immune
    if any(w in lower for w in ['vitamin', 'đề kháng', 'sức khỏe', 'bổ dưỡng', 'dinh dưỡng']):
        return (
            "🌿 **Tư vấn trái cây giàu vitamin & tăng cường sức khỏe:**\n\n"
            "• **Giàu Vitamin C nhất**: Cam vàng Úc (76.000đ/kg) và Kiwi xanh New Zealand (135.000đ/kg) giúp tăng cường sức đề kháng tối ưu.\n"
            "• **Chống lão hóa & đẹp da**: Nho xanh không hạt (159.000đ/kg) và Dâu tây Đà Lạt (119.000đ/hộp) chứa hàm lượng chất chống oxy hóa cao.\n"
            "• **Cung cấp năng lượng**: Chuối già Nam Mỹ (32.000đ/nải) giàu kali và khoáng chất tự nhiên.\n\n"
            "Bạn có muốn mình tư vấn thêm về sản phẩm cụ thể nào không?"
        )

    # 5. Diet, Weight Loss, Diabetes
    if any(w in lower for w in ['giảm cân', 'ăn kiêng', 'tiểu đường', 'eat clean', 'keto']):
        return (
            "🥗 **Gợi ý trái cây thanh mát cho chế độ ăn kiêng & giảm cân:**\n\n"
            "• **Thanh long ruột đỏ**: Giàu chất xơ, lượng calo thấp, hỗ trợ nhuận tràng và thanh lọc cơ thể.\n"
            "• **Táo Fuji Nhật Bản & Kiwi xanh**: Chỉ số đường huyết (GI) thấp, tạo cảm giác no lâu, tốt cho người kiểm soát cân nặng hoặc đường huyết.\n"
            "• **Dưa hấu ruột đỏ**: Nhiều nước, mát lạnh giải nhiệt, ít calo khi dùng lượng vừa phải.\n\n"
            "💡 Mẹo nhỏ: Nên dùng hoa quả tươi vào bữa phụ hoặc trước bữa ăn chính 30 phút."
        )

    # 6. Gift basket
    if any(w in lower for w in ['giỏ quà', 'tặng', 'biếu', 'quà tặng', 'thăm']):
        gift = next((p for p in catalog if 'giỏ quà' in p['name'].lower()), None)
        price_str = f"{gift['price']:,}đ" if gift else "399.000đ"
        return (
            f"🎁 **Giỏ quà trái cây cao cấp Tuấn Hùng Fruit ({price_str}/giỏ):**\n\n"
            "• Thành phần: Tuyển chọn các loại quả thượng hạng trong ngày (Táo Fuji, Nho xanh không hạt, Cam Úc, Kiwi, Lê Hàn Quốc).\n"
            "• Thiết kế: Giỏ mây hoặc khay gỗ sang trọng, bọc màng co và trang trí nơ lụa tỉ mỉ.\n"
            "• Rất phù hợp: Làm quà biếu lễ, thăm hỏi người thân, bạn bè hoặc đối tác.\n\n"
            "Bạn có thể vào mục 'Giỏ quà' để đặt ngay hôm nay nhé!"
        )

    # 7. Specific fruit lookup
    fruit_keywords = {
        'táo': 'Táo Fuji Nhật Bản',
        'cam': 'Cam vàng Úc',
        'nho': 'Nho xanh không hạt',
        'dâu': 'Dâu tây Đà Lạt',
        'xoài': 'Xoài cát Hòa Lộc',
        'dưa hấu': 'Dưa hấu ruột đỏ',
        'chuối': 'Chuối già Nam Mỹ',
        'dứa': 'Dứa mật',
        'kiwi': 'Kiwi xanh New Zealand',
        'thanh long': 'Thanh long ruột đỏ',
        'lê': 'Lê Hàn Quốc'
    }
    for kw, fname in fruit_keywords.items():
        if kw in lower:
            matched = next((p for p in catalog if fname.lower() in p['name'].lower() or kw in p['name'].lower()), None)
            if matched:
                stock_status = f"✓ Còn {matched['stock']} {matched['unit']}" if matched['stock'] > 0 else "Hết hàng"
                return (
                    f"🍎 **{matched['name']}**:\n\n"
                    f"• Giá niêm yết: **{matched['price']:,} VND / {matched['unit']}**\n"
                    f"• Tình trạng kho: {stock_status}\n"
                    f"• Cam kết: Tươi ngon 100%, nhập mới đều đặn, tuyển chọn kỹ càng.\n\n"
                    f"Bạn có thể thêm trực tiếp vào giỏ hàng từ trang 'Sản phẩm'!"
                )

    # 8. All products / price list
    if any(w in lower for w in ['giá', 'bảng giá', 'bao nhiêu', 'menu', 'sản phẩm', 'hoa quả', 'trái cây', 'danh mục', 'có gì', 'danh sách']):
        lines = [f"• **{p['name']}**: {p['price']:,}đ/{p['unit']} ({'Còn ' + str(p['stock']) if p['stock'] > 0 else 'Hết hàng'})" for p in catalog[:12]]
        return (
            "🛒 **Danh mục & Bảng giá trái cây tươi tại Tuấn Hùng Fruit:**\n\n"
            + "\n".join(lines)
            + "\n\n💡 Nhập mã **FRESH10** khi thanh toán để nhận ngay giảm giá 10%!"
        )

    # 9. Storage / Preservation
    if any(w in lower for w in ['bảo quản', 'để được bao lâu', 'tủ lạnh', 'giữ tươi']):
        return (
            "❄️ **Bí quyết bảo quản hoa quả luôn tươi ngon:**\n\n"
            "• Bảo quản ở ngăn mát tủ lạnh ở nhiệt độ 4 - 8°C.\n"
            "• Không nên rửa trước khi cất tủ lạnh, chỉ rửa ngay trước khi dùng.\n"
            "• Dùng hộp kín hoặc túi có lỗ thông khí để quả giữ được độ ẩm tự nhiên.\n"
            "• Riêng chuối nên treo ở nơi thoáng mát bên ngoài tủ lạnh."
        )

    # Default fallback
    return (
        "Cảm ơn bạn đã nhắn tin cho Tuấn Hùng Fruit! 🍃\n\n"
        "Bạn có thể hỏi mình về:\n"
        "• Bảng giá các loại quả: Táo, Cam, Nho, Dâu tây, Xoài, Kiwi, Dưa hấu...\n"
        "• Mã giảm giá ưu đãi (**FRESH10** - giảm 10%)\n"
        "• Hướng dẫn cách thức đặt mua và giao nhận tận nơi\n"
        "• Tư vấn trái cây theo nhu cầu sức khỏe và dinh dưỡng\n\n"
        "Hãy nhắn cho mình loại trái cây bạn đang cần tìm nhé!"
    )

@app.post('/api/chat')
def chat():
    data = request.get_json(silent=True) or {}
    question = str(data.get('message', '')).strip()[:1000]
    history = data.get('history', [])
    if not question:
        return jsonify(error='Bạn chưa nhập câu hỏi.'), 400
    if not isinstance(history, list):
        history = []
        
    catalog = db().execute('SELECT name, price, unit, stock FROM products WHERE active=1 ORDER BY id LIMIT 40').fetchall()
    products_info = '\n'.join(f"- {p['name']}: {p['price']:,} VND/{p['unit']}, còn {p['stock']}" for p in catalog)
    
    key = os.getenv('OPENAI_API_KEY', '').strip()
    openai_error = None
    
    if key:
        try:
            
            from openai import OpenAI
            client = OpenAI(api_key=key, timeout=12.0)
            model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini').strip()
            if model in ('gpt-4.1-mini', 'gpt-4.1', 'gpt-5.5', ''):
                model = 'gpt-4o-mini'
                
            clean_history = []
            for msg in history[-8:]:
                if isinstance(msg, dict) and msg.get('role') in ('user', 'assistant') and isinstance(msg.get('content'), str):
                    clean_history.append({'role': msg['role'], 'content': msg['content'][:1000]})
                    
            system_prompt = (
                "Bạn là Tuấn Hùng AI, trợ lý ảo thông minh của cửa hàng hoa quả Tuấn Hùng Fruit tại Việt Nam. "
                "Hãy trả lời bằng tiếng Việt tự nhiên, thân thiện, súc tích. "
                "Tư vấn hoa quả, dinh dưỡng, cách bảo quản và hướng dẫn đặt mua. "
                "Thông tin sản phẩm đang có tại cửa hàng:\n"
                + products_info
                + "\nChính sách cửa hàng: Đặt hàng trực tiếp trên website, thanh toán khi nhận hàng (COD), "
                "được đồng kiểm hàng trước khi thanh toán, đổi trả trong 24h nếu dập hỏng. "
                "Mã ưu đãi giảm giá 10%: FRESH10."
            )
            
            completion = client.chat.completions.create(
                model=model,
                messages=[{'role': 'system', 'content': system_prompt}] + clean_history + [{'role': 'user', 'content': question}],
                max_tokens=600,
                temperature=0.7
            )
            reply = completion.choices[0].message.content.strip()
            if reply:
                return jsonify(reply=reply, mode='ai')
        except Exception as exc:
            openai_error = str(exc)
            app.logger.warning(f"OpenAI API call failed, falling back to smart bot: {exc}")

    # Fallback to intelligent rule-based & catalog-aware bot
    smart_reply = get_smart_bot_reply(question, catalog)
    
    note = ""
    if openai_error and ('insufficient_quota' in openai_error or '429' in openai_error or 'credit_balance_exhausted' in openai_error):
        note = "\n\n*(Ghi chú: Khóa OPENAI_API_KEY trong .env hiện đã dùng hết hạn mức credit quota trên OpenAI. Trợ lý thông minh Tuấn Hùng AI đang trả lời tự động cho bạn!)*"
    elif openai_error and ('401' in openai_error or 'Incorrect API key' in openai_error):
        note = "\n\n*(Ghi chú: OPENAI_API_KEY chưa chính xác. Trợ lý thông minh Tuấn Hùng AI đang trả lời tự động.)*"

    return jsonify(reply=smart_reply + note, mode='smart_bot')

@app.get('/health')
def health():
    return jsonify(status='ok')

if os.getenv('AUTO_INIT_DB', '1') == '1':
    with app.app_context():
        init_db()

if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_DEBUG', '0') == '1', port=int(os.getenv('PORT', '5000')))

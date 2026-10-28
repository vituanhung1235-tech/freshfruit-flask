import builtins
import hashlib
import io
import os
import re
import runpy
import tempfile
import unittest
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash
from PIL import Image

# Tests must never connect to the database configured in the developer's .env.
os.environ['DATABASE_URL'] = ''
os.environ['POSTGRES_URL'] = ''

from database import POSTGRES_SCHEMA, adapt_postgres_sql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = tempfile.TemporaryDirectory()
TEST_DATABASE = Path(TEST_DATA.name) / 'freshfruit-test.db'

_environment = {
    key: os.environ.get(key)
    for key in ('AUTO_INIT_DB', 'DATABASE_PATH')
}
os.environ['AUTO_INIT_DB'] = '0'
os.environ['DATABASE_PATH'] = str(TEST_DATABASE)
import app as app_module
for _key, _value in _environment.items():
    if _value is None:
        os.environ.pop(_key, None)
    else:
        os.environ[_key] = _value

app_module.app.config.update(TESTING=True, SECRET_KEY='test-secret')


class ApplicationFlowTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        TEST_DATA.cleanup()

    def setUp(self):
        TEST_DATABASE.unlink(missing_ok=True)
        app_module.init_db()
        self.client = app_module.app.test_client()

    def csrf_token(self, path='/login'):
        self.client.get(path)
        with self.client.session_transaction() as session:
            return session['csrf']

    def test_public_pages_and_csrf_protection(self):
        self.assertEqual(self.client.get('/health').json, {'status': 'ok'})
        home = self.client.get('/')
        self.assertEqual(home.status_code, 200)
        self.assertIn(b'id="tuanhung-slider"', home.data)
        self.assertIn(b'id="slider-next"', home.data)
        self.assertIn(b'id="chat-form"', home.data)
        javascript = self.client.get('/static/app.js')
        stylesheet = self.client.get('/static/style.css')
        self.assertEqual(javascript.status_code, 200)
        self.assertEqual(stylesheet.status_code, 200)
        javascript.close()
        stylesheet.close()
        self.assertEqual(self.client.get('/products').status_code, 200)
        self.assertEqual(self.client.get('/cart').status_code, 302)
        self.assertEqual(self.client.post('/login').status_code, 400)
        for image in ('fruit_shelf.jpg', 'store_display.jpg'):
            response = self.client.get(f'/static/images/{image}')
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_postgres_sql_adapter_preserves_application_query_features(self):
        self.assertEqual(
            adapt_postgres_sql('SELECT id FROM users WHERE email=?'),
            'SELECT id FROM users WHERE email=%s',
        )
        self.assertEqual(
            adapt_postgres_sql(
                "INSERT OR IGNORE INTO coupons(code, discount_percent) VALUES('FRESH10', 10)"
            ),
            "INSERT INTO coupons(code, discount_percent) VALUES('FRESH10', 10) "
            'ON CONFLICT DO NOTHING',
        )
        self.assertEqual(
            adapt_postgres_sql(
                'INSERT INTO users(name, email) VALUES(?,?)'
            ),
            'INSERT INTO users(name, email) VALUES(%s,%s) RETURNING id',
        )
        self.assertEqual(
            adapt_postgres_sql(
                'INSERT INTO cart_items(user_id, product_id, quantity) '
                'VALUES(?,?,?) ON CONFLICT(user_id, product_id) '
                'DO UPDATE SET quantity=cart_items.quantity+excluded.quantity'
            ),
            'INSERT INTO cart_items(user_id, product_id, quantity) '
            'VALUES(%s,%s,%s) ON CONFLICT(user_id, product_id) '
            'DO UPDATE SET quantity=cart_items.quantity+excluded.quantity RETURNING id',
        )
        self.assertTrue(any('CREATE TABLE IF NOT EXISTS orders' in sql for sql in POSTGRES_SCHEMA))

    def test_chat_widget_endpoint_and_empty_input_validation(self):
        token = self.csrf_token('/')
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            response = self.client.post(
                '/api/chat',
                json={'message': 'giá dưa hấu bao nhiêu', 'history': []},
                headers={'X-CSRF-Token': token},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['mode'], 'smart_bot')
        self.assertIn('Dưa hấu ruột đỏ', response.json['reply'])

        token = self.csrf_token('/')
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            response = self.client.post(
                '/api/chat',
                json={'message': '   '},
                headers={'X-CSRF-Token': token},
            )
        self.assertEqual(response.status_code, 400)

    def test_stale_session_cannot_add_product_to_cart(self):
        self.client.get('/product/12')
        with self.client.session_transaction() as session:
            session.update(
                user_id=9999,
                name='Expired User',
                role='customer',
                csrf='stale-session-test-token',
            )

        response = self.client.post(
            '/product/12',
            data={'_csrf': 'stale-session-test-token', 'quantity': '1'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/login')
        with self.client.session_transaction() as session:
            self.assertNotIn('user_id', session)
            self.assertTrue(session.get('_flashes'))
        with app_module.app.app_context():
            cart_items = app_module.db().execute(
                'SELECT COUNT(*) FROM cart_items'
            ).fetchone()[0]
        self.assertEqual(cart_items, 0)

    def test_session_role_is_refreshed_from_database(self):
        admin_id = self.create_admin()
        with self.client.session_transaction() as session:
            session.update(
                user_id=admin_id,
                email='admin@example.com',
                name='Test Admin',
                role='admin',
            )
        with app_module.app.app_context():
            app_module.db().execute(
                'UPDATE users SET role=? WHERE id=?',
                ('customer', admin_id),
            )
            app_module.db().commit()

        response = self.client.get('/admin')

        self.assertEqual(response.status_code, 403)
        with self.client.session_transaction() as session:
            self.assertEqual(session['role'], 'customer')

    def test_session_follows_email_when_database_instance_has_different_user_ids(self):
        self.create_admin()
        csrf = 'cross-instance-admin-session-token'
        with self.client.session_transaction() as session:
            session.update(
                user_id=1,
                email='admin@example.com',
                name='Test Admin',
                role='admin',
                csrf=csrf,
            )

        other_database = Path(TEST_DATA.name) / 'other-instance.db'
        original_database = app_module.DB_PATH
        try:
            app_module.DB_PATH = str(other_database)
            with patch.dict(
                os.environ,
                {'ADMIN_EMAIL': '', 'ADMIN_PASSWORD': ''},
            ):
                app_module.init_db()
            with app_module.app.app_context():
                app_module.db().execute(
                    'INSERT INTO users(name, email, password_hash) VALUES(?,?,?)',
                    ('Other customer', 'other@example.com', 'unused'),
                )
                app_module.db().commit()
            with patch.dict(
                os.environ,
                {
                    'ADMIN_EMAIL': 'admin@example.com',
                    'ADMIN_PASSWORD': 'a-long-test-admin-password',
                    'ADMIN_NAME': 'Test Admin',
                },
            ):
                app_module.init_db()
                response = self.client.get('/admin')
        finally:
            app_module.DB_PATH = original_database

        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(session['user_id'], 2)
            self.assertEqual(session['email'], 'admin@example.com')

    def test_environment_admin_is_seeded_and_login_works(self):
        with patch.dict(
            os.environ,
            {
                'ADMIN_EMAIL': 'fixed-admin@example.com',
                'ADMIN_PASSWORD': 'a-long-test-admin-password',
                'ADMIN_NAME': 'Fixed Test Admin',
            },
        ):
            app_module.init_db()
            with app_module.app.app_context():
                admin = app_module.db().execute(
                    'SELECT id, name, role, password_hash FROM users WHERE email=?',
                    ('fixed-admin@example.com',),
                ).fetchone()
                user_count = app_module.db().execute(
                    'SELECT COUNT(*) FROM users WHERE email=?',
                    ('fixed-admin@example.com',),
                ).fetchone()[0]
            token = self.csrf_token()
            response = self.client.post(
                '/login',
                data={
                    '_csrf': token,
                    'email': 'FIXED-ADMIN@EXAMPLE.COM',
                    'password': 'a-long-test-admin-password',
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/admin')
        self.assertEqual(admin['name'], 'Fixed Test Admin')
        self.assertEqual(admin['role'], 'admin')
        self.assertEqual(user_count, 1)

    def test_vercel_requires_stable_session_key_and_shared_database(self):
        environment = os.environ.copy()
        environment.update(VERCEL='1', AUTO_INIT_DB='0', DATABASE_URL='')
        for key in ('SECRET_KEY', 'ADMIN_EMAIL', 'ADMIN_PASSWORD', 'ADMIN_NAME'):
            environment.pop(key, None)
        environment['SECRET_KEY'] = ''

        missing_key = subprocess.run(
            [
                sys.executable,
                '-c',
                'import app; print(bool(app.app.config["SECRET_KEY"]))',
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(missing_key.returncode, 0)
        self.assertIn('SECRET_KEY must be configured', missing_key.stderr)

        environment['SECRET_KEY'] = 'stable-vercel-test-key'
        missing_database = subprocess.run(
            [sys.executable, '-c', 'import app'],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(missing_database.returncode, 0)
        self.assertIn('DATABASE_URL must be configured', missing_database.stderr)

        environment['DATABASE_URL'] = 'postgresql://test:test@localhost/test'
        configured_key = subprocess.run(
            [
                sys.executable,
                '-c',
                'import app; print(app.app.config["SECRET_KEY"])',
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn('stable-vercel-test-key', configured_key.stdout)

    def test_missing_admin_environment_skips_bootstrap_without_crashing(self):
        with patch.dict(
            os.environ,
            {'ADMIN_EMAIL': '', 'ADMIN_PASSWORD': ''},
        ):
            app_module.init_db()

        with app_module.app.app_context():
            admin_count = app_module.db().execute(
                "SELECT COUNT(*) FROM users WHERE role='admin'"
            ).fetchone()[0]
        self.assertEqual(admin_count, 0)

    def test_session_is_invalidated_if_database_reuses_user_id(self):
        self.create_admin()
        csrf = 'reused-user-id-test-token'
        with self.client.session_transaction() as session:
            session.update(
                user_id=1,
                email='admin@example.com',
                name='Test Admin',
                role='admin',
                csrf=csrf,
            )
        with app_module.app.app_context():
            app_module.db().execute('DELETE FROM users WHERE id=1')
            app_module.db().execute(
                'INSERT INTO users(id, name, email, password_hash, role) '
                'VALUES(1,?,?,?,?)',
                ('Different User', 'other@example.com', 'unused', 'customer'),
            )
            app_module.db().commit()

        response = self.client.post(
            '/product/12',
            data={'_csrf': csrf, 'quantity': '1'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/login')
        with self.client.session_transaction() as session:
            self.assertNotIn('user_id', session)

    def test_legacy_admin_session_can_add_product_to_cart(self):
        admin_id = self.create_admin()
        csrf = 'legacy-session-test-token'
        with self.client.session_transaction() as session:
            session.update(
                user_id=admin_id,
                name='Test Admin',
                role='admin',
                csrf=csrf,
            )

        response = self.client.post(
            '/product/12',
            data={'_csrf': csrf, 'quantity': '1'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/cart')
        with self.client.session_transaction() as session:
            self.assertEqual(session['email'], 'admin@example.com')
        with app_module.app.app_context():
            cart_item = app_module.db().execute(
                'SELECT quantity FROM cart_items WHERE user_id=? AND product_id=?',
                (admin_id, 12),
            ).fetchone()
        self.assertEqual(cart_item['quantity'], 1)

    def test_customer_registration_login_and_order_checkout(self):
        token = self.csrf_token('/register')
        response = self.client.post(
            '/register',
            data={
                '_csrf': token,
                'name': 'Test Customer',
                'email': 'Customer@Example.com',
                'password': 'customer-password',
                'confirm': 'customer-password',
            },
        )
        self.assertEqual(response.status_code, 302)

        token = self.csrf_token()
        self.client.post('/logout', data={'_csrf': token})
        token = self.csrf_token()
        response = self.client.post(
            '/login',
            data={
                '_csrf': token,
                'email': 'CUSTOMER@EXAMPLE.COM',
                'password': 'customer-password',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')
        self.assertEqual(self.client.get('/profile').status_code, 200)

        token = self.csrf_token('/product/1')
        response = self.client.post('/product/1', data={'_csrf': token, 'quantity': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/cart').status_code, 200)

        token = self.csrf_token('/checkout')
        response = self.client.post(
            '/checkout',
            data={
                '_csrf': token,
                'recipient': 'Test Customer',
                'phone': '0123456789',
                'address': '123 Example Street',
                'coupon': 'fresh10',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertRegex(response.headers['Location'], r'^/orders/\d+$')
        with app_module.app.app_context():
            order = app_module.db().execute('SELECT total FROM orders').fetchone()
            stock = app_module.db().execute(
                'SELECT stock FROM products WHERE id=1'
            ).fetchone()
        self.assertEqual(order['total'], 80100)
        self.assertEqual(stock['stock'], 39)
        self.assertEqual(self.client.get('/orders').status_code, 200)

    def test_admin_creation_and_login(self):
        for name, password in (
            ('First Admin', 'first-password'),
            ('Updated Admin', 'updated-password'),
        ):
            output = io.StringIO()
            with (
                patch.object(
                    builtins, 'input', side_effect=['Admin@Example.com', name]
                ),
                patch('getpass.getpass', return_value=password),
                redirect_stdout(output),
            ):
                runpy.run_path(
                    str(PROJECT_ROOT / 'create_admin.py'),
                    run_name='__main__',
                )
            self.assertIn('Đã tạo/cập nhật Admin', output.getvalue())

        token = self.csrf_token()
        response = self.client.post(
            '/login',
            data={
                '_csrf': token,
                'email': 'ADMIN@EXAMPLE.COM',
                'password': 'wrong-password',
            },
        )
        self.assertEqual(response.status_code, 200)

        token = self.csrf_token()
        response = self.client.post(
            '/login',
            data={
                '_csrf': token,
                'email': 'ADMIN@EXAMPLE.COM',
                'password': 'updated-password',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/admin')
        self.assertEqual(self.client.get('/admin').status_code, 200)

        token = self.csrf_token()
        response = self.client.post(
            '/admin/product',
            data={
                '_csrf': token,
                'name': 'Test Fruit',
                'category': 'Việt Nam',
                'unit': 'kg',
                'price': '50000',
                'stock': '10',
            },
        )
        self.assertEqual(response.status_code, 302)
        with app_module.app.app_context():
            count = app_module.db().execute(
                'SELECT COUNT(*) FROM users WHERE email=? AND role=?',
                ('admin@example.com', 'admin'),
            ).fetchone()[0]
            product = app_module.db().execute(
                'SELECT id FROM products WHERE name=?', ('Test Fruit',)
            ).fetchone()
        self.assertEqual(count, 1)
        self.assertIsNotNone(product)
        self.assertEqual(
            response.headers['Location'],
            f'/admin#product-{product["id"]}',
        )

    def test_admin_deletes_unreferenced_products_and_archives_referenced_products(self):
        admin_id = self.create_admin()
        with app_module.app.app_context():
            removable_id = app_module.db().execute(
                'INSERT INTO products(name, category, price, unit, description, emoji, stock) '
                'VALUES(?,?,?,?,?,?,?)',
                ('Removable Fruit', 'Test', 1000, 'kg', 'Test', '🍎', 5),
            ).lastrowid
            archived_id = app_module.db().execute(
                'INSERT INTO products(name, category, price, unit, description, emoji, stock) '
                'VALUES(?,?,?,?,?,?,?)',
                ('Archived Fruit', 'Test', 1000, 'kg', 'Test', '🍎', 5),
            ).lastrowid
            app_module.db().execute(
                'INSERT INTO cart_items(user_id, product_id, quantity) VALUES(?,?,?)',
                (admin_id, archived_id, 1),
            )
            app_module.db().commit()

        with self.client.session_transaction() as session:
            session.update(user_id=admin_id, email='admin@example.com')

        token = self.csrf_token('/admin')
        response = self.client.post(
            f'/admin/product/{removable_id}/delete',
            data={'_csrf': token},
        )
        self.assertEqual(response.status_code, 302)

        token = self.csrf_token('/admin')
        response = self.client.post(
            f'/admin/product/{archived_id}/delete',
            data={'_csrf': token},
        )
        self.assertEqual(response.status_code, 302)
        with app_module.app.app_context():
            removed = app_module.db().execute(
                'SELECT id FROM products WHERE id=?', (removable_id,)
            ).fetchone()
            archived = app_module.db().execute(
                'SELECT active FROM products WHERE id=?', (archived_id,)
            ).fetchone()
            cart_item = app_module.db().execute(
                'SELECT quantity FROM cart_items WHERE product_id=?', (archived_id,)
            ).fetchone()
        self.assertIsNone(removed)
        self.assertEqual(archived['active'], 0)
        self.assertEqual(cart_item['quantity'], 1)

    def test_admin_uploads_product_image_and_preserves_it_on_edit(self):
        admin_id = self.create_admin()
        with self.client.session_transaction() as session:
            session.update(user_id=admin_id, email='admin@example.com')

        image_buffer = io.BytesIO()
        Image.new('RGB', (32, 24), (35, 120, 80)).save(image_buffer, format='PNG')
        image_buffer.seek(0)
        token = self.csrf_token('/admin')
        response = self.client.post(
            '/admin/product',
            data={
                '_csrf': token,
                'name': 'Uploaded Fruit',
                'category': 'Test',
                'unit': 'kg',
                'price': '12000',
                'stock': '8',
                'image': (image_buffer, 'fruit.png'),
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 302)
        with app_module.app.app_context():
            product = app_module.db().execute(
                'SELECT id, image FROM products WHERE name=?', ('Uploaded Fruit',)
            ).fetchone()
        self.assertTrue(product['image'].startswith('data:image/png;base64,'))

        image_response = self.client.get(f"/product/{product['id']}/image")
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.mimetype, 'image/png')
        with Image.open(io.BytesIO(image_response.data)) as uploaded:
            self.assertEqual(uploaded.size, (32, 24))

        token = self.csrf_token('/admin')
        response = self.client.post(
            '/admin/product',
            data={
                '_csrf': token,
                'id': str(product['id']),
                'name': 'Uploaded Fruit Updated',
                'category': 'Test',
                'unit': 'kg',
                'price': '13000',
                'stock': '7',
            },
        )
        self.assertEqual(response.status_code, 302)
        with app_module.app.app_context():
            updated = app_module.db().execute(
                'SELECT image FROM products WHERE id=?', (product['id'],)
            ).fetchone()
        self.assertEqual(updated['image'], product['image'])

    def test_admin_rejects_non_image_upload(self):
        admin_id = self.create_admin()
        with self.client.session_transaction() as session:
            session.update(user_id=admin_id, email='admin@example.com')

        token = self.csrf_token('/admin')
        response = self.client.post(
            '/admin/product',
            data={
                '_csrf': token,
                'name': 'Fake Image Fruit',
                'category': 'Test',
                'unit': 'kg',
                'price': '12000',
                'stock': '8',
                'image': (io.BytesIO(b'not an image'), 'fake.png'),
            },
            content_type='multipart/form-data',
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('Tệp tải lên không phải ảnh hợp lệ.', response.get_data(as_text=True))
        with app_module.app.app_context():
            product = app_module.db().execute(
                'SELECT id FROM products WHERE name=?', ('Fake Image Fruit',)
            ).fetchone()
        self.assertIsNone(product)

    def test_cashier_pos_sale_calculates_total_and_deducts_stock(self):
        with app_module.app.app_context():
            cashier_id = app_module.db().execute(
                'INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',
                ('Test Cashier', 'cashier@example.com', generate_password_hash('cashier-password'), 'cashier'),
            ).lastrowid
            initial_stock = app_module.db().execute(
                'SELECT stock,price FROM products WHERE id=1'
            ).fetchone()
            app_module.db().commit()

        with self.client.session_transaction() as session:
            session.update(user_id=cashier_id, email='cashier@example.com')
        self.assertEqual(self.client.get('/staff/pos').status_code, 200)
        token = self.csrf_token('/staff/pos')
        response = self.client.post(
            '/staff/pos',
            data={
                '_csrf': token,
                'product_id': ['1', '1'],
                'quantity': ['1', '2'],
                'payment_method': 'Tiền mặt',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('sale=', response.headers['Location'])
        with app_module.app.app_context():
            sale = app_module.db().execute(
                'SELECT id,total,payment_method FROM pos_sales WHERE cashier_user_id=?',
                (cashier_id,),
            ).fetchone()
            item = app_module.db().execute(
                'SELECT quantity,unit_price FROM pos_sale_items WHERE sale_id=?',
                (sale['id'],),
            ).fetchone()
            final_stock = app_module.db().execute(
                'SELECT stock FROM products WHERE id=1'
            ).fetchone()['stock']
        self.assertEqual(sale['total'], initial_stock['price'] * 3)
        self.assertEqual(sale['payment_method'], 'Tiền mặt')
        self.assertEqual(item['quantity'], 3)
        self.assertEqual(final_stock, initial_stock['stock'] - 3)

    def test_cart_auto_updates_multiple_quantities_and_rolls_back_oversell(self):
        user_id = self.create_admin()
        with app_module.app.app_context():
            app_module.db().execute(
                'INSERT INTO cart_items(user_id,product_id,quantity) VALUES(?,?,?)',
                (user_id, 1, 1),
            )
            app_module.db().execute(
                'INSERT INTO cart_items(user_id,product_id,quantity) VALUES(?,?,?)',
                (user_id, 2, 1),
            )
            app_module.db().commit()
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, email='admin@example.com')

        page = self.client.get('/cart')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'data-cart-quantity', page.data)
        self.assertIn(b'id="cart-subtotal"', page.data)
        token = self.csrf_token('/cart')
        response = self.client.post(
            '/cart/update',
            data={'_csrf': token, 'quantity_1': '3', 'quantity_2': '0'},
        )
        self.assertEqual(response.status_code, 302)
        with app_module.app.app_context():
            rows = app_module.db().execute(
                'SELECT product_id,quantity FROM cart_items WHERE user_id=? ORDER BY product_id',
                (user_id,),
            ).fetchall()
        self.assertEqual([(row['product_id'], row['quantity']) for row in rows], [(1, 3)])

        token = self.csrf_token('/cart')
        response = self.client.post(
            '/cart/update',
            data={'_csrf': token, 'quantity_1': '4', 'quantity_2': '999'},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('Số lượng vượt tồn kho hoặc sản phẩm đã ngừng bán.', response.get_data(as_text=True))
        with app_module.app.app_context():
            quantity = app_module.db().execute(
                'SELECT quantity FROM cart_items WHERE user_id=? AND product_id=1',
                (user_id,),
            ).fetchone()['quantity']
        self.assertEqual(quantity, 3)

    def test_staff_roles_have_separate_workspaces(self):
        role_users = {}
        with app_module.app.app_context():
            for role in ('manager', 'cashier', 'packer'):
                role_users[role] = app_module.db().execute(
                    'INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',
                    (role.title(), f'{role}@example.com', generate_password_hash('staff-password'), role),
                ).lastrowid
            app_module.db().commit()

        expected = {
            'manager': {'/admin': 200, '/admin/staff': 403, '/staff/pos': 200, '/staff/packing': 200},
            'cashier': {'/admin': 403, '/admin/staff': 403, '/staff/pos': 200, '/staff/packing': 403},
            'packer': {'/admin': 403, '/admin/staff': 403, '/staff/pos': 403, '/staff/packing': 200},
        }
        for role, checks in expected.items():
            with self.client.session_transaction() as session:
                session.clear()
                session.update(user_id=role_users[role], email=f'{role}@example.com')
            for path, status in checks.items():
                self.assertEqual(self.client.get(path).status_code, status, f'{role} -> {path}')

    def test_admin_can_create_staff_with_role(self):
        admin_id = self.create_admin()
        with self.client.session_transaction() as session:
            session.update(user_id=admin_id, email='admin@example.com')
        token = self.csrf_token('/admin/staff')
        response = self.client.post(
            '/admin/staff',
            data={
                '_csrf': token,
                'name': 'Packing Staff',
                'email': 'packing@example.com',
                'password': 'long-staff-password',
                'role': 'packer',
            },
        )
        self.assertEqual(response.status_code, 302)
        with app_module.app.app_context():
            staff = app_module.db().execute(
                'SELECT role,password_hash FROM users WHERE email=?',
                ('packing@example.com',),
            ).fetchone()
        self.assertEqual(staff['role'], 'packer')
        self.assertNotEqual(staff['password_hash'], 'long-staff-password')

    def test_confirmed_online_orders_move_through_packing_stages(self):
        manager_id = self.create_admin()
        with app_module.app.app_context():
            customer_id = app_module.db().execute(
                'INSERT INTO users(name,email,password_hash) VALUES(?,?,?)',
                ('Order Customer', 'order-customer@example.com', 'unused'),
            ).lastrowid
            packer_id = app_module.db().execute(
                'INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',
                ('Order Packer', 'order-packer@example.com', 'unused', 'packer'),
            ).lastrowid
            order_id = app_module.db().execute(
                'INSERT INTO orders(user_id,recipient,phone,address,total,created_at) VALUES(?,?,?,?,?,?)',
                (customer_id, 'Order Customer', '0123456789', 'Test address', 89000, '2026-10-28T10:00:00'),
            ).lastrowid
            app_module.db().execute(
                'INSERT INTO order_items(order_id,product_id,product_name,unit_price,quantity) VALUES(?,?,?,?,?)',
                (order_id, 1, 'Táo Fuji Nhật Bản', 89000, 1),
            )
            app_module.db().commit()

        with self.client.session_transaction() as session:
            session.update(user_id=manager_id, email='admin@example.com')
        token = self.csrf_token('/admin')
        confirmed = self.client.post(
            f'/admin/order/{order_id}/status',
            data={'_csrf': token, 'status': 'Đã xác nhận'},
        )
        self.assertEqual(confirmed.status_code, 302)

        with self.client.session_transaction() as session:
            session.update(user_id=packer_id, email='order-packer@example.com')
        queue = self.client.get('/staff/packing')
        self.assertEqual(queue.status_code, 200)
        self.assertIn(f'DH-{order_id:06d}', queue.get_data(as_text=True))

        token = self.csrf_token('/staff/packing')
        skipped = self.client.post(
            f'/staff/packing/{order_id}/status',
            data={'_csrf': token, 'status': 'Sẵn sàng giao'},
        )
        self.assertEqual(skipped.status_code, 400)
        for status in ('Đang đóng gói', 'Sẵn sàng giao', 'Đang giao'):
            token = self.csrf_token('/staff/packing')
            response = self.client.post(
                f'/staff/packing/{order_id}/status',
                data={'_csrf': token, 'status': status},
            )
            self.assertEqual(response.status_code, 302)
        with app_module.app.app_context():
            final_status = app_module.db().execute(
                'SELECT status FROM orders WHERE id=?', (order_id,)
            ).fetchone()['status']
        self.assertEqual(final_status, 'Đang giao')

    def create_admin(self, email='admin@example.com'):
        with app_module.app.app_context():
            cursor = app_module.db().execute(
                'INSERT INTO users(name, email, password_hash, role) VALUES(?,?,?,?)',
                ('Test Admin', email, generate_password_hash('old-password'), 'admin'),
            )
            app_module.db().commit()
            return cursor.lastrowid

    def smtp_settings(self):
        return patch.dict(
            os.environ,
            {
                'APP_BASE_URL': 'https://fruit.example',
                'SMTP_HOST': 'smtp.example',
                'SMTP_PORT': '587',
                'SMTP_USERNAME': 'sender@example.com',
                'SMTP_PASSWORD': 'test-mail-password',
                'MAIL_FROM': 'sender@example.com',
            },
        )

    def test_admin_can_reset_password_from_single_use_email_link(self):
        self.create_admin()
        token = self.csrf_token('/forgot-password')
        smtp_connection = unittest.mock.MagicMock()
        with self.smtp_settings(), patch(
            'password_reset.smtplib.SMTP'
        ) as smtp_constructor:
            smtp_constructor.return_value.__enter__.return_value = smtp_connection
            response = self.client.post(
                '/forgot-password',
                data={'_csrf': token, 'email': 'ADMIN@EXAMPLE.COM'},
            )

        self.assertEqual(response.status_code, 302)
        smtp_connection.starttls.assert_called_once()
        smtp_connection.login.assert_called_once_with(
            'sender@example.com', 'test-mail-password'
        )
        message = smtp_connection.send_message.call_args.args[0]
        self.assertEqual(message['To'], 'admin@example.com')
        match = re.search(r'https://fruit\.example/reset-password/(\S+)', message.get_content())
        self.assertIsNotNone(match)
        reset_token = match.group(1)
        token_hash = hashlib.sha256(reset_token.encode()).hexdigest()
        with app_module.app.app_context():
            stored_token = app_module.db().execute(
                'SELECT token_hash FROM password_reset_tokens'
            ).fetchone()
        self.assertEqual(stored_token['token_hash'], token_hash)
        self.assertNotEqual(stored_token['token_hash'], reset_token)

        response = self.client.get(f'/reset-password/{reset_token}')
        self.assertEqual(response.status_code, 200)
        token = self.csrf_token(f'/reset-password/{reset_token}')
        response = self.client.post(
            f'/reset-password/{reset_token}',
            data={
                '_csrf': token,
                'password': 'new-admin-password',
                'confirm': 'different-password',
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(f'/reset-password/{reset_token}').status_code, 200)
        token = self.csrf_token(f'/reset-password/{reset_token}')
        response = self.client.post(
            f'/reset-password/{reset_token}',
            data={
                '_csrf': token,
                'password': 'new-admin-password',
                'confirm': 'new-admin-password',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/login')
        with app_module.app.app_context():
            admin = app_module.db().execute(
                'SELECT password_hash FROM users WHERE email=?',
                ('admin@example.com',),
            ).fetchone()
        from werkzeug.security import check_password_hash

        self.assertTrue(check_password_hash(admin['password_hash'], 'new-admin-password'))
        self.assertEqual(self.client.get(f'/reset-password/{reset_token}').status_code, 400)

    def test_customer_email_does_not_send_admin_reset_message(self):
        with app_module.app.app_context():
            app_module.db().execute(
                'INSERT INTO users(name, email, password_hash) VALUES(?,?,?)',
                ('Customer', 'customer@example.com', generate_password_hash('password')),
            )
            app_module.db().commit()

        token = self.csrf_token('/forgot-password')
        with self.smtp_settings(), patch('password_reset.smtplib.SMTP') as smtp:
            response = self.client.post(
                '/forgot-password',
                data={'_csrf': token, 'email': 'customer@example.com'},
            )
        self.assertEqual(response.status_code, 302)
        smtp.assert_not_called()
        with app_module.app.app_context():
            tokens = app_module.db().execute(
                'SELECT COUNT(*) FROM password_reset_tokens'
            ).fetchone()[0]
        self.assertEqual(tokens, 0)

    def test_mail_configuration_failure_is_reported_and_token_removed(self):
        self.create_admin()
        token = self.csrf_token('/forgot-password')
        smtp_connection = unittest.mock.MagicMock()
        smtp_connection.starttls.side_effect = OSError('SMTP unavailable')
        with self.smtp_settings(), patch(
            'password_reset.smtplib.SMTP'
        ) as smtp_constructor:
            smtp_constructor.return_value.__enter__.return_value = smtp_connection
            response = self.client.post(
                '/forgot-password',
                data={'_csrf': token, 'email': 'admin@example.com'},
            )
        self.assertEqual(response.status_code, 503)
        with app_module.app.app_context():
            tokens = app_module.db().execute(
                'SELECT COUNT(*) FROM password_reset_tokens'
            ).fetchone()[0]
        self.assertEqual(tokens, 0)

    def test_expired_reset_token_is_rejected(self):
        admin_id = self.create_admin()
        reset_token = 'expired-test-token'
        token_hash = hashlib.sha256(reset_token.encode()).hexdigest()
        with app_module.app.app_context():
            app_module.db().execute(
                'INSERT INTO password_reset_tokens(token_hash, user_id, expires_at, created_at) '
                "VALUES(?,?, '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00')",
                (token_hash, admin_id),
            )
            app_module.db().commit()
        self.assertEqual(self.client.get(f'/reset-password/{reset_token}').status_code, 400)


if __name__ == '__main__':
    unittest.main()

import builtins
import hashlib
import io
import os
import re
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

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

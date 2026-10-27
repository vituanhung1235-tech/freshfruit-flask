import hashlib
import os
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urljoin, urlsplit

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash


def create_password_reset_blueprint(get_db):
    blueprint = Blueprint('password_reset', __name__)

    def ensure_token_table():
        get_db().execute(
            '''
            CREATE TABLE IF NOT EXISTS password_reset_tokens(
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )

    def valid_token(token):
        token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        return get_db().execute(
            '''
            SELECT tokens.user_id
            FROM password_reset_tokens AS tokens
            JOIN users ON users.id=tokens.user_id
            WHERE tokens.token_hash=? AND tokens.expires_at>? AND users.role='admin'
            ''',
            (token_hash, now),
        ).fetchone()

    def send_reset_email(email, reset_url):
        host = os.getenv('SMTP_HOST', '').strip()
        username = os.getenv('SMTP_USERNAME', '').strip()
        password = os.getenv('SMTP_PASSWORD', '')
        sender = os.getenv('MAIL_FROM', '').strip()
        base_url = os.getenv('APP_BASE_URL', '').strip().rstrip('/') + '/'
        parsed_base_url = urlsplit(base_url)
        try:
            port = int(os.getenv('SMTP_PORT', '587'))
        except ValueError as exc:
            raise ValueError('SMTP_PORT must be an integer') from exc

        if (
            not all((host, username, password, sender))
            or parsed_base_url.scheme not in ('https', 'http')
            or not parsed_base_url.netloc
            or parsed_base_url.username
            or parsed_base_url.password
        ):
            raise ValueError('SMTP and APP_BASE_URL settings are required')
        if not 1 <= port <= 65535:
            raise ValueError('SMTP_PORT is outside the valid range')

        message = EmailMessage()
        message['Subject'] = 'Đặt lại mật khẩu quản trị Tuấn Hùng Fruit'
        message['From'] = sender
        message['To'] = email
        message.set_content(
            'Chúng tôi nhận được yêu cầu đặt lại mật khẩu quản trị. '
            f'Mở liên kết sau để tạo mật khẩu mới (có hiệu lực trong 30 phút): {reset_url}\n\n'
            'Nếu bạn không yêu cầu, hãy bỏ qua email này.'
        )

        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(username, password)
            smtp.send_message(message)

    @blueprint.route('/forgot-password', methods=['GET', 'POST'])
    def forgot_password():
        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()[:255]
            ensure_token_table()
            user = get_db().execute(
                "SELECT id FROM users WHERE email=? AND role='admin'",
                (email,),
            ).fetchone()

            if user:
                token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
                now = datetime.now(timezone.utc)
                get_db().execute(
                    'DELETE FROM password_reset_tokens WHERE user_id=?',
                    (user['id'],),
                )
                get_db().execute(
                    'INSERT INTO password_reset_tokens(token_hash, user_id, expires_at, created_at) '
                    'VALUES(?,?,?,?)',
                    (
                        token_hash,
                        user['id'],
                        (now + timedelta(minutes=30)).isoformat(timespec='seconds'),
                        now.isoformat(timespec='seconds'),
                    ),
                )
                get_db().commit()
                reset_url = urljoin(
                    os.getenv('APP_BASE_URL', '').rstrip('/') + '/',
                    url_for('password_reset.reset_password', token=token),
                )
                try:
                    send_reset_email(email, reset_url)
                except (OSError, smtplib.SMTPException, ValueError):
                    current_app.logger.exception('Could not send admin password reset email')
                    get_db().execute(
                        'DELETE FROM password_reset_tokens WHERE token_hash=?',
                        (token_hash,),
                    )
                    get_db().commit()
                    flash('Chưa gửi được email đặt lại mật khẩu. Vui lòng kiểm tra cấu hình email.', 'danger')
                    return render_template('forgot_password.html'), 503

            flash(
                'Nếu email thuộc tài khoản quản trị, hướng dẫn đặt lại mật khẩu sẽ được gửi đến hộp thư.',
                'success',
            )
            return redirect(url_for('password_reset.forgot_password'))

        return render_template('forgot_password.html')

    @blueprint.route('/reset-password/<token>', methods=['GET', 'POST'])
    def reset_password(token):
        ensure_token_table()
        if request.method == 'POST':
            get_db().execute('BEGIN IMMEDIATE')
        reset = valid_token(token)
        if not reset:
            if request.method == 'POST':
                get_db().rollback()
            return render_template('reset_password.html', invalid=True), 400

        if request.method == 'POST':
            password = request.form.get('password', '')
            confirm = request.form.get('confirm', '')
            if len(password) < 8 or password != confirm:
                get_db().rollback()
                flash('Mật khẩu phải có ít nhất 8 ký tự và hai lần nhập phải khớp.', 'danger')
                return render_template('reset_password.html', invalid=False), 400

            get_db().execute(
                'UPDATE users SET password_hash=? WHERE id=? AND role=?',
                (generate_password_hash(password), reset['user_id'], 'admin'),
            )
            get_db().execute(
                'DELETE FROM password_reset_tokens WHERE user_id=?',
                (reset['user_id'],),
            )
            get_db().commit()
            flash('Đã đổi mật khẩu. Bạn có thể đăng nhập bằng mật khẩu mới.', 'success')
            return redirect(url_for('login'))

        return render_template('reset_password.html', invalid=False)

    return blueprint

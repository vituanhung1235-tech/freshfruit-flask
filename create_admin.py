"""Chạy: python create_admin.py để tạo/nâng cấp tài khoản admin."""
import getpass
from app import app,db
from werkzeug.security import generate_password_hash

with app.app_context():
    email=input('Email admin: ').strip().lower()
    name=input('Họ tên admin: ').strip()
    password=getpass.getpass('Mật khẩu admin (ít nhất 8 ký tự): ')
    if not email or not name or len(password)<8:raise SystemExit('Dữ liệu không hợp lệ')
    user=db().execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
    if user:
        db().execute('UPDATE users SET name=?,password_hash=?,role=? WHERE id=?',(name,generate_password_hash(password),'admin',user['id']))
    else:
        db().execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',(name,email,generate_password_hash(password),'admin'))
    db().commit();print('Đã tạo/cập nhật Admin. Đăng nhập tại /login, quản trị tại /admin')

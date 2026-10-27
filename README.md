# FreshFruit – Website bán hoa quả với Flask, SQLite và chatbot AI

Website học tập hoàn chỉnh chạy trong VS Code: trang chủ, bộ lọc và tìm kiếm sản phẩm, đăng ký/đăng nhập, phân quyền Admin, giỏ hàng, đặt COD, quản lý đơn hàng, tồn kho, đánh giá, mã giảm giá FRESH10 và chatbot tư vấn.

## 1. Cài đặt và chạy (Windows – VS Code)

Cài Python 3.11+ và mở thư mục dự án trong VS Code → Terminal → New Terminal.

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

Mở **http://127.0.0.1:5000**. Lần đầu chạy tự khởi tạo `freshfruit.db` và 12 sản phẩm mẫu.

Nếu PowerShell không cho kích hoạt virtual environment, chạy trực tiếp `.venv\Scripts\python.exe -m pip install -r requirements.txt` và `.venv\Scripts\python.exe app.py`.

Trên macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && cp .env.example .env && python app.py`.

## 2. Tạo tài khoản quản trị

```powershell
python create_admin.py
```

Nhập email, tên, mật khẩu tự chọn. Đăng nhập trên `/login`, truy cập `/admin`. Không có mật khẩu Admin mặc định.

## 3. Bật chatbot AI trả lời đa lĩnh vực

Trong `.env` thêm:

```env
OPENAI_API_KEY=sk-...thay_bang_key_that...
OPENAI_MODEL=gpt-5.5
```

Lấy API key từ https://platform.openai.com/api-keys. Tài khoản API thường cần thanh toán riêng; tên model có thể thay đổi theo quyền truy cập. Khởi động lại app sau khi sửa `.env`. Nếu không có key, chatbot vẫn hướng dẫn mua hàng và đọc danh sách giá nhưng **không** giải đáp đa lĩnh vực. Key chỉ đặt phía backend; KHÔNG đẩy `.env` lên GitHub.

**Chatbot không thể đảm bảo trả lời chính xác mọi câu hỏi.** Không có cơ chế lấy dữ liệu web theo thời gian thực trong bản demo này.

## 4. Đặt lại mật khẩu admin qua email

Trong `.env`, cấu hình SMTP và URL gốc của website:

```env
APP_BASE_URL=https://ten-mien-cua-ban
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=email-gui-cua-ban@gmail.com
SMTP_PASSWORD=mat-khau-ung-dung-16-ky-tu
MAIL_FROM=email-gui-cua-ban@gmail.com
```

Với Gmail, hãy bật xác minh 2 bước và tạo **App Password**; không dùng mật khẩu đăng nhập Gmail thông thường. Giữ các giá trị bí mật trong `.env`, không đưa vào GitHub. Trên máy local có thể dùng `APP_BASE_URL=http://127.0.0.1:5000`. Khởi động lại ứng dụng sau khi cập nhật cấu hình.

Tại trang đăng nhập, chọn **Quên mật khẩu admin?**, nhập email quản trị và mở liên kết được gửi đến hộp thư. Liên kết chỉ dùng một lần, hết hạn sau 30 phút; khách hàng không thể dùng chức năng này để đổi mật khẩu. SMTP cần được cấu hình mới gửi email thật; bộ test kiểm tra luồng gửi bằng SMTP giả lập.

## 5. Đẩy code lên GitHub

```bash
git init
git add .
git commit -m "FreshFruit Flask initial"
git branch -M main
git remote add origin https://github.com/USERNAME/freshfruit-flask.git
git push -u origin main
```

Thay `USERNAME` bằng tên tài khoản GitHub. File `.env`, database và thư mục `.venv` đã được loại khỏi Git.

## 6. Triển khai lên mạng Internet (quan trọng)

Mã nguồn đã có entrypoint Flask tại `api/index.py` và route rewrite trong `vercel.json`. Tuy nhiên, **không triển khai cấu hình SQLite này lên Vercel để bán hàng thật**: filesystem `/tmp` của function chỉ tạm thời, mỗi function instance có thể dùng database riêng và dữ liệu có thể mất sau khi instance/deploy thay đổi. Để tài khoản, phiên đăng nhập, giỏ hàng và đơn hàng hoạt động ổn định, chuyển sang PostgreSQL/MySQL dùng chung (ví dụ Neon/Supabase/Railway) trước khi deploy; hoặc chạy Flask + SQLite trên VPS có ổ đĩa bền vững và backup.

Trong môi trường thật: SECRET_KEY mạnh, COOKIE_SECURE=1 khi chạy HTTPS, cấu hình DB có backup, giới hạn tần suất đăng nhập/chatbot, email reset password, xác thực địa chỉ/số điện thoại, payment gateway và xử lý quy trình giao hàng.

## 7. Giới hạn hiện tại

- Chỉ hỗ trợ thanh toán khi nhận hàng; chưa có thanh toán online.
- Chưa có đăng nhập Google.
- Chưa có giới hạn tần suất, chính sách hoàn hàng, bảng giá giao hàng hay lưu lịch sử chatbot vào DB.
- Hình ảnh hoa quả dùng emoji, có thể đổi sang hình ảnh thật khi triển khai.
- Chưa tích hợp kiểm duyệt nội dung và bảo vệ chống abuse ở quy mô công khai.
- Kiến trúc phù hợp để học và demo, **chưa phải phần mềm thương mại production-ready**.

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

Nếu cấu hình `ADMIN_EMAIL`, `ADMIN_PASSWORD` (tối thiểu 12 ký tự) và tùy chọn `ADMIN_NAME` trong `.env`, ứng dụng sẽ tạo/cập nhật tài khoản admin này mỗi lần khởi tạo database. Không commit mật khẩu vào GitHub.

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

## 6. Deploy Vercel với database dùng chung

Vercel cần PostgreSQL dùng chung để tài khoản, phiên làm việc, giỏ hàng, đơn hàng và dữ liệu quản trị được giữ lại qua các lần chạy. Ứng dụng giữ SQLite làm mặc định trên máy local; khi có `DATABASE_URL`, ứng dụng dùng PostgreSQL (Neon được hỗ trợ). Không dùng SQLite `/tmp` cho dữ liệu cần lưu bền trên Vercel.

### Chuẩn bị Neon và chuyển dữ liệu local

1. Tạo một project/database PostgreSQL trên Neon và lấy connection string trong **Dashboard → Connect**. Giữ URL riêng tư; không dán vào chat, GitHub hay ảnh chụp màn hình.
2. Sao lưu file `freshfruit.db` trước khi chuyển. Lệnh migration không xóa hoặc sửa SQLite nguồn và chỉ ghi vào database PostgreSQL đích **đang trống**.
3. Mở `.env` local và thêm `DATABASE_URL` bằng connection string Neon. Không commit `.env`.
4. Chạy lệnh migration từ thư mục dự án:

   ```powershell
   .venv\Scripts\python.exe migrate_sqlite_to_postgres.py
   ```

   Lệnh báo số hàng đã chuyển theo bảng. Nếu database đích đã có dữ liệu, lệnh dừng mà không ghi/xóa dữ liệu; không trỏ đến database đang dùng thật.

### Cấu hình và deploy Vercel

1. Vào **Vercel → Project → Settings → Environment Variables** và đặt các biến cho **Production** (thêm Preview nếu cần):
   - `DATABASE_URL`: cùng Neon connection string đã dùng để chuyển dữ liệu.
   - `SECRET_KEY`: bắt buộc; khóa ngẫu nhiên dài, ổn định giữa các lần deploy và dùng chung cho Production/Preview tương ứng. Tạo local bằng `py -c "import secrets; print(secrets.token_urlsafe(48))"`. App sẽ không khởi động trên Vercel nếu thiếu khóa này, vì cookie phiên và CSRF token sẽ lỗi giữa các function instance.
   - `ADMIN_EMAIL`, `ADMIN_PASSWORD` (ít nhất 12 ký tự), `ADMIN_NAME`.
   - `COOKIE_SECURE=1`.
   - `OPENAI_API_KEY` và `OPENAI_MODEL=gpt-4o-mini` nếu bật chatbot AI.
2. Nếu dùng quên mật khẩu qua email, đặt thêm `APP_BASE_URL` bằng URL Production và các biến SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_FROM`.
3. Sau khi xác nhận biến đã lưu, redeploy Production từ nhánh `main`.

Không gửi credential hoặc connection string cho người khác, và không commit chúng vào source code. Nếu `DATABASE_URL` chưa được cấu hình trên Vercel, app vẫn có thể chạy SQLite tạm thời để báo lỗi cấu hình trong log, nhưng dữ liệu đó không bền và không đồng bộ. PostgreSQL được khởi tạo schema tự động; tài khoản admin được tạo/cập nhật từ các biến `ADMIN_*`.

### Kiểm tra sau deploy

- Mở trang chủ và `/health`.
- Đăng ký/đăng nhập, tải lại trang và kiểm tra phiên vẫn được giữ.
- Thêm sản phẩm vào giỏ, tải lại giỏ, rồi đặt thử đơn COD.
- Kiểm tra đơn hàng và cập nhật trạng thái trong `/admin`.
- Chatbot dùng OpenAI khi key hợp lệ và project API còn hạn mức; nếu không, endpoint trả lời bằng bot dự phòng.

Trong môi trường thật, bật backup PostgreSQL, giới hạn tần suất đăng nhập/chatbot, cấu hình email reset password, xác thực địa chỉ/số điện thoại, payment gateway và quy trình giao hàng. Bản này chưa tích hợp thanh toán online.

## 7. Giới hạn hiện tại

- Chỉ hỗ trợ thanh toán khi nhận hàng; chưa có thanh toán online.
- Chưa có đăng nhập Google.
- Chưa có giới hạn tần suất, chính sách hoàn hàng, bảng giá giao hàng hay lưu lịch sử chatbot vào DB.
- Hình ảnh hoa quả dùng emoji, có thể đổi sang hình ảnh thật khi triển khai.
- Chưa tích hợp kiểm duyệt nội dung và bảo vệ chống abuse ở quy mô công khai.
- Kiến trúc phù hợp để học và demo, **chưa phải phần mềm thương mại production-ready**.

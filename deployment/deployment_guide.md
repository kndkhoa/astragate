# Hướng dẫn Cấu hình CI/CD Tự động hóa Triển khai (Frontend -> Vercel, Backend -> VPS)

Tài liệu này hướng dẫn bạn cách cấu hình hệ thống để mỗi khi bạn **git push** code lên GitHub:
1. **Frontend (Next.js)** sẽ tự động build và deploy lên Vercel.
2. **Backend (Docker/FastAPI/Redis/LiteLLM)** sẽ tự động SSH vào VPS Đức để kéo code mới và cập nhật Docker containers.

---

## 1. Cấu hình Frontend trên Vercel

Vercel hỗ trợ kết nối trực tiếp với GitHub nên cấu hình cực kỳ đơn giản:

1. Truy cập vào **[Vercel.com](https://vercel.com/)** và đăng nhập bằng tài khoản GitHub của bạn.
2. Nhấp vào nút **Add New...** -> **Project**.
3. Chọn Repository `astragate` của bạn từ danh sách GitHub.
4. Tại phần **Configure Project**:
   * **Framework Preset**: Chọn `Next.js`.
   * **Root Directory**: Nhấp vào *Edit* và chọn thư mục **`frontend`** (rất quan trọng!).
5. Mở rộng phần **Environment Variables** và nhập 3 biến môi trường sau:
   * `NEXT_PUBLIC_API_URL`: `http://194.163.145.31:8000` (Thay bằng IP của VPS của bạn).
   * `NEXT_PUBLIC_SUPABASE_URL`: `https://nvyzvhslxnpqpjfhmirs.supabase.co` (Link Supabase Đức).
   * `NEXT_PUBLIC_SUPABASE_ANON_KEY`: `eyJhbGciOiJIUzI1NiIsInR5c...` (Anon Key Supabase Đức).
6. Nhấp vào **Deploy**. Kể từ bây giờ, bất kỳ khi nào bạn push code lên GitHub, Vercel sẽ tự động build lại Frontend cho bạn.

---

## 2. Chuẩn bị trên VPS Contabo của bạn

Trước khi chạy CI/CD cho Backend, bạn cần chuẩn bị môi trường trên VPS Đức:

1. **SSH vào VPS bằng quyền root:**
   ```bash
   ssh root@194.163.145.31
   ```
2. **Cài đặt Docker và Git (nếu chưa có):**
   ```bash
   # Cập nhật hệ thống
   apt-get update && apt-get upgrade -y
   
   # Cài đặt Git
   apt-get install git -y
   
   # Cài đặt Docker
   curl -fsSL https://get.docker.com -o get-docker.sh
   sh get-docker.sh
   ```
3. **Thiết lập thư mục dự án trên VPS:**
   * Bạn cần clone source code từ GitHub về thư mục `/root/astragate` trên VPS:
   ```bash
   cd /root
   # Clone repo của bạn về (Nếu là repo private, bạn cần sử dụng SSH Key hoặc Personal Access Token)
   git clone https://github.com/USERNAME/REPO_NAME.git astragate
   ```
4. **Tạo file cấu hình `.env` trực tiếp trên VPS:**
   * Di chuyển vào thư mục dự án trên VPS và tạo file `.env` chứa toàn bộ key bảo mật (như JWT_SECRET, API keys của LLM, và DATABASE_URL của Supabase Đức):
   ```bash
   cd /root/astragate
   nano .env
   ```
   *(Dán nội dung file `.env` cấu hình Supabase Đức của bạn vào đây, lưu ý: giữ `REDIS_URL=redis://redis:6379` và `LITELLM_URL=http://litellm:4000`)*.

---

## 3. Cấu hình GitHub Actions Secrets cho Backend

Để GitHub Actions Workflow deploy-backend.yml có thể đăng nhập vào VPS của bạn và chạy lệnh triển khai, bạn cần cấu hình các thông tin bảo mật (Secrets) trên GitHub:

1. Truy cập vào Repository của bạn trên **GitHub**.
2. Chọn tab **Settings** -> **Secrets and variables** (ở cột bên trái) -> **Actions**.
3. Nhấp vào nút **New repository secret** để thêm 3 Secrets sau:

| Tên Secret | Giá trị | Mô tả |
| :--- | :--- | :--- |
| **`VPS_HOST`** | `194.163.145.31` | Địa chỉ IP của VPS Contabo của bạn. |
| **`VPS_USERNAME`** | `root` | Tài khoản đăng nhập (mặc định là `root`). |
| **`VPS_SSH_KEY`** | `-----BEGIN OPENSSH PRIVATE KEY-----...` | Khóa SSH private (file `.pem` hoặc `id_rsa`) dùng để SSH vào VPS mà không cần mật khẩu. |

> [!TIP]
> **Cách lấy khóa SSH Private:**
> Nếu bạn chưa cấu hình SSH Key trên VPS, hãy chạy lệnh sau trên VPS để tạo:
> ```bash
> ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N ""
   cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys
   ```
> Sau đó, in khóa Private ra để copy đưa vào GitHub Secret `VPS_SSH_KEY`:
> ```bash
   cat ~/.ssh/id_rsa
   ```

---

## 4. Kiểm tra hoạt động của CI/CD

Sau khi hoàn tất 3 bước trên, bạn chỉ cần thực hiện thay đổi bất kỳ trong thư mục dự án ở máy local và push lên GitHub:
```bash
git add .
git commit -m "Configure CI/CD pipelines"
git push origin main
```
* **Frontend:** Vercel sẽ tự bắt sự kiện và deploy tự động.
* **Backend:** GitHub Actions sẽ kích hoạt Job `Deploy Backend to VPS`. Bạn có thể theo dõi tiến trình chạy trực quan trong tab **Actions** on GitHub. Nó sẽ SSH vào VPS của bạn, kéo code mới nhất về và tự động chạy lệnh:
  `docker compose up -d --build api redis litellm` để cập nhật dịch vụ mà không gây gián đoạn hệ thống.

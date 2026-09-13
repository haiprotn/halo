# Halo — cổng quản trị WiFi Marketing

Gộp `halo-ads` (quảng cáo, tên cũ `wifi-ads-server`) và `halo-admin` (quản trị, tên cũ
`vsp-server`) thành **một ứng dụng Python duy nhất** trên **PostgreSQL**: một lần đăng
nhập, một danh sách khách hàng và địa điểm dùng chung, một bản sao lưu.

> **Hệ quả của việc gộp lên kế hoạch GitHub:** chỉ còn **một repo Private `halo`**, nên
> chỉ cần **một deploy key** và **một bí danh** trong `~/.ssh/config` — thay vì hai bộ
> như dự tính ban đầu (bẫy #25: một deploy key chỉ gắn được vào một repo).

- **Stack:** Python 3.11+ · FastAPI · SQLAlchemy 2.0 · Jinja2 · PostgreSQL 16 · systemd + nginx
- **Giao diện:** HTML/CSS thuần, không framework, không bước build, không tải gì từ CDN
- **Kiểm thử:** 52 phép kiểm chạy trên router MikroTik giả lập, không cần thiết bị thật

---

## 1. Vì sao gộp mà vẫn an toàn

Bản Node tách làm hai tiến trình vì một lý do rất cụ thể: quảng cáo là luồng **đẩy file**
(SFTP, chậm), quản trị là luồng **đọc trạng thái** (REST, liên tục); trộn chung thì một
router timeout làm treo cả việc đẩy banner.

Bản Python gộp **mã nguồn và CSDL**, nhưng giữ nguyên sự tách biệt ở đúng chỗ quan trọng:

| Tiến trình | Dịch vụ systemd | Việc |
|---|---|---|
| `app/main.py` | `halo-web` | phục vụ người dùng, chỉ chạm router khi có người bấm nút |
| `app/worker.py` | `halo-worker` | quét trạng thái 60 giây/lần, **chỉ đọc** |

Hai tiến trình khởi động lại độc lập. Worker chết thì web vẫn bấm nút được; web quá tải
thì việc quét vẫn chạy. Thêm vào đó **mọi lời gọi ra router đều có timeout cứng**
(`MONITOR_TIMEOUT`, `SFTP_TIMEOUT`).

---

## 2. Cài đặt lên `server-001`

Xem `TRIEN-KHAI.md` để có từng lệnh theo thứ tự. Tóm tắt:

```bash
cd /srv/app/halo && sudo bash install.sh
```

Script làm: cài gói hệ thống → tạo user `halo` → dựng venv → tạo CSDL PostgreSQL →
sinh `.env` (kèm khóa bí mật) → tạo bảng + tài khoản quản trị đầu tiên → cài 2 dịch vụ
systemd + nginx. Chạy lại nhiều lần được; **`.env` đã có thì không bị ghi đè**.
Chạy ngay tại `/srv/app/halo` thì bước chép mã nguồn tự bỏ qua — đúng với luồng `git pull`.

**Ba điều chỉnh riêng cho máy này** (lấy từ `claude/may-chu-tai-cho.md`):

| Mặc định | Trên `server-001` | Vì sao |
|---|---|---|
| `/opt/…` | `/srv/app/halo` | quy ước đã chốt của máy |
| cổng `8080` | cổng **`8081`** | máy đang có một tiến trình Python lạ nghe `0.0.0.0:8080` |
| certbot ngay | **hoãn** | chưa có IP tĩnh; giai đoạn 1 chạy HTTP trong LAN |

Cập nhật về sau (sau khi đẩy mã mới lên GitHub):

```bash
cd /srv/app/halo && sudo bash deploy/cap-nhat.sh
```

Script này **tự dump CSDL trước khi pull**, cài lại thư viện, chạy migration, khởi động
lại dịch vụ, gọi thử `/khoe`, và in sẵn lệnh quay lui nếu web không trả lời.

Kiểm tra:

```bash
curl -s http://127.0.0.1:8081/khoe        # phải in "ok"
journalctl -u halo-web -f
journalctl -u halo-worker -f
```

### Chạy thử trên máy cá nhân

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 -c "import secrets; print('APP_KEY=' + secrets.token_hex(32)); print('SESSION_SECRET=' + secrets.token_hex(32))"  # dán vào .env
createdb halo                              # hoặc: sudo -u postgres createdb -O halo halo
python3 -m scripts.init_db
uvicorn app.main:app --reload --port 8081
```

---

## 3. Đường đi của mã nguồn

```
app/
  config.py          đọc .env, một chỗ duy nhất chứa tham số
  db.py              kết nối PostgreSQL
  models.py          các bảng (tenant, user, site, banner, voucher, alert, …)
  security.py        bcrypt (mật khẩu người) + AES-256-GCM (mật khẩu router) + cookie phiên
  deps.py            ★ cách ly đa khách hàng — mọi truy vấn địa điểm đi qua get_site()
  templating.py      bộ lọc hiển thị (giờ Việt Nam, dung lượng, thứ trong tuần)
  main.py            tiến trình web
  worker.py          tiến trình quét nền
  routers/
    auth.py          đăng nhập, khóa tài khoản sau 5 lần sai
    pages.py         tổng quan, địa điểm, bảng kiểm tra, báo cáo, nhật ký
    ads.py           banner: tải lên, lịch chiếu, đẩy xuống router
    vouchers.py      sinh / đẩy / đồng bộ / xóa / in / xuất CSV
    admin.py         khách hàng, tài khoản, địa điểm, khối lệnh .rsc
  services/
    routeros.py      client REST API RouterOS 7
    checks.py        ★ 14 mục kiểm tra cấu hình
    vouchers.py      ★ logic voucher, có quay lui khi gán gói cước lỗi
    adsjs.py         sinh file ads.js cho portal
    sftp_push.py     đẩy file xuống router
    monitor.py       quét trạng thái, cảnh báo dạng trạng thái
    wireguard.py     cấp IP, sinh khóa, in khối .rsc
tests/               52 phép kiểm + router giả lập
deploy/              2 unit systemd + cấu hình nginx
```

---

## 4. Ba bài học đã trả giá, được cài cứng vào mã

**Bẫy #18 — đẩy voucher thất bại giữa chừng để lại tài khoản mồ côi.**
Tạo user xong mà gán gói cước lỗi thì trên User Manager còn một tài khoản **không có
giới hạn**; khách nhập mã đó vẫn vào được. `push_voucher()` luôn **quay lui** — xóa
user vừa tạo rồi mới báo lỗi. Nếu cả việc xóa cũng hỏng, thông báo nói thẳng là phải
vào router xóa tay ngay. (`tests/test_voucher.py`)

**Bẫy #19 — "không thấy trên router" ≠ "đã xóa".**
Mã ở trạng thái `moi` hoặc `loi` cũng không có trên router. Chỉ mã **đã từng** lên
router mới được chuyển sang `da_xoa`, nếu không người vận hành mất dấu vết lô bị lỗi.

**Bẫy #20 — trần 50 phiên UM ≠ trần 50 voucher.**
Voucher chưa dùng không chiếm chỗ. Cảnh báo nói rõ 50 là trần **phiên đồng thời**.

**Nguyên tắc vàng — không đoán tên đối tượng.** Bảng kiểm tra đọc tên thật trên router
(`ip/hotspot` → `profile` → `trial-user-profile`) rồi mới xét, không dựa vào tên trong
bản mẫu. Có một bài kiểm thử riêng đổi hết tên profile để chứng minh điều này.

---

## 5. ★ APP_KEY — mất là mất mật khẩu router

Hai loại mật khẩu, hai cách xử lý:

| | Mật khẩu người dùng | Mật khẩu router |
|---|---|---|
| Cách lưu | bcrypt (băm một chiều) | AES-256-GCM (mã hóa hai chiều) |
| Vì sao | chỉ cần so khớp | phải đọc lại để gọi REST/SFTP |
| Khóa | không có | `APP_KEY` trong `.env` |

**Sao lưu `.env` cùng với bản dump CSDL.** Có CSDL mà không có `APP_KEY` thì mật khẩu
router coi như mất, phải vào từng router đặt lại.

Thêm ba dòng này vào `/usr/local/bin/sao-luu.sh` đang có sẵn trên `server-001`
(cron đã chạy sẵn, không phải tạo lịch mới):

```bash
sudo -u postgres pg_dump halo | gzip > /srv/backup/halo/halo-$(date +%F).sql.gz
cp /srv/app/halo/.env /srv/backup/halo/env-$(date +%F)
tar czf /srv/backup/halo/banners-$(date +%F).tgz -C /srv/app/halo data/banners
```

`.env` và bản dump phải nằm **cùng một chỗ sao lưu** — tách ra là có ngày khôi phục được
CSDL mà không mở được mật khẩu router.

---

## 6. WireGuard hub trên `server-001`

★ **UFW trên máy này đã mở sẵn `51820/udp`**, còn cấu hình dưới đây nghe `13231/udp`
(cổng WireGuard mặc định của RouterOS). Chọn một trong hai rồi thống nhất ở cả ba chỗ:
`ListenPort` ở đây · luật UFW · `endpoint-port` trong khối `.rsc` sinh cho router.
Dễ nhất là **đổi `ListenPort` thành `51820`** để khỏi động vào UFW.

```bash
# Trên server-001, một lần duy nhất
sudo -v
sudo sh -c 'umask 077; wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key'
sudo tee /etc/wireguard/wg0.conf >/dev/null <<EOF
[Interface]
Address = 10.90.0.1/24
ListenPort = 51820
PrivateKey = $(sudo cat /etc/wireguard/private.key)
SaveConfig = true
EOF
sudo chmod 600 /etc/wireguard/wg0.conf
sudo systemctl enable --now wg-quick@wg0
sudo cat /etc/wireguard/public.key     # dán vào form khi lấy khối lệnh cho router
```

Viết bằng `tee` + heredoc chứ không mở `nano` — nano tự thụt lề đã từng làm hỏng YAML
netplan trên chính máy này. Và chạy `sudo -v` trước khi dán khối nhiều dòng, nếu không
các dòng sau bị nuốt làm mật khẩu sudo.

**Chưa có IP tĩnh công cộng** nên giai đoạn này router chỉ quay về được khi cùng mạng.
Khi dọn sang phòng máy chủ và có IP tĩnh: NAT `51820/udp` về máy, rồi đổi
`endpoint-address` của peer **từng site một**.

Mỗi khi thêm địa điểm: trang Quản trị → **Khối lệnh cho router** → dán vào router
(Ctrl+X bật Safe Mode trước) → rồi trên `server-001`:

```bash
wg set wg0 peer <KHÓA CÔNG KHAI CỦA ROUTER> allowed-ips 10.90.0.X/32
wg-quick save wg0
```

---

## 7. Cam kết dữ liệu — đọc trước khi đổi `COLLECT_SESSIONS`

Mặc định `COLLECT_SESSIONS=false`: hệ thống chỉ lưu **số đếm tổng hợp**, không lưu chi
tiết từng phiên của khách. Đó là điều giữ cho câu *"dữ liệu không rời khỏi khuôn viên"*
trong bản thuyết minh Word gửi khách hàng còn đúng.

Bật `true` là **phải sửa mục "luồng dữ liệu" trong bản Word trước khi gửi**. Cảnh báo
này hiện ngay trên trang Tổng quan để không ai bật rồi quên.

Báo cáo luôn nhắc: MAC randomization của iOS/Android làm số "thiết bị duy nhất" cao
hơn số người thật — **không dùng làm cam kết với nhà tài trợ khi chưa đo pilot**.

---

## 8. Kiểm thử

```bash
sudo -u postgres psql -c "CREATE DATABASE halo_test OWNER halo;"
pytest -q
```

52 phép kiểm, chạy khoảng 40 giây, **không cần router thật**. `tests/mock_router.py`
dựng hai router giả lập: một cấu hình sạch (mọi mục kiểm tra phải đạt) và một cấu hình
cố ý sai đúng những lỗi đã gặp ngoài hiện trường (thiếu gói `user-manager` do tải nhầm
kiến trúc npk, thiếu `radius incoming accept=yes`, `login-by` không có trial, phiên
trial lọt vào UM, walled garden chứa trang dò captive, thiếu MSS clamp…).

Phạm vi đã kiểm: đăng nhập và khóa tài khoản · cách ly đa khách hàng (404 chứ không
403) · mật khẩu router không lộ qua giao diện và không nằm dạng chữ thường trong CSDL ·
14 mục kiểm tra cấu hình · sinh/đẩy/đồng bộ/xóa voucher kèm quay lui · cảnh báo dạng
trạng thái · sinh `ads.js` · giới hạn dung lượng banner · nhật ký thao tác.

---

## 9. Ghi chú cho người đang học Python

Vài chỗ trong mã đáng đọc kỹ vì đó là cách làm chuẩn của hệ sinh thái Python:

- **`Depends(...)` của FastAPI** (`app/deps.py`) — "tiêm phụ thuộc": route khai báo nó
  cần gì (`db`, `user`), framework lo việc tạo và dọn. Đây là chỗ đặt kiểm quyền để
  không route nào quên kiểm.
- **Route viết bằng `def` chứ không `async def`** — FastAPI tự đẩy sang luồng riêng, nên
  truy vấn CSDL đồng bộ không làm nghẽn. Viết `async def` mà bên trong gọi hàm đồng bộ
  mới là chỗ hay sai.
- **`yield` trong `get_db()`** (`app/db.py`) — mở tài nguyên, giao cho route dùng, đóng
  ở `finally`. Cùng ý tưởng với `with`.
- **SQLAlchemy 2.0 kiểu `Mapped[...]`** (`app/models.py`) — bảng khai báo bằng gợi ý
  kiểu, trình soạn thảo gợi ý được tên cột.
- **`pydantic-settings`** (`app/config.py`) — cấu hình là một lớp có kiểu, sai kiểu là
  báo lỗi ngay lúc khởi động chứ không phải lúc 2 giờ sáng.
- **`pytest` + fixture** (`tests/conftest.py`) — `fixture` là dữ liệu/bối cảnh dựng sẵn
  cho bài kiểm thử; `monkeypatch` thay tạm một hàm để không phải gọi ra thiết bị thật.

---

## 10. Còn để ngỏ

- Gửi cảnh báo ra ngoài (Telegram/email) khi site rớt — hiện chỉ hiện trên dashboard
- Đẩy hàng loạt: sinh voucher hoặc đẩy banner cho nhiều site cùng lúc
- Nhân bản bộ banner từ site này sang site khác
- Thống kê lượt hiển thị / lượt bấm banner (cần sửa cam kết dữ liệu trong bản Word)
- Đường nâng cấp FreeRADIUS nếu Viettel yêu cầu OTP: module giám sát và báo cáo giữ
  nguyên, chỉ viết lại phần đẩy mã trong `app/services/vouchers.py`

# Triển khai Halo lên `server-001`

Máy: Ubuntu Server 24.04.3 LTS · `192.168.1.10` · user `haiprotn` · SSH bí danh `server-001`.
Phần cứng hoá (SSH, UFW, fail2ban, unattended-upgrades, sao lưu) **đã làm xong** —
tài liệu này chỉ lo phần ứng dụng.

Làm theo đúng thứ tự. Mỗi bước có **cách kiểm chứng**; chưa thấy kết quả đúng thì đừng
sang bước sau.

---

## Bước 0 — Ba việc phải làm TRƯỚC khi cài (5 phút)

### 0.1 Truy ra tiến trình lạ đang nghe cổng 8080

Trong `may-chu-tai-cho.md` có ghi hai dịch vụ chưa truy ra nguồn gốc, trong đó **một
tiến trình Python nghe `0.0.0.0:8080`** — tức là mọi card mạng. Halo đã được đổi sang
**cổng 8081** nên không đụng nhau, nhưng vẫn phải biết đó là cái gì trước khi máy ra
Internet.

```bash
ssh server-001
sudo ss -tulpn | grep -E ':8080|:27019|:8088'
PID=$(sudo ss -tulpn | awk '/:8080/ {print $NF}' | grep -o '[0-9]*' | head -1)
sudo tr '\0' ' ' < /proc/$PID/cmdline; echo
sudo systemctl status $PID --no-pager | head -5
snap list
```

Kết quả in ra dán lại cho mình xem nếu chưa rõ. Nếu là thứ không dùng: `sudo systemctl
disable --now <unit>` hoặc `sudo snap remove <tên>`.

### 0.2 Kiểm tra chỗ trống và PostgreSQL

```bash
df -h /srv /var        # cần ít nhất ~5 GB trống
free -h                # RAM 7.6G, PostgreSQL + 2 tiến trình Python rất thoải mái
which psql || echo "chưa cài postgres — install.sh sẽ tự cài"
```

### 0.3 Tạo thư mục đích

```bash
sudo mkdir -p /srv/app /srv/backup/halo
sudo chown haiprotn:haiprotn /srv/app
```

---

## Bước 1 — Đưa mã nguồn lên GitHub (làm ở máy Windows)

> **Việc gộp hai app làm một khiến phần này nhẹ đi một nửa:** chỉ còn **một repo
> Private `halo`**, nên chỉ cần **một deploy key** và **một bí danh** trong
> `~/.ssh/config` — thay vì hai bộ như dự tính (bẫy #25: một deploy key chỉ gắn được
> vào đúng một repo trên toàn GitHub).

Giải nén `halo-python.zip` ra `C:\Users\<bạn>\source\halo` rồi mở PowerShell tại đó:

```powershell
cd $env:USERPROFILE\source\halo

# ★ KIỂM TRA .gitignore TRƯỚC KHI COMMIT — đây là bước không sửa lại được
type .gitignore | findstr /C:".env" /C:"data/"
#    phải thấy cả hai dòng. Lỡ đẩy APP_KEY lên thì xóa file KHÔNG đủ:
#    khóa nằm trong lịch sử git vĩnh viễn, phải sinh khóa mới và mã hóa lại
#    mật khẩu router ở MỌI địa điểm.

git init -b main
git add .
git status --short | findstr ".env"     # phải KHÔNG in ra gì (trừ .env.example)
git commit -m "Halo: gop halo-admin + halo-ads thanh mot app Python/PostgreSQL"
```

Tạo repo **Private** tên `halo` trên GitHub (web), rồi:

```powershell
git remote add origin git@github.com:<tài-khoản>/halo.git
git push -u origin main
```

Máy Windows dùng khóa tài khoản `~/.ssh/id_github` (có passphrase) — đúng như bố trí
đã chốt.

---

## Bước 2 — Deploy key cho máy chủ (chỉ đọc, một repo)

Trên `server-001`:

```bash
ssh server-001
ssh-keygen -t ed25519 -f ~/.ssh/deploy_halo -C "deploy-key halo server-001" -N ""
#            ↑ KHÔNG passphrase: kịch bản cập nhật phải tự chạy được.
#              Bù lại bằng: quyền CHỈ ĐỌC + chỉ dùng được cho đúng repo halo.
cat ~/.ssh/deploy_halo.pub
```

Dán nội dung đó vào GitHub: repo `halo` → **Settings → Deploy keys → Add deploy key**
→ **KHÔNG tích** "Allow write access".

Rồi khai bí danh SSH — **`IdentitiesOnly yes` là bắt buộc**, thiếu nó thì ssh chào khóa
tài khoản trước, GitHub cho qua, mọi thứ vẫn chạy nên không ai phát hiện deploy key
chưa bao giờ được dùng:

```bash
sudo -v
tee -a ~/.ssh/config >/dev/null <<'EOF'

Host gh-halo
    HostName github.com
    User git
    IdentityFile ~/.ssh/deploy_halo
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config

# Ghim vân tay GitHub trước, không gõ "yes" mù
ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts
ssh-keygen -lf ~/.ssh/known_hosts | grep github
#   phải khớp: SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU
```

**Kiểm chứng deploy key (bước hay bị bỏ qua nhất):**

```bash
ssh -T gh-halo
```

Phải chào bằng **tên repo**:
`Hi <tài-khoản>/halo! You've successfully authenticated...`

Nếu nó chào bằng **tên tài khoản** thì deploy key KHÔNG được dùng — sai `IdentitiesOnly`.

---

## Bước 3 — Clone và cài

```bash
git clone gh-halo:<tài-khoản>/halo.git /srv/app/halo
cd /srv/app/halo
sudo bash install.sh
```

Script in ra 8 bước. Bước 7 in **tài khoản quản trị và mật khẩu — chép lại ngay**,
mật khẩu chỉ hiện một lần.

**Kiểm chứng:**

```bash
curl -s http://127.0.0.1:8081/khoe          # phải in: ok
systemctl status halo-web halo-worker --no-pager | grep -E "Active|●"
sudo ss -tulpn | grep 8081                  # phải là 127.0.0.1:8081, KHÔNG phải 0.0.0.0
```

Dòng cuối quan trọng: uvicorn chỉ được nghe trên localhost, mọi thứ vào qua nginx.

Từ máy Windows mở: **http://192.168.1.10/** → đăng nhập → **đổi mật khẩu ngay**.

---

## Bước 4 — Chép `.env` ra chỗ an toàn (làm NGAY, đừng để hôm sau)

```bash
sudo cp /srv/app/halo/.env /srv/backup/halo/env-$(date +%F)
sudo chmod 600 /srv/backup/halo/env-*
```

Rồi chép thêm một bản ra **ngoài máy này** (USB hoặc két). Lý do:

> `APP_KEY` trong `.env` là khóa AES-256-GCM mã hóa mật khẩu router của mọi địa điểm.
> Có bản dump CSDL mà không có `APP_KEY` thì mật khẩu router coi như mất — phải tới
> từng site đặt lại. Bản dump và `.env` phải nằm **cùng một chỗ sao lưu**.

Gắn Halo vào kịch bản sao lưu sẵn có:

```bash
sudo tee -a /usr/local/bin/sao-luu.sh >/dev/null <<'EOF'

# --- Halo ---
mkdir -p /srv/backup/halo
sudo -u postgres pg_dump halo | gzip > /srv/backup/halo/halo-$(date +%F).sql.gz
cp /srv/app/halo/.env /srv/backup/halo/env-$(date +%F)
tar czf /srv/backup/halo/banners-$(date +%F).tgz -C /srv/app/halo data/banners
find /srv/backup/halo -type f -mtime +30 -delete
EOF
sudo bash /usr/local/bin/sao-luu.sh && ls -lh /srv/backup/halo/
```

---

## Bước 5 — WireGuard hub

```bash
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
ip -brief addr show wg0                 # phải thấy 10.90.0.1/24
sudo cat /etc/wireguard/public.key      # ← khóa công khai của VPS/máy chủ, giữ lại
```

`ListenPort = 51820` để trùng luật UFW đã mở sẵn — khỏi phải sửa firewall.
Viết bằng `tee` + heredoc chứ không mở `nano` (nano tự thụt lề đã từng làm hỏng netplan
trên chính máy này), và `sudo -v` trước khi dán khối nhiều dòng.

---

## Bước 6 — Nối site đầu tiên

1. Trong Halo: **Quản trị** → thêm khách hàng → thêm địa điểm (hệ thống tự cấp
   `10.90.0.2` và sinh cặp khóa cho router).
2. Bấm **Khối lệnh cho router**, điền vào ô:
   - khóa công khai của máy chủ (lấy ở bước 5)
   - endpoint: hiện tại là `192.168.1.10` (cùng LAN); sau khi có IP tĩnh thì đổi.
3. Trên router: **Ctrl+X bật Safe Mode** → dán khối lệnh → kiểm tra vẫn vào được →
   Ctrl+X lần nữa để chốt.
4. Khai peer ở máy chủ:
   ```bash
   sudo wg set wg0 peer <KHÓA CÔNG KHAI CỦA ROUTER> allowed-ips 10.90.0.2/32
   sudo wg-quick save wg0
   ping -c3 10.90.0.2
   ```
5. Trong Halo: **Quản trị** → nhập mật khẩu REST và SFTP của router (đúng mật khẩu đã
   đổi trong khối `.rsc`) → **Lưu**.
6. Vào **Địa điểm** → **Quét trạng thái ngay** → phải thấy `online` và đúng tên thiết bị.
7. Bấm **Chạy bảng kiểm tra cấu hình** → 14 mục sẽ nói ngay router còn thiếu gì.

---

## Bước 7 — Nghiệm thu (đánh dấu từng dòng)

- [ ] `curl http://127.0.0.1:8081/khoe` → `ok`
- [ ] `sudo ss -tulpn | grep 8081` → chỉ `127.0.0.1`
- [ ] Vào được `http://192.168.1.10/` từ máy khác, đã đổi mật khẩu quản trị
- [ ] `systemctl is-enabled halo-web halo-worker` → `enabled` cả hai
- [ ] **Thử khởi động lại máy** (`sudo reboot`) → hai dịch vụ tự lên lại
- [ ] `journalctl -u halo-worker -n 20` → thấy dòng "Vòng quét: …" mỗi 60 giây
- [ ] `.env` đã có bản sao NGOÀI máy chủ
- [ ] `sudo bash /usr/local/bin/sao-luu.sh` chạy xong, có file trong `/srv/backup/halo/`
- [ ] `ip -brief addr show wg0` → `10.90.0.1/24`
- [ ] Đã truy ra hoặc đã tắt tiến trình lạ ở cổng 8080

---

## Cập nhật về sau

Sửa code ở máy Windows → `git push` → trên máy chủ:

```bash
cd /srv/app/halo && sudo bash deploy/cap-nhat.sh
```

Script tự dump CSDL trước khi pull, cài lại thư viện, chạy migration, khởi động lại
dịch vụ, gọi thử `/khoe`, và in sẵn lệnh quay lui nếu hỏng.

**Không bao giờ sửa code trực tiếp trên máy chủ** — lần pull sau sẽ đụng độ. Máy chủ
chỉ `git pull` một chiều.

---

## Khi dọn sang phòng máy chủ (giai đoạn 2)

Theo đúng mục 10 trong `may-chu-tai-cho.md`, phần riêng của Halo:

1. Hạ TTL bản ghi A xuống 300s **vài ngày trước**
2. Sửa `server_name` trong `/etc/nginx/sites-available/halo` thành `halo.<tên-miền>`
3. `sudo certbot --nginx -d halo.<tên-miền>`
4. NAT `51820/udp` về máy
5. Đổi `endpoint-address` của peer **từng site một**, site nào xong mới sang site sau
6. `sudo systemctl restart halo-web halo-worker` rồi chạy lại bảng kiểm tra mỗi site

Lưu ý tên miền: đã có một CMS mã nguồn mở tên Halo — tra Google sẽ lẫn. Dùng
`halo.<tên-miền-sẵn-có>` là gọn nhất, khỏi mua thêm.

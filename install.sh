#!/usr/bin/env bash
# Cài Halo lên máy chủ Ubuntu 24.04 (server-001).
#
# Chạy TỪ TRONG thư mục mã nguồn, ví dụ sau khi clone về /srv/app/halo:
#     cd /srv/app/halo && sudo bash install.sh
#
# Chạy lại nhiều lần được. ★ `.env` đã có thì KHÔNG BAO GIỜ bị ghi đè —
# ghi đè là mất APP_KEY, mất APP_KEY là không giải mã lại được mật khẩu router
# ở tất cả các địa điểm.

set -euo pipefail

DICH=/srv/app/halo
NGUOI_DUNG=halo
CSDL=halo
CSDL_USER=halo
CONG=8081          # 8080 đang bị một tiến trình Python lạ chiếm trên máy này

NGUON="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> 1/8 Cài gói hệ thống"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-dev build-essential \
    postgresql postgresql-contrib nginx certbot python3-certbot-nginx \
    wireguard-tools rsync curl >/dev/null

echo "==> 2/8 Tạo người dùng hệ thống $NGUOI_DUNG"
id -u "$NGUOI_DUNG" >/dev/null 2>&1 || useradd --system --home "$DICH" --shell /usr/sbin/nologin "$NGUOI_DUNG"

echo "==> 3/8 Chuẩn bị thư mục $DICH"
mkdir -p "$DICH"
if [ "$NGUON" != "$DICH" ]; then
    # Chỉ chép khi chạy từ nơi khác. Cách triển khai chuẩn của máy này là
    # `git pull` ngay tại /srv/app/halo, khi đó bước chép này tự bỏ qua.
    rsync -a --exclude '.env' --exclude 'data/' --exclude 'venv/' --exclude '__pycache__' \
        --exclude '.git' "$NGUON"/ "$DICH"/
else
    echo "    Đang chạy ngay tại $DICH — bỏ qua bước chép (đúng với luồng git pull)."
fi
mkdir -p "$DICH/data/banners"

echo "==> 4/8 Tạo môi trường Python"
[ -d "$DICH/venv" ] || python3 -m venv "$DICH/venv"
"$DICH/venv/bin/pip" install -q --upgrade pip
"$DICH/venv/bin/pip" install -q -r "$DICH/requirements.txt"

echo "==> 5/8 Tạo cơ sở dữ liệu PostgreSQL"
systemctl enable --now postgresql
MAT_KHAU_CSDL=""
if ! su postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='$CSDL_USER'\"" | grep -q 1; then
    MAT_KHAU_CSDL=$(openssl rand -hex 16)
    su postgres -c "psql -c \"CREATE USER $CSDL_USER WITH PASSWORD '$MAT_KHAU_CSDL';\"" >/dev/null
    echo "    Đã tạo user CSDL $CSDL_USER"
fi
if ! su postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='$CSDL'\"" | grep -q 1; then
    su postgres -c "psql -c \"CREATE DATABASE $CSDL OWNER $CSDL_USER;\"" >/dev/null
    echo "    Đã tạo CSDL $CSDL"
fi

echo "==> 6/8 Tạo file .env"
if [ -f "$DICH/.env" ]; then
    echo "    .env đã có — GIỮ NGUYÊN (ghi đè là mất APP_KEY, mất luôn mật khẩu router)."
else
    if [ -z "$MAT_KHAU_CSDL" ]; then
        echo "    !! User CSDL đã tồn tại từ trước nhưng chưa có .env."
        echo "       Sửa DATABASE_URL trong $DICH/.env bằng mật khẩu thật rồi chạy lại."
        MAT_KHAU_CSDL="DOI_MAT_KHAU_NAY"
    fi
    cat > "$DICH/.env" <<EOF
DATABASE_URL=postgresql+psycopg://$CSDL_USER:$MAT_KHAU_CSDL@localhost:5432/$CSDL
APP_KEY=$(openssl rand -hex 32)
SESSION_SECRET=$(openssl rand -hex 32)
WG_PREFIX=10.90.0.
VPS_WG_IP=10.90.0.1
WG_PORT=51820
COLLECT_SESSIONS=false
HASH_MAC=true
MONITOR_INTERVAL=60
MONITOR_TIMEOUT=8
SFTP_TIMEOUT=30
MAX_BANNER_BYTES=153600
MAX_BANNERS_PER_SITE=8
EOF
    chmod 600 "$DICH/.env"
    echo "    Đã sinh .env mới."
    echo "    ★ CHÉP NGAY /srv/app/halo/.env RA CHỖ SAO LƯU RIÊNG."
fi

chown -R "$NGUOI_DUNG:$NGUOI_DUNG" "$DICH"
chmod 600 "$DICH/.env"

echo "==> 7/8 Tạo bảng và tài khoản quản trị đầu tiên"
cd "$DICH"
sudo -u "$NGUOI_DUNG" "$DICH/venv/bin/python" -m scripts.init_db

echo "==> 8/8 Cài dịch vụ systemd và nginx"
cp "$DICH/deploy/halo-web.service" /etc/systemd/system/
cp "$DICH/deploy/halo-worker.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now halo-web halo-worker

if [ ! -f /etc/nginx/sites-available/halo ]; then
    cp "$DICH/deploy/nginx.conf" /etc/nginx/sites-available/halo
    ln -sf /etc/nginx/sites-available/halo /etc/nginx/sites-enabled/halo
    rm -f /etc/nginx/sites-enabled/default
fi
nginx -t && systemctl reload nginx

echo
echo "================================================================"
echo " Halo đã cài xong."
echo "   Kiểm tra ngay:  curl -s http://127.0.0.1:$CONG/khoe"
echo "   Từ máy khác  :  http://192.168.1.10/"
echo "   Nhật ký      :  journalctl -u halo-web -f"
echo "                   journalctl -u halo-worker -f"
echo
echo " Việc còn phải làm:"
echo "   1. Chép .env ra chỗ sao lưu riêng (MẤT APP_KEY = MẤT MẬT KHẨU ROUTER)."
echo "   2. Dựng WireGuard hub 10.90.0.1 (xem README mục 6)."
echo "   3. Gắn sao lưu CSDL vào /usr/local/bin/sao-luu.sh đang có sẵn."
echo "   4. Khi có IP tĩnh: đổi server_name trong /etc/nginx/sites-available/halo"
echo "      rồi chạy certbot."
echo "================================================================"

#!/usr/bin/env bash
# Cập nhật Halo sau khi đã đẩy mã mới lên GitHub.
#
#     cd /srv/app/halo && sudo bash deploy/cap-nhat.sh
#
# Trên máy chủ CHỈ `git pull` một chiều: sửa code ở máy làm việc → đẩy lên →
# máy chủ kéo về. Không bao giờ sửa trực tiếp trên máy chủ, vì lần pull sau sẽ đụng độ.

set -euo pipefail

DICH=/srv/app/halo
NGUOI_DUNG=halo
cd "$DICH"

echo "==> Sao lưu CSDL trước khi cập nhật"
NGAY=$(date +%F-%H%M)
mkdir -p /srv/backup/halo
su postgres -c "pg_dump halo" | gzip > "/srv/backup/halo/halo-$NGAY.sql.gz"
echo "    /srv/backup/halo/halo-$NGAY.sql.gz"

echo "==> Kéo mã mới"
BAN_CU=$(sudo -u "$NGUOI_DUNG" git rev-parse --short HEAD)
sudo -u "$NGUOI_DUNG" git pull --ff-only
BAN_MOI=$(sudo -u "$NGUOI_DUNG" git rev-parse --short HEAD)
echo "    $BAN_CU → $BAN_MOI"

if [ "$BAN_CU" = "$BAN_MOI" ]; then
    echo "    Không có gì mới. Dừng."
    exit 0
fi

echo "==> Cập nhật thư viện"
"$DICH/venv/bin/pip" install -q -r requirements.txt

echo "==> Cập nhật cấu trúc bảng (nếu có)"
if [ -n "$(ls -A alembic/versions 2>/dev/null)" ]; then
    sudo -u "$NGUOI_DUNG" "$DICH/venv/bin/alembic" upgrade head
else
    sudo -u "$NGUOI_DUNG" "$DICH/venv/bin/python" -m scripts.init_db
fi

echo "==> Cập nhật unit systemd và nginx nếu file có đổi"
cmp -s deploy/halo-web.service /etc/systemd/system/halo-web.service || {
    cp deploy/halo-web.service /etc/systemd/system/; CAN_RELOAD=1; }
cmp -s deploy/halo-worker.service /etc/systemd/system/halo-worker.service || {
    cp deploy/halo-worker.service /etc/systemd/system/; CAN_RELOAD=1; }
[ "${CAN_RELOAD:-0}" = "1" ] && systemctl daemon-reload

chown -R "$NGUOI_DUNG:$NGUOI_DUNG" "$DICH"
chmod 600 "$DICH/.env"

echo "==> Khởi động lại dịch vụ"
systemctl restart halo-web halo-worker
sleep 3

if curl -fsS http://127.0.0.1:8081/khoe >/dev/null; then
    echo "==> OK — Halo đang chạy bản $BAN_MOI"
else
    echo "!! Web không trả lời. Xem log rồi quay lui nếu cần:"
    echo "   journalctl -u halo-web -n 50 --no-pager"
    echo "   cd $DICH && sudo -u $NGUOI_DUNG git reset --hard $BAN_CU && sudo bash deploy/cap-nhat.sh"
    exit 1
fi

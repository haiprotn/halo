"""Cấp phát địa chỉ WireGuard, sinh cặp khóa, và in khối lệnh `.rsc` cho router.

★ Vì sao WireGuard: router quay ra máy chủ Halo nên không cần IP tĩnh và vượt được CGNAT
  của nhà mạng. Kèm luôn đường WinBox từ xa. Máy chủ Halo là hub 10.90.0.1, mỗi site một
  địa chỉ 10.90.0.X.

★ Khối `.rsc` sinh ra khóa tài khoản theo CẢ QUYỀN LẪN ĐỊA CHỈ NGUỒN. Tài khoản
  đẩy file không sửa được cấu hình mạng; tài khoản đọc trạng thái chỉ gọi được
  từ 10.90.0.1.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Site


def generate_keypair() -> tuple[str, str]:
    """Trả về (khóa riêng, khóa công khai) dạng base64 chuẩn WireGuard."""
    private = X25519PrivateKey.generate()
    priv_raw = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(priv_raw).decode(), base64.b64encode(pub_raw).decode()


def next_wg_ip(db: Session) -> str:
    """Cấp địa chỉ WireGuard tiếp theo còn trống trong dải cấu hình.

    .1 dành cho máy chủ Halo (hub), nên site bắt đầu từ .2.
    """
    used = {ip for (ip,) in db.execute(select(Site.wg_ip)).all()}
    prefix = settings.wg_prefix
    for last in range(2, 255):
        candidate = f"{prefix}{last}"
        if candidate not in used and candidate != settings.vps_wg_ip:
            return candidate
    raise RuntimeError(
        f"Hết địa chỉ trong dải {prefix}0/24. Mở rộng WG_PREFIX hoặc dùng dải /23."
    )


def router_rsc(
    site: Site,
    private_key: str,
    vps_public_key: str,
    vps_endpoint: str,
    rest_password: str,
    sftp_password: str,
    wg_port: int | None = None,
) -> str:
    """Sinh khối lệnh dán thẳng vào router (bật Safe Mode — Ctrl+X — trước khi dán)."""
    vps_ip = settings.vps_wg_ip
    wg_port = wg_port or settings.wg_port
    # Giá trị mặc định của cột chỉ được áp khi ghi vào CSDL. Nếu ai đó sinh khối .rsc
    # từ một đối tượng Site chưa lưu, không được để in ra `port=None`.
    rest_port = site.rest_port or 443
    sftp_port = site.sftp_port or 22
    return f"""# ============================================================
# Khối cấu hình cho địa điểm: {site.name}
# BẬT SAFE MODE TRƯỚC KHI DÁN: nhấn Ctrl+X trong terminal WinBox.
# Dán xong kiểm tra vào được rồi mới nhấn Ctrl+X lần nữa để chốt.
# ============================================================

# --- 1. Đường hầm WireGuard về máy chủ Halo ---
/interface wireguard
add name=wg-vps listen-port={wg_port} private-key="{private_key}"
/ip address
add address={site.wg_ip}/32 interface=wg-vps network={vps_ip}
/interface wireguard peers
add interface=wg-vps public-key="{vps_public_key}" \\
    endpoint-address={vps_endpoint} endpoint-port={wg_port} \\
    allowed-address={vps_ip}/32 persistent-keepalive=25s

# --- 2. Tài khoản đọc trạng thái (REST API) ---
# Chỉ gọi được từ máy chủ Halo. Đổi mật khẩu dưới đây rồi nhập lại vào cổng quản trị.
/user group
add name=vsp-read policy=read,api,rest-api,winbox
/user
add name={site.rest_user} group=vsp-read password="{rest_password}" address={vps_ip}/32

# --- 3. Tài khoản đẩy banner (SFTP) ---
/user group
add name=ads-push policy=ftp,read,write,ssh
/user
add name={site.sftp_user} group=ads-push password="{sftp_password}" address={vps_ip}/32

# --- 4. Mở dịch vụ cho đúng một địa chỉ nguồn ---
/ip service
set www-ssl disabled=no port={rest_port} address={vps_ip}/32
set ssh disabled=no port={sftp_port} address={vps_ip}/32
set api disabled=yes
set ftp disabled=yes
set telnet disabled=yes

# --- 5. Cho phép lưu lượng quản trị đi trong đường hầm ---
/ip firewall filter
add chain=input in-interface=wg-vps src-address={vps_ip}/32 action=accept \\
    comment="Quan tri tu may chu Halo" place-before=0

# ============================================================
# Sau khi dán, ở máy chủ Halo chạy:
#   wg set wg0 peer <KHÓA CÔNG KHAI CỦA ROUTER> allowed-ips {site.wg_ip}/32
#   wg-quick save wg0
# Khóa công khai của router: {site.wg_public_key or '(chưa sinh)'}
# ============================================================
"""

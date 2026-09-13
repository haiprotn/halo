"""Mật khẩu, mã hóa và phiên đăng nhập.

Hai loại mật khẩu, hai cách xử lý hoàn toàn khác nhau:

1. Mật khẩu TÀI KHOẢN NGƯỜI DÙNG → bcrypt (băm một chiều).
   Không bao giờ cần đọc lại, chỉ cần so khớp.

2. Mật khẩu ROUTER → AES-256-GCM (mã hóa hai chiều).
   Phải đọc lại được để gọi REST API và SFTP, nên không thể băm.
   Khóa nằm ở APP_KEY trong .env — MẤT .env LÀ MẤT LUÔN MẬT KHẨU ROUTER,
   phải sao lưu .env cùng bản dump CSDL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import string

import bcrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import settings

# ------------------------------------------------------------- mật khẩu người dùng
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


# ------------------------------------------------------------- mật khẩu router
def encrypt_secret(plain: str) -> str:
    """Mã hóa AES-256-GCM. Kết quả: base64 của (nonce 12 byte + bản mã + thẻ xác thực)."""
    if plain == "":
        return ""
    aes = AESGCM(settings.key_bytes())
    nonce = os.urandom(12)
    blob = nonce + aes.encrypt(nonce, plain.encode("utf-8"), None)
    return base64.b64encode(blob).decode("ascii")


def decrypt_secret(blob_b64: str) -> str:
    """Giải mã. GCM tự phát hiện nếu dữ liệu bị sửa → ném lỗi chứ không trả rác."""
    if not blob_b64:
        return ""
    blob = base64.b64decode(blob_b64)
    aes = AESGCM(settings.key_bytes())
    return aes.decrypt(blob[:12], blob[12:], None).decode("utf-8")


# ------------------------------------------------------------- phiên đăng nhập
_SESSION_SALT = "halo-session"


def _serializer() -> URLSafeTimedSerializer:
    secret = settings.session_secret or settings.app_key
    if not secret:
        raise RuntimeError("Chưa đặt SESSION_SECRET trong .env.")
    return URLSafeTimedSerializer(secret, salt=_SESSION_SALT)


def make_session_token(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def read_session_token(token: str, max_age_seconds: int = 12 * 3600) -> int | None:
    """Trả về user_id, hoặc None nếu chữ ký sai / cookie quá hạn."""
    try:
        data = _serializer().loads(token, max_age=max_age_seconds)
    except BadSignature:
        return None
    except Exception:
        return None
    uid = data.get("uid")
    return int(uid) if isinstance(uid, int) else None


# ------------------------------------------------------------- tiện ích khác
# Bỏ 0 O 1 l i để khách đọc phiếu in không nhầm — giữ nguyên quy ước của bản Node.
VOUCHER_ALPHABET = "".join(c for c in (string.ascii_lowercase + string.digits) if c not in "0o1li")


def random_code(length: int = 8) -> str:
    return "".join(secrets.choice(VOUCHER_ALPHABET) for _ in range(length))


def hash_mac(mac: str) -> str:
    """Băm MAC khi HASH_MAC=true — vẫn đếm được thiết bị duy nhất mà không lưu định danh thật."""
    key = settings.session_secret.encode("utf-8") or b"halo"
    return hmac.new(key, mac.upper().encode("utf-8"), hashlib.sha256).hexdigest()[:16]

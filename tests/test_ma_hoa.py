"""Mã hóa mật khẩu router và băm mật khẩu người dùng."""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import text

from app.db import engine
from app.security import decrypt_secret, encrypt_secret, hash_password, random_code, verify_password


def test_ma_hoa_giai_ma_khop():
    goc = "mat-khau-router-rat-dai-123"
    assert decrypt_secret(encrypt_secret(goc)) == goc


def test_moi_lan_ma_hoa_ra_ban_ma_khac_nhau():
    """AES-GCM dùng nonce ngẫu nhiên — hai bản mã khác nhau dù cùng một mật khẩu."""
    a, b = encrypt_secret("abc"), encrypt_secret("abc")
    assert a != b
    assert decrypt_secret(a) == decrypt_secret(b) == "abc"


def test_sua_ban_ma_thi_giai_ma_that_bai():
    """GCM có thẻ xác thực: dữ liệu bị sửa thì ném lỗi chứ không trả về rác."""
    import base64

    blob = bytearray(base64.b64decode(encrypt_secret("abc")))
    blob[-1] ^= 0x01
    with pytest.raises(InvalidTag):
        decrypt_secret(base64.b64encode(bytes(blob)).decode())


def test_mat_khau_khong_nam_dang_chu_thuong_trong_csdl(du_lieu_mau):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT rest_password_enc, sftp_password_enc FROM sites")).all()
    noi_dung = " ".join(str(c) for r in rows for c in r)
    assert "mat-khau-rest-A" not in noi_dung
    assert "mat-khau-sftp-A" not in noi_dung


def test_bcrypt():
    h = hash_password("mat-khau-nguoi-dung")
    assert h != "mat-khau-nguoi-dung"
    assert verify_password("mat-khau-nguoi-dung", h)
    assert not verify_password("sai", h)


def test_bo_ky_tu_ma_bo_cac_ky_tu_de_nhin_nham():
    """Bỏ 0 O 1 l i để khách đọc phiếu in không nhầm."""
    mau = "".join(random_code(20) for _ in range(200))
    for ky_tu in "0o1li":
        assert ky_tu not in mau

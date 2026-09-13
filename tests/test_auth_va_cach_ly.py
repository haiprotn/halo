"""Đăng nhập, chống dò mật khẩu, và cách ly đa khách hàng.

Cách ly phải kiểm ở TẦNG SERVER — không phải chỉ ẩn nút trên giao diện.
"""

from __future__ import annotations

from tests.conftest import dang_nhap


def test_sai_mat_khau_bi_tu_choi(client, du_lieu_mau):
    r = client.post("/dang-nhap", data={"username": "khach-a", "password": "sai"},
                    follow_redirects=False)
    assert r.status_code == 401
    # Thông báo không được nói rõ sai tên hay sai mật khẩu
    assert "không đúng" in r.text


def test_khoa_tam_thoi_sau_5_lan_sai(client, du_lieu_mau):
    for _ in range(5):
        client.post("/dang-nhap", data={"username": "khach-a", "password": "sai"},
                    follow_redirects=False)
    r = client.post("/dang-nhap", data={"username": "khach-a", "password": "mat-khau-a"},
                    follow_redirects=False)
    assert r.status_code == 429
    assert "khóa tạm thời" in r.text


def test_chua_dang_nhap_bi_day_ve_trang_dang_nhap(client, du_lieu_mau):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/dang-nhap"


def test_tenant_khong_thay_dia_diem_cua_tenant_khac(client, du_lieu_mau):
    dang_nhap(client, "khach-a", "mat-khau-a")
    site_b = du_lieu_mau["site_b"]

    # 404 chứ không phải 403: không lộ ra là địa điểm đó có tồn tại
    assert client.get(f"/dia-diem/{site_b.id}").status_code == 404
    assert client.get(f"/quang-cao/{site_b.id}").status_code == 404
    assert client.get(f"/voucher/{site_b.id}").status_code == 404

    # Địa điểm của chính mình thì vào được
    assert client.get(f"/dia-diem/{du_lieu_mau['site_a'].id}").status_code == 200


def test_tenant_khong_vao_duoc_trang_quan_tri(client, du_lieu_mau):
    dang_nhap(client, "khach-a", "mat-khau-a")
    assert client.get("/quan-tri").status_code == 403
    assert client.post("/quan-tri/khach-hang", data={"name": "gia mao"}).status_code == 403


def test_admin_thay_het(client, du_lieu_mau):
    dang_nhap(client, "admin", "mat-khau-admin")
    assert client.get("/quan-tri").status_code == 200
    assert client.get(f"/dia-diem/{du_lieu_mau['site_a'].id}").status_code == 200
    assert client.get(f"/dia-diem/{du_lieu_mau['site_b'].id}").status_code == 200


def test_mat_khau_router_khong_lo_ra_giao_dien(client, du_lieu_mau):
    dang_nhap(client, "admin", "mat-khau-admin")
    html = client.get("/quan-tri").text
    assert "mat-khau-rest-A" not in html
    assert "mat-khau-sftp-A" not in html

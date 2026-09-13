"""Quy trình quản trị: thêm khách hàng → địa điểm → tài khoản → khối lệnh router."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Site, Tenant, User
from app.security import decrypt_secret
from app.services import wireguard as wg
from tests.conftest import dang_nhap


def test_them_dia_diem_tu_cap_ip_va_sinh_khoa(client, db, du_lieu_mau):
    dang_nhap(client, "admin", "mat-khau-admin")
    kh = du_lieu_mau["kh_a"]

    r = client.post("/quan-tri/dia-diem",
                    data={"tenant_id": str(kh.id), "name": "Nha hang C",
                          "address": "Ben Cau", "hotspot_dir": "hotspot-vn"},
                    follow_redirects=False)
    assert r.status_code == 303

    site = db.scalar(select(Site).where(Site.name == "Nha hang C"))
    assert site is not None
    assert site.wg_ip == "10.90.0.4"          # .1 là VPS, .2 và .3 đã dùng
    assert site.wg_public_key
    assert site.wg_private_key_enc
    assert decrypt_secret(site.wg_private_key_enc)  # giải mã lại được


def test_ip_wireguard_khong_bao_gio_trung(db, du_lieu_mau):
    dung = set()
    for _ in range(5):
        ip = wg.next_wg_ip(db)
        assert ip not in dung
        dung.add(ip)
        db.add(Site(tenant_id=du_lieu_mau["kh_a"].id, name=f"s{ip}", wg_ip=ip))
        db.flush()
    assert "10.90.0.1" not in dung, "Địa chỉ .1 dành cho VPS"


def test_khoi_lenh_rsc_khoa_tai_khoan_theo_dia_chi_nguon(client, du_lieu_mau):
    dang_nhap(client, "admin", "mat-khau-admin")
    site = du_lieu_mau["site_a"]

    rsc = client.get(f"/quan-tri/dia-diem/{site.id}/rsc").text

    assert "/interface wireguard" in rsc
    assert f"address={ '10.90.0.1' }/32" in rsc, "Tài khoản phải bị khóa theo địa chỉ nguồn"
    assert "policy=ftp,read,write,ssh" in rsc
    assert "Ctrl+X" in rsc, "Phải nhắc bật Safe Mode trước khi dán"
    assert "wg set wg0 peer" in rsc, "Phải nhắc bước khai peer ở VPS"
    assert site.wg_ip in rsc


def test_tao_tai_khoan_tenant_phai_gan_khach_hang(client, du_lieu_mau):
    dang_nhap(client, "admin", "mat-khau-admin")
    r = client.post("/quan-tri/tai-khoan",
                    data={"username": "moi", "role": "tenant", "tenant_id": "", "password": ""},
                    follow_redirects=False)
    assert "loi=" in r.headers["location"]


def test_cap_nhat_dia_diem_de_trong_mat_khau_thi_giu_nguyen(client, db, du_lieu_mau):
    dang_nhap(client, "admin", "mat-khau-admin")
    site = du_lieu_mau["site_a"]
    cu = site.rest_password_enc

    client.post(f"/quan-tri/dia-diem/{site.id}",
                data={"name": site.name, "address": "", "hotspot_dir": "hotspot-vn",
                      "rest_user": "vsp", "rest_port": "443", "rest_password": "",
                      "sftp_user": "ads", "sftp_port": "22", "sftp_password": "",
                      "active": "on"},
                follow_redirects=False)
    db.refresh(site)
    assert site.rest_password_enc == cu
    assert decrypt_secret(site.rest_password_enc) == "mat-khau-rest-A"


def test_doi_mat_khau_router_thi_ma_hoa_lai(client, db, du_lieu_mau):
    dang_nhap(client, "admin", "mat-khau-admin")
    site = du_lieu_mau["site_a"]

    client.post(f"/quan-tri/dia-diem/{site.id}",
                data={"name": site.name, "address": "", "hotspot_dir": "hotspot-vn",
                      "rest_user": "vsp", "rest_port": "443", "rest_password": "mat-khau-moi",
                      "sftp_user": "ads", "sftp_port": "22", "sftp_password": "",
                      "active": "on"},
                follow_redirects=False)
    db.refresh(site)
    assert decrypt_secret(site.rest_password_enc) == "mat-khau-moi"
    assert "mat-khau-moi" not in site.rest_password_enc


def test_khong_tu_khoa_tai_khoan_cua_chinh_minh(client, db, du_lieu_mau):
    dang_nhap(client, "admin", "mat-khau-admin")
    admin = db.scalar(select(User).where(User.username == "admin"))

    r = client.post(f"/quan-tri/tai-khoan/{admin.id}/bat-tat", follow_redirects=False)
    assert "loi=" in r.headers["location"]
    db.refresh(admin)
    assert admin.active


def test_nhat_ky_ghi_lai_thao_tac(client, db, du_lieu_mau):
    dang_nhap(client, "admin", "mat-khau-admin")
    client.post("/quan-tri/khach-hang", data={"name": "Khach C"}, follow_redirects=False)

    html = client.get("/nhat-ky").text
    assert "them_khach_hang" in html
    assert db.scalar(select(Tenant).where(Tenant.name == "Khach C")) is not None

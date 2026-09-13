"""Voucher: sinh, đẩy (có quay lui), đồng bộ, cảnh báo trần license."""

from __future__ import annotations

import pytest

from app.models import Voucher, now_utc
from app.services import vouchers as vsvc
from app.services.routeros import RouterOS, RouterOSError
from tests.mock_router import MockRouter


def _ros(mock: MockRouter) -> RouterOS:
    return RouterOS("https://router/rest", "vsp", "x", client=mock.client())


def test_sinh_ma_khong_trung_va_o_trang_thai_moi(db, du_lieu_mau):
    site = du_lieu_mau["site_a"]
    lo = vsvc.generate_codes(db, site.id, "1h", 50)
    db.commit()

    ds = db.query(Voucher).filter(Voucher.site_id == site.id).all()
    assert len(ds) == 50
    assert len({v.code for v in ds}) == 50, "Mã bị trùng"
    assert all(v.state == "moi" for v in ds), "Mã mới sinh chưa được coi là đã lên router"
    assert lo.quantity == 50


def test_day_thanh_cong(db, du_lieu_mau):
    site = du_lieu_mau["site_a"]
    vsvc.generate_codes(db, site.id, "1h", 5)
    db.commit()
    ds = db.query(Voucher).filter(Voucher.site_id == site.id).all()

    mock = MockRouter()
    with _ros(mock) as ros:
        kq = vsvc.push_batch(db, ros, ds)
    db.commit()

    assert kq == {"ok": 5, "failed": 0, "errors": []}
    assert sorted(mock.ten_user()) == sorted(v.code for v in ds)
    assert all(v.state == "tren_router" and v.pushed_at for v in ds)


def test_gan_goi_cuoc_loi_thi_quay_lui_khong_de_lai_tai_khoan_mo_coi(db, du_lieu_mau):
    """Bẫy #18 — tài khoản tạo xong mà không gán được gói cước là tài khoản KHÔNG GIỚI HẠN."""
    site = du_lieu_mau["site_a"]
    vsvc.generate_codes(db, site.id, "goi-khong-ton-tai", 3)
    db.commit()
    ds = db.query(Voucher).filter(Voucher.site_id == site.id).all()

    mock = MockRouter(loi_gan_goi_cuoc=True)
    with _ros(mock) as ros:
        kq = vsvc.push_batch(db, ros, ds)
    db.commit()

    assert kq["ok"] == 0 and kq["failed"] == 3
    assert mock.ten_user() == [], "Còn tài khoản mồ côi trên router — khách vẫn vào được bằng mã đó"
    assert all(v.state == "loi" for v in ds)
    assert all("quay lui" in v.last_error for v in ds)


def test_khong_quay_lui_duoc_thi_bao_that_ro(db, du_lieu_mau):
    """Trường hợp xấu nhất: phải nói thẳng là còn tài khoản không giới hạn trên router."""
    site = du_lieu_mau["site_a"]
    vsvc.generate_codes(db, site.id, "sai-goi", 1)
    db.commit()
    v = db.query(Voucher).filter(Voucher.site_id == site.id).one()

    mock = MockRouter(loi_gan_goi_cuoc=True, loi_xoa_user=True)
    with _ros(mock) as ros:
        with pytest.raises(RouterOSError) as loi:
            vsvc.push_voucher(ros, v)

    assert "không có giới hạn" in str(loi.value)
    assert "xóa tay" in str(loi.value)


def test_dong_bo_khong_coi_ma_moi_la_da_xoa(db, du_lieu_mau):
    """Bẫy #19 — mã `moi` và `loi` cũng không có trên router, nhưng KHÔNG phải đã xóa."""
    site = du_lieu_mau["site_a"]
    vsvc.generate_codes(db, site.id, "1h", 4)
    db.commit()
    ds = db.query(Voucher).filter(Voucher.site_id == site.id).order_by(Voucher.code).all()

    # Hai mã đã lên router, một mã lỗi, một mã còn mới
    mock = MockRouter()
    with _ros(mock) as ros:
        vsvc.push_batch(db, ros, ds[:2])
    ds[2].state = "loi"
    db.commit()

    # Trên router có người xóa tay mất một mã
    mock.data["user-manager/user"] = [
        u for u in mock.data["user-manager/user"] if u["name"] != ds[0].code
    ]

    with _ros(mock) as ros:
        kq = vsvc.sync_site(db, ros, site.id)
    db.commit()

    assert ds[0].state == "da_xoa", "Mã đã từng lên router và nay biến mất → đã xóa"
    assert ds[1].state == "tren_router"
    assert ds[2].state == "loi", "Mã lỗi không được biến thành `da_xoa`"
    assert ds[3].state == "moi", "Mã mới không được biến thành `da_xoa`"
    assert kq["chuyen_da_xoa"] == 1


def test_dong_bo_sua_lai_ma_da_co_tren_router(db, du_lieu_mau):
    """Mã ghi là `moi` nhưng thật ra đã có trên router → đồng bộ lại cho đúng."""
    site = du_lieu_mau["site_a"]
    vsvc.generate_codes(db, site.id, "1h", 1)
    db.commit()
    v = db.query(Voucher).filter(Voucher.site_id == site.id).one()

    mock = MockRouter()
    mock.data["user-manager/user"].append({".id": "*99", "name": v.code, "profile": "1h"})

    with _ros(mock) as ros:
        vsvc.sync_site(db, ros, site.id)
    db.commit()

    assert v.state == "tren_router"
    assert v.pushed_at is not None


def test_canh_bao_tran_license_noi_ro_la_tran_phien_dong_thoi():
    """Bẫy #20 — 50 là trần PHIÊN ĐỒNG THỜI, không phải trần số voucher."""
    canh_bao = vsvc.session_warning(um_sessions=10, active_vouchers=300, license_level=5)
    assert canh_bao is not None
    assert "PHIÊN ĐỒNG THỜI" in canh_bao
    assert "KHÔNG phải lỗi" in canh_bao

    cham_tran = vsvc.session_warning(um_sessions=50, active_vouchers=10, license_level=5)
    assert "chạm trần" in cham_tran.lower()

    assert vsvc.session_warning(um_sessions=5, active_vouchers=10, license_level=5) is None
    assert vsvc.session_warning(um_sessions=999, active_vouchers=999, license_level=6) is None


def test_xoa_ma_het_han(db, du_lieu_mau):
    site = du_lieu_mau["site_a"]
    vsvc.generate_codes(db, site.id, "1h", 3)
    db.commit()
    ds = db.query(Voucher).filter(Voucher.site_id == site.id).all()

    mock = MockRouter()
    with _ros(mock) as ros:
        vsvc.push_batch(db, ros, ds)
        for v in ds[:2]:
            v.state = "het_han"
        db.flush()
        kq = vsvc.delete_expired(db, ros, site.id)
    db.commit()

    assert kq == {"da_xoa": 2, "loi": 0}
    assert len(mock.ten_user()) == 1
    assert sum(1 for v in ds if v.state == "da_xoa") == 2


def test_trang_voucher_hien_thi_duoc(client, du_lieu_mau, router_gia):
    from tests.conftest import dang_nhap

    router_gia(MockRouter())
    dang_nhap(client, "khach-a", "mat-khau-a")
    r = client.get(f"/voucher/{du_lieu_mau['site_a'].id}")
    assert r.status_code == 200
    assert "Sinh lô mã mới" in r.text
    # Danh sách gói cước phải đọc từ router, không hardcode
    assert "7d" in r.text


def test_sinh_va_day_qua_giao_dien(client, db, du_lieu_mau, router_gia):
    from tests.conftest import dang_nhap

    mock = router_gia(MockRouter())
    dang_nhap(client, "khach-a", "mat-khau-a")
    site = du_lieu_mau["site_a"]

    r = client.post(
        f"/voucher/{site.id}/sinh",
        data={"profile_name": "1h", "quantity": "7", "note": "le tan", "day_luon": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(mock.ten_user()) == 7
    assert db.query(Voucher).filter(Voucher.state == "tren_router").count() == 7


def test_expire_stale(db, du_lieu_mau):
    import datetime as dt

    site = du_lieu_mau["site_a"]
    vsvc.generate_codes(db, site.id, "1h", 2)
    db.commit()
    ds = db.query(Voucher).filter(Voucher.site_id == site.id).all()
    for v in ds:
        v.state = "tren_router"
        v.created_at = now_utc() - dt.timedelta(days=40)
    db.commit()

    assert vsvc.expire_stale(db, site.id, validity_days=30) == 2
    db.commit()
    assert all(v.state == "het_han" for v in ds)

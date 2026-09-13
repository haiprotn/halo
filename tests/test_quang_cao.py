"""Sinh ads.js và luồng quản lý banner."""

from __future__ import annotations

import json
import re
from urllib.parse import unquote

from app.models import Banner
from app.services import adsjs
from tests.conftest import dang_nhap


def _du_lieu_trong_js(js: str) -> list[dict]:
    khoi = re.search(r"var ADS = (\[.*?\]);", js, re.S)
    assert khoi, "Không tìm thấy mảng ADS trong ads.js"
    return json.loads(khoi.group(1))


def test_ads_js_chua_dung_du_lieu_lich_chieu(db, du_lieu_mau):
    site = du_lieu_mau["site_a"]
    db.add(Banner(
        site_id=site.id, filename="ad-1.jpg", days="0,1,2,3,4",
        start_time="11:00", end_time="14:00", slots="login", link_url="https://vd.vn",
    ))
    db.add(Banner(site_id=site.id, filename="ad-2.jpg", active=False))  # đang tắt
    db.commit()

    banners = db.query(Banner).filter(Banner.site_id == site.id).all()
    js = adsjs.render(banners, site.name)
    data = _du_lieu_trong_js(js)

    assert len(data) == 1, "Banner đang tắt không được đưa xuống router"
    assert data[0] == {
        "file": "ad-1.jpg", "days": [0, 1, 2, 3, 4],
        "from": "11:00", "to": "14:00", "slots": ["login"], "link": "https://vd.vn",
    }


def test_ads_js_doi_dung_quy_uoc_thu_trong_tuan():
    """Python 0=Thứ Hai, JavaScript getDay() 0=Chủ Nhật — sai chỗ này là lệch đúng một ngày."""
    js = adsjs.render([], "x")
    assert "(now.getDay() + 6) % 7" in js


def test_ads_js_gan_onerror_truoc_khi_gan_src():
    """Bẫy đã gặp: src rỗng kích hoạt onerror ngay lúc tải trang, khối quảng cáo bị ẩn."""
    js = adsjs.render([], "x")
    vi_tri_onerror = js.index("img.onerror")
    vi_tri_src = js.index("img.src = ad.file")
    assert vi_tri_onerror < vi_tri_src


def test_ads_js_xu_ly_khung_gio_vat_qua_nua_dem():
    js = adsjs.render([], "x")
    assert "khung giờ vắt qua nửa đêm" in js


def test_tai_len_anh_qua_lon_bi_tu_choi(client, du_lieu_mau):
    dang_nhap(client, "khach-a", "mat-khau-a")
    site = du_lieu_mau["site_a"]

    to_dung = b"\xff\xd8\xff" + b"0" * 300_000  # ~300 KB, vượt trần 150 KB
    r = client.post(
        f"/quang-cao/{site.id}/tai-len",
        files={"file": ("to.jpg", to_dung, "image/jpeg")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "vượt trần" in unquote(r.headers["location"])


def test_tai_len_sai_dinh_dang_bi_tu_choi(client, du_lieu_mau):
    dang_nhap(client, "khach-a", "mat-khau-a")
    site = du_lieu_mau["site_a"]

    r = client.post(
        f"/quang-cao/{site.id}/tai-len",
        files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Chỉ nhận ảnh" in unquote(r.headers["location"])


def test_tai_len_va_sua_lich_chieu(client, db, du_lieu_mau):
    dang_nhap(client, "khach-a", "mat-khau-a")
    site = du_lieu_mau["site_a"]

    r = client.post(
        f"/quang-cao/{site.id}/tai-len",
        files={"file": ("banner.jpg", b"\xff\xd8\xff" + b"x" * 500, "image/jpeg")},
        follow_redirects=False,
    )
    assert r.status_code == 303

    b = db.query(Banner).filter(Banner.site_id == site.id).one()
    r = client.post(
        f"/quang-cao/{site.id}/sua/{b.id}",
        data={"days": ["0", "1"], "slots": ["login"], "start_time": "11:00",
              "end_time": "14:00", "sort_order": "1", "active": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    db.refresh(b)
    assert b.days == "0,1"
    assert b.slots == "login"
    assert b.start_time == "11:00"


def test_gio_khong_hop_le_bi_thay_bang_mac_dinh(client, db, du_lieu_mau):
    dang_nhap(client, "khach-a", "mat-khau-a")
    site = du_lieu_mau["site_a"]
    db.add(Banner(site_id=site.id, filename="ad-x.jpg"))
    db.commit()
    b = db.query(Banner).filter(Banner.site_id == site.id).one()

    client.post(
        f"/quang-cao/{site.id}/sua/{b.id}",
        data={"days": ["0"], "slots": ["login"], "start_time": "25:99",
              "end_time": "abc", "sort_order": "0", "active": "on"},
        follow_redirects=False,
    )
    db.refresh(b)
    assert b.start_time == "00:00"
    assert b.end_time == "23:59"


def test_day_khi_chua_khai_bao_mat_khau_sftp_bao_loi_ro_rang(client, du_lieu_mau):
    """Không được làm sập app, phải ghi nhật ký và báo lỗi rõ ràng."""
    dang_nhap(client, "khach-b", "mat-khau-b")
    site_b = du_lieu_mau["site_b"]  # site này chưa có mật khẩu SFTP

    r = client.post(f"/quang-cao/{site_b.id}/day", follow_redirects=False)
    assert r.status_code == 303
    assert "loi=" in r.headers["location"]

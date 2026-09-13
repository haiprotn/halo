"""Giám sát: cập nhật trạng thái, số đo, và cảnh báo dạng trạng thái."""

from __future__ import annotations

from app.models import Alert, Sample, SiteStatus
from app.services import monitor
from tests.mock_router import MockRouter, cau_hinh_sach


def test_quet_thanh_cong_ghi_trang_thai_va_so_do(db, du_lieu_mau, router_gia):
    router_gia(MockRouter())
    site = du_lieu_mau["site_a"]

    st = monitor.scan_site(db, site)
    db.commit()

    assert st.online
    assert st.board_name == "L009UiGS-RM"
    assert st.architecture == "arm"
    assert st.hotspot_active == 3
    assert st.um_sessions == 1
    assert db.query(Sample).filter(Sample.site_id == site.id).count() == 1


def test_router_mat_ket_noi_chi_mo_mot_canh_bao(db, du_lieu_mau, monkeypatch):
    """Cảnh báo là TRẠNG THÁI: quét 5 vòng khi router chết vẫn chỉ một dòng cảnh báo."""
    from app.services import routeros as ros_mod

    def client_hong(username, password, timeout):
        import httpx

        def handler(request):
            raise httpx.ConnectError("network unreachable")

        return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://router")

    monkeypatch.setattr(ros_mod, "CLIENT_FACTORY", client_hong)
    site = du_lieu_mau["site_a"]

    for _ in range(5):
        monitor.scan_site(db, site)
    db.commit()

    mo = db.query(Alert).filter(Alert.site_id == site.id, Alert.closed_at.is_(None)).all()
    assert len(mo) == 1
    assert mo[0].kind == "offline"
    assert db.get(SiteStatus, site.id).online is False


def test_canh_bao_tu_dong_dong_khi_router_song_lai(db, du_lieu_mau, router_gia, monkeypatch):
    from app.services import routeros as ros_mod

    site = du_lieu_mau["site_a"]

    def client_hong(username, password, timeout):
        import httpx

        def handler(request):
            raise httpx.ConnectError("down")

        return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://router")

    monkeypatch.setattr(ros_mod, "CLIENT_FACTORY", client_hong)
    monitor.scan_site(db, site)
    db.commit()
    assert db.query(Alert).filter(Alert.closed_at.is_(None)).count() == 1

    router_gia(MockRouter())
    monitor.scan_site(db, site)
    db.commit()

    assert db.query(Alert).filter(Alert.closed_at.is_(None)).count() == 0
    assert db.query(Alert).filter(Alert.closed_at.isnot(None)).count() == 1


def test_canh_bao_khi_gan_tran_phien_user_manager(db, du_lieu_mau, router_gia):
    data = cau_hinh_sach()
    data["user-manager/session"] = [{"user": f"u{i}"} for i in range(48)]  # 48/50
    router_gia(MockRouter(data))

    monitor.scan_site(db, du_lieu_mau["site_a"])
    db.commit()

    loai = {a.kind for a in db.query(Alert).filter(Alert.closed_at.is_(None)).all()}
    assert "um_session_high" in loai


def test_canh_bao_khi_flash_gan_day(db, du_lieu_mau, router_gia):
    data = cau_hinh_sach()
    data["system/resource"]["free-hdd-space"] = 900_000   # < 2 MiB
    router_gia(MockRouter(data))

    monitor.scan_site(db, du_lieu_mau["site_a"])
    db.commit()

    loai = {a.kind for a in db.query(Alert).filter(Alert.closed_at.is_(None)).all()}
    assert "flash_low" in loai


def test_scan_all_khong_chet_vi_mot_site_hong(db, du_lieu_mau, router_gia):
    router_gia(MockRouter())
    kq = monitor.scan_all(db)
    assert kq["tong"] == 2
    assert kq["online"] == 2

"""Bảng kiểm tra cấu hình chạy trên router giả lập.

Router "sạch" phải đạt hết; router "lỗi" phải chỉ ra ĐÚNG những lỗi đã gặp thật.
"""

from __future__ import annotations

from app.services import checks
from app.services.routeros import RouterOS
from tests.mock_router import MockRouter, cau_hinh_loi, cau_hinh_sach


def _chay(data: dict) -> dict[str, dict]:
    mock = MockRouter(data)
    with RouterOS("https://router/rest", "vsp", "x", client=mock.client()) as ros:
        return {r["key"]: r for r in checks.run_all(ros)}


def test_router_sach_dat_het():
    kq = _chay(cau_hinh_sach())
    hong = [k for k, v in kq.items() if not v["ok"]]
    assert hong == [], f"Router sạch mà vẫn báo lỗi: {hong}"
    assert len(kq) == 14


def test_router_loi_bi_bat_dung_muc():
    kq = _chay(cau_hinh_loi())

    assert not kq["npk"]["ok"]              # thiếu gói user-manager (tải nhầm kiến trúc npk)
    assert not kq["um_enabled"]["ok"]
    assert not kq["radius_incoming"]["ok"]  # voucher hết hạn mà không đá được phiên
    assert not kq["login_by_trial"]["ok"]   # nút bấm-là-vào không hoạt động
    assert not kq["chap"]["ok"]             # http-pap gửi mật khẩu trần trên sóng mở
    assert not kq["idle_timeout"]["ok"]     # 2m: điện thoại bỏ túi là rớt
    assert not kq["dns"]["ok"]              # trần mặc định 100 truy vấn
    assert not kq["walled_garden"]["ok"]    # captive.apple.com lọt vào walled garden
    assert not kq["trial_leak"]["ok"]       # phiên T-... lọt vào User Manager
    assert not kq["mss"]["ok"]              # thiếu MSS clamp cho PPPoE
    assert not kq["mac_cookie"]["ok"]       # thủ phạm số một: bỏ qua trang chào
    assert not kq["cookie_lifetime"]["ok"]
    assert not kq["session_vs_trial"]["ok"]  # đặt bằng nhau = khóa khách cả ngày
    assert not kq["trial_profile"]["ok"]    # trial-user-profile=default thì hotspot từ chối


def test_moi_muc_hong_deu_co_goi_y_xu_ly():
    kq = _chay(cau_hinh_loi())
    for key, r in kq.items():
        if not r["ok"]:
            assert r["hint"], f"Mục {key} báo hỏng mà không nói cách xử lý"


def test_khong_doan_ten_doi_tuong():
    """Nguyên tắc vàng: đọc tên thật trên router, không dựa vào tên trong bản mẫu."""
    data = cau_hinh_sach()
    # Đổi tên mọi profile — cấu hình vẫn đúng, chỉ khác tên
    data["ip/hotspot"][0]["profile"] = "prof-la"
    data["ip/hotspot/profile"][1]["name"] = "prof-la"
    data["ip/hotspot/profile"][1]["trial-user-profile"] = "goi-khach-la"
    data["ip/hotspot/user/profile"][1]["name"] = "goi-khach-la"

    kq = _chay(data)
    assert kq["login_by_trial"]["ok"]
    assert kq["idle_timeout"]["ok"], "Phải tìm user profile theo trial-user-profile, không theo tên cố định"
    assert kq["trial_profile"]["ok"]


def test_parse_duration():
    assert checks.parse_duration("30m") == 1800
    assert checks.parse_duration("1h30m") == 5400
    assert checks.parse_duration("8h") == 28800
    assert checks.parse_duration("0s") == 0
    # 'none' và để trống KHÔNG phải là 0 giây — đó là hai hành vi khác hẳn nhau
    assert checks.parse_duration("none") is None
    assert checks.parse_duration("") is None
    assert checks.parse_duration(None) is None

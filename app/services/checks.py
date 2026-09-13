"""Bảng kiểm tra cấu hình router — mỗi dòng là một lỗi đã gặp thật ngoài hiện trường.

10 mục đầu là checklist nghiệm thu trong `quy-trinh-usermanager.md`, nay chạy tự động.
4 mục sau là các tham số hay bị đặt sai trong bảng tham số chốt sau lab 09/2026.

★ Nguyên tắc vàng nhắc lại: KHÔNG đoán tên đối tượng. Mọi mục ở đây đều ĐỌC tên
  thật trên router rồi mới xét — không giả định profile tên `free-trial` hay `trial`.
"""

from __future__ import annotations

from typing import Any, Callable

from .routeros import RouterOS, RouterOSError

# Ba tên miền mà điện thoại dùng để dò xem có bị giữ lại không.
# Chúng PHẢI bị chặn thì cửa sổ đăng nhập mới tự bật — cho vào walled garden là
# tự tay tắt cơ chế này (bẫy #2).
CAPTIVE_PROBES = (
    "captive.apple.com",
    "connectivitycheck.gstatic.com",
    "msftconnecttest.com",
    "www.msftconnecttest.com",
    "connectivitycheck.android.com",
)


# ------------------------------------------------------------------ tiện ích đọc
def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "yes", "1"}


def parse_duration(text: str | None) -> int | None:
    """Đổi chuỗi thời lượng của RouterOS ('30m', '1h30m', '8h', 'none') sang giây.

    Trả về None khi là 'none' hoặc để trống — hai thứ này khác hẳn 0 giây.
    """
    if text is None:
        return None
    text = str(text).strip().lower()
    if text in {"", "none"}:
        return None
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1, "w": 604800}
    total, number = 0, ""
    for ch in text:
        if ch.isdigit():
            number += ch
        elif ch in units:
            if not number:
                return None
            total += int(number) * units[ch]
            number = ""
        else:
            return None
    if number:  # chuỗi chỉ có số, coi như giây
        total += int(number)
    return total


def _result(key: str, name: str, ok: bool, detail: str, hint: str = "", core: bool = True) -> dict:
    return {"key": key, "name": name, "ok": ok, "detail": detail, "hint": hint, "core": core}


def _server_profiles(ros: RouterOS) -> list[dict]:
    return ros.get_list("ip/hotspot/profile")


def _user_profiles(ros: RouterOS) -> list[dict]:
    return ros.get_list("ip/hotspot/user/profile")


def _active_server_profile(ros: RouterOS) -> dict:
    """Tìm server profile ĐANG ĐƯỢC hotspot server dùng — không đoán theo tên."""
    servers = ros.get_list("ip/hotspot")
    profiles = _server_profiles(ros)
    if servers:
        wanted = servers[0].get("profile")
        for p in profiles:
            if p.get("name") == wanted:
                return p
    # Không có hotspot server nào đang chạy: lấy profile khác 'default' nếu có
    for p in profiles:
        if p.get("name") != "default":
            return p
    return profiles[0] if profiles else {}


def _trial_user_profile(ros: RouterOS) -> dict:
    """User profile dành cho khách trial, lấy theo `trial-user-profile` của server profile."""
    server = _active_server_profile(ros)
    wanted = server.get("trial-user-profile", "")
    for p in _user_profiles(ros):
        if p.get("name") == wanted:
            return p
    return {}


# ------------------------------------------------------------------ 10 mục cốt lõi
def check_npk_architecture(ros: RouterOS) -> dict:
    res = ros.system_resource()
    arch = res.get("architecture-name", "?")
    board = res.get("board-name", "?")
    names = {p.get("name") for p in ros.packages()}
    ok = "user-manager" in names
    return _result(
        "npk",
        "Kiến trúc npk và gói User Manager",
        ok,
        f"Router {board}, kiến trúc {arch}. Gói đã cài: {', '.join(sorted(n for n in names if n))}",
        hint=(
            f"Không thấy gói `user-manager`. Thường là tải nhầm kiến trúc npk — router này là "
            f"`{arch}` (L009 là arm 32-bit, RB5009 là arm64). Router nhận file, reboot xong "
            "gói không xuất hiện và không báo lỗi gì."
        ),
    )


def check_um_enabled(ros: RouterOS) -> dict:
    try:
        um = ros.get_one("user-manager")
    except RouterOSError as exc:
        return _result("um_enabled", "User Manager đã bật", False, str(exc),
                       hint="Chạy: /user-manager set enabled=yes")
    ok = _truthy(um.get("enabled"))
    return _result(
        "um_enabled", "User Manager đã bật", ok,
        f"enabled = {um.get('enabled', '?')}",
        hint="Chạy: /user-manager set enabled=yes",
    )


def check_radius_incoming(ros: RouterOS) -> dict:
    inc = ros.get_one("radius/incoming")
    ok = _truthy(inc.get("accept"))
    return _result(
        "radius_incoming", "RADIUS incoming accept=yes", ok,
        f"accept = {inc.get('accept', '?')}",
        hint=(
            "Thiếu dòng này thì voucher hết hạn nhưng khách vẫn online tới khi tự ngắt — "
            "User Manager không đá được phiên (CoA/Disconnect). Chạy: /radius incoming set accept=yes"
        ),
    )


def check_login_by_trial_first(ros: RouterOS) -> dict:
    prof = _active_server_profile(ros)
    login_by = [x.strip() for x in str(prof.get("login-by", "")).split(",") if x.strip()]
    ok = bool(login_by) and login_by[0] == "trial"
    return _result(
        "login_by_trial", "`trial` đứng đầu trong login-by", ok,
        f"profile `{prof.get('name', '?')}` có login-by = {','.join(login_by) or '(trống)'}",
        hint="Thiếu `trial` = nút bấm-là-vào không hoạt động. Đặt login-by=trial,http-chap",
    )


def check_chap_not_pap(ros: RouterOS) -> dict:
    prof = _active_server_profile(ros)
    login_by = {x.strip() for x in str(prof.get("login-by", "")).split(",") if x.strip()}
    ok = "http-chap" in login_by and "http-pap" not in login_by
    return _result(
        "chap", "Voucher xác thực bằng http-chap (không dùng http-pap)", ok,
        f"login-by = {','.join(sorted(login_by)) or '(trống)'}",
        hint="http-pap gửi mật khẩu trần trên sóng WiFi mở. http-chap băm MD5 tại trình duyệt.",
    )


def check_idle_timeout(ros: RouterOS) -> dict:
    prof = _trial_user_profile(ros)
    raw = prof.get("idle-timeout")
    seconds = parse_duration(raw)
    ok = seconds is not None and seconds >= 600
    return _result(
        "idle_timeout", "idle-timeout ≥ 10 phút", ok,
        f"user profile `{prof.get('name', '?')}` có idle-timeout = {raw or 'none'}",
        hint=(
            "Ngắn quá thì điện thoại bỏ túi vài phút là rớt, khách kể lại là 'wifi chập chờn'. "
            "Giá trị chạy thật: 15m (bàn lab để none)."
        ),
    )


def check_dns_limits(ros: RouterOS) -> dict:
    dns = ros.get_one("ip/dns")
    queries = int(str(dns.get("max-concurrent-queries", 0)) or 0)
    ok = queries >= 500
    return _result(
        "dns", "DNS đã nâng trần trước khi lên quy mô", ok,
        f"max-concurrent-queries = {queries}, cache-size = {dns.get('cache-size', '?')}",
        hint=(
            "Mặc định 100 truy vấn đồng thời là quá thấp cho 100–500 khách. Đặt: "
            "/ip dns set max-concurrent-queries=500 max-concurrent-tcp-sessions=100 cache-size=8192KiB"
        ),
    )


def check_walled_garden(ros: RouterOS) -> dict:
    entries = ros.get_list("ip/hotspot/walled-garden")
    bad = []
    for e in entries:
        host = str(e.get("dst-host", "")).lower()
        if not host or _truthy(e.get("disabled")):
            continue
        for probe in CAPTIVE_PROBES:
            if probe in host or host.strip(".*") in probe:
                bad.append(host)
                break
    ok = not bad
    return _result(
        "walled_garden", "Walled garden không chứa trang dò captive", ok,
        f"{len(entries)} mục; vi phạm: {', '.join(bad) if bad else 'không có'}",
        hint=(
            "Các tên miền dò PHẢI bị chặn thì cửa sổ đăng nhập mới tự bật. "
            "Cho chúng vào walled garden là tự tay tắt cơ chế này."
        ),
    )


def check_trial_not_in_um(ros: RouterOS) -> dict:
    sessions = ros.um_sessions()
    leaked = [s.get("user", "") for s in sessions if str(s.get("user", "")).upper().startswith("T-")]
    ok = not leaked
    return _result(
        "trial_leak", "Khách trial không lọt vào User Manager", ok,
        f"{len(sessions)} phiên UM; phiên trial lọt vào: {len(leaked)}",
        hint=(
            "Trần license là 50 phiên UM. Đẩy khách vãng lai qua RADIUS sẽ chết ở khách thứ 51. "
            "Khách trial phải xử lý nội bộ RouterOS, không chạm RADIUS."
        ),
    )


def check_mss_clamp(ros: RouterOS) -> dict:
    rules = ros.get_list("ip/firewall/mangle")
    ok = any(
        r.get("action") == "change-mss"
        and "clamp" in str(r.get("new-mss", ""))
        and not _truthy(r.get("disabled"))
        for r in rules
    )
    return _result(
        "mss", "MSS clamp cho WAN PPPoE", ok,
        f"{len(rules)} rule mangle; có rule change-mss: {'có' if ok else 'không'}",
        hint=(
            "PPPoE làm MTU còn 1492. Thiếu rule này thì một số trang HTTPS treo giữa chừng, "
            "rất dễ chẩn đoán nhầm thành 'mạng chậm'."
        ),
    )


# ------------------------------------------------------------------ 4 mục bổ sung
def check_mac_cookie(ros: RouterOS) -> dict:
    prof = _trial_user_profile(ros)
    if not prof or "add-mac-cookie" not in prof:
        return _result(
            "mac_cookie", "add-mac-cookie = no", False,
            "Không đọc được user profile dành cho khách trial "
            "(kiểm tra `trial-user-profile` của server profile).",
            hint="Sửa mục `trial-user-profile` trước, rồi chạy lại bảng kiểm tra.",
            core=False,
        )
    raw = prof.get("add-mac-cookie", "?")
    ok = not _truthy(raw)
    return _result(
        "mac_cookie", "add-mac-cookie = no", ok,
        f"user profile `{prof.get('name', '?')}` có add-mac-cookie = {raw}",
        hint=(
            "Thủ phạm số một: bật lên là router tự đăng nhập lại máy quen, bỏ qua cả trang chào "
            "lẫn quảng cáo."
        ),
        core=False,
    )


def check_cookie_lifetime(ros: RouterOS) -> dict:
    prof = _active_server_profile(ros)
    raw = str(prof.get("http-cookie-lifetime", "?"))
    ok = parse_duration(raw) == 0 or raw.strip() in {"0s", "0"}
    return _result(
        "cookie_lifetime", "http-cookie-lifetime = 0s", ok,
        f"server profile `{prof.get('name', '?')}` có http-cookie-lifetime = {raw}",
        hint="Router nhớ máy khách qua cookie → trang chào không hiện lại → không xem quảng cáo lần hai.",
        core=False,
    )


def check_session_vs_trial(ros: RouterOS) -> dict:
    server = _active_server_profile(ros)
    user = _trial_user_profile(ros)
    session = parse_duration(user.get("session-timeout"))
    trial_raw = str(server.get("trial-uptime", ""))
    trial = parse_duration(trial_raw.split("/")[0]) if trial_raw else None
    ok = session is not None and trial is not None and session != trial
    detail = (
        f"session-timeout = {user.get('session-timeout') or 'trống'} (user profile), "
        f"trial-uptime = {trial_raw or 'trống'} (server profile)"
    )
    return _result(
        "session_vs_trial", "session-timeout khác trial-uptime", ok, detail,
        hint=(
            "Hai thứ hoàn toàn khác nhau: session-timeout là độ dài MỘT phiên (nhịp quảng cáo, 30m), "
            "trial-uptime là ngân sách CỘNG DỒN trong chu kỳ (8h/1d). Đặt bằng nhau = khách vào một "
            "phiên rồi bị khóa cả ngày."
        ),
        core=False,
    )


def check_trial_profile_not_default(ros: RouterOS) -> dict:
    server = _active_server_profile(ros)
    name = str(server.get("trial-user-profile", ""))
    ok = bool(name) and name != "default"
    return _result(
        "trial_profile", "trial-user-profile không phải `default`", ok,
        f"trial-user-profile = {name or '(trống)'}",
        hint="Để `default` thì hotspot từ chối cấp phiên trial.",
        core=False,
    )


ALL_CHECKS: list[Callable[[RouterOS], dict]] = [
    check_npk_architecture,
    check_um_enabled,
    check_radius_incoming,
    check_login_by_trial_first,
    check_chap_not_pap,
    check_idle_timeout,
    check_dns_limits,
    check_walled_garden,
    check_trial_not_in_um,
    check_mss_clamp,
    check_mac_cookie,
    check_cookie_lifetime,
    check_session_vs_trial,
    check_trial_profile_not_default,
]


def run_all(ros: RouterOS) -> list[dict]:
    """Chạy toàn bộ bảng kiểm tra. Một mục lỗi không làm hỏng các mục còn lại."""
    results: list[dict] = []
    for fn in ALL_CHECKS:
        try:
            results.append(fn(ros))
        except RouterOSError as exc:
            results.append(
                _result(fn.__name__, fn.__name__.replace("check_", ""), False, f"Không đọc được: {exc}")
            )
        except Exception as exc:  # lỗi phân tích dữ liệu lạ — vẫn phải chạy tiếp
            results.append(
                _result(fn.__name__, fn.__name__.replace("check_", ""), False, f"Lỗi xử lý: {exc}")
            )
    return results

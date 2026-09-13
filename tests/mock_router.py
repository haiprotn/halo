"""Router MikroTik giả lập — để chạy toàn bộ bộ kiểm thử mà KHÔNG cần thiết bị thật.

Hai cấu hình:
  cau_hinh_sach()  — router đã cấu hình đúng, mọi mục kiểm tra phải ĐẠT
  cau_hinh_loi()   — router cố ý sai đúng những lỗi đã gặp thật ngoài hiện trường:
                     thiếu gói user-manager (tải nhầm kiến trúc npk),
                     thiếu `/radius incoming accept=yes`,
                     `login-by` không có trial và còn dùng http-pap,
                     phiên trial lọt vào User Manager,
                     walled garden chứa trang dò captive,
                     thiếu MSS clamp, DNS chưa nâng trần, idle-timeout quá ngắn.
"""

from __future__ import annotations

import copy
import json

import httpx


def cau_hinh_sach() -> dict:
    return {
        "system/resource": {
            "board-name": "L009UiGS-RM", "architecture-name": "arm", "version": "7.15.3",
            "uptime": "3d4h12m", "cpu-load": 7, "free-memory": 380000000,
            "total-memory": 536870912, "free-hdd-space": 100000000,
        },
        "system/identity": {"name": "hs-benca"},
        "system/package": [
            {"name": "routeros", "version": "7.15.3"},
            {"name": "user-manager", "version": "7.15.3"},
        ],
        "user-manager": {"enabled": "true"},
        "radius/incoming": {"accept": "true"},
        "ip/hotspot": [{".id": "*1", "name": "hs-guest", "profile": "hsprof-guest"}],
        "ip/hotspot/profile": [
            {".id": "*1", "name": "default"},
            {
                ".id": "*2", "name": "hsprof-guest",
                "login-by": "trial,http-chap",
                "html-directory": "hotspot-vn",
                "http-cookie-lifetime": "0s",
                "trial-uptime": "8h/1d",
                "trial-user-profile": "trial",
            },
        ],
        "ip/hotspot/user/profile": [
            {".id": "*1", "name": "default"},
            {
                ".id": "*2", "name": "trial",
                "session-timeout": "30m", "idle-timeout": "15m", "keepalive-timeout": "5m",
                "add-mac-cookie": "false", "rate-limit": "2M/5M",
                "open-status-page": "http-login", "transparent-proxy": "false",
            },
        ],
        "ip/dns": {"max-concurrent-queries": 500, "cache-size": "8192KiB"},
        "ip/hotspot/walled-garden": [{".id": "*1", "dst-host": "vnpay.vn"}],
        "ip/hotspot/active": [{".id": "*1"}, {".id": "*2"}, {".id": "*3"}],
        "user-manager/session": [{".id": "*1", "user": "abc2def"}],
        "user-manager/user": [],
        "user-manager/profile": [{"name": "1h"}, {"name": "1d"}, {"name": "7d"}],
        "ip/firewall/mangle": [
            {".id": "*1", "chain": "forward", "action": "change-mss", "new-mss": "clamp-to-pmtu"}
        ],
    }


def cau_hinh_loi() -> dict:
    data = cau_hinh_sach()
    data["system/package"] = [{"name": "routeros", "version": "7.15.3"}]   # thiếu user-manager
    data["system/resource"]["architecture-name"] = "arm64"                  # npk sai kiến trúc
    data["user-manager"] = {"enabled": "false"}
    data["radius/incoming"] = {"accept": "false"}
    data["ip/hotspot/profile"][1]["login-by"] = "http-pap"                  # thiếu trial, dùng pap
    data["ip/hotspot/profile"][1]["http-cookie-lifetime"] = "3d"
    data["ip/hotspot/profile"][1]["trial-user-profile"] = "default"
    data["ip/hotspot/profile"][1]["trial-uptime"] = "30m"                   # bằng session-timeout
    data["ip/hotspot/user/profile"][1]["idle-timeout"] = "2m"
    data["ip/hotspot/user/profile"][1]["add-mac-cookie"] = "true"
    data["ip/dns"] = {"max-concurrent-queries": 100, "cache-size": "2048KiB"}
    data["ip/hotspot/walled-garden"] = [{".id": "*1", "dst-host": "captive.apple.com"}]
    data["user-manager/session"] = [{".id": "*1", "user": "T-BE:F5:47:0F:73:2B"}]  # trial lọt vào UM
    data["ip/firewall/mangle"] = []
    return data


class MockRouter:
    """Giữ trạng thái và trả lời như REST API của RouterOS 7."""

    def __init__(self, data: dict | None = None, loi_gan_goi_cuoc: bool = False,
                 loi_xoa_user: bool = False) -> None:
        self.data = copy.deepcopy(data or cau_hinh_sach())
        self.loi_gan_goi_cuoc = loi_gan_goi_cuoc   # mô phỏng bẫy #18
        self.loi_xoa_user = loi_xoa_user
        self.lich_su: list[tuple[str, str]] = []

    # ---------------------------------------------------------------- xử lý
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.replace("/rest/", "", 1).strip("/")
        self.lich_su.append((request.method, path))

        if request.method == "GET":
            if path not in self.data:
                return httpx.Response(404, json={"detail": f"no such command: {path}"})
            return httpx.Response(200, json=self.data[path])

        if request.method == "POST":
            body = json.loads(request.content or b"{}")
            return self._post(path, body)

        return httpx.Response(405, json={"detail": "method not allowed"})

    def _post(self, path: str, body: dict) -> httpx.Response:
        if path == "user-manager/user":
            ten = body.get("name", "")
            if any(u["name"] == ten for u in self.data["user-manager/user"]):
                return httpx.Response(400, json={"detail": "already have user with such name"})
            self.data["user-manager/user"].append(
                {".id": f"*{len(self.data['user-manager/user']) + 10}", "name": ten,
                 "password": body.get("password", ""), "shared-users": body.get("shared-users", "1"),
                 "profile": ""}
            )
            return httpx.Response(200, json={})

        if path == "user-manager/user/create-and-activate-profile":
            if self.loi_gan_goi_cuoc:
                return httpx.Response(400, json={"detail": "no such item (profile)"})
            ten = body.get("numbers", "")
            goi = body.get("profile", "")
            if goi not in [p["name"] for p in self.data["user-manager/profile"]]:
                return httpx.Response(400, json={"detail": f"no such profile: {goi}"})
            for u in self.data["user-manager/user"]:
                if u["name"] == ten:
                    u["profile"] = goi
                    return httpx.Response(200, json={})
            return httpx.Response(400, json={"detail": "no such user"})

        if path == "user-manager/user/remove":
            if self.loi_xoa_user:
                return httpx.Response(500, json={"detail": "router busy"})
            ten = body.get("numbers", "")
            truoc = len(self.data["user-manager/user"])
            self.data["user-manager/user"] = [
                u for u in self.data["user-manager/user"] if u["name"] != ten
            ]
            if len(self.data["user-manager/user"]) == truoc:
                return httpx.Response(400, json={"detail": "no such user"})
            return httpx.Response(200, json={})

        return httpx.Response(404, json={"detail": f"no such command: {path}"})

    # ---------------------------------------------------------------- tiện ích
    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle), base_url="https://router")

    def ten_user(self) -> list[str]:
        return [u["name"] for u in self.data["user-manager/user"]]

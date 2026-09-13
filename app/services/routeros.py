"""Client REST API của RouterOS 7.

Vì sao REST (`/rest`) chứ không phải API nhị phân cổng 8728: REST đi trên HTTPS,
dùng lại được thư viện HTTP sẵn có, và trên router chỉ cần mở dịch vụ `www-ssl`
cho đúng một địa chỉ nguồn là VPS (10.90.0.1/32).

★ Timeout là bắt buộc, không phải tùy chọn.
  Cả hai nghiệp vụ (đẩy banner và đọc trạng thái) nay chạy chung một ứng dụng.
  Một router treo mà không có timeout sẽ giữ luôn luồng xử lý và kéo theo việc
  đẩy banner của địa điểm khác. Mọi lời gọi ra ngoài trong dự án này đều có
  timeout cứng, và tác vụ nền chạy ở tiến trình riêng (xem `app/worker.py`).

Chứng chỉ: router dùng chứng chỉ tự ký, lại chỉ truy cập được trong đường hầm
WireGuard, nên tắt kiểm tra chứng chỉ là chấp nhận được — đường truyền đã được
WireGuard mã hóa sẵn.
"""

from __future__ import annotations

from typing import Any

import httpx


class RouterOSError(Exception):
    """Lỗi khi nói chuyện với router: không kết nối được, sai mật khẩu, router trả lỗi."""


def _default_client(username: str, password: str, timeout: float) -> httpx.Client:
    return httpx.Client(
        auth=(username, password),
        timeout=timeout,
        verify=False,  # chứng chỉ tự ký của router; kênh đã được WireGuard bảo vệ
        follow_redirects=False,
    )


# Điểm móc để bộ kiểm thử thay bằng router giả lập, không cần thiết bị thật.
# Mã chạy thật không bao giờ đổi giá trị này.
CLIENT_FACTORY = _default_client


class RouterOS:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 8.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._own_client = client is None
        self._client = client or CLIENT_FACTORY(username, password, timeout)

    # -------------------------------------------------- vòng đời
    def close(self) -> None:
        if self._own_client:
            self._client.close()

    def __enter__(self) -> "RouterOS":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -------------------------------------------------- lời gọi cơ bản
    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = self._client.request(method, url, json=payload)
        except httpx.TimeoutException as exc:
            raise RouterOSError(f"Router không trả lời trong thời gian cho phép ({path})") from exc
        except httpx.HTTPError as exc:
            raise RouterOSError(f"Không kết nối được tới router: {exc}") from exc

        if resp.status_code == 401:
            raise RouterOSError("Sai tài khoản hoặc mật khẩu REST của router")
        if resp.status_code == 403:
            raise RouterOSError(
                "Router từ chối (403). Kiểm tra quyền của tài khoản và ràng buộc địa chỉ nguồn."
            )
        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json()
                detail = body.get("detail") or body.get("message") or ""
            except Exception:
                detail = resp.text[:200]
            raise RouterOSError(f"Router báo lỗi {resp.status_code} ở {path}: {detail}")

        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise RouterOSError(f"Router trả về dữ liệu không phải JSON ở {path}") from exc

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def get_list(self, path: str) -> list[dict]:
        """Luôn trả về danh sách, kể cả khi router trả về một đối tượng đơn."""
        data = self.get(path)
        if data is None:
            return []
        if isinstance(data, dict):
            return [data]
        return list(data)

    def get_one(self, path: str) -> dict:
        """Dùng cho các nhóm cấu hình chỉ có một bản ghi (system/resource, ip/dns, ...)."""
        data = self.get(path)
        if isinstance(data, list):
            return data[0] if data else {}
        return data or {}

    def post(self, path: str, payload: dict | None = None) -> Any:
        return self._request("POST", path, payload or {})

    def patch(self, path: str, payload: dict) -> Any:
        return self._request("PATCH", path, payload)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # -------------------------------------------------- tiện ích nghiệp vụ
    def system_resource(self) -> dict:
        return self.get_one("system/resource")

    def identity(self) -> str:
        return self.get_one("system/identity").get("name", "")

    def packages(self) -> list[dict]:
        return self.get_list("system/package")

    def hotspot_active_count(self) -> int:
        return len(self.get_list("ip/hotspot/active"))

    def um_sessions(self) -> list[dict]:
        """Danh sách phiên User Manager. Trên router chưa bật UM sẽ trả về rỗng."""
        try:
            return self.get_list("user-manager/session")
        except RouterOSError:
            return []

    def ping(self) -> dict:
        """Một lời gọi rẻ nhất để biết router còn sống và tài khoản còn đúng."""
        return self.system_resource()


def open_router(site, password: str, timeout: float = 8.0) -> RouterOS:
    """Mở kết nối tới router của một địa điểm (mật khẩu đã được giải mã ở tầng trên)."""
    return RouterOS(site.rest_base, site.rest_user, password, timeout=timeout)

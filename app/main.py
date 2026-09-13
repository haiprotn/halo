"""Điểm khởi động ứng dụng web.

★ VÌ SAO GỘP HAI HỆ THÀNH MỘT MÀ VẪN AN TOÀN.
  Bản Node tách `wifi-ads-server` và `vsp-server` thành hai tiến trình vì lý do
  rất cụ thể: quảng cáo là luồng ĐẨY FILE (SFTP, chậm), quản trị là luồng ĐỌC
  TRẠNG THÁI (REST, liên tục); trộn chung thì một router timeout làm treo cả
  việc đẩy banner.

  Bản Python này gộp mã nguồn và CSDL (để đăng nhập một lần, dùng chung khách
  hàng/địa điểm) nhưng GIỮ NGUYÊN sự tách biệt ở chỗ nó thật sự quan trọng:

    - `app/main.py`   → tiến trình WEB, phục vụ người dùng (systemd: halo-web)
    - `app/worker.py` → tiến trình QUÉT nền 60 giây/lần (systemd: halo-worker)

  Hai tiến trình riêng, khởi động lại độc lập. Worker chết thì web vẫn bấm nút
  được; web quá tải thì việc quét vẫn chạy. Thêm vào đó mọi lời gọi ra router
  đều có timeout cứng (MONITOR_TIMEOUT, SFTP_TIMEOUT).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import BASE_DIR, settings
from .routers import admin, ads, auth, pages, vouchers
from .templating import templates

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(
    title="Halo — cổng quản trị WiFi",
    description="Quảng cáo + giám sát + voucher cho hệ thống hotspot MikroTik",
    docs_url="/api-docs" if settings.debug else None,
    redoc_url=None,
)

STATIC_DIR = BASE_DIR / "app" / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(ads.router)
app.include_router(vouchers.router)
app.include_router(admin.router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """401 trên trang HTML thì đưa về trang đăng nhập; trên API thì trả JSON."""
    muon_json = request.url.path.startswith("/api") or "application/json" in request.headers.get("accept", "")

    if exc.status_code == 401 and not muon_json:
        return RedirectResponse("/dang-nhap", status_code=303)
    if muon_json:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return templates.TemplateResponse(
        request, "error.html",
        {"ma": exc.status_code, "thong_diep": exc.detail},
        status_code=exc.status_code,
    )


@app.get("/khoe", response_class=HTMLResponse, include_in_schema=False)
def health():
    """Điểm kiểm tra sống cho nginx/systemd. Không cần đăng nhập, không lộ dữ liệu."""
    return "ok"

"""Cấu hình Jinja2 và các bộ lọc hiển thị dùng chung."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from .config import settings

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
VN = ZoneInfo("Asia/Ho_Chi_Minh")

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def gio_vn(value: dt.datetime | None, fmt: str = "%H:%M %d/%m/%Y") -> str:
    """Lưu ở UTC, hiển thị ở giờ Việt Nam."""
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(VN).strftime(fmt)


def truoc_day(value: dt.datetime | None) -> str:
    """'3 phút trước' — dễ đọc hơn mốc thời gian tuyệt đối khi theo dõi trạng thái."""
    if value is None:
        return "chưa bao giờ"
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    giay = int((dt.datetime.now(dt.timezone.utc) - value).total_seconds())
    if giay < 60:
        return f"{giay} giây trước"
    if giay < 3600:
        return f"{giay // 60} phút trước"
    if giay < 86400:
        return f"{giay // 3600} giờ trước"
    return f"{giay // 86400} ngày trước"


def dung_luong(value: int | None) -> str:
    if not value:
        return "0 B"
    for don_vi in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or don_vi == "GiB":
            return f"{value:.0f} {don_vi}" if don_vi == "B" else f"{value:.1f} {don_vi}"
        value /= 1024
    return str(value)


THU_VN = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


def thu(days_csv: str) -> str:
    idx = [int(d) for d in str(days_csv).split(",") if d.strip().isdigit()]
    if len(idx) == 7:
        return "cả tuần"
    return ", ".join(THU_VN[i] for i in idx if 0 <= i < 7)


templates.env.filters["gio_vn"] = gio_vn
templates.env.filters["truoc_day"] = truoc_day
templates.env.filters["dung_luong"] = dung_luong
templates.env.filters["thu"] = thu
templates.env.globals["collect_sessions"] = settings.collect_sessions
templates.env.globals["wg_prefix"] = settings.wg_prefix

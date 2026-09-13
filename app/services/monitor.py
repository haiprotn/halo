"""Tác vụ nền: quét trạng thái các địa điểm và sinh cảnh báo.

★ VPS CHỈ ĐỌC ở tác vụ nền. Mọi thay đổi trên router (sinh voucher, đá phiên,
  xóa mã, đẩy banner) đều do người dùng bấm nút. Không có tác vụ nền nào tự sửa
  cấu hình router — nguyên tắc này giữ nguyên từ bản Node.

★ Cảnh báo là TRẠNG THÁI, không phải sự kiện: mở một lần khi điều kiện xuất hiện,
  tự đóng khi hết. Nếu sinh cảnh báo mới mỗi vòng quét thì một site rớt mạng qua
  đêm sẽ đẻ ra hàng trăm dòng giống hệt nhau và không ai đọc nữa.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Alert, Sample, Site, SiteStatus, now_utc
from ..security import decrypt_secret
from .routeros import RouterOS, RouterOSError
from .vouchers import UM_SESSION_LIMIT

log = logging.getLogger("halo.monitor")


# ------------------------------------------------------------------ cảnh báo
def open_alert(db: Session, site_id: int, kind: str, message: str, severity: str = "warning") -> None:
    existing = db.scalar(
        select(Alert).where(Alert.site_id == site_id, Alert.kind == kind, Alert.closed_at.is_(None))
    )
    if existing is not None:
        existing.message = message  # cập nhật nội dung, giữ nguyên thời điểm mở
        return
    db.add(Alert(site_id=site_id, kind=kind, message=message, severity=severity))
    # flush ngay: vòng quét sau phải THẤY cảnh báo vừa mở, nếu không mỗi vòng lại
    # đẻ thêm một dòng giống hệt (session đang tắt autoflush).
    db.flush()


def close_alert(db: Session, site_id: int, kind: str) -> None:
    rows = db.scalars(
        select(Alert).where(Alert.site_id == site_id, Alert.kind == kind, Alert.closed_at.is_(None))
    ).all()
    for a in rows:
        a.closed_at = now_utc()
    if rows:
        db.flush()


def open_alerts(db: Session, site_ids: list[int] | None = None) -> list[Alert]:
    stmt = select(Alert).where(Alert.closed_at.is_(None)).order_by(Alert.opened_at.desc())
    if site_ids is not None:
        stmt = stmt.where(Alert.site_id.in_(site_ids or [-1]))
    return list(db.scalars(stmt))


# ------------------------------------------------------------------ quét
def _status_row(db: Session, site_id: int) -> SiteStatus:
    st = db.get(SiteStatus, site_id)
    if st is None:
        st = SiteStatus(site_id=site_id)
        db.add(st)
        db.flush()
    return st


def scan_site(db: Session, site: Site, license_level: int = 5) -> SiteStatus:
    """Đọc trạng thái một router. Không bao giờ ném lỗi ra ngoài — lỗi là một trạng thái."""
    st = _status_row(db, site.id)

    try:
        password = decrypt_secret(site.rest_password_enc)
    except Exception as exc:
        st.online = False
        st.last_error = f"Không giải mã được mật khẩu router (kiểm tra APP_KEY trong .env): {exc}"
        st.updated_at = now_utc()
        open_alert(db, site.id, "credentials", st.last_error, severity="error")
        return st

    try:
        with RouterOS(site.rest_base, site.rest_user, password, timeout=settings.monitor_timeout) as ros:
            res = ros.system_resource()
            st.identity = ros.identity()
            st.board_name = res.get("board-name", "")
            st.version = res.get("version", "")
            st.architecture = res.get("architecture-name", "")
            st.uptime = res.get("uptime", "")
            st.cpu_load = int(str(res.get("cpu-load", 0)) or 0)
            st.free_memory = int(str(res.get("free-memory", 0)) or 0)
            st.total_memory = int(str(res.get("total-memory", 0)) or 0)
            st.free_hdd = int(str(res.get("free-hdd-space", 0)) or 0)
            st.hotspot_active = ros.hotspot_active_count()
            st.um_sessions = len(ros.um_sessions())
    except RouterOSError as exc:
        st.online = False
        st.last_error = str(exc)
        st.updated_at = now_utc()
        open_alert(db, site.id, "offline", f"Không đọc được trạng thái: {exc}", severity="error")
        return st

    st.online = True
    st.last_error = ""
    st.last_ok_at = now_utc()
    st.updated_at = now_utc()
    close_alert(db, site.id, "offline")
    close_alert(db, site.id, "credentials")

    # Số đo cho biểu đồ và báo cáo — chỉ số đếm tổng hợp, không có định danh khách
    db.add(Sample(
        site_id=site.id,
        hotspot_active=st.hotspot_active,
        um_sessions=st.um_sessions,
        cpu_load=st.cpu_load,
        free_memory=st.free_memory,
    ))

    # Cảnh báo trần phiên User Manager (đây là trần LICENSE, không phải phần cứng)
    limit = UM_SESSION_LIMIT.get(license_level, 50)
    if limit and st.um_sessions >= limit * 0.9:
        open_alert(
            db, site.id, "um_session_high",
            f"{st.um_sessions}/{limit} phiên User Manager đồng thời (trần license Level {license_level}). "
            f"Chạm trần là khách nhập mã sẽ bị từ chối.",
        )
    else:
        close_alert(db, site.id, "um_session_high")

    # Flash sắp đầy → không đẩy được banner nữa
    if st.free_hdd and st.free_hdd < 2 * 1024 * 1024:
        open_alert(
            db, site.id, "flash_low",
            f"Flash router chỉ còn {st.free_hdd // 1024} KiB. Giảm số banner hoặc nén ảnh nhỏ lại.",
        )
    else:
        close_alert(db, site.id, "flash_low")

    return st


def scan_all(db: Session) -> dict:
    """Một vòng quét toàn bộ địa điểm đang bật."""
    sites = list(db.scalars(select(Site).where(Site.active.is_(True))))
    ok = fail = 0
    for site in sites:
        st = scan_site(db, site)
        ok += 1 if st.online else 0
        fail += 0 if st.online else 1
    db.commit()
    return {"tong": len(sites), "online": ok, "offline": fail}


def prune_samples(db: Session, keep_days: int = 90) -> int:
    """Xóa số đo cũ. 60 giây một mẫu × 90 ngày ≈ 130 nghìn dòng mỗi site — đủ cho báo cáo."""
    cutoff = now_utc() - dt.timedelta(days=keep_days)
    deleted = db.query(Sample).filter(Sample.taken_at < cutoff).delete(synchronize_session=False)
    db.commit()
    return deleted

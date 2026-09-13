"""Các bảng trong CSDL.

Một CSDL PostgreSQL duy nhất cho cả hai mặt nghiệp vụ:
  - quảng cáo  (banner, lịch chiếu, lần đẩy file xuống router)
  - quản trị   (giám sát, voucher/User Manager, báo cáo)
Cách ly khách hàng luôn dựa trên `Site.tenant_id` — xem `app/deps.py`.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now_utc() -> dt.datetime:
    """Mọi mốc thời gian lưu ở UTC, chỉ đổi sang giờ Việt Nam khi hiển thị."""
    return dt.datetime.now(dt.timezone.utc)


TS = DateTime(timezone=True)


# ---------------------------------------------------------------- khách hàng
class Tenant(Base):
    """Một khách hàng (đơn vị thuê hệ thống). Mỗi khách có nhiều địa điểm."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    note: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=now_utc)

    sites: Mapped[list["Site"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    users: Mapped[list["User"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class User(Base):
    """Tài khoản đăng nhập cổng quản trị.

    role = 'admin'  : quản trị hệ thống, thấy mọi khách hàng
    role = 'tenant' : nhân sự của một khách hàng, chỉ thấy địa điểm của mình
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    full_name: Mapped[str] = mapped_column(String(150), default="")
    role: Mapped[str] = mapped_column(String(20), default="tenant")
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(TS, nullable=True)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=now_utc)

    tenant: Mapped["Tenant | None"] = relationship(back_populates="users")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# ---------------------------------------------------------------- địa điểm
class Site(Base):
    """Một địa điểm lắp đặt: một router MikroTik (L009/RB5009) + các AP của nó.

    Mật khẩu router KHÔNG bao giờ lưu dạng chữ thường: mã hóa AES-256-GCM
    bằng APP_KEY (xem `app/security.py`).
    """

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    address: Mapped[str] = mapped_column(String(255), default="")

    # --- đường đi tới router ---
    wg_ip: Mapped[str] = mapped_column(String(45), unique=True)       # 10.90.0.X
    wg_public_key: Mapped[str] = mapped_column(String(64), default="")   # khóa công khai của router
    wg_private_key_enc: Mapped[str] = mapped_column(Text, default="")    # khóa riêng của router (mã hóa)

    # --- tài khoản REST API (đọc trạng thái, sinh voucher) ---
    rest_scheme: Mapped[str] = mapped_column(String(8), default="https")
    rest_port: Mapped[int] = mapped_column(Integer, default=443)
    rest_user: Mapped[str] = mapped_column(String(64), default="vsp")
    rest_password_enc: Mapped[str] = mapped_column(Text, default="")

    # --- tài khoản SFTP (đẩy banner + ads.js) ---
    sftp_port: Mapped[int] = mapped_column(Integer, default=22)
    sftp_user: Mapped[str] = mapped_column(String(64), default="ads")
    sftp_password_enc: Mapped[str] = mapped_column(Text, default="")
    hotspot_dir: Mapped[str] = mapped_column(String(120), default="hotspot-vn")

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=now_utc)

    tenant: Mapped["Tenant"] = relationship(back_populates="sites")
    status: Mapped["SiteStatus | None"] = relationship(
        back_populates="site", cascade="all, delete-orphan", uselist=False
    )
    banners: Mapped[list["Banner"]] = relationship(
        back_populates="site", cascade="all, delete-orphan", order_by="Banner.sort_order"
    )
    vouchers: Mapped[list["Voucher"]] = relationship(back_populates="site", cascade="all, delete-orphan")

    @property
    def rest_base(self) -> str:
        return f"{self.rest_scheme}://{self.wg_ip}:{self.rest_port}/rest"


class SiteStatus(Base):
    """Ảnh chụp trạng thái mới nhất của một địa điểm (mỗi site đúng một dòng)."""

    __tablename__ = "site_status"

    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    last_ok_at: Mapped[dt.datetime | None] = mapped_column(TS, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    identity: Mapped[str] = mapped_column(String(120), default="")
    board_name: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[str] = mapped_column(String(40), default="")
    architecture: Mapped[str] = mapped_column(String(40), default="")
    uptime: Mapped[str] = mapped_column(String(40), default="")
    cpu_load: Mapped[int] = mapped_column(Integer, default=0)
    free_memory: Mapped[int] = mapped_column(Integer, default=0)
    total_memory: Mapped[int] = mapped_column(Integer, default=0)
    free_hdd: Mapped[int] = mapped_column(Integer, default=0)
    hotspot_active: Mapped[int] = mapped_column(Integer, default=0)
    um_sessions: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, default=now_utc)

    site: Mapped["Site"] = relationship(back_populates="status")


class Sample(Base):
    """Số đo theo thời gian, dùng vẽ biểu đồ và tính đỉnh khách online theo ngày.

    Chỉ lưu SỐ ĐẾM TỔNG HỢP — không có MAC, không có tên khách.
    """

    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[dt.datetime] = mapped_column(TS, default=now_utc)
    hotspot_active: Mapped[int] = mapped_column(Integer, default=0)
    um_sessions: Mapped[int] = mapped_column(Integer, default=0)
    cpu_load: Mapped[int] = mapped_column(Integer, default=0)
    free_memory: Mapped[int] = mapped_column(Integer, default=0)


Index("ix_samples_site_time", Sample.site_id, Sample.taken_at)


class Alert(Base):
    """Cảnh báo dạng TRẠNG THÁI: mở một lần, tự đóng khi hết điều kiện.

    Không sinh cảnh báo mới mỗi vòng quét — nếu không mỗi site rớt mạng qua đêm
    sẽ đẻ ra hàng trăm dòng giống hệt nhau.
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(60))          # offline | um_session_high | check_failed | ...
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    message: Mapped[str] = mapped_column(Text, default="")
    opened_at: Mapped[dt.datetime] = mapped_column(TS, default=now_utc)
    closed_at: Mapped[dt.datetime | None] = mapped_column(TS, nullable=True)


class CheckRun(Base):
    """Một lần chạy bảng 10 mục kiểm tra cấu hình router."""

    __tablename__ = "check_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    run_at: Mapped[dt.datetime] = mapped_column(TS, default=now_utc)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[list] = mapped_column(JSON, default=list)


# ---------------------------------------------------------------- quảng cáo
class Banner(Base):
    """Một ảnh quảng cáo kèm lịch chiếu.

    `days`  : chuỗi các thứ trong tuần theo chuẩn Python (0=Thứ Hai … 6=Chủ Nhật), vd "0,1,2,3,4"
    `slots` : trang được phép hiện — login | alogin | status (nhiều giá trị cách nhau dấu phẩy)
    """

    __tablename__ = "banners"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(200))        # tên file lưu trên VPS và trên router
    original_name: Mapped[str] = mapped_column(String(200), default="")
    content_type: Mapped[str] = mapped_column(String(80), default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    days: Mapped[str] = mapped_column(String(40), default="0,1,2,3,4,5,6")
    start_time: Mapped[str] = mapped_column(String(5), default="00:00")
    end_time: Mapped[str] = mapped_column(String(5), default="23:59")
    slots: Mapped[str] = mapped_column(String(60), default="login,alogin")
    link_url: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=now_utc)

    site: Mapped["Site"] = relationship(back_populates="banners")


class PushLog(Base):
    """Nhật ký mỗi lần đẩy bộ banner xuống router."""

    __tablename__ = "push_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(TS, default=now_utc)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TS, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    files: Mapped[int] = mapped_column(Integer, default=0)
    bytes_sent: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- voucher
class VoucherBatch(Base):
    """Một lô voucher sinh cùng lúc (in cùng một tờ phiếu)."""

    __tablename__ = "voucher_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    profile_name: Mapped[str] = mapped_column(String(60))    # tên gói cước trên User Manager
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=now_utc)

    vouchers: Mapped[list["Voucher"]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class Voucher(Base):
    """Một mã voucher.

    Vòng đời trạng thái:
      moi         → vừa sinh trên VPS, CHƯA có trên router
      tren_router → đã đẩy lên User Manager thành công
      da_dung     → đã có phiên sử dụng (đồng bộ từ router)
      het_han     → hết hạn theo `validity` của gói cước
      loi         → đẩy thất bại (đã quay lui, KHÔNG để lại tài khoản mồ côi)
      da_xoa      → đã từng lên router và nay không còn trên đó nữa

    Quy tắc đồng bộ: chỉ mã ĐÃ TỪNG lên router mới được chuyển sang `da_xoa`.
    Mã `moi` và `loi` cũng không có trên router nhưng KHÔNG phải là đã xóa.
    """

    __tablename__ = "vouchers"
    __table_args__ = (UniqueConstraint("site_id", "code", name="uq_voucher_site_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("voucher_batches.id", ondelete="SET NULL"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(40))
    password: Mapped[str] = mapped_column(String(40))
    profile_name: Mapped[str] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(20), default="moi", index=True)
    pushed_at: Mapped[dt.datetime | None] = mapped_column(TS, nullable=True)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(TS, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(TS, default=now_utc)

    site: Mapped["Site"] = relationship(back_populates="vouchers")
    batch: Mapped["VoucherBatch | None"] = relationship(back_populates="vouchers")


# ---------------------------------------------------------------- nhật ký
class AuditLog(Base):
    """Ai làm gì, lúc nào, từ địa chỉ nào. Chỉ ghi thao tác thay đổi dữ liệu."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(TS, default=now_utc, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str] = mapped_column(String(80), default="")
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(60), default="")

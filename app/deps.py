"""Dependency dùng chung: lấy người đang đăng nhập và chặn truy cập chéo khách hàng.

★ Nguyên tắc cách ly đa khách hàng — kiểm ở TẦNG SERVER, không phải chỉ ẩn nút:
  - Gọi API địa điểm của khách hàng khác  → 404 (không phải 403, để không lộ sự tồn tại)
  - Tài khoản tenant gọi /quan-tri/*      → 403
Mọi truy vấn địa điểm trong dự án này đều phải đi qua `get_site` dưới đây.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Site, User
from .security import read_session_token

COOKIE_NAME = "halo_session"


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    uid = read_session_token(token)
    if uid is None:
        return None
    user = db.get(User, uid)
    if user is None or not user.active:
        return None
    return user


def current_user(user: User | None = Depends(current_user_optional)) -> User:
    if user is None:
        # 401 kèm header để tầng trên biết mà chuyển hướng về trang đăng nhập
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Chưa đăng nhập")
    return user


def admin_required(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Chỉ quản trị hệ thống mới được vào mục này")
    return user


def visible_sites(db: Session, user: User) -> list[Site]:
    """Danh sách địa điểm người này được phép thấy."""
    stmt = select(Site).order_by(Site.name)
    if not user.is_admin:
        stmt = stmt.where(Site.tenant_id == user.tenant_id)
    return list(db.scalars(stmt))


def get_site(db: Session, user: User, site_id: int) -> Site:
    """Lấy một địa điểm và kiểm quyền cùng lúc.

    Cố tình trả 404 (không phải 403) khi site thuộc khách hàng khác: người gọi
    không phân biệt được 'không có site này' với 'có nhưng không cho xem'.
    """
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy địa điểm")
    if not user.is_admin and site.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy địa điểm")
    return site


def client_ip(request: Request) -> str:
    """Lấy IP thật khi chạy sau nginx (nginx phải set X-Forwarded-For)."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""

"""Ghi nhật ký thao tác.

Chỉ ghi thao tác LÀM THAY ĐỔI dữ liệu hoặc chạm tới router. Không ghi việc xem
trang — nhật ký mà đầy dòng "đã xem tổng quan" thì không ai đọc nữa.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog, User


def log_action(db: Session, user: User | None, action: str, target: str = "",
               detail: str = "", ip: str = "") -> None:
    db.add(AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else "",
        action=action,
        target=target,
        detail=detail[:2000],
        ip=ip,
    ))


def recent(db: Session, limit: int = 200) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(limit)))

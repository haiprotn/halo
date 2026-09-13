"""Kết nối PostgreSQL và phiên làm việc (session) của SQLAlchemy.

Ghi chú cho người đang học:
- `engine` là bể kết nối (connection pool) dùng chung cho cả tiến trình.
- Mỗi request web mượn một `Session` từ bể, dùng xong trả lại (xem `get_db`).
- Dùng SQLAlchemy kiểu đồng bộ (không async) cho dễ đọc. FastAPI tự đẩy các
  hàm route khai báo bằng `def` (không phải `async def`) sang luồng riêng,
  nên truy vấn CSDL không làm nghẽn vòng lặp sự kiện.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,   # kiểm tra kết nối trước khi dùng, tránh lỗi "server closed the connection"
    pool_size=5,
    max_overflow=10,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Lớp cha của mọi bảng."""


def get_db() -> Iterator[Session]:
    """Dependency của FastAPI: mở session, trả về cho route, đóng khi xong."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

"""Tạo bảng và tài khoản quản trị đầu tiên.

Chạy:  python3 -m scripts.init_db
       python3 -m scripts.init_db --admin hai --mat-khau 'chuoi-dai-toi-thieu-10-ky-tu'

Chạy lại nhiều lần không sao: bảng đã có thì bỏ qua, tài khoản đã có thì không tạo lại.
Khi đã chạy thật (có dữ liệu), mọi thay đổi cấu trúc bảng nên đi qua Alembic:
    alembic revision --autogenerate -m "mo ta thay doi"
    alembic upgrade head
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import User  # noqa: F401 - phải import để SQLAlchemy biết hết các bảng
from app.security import hash_password, random_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Khởi tạo CSDL cổng quản trị WiFi")
    parser.add_argument("--admin", default="admin", help="tên đăng nhập quản trị đầu tiên")
    parser.add_argument("--mat-khau", default="", help="mật khẩu; bỏ trống thì sinh tự động")
    args = parser.parse_args()

    print("Tạo bảng…")
    Base.metadata.create_all(engine)

    db = SessionLocal()
    try:
        if db.scalar(select(User).where(User.username == args.admin)):
            print(f"Tài khoản `{args.admin}` đã có, không tạo lại.")
            return 0

        mat_khau = args.mat_khau or random_code(14)
        db.add(User(
            username=args.admin,
            password_hash=hash_password(mat_khau),
            full_name="Quản trị hệ thống",
            role="admin",
        ))
        db.commit()
        print("\n" + "=" * 56)
        print(f"  Tài khoản quản trị : {args.admin}")
        print(f"  Mật khẩu           : {mat_khau}")
        print("  → Đăng nhập rồi đổi mật khẩu ngay.")
        print("=" * 56 + "\n")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

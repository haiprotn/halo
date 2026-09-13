"""Thiết lập chung cho bộ kiểm thử.

Chạy:  pytest -q
Cần một CSDL PostgreSQL rỗng dành riêng cho kiểm thử (mặc định `halo_test`):
    sudo -u postgres psql -c "CREATE DATABASE halo_test OWNER halo;"
Hoặc đặt biến môi trường TEST_DATABASE_URL trỏ tới nơi khác.
"""

from __future__ import annotations

import os
from pathlib import Path

# Phải đặt biến môi trường TRƯỚC khi import app, vì app đọc cấu hình lúc import.
TEST_DATA_DIR = Path(__file__).resolve().parent / "_du_lieu_tam"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://halo:halo@localhost:5432/halo_test"
)
os.environ["APP_KEY"] = "1f" * 32          # 64 ký tự hex, chỉ dùng khi kiểm thử
os.environ["SESSION_SECRET"] = "bi-mat-kiem-thu"
os.environ["DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["COLLECT_SESSIONS"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Site, SiteStatus, Tenant, User  # noqa: E402
from app.security import encrypt_secret, hash_password  # noqa: E402
from app.services import routeros as routeros_module  # noqa: E402
from tests.mock_router import MockRouter  # noqa: E402


@pytest.fixture(autouse=True)
def csdl_sach():
    """Mỗi bài kiểm thử bắt đầu với CSDL trống."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    with engine.begin() as conn:
        conn.execute(text("SELECT 1"))


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def du_lieu_mau(db):
    """Hai khách hàng, mỗi khách một địa điểm và một tài khoản, cộng một quản trị."""
    kh_a = Tenant(name="Khach A")
    kh_b = Tenant(name="Khach B")
    db.add_all([kh_a, kh_b])
    db.flush()

    site_a = Site(
        tenant_id=kh_a.id, name="Quan ca phe A", wg_ip="10.90.0.2",
        rest_password_enc=encrypt_secret("mat-khau-rest-A"),
        sftp_password_enc=encrypt_secret("mat-khau-sftp-A"),
    )
    site_b = Site(
        tenant_id=kh_b.id, name="Khach san B", wg_ip="10.90.0.3",
        rest_password_enc=encrypt_secret("mat-khau-rest-B"),
    )
    db.add_all([site_a, site_b])
    db.flush()
    db.add_all([SiteStatus(site_id=site_a.id), SiteStatus(site_id=site_b.id)])

    admin = User(username="admin", password_hash=hash_password("mat-khau-admin"), role="admin")
    user_a = User(username="khach-a", password_hash=hash_password("mat-khau-a"),
                  role="tenant", tenant_id=kh_a.id)
    user_b = User(username="khach-b", password_hash=hash_password("mat-khau-b"),
                  role="tenant", tenant_id=kh_b.id)
    db.add_all([admin, user_a, user_b])
    db.commit()

    return {
        "kh_a": kh_a, "kh_b": kh_b, "site_a": site_a, "site_b": site_b,
        "admin": admin, "user_a": user_a, "user_b": user_b,
    }


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def dang_nhap(client: TestClient, username: str, password: str) -> TestClient:
    r = client.post("/dang-nhap", data={"username": username, "password": password},
                    follow_redirects=False)
    assert r.status_code == 303, f"Đăng nhập thất bại: {r.status_code}"
    return client


@pytest.fixture
def router_gia(monkeypatch):
    """Thay client HTTP thật bằng router giả lập cho mọi lời gọi RouterOS."""
    hop = {}

    def gan(mock: MockRouter) -> MockRouter:
        hop["mock"] = mock
        monkeypatch.setattr(
            routeros_module, "CLIENT_FACTORY",
            lambda username, password, timeout: mock.client(),
        )
        return mock

    return gan

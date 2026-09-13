"""Cấu hình toàn hệ thống.

Mọi tham số đọc từ biến môi trường hoặc file `.env` ở thư mục gốc dự án.
Không hardcode địa chỉ, cổng, dải mạng ở bất kỳ chỗ nào khác — sửa ở đây là đủ.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Thư mục gốc dự án (chứa .env, requirements.txt, install.sh)
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Cơ sở dữ liệu ---
    database_url: str = "postgresql+psycopg://halo:halo@localhost:5432/halo"

    # --- Khóa bí mật ---
    # 64 ký tự hex = 32 byte cho AES-256-GCM. Bắt buộc phải đặt trong .env.
    app_key: str = ""
    session_secret: str = ""

    # --- Mạng WireGuard ---
    wg_prefix: str = "10.90.0."
    vps_wg_ip: str = "10.90.0.1"
    # 51820 là cổng UFW trên server-001 đã mở sẵn. Đổi ở đây thì khối .rsc sinh cho
    # router đổi theo — giữ ba chỗ (ListenPort · UFW · .rsc) luôn cùng một số.
    wg_port: int = 51820

    # --- Cam kết dữ liệu ---
    collect_sessions: bool = False
    hash_mac: bool = True

    # --- Tác vụ nền ---
    monitor_interval: int = 60      # giây giữa hai vòng quét
    monitor_timeout: float = 8.0    # timeout cứng khi gọi REST router
    sftp_timeout: float = 30.0      # timeout cứng khi đẩy file

    # --- Giới hạn banner ---
    max_banner_bytes: int = 150 * 1024
    max_banners_per_site: int = 8

    # --- Thư mục dữ liệu ---
    data_dir: Path = BASE_DIR / "data"

    debug: bool = False

    @property
    def banner_dir(self) -> Path:
        return self.data_dir / "banners"

    def key_bytes(self) -> bytes:
        """Trả về APP_KEY dạng 32 byte, báo lỗi sớm và rõ nếu cấu hình sai."""
        if not self.app_key:
            raise RuntimeError(
                "Chưa đặt APP_KEY trong .env. "
                'Sinh khóa: python3 -c "import secrets; print(secrets.token_hex(32))"'
            )
        try:
            raw = bytes.fromhex(self.app_key)
        except ValueError as exc:
            raise RuntimeError("APP_KEY phải là chuỗi hex (0-9a-f), 64 ký tự.") from exc
        if len(raw) != 32:
            raise RuntimeError(f"APP_KEY phải dài đúng 32 byte (64 ký tự hex), đang có {len(raw)} byte.")
        return raw


@lru_cache
def get_settings() -> Settings:
    """Đọc cấu hình một lần rồi nhớ lại — gọi bao nhiêu lần cũng chỉ đọc file một lần."""
    s = Settings()
    s.banner_dir.mkdir(parents=True, exist_ok=True)
    return s


settings = get_settings()

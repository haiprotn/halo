"""Đẩy banner + ads.js xuống router bằng SFTP.

★ Kiến trúc "đẩy, không kéo": VPS chủ động gửi file xuống router theo lệnh người
  vận hành. Router không bao giờ gọi ra VPS để lấy nội dung, nên VPS sập cũng
  không ảnh hưởng tới khách đang dùng WiFi.

★ Tài khoản đẩy file trên router bị khóa CẢ QUYỀN LẪN ĐỊA CHỈ NGUỒN:
      /user add name=ads group=ftp-only password=... address=10.90.0.1/32
      (group ftp-only chỉ có policy=ftp,read,write)
  Mật khẩu chỉ đi trong đường hầm WireGuard.

★ Flash router là tài nguyên khan hiếm (hEX lab còn 5,7 MiB; L009 có 128 MB NAND).
  Giới hạn dung lượng và số ảnh kiểm ở tầng VPS — xem MAX_BANNER_BYTES,
  MAX_BANNERS_PER_SITE trong .env.
"""

from __future__ import annotations

import io
import socket
from pathlib import Path

import paramiko

from ..config import settings


class PushError(Exception):
    """Đẩy file thất bại — thông điệp phải nói rõ hỏng ở bước nào."""


def _open_sftp(host: str, port: int, user: str, password: str, timeout: float):
    try:
        transport = paramiko.Transport((host, port))
        transport.banner_timeout = timeout
        transport.connect(username=user, password=password)
    except (paramiko.AuthenticationException,) as exc:
        raise PushError("Sai tài khoản hoặc mật khẩu SFTP của router") from exc
    except (socket.timeout, socket.error, paramiko.SSHException) as exc:
        raise PushError(
            f"Không mở được SFTP tới {host}:{port} — kiểm tra đường hầm WireGuard "
            f"và dịch vụ ssh/ftp trên router. ({exc})"
        ) from exc
    return transport, paramiko.SFTPClient.from_transport(transport)


def _ensure_dir(sftp: paramiko.SFTPClient, path: str) -> None:
    """Tạo thư mục nếu chưa có.

    Ghi chú thực địa: WinBox KHÔNG tạo được thư mục rỗng — kéo từng file lẻ vào
    Files thì chúng rơi ra gốc flash/ và portal không thấy gì. Qua SFTP thì tạo
    được, nhưng RouterOS cũng chỉ giữ thư mục khi trong đó có file.
    """
    try:
        sftp.stat(path)
    except FileNotFoundError:
        try:
            sftp.mkdir(path)
        except OSError as exc:
            raise PushError(f"Không tạo được thư mục `{path}` trên router: {exc}") from exc
    except OSError:
        pass


def push_files(
    host: str,
    port: int,
    user: str,
    password: str,
    remote_dir: str,
    files: list[tuple[str, Path]],
    ads_js: str,
    timeout: float | None = None,
) -> dict:
    """Đẩy danh sách file + ads.js lên `remote_dir` của router.

    `files`: danh sách (tên file trên router, đường dẫn file trên VPS).
    Trả về thống kê để ghi vào nhật ký đẩy.
    """
    timeout = timeout or settings.sftp_timeout
    transport = sftp = None
    sent = 0
    total_bytes = 0
    try:
        transport, sftp = _open_sftp(host, port, user, password, timeout)
        sftp.get_channel().settimeout(timeout)
        _ensure_dir(sftp, remote_dir)

        for remote_name, local_path in files:
            if not local_path.exists():
                raise PushError(f"Thiếu file trên VPS: {local_path.name}")
            size = local_path.stat().st_size
            sftp.put(str(local_path), f"{remote_dir}/{remote_name}")
            sent += 1
            total_bytes += size

        # ads.js sinh trong bộ nhớ, không cần ghi ra đĩa VPS trước
        data = ads_js.encode("utf-8")
        with sftp.open(f"{remote_dir}/ads.js", "wb") as fh:
            fh.write(data)
        sent += 1
        total_bytes += len(data)

    except PushError:
        raise
    except (socket.timeout, OSError, paramiko.SSHException) as exc:
        raise PushError(f"Lỗi khi đẩy file: {exc}") from exc
    finally:
        try:
            if sftp is not None:
                sftp.close()
        finally:
            if transport is not None:
                transport.close()

    return {"files": sent, "bytes": total_bytes}


def read_remote_text(host: str, port: int, user: str, password: str, path: str,
                     timeout: float | None = None) -> str:
    """Đọc lại một file text trên router — dùng để kiểm chứng sau khi đẩy."""
    timeout = timeout or settings.sftp_timeout
    transport, sftp = _open_sftp(host, port, user, password, timeout)
    try:
        buf = io.BytesIO()
        sftp.getfo(path, buf)
        return buf.getvalue().decode("utf-8", errors="replace")
    finally:
        sftp.close()
        transport.close()

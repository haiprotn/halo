"""Tiến trình quét nền — chạy riêng với tiến trình web (systemd: halo-worker).

Chạy tay để thử:   python3 -m app.worker
Chạy một vòng rồi thoát (dùng khi kiểm tra):   python3 -m app.worker --mot-vong

Nguyên tắc: tiến trình này CHỈ ĐỌC router. Mọi thay đổi trên router đều do người
dùng bấm nút ở giao diện web.
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from .config import settings
from .db import SessionLocal
from .services.monitor import prune_samples, scan_all

log = logging.getLogger("halo.worker")

_dang_chay = True


def _dung(signum, frame):  # noqa: ARG001
    global _dang_chay
    _dang_chay = False
    log.info("Nhận tín hiệu dừng, kết thúc sau vòng quét hiện tại.")


def mot_vong() -> dict:
    db = SessionLocal()
    try:
        return scan_all(db)
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    signal.signal(signal.SIGTERM, _dung)
    signal.signal(signal.SIGINT, _dung)

    if "--mot-vong" in sys.argv:
        log.info("Kết quả: %s", mot_vong())
        return

    log.info("Bắt đầu quét, chu kỳ %s giây.", settings.monitor_interval)
    lan_don_dep = 0
    while _dang_chay:
        bat_dau = time.monotonic()
        try:
            log.info("Vòng quét: %s", mot_vong())
        except Exception:  # không bao giờ để worker chết vì một lỗi lẻ
            log.exception("Vòng quét lỗi, sẽ thử lại ở chu kỳ sau")

        lan_don_dep += 1
        if lan_don_dep >= 1440:  # mỗi ~1 ngày với chu kỳ 60 giây
            lan_don_dep = 0
            db = SessionLocal()
            try:
                log.info("Dọn số đo cũ: xóa %s dòng", prune_samples(db))
            except Exception:
                log.exception("Dọn số đo lỗi")
            finally:
                db.close()

        con_lai = settings.monitor_interval - (time.monotonic() - bat_dau)
        while con_lai > 0 and _dang_chay:
            time.sleep(min(1.0, con_lai))
            con_lai -= 1.0


if __name__ == "__main__":
    main()

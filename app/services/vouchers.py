"""Sinh, đẩy, đồng bộ và xóa voucher trên User Manager v7.

Ba bài học đã trả giá, được cài cứng vào đây:

★ Bẫy #18 — ĐẨY THẤT BẠI GIỮA CHỪNG ĐỂ LẠI TÀI KHOẢN MỒ CÔI.
  Tạo user thành công nhưng gán gói cước lỗi (sai tên gói) → trên User Manager
  còn một tài khoản KHÔNG CÓ GIỚI HẠN. Khách nhập mã đó vẫn vào được và không
  hạn sử dụng nào chặn lại. `push_voucher` luôn QUAY LUI: gán gói lỗi thì xóa
  user vừa tạo, rồi mới báo lỗi.

★ Bẫy #19 — "KHÔNG THẤY TRÊN ROUTER" KHÔNG PHẢI LÀ "ĐÃ XÓA".
  Mã ở trạng thái `moi` (chưa đẩy) hay `loi` (đẩy hỏng) cũng không có trên
  router. Gộp chung làm người vận hành mất dấu vết lô bị lỗi. Chỉ mã ĐÃ TỪNG
  lên router (`pushed_at` khác None) mới được chuyển sang `da_xoa`.

★ Bẫy #20 — TRẦN 50 PHIÊN UM KHÔNG PHẢI TRẦN 50 VOUCHER.
  Voucher chưa dùng không chiếm chỗ. Cảnh báo phải nói rõ đây là trần PHIÊN
  ĐỒNG THỜI, không phải số mã được phép tồn tại.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from ..models import Voucher, VoucherBatch, now_utc
from ..security import random_code
from .routeros import RouterOS, RouterOSError

# Trần phiên đồng thời của User Manager theo license (phát hiện quyết định #1).
UM_SESSION_LIMIT = {4: 20, 5: 50, 6: 0}  # 0 = không giới hạn
HOTSPOT_LIMIT = {4: 200, 5: 500, 6: 0}


def generate_codes(db: Session, site_id: int, profile_name: str, quantity: int,
                   user_id: int | None = None, note: str = "", code_length: int = 8) -> VoucherBatch:
    """Sinh một lô mã trong CSDL (chưa đụng tới router).

    Mã sinh xong ở trạng thái `moi`. Người vận hành bấm "Đẩy lên router" ở bước sau —
    không có tác vụ nền nào tự sửa cấu hình router.
    """
    quantity = max(1, min(quantity, 500))
    batch = VoucherBatch(
        site_id=site_id, user_id=user_id, profile_name=profile_name,
        quantity=quantity, note=note,
    )
    db.add(batch)
    db.flush()

    existing = {c for (c,) in db.query(Voucher.code).filter(Voucher.site_id == site_id).all()}
    made = 0
    while made < quantity:
        code = random_code(code_length)
        if code in existing:
            continue  # trùng thì bốc lại, không tăng biến đếm
        existing.add(code)
        db.add(Voucher(
            site_id=site_id, batch_id=batch.id, code=code,
            password=code,  # một mã = tên đăng nhập = mật khẩu, khách chỉ phải gõ một chuỗi
            profile_name=profile_name, state="moi",
        ))
        made += 1
    db.flush()
    return batch


def push_voucher(ros: RouterOS, voucher: Voucher, shared_users: int = 1) -> None:
    """Đẩy một mã lên User Manager. Gán gói cước lỗi thì quay lui, không để lại rác.

    `shared_users=1` — một mã một thiết bị tại một thời điểm.
    """
    # Bước 1: tạo tài khoản
    ros.post("user-manager/user", {
        "name": voucher.code,
        "password": voucher.password,
        "shared-users": str(shared_users),
    })

    # Bước 2: gán gói cước. Hỏng ở đây là nguy hiểm nhất → phải quay lui.
    try:
        ros.post("user-manager/user/create-and-activate-profile", {
            "numbers": voucher.code,
            "profile": voucher.profile_name,
        })
    except RouterOSError as exc:
        try:
            ros.post("user-manager/user/remove", {"numbers": voucher.code})
        except RouterOSError:
            # Không xóa được thì phải nói thật, tuyệt đối không im lặng:
            # tài khoản này đang KHÔNG CÓ GIỚI HẠN trên router.
            raise RouterOSError(
                f"Gán gói cước `{voucher.profile_name}` lỗi VÀ không quay lui được. "
                f"Tài khoản `{voucher.code}` đang tồn tại trên router mà không có giới hạn — "
                f"phải vào router xóa tay ngay. Lỗi gốc: {exc}"
            ) from exc
        raise RouterOSError(
            f"Gán gói cước `{voucher.profile_name}` lỗi, đã quay lui (xóa tài khoản vừa tạo). "
            f"Kiểm tra tên gói cước có đúng trên User Manager không. Lỗi gốc: {exc}"
        ) from exc


def push_batch(db: Session, ros: RouterOS, vouchers: list[Voucher]) -> dict:
    """Đẩy nhiều mã, mã nào hỏng thì đánh dấu mã đó chứ không hủy cả lô."""
    ok = failed = 0
    errors: list[str] = []
    for v in vouchers:
        if v.state not in {"moi", "loi"}:
            continue
        try:
            push_voucher(ros, v)
        except RouterOSError as exc:
            v.state = "loi"
            v.last_error = str(exc)[:500]
            failed += 1
            if len(errors) < 5:
                errors.append(f"{v.code}: {exc}")
        else:
            v.state = "tren_router"
            v.pushed_at = now_utc()
            v.last_seen_at = now_utc()
            v.last_error = ""
            ok += 1
    db.flush()
    return {"ok": ok, "failed": failed, "errors": errors}


def sync_site(db: Session, ros: RouterOS, site_id: int) -> dict:
    """Đối chiếu CSDL với những gì đang thật sự có trên User Manager."""
    on_router: dict[str, dict] = {}
    for u in ros.get_list("user-manager/user"):
        name = str(u.get("name", ""))
        if name:
            on_router[name] = u

    rows = db.query(Voucher).filter(Voucher.site_id == site_id).all()
    seen = used = removed = 0
    for v in rows:
        info = on_router.get(v.code)
        if info is not None:
            seen += 1
            v.last_seen_at = now_utc()
            if v.state in {"moi", "loi"}:
                # Có trên router mà CSDL ghi là chưa đẩy → đồng bộ lại cho đúng
                v.state = "tren_router"
                v.pushed_at = v.pushed_at or now_utc()
            # `attributes`/`last-seen` tùy phiên bản; dùng cờ dùng-rồi nếu router có báo
            if str(info.get("disabled", "")).lower() == "true":
                v.state = "het_han"
            continue

        # Không thấy trên router — bẫy #19: chỉ mã ĐÃ TỪNG lên router mới là đã xóa
        if v.pushed_at is not None and v.state in {"tren_router", "da_dung", "het_han"}:
            v.state = "da_xoa"
            removed += 1

    used = sum(1 for v in rows if v.state == "da_dung")
    db.flush()
    return {
        "tren_router": seen,
        "da_dung": used,
        "chuyen_da_xoa": removed,
        "tong": len(rows),
    }


def delete_expired(db: Session, ros: RouterOS, site_id: int) -> dict:
    """Xóa khỏi router những mã đã hết hạn, rồi cập nhật CSDL."""
    rows = (
        db.query(Voucher)
        .filter(Voucher.site_id == site_id, Voucher.state == "het_han")
        .all()
    )
    ok = failed = 0
    for v in rows:
        try:
            ros.post("user-manager/user/remove", {"numbers": v.code})
        except RouterOSError as exc:
            v.last_error = str(exc)[:500]
            failed += 1
        else:
            v.state = "da_xoa"
            ok += 1
    db.flush()
    return {"da_xoa": ok, "loi": failed}


def session_warning(um_sessions: int, active_vouchers: int, license_level: int = 5) -> str | None:
    """Cảnh báo trần license — nói rõ 50 là trần PHIÊN ĐỒNG THỜI (bẫy #20)."""
    limit = UM_SESSION_LIMIT.get(license_level, 50)
    if not limit:
        return None
    if um_sessions >= limit:
        return (
            f"Đã chạm trần {limit} phiên User Manager đồng thời (license Level {license_level}). "
            f"Khách tiếp theo nhập mã sẽ bị từ chối. Vượt trần phải lên Level 6."
        )
    if active_vouchers > limit:
        return (
            f"Đang có {active_vouchers} voucher còn hiệu lực, nhiều hơn trần {limit}. "
            f"Đây KHÔNG phải lỗi: {limit} là trần PHIÊN ĐỒNG THỜI, voucher chưa dùng không chiếm chỗ. "
            f"Chỉ cần đảm bảo không quá {limit} khách online cùng lúc qua User Manager."
        )
    return None


def print_rows(vouchers: list[Voucher]) -> list[dict]:
    """Chuẩn bị dữ liệu in phiếu — chỉ những trường cần in."""
    return [{"code": v.code, "profile": v.profile_name, "created": v.created_at} for v in vouchers]


def expire_stale(db: Session, site_id: int, validity_days: int) -> int:
    """Đánh dấu hết hạn theo ngày sinh mã (dùng khi không đọc được `validity` từ router)."""
    cutoff = now_utc() - dt.timedelta(days=validity_days)
    rows = (
        db.query(Voucher)
        .filter(
            Voucher.site_id == site_id,
            Voucher.state.in_(["tren_router", "da_dung"]),
            Voucher.created_at < cutoff,
        )
        .all()
    )
    for v in rows:
        v.state = "het_han"
    db.flush()
    return len(rows)

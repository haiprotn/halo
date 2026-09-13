"""Sinh, đẩy, đồng bộ, in và xuất voucher."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import client_ip, current_user, get_site
from ..models import SiteStatus, User, Voucher, VoucherBatch
from ..security import decrypt_secret
from ..services import vouchers as vsvc
from ..services.audit import log_action
from ..services.routeros import RouterOS, RouterOSError
from ..templating import templates

router = APIRouter(prefix="/voucher")


def _mo_router(site) -> RouterOS:
    return RouterOS(
        site.rest_base, site.rest_user, decrypt_secret(site.rest_password_enc),
        timeout=settings.monitor_timeout,
    )


@router.get("/{site_id}", response_class=HTMLResponse)
def page(site_id: int, request: Request,
         user: User = Depends(current_user), db: Session = Depends(get_db)):
    site = get_site(db, user, site_id)

    # Đọc danh sách gói cước thật trên User Manager — không đoán tên (nguyên tắc vàng).
    goi_cuoc: list[str] = []
    loi_goi_cuoc = ""
    try:
        with _mo_router(site) as ros:
            goi_cuoc = [p.get("name", "") for p in ros.get_list("user-manager/profile") if p.get("name")]
    except (RouterOSError, Exception) as exc:  # noqa: B014
        loi_goi_cuoc = f"Chưa đọc được danh sách gói cước từ router ({exc}). Nhập tên gói bằng tay."

    lo = list(db.scalars(
        select(VoucherBatch).where(VoucherBatch.site_id == site.id)
        .order_by(VoucherBatch.created_at.desc()).limit(20)
    ))
    dem = dict(db.execute(
        select(Voucher.state, func.count(Voucher.id))
        .where(Voucher.site_id == site.id).group_by(Voucher.state)
    ).all())
    con_hieu_luc = dem.get("tren_router", 0) + dem.get("da_dung", 0)
    st = db.get(SiteStatus, site.id)

    return templates.TemplateResponse(request, "vouchers.html", {
        "user": user, "site": site, "lo": lo, "dem": dem, "goi_cuoc": goi_cuoc,
        "loi_goi_cuoc": loi_goi_cuoc,
        "canh_bao": vsvc.session_warning(st.um_sessions if st else 0, con_hieu_luc),
        "ok": request.query_params.get("ok"), "loi": request.query_params.get("loi"),
    })


@router.post("/{site_id}/sinh")
def generate(
    site_id: int,
    request: Request,
    profile_name: str = Form(...),
    quantity: int = Form(...),
    note: str = Form(""),
    day_luon: str = Form("off"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    site = get_site(db, user, site_id)
    batch = vsvc.generate_codes(
        db, site.id, profile_name.strip(), quantity, user_id=user.id, note=note.strip()
    )
    log_action(db, user, "sinh_voucher", site.name,
               f"lô #{batch.id}: {batch.quantity} mã, gói {batch.profile_name}", client_ip(request))
    db.commit()

    if day_luon != "on":
        return RedirectResponse(
            f"/voucher/{site_id}?ok=Đã sinh {batch.quantity} mã ở trạng thái `moi`. "
            f"Bấm 'Đẩy lên router' khi muốn kích hoạt.",
            status_code=303,
        )
    return _day_lo(db, user, request, site, batch.id)


def _day_lo(db: Session, user: User, request: Request, site, batch_id: int | None):
    stmt = select(Voucher).where(Voucher.site_id == site.id, Voucher.state.in_(["moi", "loi"]))
    if batch_id is not None:
        stmt = stmt.where(Voucher.batch_id == batch_id)
    ds = list(db.scalars(stmt))
    if not ds:
        return RedirectResponse(f"/voucher/{site.id}?ok=Không có mã nào cần đẩy.", status_code=303)

    try:
        with _mo_router(site) as ros:
            kq = vsvc.push_batch(db, ros, ds)
    except (RouterOSError, Exception) as exc:  # noqa: B014
        db.commit()
        return RedirectResponse(f"/voucher/{site.id}?loi=Không kết nối được router: {exc}", status_code=303)

    log_action(db, user, "day_voucher", site.name,
               f"{kq['ok']} thành công, {kq['failed']} lỗi", client_ip(request))
    db.commit()

    if kq["failed"]:
        chi_tiet = " | ".join(kq["errors"])
        return RedirectResponse(
            f"/voucher/{site.id}?loi=Đẩy {kq['ok']} mã thành công, {kq['failed']} mã lỗi "
            f"(đã quay lui, không để lại tài khoản mồ côi trên router). {chi_tiet}",
            status_code=303,
        )
    return RedirectResponse(f"/voucher/{site.id}?ok=Đã đẩy {kq['ok']} mã lên User Manager.",
                            status_code=303)


@router.post("/{site_id}/day")
def push_all(site_id: int, request: Request,
             user: User = Depends(current_user), db: Session = Depends(get_db)):
    site = get_site(db, user, site_id)
    return _day_lo(db, user, request, site, None)


@router.post("/{site_id}/day/{batch_id}")
def push_batch_route(site_id: int, batch_id: int, request: Request,
                     user: User = Depends(current_user), db: Session = Depends(get_db)):
    site = get_site(db, user, site_id)
    return _day_lo(db, user, request, site, batch_id)


@router.post("/{site_id}/dong-bo")
def sync(site_id: int, request: Request,
         user: User = Depends(current_user), db: Session = Depends(get_db)):
    site = get_site(db, user, site_id)
    try:
        with _mo_router(site) as ros:
            kq = vsvc.sync_site(db, ros, site.id)
    except (RouterOSError, Exception) as exc:  # noqa: B014
        return RedirectResponse(f"/voucher/{site_id}?loi=Không đồng bộ được: {exc}", status_code=303)

    log_action(db, user, "dong_bo_voucher", site.name, str(kq), client_ip(request))
    db.commit()
    return RedirectResponse(
        f"/voucher/{site_id}?ok=Đồng bộ xong: {kq['tren_router']} mã có trên router, "
        f"{kq['chuyen_da_xoa']} mã chuyển sang `đã xóa`. "
        f"(Mã `mới` và `lỗi` không nằm trên router nhưng KHÔNG phải đã xóa.)",
        status_code=303,
    )


@router.post("/{site_id}/xoa-het-han")
def delete_expired(site_id: int, request: Request,
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    site = get_site(db, user, site_id)
    try:
        with _mo_router(site) as ros:
            kq = vsvc.delete_expired(db, ros, site.id)
    except (RouterOSError, Exception) as exc:  # noqa: B014
        return RedirectResponse(f"/voucher/{site_id}?loi=Không xóa được: {exc}", status_code=303)
    log_action(db, user, "xoa_voucher_het_han", site.name, str(kq), client_ip(request))
    db.commit()
    return RedirectResponse(
        f"/voucher/{site_id}?ok=Đã xóa {kq['da_xoa']} mã hết hạn khỏi router ({kq['loi']} lỗi).",
        status_code=303,
    )


@router.get("/{site_id}/in/{batch_id}", response_class=HTMLResponse)
def print_batch(site_id: int, batch_id: int, request: Request,
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Trang in phiếu — mỗi mã một ô, cắt rời phát cho khách."""
    site = get_site(db, user, site_id)
    batch = db.get(VoucherBatch, batch_id)
    if batch is None or batch.site_id != site.id:
        return RedirectResponse(f"/voucher/{site_id}?loi=Không tìm thấy lô", status_code=303)
    ds = list(db.scalars(select(Voucher).where(Voucher.batch_id == batch.id).order_by(Voucher.code)))
    return templates.TemplateResponse(request, "voucher_print.html",
                                      {"site": site, "batch": batch, "vouchers": ds})


@router.get("/{site_id}/csv")
def export_csv(site_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    site = get_site(db, user, site_id)
    ds = list(db.scalars(select(Voucher).where(Voucher.site_id == site.id).order_by(Voucher.created_at)))

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ma", "mat_khau", "goi_cuoc", "trang_thai", "ngay_sinh", "ngay_day"])
    for v in ds:
        w.writerow([
            v.code, v.password, v.profile_name, v.state,
            v.created_at.isoformat() if v.created_at else "",
            v.pushed_at.isoformat() if v.pushed_at else "",
        ])
    buf.seek(0)
    # BOM để Excel trên Windows đọc đúng tiếng Việt
    data = "﻿" + buf.getvalue()
    return StreamingResponse(
        io.BytesIO(data.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="voucher-site{site.id}.csv"'},
    )

"""Quản lý banner quảng cáo và đẩy xuống router."""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import client_ip, current_user, get_site
from ..models import Banner, PushLog, User, now_utc
from ..security import decrypt_secret
from ..services import adsjs
from ..services.audit import log_action
from ..services.sftp_push import PushError, push_files
from ..templating import templates

router = APIRouter(prefix="/quang-cao")

CHO_PHEP = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def _thu_muc(site_id: int) -> Path:
    d = settings.banner_dir / f"site{site_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lam_sach_gio(value: str, mac_dinh: str) -> str:
    return value if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value or "") else mac_dinh


@router.get("/{site_id}", response_class=HTMLResponse)
def page(site_id: int, request: Request,
         user: User = Depends(current_user), db: Session = Depends(get_db)):
    site = get_site(db, user, site_id)
    banners = list(db.scalars(select(Banner).where(Banner.site_id == site.id).order_by(Banner.sort_order)))
    lan_day = list(db.scalars(
        select(PushLog).where(PushLog.site_id == site.id).order_by(PushLog.started_at.desc()).limit(10)
    ))
    return templates.TemplateResponse(request, "ads.html", {
        "user": user, "site": site, "banners": banners, "lan_day": lan_day,
        "max_bytes": settings.max_banner_bytes, "max_banners": settings.max_banners_per_site,
        "ok": request.query_params.get("ok"), "loi": request.query_params.get("loi"),
    })


@router.get("/{site_id}/anh/{banner_id}")
def image(site_id: int, banner_id: int,
          user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Xem lại ảnh đã tải lên. Đi qua kiểm quyền chứ không phục vụ file tĩnh trực tiếp."""
    site = get_site(db, user, site_id)
    b = db.get(Banner, banner_id)
    if b is None or b.site_id != site.id:
        return PlainTextResponse("Không tìm thấy", status_code=404)
    path = _thu_muc(site.id) / b.filename
    if not path.exists():
        return PlainTextResponse("File không còn trên VPS", status_code=404)
    return FileResponse(path, media_type=b.content_type)


@router.post("/{site_id}/tai-len")
async def upload(
    site_id: int,
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    site = get_site(db, user, site_id)

    so_luong = len(list(db.scalars(select(Banner.id).where(Banner.site_id == site.id))))
    if so_luong >= settings.max_banners_per_site:
        return RedirectResponse(
            f"/quang-cao/{site_id}?loi=Đã đạt trần {settings.max_banners_per_site} banner mỗi địa điểm "
            f"(flash router là tài nguyên khan hiếm). Xóa bớt ảnh cũ trước.",
            status_code=303,
        )

    duoi = CHO_PHEP.get(file.content_type or "")
    if duoi is None:
        return RedirectResponse(
            f"/quang-cao/{site_id}?loi=Chỉ nhận ảnh JPG, PNG, WEBP hoặc GIF.", status_code=303
        )

    data = await file.read()
    if len(data) > settings.max_banner_bytes:
        return RedirectResponse(
            f"/quang-cao/{site_id}?loi=Ảnh {len(data)//1024} KB vượt trần "
            f"{settings.max_banner_bytes//1024} KB. Nén nhỏ lại trước khi tải lên.",
            status_code=303,
        )

    ten_file = f"ad-{secrets.token_hex(4)}{duoi}"
    (_thu_muc(site.id) / ten_file).write_bytes(data)

    db.add(Banner(
        site_id=site.id, filename=ten_file, original_name=(file.filename or "")[:200],
        content_type=file.content_type or "image/jpeg", size_bytes=len(data),
        sort_order=so_luong,
    ))
    log_action(db, user, "tai_banner", site.name, ten_file, client_ip(request))
    db.commit()
    return RedirectResponse(f"/quang-cao/{site_id}?ok=Đã tải ảnh lên VPS. Nhớ bấm "
                            f"'Đẩy xuống router' thì khách mới thấy.", status_code=303)


@router.post("/{site_id}/sua/{banner_id}")
def edit(
    site_id: int,
    banner_id: int,
    request: Request,
    days: list[str] = Form(default=[]),
    slots: list[str] = Form(default=[]),
    start_time: str = Form("00:00"),
    end_time: str = Form("23:59"),
    link_url: str = Form(""),
    sort_order: int = Form(0),
    active: str = Form("off"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    site = get_site(db, user, site_id)
    b = db.get(Banner, banner_id)
    if b is None or b.site_id != site.id:
        return RedirectResponse(f"/quang-cao/{site_id}?loi=Không tìm thấy banner", status_code=303)

    hop_le_days = [d for d in days if d in {"0", "1", "2", "3", "4", "5", "6"}]
    hop_le_slots = [s for s in slots if s in {"login", "alogin", "status"}]
    b.days = ",".join(hop_le_days) or "0,1,2,3,4,5,6"
    b.slots = ",".join(hop_le_slots) or "login,alogin"
    b.start_time = _lam_sach_gio(start_time, "00:00")
    b.end_time = _lam_sach_gio(end_time, "23:59")
    b.link_url = link_url.strip()[:500]
    b.sort_order = sort_order
    b.active = active == "on"

    log_action(db, user, "sua_banner", site.name, b.filename, client_ip(request))
    db.commit()
    return RedirectResponse(f"/quang-cao/{site_id}?ok=Đã lưu lịch chiếu. Đẩy xuống router để áp dụng.",
                            status_code=303)


@router.post("/{site_id}/xoa/{banner_id}")
def delete(site_id: int, banner_id: int, request: Request,
           user: User = Depends(current_user), db: Session = Depends(get_db)):
    site = get_site(db, user, site_id)
    b = db.get(Banner, banner_id)
    if b is None or b.site_id != site.id:
        return RedirectResponse(f"/quang-cao/{site_id}?loi=Không tìm thấy banner", status_code=303)
    (_thu_muc(site.id) / b.filename).unlink(missing_ok=True)
    db.delete(b)
    log_action(db, user, "xoa_banner", site.name, b.filename, client_ip(request))
    db.commit()
    return RedirectResponse(
        f"/quang-cao/{site_id}?ok=Đã xóa trên VPS. File cũ vẫn còn trên router cho tới lần đẩy sau.",
        status_code=303,
    )


@router.get("/{site_id}/xem-ads-js", response_class=PlainTextResponse)
def preview_ads_js(site_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Xem trước đúng nội dung file sẽ được đẩy xuống router."""
    site = get_site(db, user, site_id)
    banners = list(db.scalars(select(Banner).where(Banner.site_id == site.id).order_by(Banner.sort_order)))
    return adsjs.render(banners, site.name)


@router.post("/{site_id}/day")
def push(site_id: int, request: Request,
         user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Đẩy toàn bộ banner đang bật + ads.js xuống router."""
    site = get_site(db, user, site_id)
    banners = list(db.scalars(
        select(Banner).where(Banner.site_id == site.id, Banner.active.is_(True)).order_by(Banner.sort_order)
    ))

    ghi = PushLog(site_id=site.id, user_id=user.id)
    db.add(ghi)
    db.flush()

    if not site.sftp_password_enc:
        ghi.ok = False
        ghi.finished_at = now_utc()
        ghi.message = "Chưa khai báo mật khẩu SFTP của router cho địa điểm này."
        db.commit()
        return RedirectResponse(f"/quang-cao/{site_id}?loi={ghi.message}", status_code=303)

    try:
        mat_khau = decrypt_secret(site.sftp_password_enc)
        files = [(b.filename, _thu_muc(site.id) / b.filename) for b in banners]
        ket_qua = push_files(
            host=site.wg_ip, port=site.sftp_port, user=site.sftp_user, password=mat_khau,
            remote_dir=site.hotspot_dir, files=files, ads_js=adsjs.render(banners, site.name),
        )
    except PushError as exc:
        ghi.ok = False
        ghi.finished_at = now_utc()
        ghi.message = str(exc)[:2000]
        log_action(db, user, "day_banner_loi", site.name, ghi.message, client_ip(request))
        db.commit()
        return RedirectResponse(f"/quang-cao/{site_id}?loi={exc}", status_code=303)
    except Exception as exc:
        ghi.ok = False
        ghi.finished_at = now_utc()
        ghi.message = f"Lỗi không lường trước: {exc}"[:2000]
        db.commit()
        return RedirectResponse(f"/quang-cao/{site_id}?loi={ghi.message}", status_code=303)

    ghi.ok = True
    ghi.finished_at = now_utc()
    ghi.files = ket_qua["files"]
    ghi.bytes_sent = ket_qua["bytes"]
    ghi.message = f"Đã đẩy {ket_qua['files']} file ({ket_qua['bytes'] // 1024} KB)."
    log_action(db, user, "day_banner", site.name, ghi.message, client_ip(request))
    db.commit()
    return RedirectResponse(f"/quang-cao/{site_id}?ok={ghi.message}", status_code=303)

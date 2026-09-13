"""Các trang chính: tổng quan, danh sách địa điểm, chi tiết địa điểm, báo cáo, nhật ký."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import client_ip, current_user, get_site, visible_sites
from ..models import Alert, Banner, CheckRun, Sample, Site, SiteStatus, User, Voucher, now_utc
from ..security import decrypt_secret
from ..services import checks as check_svc
from ..services import monitor as monitor_svc
from ..services.audit import log_action, recent
from ..services.routeros import RouterOS, RouterOSError
from ..services.vouchers import session_warning
from ..templating import templates

router = APIRouter()


# ---------------------------------------------------------------- tổng quan
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    sites = visible_sites(db, user)
    ids = [s.id for s in sites]
    statuses = {st.site_id: st for st in db.scalars(select(SiteStatus).where(SiteStatus.site_id.in_(ids or [-1])))}
    alerts = monitor_svc.open_alerts(db, ids)

    tong_online = sum(1 for s in sites if statuses.get(s.id) and statuses[s.id].online)
    tong_khach = sum(statuses[s.id].hotspot_active for s in sites if s.id in statuses)
    tong_um = sum(statuses[s.id].um_sessions for s in sites if s.id in statuses)

    voucher_con = db.scalar(
        select(func.count(Voucher.id)).where(
            Voucher.site_id.in_(ids or [-1]), Voucher.state.in_(["tren_router", "da_dung"])
        )
    ) or 0

    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user,
        "sites": sites,
        "statuses": statuses,
        "alerts": alerts,
        "tong_online": tong_online,
        "tong_khach": tong_khach,
        "tong_um": tong_um,
        "voucher_con": voucher_con,
        "canh_bao_um": session_warning(tong_um, voucher_con),
    })


# ---------------------------------------------------------------- địa điểm
@router.get("/dia-diem", response_class=HTMLResponse)
def site_list(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    sites = visible_sites(db, user)
    ids = [s.id for s in sites]
    statuses = {st.site_id: st for st in db.scalars(select(SiteStatus).where(SiteStatus.site_id.in_(ids or [-1])))}
    return templates.TemplateResponse(request, "sites.html", {
        "user": user, "sites": sites, "statuses": statuses,
    })


@router.get("/dia-diem/{site_id}", response_class=HTMLResponse)
def site_detail(
    site_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    site = get_site(db, user, site_id)
    status = db.get(SiteStatus, site.id)
    alerts = monitor_svc.open_alerts(db, [site.id])
    last_check = db.scalar(
        select(CheckRun).where(CheckRun.site_id == site.id).order_by(CheckRun.run_at.desc()).limit(1)
    )
    banners = list(db.scalars(select(Banner).where(Banner.site_id == site.id).order_by(Banner.sort_order)))

    dem_voucher = dict(
        db.execute(
            select(Voucher.state, func.count(Voucher.id))
            .where(Voucher.site_id == site.id)
            .group_by(Voucher.state)
        ).all()
    )

    return templates.TemplateResponse(request, "site_detail.html", {
        "user": user, "site": site, "status": status, "alerts": alerts,
        "last_check": last_check, "banners": banners, "dem_voucher": dem_voucher,
        "ok": request.query_params.get("ok"), "loi": request.query_params.get("loi"),
    })


@router.post("/dia-diem/{site_id}/quet")
def scan_now(site_id: int, request: Request,
             user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Quét trạng thái ngay, không đợi vòng quét 60 giây của tác vụ nền."""
    site = get_site(db, user, site_id)
    st = monitor_svc.scan_site(db, site)
    db.commit()
    thong_bao = "Đã cập nhật trạng thái." if st.online else f"Không đọc được: {st.last_error}"
    khoa = "ok" if st.online else "loi"
    return RedirectResponse(f"/dia-diem/{site_id}?{khoa}={thong_bao}", status_code=303)


@router.post("/dia-diem/{site_id}/kiem-tra")
def run_checks(site_id: int, request: Request,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Chạy bảng kiểm tra cấu hình — 14 phép đọc, không sửa gì trên router."""
    site = get_site(db, user, site_id)
    try:
        password = decrypt_secret(site.rest_password_enc)
        with RouterOS(site.rest_base, site.rest_user, password, timeout=settings.monitor_timeout) as ros:
            results = check_svc.run_all(ros)
    except (RouterOSError, Exception) as exc:  # noqa: B014 - muốn bắt cả lỗi giải mã
        return RedirectResponse(
            f"/dia-diem/{site_id}?loi=Không chạy được bảng kiểm tra: {exc}", status_code=303
        )

    passed = sum(1 for r in results if r["ok"])
    db.add(CheckRun(site_id=site.id, passed=passed, total=len(results), results=results))

    hong = [r["name"] for r in results if not r["ok"] and r.get("core")]
    if hong:
        monitor_svc.open_alert(
            db, site.id, "check_failed",
            f"{len(hong)} mục kiểm tra cốt lõi chưa đạt: {', '.join(hong[:3])}"
            + ("…" if len(hong) > 3 else ""),
        )
    else:
        monitor_svc.close_alert(db, site.id, "check_failed")

    log_action(db, user, "kiem_tra_cau_hinh", site.name, f"{passed}/{len(results)} đạt", client_ip(request))
    db.commit()
    return RedirectResponse(f"/dia-diem/{site_id}#kiem-tra", status_code=303)


# ---------------------------------------------------------------- báo cáo
@router.get("/bao-cao", response_class=HTMLResponse)
def reports(
    request: Request,
    ngay: int = 14,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Đỉnh khách online theo ngày — số này LUÔN có, không phụ thuộc COLLECT_SESSIONS.

    Nhắc bắt buộc trên trang: MAC randomization của iOS/Android làm số 'thiết bị
    duy nhất' cao hơn số người thật. Không dùng làm cam kết với nhà tài trợ khi
    chưa đo pilot.
    """
    ngay = max(1, min(ngay, 90))
    sites = visible_sites(db, user)
    ids = [s.id for s in sites]
    tu = now_utc() - dt.timedelta(days=ngay)

    rows = db.execute(
        select(
            Sample.site_id,
            func.date(Sample.taken_at).label("ngay"),
            func.max(Sample.hotspot_active).label("dinh_khach"),
            func.max(Sample.um_sessions).label("dinh_um"),
            func.avg(Sample.hotspot_active).label("tb_khach"),
        )
        .where(Sample.site_id.in_(ids or [-1]), Sample.taken_at >= tu)
        .group_by(Sample.site_id, func.date(Sample.taken_at))
        .order_by(func.date(Sample.taken_at).desc())
    ).all()

    ten_site = {s.id: s.name for s in sites}
    bang = [
        {
            "site": ten_site.get(r.site_id, "?"),
            "ngay": r.ngay,
            "dinh_khach": r.dinh_khach or 0,
            "dinh_um": r.dinh_um or 0,
            "tb_khach": round(float(r.tb_khach or 0), 1),
        }
        for r in rows
    ]

    return templates.TemplateResponse(request, "reports.html", {
        "user": user, "bang": bang, "ngay": ngay,
        "collect_sessions": settings.collect_sessions,
    })


# ---------------------------------------------------------------- nhật ký
@router.get("/nhat-ky", response_class=HTMLResponse)
def audit_page(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not user.is_admin:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "audit.html", {"user": user, "rows": recent(db)})

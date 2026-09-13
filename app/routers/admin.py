"""Trang quản trị: khách hàng, tài khoản, địa điểm, khối lệnh WireGuard cho router.

Chỉ tài khoản role='admin' vào được — tài khoản tenant gọi vào đây nhận 403
(xem `admin_required` trong app/deps.py).

Quy trình thêm một khách hàng, 6 bước (giữ nguyên từ bản Node):
  1. Thêm khách hàng
  2. Thêm địa điểm  → hệ thống tự cấp IP WireGuard và sinh cặp khóa cho router
  3. Thêm tài khoản, gắn vào khách hàng đó
  4. Bấm "Khối lệnh cho router" → dán vào router (Safe Mode trước), đổi mật khẩu
  5. Trên VPS: wg set wg0 peer <pub> allowed-ips 10.90.0.X/32 && wg-quick save wg0
  6. Nhập mật khẩu router vào trang này → Lưu → sang mục quảng cáo bấm "Đẩy xuống router"
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_required, client_ip
from ..models import Site, SiteStatus, Tenant, User
from ..security import encrypt_secret, hash_password, random_code
from ..services import wireguard as wg
from ..services.audit import log_action
from ..templating import templates

router = APIRouter(prefix="/quan-tri")


@router.get("", response_class=HTMLResponse)
def page(request: Request, user: User = Depends(admin_required), db: Session = Depends(get_db)):
    tenants = list(db.scalars(select(Tenant).order_by(Tenant.name)))
    users = list(db.scalars(select(User).order_by(User.username)))
    sites = list(db.scalars(select(Site).order_by(Site.name)))
    statuses = {s.site_id: s for s in db.scalars(select(SiteStatus))}
    return templates.TemplateResponse(request, "admin.html", {
        "user": user, "tenants": tenants, "users": users, "sites": sites, "statuses": statuses,
        "ok": request.query_params.get("ok"), "loi": request.query_params.get("loi"),
    })


# ---------------------------------------------------------------- khách hàng
@router.post("/khach-hang")
def add_tenant(request: Request, name: str = Form(...), note: str = Form(""),
               user: User = Depends(admin_required), db: Session = Depends(get_db)):
    name = name.strip()
    if db.scalar(select(Tenant).where(Tenant.name == name)):
        return RedirectResponse("/quan-tri?loi=Tên khách hàng đã tồn tại", status_code=303)
    t = Tenant(name=name, note=note.strip())
    db.add(t)
    log_action(db, user, "them_khach_hang", name, ip=client_ip(request))
    db.commit()
    return RedirectResponse(f"/quan-tri?ok=Đã thêm khách hàng {name}", status_code=303)


# ---------------------------------------------------------------- tài khoản
@router.post("/tai-khoan")
def add_user(
    request: Request,
    username: str = Form(...),
    full_name: str = Form(""),
    role: str = Form("tenant"),
    tenant_id: str = Form(""),
    password: str = Form(""),
    admin: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    username = username.strip().lower()
    if db.scalar(select(User).where(User.username == username)):
        return RedirectResponse("/quan-tri?loi=Tên đăng nhập đã tồn tại", status_code=303)
    if role not in {"admin", "tenant"}:
        return RedirectResponse("/quan-tri?loi=Vai trò không hợp lệ", status_code=303)
    if role == "tenant" and not tenant_id:
        return RedirectResponse("/quan-tri?loi=Tài khoản khách hàng phải gắn vào một khách hàng",
                                status_code=303)

    mat_khau = password.strip() or random_code(12)
    u = User(
        username=username, full_name=full_name.strip(), role=role,
        tenant_id=int(tenant_id) if tenant_id else None,
        password_hash=hash_password(mat_khau),
    )
    db.add(u)
    log_action(db, admin, "them_tai_khoan", username, f"role={role}", client_ip(request))
    db.commit()
    return RedirectResponse(
        f"/quan-tri?ok=Đã tạo tài khoản {username}. Mật khẩu: {mat_khau} "
        f"(chỉ hiện một lần — chép lại ngay và bảo người dùng đổi sau lần đăng nhập đầu).",
        status_code=303,
    )


@router.post("/tai-khoan/{user_id}/bat-tat")
def toggle_user(user_id: int, request: Request,
                admin: User = Depends(admin_required), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        return RedirectResponse("/quan-tri?loi=Không tìm thấy tài khoản", status_code=303)
    if u.id == admin.id:
        return RedirectResponse("/quan-tri?loi=Không tự khóa tài khoản của chính mình được",
                                status_code=303)
    u.active = not u.active
    log_action(db, admin, "bat_tat_tai_khoan", u.username, f"active={u.active}", client_ip(request))
    db.commit()
    return RedirectResponse(f"/quan-tri?ok=Đã {'mở' if u.active else 'khóa'} tài khoản {u.username}",
                            status_code=303)


@router.post("/tai-khoan/{user_id}/dat-lai-mat-khau")
def reset_password(user_id: int, request: Request,
                   admin: User = Depends(admin_required), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        return RedirectResponse("/quan-tri?loi=Không tìm thấy tài khoản", status_code=303)
    moi = random_code(12)
    u.password_hash = hash_password(moi)
    u.failed_logins = 0
    u.locked_until = None
    log_action(db, admin, "dat_lai_mat_khau", u.username, ip=client_ip(request))
    db.commit()
    return RedirectResponse(f"/quan-tri?ok=Mật khẩu mới của {u.username}: {moi} (chỉ hiện một lần)",
                            status_code=303)


# ---------------------------------------------------------------- địa điểm
@router.post("/dia-diem")
def add_site(
    request: Request,
    tenant_id: int = Form(...),
    name: str = Form(...),
    address: str = Form(""),
    hotspot_dir: str = Form("hotspot-vn"),
    admin: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """Thêm địa điểm: tự cấp IP WireGuard còn trống và sinh cặp khóa cho router."""
    if db.get(Tenant, tenant_id) is None:
        return RedirectResponse("/quan-tri?loi=Không tìm thấy khách hàng", status_code=303)
    try:
        ip = wg.next_wg_ip(db)
    except RuntimeError as exc:
        return RedirectResponse(f"/quan-tri?loi={exc}", status_code=303)

    priv, pub = wg.generate_keypair()
    site = Site(
        tenant_id=tenant_id, name=name.strip(), address=address.strip(),
        wg_ip=ip, wg_public_key=pub, wg_private_key_enc=encrypt_secret(priv),
        hotspot_dir=hotspot_dir.strip() or "hotspot-vn",
    )
    db.add(site)
    db.flush()
    db.add(SiteStatus(site_id=site.id))
    log_action(db, admin, "them_dia_diem", site.name, f"wg={ip}", client_ip(request))
    db.commit()
    return RedirectResponse(
        f"/quan-tri?ok=Đã thêm địa điểm {site.name}, cấp địa chỉ WireGuard {ip}. "
        f"Bước tiếp: lấy khối lệnh cho router.",
        status_code=303,
    )


@router.post("/dia-diem/{site_id}")
def update_site(
    site_id: int,
    request: Request,
    name: str = Form(...),
    address: str = Form(""),
    hotspot_dir: str = Form("hotspot-vn"),
    rest_user: str = Form("vsp"),
    rest_port: int = Form(443),
    rest_password: str = Form(""),
    sftp_user: str = Form("ads"),
    sftp_port: int = Form(22),
    sftp_password: str = Form(""),
    active: str = Form("off"),
    admin: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """Cập nhật địa điểm. Mật khẩu để trống = GIỮ NGUYÊN mật khẩu cũ, không xóa."""
    site = db.get(Site, site_id)
    if site is None:
        return RedirectResponse("/quan-tri?loi=Không tìm thấy địa điểm", status_code=303)

    site.name = name.strip()
    site.address = address.strip()
    site.hotspot_dir = hotspot_dir.strip() or "hotspot-vn"
    site.rest_user = rest_user.strip() or "vsp"
    site.rest_port = rest_port
    site.sftp_user = sftp_user.strip() or "ads"
    site.sftp_port = sftp_port
    site.active = active == "on"

    if rest_password.strip():
        site.rest_password_enc = encrypt_secret(rest_password.strip())
    if sftp_password.strip():
        site.sftp_password_enc = encrypt_secret(sftp_password.strip())

    log_action(db, admin, "sua_dia_diem", site.name, ip=client_ip(request))
    db.commit()
    return RedirectResponse(f"/quan-tri?ok=Đã lưu địa điểm {site.name}", status_code=303)


@router.get("/dia-diem/{site_id}/rsc", response_class=PlainTextResponse)
def site_rsc(
    site_id: int,
    vps_public_key: str = "",
    vps_endpoint: str = "",
    admin: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """Khối lệnh `.rsc` dán vào router.

    Mật khẩu trong khối này là mật khẩu SINH MỚI, chưa lưu vào CSDL — dán xong
    phải nhập lại vào form địa điểm để hệ thống mã hóa và lưu. Làm vậy để mật
    khẩu không bao giờ được tạo ra rồi nằm lơ lửng ở hai nơi khác nhau.
    """
    from ..security import decrypt_secret

    site = db.get(Site, site_id)
    if site is None:
        return PlainTextResponse("Không tìm thấy địa điểm", status_code=404)

    priv = decrypt_secret(site.wg_private_key_enc) if site.wg_private_key_enc else "(chưa sinh khóa)"
    return wg.router_rsc(
        site,
        private_key=priv,
        vps_public_key=vps_public_key or "<DÁN KHÓA CÔNG KHAI CỦA VPS VÀO ĐÂY>",
        vps_endpoint=vps_endpoint or "<TÊN MIỀN HOẶC IP CÔNG CỘNG CỦA VPS>",
        rest_password=random_code(16),
        sftp_password=random_code(16),
    )

"""Đăng nhập, đăng xuất, đổi mật khẩu.

Chống dò mật khẩu: sai 5 lần liên tiếp thì khóa tài khoản 5 phút. Thông báo lỗi
cố tình KHÔNG nói rõ sai tên hay sai mật khẩu — nói rõ là giúp người dò biết tài
khoản nào có thật.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import COOKIE_NAME, client_ip, current_user, current_user_optional
from ..models import User, now_utc
from ..security import hash_password, make_session_token, verify_password
from ..services.audit import log_action
from ..templating import templates

router = APIRouter()

MAX_FAILED = 5
LOCK_MINUTES = 5


@router.get("/dang-nhap", response_class=HTMLResponse)
def login_form(request: Request, user: User | None = Depends(current_user_optional)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"loi": request.query_params.get("loi")})


@router.post("/dang-nhap")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    now = now_utc()

    if user is not None and user.locked_until and user.locked_until > now:
        con_lai = int((user.locked_until - now).total_seconds() // 60) + 1
        return templates.TemplateResponse(
            request, "login.html",
            {"loi": f"Tài khoản đang bị khóa tạm thời, thử lại sau {con_lai} phút."},
            status_code=429,
        )

    if user is None or not user.active or not verify_password(password, user.password_hash):
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED:
                user.locked_until = now + dt.timedelta(minutes=LOCK_MINUTES)
                user.failed_logins = 0
            db.commit()
        return templates.TemplateResponse(
            request, "login.html",
            {"loi": "Tên đăng nhập hoặc mật khẩu không đúng."},
            status_code=401,
        )

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    log_action(db, user, "dang_nhap", ip=client_ip(request))
    db.commit()

    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        make_session_token(user.id),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=12 * 3600,
        path="/",
    )
    return resp


@router.get("/dang-xuat")
def logout():
    resp = RedirectResponse("/dang-nhap", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.get("/doi-mat-khau", response_class=HTMLResponse)
def change_password_form(request: Request, user: User = Depends(current_user)):
    return templates.TemplateResponse(request, "doi_mat_khau.html", {"user": user})


@router.post("/doi-mat-khau", response_class=HTMLResponse)
def change_password(
    request: Request,
    cu: str = Form(...),
    moi: str = Form(...),
    lap_lai: str = Form(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ctx = {"user": user}
    if not verify_password(cu, user.password_hash):
        ctx["loi"] = "Mật khẩu hiện tại không đúng."
    elif len(moi) < 10:
        ctx["loi"] = "Mật khẩu mới phải dài ít nhất 10 ký tự."
    elif moi != lap_lai:
        ctx["loi"] = "Hai lần nhập mật khẩu mới không khớp."
    else:
        user.password_hash = hash_password(moi)
        log_action(db, user, "doi_mat_khau", ip=client_ip(request))
        db.commit()
        ctx["ok"] = "Đã đổi mật khẩu."
    return templates.TemplateResponse(request, "doi_mat_khau.html", ctx)

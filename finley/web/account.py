"""Sign up, log in, onboarding and settings. The account logic itself lives in finley/accounts.py."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import accounts
from ..accounts import SESSION_COOKIE, AccountError
from .deps import current_user, profile, redirect, render, require_user, safe_next, services

router = APIRouter()

SECTIONS = {"business", "ai", "sending", "defaults", "onboarding"}


def _logged_in(request: Request, user_id: int, to: str) -> RedirectResponse:
    resp = RedirectResponse(safe_next(to), status_code=303)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(SESSION_COOKIE, accounts.start_session(services(request).db, user_id),
                    max_age=accounts.SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=secure)
    return resp


def _signups_closed(request: Request) -> bool:
    svc = services(request)
    return not svc.signups_open and svc.db.one("SELECT 1 FROM users") is not None


@router.get("/signup")
def signup_page(request: Request, next: str = "/app/welcome"):
    if current_user(request):
        return RedirectResponse("/app", status_code=303)
    return render(request, "site/signup.html", error="", closed=_signups_closed(request), next=next, form={})


@router.post("/signup")
def signup(request: Request, email: str = Form(...), password: str = Form(...), name: str = Form(""),
           next: str = Form("/app/welcome")):
    if _signups_closed(request):
        raise HTTPException(403, "Sign-ups are closed on this server.")
    try:
        uid = accounts.create_user(services(request).db, email, password, name)
    except AccountError as e:
        return render(request, "site/signup.html", status_code=400, error=str(e), closed=False, next=next,
                      form={"email": email, "name": name})
    return _logged_in(request, uid, next)


@router.get("/login")
def login_page(request: Request, next: str = "/app"):
    if current_user(request):
        return RedirectResponse(safe_next(next), status_code=303)
    return render(request, "site/login.html", error="", next=next, email="",
                  signups_open=not _signups_closed(request))


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/app")):
    uid = accounts.authenticate(services(request).db, email, password)
    if uid is None:
        return render(request, "site/login.html", error="That email and password don't match.",
                      next=next, email=email, signups_open=not _signups_closed(request))
    return _logged_in(request, uid, next)


@router.post("/logout")
def logout(request: Request):
    accounts.end_session(services(request).db, request.cookies.get(SESSION_COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.get("/app/welcome")
def welcome(request: Request):
    require_user(request)
    return render(request, "app/welcome.html", s=profile(request), can_store_secrets=services(request).vault.enabled)


@router.get("/app/settings")
def settings_page(request: Request):
    user = require_user(request)
    svc = services(request)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    spend = svc.db.one(
        "SELECT COALESCE(SUM(ev.cost_usd), 0) AS c FROM events ev JOIN campaigns c ON c.id = ev.campaign_id "
        "WHERE c.user_id = ? AND ev.ts >= ?", user["id"], f"{month}-01")["c"]
    suppressions = svc.db.all("SELECT email, reason, ts FROM suppressions WHERE user_id = ? ORDER BY ts DESC LIMIT 200",
                              user["id"])
    return render(request, "app/settings.html", active="settings", s=profile(request),
                  can_store_secrets=svc.vault.enabled, suppressions=suppressions,
                  usage={"month_cost": float(spend or 0)})


@router.post("/app/settings/password")
def change_password(request: Request, current: str = Form(...), new: str = Form(...)):
    user = require_user(request)
    try:
        accounts.change_password(services(request).db, user["id"], current, new)
    except AccountError as e:
        return redirect("/app/settings", err=str(e), anchor="account")
    return redirect("/app/settings", msg="Password changed.", anchor="account")


@router.post("/app/settings/dnc")
def add_suppressions(request: Request, entries: str = Form("")):
    user = require_user(request)
    db = services(request).db
    added = 0
    for raw in entries.replace(",", "\n").splitlines():
        value = raw.strip().lower()
        if value and ("@" in value or "." in value):
            db.suppress(value, "added by you", user_id=user["id"])
            added += 1
    return redirect("/app/settings", msg=f"Added {added} to your do-not-contact list.", anchor="dnc")


@router.post("/app/settings/delete-account")
def delete_account(request: Request, confirm_email: str = Form(...)):
    user = require_user(request)
    if confirm_email.strip().lower() != user["email"]:
        return redirect("/app/settings", err="Type your email exactly to confirm.", anchor="danger")
    accounts.delete_user(services(request).db, user["id"])
    resp = RedirectResponse("/signup", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.post("/app/settings/{section}")
async def save_section(request: Request, section: str):
    user = require_user(request)
    if section not in SECTIONS:
        raise HTTPException(404)
    form = {k: v for k, v in (await request.form()).items() if k != "section"}
    svc = services(request)
    to = "/app/find" if section == "onboarding" else "/app/settings"
    try:
        accounts.save_settings(svc.db, svc.vault, user["id"], form)
    except AccountError as e:
        back = "/app/welcome" if section == "onboarding" else "/app/settings"
        return redirect(back, err=str(e), anchor=section)
    if section == "onboarding":
        return redirect(to, msg="You're set up. Describe your ideal customer to find your first leads.")
    return redirect(to, msg="Saved.", anchor=section)


@router.get("/api/me")
def me(request: Request):
    user = require_user(request)
    return {"id": user["id"], "email": user["email"], "name": user["name"], "settings": profile(request)}

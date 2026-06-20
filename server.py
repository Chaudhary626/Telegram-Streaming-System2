"""
TG Stream Server — Complete System

Single process: FastAPI + Pyrogram Bot
Routes: /stream, /watch, /embed, /api, /admin
Streaming: MTProto (NO Bot API getFile, NO 20MB limit)
"""
import re
import time
import hmac
import hashlib
import logging
import asyncio
from typing import Optional
from html import escape as html_escape

from fastapi import FastAPI, Request, HTTPException, Form, Response
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pyrogram import Client, filters, raw
from pyrogram.types import Message
from urllib.parse import quote as url_quote

from config import (
    API_ID, API_HASH, BOT_TOKEN, CHANNEL_ID,
    STREAM_SECRET, STREAM_HOST, STREAM_PORT,
    STREAM_BASE_URL, TOKEN_LIFETIME, CHUNK_SIZE,
    ALLOWED_ORIGINS, ADMIN_PASSWORD, ADMIN_TELEGRAM_ID,
    validate as validate_config,
)
from streamer import TelegramStreamer
import database as db
from player import WATCH_PAGE, EMBED_PAGE, ADMIN_BASE, ADMIN_LOGIN

# ══════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tg-stream")

# ══════════════════════════════════════════════════════════════
# PYROGRAM CLIENT
# ══════════════════════════════════════════════════════════════
tg_client = Client(
    name="stream_bot", api_id=API_ID, api_hash=API_HASH,
    bot_token=BOT_TOKEN, in_memory=True, no_updates=False,
)
streamer: Optional[TelegramStreamer] = None

# ══════════════════════════════════════════════════════════════
# FASTAPI APP
# ══════════════════════════════════════════════════════════════
app = FastAPI(title="TG Stream Server", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_methods=["GET", "HEAD", "OPTIONS", "POST"],
    allow_headers=["Range", "Content-Type"],
    expose_headers=["Content-Range", "Content-Length", "Accept-Ranges",
                     "Content-Type", "Content-Disposition"],
)

BASE = lambda: STREAM_BASE_URL or f"http://localhost:{STREAM_PORT}"


# ══════════════════════════════════════════════════════════════
# STARTUP / SHUTDOWN
# ══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    global streamer
    errors = validate_config()
    if errors:
        for e in errors:
            logger.error(f"Config error: {e}")
        return
    await tg_client.start()
    streamer = TelegramStreamer(tg_client)
    try:
        db.ensure_tables()
    except Exception as e:
        logger.warning(f"DB init: {e}")
    _register_bot_handlers()
    me = await tg_client.get_me()
    logger.info(f"✅ Server started | Bot: @{me.username} | MTProto: ON | Port: {STREAM_PORT}")


@app.on_event("shutdown")
async def shutdown():
    if tg_client.is_connected:
        await tg_client.stop()
    logger.info("Server stopped.")


# ══════════════════════════════════════════════════════════════
# TOKEN HELPERS
# ══════════════════════════════════════════════════════════════

def _hmac_sign(payload: str) -> str:
    return hmac.new(STREAM_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _validate_stream_token(file_id: str, token: str, expires: int, file_size: int = 0) -> bool:
    if not token or not expires or time.time() > expires:
        return False
    expected = _hmac_sign(f"{file_id}:{expires}:{file_size}")
    return hmac.compare_digest(token, expected)


def _make_stream_url(file_id: str, file_size: int = 0) -> str:
    expires = int(time.time()) + TOKEN_LIFETIME
    token = _hmac_sign(f"{file_id}:{expires}:{file_size}")
    return f"{BASE()}/stream/{url_quote(file_id)}?token={token}&expires={expires}&size={file_size}"


def _page_token(slug: str) -> str:
    return _hmac_sign(f"page:{slug}")[:32]


def _validate_page_token(slug: str, token: str) -> bool:
    return hmac.compare_digest(_page_token(slug), token)


def _admin_cookie_value() -> str:
    return _hmac_sign(f"admin:{ADMIN_PASSWORD}")[:48]


def _is_admin(request: Request) -> bool:
    return request.cookies.get("admin_token") == _admin_cookie_value()


def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s-]+', '-', s)
    return s.strip('-')[:200]


def _format_size(b: int) -> str:
    if b >= 1073741824: return f"{b/1073741824:.2f} GB"
    if b >= 1048576: return f"{b/1048576:.1f} MB"
    return f"{b/1024:.0f} KB"


def _detect_quality(w: int, h: int) -> str:
    r = min(w, h) if w > 0 and h > 0 else max(w, h)
    if r >= 2160: return "2160p"
    if r >= 1440: return "1440p"
    if r >= 1080: return "1080p"
    if r >= 720: return "720p"
    return "480p"


# ══════════════════════════════════════════════════════════════
# /stream/{file_id} — MTProto raw stream (existing, unchanged)
# ══════════════════════════════════════════════════════════════

@app.get("/stream/{file_id}")
async def stream_video(file_id: str, request: Request,
                       token: str = "", expires: int = 0, size: int = 0):
    if not streamer:
        raise HTTPException(503, "Not initialized")
    if not _validate_stream_token(file_id, token, expires, size):
        raise HTTPException(403, "Invalid or expired token")

    file_size = size
    if not file_size:
        vid = db.get_video_by_file_id(file_id)
        if vid:
            file_size = vid.get("file_size", 0) or 0
        if not file_size:
            try: file_size = await streamer.get_file_size(file_id)
            except: pass
    else:
        streamer.cache_file_size(file_id, file_size)

    range_hdr = request.headers.get("range", "")
    start, end, is_range = 0, (file_size - 1 if file_size else None), False

    if range_hdr and file_size:
        is_range = True
        parts = range_hdr.replace("bytes=", "").split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
        if end >= file_size: end = file_size - 1
        if start > end: raise HTTPException(416, "Range not satisfiable")

    content_len = (end - start + 1) if end is not None else None
    headers = {
        "Content-Type": "video/mp4", "Accept-Ranges": "bytes",
        "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store", "X-Stream-Method": "mtproto",
    }
    if file_size and content_len:
        headers["Content-Length"] = str(content_len)
    if is_range and file_size:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    async def gen():
        try:
            async for chunk in streamer.stream(file_id, offset=start, end=end):
                yield chunk
        except Exception as e:
            logger.error(f"Stream error: {e}")

    return StreamingResponse(gen(), status_code=206 if is_range else 200,
                             headers=headers, media_type="video/mp4")


@app.head("/stream/{file_id}")
async def stream_head(file_id: str, token: str = "", expires: int = 0, size: int = 0):
    if not _validate_stream_token(file_id, token, expires, size):
        raise HTTPException(403, "Invalid token")
    headers = {"Content-Type": "video/mp4", "Accept-Ranges": "bytes", "X-Stream-Method": "mtproto"}
    if size: headers["Content-Length"] = str(size)
    return Response(headers=headers)


# ══════════════════════════════════════════════════════════════
# /watch/{slug} — Full custom player page
# ══════════════════════════════════════════════════════════════

@app.get("/watch/{slug}", response_class=HTMLResponse)
async def watch_page(slug: str, request: Request):
    content = db.get_content_by_slug(slug)
    if not content:
        raise HTTPException(404, "Content not found")
    base = BASE()
    html = WATCH_PAGE.format(
        title=html_escape(content["title"]),
        slug=slug,
        api_sources_url=f"{base}/api/sources/{url_quote(slug)}",
        api_ads_url=f"{base}/api/ads",
    )
    # Log view
    ip_hash = hashlib.md5((request.client.host or "").encode()).hexdigest()[:16]
    ua = (request.headers.get("user-agent") or "")[:255]
    try: db.log_view(content["id"], ip_hash=ip_hash, user_agent=ua)
    except: pass
    return HTMLResponse(content=html)


# ══════════════════════════════════════════════════════════════
# /embed/{slug} — iFrame player
# ══════════════════════════════════════════════════════════════

@app.get("/embed/{slug}", response_class=HTMLResponse)
async def embed_page(slug: str):
    content = db.get_content_by_slug(slug)
    if not content:
        raise HTTPException(404, "Content not found")
    base = BASE()
    html = EMBED_PAGE.format(
        api_sources_url=f"{base}/api/sources/{url_quote(slug)}",
        api_ads_url=f"{base}/api/ads",
    )
    return HTMLResponse(content=html)


# ══════════════════════════════════════════════════════════════
# /api/sources/{slug} — JSON: all signed stream URLs
# ══════════════════════════════════════════════════════════════

@app.get("/api/sources/{slug}")
async def api_sources(slug: str):
    content = db.get_content_by_slug(slug)
    if not content:
        return JSONResponse({"success": False, "error": "Not found"}, 404)

    sources = db.get_sources_by_content(content["id"])
    result = []
    for s in sources:
        result.append({
            "language": s["language"],
            "quality": s["quality"],
            "url": _make_stream_url(s["file_id"], s["file_size"] or 0),
            "file_size": s["file_size"] or 0,
            "duration": s["duration"] or 0,
            "label": s["label"] or f"{s['language']} {s['quality']}",
        })

    return JSONResponse({
        "success": True,
        "title": content["title"],
        "slug": content["slug"],
        "sources": result,
    })


# ══════════════════════════════════════════════════════════════
# /api/ads — JSON: active ads for player
# ══════════════════════════════════════════════════════════════

@app.get("/api/ads")
async def api_ads():
    ads = db.get_active_ads()
    return JSONResponse([{
        "ad_type": a["ad_type"], "ad_url": a["ad_url"],
        "ad_html": a["ad_html"], "position": a["position"],
        "duration": a["duration"], "is_active": bool(a["is_active"]),
    } for a in ads])


# ══════════════════════════════════════════════════════════════
# /health
# ══════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    connected = tg_client.is_connected if tg_client else False
    return JSONResponse({
        "status": "ok" if connected else "degraded",
        "telegram": "connected" if connected else "disconnected",
        "protocol": "MTProto (NO Bot API getFile)",
        "twenty_mb_limit": "BYPASSED",
        "max_file": "4GB", "chunk": f"{CHUNK_SIZE//1024}KB",
    })


# ══════════════════════════════════════════════════════════════
# ADMIN PANEL
# ══════════════════════════════════════════════════════════════

def _admin_page(body: str, page_title: str = "Dashboard", active: str = "dashboard") -> HTMLResponse:
    nav = {f"nav_{k}": ('class="active"' if k == active else '') for k in
           ["dashboard", "content", "users", "ads", "logs"]}
    return HTMLResponse(ADMIN_BASE.format(page_title=page_title, body=body, **nav))


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page():
    return HTMLResponse(ADMIN_LOGIN.format(error=""))


@app.post("/admin/login")
async def admin_login(password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return HTMLResponse(ADMIN_LOGIN.format(
            error='<p class="err">Wrong password</p>'), status_code=401)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie("admin_token", _admin_cookie_value(), httponly=True, max_age=86400)
    return resp


@app.get("/admin")
async def admin_dashboard(request: Request):
    if not _is_admin(request):
        return RedirectResponse("/admin/login")
    content_list = db.list_content()
    users = db.list_api_users()
    stats = db.get_view_stats()

    body = f"""
    <h1>📊 Dashboard</h1>
    <div class="grid">
      <div class="card stat"><div class="stat-value">{len(content_list)}</div><div class="stat-label">Content Items</div></div>
      <div class="card stat"><div class="stat-value">{len(users)}</div><div class="stat-label">API Users</div></div>
      <div class="card stat"><div class="stat-value">{stats['total']}</div><div class="stat-label">Total Views</div></div>
      <div class="card stat"><div class="stat-value">{stats['today']}</div><div class="stat-label">Today's Views</div></div>
    </div>
    <div class="card">
      <h3 style="margin-bottom:12px">Recent Content</h3>
      <table><tr><th>Title</th><th>Slug</th><th>Sources</th><th>Created</th></tr>
      {''.join(f'<tr><td>{html_escape(c["title"])}</td><td class="mono">{c["slug"]}</td><td>{c.get("source_count",0)}</td><td>{c["created_at"]}</td></tr>' for c in content_list[:10])}
      </table>
    </div>"""
    return _admin_page(body, "Dashboard", "dashboard")


# ── Admin: Content Management ────────────────────────────────

@app.get("/admin/content")
async def admin_content(request: Request, msg: str = ""):
    if not _is_admin(request):
        return RedirectResponse("/admin/login")

    items = db.list_content()
    flash = f'<div class="flash flash-ok">{html_escape(msg)}</div>' if msg else ""
    base = BASE()

    rows = ""
    for c in items:
        watch_url = f"{base}/watch/{c['slug']}"
        embed_url = f"{base}/embed/{c['slug']}"
        rows += f"""<tr>
          <td><strong>{html_escape(c['title'])}</strong><br><span class="mono">{c['slug']}</span></td>
          <td>{c.get('source_count', 0)}</td>
          <td><a href="{watch_url}" target="_blank" class="mono" style="color:var(--acc2)">{watch_url}</a></td>
          <td>
            <a href="/admin/content/{c['id']}/sources" class="btn btn-primary btn-sm">Sources</a>
            <form method="POST" action="/admin/content/{c['id']}/delete" style="display:inline"
                  onsubmit="return confirm('Delete this?')">
              <button class="btn btn-danger btn-sm">Del</button>
            </form>
          </td>
        </tr>"""

    body = f"""
    <h1>📁 Content Management</h1>
    {flash}
    <div class="card">
      <h3 style="margin-bottom:12px">Create New Content</h3>
      <form method="POST" action="/admin/content/create" style="display:flex;gap:10px;align-items:end;flex-wrap:wrap">
        <div class="form-group" style="flex:1;min-width:200px">
          <label>Title</label><input type="text" name="title" required placeholder="e.g. Naruto Episode 1">
        </div>
        <div class="form-group" style="flex:1;min-width:150px">
          <label>Slug (auto if empty)</label><input type="text" name="slug" placeholder="naruto-episode-1">
        </div>
        <button class="btn btn-primary" type="submit">Create</button>
      </form>
    </div>
    <table><tr><th>Content</th><th>Sources</th><th>Watch URL</th><th>Actions</th></tr>
    {rows}
    </table>"""
    return _admin_page(body, "Content", "content")


@app.post("/admin/content/create")
async def admin_content_create(request: Request, title: str = Form(...), slug: str = Form("")):
    if not _is_admin(request):
        raise HTTPException(403)
    if not slug:
        slug = _slugify(title)
    try:
        db.create_content(title, slug, owner_id=ADMIN_TELEGRAM_ID)
        return RedirectResponse(f"/admin/content?msg=Created: {title}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/content?msg=Error: {e}", status_code=303)


@app.post("/admin/content/{content_id}/delete")
async def admin_content_delete(content_id: int, request: Request):
    if not _is_admin(request):
        raise HTTPException(403)
    db.delete_content(content_id)
    return RedirectResponse("/admin/content?msg=Deleted", status_code=303)


@app.get("/admin/content/{content_id}/sources")
async def admin_content_sources(content_id: int, request: Request, msg: str = ""):
    if not _is_admin(request):
        return RedirectResponse("/admin/login")
    content = db.get_content_by_id(content_id)
    if not content:
        raise HTTPException(404)
    sources = db.get_sources_by_content(content_id)
    flash = f'<div class="flash flash-ok">{html_escape(msg)}</div>' if msg else ""

    rows = ""
    for s in sources:
        sz = _format_size(s["file_size"]) if s["file_size"] else "—"
        rows += f"""<tr>
          <td>{s['language']}</td><td>{s['quality']}</td>
          <td>{s['width']}×{s['height']}</td><td>{sz}</td>
          <td class="mono" style="max-width:200px;overflow:hidden;text-overflow:ellipsis">{s['file_id'][:40]}...</td>
          <td><form method="POST" action="/admin/source/{s['id']}/delete" style="display:inline"
                onsubmit="return confirm('Delete?')">
            <input type="hidden" name="content_id" value="{content_id}">
            <button class="btn btn-danger btn-sm">Del</button></form></td>
        </tr>"""

    body = f"""
    <h1>📁 {html_escape(content['title'])} — Sources</h1>
    <p style="margin-bottom:16px;color:var(--txt2)">Slug: <span class="mono">{content['slug']}</span>
    &nbsp;|&nbsp; Watch: <a href="{BASE()}/watch/{content['slug']}" target="_blank" style="color:var(--acc2)">{BASE()}/watch/{content['slug']}</a></p>
    {flash}
    <div class="card">
      <h3 style="margin-bottom:12px">Add Source</h3>
      <form method="POST" action="/admin/source/add" style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
        <input type="hidden" name="content_id" value="{content_id}">
        <div class="form-group" style="flex:2;min-width:200px">
          <label>File ID</label><input type="text" name="file_id" required placeholder="Telegram file_id">
        </div>
        <div class="form-group" style="flex:1;min-width:100px">
          <label>Language</label><input type="text" name="language" value="Hindi" required>
        </div>
        <div class="form-group" style="flex:1;min-width:80px">
          <label>Quality</label>
          <select name="quality"><option>480p</option><option selected>720p</option><option>1080p</option><option>2160p</option></select>
        </div>
        <div class="form-group" style="min-width:80px">
          <label>Size (bytes)</label><input type="number" name="file_size" value="0">
        </div>
        <button class="btn btn-primary" type="submit">Add</button>
      </form>
    </div>
    <table><tr><th>Language</th><th>Quality</th><th>Resolution</th><th>Size</th><th>File ID</th><th></th></tr>
    {rows}
    </table>"""
    return _admin_page(body, f"Sources — {content['title']}", "content")


@app.post("/admin/source/add")
async def admin_source_add(request: Request, content_id: int = Form(...),
                           file_id: str = Form(...), language: str = Form("Hindi"),
                           quality: str = Form("720p"), file_size: int = Form(0)):
    if not _is_admin(request):
        raise HTTPException(403)
    try:
        db.add_source(content_id, file_id.strip(), language.strip(), quality.strip(),
                      file_size=file_size)
        return RedirectResponse(f"/admin/content/{content_id}/sources?msg=Source added",
                                status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/content/{content_id}/sources?msg=Error: {e}",
                                status_code=303)


@app.post("/admin/source/{source_id}/delete")
async def admin_source_delete(source_id: int, request: Request, content_id: int = Form(...)):
    if not _is_admin(request):
        raise HTTPException(403)
    db.delete_source(source_id)
    return RedirectResponse(f"/admin/content/{content_id}/sources?msg=Deleted", status_code=303)


# ── Admin: User Management ───────────────────────────────────

@app.get("/admin/users")
async def admin_users(request: Request, msg: str = ""):
    if not _is_admin(request):
        return RedirectResponse("/admin/login")
    users = db.list_api_users()
    flash = f'<div class="flash flash-ok">{html_escape(msg)}</div>' if msg else ""

    rows = ""
    for u in users:
        status = '<span class="badge badge-ok">Active</span>' if u["is_active"] \
            else '<span class="badge badge-err">Disabled</span>'
        rows += f"""<tr>
          <td>{html_escape(u['name'])}</td>
          <td class="mono">{u['api_key'][:16]}...</td>
          <td>{u['plan']}</td><td>{u['max_views']}/day</td><td>{u['max_embeds']}</td>
          <td>{status}</td>
          <td><form method="POST" action="/admin/users/{u['id']}/delete" style="display:inline"
                onsubmit="return confirm('Delete?')">
            <button class="btn btn-danger btn-sm">Del</button></form></td>
        </tr>"""

    body = f"""
    <h1>👤 API Users</h1>
    {flash}
    <div class="card">
      <h3 style="margin-bottom:12px">Create User</h3>
      <form method="POST" action="/admin/users/create" style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
        <div class="form-group" style="flex:1;min-width:150px">
          <label>Name</label><input type="text" name="name" required placeholder="User name">
        </div>
        <div class="form-group" style="min-width:100px">
          <label>Plan</label>
          <select name="plan"><option>free</option><option>pro</option><option>premium</option></select>
        </div>
        <div class="form-group" style="min-width:80px">
          <label>Max Views/Day</label><input type="number" name="max_views" value="1000">
        </div>
        <div class="form-group" style="min-width:80px">
          <label>Max Embeds</label><input type="number" name="max_embeds" value="10">
        </div>
        <button class="btn btn-primary" type="submit">Create</button>
      </form>
    </div>
    <table><tr><th>Name</th><th>API Key</th><th>Plan</th><th>Views Limit</th><th>Embeds</th><th>Status</th><th></th></tr>
    {rows}
    </table>"""
    return _admin_page(body, "Users", "users")


@app.post("/admin/users/create")
async def admin_users_create(request: Request, name: str = Form(...),
                             plan: str = Form("free"), max_views: int = Form(1000),
                             max_embeds: int = Form(10)):
    if not _is_admin(request):
        raise HTTPException(403)
    user = db.create_api_user(name, plan, max_views, max_embeds)
    return RedirectResponse(f"/admin/users?msg=Created {name} — Key: {user['api_key'][:20]}...",
                            status_code=303)


@app.post("/admin/users/{user_id}/delete")
async def admin_users_delete(user_id: int, request: Request):
    if not _is_admin(request):
        raise HTTPException(403)
    db.delete_api_user(user_id)
    return RedirectResponse("/admin/users?msg=Deleted", status_code=303)


# ── Admin: Ads Management ────────────────────────────────────

@app.get("/admin/ads")
async def admin_ads(request: Request, msg: str = ""):
    if not _is_admin(request):
        return RedirectResponse("/admin/login")
    ads = db.list_ads()
    flash = f'<div class="flash flash-ok">{html_escape(msg)}</div>' if msg else ""

    rows = ""
    for a in ads:
        status = '<span class="badge badge-ok">On</span>' if a["is_active"] \
            else '<span class="badge badge-err">Off</span>'
        rows += f"""<tr>
          <td>{html_escape(a['name'])}</td><td>{a['ad_type']}</td><td>{a['position']}</td>
          <td>{a['duration']}s</td><td>{status}</td>
          <td>
            <form method="POST" action="/admin/ads/{a['id']}/toggle" style="display:inline">
              <button class="btn btn-primary btn-sm">{'Disable' if a['is_active'] else 'Enable'}</button></form>
            <form method="POST" action="/admin/ads/{a['id']}/delete" style="display:inline"
                  onsubmit="return confirm('Delete?')">
              <button class="btn btn-danger btn-sm">Del</button></form>
          </td>
        </tr>"""

    body = f"""
    <h1>📢 Ads Management</h1>
    {flash}
    <div class="card">
      <h3 style="margin-bottom:12px">Create Ad</h3>
      <form method="POST" action="/admin/ads/create">
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px">
          <div class="form-group" style="flex:1;min-width:150px">
            <label>Name</label><input type="text" name="name" required placeholder="Pre-roll Ad">
          </div>
          <div class="form-group" style="min-width:100px">
            <label>Type</label><select name="ad_type"><option value="vast">VAST</option><option value="custom">Custom HTML</option></select>
          </div>
          <div class="form-group" style="min-width:80px">
            <label>Position</label><select name="position"><option value="pre">Pre-roll</option><option value="mid">Mid-roll</option><option value="post">Post-roll</option></select>
          </div>
          <div class="form-group" style="min-width:80px">
            <label>Duration (sec)</label><input type="number" name="duration" value="5">
          </div>
        </div>
        <div class="form-group">
          <label>VAST URL (if type=vast)</label><input type="text" name="ad_url" placeholder="https://vast-server.com/tag.xml">
        </div>
        <div class="form-group">
          <label>Custom HTML (if type=custom)</label><textarea name="ad_html" rows="3" placeholder="<div>Your ad here</div>"></textarea>
        </div>
        <button class="btn btn-primary" type="submit">Create Ad</button>
      </form>
    </div>
    <table><tr><th>Name</th><th>Type</th><th>Position</th><th>Duration</th><th>Status</th><th>Actions</th></tr>
    {rows}
    </table>"""
    return _admin_page(body, "Ads", "ads")


@app.post("/admin/ads/create")
async def admin_ads_create(request: Request, name: str = Form(...),
                           ad_type: str = Form("custom"), ad_url: str = Form(""),
                           ad_html: str = Form(""), position: str = Form("pre"),
                           duration: int = Form(5)):
    if not _is_admin(request):
        raise HTTPException(403)
    db.create_ad(name, ad_type, ad_url, ad_html, position, duration)
    return RedirectResponse("/admin/ads?msg=Ad created", status_code=303)


@app.post("/admin/ads/{ad_id}/toggle")
async def admin_ads_toggle(ad_id: int, request: Request):
    if not _is_admin(request):
        raise HTTPException(403)
    ads = db.list_ads()
    ad = next((a for a in ads if a["id"] == ad_id), None)
    if ad:
        db.toggle_ad(ad_id, not ad["is_active"])
    return RedirectResponse("/admin/ads?msg=Toggled", status_code=303)


@app.post("/admin/ads/{ad_id}/delete")
async def admin_ads_delete(ad_id: int, request: Request):
    if not _is_admin(request):
        raise HTTPException(403)
    db.delete_ad(ad_id)
    return RedirectResponse("/admin/ads?msg=Deleted", status_code=303)


# ── Admin: Logs ──────────────────────────────────────────────

@app.get("/admin/logs")
async def admin_logs(request: Request):
    if not _is_admin(request):
        return RedirectResponse("/admin/login")
    logs = db.get_recent_logs(50)
    stats = db.get_view_stats()

    rows = ""
    for l in logs:
        rows += f"""<tr>
          <td>{l.get('content_title') or '—'}</td>
          <td class="mono">{l.get('ip_hash','')[:12]}</td>
          <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">{html_escape(l.get('user_agent','')[:60])}</td>
          <td>{l['viewed_at']}</td>
        </tr>"""

    body = f"""
    <h1>📋 View Logs</h1>
    <div class="grid" style="margin-bottom:20px">
      <div class="card stat"><div class="stat-value">{stats['total']}</div><div class="stat-label">Total Views</div></div>
      <div class="card stat"><div class="stat-value">{stats['today']}</div><div class="stat-label">Today</div></div>
      <div class="card stat"><div class="stat-value">{stats['unique_ips']}</div><div class="stat-label">Unique IPs</div></div>
    </div>
    <table><tr><th>Content</th><th>IP Hash</th><th>User Agent</th><th>Time</th></tr>
    {rows}
    </table>"""
    return _admin_page(body, "Logs", "logs")


# ══════════════════════════════════════════════════════════════
# LEGACY: /player/{file_id} and /embed/{file_id} (backward compat)
# ══════════════════════════════════════════════════════════════

@app.get("/player/{file_id}")
async def legacy_player(file_id: str, pt: str = ""):
    if not pt or not _validate_page_token(file_id, pt):
        raise HTTPException(403, "Invalid page token")
    vid = db.get_video_by_file_id(file_id)
    size = vid.get("file_size", 0) if vid else 0
    title = (vid.get("file_name") or vid.get("caption") or "Video") if vid else "Video"
    stream = _make_stream_url(file_id, size)
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
    <title>{html_escape(title)}</title>
    <style>*{{margin:0;padding:0;box-sizing:border-box}}body{{background:#0a0a12;display:flex;align-items:center;justify-content:center;min-height:100vh}}
    video{{max-width:960px;width:100%;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.5)}}</style></head><body>
    <video controls autoplay playsinline preload="auto" controlsList="nodownload"><source src="{stream}" type="video/mp4"></video>
    <script>document.querySelector('video').addEventListener('contextmenu',e=>e.preventDefault())</script></body></html>"""
    return HTMLResponse(content=html)


# ══════════════════════════════════════════════════════════════
# BOT HANDLERS
# ══════════════════════════════════════════════════════════════

# Temporary storage for pending video additions (user forwards video, then tags it)
_pending_videos: dict = {}  # chat_id -> {file_id, file_unique_id, file_size, ...}

def _register_bot_handlers():

    def is_admin_user(user_id: int) -> bool:
        return user_id == ADMIN_TELEGRAM_ID if ADMIN_TELEGRAM_ID else True

    # ── /start ───────────────────────────────────────────────
    @tg_client.on_message(filters.private & filters.command("start"))
    async def cmd_start(_c: Client, m: Message):
        await m.reply_text(
            "🎬 **TG Stream Server Bot**\n\n"
            "**Commands:**\n"
            "/new `Title` — Create content group\n"
            "/add `slug` `Language` `Quality` — Add video to content (send video first)\n"
            "/links `slug` — Get all stream links\n"
            "/list — List all content\n"
            "/delete `slug` — Delete content\n"
            "/status — Server status\n\n"
            "**Quick Flow:**\n"
            "1️⃣ `/new Naruto Episode 1`\n"
            "2️⃣ Send/forward a video\n"
            "3️⃣ `/add naruto-episode-1 Hindi 720p`\n"
            "4️⃣ `/links naruto-episode-1`\n\n"
            "⚡ MTProto streaming — NO 20MB limit!",
        )

    # ── /new — Create content group ──────────────────────────
    @tg_client.on_message(filters.private & filters.command("new"))
    async def cmd_new(_c: Client, m: Message):
        if not is_admin_user(m.from_user.id):
            return await m.reply_text("⛔ Admin only.")
        parts = m.text.split(maxsplit=1)
        if len(parts) < 2:
            return await m.reply_text("Usage: /new `Title Here`\nExample: `/new Naruto Episode 1`")
        title = parts[1].strip()
        slug = _slugify(title)
        try:
            cid = db.create_content(title, slug, owner_id=m.from_user.id)
            await m.reply_text(
                f"✅ **Content Created!**\n\n"
                f"📝 **Title:** {title}\n"
                f"🔗 **Slug:** `{slug}`\n"
                f"🆔 **ID:** {cid}\n\n"
                f"**Next:** Send a video, then:\n"
                f"`/add {slug} Hindi 720p`",
            )
        except Exception as e:
            await m.reply_text(f"❌ Error: {e}")

    # ── Receive video (stores temporarily) ───────────────────
    @tg_client.on_message(filters.private & (filters.video | filters.document))
    async def on_video(_c: Client, m: Message):
        media = m.video or (m.document if m.document and (m.document.mime_type or "").startswith("video/") else None)
        if not media:
            return

        _pending_videos[m.from_user.id] = {
            "file_id": media.file_id,
            "file_unique_id": media.file_unique_id,
            "file_size": media.file_size or 0,
            "duration": getattr(media, "duration", 0) or 0,
            "width": getattr(media, "width", 0) or 0,
            "height": getattr(media, "height", 0) or 0,
            "file_name": getattr(media, "file_name", "") or "",
        }

        quality = _detect_quality(
            getattr(media, "width", 0) or 0,
            getattr(media, "height", 0) or 0,
        )
        size_str = _format_size(media.file_size or 0)

        # Also save to legacy tg_videos table
        try:
            db.save_video({
                "file_id": media.file_id, "file_unique_id": media.file_unique_id,
                "file_size": media.file_size or 0, "duration": getattr(media, "duration", 0) or 0,
                "width": getattr(media, "width", 0) or 0, "height": getattr(media, "height", 0) or 0,
                "file_name": getattr(media, "file_name", "") or "",
                "mime_type": media.mime_type or "video/mp4",
                "caption": m.caption or "", "message_id": m.id,
                "channel_id": m.chat.id, "quality": quality,
            })
        except: pass

        await m.reply_text(
            f"📹 **Video Received!**\n\n"
            f"📝 **File:** {getattr(media, 'file_name', '') or 'N/A'}\n"
            f"📐 **Resolution:** {getattr(media, 'width', 0)}×{getattr(media, 'height', 0)} ({quality})\n"
            f"💾 **Size:** {size_str}\n\n"
            f"**Now add it to a content group:**\n"
            f"`/add <slug> <Language> <Quality>`\n\n"
            f"Example:\n`/add naruto-ep1 Hindi {quality}`",
            quote=True,
        )

    # ── /add — Add pending video to content group ────────────
    @tg_client.on_message(filters.private & filters.command("add"))
    async def cmd_add(_c: Client, m: Message):
        if not is_admin_user(m.from_user.id):
            return await m.reply_text("⛔ Admin only.")

        parts = m.text.split()
        if len(parts) < 4:
            return await m.reply_text(
                "Usage: `/add <slug> <Language> <Quality>`\n"
                "Example: `/add naruto-ep1 Hindi 720p`\n\n"
                "⚠️ Send the video FIRST, then use /add")

        slug = parts[1]
        language = parts[2]
        quality = parts[3]

        pending = _pending_videos.get(m.from_user.id)
        if not pending:
            return await m.reply_text("⚠️ No video pending. Send a video first, then /add.")

        content = db.get_content_by_slug(slug)
        if not content:
            return await m.reply_text(f"❌ Content `{slug}` not found. Create it first with /new.")

        try:
            db.add_source(
                content_id=content["id"],
                file_id=pending["file_id"],
                language=language,
                quality=quality,
                file_unique_id=pending["file_unique_id"],
                file_size=pending["file_size"],
                duration=pending["duration"],
                width=pending["width"],
                height=pending["height"],
            )
            del _pending_videos[m.from_user.id]

            sources = db.get_sources_by_content(content["id"])
            source_summary = ", ".join(f"{s['language']} {s['quality']}" for s in sources)

            await m.reply_text(
                f"✅ **Source Added!**\n\n"
                f"📁 **Content:** {content['title']}\n"
                f"🔤 **Language:** {language}\n"
                f"📺 **Quality:** {quality}\n\n"
                f"**All sources:** {source_summary}\n\n"
                f"Use `/links {slug}` to get player links.",
            )
        except Exception as e:
            await m.reply_text(f"❌ Error: {e}")

    # ── /links — Generate all links ──────────────────────────
    @tg_client.on_message(filters.private & filters.command("links"))
    async def cmd_links(_c: Client, m: Message):
        parts = m.text.split()
        if len(parts) < 2:
            return await m.reply_text("Usage: `/links <slug>`")
        slug = parts[1]
        content = db.get_content_by_slug(slug)
        if not content:
            return await m.reply_text(f"❌ Content `{slug}` not found.")

        sources = db.get_sources_by_content(content["id"])
        base = BASE()
        watch = f"{base}/watch/{slug}"
        embed = f"{base}/embed/{slug}"

        langs = {}
        for s in sources:
            langs.setdefault(s["language"], []).append(s["quality"])
        src_summary = "\n".join(f"  • {lang}: {', '.join(qs)}" for lang, qs in langs.items())

        await m.reply_text(
            f"🔗 **Links for \"{content['title']}\"**\n\n"
            f"▶️ **Player Page:**\n{watch}\n\n"
            f"🖼 **iFrame Embed:**\n`<iframe src=\"{embed}\" width=\"720\" height=\"405\" "
            f"allowfullscreen></iframe>`\n\n"
            f"📺 **Direct Embed URL:**\n{embed}\n\n"
            f"━━━━━ Sources ━━━━━\n{src_summary}\n\n"
            f"⚡ MTProto streaming — NO 20MB limit",
            disable_web_page_preview=True,
        )

    # ── /list — List all content ─────────────────────────────
    @tg_client.on_message(filters.private & filters.command("list"))
    async def cmd_list(_c: Client, m: Message):
        items = db.list_content(20)
        if not items:
            return await m.reply_text("📭 No content yet. Use /new to create.")
        lines = []
        for c in items:
            lines.append(f"• **{c['title']}** (`{c['slug']}`) — {c.get('source_count', 0)} sources")
        await m.reply_text("📁 **Content List:**\n\n" + "\n".join(lines))

    # ── /delete — Delete content ─────────────────────────────
    @tg_client.on_message(filters.private & filters.command("delete"))
    async def cmd_delete(_c: Client, m: Message):
        if not is_admin_user(m.from_user.id):
            return await m.reply_text("⛔ Admin only.")
        parts = m.text.split()
        if len(parts) < 2:
            return await m.reply_text("Usage: `/delete <slug>`")
        slug = parts[1]
        content = db.get_content_by_slug(slug)
        if not content:
            return await m.reply_text(f"❌ `{slug}` not found.")
        db.delete_content(content["id"])
        await m.reply_text(f"🗑 Deleted: **{content['title']}** and all its sources.")

    # ── /status ──────────────────────────────────────────────
    @tg_client.on_message(filters.private & filters.command("status"))
    async def cmd_status(_c: Client, m: Message):
        connected = tg_client.is_connected
        items = db.list_content()
        stats = db.get_view_stats()
        await m.reply_text(
            f"📊 **Server Status**\n\n"
            f"🔌 MTProto: {'✅ Connected' if connected else '❌ Disconnected'}\n"
            f"🔓 20MB Limit: **BYPASSED**\n"
            f"📁 Content: {len(items)} groups\n"
            f"👁 Views: {stats['total']} total, {stats['today']} today\n"
            f"🌐 Server: {BASE()}\n"
            f"📦 Chunk: {CHUNK_SIZE//1024}KB",
        )

    # ── Channel video auto-save ──────────────────────────────
    @tg_client.on_message(filters.channel & (filters.video | filters.document))
    async def on_channel_video(_c: Client, m: Message):
        media = m.video or (m.document if m.document and (m.document.mime_type or "").startswith("video/") else None)
        if not media:
            return
        try:
            db.save_video({
                "file_id": media.file_id, "file_unique_id": media.file_unique_id,
                "file_size": media.file_size or 0, "duration": getattr(media, "duration", 0) or 0,
                "width": getattr(media, "width", 0) or 0, "height": getattr(media, "height", 0) or 0,
                "file_name": getattr(media, "file_name", "") or "",
                "mime_type": media.mime_type or "video/mp4", "caption": m.caption or "",
                "message_id": m.id, "channel_id": m.chat.id,
                "quality": _detect_quality(getattr(media, "width", 0) or 0, getattr(media, "height", 0) or 0),
            })
            logger.info(f"📹 Channel video saved: {getattr(media, 'file_name', '') or media.file_unique_id}")
        except Exception as e:
            logger.error(f"Channel video save error: {e}")

    logger.info("Bot handlers registered.")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=STREAM_HOST, port=STREAM_PORT,
                log_level="info", access_log=True)

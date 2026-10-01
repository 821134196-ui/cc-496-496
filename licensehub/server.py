"""HTTP 服务：标准库 http.server，会话 Cookie，multipart 上传，权限隔离。"""
from __future__ import annotations

import datetime as dt
import io
import re
import secrets
import urllib.parse
import zlib
from http.cookies import SimpleCookie
from pathlib import Path

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import scans, views
from .notifier import MockSmsAdapter
from .reminders import ReminderEngine
from .service import LicenseService, PermissionDenied, ServiceError
from .db import DB
from .seed import seed


# ---------------- multipart/form-data 极简解析 ----------------
def parse_multipart(body: bytes, boundary: bytes) -> dict:
    """返回 {field: str | ('__file__', filename, bytes)}。"""
    delim = b"--" + boundary
    out: dict = {}
    for part in body.split(delim):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        head, content = part.split(b"\r\n\r\n", 1)
        headers = head.decode("utf-8", "replace")
        m_name = re.search(r'name="([^"]*)"', headers)
        m_file = re.search(r'filename="([^"]*)"', headers)
        if not m_name:
            continue
        name = m_name.group(1)
        if content.endswith(b"\r\n"):
            content = content[:-2]
        if m_file:
            fname = m_file.group(1)
            if fname:
                out[name] = ("__file__", fname, content)
        else:
            out[name] = content.decode("utf-8", "replace")
    return out


def form_urlencoded(body: bytes) -> dict:
    return {k: v[0] for k, v in urllib.parse.parse_qs(body.decode("utf-8", "replace")).items()}


class App:
    def __init__(self, db: DB, data_dir: str | Path, svc: LicenseService,
                 engine: ReminderEngine | None = None):
        self.db = db
        self.data_dir = Path(data_dir)
        self.scan_dir = self.data_dir / "scans"
        self.scan_dir.mkdir(parents=True, exist_ok=True)
        self.svc = svc
        self.engine = engine
        self.sessions: dict[str, str] = {}   # sid -> username
        self.flash: dict[str, list] = {}


def make_app(data_dir: str | Path, start_engine: bool = True) -> App:
    data_dir = Path(data_dir)
    db_path = data_dir / "licensehub.db"
    fresh = not db_path.exists()
    db = DB(str(db_path))
    adapter = MockSmsAdapter()
    svc = LicenseService(db, notifier=adapter)
    engine = ReminderEngine(svc, interval=60)
    app = App(db, data_dir, svc, engine)
    if fresh:
        # 用真实时钟播种，立即生成一轮提醒
        seeded = seed(db, data_dir)
        # seed 内部用的是独立 svc 实例（同一数据库文件），复用其适配器记录无意义；
        # 正式引擎再 tick 一次（已通知档位会去重）
    if start_engine:
        engine.start()
    return app


class Handler(BaseHTTPRequestHandler):
    app: App = None  # 由工厂注入

    server_version = "LicenseHub/1.0"

    # ---------- 工具 ----------
    def log_message(self, fmt, *args):
        pass

    @property
    def svc(self) -> LicenseService:
        return self.server.app.svc

    @property
    def app_(self) -> App:
        return self.server.app

    def _sid(self) -> str | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" in cookie:
            return cookie["sid"].value
        return None

    def current_user(self):
        sid = self._sid()
        if not sid or sid not in self.app_.sessions:
            return None
        return self.svc.get_user(self.app_.sessions[sid])

    def _flash_pop(self, sid):
        return self.app_.flash.pop(sid, None)

    def _flash_push(self, sid, kind, msg):
        self.app_.flash.setdefault(sid, []).append((kind, msg))

    def send_html(self, html_str: str, status: int = 200, extra_headers=None):
        data = html_str.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, path, sid=None, kind=None, msg=None):
        if sid and kind and msg:
            self._flash_push(sid, kind, msg)
        self.send_response(302)
        self.send_header("Location", path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            m = re.search(r"boundary=([^;]+)", ctype)
            if not m:
                return {}
            return parse_multipart(body, m.group(1).strip('"').encode())
        return form_urlencoded(body)

    def require_login(self):
        user = self.current_user()
        if not user:
            self.redirect("/login")
            return None
        return user

    def error_page(self, user, err, status=400):
        body = f'<div class="card"><p>{views.esc(err)}</p>' \
               f'<p><a href="javascript:history.back()">返回上一页</a> · <a href="/">回到看板</a></p></div>'
        self.send_html(views.page(user, "操作未完成", body, flash=("err", err)), status)

    # ---------- 路由 ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        try:
            self.route_get(path, qs)
        except PermissionDenied as e:
            user = self.current_user()
            self.error_page(user, f"无权访问：{e}", status=403)
        except ServiceError as e:
            user = self.current_user()
            self.error_page(user, str(e), status=400)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            self.route_post(path)
        except PermissionDenied as e:
            user = self.current_user()
            self.error_page(user, f"无权操作：{e}", status=403)
        except ServiceError as e:
            user = self.current_user()
            self.error_page(user, str(e), status=400)

    def route_get(self, path, qs):
        if path == "/login":
            users = self.db_query_users()
            self.send_html(views.login_page(users, err=qs.get("err")))
            return
        user = self.require_login()
        if not user:
            return
        sid = self._sid()
        flash = self._flash_pop(sid)
        svc = self.svc

        if path == "/logout":
            self.app_.sessions.pop(sid, None)
            self.redirect("/login")
            return
        if path == "/":
            dash = svc.dashboard(user)
            receipts = svc.db.query(
                "SELECT * FROM notifications ORDER BY id DESC LIMIT 50"
                if user["role"] == "hq" else
                "SELECT n.* FROM notifications n JOIN tasks tk ON tk.id=n.task_id"
                " JOIN certs c ON c.id=tk.cert_id WHERE c.store_id=?"
                " ORDER BY n.id DESC LIMIT 50",
                () if user["role"] == "hq" else (user["store_id"],))
            self.send_html(views.dashboard(svc, user, dash, receipts))
        elif path == "/matrix":
            self.send_html(views.matrix_view(user, svc.matrix(user)))
        elif path == "/tasks":
            self.send_html(views.tasks_view(svc, user, svc.list_tasks(user)))
        elif path == "/corrections":
            self.send_html(views.corrections_view(user, svc.list_corrections(user)))
        elif path == "/audits":
            if user["role"] != "hq":
                raise PermissionDenied("审核记录仅总部可查")
            self.send_html(views.audits_view(user, svc.list_audit_logs(user)))
        elif path == "/config":
            if user["role"] != "hq":
                raise PermissionDenied("提醒配置仅总部法务可操作")
            logs = svc.db.query(
                "SELECT l.*, t.name FROM remind_config_log l JOIN cert_types t ON t.id=l.type_id"
                " ORDER BY l.id DESC")
            self.send_html(views.config_view(user, svc.list_cert_types(), logs, flash=flash))
        else:
            m = re.fullmatch(r"/store/(\d+)", path)
            if m:
                return self._store_page(user, int(m.group(1)), flash)
            m = re.fullmatch(r"/cert/(\d+)", path)
            if m:
                d = svc.cert_detail(user, int(m.group(1)))
                return self.send_html(views.cert_detail(svc, user, d, flash=flash))
            m = re.fullmatch(r"/scan/(\d+)", path)
            if m:
                return self._serve_scan(user, int(m.group(1)))
            self.not_found(user)

    def db_query_users(self):
        return self.svc.db.query(
            "SELECT * FROM users ORDER BY role DESC, id")

    def _store_page(self, user, store_id, flash):
        store = self.svc.get_store(user, store_id)
        rows = self.svc.db.query(
            "SELECT c.id, t.name AS type_name, v.status, v.valid_to, v.responsible"
            " FROM certs c JOIN cert_types t ON t.id=c.type_id"
            " LEFT JOIN cert_versions v ON v.id=("
            "  SELECT id FROM cert_versions WHERE cert_id=c.id"
            "  AND status IN ('approved','expired') ORDER BY id DESC LIMIT 1)"
            " WHERE c.store_id=? ORDER BY t.id", (store_id,))
        self.send_html(views.store_detail(self.svc, user, store, rows, flash=flash))

    def _serve_scan(self, user, version_id):
        v = self.svc.get_version(user, version_id)
        if not v["scan_path"]:
            self.not_found(user)
            return
        target = (self.app_.scan_dir / v["scan_path"]).resolve()
        root = self.app_.scan_dir.resolve()
        if root not in target.parents and target != root:
            raise PermissionDenied("非法扫描件路径")
        if not target.exists():
            self.not_found(user)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=60")
        self.end_headers()
        self.wfile.write(data)

    def not_found(self, user):
        body = '<div class="card"><p>页面不存在。</p><p><a href="/">回到看板</a></p></div>'
        self.send_html(views.page(user, "404", body), 404)

    # ---------- POST 动作 ----------
    def route_post(self, path):
        if path == "/login":
            return self._do_login()
        user = self.require_login()
        if not user:
            return
        sid = self._sid()
        form = self.read_body()
        svc = self.svc

        if path == "/logout":
            self.app_.sessions.pop(sid, None)
            return self.redirect("/login")

        m = re.fullmatch(r"/config/type/(\d+)", path)
        if m:
            svc.update_remind_days(user, int(m.group(1)), form.get("remind_days", ""))
            return self.redirect("/config", sid, "ok", "提醒提前量已保存并留痕")

        m = re.fullmatch(r"/store/(\d+)/change", path)
        if m:
            note = svc.change_store_info(user, int(m.group(1)),
                                         form.get("address"), form.get("business_scope"))
            return self.redirect(f"/store/{m.group(1)}", sid, "ok",
                                 f"已触发证照重新核查：{note}")

        m = re.fullmatch(r"/cert/(\d+)/submit", path)
        if m:
            return self._do_submit(user, int(m.group(1)), form, sid, resubmit=False)

        m = re.fullmatch(r"/cert/(\d+)/lost", path)
        if m:
            svc.report_origin_lost(user, int(m.group(1)), form.get("note", ""))
            return self.redirect(f"/cert/{m.group(1)}", sid, "ok", "已登记原件遗失并生成补正任务")

        m = re.fullmatch(r"/version/(\d+)/review", path)
        if m:
            decision = form.get("decision", "")
            result = svc.review(user, int(m.group(1)), decision, form.get("note", ""))
            cn = {"approved": "已审核通过，新版本标为当前有效版本",
                  "returned": "已退回门店补正", "rejected": "已驳回"}[result]
            v = svc.get_version(user, int(m.group(1)))
            return self.redirect(f"/cert/{v['cert_id']}", sid,
                                 "ok" if result == "approved" else "err", cn)

        m = re.fullmatch(r"/version/(\d+)/resubmit", path)
        if m:
            return self._do_resubmit(user, int(m.group(1)), form, sid)

        m = re.fullmatch(r"/version/(\d+)/acceptance", path)
        if m:
            rid = svc.record_acceptance(user, int(m.group(1)),
                                        form.get("accept_no", ""),
                                        form.get("authority", ""),
                                        form.get("note", ""))
            v = svc.get_version(user, int(m.group(1)))
            return self.redirect(f"/cert/{v['cert_id']}", sid, "ok",
                                 f"受理号已登记（#{rid}）")

        m = re.fullmatch(r"/renewal/(\d+)/reject", path)
        if m:
            row = svc.db.query_one(
                "SELECT r.cert_id FROM renewals r WHERE r.id=?", (int(m.group(1)),))
            svc.reject_acceptance(user, int(m.group(1)), form.get("reason", ""))
            return self.redirect(f"/cert/{row['cert_id']}", sid, "err",
                                 "已登记受理驳回并生成材料补正")

        m = re.fullmatch(r"/correction/(\d+)/resolve", path)
        if m:
            cid = int(m.group(1))
            row = svc.db.query_one(
                "SELECT v.cert_id FROM corrections k JOIN cert_versions v ON v.id=k.version_id"
                " WHERE k.id=?", (cid,))
            svc.resolve_correction(user, cid, form.get("resolution", ""))
            return self.redirect(f"/cert/{row['cert_id']}", sid, "ok", "补正已闭环")

        raise ServiceError("未知操作")

    # ---------- 登录 ----------
    def _do_login(self):
        form = self.read_body()
        username, pin = form.get("username", ""), form.get("pin", "")
        u = self.svc.get_user(username)
        if not u or u["pin"] != pin:
            return self.redirect("/login?err=1")
        sid = secrets.token_hex(16)
        self.app_.sessions[sid] = username
        self.send_response(302)
        self.send_header("Set-Cookie", f"sid={sid}; HttpOnly; Path=/")
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- 扫描件上传 ----------
    def _consume_scan(self, form, cert, fallback_title):
        """返回 (rel, w, h, clear)。无文件时生成样例件。"""
        upload = form.get("scan")
        force_blur = form.get("blurry") == "1"
        if isinstance(upload, tuple) and upload[2]:
            _, fname, data = upload
            rel, w, h = scans.save_upload(self.app_.scan_dir, data, fname)
            # 尺寸过小视为模糊；演示勾选项可强制标记
            clear = (w >= 600 and h >= 600) and not force_blur
            return rel, w, h, clear
        # 生成系统样例件
        stamp = f"{dt.datetime.now():%H%M%S%f}"[:10]
        rel = f"sample_{cert.id}_{stamp}.png"
        w, h = scans.generate_sample(
            self.app_.scan_dir / rel, fallback_title,
            [f"NO {form.get('cert_number', 'SAMPLE')}",
             f"VALID {form.get('valid_from', '--------')} TO {form.get('valid_to', '--------')}"],
            landscape=cert["type_code"] != "business_license",
            clear=not force_blur)
        return rel, w, h, not force_blur

    def _do_submit(self, user, cert_id, form, sid, resubmit):
        cert = self.svc._get_cert_checked(user, cert_id)
        rel, w, h, clear = self._consume_scan(
            form, cert, cert["type_code"].upper().replace("_", " "))
        vid = self.svc.submit_version(
            user, cert_id,
            cert_number=form.get("cert_number", "").strip(),
            valid_from=form.get("valid_from", ""),
            valid_to=form.get("valid_to", ""),
            responsible=form.get("responsible", "").strip(),
            scan_rel=rel, scan_width=w, scan_height=h, scan_clear=clear,
            note=form.get("note", ""))
        msg = "续证材料已提交，等待总部审核"
        if not clear:
            msg += "（系统已标记扫描件模糊，将被退回补正）"
        self.redirect(f"/cert/{cert_id}", sid, "ok", msg)

    def _do_resubmit(self, user, version_id, form, sid):
        v = self.svc.get_version(user, version_id)
        cert = self.svc._get_cert_checked(user, v["cert_id"])
        form = dict(form)
        form.setdefault("cert_number", v["cert_number"])
        form.setdefault("valid_from", v["valid_from"])
        form.setdefault("valid_to", v["valid_to"])
        rel, w, h, clear = self._consume_scan(
            form, cert, cert["type_code"].upper().replace("_", " "))
        self.svc.resubmit_scan(user, version_id, scan_rel=rel, scan_width=w,
                               scan_height=h, scan_clear=clear,
                               note=form.get("note", ""))
        self.redirect(f"/cert/{v['cert_id']}", sid, "ok",
                      "扫描件已重新上传并送审" if clear
                      else "重新上传的扫描件仍被判定模糊，请上传高清原件")


def build_server(host: str, port: int, data_dir: str | Path,
                 start_engine: bool = True) -> ThreadingHTTPServer:
    app = make_app(data_dir, start_engine=start_engine)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.app = app  # type: ignore[attr-defined]
    return httpd

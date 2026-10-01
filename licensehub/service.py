"""核心业务服务：权限隔离、版本链、审核链、续证/补正、到期拦截与衔接核查。

所有日期为 YYYY-MM-DD 字符串；clock 可注入（测试用固定日期）。
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, Optional

from .db import DB

VERSION_PENDING = "pending_review"
VERSION_RETURNED = "returned"
VERSION_APPROVED = "approved"
VERSION_REJECTED = "rejected"
VERSION_EXPIRED = "expired"
VERSION_SUPERSEDED = "superseded"

ROLE_HQ = "hq"
ROLE_STORE = "store"


class ServiceError(Exception):
    """业务规则不满足（如过期拦截、重复审批）。"""


class PermissionDenied(ServiceError):
    """越权访问：门店只能管理本店资料，审核仅限总部。"""


def parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def today_str(d: dt.date) -> str:
    return d.isoformat()


class LicenseService:
    def __init__(self, db: DB, clock: Optional[Callable[[], dt.date]] = None,
                 notifier=None):
        self.db = db
        self.clock = clock or dt.date.today
        self.notifier = notifier

    # ---------- 基础工具 ----------
    def today(self) -> dt.date:
        return self.clock()

    @staticmethod
    def now() -> str:
        return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def get_user(self, username: str):
        return self.db.query_one("SELECT * FROM users WHERE username=?", (username,))

    def require_user(self, user):
        if not user:
            raise PermissionDenied("未登录")
        return user

    def _require_hq(self, user):
        self.require_user(user)
        if user["role"] != ROLE_HQ:
            raise PermissionDenied("仅总部法务可执行该操作")

    def _require_store(self, user):
        self.require_user(user)
        if user["role"] != ROLE_STORE or not user["store_id"]:
            raise PermissionDenied("仅门店账号可执行该操作")

    def _check_own(self, user, store_id):
        """门店只能访问本店；总部放行。"""
        self.require_user(user)
        if user["role"] == ROLE_HQ:
            return
        if user["store_id"] != store_id:
            raise PermissionDenied("门店只能管理本店证照资料")

    def visible_store_ids(self, user):
        """None 表示总部可见全部门店。"""
        self.require_user(user)
        return None if user["role"] == ROLE_HQ else [user["store_id"]]

    # ---------- 门店 / 证照类型 ----------
    def list_stores(self, user):
        ids = self.visible_store_ids(user)
        if ids is None:
            return self.db.query("SELECT * FROM stores ORDER BY id")
        return self.db.query("SELECT * FROM stores WHERE id=? ORDER BY id", (ids[0],))

    def get_store(self, user, store_id):
        self._check_own(user, store_id)
        row = self.db.query_one("SELECT * FROM stores WHERE id=?", (store_id,))
        if not row:
            raise ServiceError("门店不存在")
        return row

    def list_cert_types(self):
        return self.db.query("SELECT * FROM cert_types ORDER BY id")

    def update_remind_days(self, user, type_id, days):
        """总部法务配置某类证照的提醒提前量（多档，正整数、升序去重）。"""
        self._require_hq(user)
        clean = self._normalize_days(days)
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM cert_types WHERE id=?", (type_id,)).fetchone()
            if not row:
                raise ServiceError("证照类型不存在")
            conn.execute(
                "INSERT INTO remind_config_log(type_id, old_days, new_days, actor_id, created_at)"
                " VALUES(?,?,?,?,?)",
                (type_id, row["remind_days"], clean, user["id"], self.now()))
            conn.execute("UPDATE cert_types SET remind_days=? WHERE id=?", (clean, type_id))
        return clean

    @staticmethod
    def _normalize_days(days) -> str:
        if isinstance(days, str):
            parts = [p.strip() for p in days.replace("，", ",").split(",") if p.strip()]
        else:
            parts = [str(int(d)) for d in days]
        nums = sorted({int(p) for p in parts})
        if not nums or any(n <= 0 for n in nums):
            raise ServiceError("提醒提前量须为正整数，且至少一档")
        return ",".join(str(n) for n in nums)

    # ---------- 门店资料变更：触发证照重新核查 ----------
    def change_store_info(self, user, store_id, address=None, business_scope=None):
        """迁址或经营范围变化 → address_seq + 1，全部当前证挂重新核查任务，不能沿用旧证。"""
        self._check_own(user, store_id)
        with self.db.transaction() as conn:
            store = conn.execute("SELECT * FROM stores WHERE id=?", (store_id,)).fetchone()
            if not store:
                raise ServiceError("门店不存在")
            new_addr = (address or store["address"]).strip()
            new_scope = (business_scope if business_scope is not None
                         else store["business_scope"]).strip()
            addr_changed = new_addr != store["address"]
            scope_changed = new_scope != store["business_scope"]
            if not addr_changed and not scope_changed:
                raise ServiceError("地址与经营范围均未变化，无需触发重新核查")
            conn.execute("UPDATE stores SET address=?, business_scope=?, address_seq=address_seq+1"
                         " WHERE id=?", (new_addr, new_scope, store_id))
            reason = []
            if addr_changed:
                reason.append(f"门店迁址：{store['address']} → {new_addr}")
            if scope_changed:
                reason.append(f"经营范围变更：{store['business_scope']} → {new_scope}")
            note = "；".join(reason)
            # 该店所有“当前有效/已过期未换”证都须重新核查；待审版本因地址序号过期会在审批时拦截
            rows = conn.execute(
                "SELECT v.id, v.cert_id FROM cert_versions v JOIN certs c ON c.id=v.cert_id"
                " WHERE c.store_id=? AND v.status IN ('approved','expired')", (store_id,)).fetchall()
            for r in rows:
                self._open_correction(conn, r["id"], "address_check", note, user["id"])
                self._ensure_task(conn, r["cert_id"], "recheck", self.today(),
                                  self.today(), user["id"], note=note)
                conn.execute(
                    "INSERT INTO audit_logs(version_id, action, actor_id, note, created_at)"
                    " VALUES(?,?,?,?,?)", (r["id"], "recheck", user["id"], note, self.now()))
        return note

    # ---------- 版本链 ----------
    def list_versions(self, user, cert_id):
        cert = self._get_cert_checked(user, cert_id)
        return self.db.query(
            "SELECT v.*, u.display AS submitter_display FROM cert_versions v"
            " LEFT JOIN users u ON u.id=v.submitted_by"
            " WHERE v.cert_id=? ORDER BY v.version_no DESC", (cert_id,))

    def current_version(self, conn, cert_id):
        return conn.execute(
            "SELECT * FROM cert_versions WHERE cert_id=? AND status IN ('approved','expired')"
            " ORDER BY id DESC LIMIT 1", (cert_id,)).fetchone()

    def get_version(self, user, version_id):
        row = self.db.query_one(
            "SELECT v.*, c.store_id FROM cert_versions v JOIN certs c ON c.id=v.cert_id"
            " WHERE v.id=?", (version_id,))
        if not row:
            raise ServiceError("证照版本不存在")
        self._check_own(user, row["store_id"])
        return row

    def _get_cert_checked(self, user, cert_id):
        cert = self.db.query_one(
            "SELECT c.*, s.name AS store_name, s.address AS store_address,"
            " s.business_scope AS store_scope, s.address_seq AS store_seq,"
            " t.name AS type_name, t.code AS type_code, t.remind_days"
            " FROM certs c JOIN stores s ON s.id=c.store_id"
            " JOIN cert_types t ON t.id=c.type_id WHERE c.id=?", (cert_id,))
        if not cert:
            raise ServiceError("证照不存在")
        self._check_own(user, cert["store_id"])
        return cert

    def submit_version(self, user, cert_id, *, cert_number, valid_from, valid_to,
                       responsible, scan_rel, scan_width, scan_height, scan_clear,
                       note=""):
        """门店上传续证材料 → 新版本进入待总部审核。"""
        self._require_store(user)
        cert = self._get_cert_checked(user, cert_id)
        vf, vt = parse_date(valid_from), parse_date(valid_to)
        if vf >= vt:
            raise ServiceError("有效期起始日必须早于到期日")
        # 过期拦截：不允许把已经过期的证当作“新证”提交待批
        if vt <= self.today():
            raise ServiceError(
                f"过期拦截：新证到期日 {valid_to} 已不晚于今天 {self.today()}，"
                "不能登记为待生效版本，请先与发证机关核实")
        with self.db.transaction() as conn:
            store = conn.execute("SELECT * FROM stores WHERE id=?", (cert["store_id"],)).fetchone()
            pend = conn.execute(
                "SELECT id FROM cert_versions WHERE cert_id=? AND status='pending_review'",
                (cert_id,)).fetchone()
            if pend:
                raise ServiceError("该证照已有待总部审核的版本，请勿重复提交")
            vno = conn.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM cert_versions WHERE cert_id=?",
                (cert_id,)).fetchone()[0]
            # 地址冻结快照必须与当前门店一致：迁址后不得拿旧地址版本冒充
            prev = self.current_version(conn, cert_id)
            vid = conn.execute(
                "INSERT INTO cert_versions(cert_id, version_no, cert_number, valid_from, valid_to,"
                " address, business_scope, responsible, scan_path, scan_width, scan_height,"
                " scan_clear, address_seq, status, submitted_by, submitted_at, note, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending_review', ?, ?, ?, ?)",
                (cert_id, vno, cert_number.strip(), valid_from, valid_to,
                 store["address"], store["business_scope"], responsible.strip(),
                 scan_rel, scan_width, scan_height, 1 if scan_clear else 0,
                 store["address_seq"], user["id"], self.now(), note, self.now())
            ).lastrowid
            conn.execute(
                "INSERT INTO audit_logs(version_id, action, actor_id, note, created_at)"
                " VALUES(?, 'submit', ?, ?, ?)", (vid, user["id"], note, self.now()))
            # 新旧证有效期衔接核查：理想衔接为 旧证到期次日 = 新证起始日
            if prev:
                gap = (vf - parse_date(prev["valid_to"])).days - 1
                if gap > 0:
                    self._open_correction(
                        conn, vid, "gap",
                        f"新旧证有效期存在 {gap} 天空档：旧证至 {prev['valid_to']}，"
                        f"新证自 {valid_from} 起始", user["id"])
                elif gap < 0:
                    self._open_correction(
                        conn, vid, "gap",
                        f"新旧证有效期重叠 {-gap} 天：旧证至 {prev['valid_to']}，"
                        f"新证自 {valid_from} 起始，请核实", user["id"])
            # 模糊扫描件直接挂补正（审批时也会硬拦截）
            if not scan_clear:
                self._open_correction(conn, vid, "blurry",
                                      "扫描件分辨率过低/字迹模糊，请重新上传清晰原件",
                                      user["id"])
            # 新版本受理后，旧版本上的受理驳回/退回/模糊补正随之闭环
            conn.execute(
                "UPDATE corrections SET status='resolved', resolved_by=?, resolved_at=?,"
                " resolution=COALESCE(NULLIF(resolution,''), ?)"
                " WHERE status='open' AND version_id != ? AND kind IN ('rejected','returned','blurry')"
                " AND version_id IN (SELECT id FROM cert_versions WHERE cert_id=?)",
                (user["id"], self.now(), f"门店已上传 V{vno} 新证材料", vid, cert_id))
        return vid

    def resubmit_scan(self, user, version_id, *, scan_rel, scan_width, scan_height,
                      scan_clear, note=""):
        """总部退回补正后，门店在同版本上重传扫描件并重新送审。"""
        self._require_store(user)
        with self.db.transaction() as conn:
            v = conn.execute(
                "SELECT v.*, c.store_id FROM cert_versions v JOIN certs c ON c.id=v.cert_id"
                " WHERE v.id=?", (version_id,)).fetchone()
            if not v:
                raise ServiceError("版本不存在")
            self._check_own(user, v["store_id"])
            if v["status"] != VERSION_RETURNED:
                raise ServiceError("仅被退回补正的版本可以重新上传扫描件")
            conn.execute(
                "UPDATE cert_versions SET scan_path=?, scan_width=?, scan_height=?,"
                " scan_clear=?, status='pending_review', submitted_at=?, decision_note=''"
                " WHERE id=?",
                (scan_rel, scan_width, scan_height, 1 if scan_clear else 0,
                 self.now(), version_id))
            conn.execute(
                "INSERT INTO audit_logs(version_id, action, actor_id, note, created_at)"
                " VALUES(?, 'submit', ?, ?, ?)",
                (version_id, user["id"], "补正后重新送审：" + note, self.now()))
            if scan_clear:
                conn.execute(
                    "UPDATE corrections SET status='resolved', resolved_by=?, resolved_at=?,"
                    " resolution='门店已重新上传清晰扫描件'"
                    " WHERE version_id=? AND kind='blurry' AND status='open'",
                    (user["id"], self.now(), version_id))
            else:
                self._open_correction(conn, version_id, "blurry",
                                      "重新上传的扫描件仍然模糊，请上传高清原件", user["id"])

    # ---------- 续证受理 ----------
    def record_acceptance(self, user, version_id, accept_no, authority="", note=""):
        self._require_store(user)
        with self.db.transaction() as conn:
            v = conn.execute(
                "SELECT v.*, c.store_id, c.id AS cert_id FROM cert_versions v"
                " JOIN certs c ON c.id=v.cert_id WHERE v.id=?", (version_id,)).fetchone()
            if not v:
                raise ServiceError("版本不存在")
            self._check_own(user, v["store_id"])
            if v["status"] not in (VERSION_PENDING, VERSION_RETURNED):
                raise ServiceError("仅待审核/退回补正中的材料可登记受理号")
            dup = conn.execute("SELECT id FROM renewals WHERE accept_no=? AND status='accepted'",
                               (accept_no.strip(),)).fetchone()
            if dup:
                raise ServiceError("受理号已存在，请勿重复登记")
            rid = conn.execute(
                "INSERT INTO renewals(cert_id, version_id, accept_no, accepted_at, authority,"
                " note, created_by, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (v["cert_id"], version_id, accept_no.strip(), self.today().isoformat(),
                 authority.strip(), note, user["id"], self.now())).lastrowid
        return rid

    def reject_acceptance(self, user, renewal_id, reason):
        """受理驳回：门店记录窗口驳回结果 → 挂材料补正。"""
        with self.db.transaction() as conn:
            r = conn.execute(
                "SELECT r.*, c.store_id FROM renewals r JOIN certs c ON c.id=r.cert_id"
                " WHERE r.id=?", (renewal_id,)).fetchone()
            if not r:
                raise ServiceError("受理记录不存在")
            self._check_own(user, r["store_id"])
            if r["status"] != "accepted":
                raise ServiceError("该受理已结案，不能再标记驳回")
            conn.execute("UPDATE renewals SET status='rejected', reject_reason=? WHERE id=?",
                         (reason.strip(), renewal_id))
            self._open_correction(conn, r["version_id"], "rejected",
                                  f"受理号 {r['accept_no']} 被驳回：{reason.strip()}",
                                  user["id"])

    def list_renewals(self, user, cert_id):
        cert = self._get_cert_checked(user, cert_id)
        return self.db.query("SELECT r.*, u.display AS created_by_display FROM renewals r"
                             " LEFT JOIN users u ON u.id=r.created_by"
                             " WHERE r.cert_id=? ORDER BY r.id DESC", (cert_id,))

    # ---------- 原件遗失 ----------
    def report_origin_lost(self, user, cert_id, note=""):
        self._require_store(user)
        cert = self._get_cert_checked(user, cert_id)
        with self.db.transaction() as conn:
            v = self.current_version(conn, cert_id)
            if not v:
                raise ServiceError("该证照尚无有效版本，无需报失")
            if v["origin_lost"]:
                raise ServiceError("该版本已登记原件遗失")
            conn.execute("UPDATE cert_versions SET origin_lost=1 WHERE id=?", (v["id"],))
            self._open_correction(conn, v["id"], "lost",
                                  "证照原件遗失，需办理补办/挂失后重新归档" +
                                  (f"：{note}" if note else ""), user["id"])

    # ---------- 审核链（总部） ----------
    def review(self, user, version_id, decision, note=""):
        """decision: approve / reject / return。整段事务 + 状态复读 → 并发审批只有一个成功。"""
        self._require_hq(user)
        if decision not in ("approve", "reject", "return"):
            raise ServiceError("未知审核动作")
        with self.db.transaction() as conn:
            v = conn.execute(
                "SELECT v.*, c.store_id, c.id AS cert_id FROM cert_versions v"
                " JOIN certs c ON c.id=v.cert_id WHERE v.id=?", (version_id,)).fetchone()
            if not v:
                raise ServiceError("版本不存在")
            # 并发审批：第二个线程拿到的已是终态
            if v["status"] != VERSION_PENDING:
                raise ServiceError(f"该版本已被处理（当前状态：{v['status']}），不能重复审核")

            if decision == "reject":
                conn.execute(
                    "UPDATE cert_versions SET status='rejected', decided_by=?, decided_at=?,"
                    " decision_note=? WHERE id=?",
                    (user["id"], self.now(), note, version_id))
                self._audit(conn, version_id, "reject", user["id"], note)
                return "rejected"

            if decision == "return":
                conn.execute(
                    "UPDATE cert_versions SET status='returned', decided_by=?, decided_at=?,"
                    " decision_note=? WHERE id=?",
                    (user["id"], self.now(), note, version_id))
                self._open_correction(conn, version_id, "returned",
                                      f"总部退回补正：{note}" if note else "总部退回补正",
                                      user["id"])
                self._audit(conn, version_id, "return", user["id"], note)
                return "returned"

            # approve —— 三道硬关卡
            if parse_date(v["valid_to"]) <= self.today():
                raise ServiceError(
                    f"过期拦截：该证有效期已于 {v['valid_to']} 届满，"
                    "不能标为当前有效版本，请要求门店重新换证")
            store = conn.execute("SELECT * FROM stores WHERE id=?", (v["store_id"],)).fetchone()
            if v["address_seq"] != store["address_seq"]:
                raise ServiceError(
                    "门店已迁址或变更经营范围，该版本为旧地址/旧范围材料，"
                    "须重新核查并按新登记信息提交，不能沿用旧证")
            if not v["scan_clear"]:
                raise ServiceError("扫描件模糊不清，请先退回门店补传清晰原件")

            cur = self.current_version(conn, v["cert_id"])
            if cur:
                conn.execute("UPDATE cert_versions SET status='superseded' WHERE id=?",
                             (cur["id"],))
                self._audit(conn, cur["id"], "supersede", user["id"],
                            f"被 V{v['version_no']} 替代")
            conn.execute(
                "UPDATE cert_versions SET status='approved', decided_by=?, decided_at=?,"
                " decision_note=? WHERE id=?",
                (user["id"], self.now(), note, version_id))
            self._audit(conn, version_id, "approve", user["id"], note)
            # 新证生效：该证全部挂起补正闭环、任务结案
            conn.execute(
                "UPDATE corrections SET status='resolved', resolved_by=?, resolved_at=?,"
                " resolution=COALESCE(NULLIF(resolution,''), ?)"
                " WHERE status='open' AND version_id IN"
                " (SELECT id FROM cert_versions WHERE cert_id=?)",
                (user["id"], self.now(), f"V{v['version_no']} 已审核生效，补正事项闭环",
                 v["cert_id"]))
            conn.execute(
                "UPDATE tasks SET status='done' WHERE cert_id=? AND status='open'",
                (v["cert_id"],))
            return "approved"

    def list_audit_logs(self, user, cert_id=None, limit=200):
        sql = ("SELECT a.*, v.cert_id, v.version_no, u.display AS actor_display,"
               " s.name AS store_name FROM audit_logs a"
               " JOIN cert_versions v ON v.id=a.version_id"
               " JOIN certs c ON c.id=v.cert_id JOIN stores s ON s.id=c.store_id"
               " LEFT JOIN users u ON u.id=a.actor_id")
        args = []
        if cert_id is not None:
            cert = self._get_cert_checked(user, cert_id)
            sql += " WHERE v.cert_id=?"
            args.append(cert_id)
        else:
            ids = self.visible_store_ids(user)
            if ids is not None:
                sql += " WHERE c.store_id=?"
                args.append(ids[0])
        sql += " ORDER BY a.id DESC LIMIT ?"
        args.append(limit)
        return self.db.query(sql, args)

    # ---------- 补正 ----------
    def list_corrections(self, user, status=None, store_id=None):
        sql = ("SELECT k.*, v.version_no, v.cert_id, s.name AS store_name, s.id AS store_id,"
               " t.name AS type_name FROM corrections k"
               " JOIN cert_versions v ON v.id=k.version_id"
               " JOIN certs c ON c.id=v.cert_id JOIN stores s ON s.id=c.store_id"
               " JOIN cert_types t ON t.id=c.type_id")
        cond, args = [], []
        ids = self.visible_store_ids(user)
        if ids is not None:
            cond.append("c.store_id=?")
            args.append(ids[0])
        if status:
            cond.append("k.status=?")
            args.append(status)
        if store_id:
            self._check_own(user, store_id)
            cond.append("c.store_id=?")
            args.append(store_id)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY (k.status='open') DESC, k.id DESC"
        return self.db.query(sql, args)

    def resolve_correction(self, user, correction_id, resolution):
        row = self.db.query_one(
            "SELECT k.*, c.store_id FROM corrections k JOIN cert_versions v ON v.id=k.version_id"
            " JOIN certs c ON c.id=v.cert_id WHERE k.id=?", (correction_id,))
        if not row:
            raise ServiceError("补正记录不存在")
        self._check_own(user, row["store_id"])
        with self.db.transaction() as conn:
            cur = conn.execute("SELECT * FROM corrections WHERE id=?", (correction_id,)).fetchone()
            if cur["status"] == "resolved":
                raise ServiceError("该补正已闭环")
            conn.execute("UPDATE corrections SET status='resolved', resolved_by=?, resolved_at=?,"
                         " resolution=? WHERE id=?",
                         (user["id"], self.now(), resolution.strip(), correction_id))

    # ---------- 任务 / 看板 ----------
    def list_tasks(self, user, status=None):
        sql = ("SELECT tk.*, s.name AS store_name, s.id AS store_id, t.name AS type_name,"
               " u.display AS assignee_display FROM tasks tk"
               " JOIN certs c ON c.id=tk.cert_id JOIN stores s ON s.id=c.store_id"
               " JOIN cert_types t ON t.id=c.type_id"
               " LEFT JOIN users u ON u.id=tk.assignee_id")
        cond, args = [], []
        ids = self.visible_store_ids(user)
        if ids is not None:
            cond.append("c.store_id=?")
            args.append(ids[0])
        if status:
            cond.append("tk.status=?")
            args.append(status)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY tk.due_date, tk.id"
        return self.db.query(sql, args)

    def dashboard(self, user):
        """到期看板汇总（均按 证照单元格 聚合）。"""
        rows = self._matrix_rows(user)
        expired, expiring, recheck, pending = [], [], [], []
        buckets = {7: 0, 30: 0, 90: 0}
        for r in rows:
            for c in r["cells"]:
                v = c["current"]
                if v:
                    days = (parse_date(v["valid_to"]) - self.today()).days
                    if days < 0 or v["status"] == VERSION_EXPIRED:
                        expired.append((r, c))
                    else:
                        for lead in sorted(buckets):
                            if days <= lead:
                                buckets[lead] += 1
                                break
                        if days <= 30:
                            expiring.append((r, c, days))
                if c["recheck"]:
                    recheck.append((r, c))
                if c["pending"]:
                    pending.append((r, c))
        open_corr = self.list_corrections(user, status="open")
        open_tasks = [t for t in self.list_tasks(user, status="open")]
        return {
            "today": self.today().isoformat(),
            "rows": rows,
            "expired": expired,
            "expiring": sorted(expiring, key=lambda x: x[2]),
            "recheck": recheck,
            "pending": pending,
            "corrections": open_corr,
            "tasks": open_tasks,
            "buckets": buckets,
        }

    # ---------- 总部视图：门店行 × 证照列 ----------
    def matrix(self, user):
        return self._matrix_rows(user)

    def _matrix_rows(self, user):
        stores = self.list_stores(user)
        types = self.list_cert_types()
        cert_index = {
            (c["store_id"], c["type_id"]): c
            for c in self.db.query("SELECT * FROM certs")
            if user["role"] == ROLE_HQ or c["store_id"] == user["store_id"]
        }
        today = self.today()
        result = []
        for s in stores:
            cells = []
            for t in types:
                cert = cert_index.get((s["id"], t["id"]))
                cell = {"type": t, "cert": cert, "current": None, "pending": None,
                        "days_left": None, "flags": [], "recheck": False}
                if cert:
                    with self.db.lock:
                        conn = self.db._conn()
                        cell["current"] = conn.execute(
                            "SELECT * FROM cert_versions WHERE cert_id=? AND status IN"
                            " ('approved','expired') ORDER BY id DESC LIMIT 1",
                            (cert["id"],)).fetchone()
                        cell["pending"] = conn.execute(
                            "SELECT * FROM cert_versions WHERE cert_id=? AND status IN"
                            " ('pending_review','returned') ORDER BY id DESC LIMIT 1",
                            (cert["id"],)).fetchone()
                        cell["open_corrections"] = conn.execute(
                            "SELECT kind, reason FROM corrections WHERE status='open'"
                            " AND version_id IN (SELECT id FROM cert_versions WHERE cert_id=?)"
                            " ORDER BY id", (cert["id"],)).fetchall()
                    v = cell["current"]
                    if v:
                        days = (parse_date(v["valid_to"]) - today).days
                        cell["days_left"] = days
                        if days < 0 or v["status"] == VERSION_EXPIRED:
                            cell["flags"].append("expired")
                        else:
                            leads = [int(x) for x in t["remind_days"].split(",")]
                            if any(days <= d for d in leads):
                                cell["flags"].append("expiring")
                        if v["address_seq"] < s["address_seq"]:
                            cell["flags"].append("recheck")
                            cell["recheck"] = True
                        if v["origin_lost"]:
                            cell["flags"].append("lost")
                    if cell["pending"] and cell["pending"]["status"] == "returned":
                        cell["flags"].append("returned")
                    if any(k["kind"] == "blurry" for k in cell["open_corrections"]):
                        cell["flags"].append("blurry")
                    if any(k["kind"] == "rejected" for k in cell["open_corrections"]):
                        cell["flags"].append("rejected")
                    if any(k["kind"] == "gap" for k in cell["open_corrections"]):
                        cell["flags"].append("gap")
                cells.append(cell)
            result.append({"store": s, "cells": cells})
        return result

    def cert_detail(self, user, cert_id):
        cert = self._get_cert_checked(user, cert_id)
        with self.db.lock:
            conn = self.db._conn()
            versions = conn.execute(
                "SELECT v.*, u.display AS submitter_display, d.display AS decider_display"
                " FROM cert_versions v LEFT JOIN users u ON u.id=v.submitted_by"
                " LEFT JOIN users d ON d.id=v.decided_by"
                " WHERE v.cert_id=? ORDER BY v.version_no DESC", (cert_id,)).fetchall()
            renewals = conn.execute(
                "SELECT r.*, u.display AS created_by_display FROM renewals r"
                " LEFT JOIN users u ON u.id=r.created_by WHERE r.cert_id=? ORDER BY r.id DESC",
                (cert_id,)).fetchall()
            corrections = conn.execute(
                "SELECT k.*, u.display AS requested_display, ru.display AS resolved_display"
                " FROM corrections k LEFT JOIN users u ON u.id=k.requested_by"
                " LEFT JOIN users ru ON ru.id=k.resolved_by"
                " WHERE k.version_id IN (SELECT id FROM cert_versions WHERE cert_id=?)"
                " ORDER BY k.id DESC", (cert_id,)).fetchall()
            audits = conn.execute(
                "SELECT a.*, u.display AS actor_display FROM audit_logs a"
                " LEFT JOIN users u ON u.id=a.actor_id"
                " WHERE a.version_id IN (SELECT id FROM cert_versions WHERE cert_id=?)"
                " ORDER BY a.id", (cert_id,)).fetchall()
            tasks = conn.execute(
                "SELECT tk.*, u.display AS assignee_display FROM tasks tk"
                " LEFT JOIN users u ON u.id=tk.assignee_id WHERE tk.cert_id=?"
                " ORDER BY tk.id DESC", (cert_id,)).fetchall()
        return {"cert": cert, "versions": versions, "renewals": renewals,
                "corrections": corrections, "audits": audits, "tasks": tasks}

    # ---------- 内部小工具 ----------
    def _audit(self, conn, version_id, action, actor_id, note):
        conn.execute(
            "INSERT INTO audit_logs(version_id, action, actor_id, note, created_at)"
            " VALUES(?,?,?,?,?)", (version_id, action, actor_id, note, self.now()))

    def _open_correction(self, conn, version_id, kind, reason, actor_id):
        """同版本同类型只保留一条未闭环补正。"""
        exists = conn.execute(
            "SELECT id FROM corrections WHERE version_id=? AND kind=? AND status='open'",
            (version_id, kind)).fetchone()
        if exists:
            return exists["id"]
        return conn.execute(
            "INSERT INTO corrections(version_id, kind, reason, requested_by, created_at)"
            " VALUES(?,?,?,?,?)", (version_id, kind, reason, actor_id, self.now())).lastrowid

    def _ensure_task(self, conn, cert_id, kind, due_date, fire_date, assignee_id, note=""):
        exists = conn.execute(
            "SELECT id FROM tasks WHERE cert_id=? AND kind=? AND due_date=?",
            (cert_id, kind, due_date)).fetchone()
        if exists:
            return exists["id"]
        return conn.execute(
            "INSERT INTO tasks(cert_id, kind, due_date, fire_date, assignee_id, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (cert_id, kind, due_date, fire_date, assignee_id, self.now())).lastrowid

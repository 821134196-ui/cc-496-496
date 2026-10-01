"""进程内提醒引擎：后台线程定时扫描，生成续证任务并通过模拟适配器送达。

- 按总部配置的多档提前量生成 expiring 任务（同档不重复，UNIQUE(cert_id,kind,due_date)）
- 到期当日及之后生成 expired 任务，并把未换证的已批版本标记 expired（只读看板也会判红）
- 送达回执写入 notifications
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .service import (LicenseService, VERSION_APPROVED, parse_date)


class ReminderEngine:
    def __init__(self, svc: LicenseService, notify_targets: Optional[Callable] = None,
                 interval: float = 60.0):
        self.svc = svc
        self.adapter = svc.notifier
        self.interval = interval
        # 返回某门店负责人手机号（样例）；默认虚拟号码
        self.notify_targets = notify_targets or (
            lambda store_id, assignee_id: [f"139{store_id:04d}0001"])
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.tick_count = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="reminder-engine",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        # 启动先跑一次，之后按间隔
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def tick(self):
        """单次扫描。可在测试中直接调用。"""
        svc = self.svc
        today = svc.today()
        with svc.db.transaction() as conn:
            types = {t["id"]: t for t in
                     conn.execute("SELECT * FROM cert_types").fetchall()}
            certs = conn.execute(
                "SELECT c.*, s.name AS store_name FROM certs c"
                " JOIN stores s ON s.id=c.store_id").fetchall()
            for cert in certs:
                cur = conn.execute(
                    "SELECT * FROM cert_versions WHERE cert_id=? AND status IN"
                    " ('approved','expired') ORDER BY id DESC LIMIT 1",
                    (cert["id"],)).fetchone()
                if not cur:
                    continue
                valid_to = parse_date(cur["valid_to"])
                days_left = (valid_to - today).days
                leads = [int(x) for x in types[cert["type_id"]]["remind_days"].split(",")]

                if days_left < 0 and cur["status"] == VERSION_APPROVED:                    # 真正过期：版本置 expired 并生成 expired 任务
                    conn.execute("UPDATE cert_versions SET status='expired' WHERE id=?",
                                 (cur["id"],))
                    conn.execute(
                        "INSERT INTO audit_logs(version_id, action, actor_id, note, created_at)"
                        " VALUES(?, 'expire', NULL, ?, ?)",
                        (cur["id"], f"有效期于 {cur['valid_to']} 届满，系统标记过期",
                         svc.now()))
                    tid = self._ensure_task(conn, cert["id"], "expired",
                                            cur["valid_to"], today, None)
                    already = conn.execute(
                        "SELECT 1 FROM notifications WHERE task_id=? LIMIT 1",
                        (tid,)).fetchone()
                    if not already:
                        self._notify(conn, tid, cert, cur,
                                     f"【证照过期】{cert['store_name']} 的"
                                     f"{types[cert['type_id']]['name']}已于"
                                     f"{cur['valid_to']}过期 {-days_left} 天，请立即换证")
                elif days_left >= 0:
                    for lead in leads:
                        fire = valid_to.fromordinal(valid_to.toordinal() - lead)
                        if today >= fire:
                            tid = self._ensure_task(conn, cert["id"], "expiring",
                                                    cur["valid_to"], fire.isoformat(),
                                                    None)
                            self._notify_if_due(conn, tid, cert, cur, types, days_left, lead)

            # 迁址/范围变更触发的重新核查任务：创建后首次 tick 通知一次
            pending_recheck = conn.execute(
                "SELECT tk.id AS task_id, c.id AS cert_id, c.type_id, c.store_id,"
                " s.name AS store_name, v.* FROM tasks tk"
                " JOIN certs c ON c.id=tk.cert_id JOIN stores s ON s.id=c.store_id"
                " JOIN cert_versions v ON v.cert_id=c.id AND v.status IN ('approved','expired')"
                " WHERE tk.kind='recheck' AND tk.status='open' AND NOT EXISTS"
                " (SELECT 1 FROM notifications n WHERE n.task_id=tk.id)").fetchall()
            for r in pending_recheck:
                self._notify(conn, r["task_id"], r, r,
                             f"【重新核查】{r['store_name']} 已迁址或变更经营范围，"
                             f"{types[r['type_id']]['name']}（{r['cert_number']}）"
                             "不能沿用旧证，请按新登记信息重新提交并接受总部核查")
        self.tick_count += 1

    def _ensure_task(self, conn, cert_id, kind, due_date, fire_date, assignee_id):
        row = conn.execute(
            "SELECT id FROM tasks WHERE cert_id=? AND kind=? AND due_date=?",
            (cert_id, kind, due_date)).fetchone()
        if row:
            return row["id"]
        return conn.execute(
            "INSERT INTO tasks(cert_id, kind, due_date, fire_date, assignee_id, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (cert_id, kind, due_date, fire_date, assignee_id,
             self.svc.now())).lastrowid

    def _notify_if_due(self, conn, task_id, cert, version, types, days_left, lead):
        """每个提前量档位只通知一次（以 receipt 行存在为准）。"""
        already = conn.execute(
            "SELECT 1 FROM notifications WHERE task_id=? AND content LIKE ? LIMIT 1",
            (task_id, f"%提前{lead}天%")).fetchone()
        if already:
            return
        self._notify(conn, task_id, cert, version,
                     f"【证照临期】{cert['store_name']} 的"
                     f"{types[cert['type_id']]['name']}将于 {version['valid_to']} 到期"
                     f"（剩余 {days_left} 天，提前{lead}天提醒），"
                     f"责任人 {version['responsible'] or '未指定'} 请尽快上传续证材料")

    def _notify(self, conn, task_id, cert, version, content):
        if not self.adapter:
            return
        targets = self.notify_targets(cert["store_id"], version["submitted_by"]) or []
        for target in targets:
            rec = self.adapter.send(target, content)
            conn.execute(
                "INSERT INTO notifications(task_id, channel, target, content, receipt,"
                " status, created_at) VALUES(?,?,?,?,?,?,?)",
                (task_id, "sms-mock", target, content, rec["receipt"], rec["status"],
                 self.svc.now()))
        conn.execute("UPDATE tasks SET notified=1 WHERE id=?", (task_id,))

"""测试 1：过期拦截。

- 不允许提交到期日不晚于今天的“新证”
- 待审期间证照自然过期后，总部审批必须被拦截
- 真正过期证照在矩阵中被朱红标记，并由提醒引擎生成过期任务/回执
"""
import datetime as dt
import unittest

from tests.helpers import make_env
from licensehub.service import ServiceError
from licensehub.reminders import ReminderEngine


class ExpiryInterceptionTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env()
        self.svc, self.hq = self.env.svc, self.env.hq
        self.s1 = self.env.stores[1]
        self.today = self.env.today

    def test_submit_already_expired_version_blocked(self):
        # cert 2 = 云栖路旗舰店食品经营许可证，旧证已过期 12 天，无待审版本
        rel, w, h = self.env.scan("exp_v2", landscape=True, title="FOOD PERMIT")
        with self.assertRaises(ServiceError) as cm:
            self.svc.submit_version(
                self.s1, cert_id=2, cert_number="JY-NEW-001",
                valid_from=(self.today - dt.timedelta(days=365)).isoformat(),
                valid_to=(self.today - dt.timedelta(days=1)).isoformat(),
                responsible="王敏", scan_rel=rel, scan_width=w, scan_height=h,
                scan_clear=True)
        self.assertIn("过期拦截", str(cm.exception))

    def test_valid_from_must_precede_valid_to(self):
        rel, w, h = self.env.scan("bad_range")
        with self.assertRaises(ServiceError):
            self.svc.submit_version(
                self.s1, cert_id=2, cert_number="JY-NEW-002",
                valid_from="2026-12-31", valid_to="2026-12-30",
                responsible="王敏", scan_rel=rel, scan_width=w, scan_height=h,
                scan_clear=True)

    def test_approve_blocked_after_cert_expires_in_review(self):
        # 门店今天提交：10 天后到期的新证
        rel, w, h = self.env.scan("short_lived", landscape=True)
        vid = self.svc.submit_version(
            self.s1, cert_id=2, cert_number="JY-SHORT-1",
            valid_from=(self.today + dt.timedelta(days=1)).isoformat(),
            valid_to=(self.today + dt.timedelta(days=10)).isoformat(),
            responsible="王敏", scan_rel=rel, scan_width=w, scan_height=h,
            scan_clear=True)
        # 时间走到到期之后，总部审批 → 拦截
        future = self.env.svc_with_clock(self.today + dt.timedelta(days=20))
        with self.assertRaises(ServiceError) as cm:
            future.review(self.hq, vid, "approve")
        self.assertIn("过期拦截", str(cm.exception))
        # 被拦截的版本不能成为当前有效版本
        v = self.svc.get_version(self.hq, vid)
        self.assertEqual(v["status"], "pending_review")

    def test_expired_flag_task_and_receipt(self):
        # 店 1 食品证已过期 12 天（seed tick 已处理）
        matrix = self.svc.matrix(self.hq)
        cell = next(c for r in matrix if r["store"]["id"] == 1 for c in r["cells"]
                    if c["cert"]["id"] == 2)
        self.assertIn("expired", cell["flags"])
        task = self.svc.db.query_one(
            "SELECT * FROM tasks WHERE cert_id=2 AND kind='expired'")
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "open")
        note = self.svc.db.query_one(
            "SELECT * FROM notifications WHERE task_id=?", (task["id"],))
        self.assertTrue(note["receipt"].startswith("MOCK-"))
        # 审核链存在系统过期记录
        log = self.svc.db.query_one(
            "SELECT * FROM audit_logs WHERE action='expire' AND version_id="
            " (SELECT id FROM cert_versions WHERE cert_id=2 ORDER BY id LIMIT 1)")
        self.assertIsNotNone(log)

    def test_repeated_tick_does_not_duplicate_notifications(self):
        before = self.svc.db.query_one(
            "SELECT COUNT(*) n FROM notifications")["n"]
        engine = ReminderEngine(self.svc, interval=999)
        engine.tick()
        engine.tick()
        after = self.svc.db.query_one("SELECT COUNT(*) n FROM notifications")["n"]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

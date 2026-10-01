"""测试 3：版本切换。

- 总部审核通过后新证标 approved，旧证 superseded
- 模糊件先退回；门店补传清晰件后重新送审、审批通过
- 新旧证有效期衔接空档/重叠自动挂 gap 补正；完美衔接不挂
- 新证生效后该证补正闭环、任务结案
"""
import datetime as dt
import unittest

from tests.helpers import make_env
from licensehub.service import (ServiceError, VERSION_APPROVED, VERSION_SUPERSEDED,
                               VERSION_RETURNED, VERSION_PENDING)


class VersionSwitchTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env()
        self.svc, self.hq = self.env.svc, self.env.hq
        self.s2 = self.env.stores[2]
        self.today = self.env.today

    def test_approve_switches_current_version(self):
        # seed 中店 2 营业执照 V2(id=13) 待审
        pending = self.svc.db.query_one(
            "SELECT id FROM cert_versions WHERE cert_id=4 AND status='pending_review'")
        result = self.svc.review(self.hq, pending["id"], "approve", note="核验通过")
        self.assertEqual(result, "approved")
        cur = self.svc.db.query_one(
            "SELECT * FROM cert_versions WHERE cert_id=4 AND status='approved'")
        old = self.svc.db.query_one(
            "SELECT * FROM cert_versions WHERE cert_id=4 AND status='superseded'")
        self.assertEqual(cur["version_no"], 2)
        self.assertEqual(old["version_no"], 1)
        # 审核链：旧版 supersede + 新版 approve
        actions = [r["action"] for r in self.svc.db.query(
            "SELECT action FROM audit_logs WHERE version_id IN"
            " (SELECT id FROM cert_versions WHERE cert_id=4) ORDER BY id")]
        self.assertIn("supersede", actions)
        self.assertEqual(actions.count("approve"), 2)  # 初始 V1 + 新 V2

    def test_blurry_scan_returned_then_resubmitted_and_approved(self):
        # 店 2 食品证 V2(id=14) 已被 seed 退回补正（模糊）
        v = self.svc.db.query_one(
            "SELECT * FROM cert_versions WHERE cert_id=5 AND status='returned'")
        self.assertIsNotNone(v)
        blurry = self.svc.db.query_one(
            "SELECT * FROM corrections WHERE version_id=? AND kind='blurry' AND status='open'",
            (v["id"],))
        self.assertIsNotNone(blurry)

        # 总部无法审批 returned 版本（它已不在待审队列）
        with self.assertRaises(ServiceError):
            self.svc.review(self.hq, v["id"], "approve")

        # 门店重传清晰扫描件 → 自动重新送审
        rel, w, h = self.env.scan("clear_again", landscape=True, title="FOOD PERMIT")
        self.svc.resubmit_scan(self.s2, v["id"], scan_rel=rel, scan_width=w,
                               scan_height=h, scan_clear=True, note="重传高清件")
        v2 = self.svc.get_version(self.s2, v["id"])
        self.assertEqual(v2["status"], VERSION_PENDING)
        self.assertEqual(v2["scan_clear"], 1)
        # 模糊补正闭环
        self.assertIsNone(self.svc.db.query_one(
            "SELECT id FROM corrections WHERE version_id=? AND kind='blurry' AND status='open'",
            (v["id"],)))
        # 总部通过 → 当前版本切换
        self.assertEqual(self.svc.review(self.hq, v["id"], "approve"), "approved")

    def test_blurry_cannot_be_approved_even_from_pending(self):
        # 店 4 营业执照：提交一个模糊待审版本
        rel, w, h = self.env.scan("blur4", clear=False, title="BUSINESS LICENSE")
        vid = self.svc.submit_version(
            self.env.stores[4], cert_id=10, cert_number="BLUR-001",
            valid_from=self.today.isoformat(), valid_to="2030-01-01",
            responsible="孙芸", scan_rel=rel, scan_width=w, scan_height=h,
            scan_clear=False)
        with self.assertRaises(ServiceError) as cm:
            self.svc.review(self.hq, vid, "approve")
        self.assertIn("模糊", str(cm.exception))

    def test_gap_between_old_and_new_opens_correction(self):
        # seed 店 3 消防证：旧证 +100 天到期，新证 +105 天起始 → 4 天空档
        gap = self.svc.db.query_one(
            "SELECT reason FROM corrections k JOIN cert_versions v ON v.id=k.version_id"
            " WHERE v.cert_id=9 AND k.kind='gap' AND k.status='open'")
        self.assertIsNotNone(gap)
        self.assertIn("4 天空档", gap["reason"])

    def test_perfect_handover_no_gap_flag(self):
        # 店 1 消防证 +18 天到期，门店提交次日衔接的新证（旧证 10/19 到期，新证 10/20 起）
        old = self.svc.db.query_one(
            "SELECT valid_to FROM cert_versions WHERE cert_id=3 AND status='approved'")
        start = (dt.date.fromisoformat(old["valid_to"]) + dt.timedelta(days=1)).isoformat()
        rel, w, h = self.env.scan("seamless", landscape=True, title="FIRE SAFETY")
        vid = self.svc.submit_version(
            self.env.stores[1], cert_id=3, cert_number="XK3301000199",
            valid_from=start, valid_to="2029-10-20", responsible="王敏",
            scan_rel=rel, scan_width=w, scan_height=h, scan_clear=True)
        gap = self.svc.db.query_one(
            "SELECT id FROM corrections WHERE version_id=? AND kind='gap'", (vid,))
        self.assertIsNone(gap)
        self.assertEqual(self.svc.review(self.hq, vid, "approve"), "approved")

    def test_overlapping_validity_flagged(self):
        # 起始日早于旧证到期日 → 重叠
        rel, w, h = self.env.scan("overlap", landscape=True, title="FIRE SAFETY")
        vid = self.svc.submit_version(
            self.env.stores[1], cert_id=3, cert_number="XK3301000188",
            valid_from=(self.today + dt.timedelta(days=5)).isoformat(),
            valid_to="2029-10-05", responsible="王敏",
            scan_rel=rel, scan_width=w, scan_height=h, scan_clear=True)
        gap = self.svc.db.query_one(
            "SELECT reason FROM corrections WHERE version_id=? AND kind='gap'", (vid,))
        self.assertIsNotNone(gap)
        self.assertIn("重叠", gap["reason"])

    def test_new_approved_version_closes_tasks(self):
        # 店 1 消防证有 open expiring 任务；审批新证后任务结案
        self.assertIsNotNone(self.svc.db.query_one(
            "SELECT id FROM tasks WHERE cert_id=3 AND status='open'"))
        old = self.svc.db.query_one(
            "SELECT valid_to FROM cert_versions WHERE cert_id=3 AND status='approved'")
        start = (dt.date.fromisoformat(old["valid_to"]) + dt.timedelta(days=1)).isoformat()
        rel, w, h = self.env.scan("close_task", landscape=True, title="FIRE SAFETY")
        vid = self.svc.submit_version(
            self.env.stores[1], cert_id=3, cert_number="XK3301000177",
            valid_from=start, valid_to="2029-10-20", responsible="王敏",
            scan_rel=rel, scan_width=w, scan_height=h, scan_clear=True)
        self.svc.review(self.hq, vid, "approve")
        self.assertIsNone(self.svc.db.query_one(
            "SELECT id FROM tasks WHERE cert_id=3 AND status='open'"))


if __name__ == "__main__":
    unittest.main()

"""测试 2：门店迁址 / 经营范围变化触发证照重新核查。

- 变更后 address_seq +1，旧证全部挂 address_check 补正与 recheck 任务
- 审核时旧地址序号版本被硬拦截，不能沿用旧证
- 驳回旧版本后按新地址提交的新版本可通过
"""
import datetime as dt
import unittest

from tests.helpers import make_env
from licensehub.service import ServiceError


class AddressChangeTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env()
        self.svc, self.hq = self.env.svc, self.env.hq
        self.s4 = self.env.stores[4]
        self.today = self.env.today

    def test_change_bumps_seq_and_opens_recheck_everywhere(self):
        note = self.svc.change_store_info(
            self.s4, 4, address="厦门市望海路 211 号",
            business_scope="餐饮服务；热食类制售；冷食类制售")
        self.assertIn("迁址", note)
        store = self.svc.get_store(self.hq, 4)
        self.assertEqual(store["address_seq"], 1)

        # 三张当前证都挂了 address_check 补正
        kinds = self.svc.db.query(
            "SELECT k.kind FROM corrections k JOIN cert_versions v ON v.id=k.version_id"
            " JOIN certs c ON c.id=v.cert_id WHERE c.store_id=4 AND k.status='open'")
        self.assertEqual(sum(1 for k in kinds if k["kind"] == "address_check"), 3)

        # 三张证都有 recheck 任务
        n = self.svc.db.query_one(
            "SELECT COUNT(*) n FROM tasks tk JOIN certs c ON c.id=tk.cert_id"
            " WHERE c.store_id=4 AND tk.kind='recheck' AND tk.status='open'")["n"]
        self.assertEqual(n, 3)
        # 审核链留痕
        n_audit = self.svc.db.query_one(
            "SELECT COUNT(*) n FROM audit_logs a JOIN cert_versions v ON v.id=a.version_id"
            " JOIN certs c ON c.id=v.cert_id WHERE a.action='recheck' AND c.store_id=4")["n"]
        self.assertEqual(n_audit, 3)

    def test_old_address_version_cannot_be_approved(self):
        # cert 12 = 望海楼店消防证。先造一个待审 V2（旧地址序号 0）
        rel, w, h = self.env.scan("old_addr", landscape=True, title="FIRE SAFETY")
        vid = self.svc.submit_version(
            self.s4, cert_id=12, cert_number="XK3502000499",
            valid_from=(self.today + dt.timedelta(days=1)).isoformat(),
            valid_to="2029-01-01", responsible="孙芸",
            scan_rel=rel, scan_width=w, scan_height=h, scan_clear=True)
        # 门店迁址
        self.svc.change_store_info(self.s4, 4, address="厦门市望海路 211 号")
        # 总部审批旧版本 → 拦截
        with self.assertRaises(ServiceError) as cm:
            self.svc.review(self.hq, vid, "approve")
        self.assertIn("重新核查", str(cm.exception))

    def test_resubmit_under_new_address_then_approve(self):
        rel, w, h = self.env.scan("old2", landscape=True, title="FIRE SAFETY")
        old_vid = self.svc.submit_version(
            self.s4, cert_id=12, cert_number="XK3502000499",
            valid_from=(self.today + dt.timedelta(days=1)).isoformat(),
            valid_to="2029-01-01", responsible="孙芸",
            scan_rel=rel, scan_width=w, scan_height=h, scan_clear=True)
        self.svc.change_store_info(self.s4, 4, address="厦门市望海路 211 号")
        # 总部驳回旧地址版本
        self.svc.review(self.hq, old_vid, "reject", note="迁址后旧证材料失效")
        # 门店按新地址重新提交（地址快照随门店冻结）
        rel2, w2, h2 = self.env.scan("new_addr", landscape=True, title="FIRE SAFETY")
        new_vid = self.svc.submit_version(
            self.s4, cert_id=12, cert_number="XK3502000500",
            valid_from=self.today.isoformat(), valid_to="2029-12-31",
            responsible="孙芸", scan_rel=rel2, scan_width=w2, scan_height=h2,
            scan_clear=True, note="迁址后重新核查换证")
        new_v = self.svc.get_version(self.s4, new_vid)
        self.assertEqual(new_v["address_seq"], 1)
        self.assertEqual(new_v["address"], "厦门市望海路 211 号")
        result = self.svc.review(self.hq, new_vid, "approve")
        self.assertEqual(result, "approved")
        # recheck 任务随新证生效结案
        open_tasks = self.svc.db.query(
            "SELECT * FROM tasks WHERE cert_id=12 AND status='open'")
        self.assertEqual(open_tasks, [])

    def test_no_change_no_recheck(self):
        store = self.svc.get_store(self.hq, 4)
        with self.assertRaises(ServiceError):
            self.svc.change_store_info(self.s4, 4, address=store["address"],
                                       business_scope=store["business_scope"])


if __name__ == "__main__":
    unittest.main()

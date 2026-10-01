"""测试 4：并发审批。

N 个总部线程同时审批同一个待审版本（BEGIN IMMEDIATE + 状态复读），
必须恰好一个成功，其余收到“已被处理”；不会出现重复 supersede/双生效。
"""
import datetime as dt
import threading
import unittest

from tests.helpers import make_env
from licensehub.service import ServiceError


class ConcurrentApprovalTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env()
        self.svc, self.hq = self.env.svc, self.env.hq
        self.today = self.env.today

    def _submit(self, cert_id, number, store_user, landscape=True):
        rel, w, h = self.env.scan(f"c{cert_id}_{number}", landscape=landscape)
        return self.svc.submit_version(
            store_user, cert_id=cert_id, cert_number=number,
            valid_from=self.today.isoformat(), valid_to="2030-01-01",
            responsible="经办人", scan_rel=rel, scan_width=w, scan_height=h,
            scan_clear=True)

    def test_parallel_approve_single_winner(self):
        # cert 12 = 店 4 消防证，当前无待审版本
        vid = self._submit(12, "XK-CONC-1", self.env.stores[4])
        n = 8
        barrier = threading.Barrier(n)
        results = []
        lock = threading.Lock()

        def worker():
            barrier.wait()
            try:
                r = self.svc.review(self.hq, vid, "approve", note="并发审批")
                with lock:
                    results.append(("ok", r))
            except ServiceError as e:
                with lock:
                    results.append(("err", str(e)))

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        oks = [r for r in results if r[0] == "ok"]
        errs = [r for r in results if r[0] == "err"]
        self.assertEqual(len(oks), 1, f"应恰好一次成功，实际 {results}")
        self.assertEqual(len(errs), n - 1)
        self.assertTrue(all("已被处理" in e for _, e in errs))

        # 库里状态唯一
        v = self.svc.get_version(self.hq, vid)
        self.assertEqual(v["status"], "approved")
        n_approved = self.svc.db.query_one(
            "SELECT COUNT(*) n FROM cert_versions WHERE cert_id=12 AND status='approved'")["n"]
        self.assertEqual(n_approved, 1)
        n_superseded = self.svc.db.query_one(
            "SELECT COUNT(*) n FROM cert_versions WHERE cert_id=12 AND status='superseded'")["n"]
        self.assertEqual(n_superseded, 1)
        # 审核链中 approve 仅一次
        n_approve_logs = self.svc.db.query_one(
            "SELECT COUNT(*) n FROM audit_logs WHERE version_id=? AND action='approve'",
            (vid,))["n"]
        self.assertEqual(n_approve_logs, 1)

    def test_parallel_approve_different_versions_both_win(self):
        # 不同证照的并发审批互不阻塞，各自成功
        v1 = self._submit(12, "XK-CONC-A", self.env.stores[4])
        v2 = self._submit(10, "BL-CONC-B", self.env.stores[4], landscape=False)
        out = {}
        barrier = threading.Barrier(2)

        def worker(vid, key):
            barrier.wait()
            try:
                out[key] = self.svc.review(self.hq, vid, "approve")
            except ServiceError as e:
                out[key] = f"ERR {e}"

        t1 = threading.Thread(target=worker, args=(v1, "a"))
        t2 = threading.Thread(target=worker, args=(v2, "b"))
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(out, {"a": "approved", "b": "approved"})

    def test_parallel_submit_only_one_pending(self):
        # 同证并发提交：只允许一个待审版本
        n = 5
        barrier = threading.Barrier(n)
        results = []
        lock = threading.Lock()

        def worker(i):
            rel, w, h = self.env.scan(f"race_{i}")
            barrier.wait()
            try:
                vid = self.svc.submit_version(
                    self.env.stores[4], cert_id=11, cert_number=f"RACE-{i}",
                    valid_from=self.today.isoformat(), valid_to="2030-01-01",
                    responsible="孙芸", scan_rel=rel, scan_width=w, scan_height=h,
                    scan_clear=True)
                with lock:
                    results.append(("ok", vid))
            except ServiceError as e:
                with lock:
                    results.append(("err", str(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len([r for r in results if r[0] == "ok"]), 1)
        pending = self.svc.db.query(
            "SELECT id FROM cert_versions WHERE cert_id=11 AND status='pending_review'")
        self.assertEqual(len(pending), 1)


if __name__ == "__main__":
    unittest.main()

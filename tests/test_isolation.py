"""测试 5：数据隔离（服务层 + HTTP 层）。

- 门店账号只能读/写本店证照、受理、补正、任务
- 总部可看全部；审核动作只有总部可做
- HTTP 层：越权 GET/POST 返回 403；未登录 302 跳登录
"""
import http.client
import tempfile
import threading
import unittest
from pathlib import Path

from tests.helpers import make_env
from licensehub.server import build_server
from licensehub.service import PermissionDenied, ServiceError


class ServiceIsolationTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env()
        self.svc, self.hq = self.env.svc, self.env.hq
        self.s1 = self.env.stores[1]
        self.s2 = self.env.stores[2]

    def test_store_sees_only_own_rows(self):
        stores = self.svc.list_stores(self.s1)
        self.assertEqual([s["id"] for s in stores], [1])

        tasks = self.svc.list_tasks(self.s1)
        self.assertTrue(tasks)
        self.assertTrue(all(t["store_id"] == 1 for t in tasks))

        corrs = self.svc.list_corrections(self.s1)
        self.assertTrue(all(c["store_id"] == 1 for c in corrs))

        matrix = self.svc.matrix(self.s1)
        self.assertEqual(len(matrix), 1)
        self.assertEqual(matrix[0]["store"]["id"], 1)

    def test_store_cannot_read_other_store(self):
        # cert 4 属于店 2
        with self.assertRaises(PermissionDenied):
            self.svc.cert_detail(self.s1, 4)
        with self.assertRaises(PermissionDenied):
            self.svc.list_versions(self.s1, 4)
        with self.assertRaises(PermissionDenied):
            self.svc.list_renewals(self.s1, 4)

    def test_store_cannot_write_other_store(self):
        rel, w, h = self.env.scan("x_cross")
        with self.assertRaises(PermissionDenied):
            self.svc.submit_version(
                self.s1, cert_id=4, cert_number="CROSS",
                valid_from="2026-10-02", valid_to="2030-01-01",
                responsible="王敏", scan_rel=rel, scan_width=w, scan_height=h,
                scan_clear=True)
        # 触发别家迁址
        with self.assertRaises(PermissionDenied):
            self.svc.change_store_info(self.s1, 2, address="某市某路 1 号")
        # 别家的待审版本（店 2 营业执照 V2）
        vid = self.svc.db.query_one(
            "SELECT id FROM cert_versions WHERE cert_id=4 AND status='pending_review'")["id"]
        with self.assertRaises(PermissionDenied):
            self.svc.review(self.s1, vid, "approve")

    def test_store_cannot_review_even_own_cert(self):
        rel, w, h = self.env.scan("own_review")
        vid = self.svc.submit_version(
            self.s1, cert_id=1, cert_number="OWN-REVIEW-1",
            valid_from="2026-10-02", valid_to="2030-01-01",
            responsible="王敏", scan_rel=rel, scan_width=w, scan_height=h,
            scan_clear=True)
        with self.assertRaises(PermissionDenied):
            self.svc.review(self.s1, vid, "approve")

    def test_only_hq_can_change_remind_config(self):
        with self.assertRaises(PermissionDenied):
            self.svc.update_remind_days(self.s1, 1, "15,5")
        self.svc.update_remind_days(self.hq, 1, "120,60,15")
        t = self.svc.db.query_one("SELECT remind_days FROM cert_types WHERE id=1")
        self.assertEqual(t["remind_days"], "15,60,120")
        with self.assertRaises(ServiceError):
            self.svc.update_remind_days(self.hq, 1, "0,-3")

    def test_hq_sees_all_stores(self):
        self.assertEqual(len(self.svc.list_stores(self.hq)), 4)
        self.assertEqual(len(self.svc.matrix(self.hq)), 4)


# ---------------- HTTP 层 ----------------
class HttpSession:
    def __init__(self, conn: http.client.HTTPConnection):
        self.conn = conn
        self.sid = None

    def request(self, method, path, body=None, ctype="application/x-www-form-urlencoded"):
        headers = {}
        if self.sid:
            headers["Cookie"] = f"sid={self.sid}"
        if body is not None:
            headers["Content-Type"] = ctype
        self.conn.request(method, path, body=body, headers=headers)
        resp = self.conn.getresponse()
        data = resp.read()
        sc = resp.getheader("Set-Cookie")
        if sc and "sid=" in sc:
            self.sid = sc.split("sid=", 1)[1].split(";", 1)[0]
        return resp.status, resp.getheader("Location"), data


class HttpIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="lh_http_")
        cls.server = build_server("127.0.0.1", 0, cls.tmp, start_engine=False)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def session(self):
        return HttpSession(http.client.HTTPConnection("127.0.0.1", self.port, timeout=5))

    def login(self, username):
        s = self.session()
        status, loc, _ = s.request("POST", "/login", f"username={username}&pin=1234")
        self.assertEqual(status, 302)
        self.assertTrue(s.sid)
        return s

    def test_anonymous_redirected(self):
        s = self.session()
        for path in ("/", "/matrix", "/cert/4", "/config"):
            status, loc, _ = s.request("GET", path)
            self.assertEqual(status, 302, path)
            self.assertEqual(loc, "/login", path)

    def test_wrong_pin_rejected(self):
        s = self.session()
        status, loc, _ = s.request("POST", "/login", "username=legal&pin=0000")
        self.assertEqual(status, 302)
        self.assertEqual(loc, "/login?err=1")
        self.assertFalse(s.sid)

    def test_cross_store_get_is_403(self):
        s1 = self.login("store1")
        status, _, _ = s1.request("GET", "/cert/4")   # cert 4 属店 2
        self.assertEqual(status, 403)
        status, _, _ = s1.request("GET", "/store/2")
        self.assertEqual(status, 403)
        status, _, _ = s1.request("GET", "/scan/13")  # V2 属店 2
        self.assertEqual(status, 403)
        status, _, _ = s1.request("GET", "/audits")
        self.assertEqual(status, 403)
        status, _, _ = s1.request("GET", "/config")
        self.assertEqual(status, 403)

    def test_cross_store_write_is_403(self):
        s1 = self.login("store1")
        # 试图审批店 2 的待审版本
        status, _, body = s1.request(
            "POST", "/version/13/review", "decision=approve&note=hack")
        self.assertEqual(status, 403)
        # 版本仍为待审，未被门店账号越权生效
        db = self.server.app.db
        v = db.query_one("SELECT status FROM cert_versions WHERE id=13")
        self.assertEqual(v["status"], "pending_review")

    def test_hq_can_view_and_approve_over_http(self):
        hq = self.login("legal")
        status, _, body = hq.request("GET", "/cert/4")
        self.assertEqual(status, 200)
        self.assertIn("审核链".encode(), body)
        status, loc, _ = hq.request(
            "POST", "/version/13/review", "decision=approve&note=HTTP%20%E5%AE%A1%E6%89%B9")
        self.assertEqual(status, 302)
        db = self.server.app.db
        self.assertEqual(db.query_one(
            "SELECT status FROM cert_versions WHERE id=13")["status"], "approved")
        # 再点一次 → 400（终态保护）
        status, _, _ = hq.request("POST", "/version/13/review", "decision=approve")
        self.assertEqual(status, 400)

    def test_store_pages_visible_to_owner(self):
        s2 = self.login("store2")
        for path in ("/", "/matrix", "/tasks", "/corrections", "/cert/5", "/store/2",
                     "/scan/14"):
            status, _, _ = s2.request("GET", path)
            self.assertEqual(status, 200, path)


if __name__ == "__main__":
    unittest.main()

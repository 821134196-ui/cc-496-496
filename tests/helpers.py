"""测试夹具：固定时钟下播种多门店样例。"""
import datetime as dt
import os
import tempfile
from pathlib import Path

# 小尺寸合成扫描件，加速播种（必须在导入 licensehub 模块前设置）
os.environ.setdefault("LICENHUB_FAST_SCAN", "1")

from licensehub.db import DB
from licensehub.notifier import MockSmsAdapter
from licensehub.seed import seed
from licensehub.service import LicenseService

FIXED_TODAY = dt.date(2026, 10, 1)


class Env:
    def __init__(self, tmp: str, today=FIXED_TODAY):
        self.dir = Path(tmp)
        self.db = DB(str(self.dir / "test.db"))
        self.svc = seed(self.db, self.dir, today=today, run_tick=True)
        self.today = today
        self.hq = self.svc.get_user("legal")
        self.stores = {i: self.svc.get_user(f"store{i}") for i in range(1, 5)}

    def svc_with_clock(self, today: dt.date) -> LicenseService:
        """同一数据库、换一个系统时钟（模拟时间流逝）。"""
        return LicenseService(self.db, clock=lambda: today, notifier=MockSmsAdapter())

    def scan(self, name, clear=True, landscape=False, title="CERT", fields=None):
        from licensehub import scans
        rel = f"{name}.png"
        w, h = scans.generate_sample(
            self.dir / "scans" / rel, title,
            fields or ["NO 12345", f"VALID {self.today} TO 2099-01-01"],
            landscape=landscape, clear=clear)
        return rel, w, h


def make_env() -> Env:
    return Env(tempfile.mkdtemp(prefix="lh_test_"))

"""模拟送达适配器：不真正发短信，返回固定格式的送达回执。

真实环境可替换为短信/IM 适配器，只需实现 send(target, content) -> receipt。
"""
from __future__ import annotations

import datetime as dt
import itertools
import threading


class MockSmsAdapter:
    """进程内模拟短信通道。每条消息返回回执号与发送时间戳。"""

    def __init__(self):
        self._seq = itertools.count(1)
        self._lock = threading.Lock()
        self.sent: list[dict] = []

    def send(self, target: str, content: str) -> dict:
        with self._lock:
            n = next(self._seq)
            receipt = f"MOCK-{dt.datetime.now():%Y%m%d%H%M%S}-{n:05d}"
            record = {
                "receipt": receipt,
                "target": target,
                "content": content,
                "status": "sent",
                "sent_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.sent.append(record)
        return record

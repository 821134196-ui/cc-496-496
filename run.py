#!/usr/bin/env python3
"""单条命令启动：初始化 SQLite、生成多门店样例、开启进程内提醒与 Web 服务。

用法：
    python3 run.py                # 首次运行自动播种；默认 0.0.0.0:8000
    python3 run.py --reset        # 清空样例库后重新生成
    python3 run.py --port 8080
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from licensehub.server import build_server


def main():
    ap = argparse.ArgumentParser(description="多门店证照管理系统")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data", default="data", help="数据目录（SQLite + 扫描件）")
    ap.add_argument("--reset", action="store_true", help="删除既有数据并重新生成样例")
    args = ap.parse_args()

    data_dir = Path(args.data)
    if args.reset and data_dir.exists():
        shutil.rmtree(data_dir)
        print(f"[run] 已清空旧数据目录 {data_dir}")

    httpd = build_server(args.host, args.port, data_dir, start_engine=True)
    print("=" * 64)
    print(" 多门店证照管理系统 LICENSE HUB 已启动")
    print(f"   地址：http://localhost:{args.port}")
    print("   账号：legal（总部法务） / store1..store4（门店），口令均为 1234")
    print(f"   数据：{data_dir.resolve()}（SQLite 版本/审核链 + 本地扫描件）")
    print("   提醒：进程内后台线程每 60 秒扫描；送达回执由模拟短信适配器返回")
    print("=" * 64)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[run] 正在停止……")
        httpd.app.engine.stop()
        httpd.shutdown()
        time.sleep(0.2)
        sys.exit(0)


if __name__ == "__main__":
    main()

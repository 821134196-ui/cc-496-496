"""多门店样例数据：一条命令生成 4 门店 × 3 类证照的完整场景。

覆盖：真正过期、临期待办、待总部审核、扫描模糊退回、受理驳回、
原件遗失、新旧证衔接空档、迁址重新核查。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import scans
from .db import DB
from .notifier import MockSmsAdapter
from .reminders import ReminderEngine
from .service import LicenseService

CERT_TYPES = [
    ("business_license", "营业执照", "90,30"),
    ("food_permit", "食品经营许可证", "60,30,7"),
    ("fire_safety", "消防安全检查合格证", "30"),
]

STORES = [
    # name, address, scope, owner, 场景备注
    ("云栖路旗舰店", "杭州市云栖路 12 号 1 层", "餐饮服务；预包装食品销售", "王敏"),
    ("梧桐里店", "苏州市梧桐里街区 8 栋 102", "餐饮服务", "陈立"),
    ("滨河湾店", "成都市滨河湾路 66 号", "餐饮服务；散装食品销售", "赵航"),
    ("望海楼店", "厦门市望海路 210 号", "餐饮服务；热食类制售", "孙芸"),
]


def seed(db: DB, data_dir: str | Path, today: dt.date | None = None,
         run_tick: bool = True) -> LicenseService:
    today = today or dt.date.today()
    data_dir = Path(data_dir)
    scan_dir = data_dir / "scans"
    scan_dir.mkdir(parents=True, exist_ok=True)

    def d(offset):
        return (today + dt.timedelta(days=offset)).isoformat()

    adapter = MockSmsAdapter()
    svc = LicenseService(db, clock=lambda: today, notifier=adapter)

    with db.transaction() as conn:
        conn.execute("INSERT INTO users(username, display, pin, role) VALUES(?,?,?,?)",
                     ("legal", "总部法务-周律", "1234", "hq"))
        for i, (name, addr, scope, owner) in enumerate(STORES, start=1):
            conn.execute(
                "INSERT INTO stores(name, address, business_scope, address_seq, created_at)"
                " VALUES(?,?,?,0,?)",
                (name, addr, scope, svc.now()))
            conn.execute(
                "INSERT INTO users(username, display, pin, role, store_id)"
                " VALUES(?,?,?, 'store', ?)",
                (f"store{i}", f"{owner}（{name}）", "1234", i))
        for code, name, leads in CERT_TYPES:
            conn.execute(
                "INSERT INTO cert_types(code, name, remind_days, created_at) VALUES(?,?,?,?)",
                (code, name, leads, svc.now()))
        for sid in range(1, 5):
            for tid in range(1, 3 + 1):
                conn.execute(
                    "INSERT INTO certs(store_id, type_id, created_at) VALUES(?,?,?)",
                    (sid, tid, svc.now()))

    hq = svc.get_user("legal")

    def make_scan(version_label, title, fields, *, landscape=False, clear=True, stamp="SEAL"):
        rel = f"{version_label}.png"
        scans.generate_sample(scan_dir / rel, title, fields,
                              landscape=landscape, clear=clear, stamp=stamp)
        w, h = scans.read_png_size(scan_dir / rel)
        return rel, w, h

    def insert_approved(store_id, type_id, *, vno, number, vfrom, vto, owner,
                        clear=True, landscape=False, title="CERT", stamp="SEAL"):
        cert_id = (store_id - 1) * 3 + type_id
        rel, w, h = make_scan(f"s{store_id}_t{type_id}_v{vno}", title,
                              [f"NO {number}", f"VALID {vfrom} TO {vto}",
                               f"OWNER {owner}"], landscape=landscape, clear=clear,
                              stamp=stamp)
        store = svc.get_store(hq, store_id)
        with db.transaction() as conn:
            vid = conn.execute(
                "INSERT INTO cert_versions(cert_id, version_no, cert_number, valid_from,"
                " valid_to, address, business_scope, responsible, scan_path, scan_width,"
                " scan_height, scan_clear, address_seq, status, submitted_by, submitted_at,"
                " decided_by, decided_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,"
                " 'approved', ?, ?, ?, ?, ?)",
                (cert_id, vno, number, vfrom, vto, store["address"],
                 store["business_scope"], owner, rel, w, h, 1 if clear else 0,
                 store["address_seq"], hq["id"], svc.now(), hq["id"], svc.now(),
                 svc.now())).lastrowid
            conn.execute(
                "INSERT INTO audit_logs(version_id, action, actor_id, note, created_at)"
                " VALUES(?, 'submit', ?, '初始建档', ?)", (vid, hq["id"], svc.now()))
            conn.execute(
                "INSERT INTO audit_logs(version_id, action, actor_id, note, created_at)"
                " VALUES(?, 'approve', ?, '总部核验通过', ?)", (vid, hq["id"], svc.now()))
        return cert_id, vid

    # ---- 店1：食品证已真正过期 12 天；消防证 18 天后到期（临期） ----
    insert_approved(1, 1, vno=1, number="91330100MA00A1XX01", vfrom=d(-1800), vto=d(200),
                    owner="王敏", title="BUSINESS LICENSE")
    insert_approved(1, 2, vno=1, number="JY13301000000101", vfrom=d(-1800), vto=d(-12),
                    owner="王敏", landscape=True, title="FOOD PERMIT")
    insert_approved(1, 3, vno=1, number="XK消3301000101", vfrom=d(-1000), vto=d(18),
                    owner="王敏", landscape=True, title="FIRE SAFETY")

    # ---- 店2：营业执照新证待总部审核；食品证扫描模糊被退回补正 ----
    insert_approved(2, 1, vno=1, number="91320500MA00B2XX02", vfrom=d(-1700), vto=d(45),
                    owner="陈立", title="BUSINESS LICENSE")
    insert_approved(2, 2, vno=1, number="JY13205000000202", vfrom=d(-1500), vto=d(200),
                    owner="陈立", landscape=True, title="FOOD PERMIT")
    insert_approved(2, 3, vno=1, number="XK消3205000202", vfrom=d(-900), vto=d(300),
                    owner="陈立", landscape=True, title="FIRE SAFETY")

    # ---- 店3：营业执照迁址待重新核查；食品证受理驳回；消防证新旧衔接空档 ----
    insert_approved(3, 1, vno=1, number="91510100MA00C3XX03", vfrom=d(-1600), vto=d(300),
                    owner="赵航", title="BUSINESS LICENSE")
    insert_approved(3, 2, vno=1, number="JY15101000000303", vfrom=d(-1400), vto=d(400),
                    owner="赵航", landscape=True, title="FOOD PERMIT")
    insert_approved(3, 3, vno=1, number="XK消5101000303", vfrom=d(-1200), vto=d(100),
                    owner="赵航", landscape=True, title="FIRE SAFETY")

    # ---- 店4：营业执照原件遗失；食品证 8 天后到期且未提交续证 ----
    insert_approved(4, 1, vno=1, number="91350200MA00D4XX04", vfrom=d(-1500), vto=d(250),
                    owner="孙芸", title="BUSINESS LICENSE")
    insert_approved(4, 2, vno=1, number="JY13502000000404", vfrom=d(-1800), vto=d(8),
                    owner="孙芸", landscape=True, title="FOOD PERMIT")
    insert_approved(4, 3, vno=1, number="XK消3502000404", vfrom=d(-800), vto=d(500),
                    owner="孙芸", landscape=True, title="FIRE SAFETY")

    u2, u3, u4 = (svc.get_user(f"store{i}") for i in (2, 3, 4))

    # 店2-营业执照：续证 V2 已提交，等待总部审核
    rel, w, h = make_scan("s2_t1_v2", "BUSINESS LICENSE",
                          ["NO 91320500MA00B2XX02", f"VALID {d(46)} TO {d(46+1825)}",
                           "OWNER CHEN LI"])
    svc.submit_version(u2, cert_id=4, cert_number="91320500MA00B2XX02",
                       valid_from=d(46), valid_to=d(46 + 1825), responsible="陈立",
                       scan_rel=rel, scan_width=w, scan_height=h, scan_clear=True,
                       note="营业执照续期材料")

    # 店2-食品证：V2 扫描件模糊 → 总部退回补正
    rel, w, h = make_scan("s2_t2_v2", "FOOD PERMIT",
                          ["NO JY13205000000202", f"VALID {d(201)} TO {d(201+1825)}"],
                          landscape=True, clear=False)
    vid_blur = svc.submit_version(u2, cert_id=5, cert_number="JY13205000000202",
                                  valid_from=d(201), valid_to=d(201 + 1825),
                                  responsible="陈立", scan_rel=rel, scan_width=w,
                                  scan_height=h, scan_clear=False, note="窗口换发新证")
    svc.review(hq, vid_blur, "return", note="扫描件模糊，编号与有效期无法辨认，请重传")

    # 店3-营业执照：门店迁址（经营范围也变化）→ 触发重新核查
    svc.change_store_info(u3, 3, address="成都市滨河湾路 88 号附 1 号",
                          business_scope="餐饮服务；散装食品销售；冷食类制售")

    # 店3-食品证：续证 V2 已提交，受理后被窗口驳回
    rel, w, h = make_scan("s3_t2_v2", "FOOD PERMIT",
                          ["NO JY15101000000303", f"VALID {d(401)} TO {d(401+1825)}",
                           "OWNER ZHAO HANG"], landscape=True)
    vid_food3 = svc.submit_version(u3, cert_id=8, cert_number="JY15101000000303",
                                   valid_from=d(401), valid_to=d(401 + 1825),
                                   responsible="赵航", scan_rel=rel, scan_width=w,
                                   scan_height=h, scan_clear=True, note="食品证延续")
    rid = svc.record_acceptance(u3, vid_food3, "SL2026-CDY-7788",
                                authority="成都市市场监督管理局", note="延续受理")
    svc.reject_acceptance(u3, rid, reason="经营场所平面图与申请书不一致，需补正")

    # 店3-消防证：V2 起始日晚于旧证到期 5 天，形成衔接空档
    rel, w, h = make_scan("s3_t3_v2", "FIRE SAFETY",
                          ["NO XK5101000303", f"VALID {d(105)} TO {d(105+1095)}"],
                          landscape=True)
    svc.submit_version(u3, cert_id=9, cert_number="XK消5101000303",
                       valid_from=d(105), valid_to=d(105 + 1095), responsible="赵航",
                       scan_rel=rel, scan_width=w, scan_height=h, scan_clear=True,
                       note="消防合格证延续")

    # 店4-营业执照：原件遗失报失
    svc.report_origin_lost(u4, cert_id=10, note="门店装修期间遗失")

    if run_tick:
        engine = ReminderEngine(svc, interval=9999)
        engine.tick()

    return svc

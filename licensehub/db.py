"""证照管理系统 —— SQLite 数据层

保存：门店/用户、证照类型（总部可配提醒提前量）、证照版本链、
受理、补正、审核链、续证任务、送达回执、报失记录。
"""
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS stores (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    address     TEXT NOT NULL,
    business_scope TEXT NOT NULL DEFAULT '',
    address_seq INTEGER NOT NULL DEFAULT 0,          -- 迁址 / 经营范围变更序号，触发重新核查
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id        INTEGER PRIMARY KEY,
    username  TEXT NOT NULL UNIQUE,
    display   TEXT NOT NULL,
    pin       TEXT NOT NULL DEFAULT '',
    role      TEXT NOT NULL CHECK (role IN ('hq','store')),
    store_id  INTEGER REFERENCES stores(id),         -- 门店角色必填；总部为 NULL
    CHECK (role = 'hq' OR store_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS cert_types (
    id               INTEGER PRIMARY KEY,
    code             TEXT NOT NULL UNIQUE,           -- business_license / food_permit / ...
    name             TEXT NOT NULL,
    remind_days      TEXT NOT NULL DEFAULT '30',     -- 多档提前量，逗号分隔（如 90,30,7）
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS certs (
    id           INTEGER PRIMARY KEY,
    store_id     INTEGER NOT NULL REFERENCES stores(id),
    type_id      INTEGER NOT NULL REFERENCES cert_types(id),
    created_at   TEXT NOT NULL,
    UNIQUE (store_id, type_id)
);

-- 证照版本链：同一证照每次提交/上传产生一个新版本；总部审核通过后成为当前有效版本
CREATE TABLE IF NOT EXISTS cert_versions (
    id                INTEGER PRIMARY KEY,
    cert_id           INTEGER NOT NULL REFERENCES certs(id),
    version_no        INTEGER NOT NULL,
    cert_number       TEXT NOT NULL,
    valid_from        TEXT NOT NULL,                 -- YYYY-MM-DD
    valid_to          TEXT NOT NULL,
    address           TEXT NOT NULL,                 -- 本版本登记的门店地址（冻结快照）
    business_scope    TEXT NOT NULL DEFAULT '',
    responsible      TEXT NOT NULL DEFAULT '',
    scan_path        TEXT,                            -- 相对文件目录的扫描件路径
    scan_width       INTEGER,
    scan_height      INTEGER,
    scan_clear       INTEGER NOT NULL DEFAULT 1,      -- 0 = 模糊
    origin_lost      INTEGER NOT NULL DEFAULT 0,      -- 原件遗失
    address_seq       INTEGER NOT NULL DEFAULT 0,     -- 提交时门店地址序号
    status            TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (status IN ('draft','pending_review','returned','approved','rejected',
                          'superseded','expired')),
    submitted_by      INTEGER REFERENCES users(id),
    submitted_at      TEXT,
    decided_by        INTEGER REFERENCES users(id),
    decided_at        TEXT,
    decision_note     TEXT NOT NULL DEFAULT '',
    note              TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    UNIQUE (cert_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_versions_cert ON cert_versions(cert_id);

-- 续证受理（门店向监管/窗口提交续证材料的受理号）
CREATE TABLE IF NOT EXISTS renewals (
    id             INTEGER PRIMARY KEY,
    cert_id        INTEGER NOT NULL REFERENCES certs(id),
    version_id     INTEGER REFERENCES cert_versions(id),
    accept_no      TEXT NOT NULL,                     -- 受理号
    accepted_at    TEXT NOT NULL,                     -- 受理日期
    authority      TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'accepted'
        CHECK (status IN ('accepted','rejected','closed')),
    reject_reason  TEXT NOT NULL DEFAULT '',
    created_by     INTEGER REFERENCES users(id),
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_renewals_cert ON renewals(cert_id);

-- 材料补正（受理驳回/审核退回/扫描模糊等）
CREATE TABLE IF NOT EXISTS corrections (
    id           INTEGER PRIMARY KEY,
    version_id   INTEGER NOT NULL REFERENCES cert_versions(id),
    renewal_id   INTEGER REFERENCES renewals(id),
    kind         TEXT NOT NULL
        CHECK (kind IN ('returned','rejected','blurry','lost','gap','address_check','expired')),
    reason       TEXT NOT NULL DEFAULT '',
    requested_by INTEGER REFERENCES users(id),
    resolved_by  INTEGER REFERENCES users(id),
    resolved_at  TEXT,
    resolution   TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_corrections_version ON corrections(version_id);
CREATE INDEX IF NOT EXISTS idx_corrections_status ON corrections(status);

-- 审核记录（总部审核链：每次状态裁决一行）
CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY,
    version_id  INTEGER NOT NULL REFERENCES cert_versions(id),
    action      TEXT NOT NULL
        CHECK (action IN ('submit','approve','reject','return','supersede','expire','recheck')),
    actor_id    INTEGER REFERENCES users(id),
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_version ON audit_logs(version_id);

-- 续证任务（提醒引擎按提前量生成；同档不重复）
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY,
    cert_id      INTEGER NOT NULL REFERENCES certs(id),
    version_id   INTEGER REFERENCES cert_versions(id),
    kind         TEXT NOT NULL CHECK (kind IN ('expiring','expired','recheck')),
    due_date     TEXT NOT NULL,                      -- 触发任务的证照到期日
    fire_date    TEXT NOT NULL,                      -- 任务生成日（提前量日）
    assignee_id  INTEGER REFERENCES users(id),
    status       TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','done','cancelled')),
    notified     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    UNIQUE (cert_id, kind, due_date)
);

-- 送达回执（模拟适配器返回）
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY,
    task_id     INTEGER REFERENCES tasks(id),
    channel     TEXT NOT NULL DEFAULT 'sms-mock',
    target      TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    receipt     TEXT NOT NULL DEFAULT '',             -- 回执号
    status      TEXT NOT NULL DEFAULT 'sent'
        CHECK (status IN ('sent','failed')),
    created_at  TEXT NOT NULL
);

-- 提醒配置变更留痕（总部法务调整提前量）
CREATE TABLE IF NOT EXISTS remind_config_log (
    id          INTEGER PRIMARY KEY,
    type_id     INTEGER NOT NULL REFERENCES cert_types(id),
    old_days    TEXT NOT NULL DEFAULT '',
    new_days    TEXT NOT NULL DEFAULT '',
    actor_id    INTEGER REFERENCES users(id),
    created_at  TEXT NOT NULL
);
"""


class DB:
    """线程安全的 SQLite 封装：每连接一把锁，写事务串行化。"""

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._tl = threading.local()
        self.init()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._tl, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            self._tl.conn = conn
        return conn

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def init(self):
        with self._lock:
            self._conn().executescript(SCHEMA)

    def query(self, sql, args=()):
        with self._lock:
            return self._conn().execute(sql, args).fetchall()

    def query_one(self, sql, args=()):
        with self._lock:
            return self._conn().execute(sql, args).fetchone()

    def execute(self, sql, args=()):
        with self._lock:
            return self._conn().execute(sql, args)

    def transaction(self):
        return _Tx(self)


class _Tx:
    def __init__(self, db: DB):
        self.db = db
        self.conn = None

    def __enter__(self):
        self.db.lock.acquire()
        self.conn = self.db._conn()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.execute("COMMIT")
            else:
                self.conn.execute("ROLLBACK")
        finally:
            self.db.lock.release()
        return False

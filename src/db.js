'use strict';
const { DatabaseSync } = require('node:sqlite');

const SCHEMA = `
CREATE TABLE IF NOT EXISTS stores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  address TEXT NOT NULL,
  business_scope TEXT NOT NULL,
  manager_name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('hq_legal','store_manager')),
  store_id INTEGER REFERENCES stores(id)
);

CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS license_types (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  remind_days INTEGER NOT NULL DEFAULT 30,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS licenses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id INTEGER NOT NULL REFERENCES stores(id),
  type_id INTEGER NOT NULL REFERENCES license_types(id),
  current_version_id INTEGER,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','expired')),
  recheck_required INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (store_id, type_id)
);

CREATE TABLE IF NOT EXISTS license_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  license_id INTEGER NOT NULL REFERENCES licenses(id),
  version_no INTEGER NOT NULL,
  cert_no TEXT NOT NULL,
  issue_date TEXT NOT NULL,
  expiry_date TEXT NOT NULL,
  address TEXT NOT NULL,
  scope TEXT NOT NULL,
  responsible_person TEXT NOT NULL,
  scan_path TEXT,
  scan_width INTEGER,
  scan_height INTEGER,
  status TEXT NOT NULL DEFAULT 'pending_review'
    CHECK (status IN ('pending_review','current','superseded','rejected')),
  acceptance_no TEXT,
  is_original_lost INTEGER NOT NULL DEFAULT 0,
  is_scan_blurry INTEGER NOT NULL DEFAULT 0,
  submitted_by INTEGER NOT NULL REFERENCES users(id),
  submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
  reviewed_by INTEGER REFERENCES users(id),
  reviewed_at TEXT,
  review_comment TEXT,
  UNIQUE (license_id, version_no)
);

CREATE TABLE IF NOT EXISTS renewal_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  license_id INTEGER NOT NULL REFERENCES licenses(id),
  version_id INTEGER REFERENCES license_versions(id),
  due_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','submitted','rejected','correcting','completed','overdue')),
  acceptance_no TEXT,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  renewal_task_id INTEGER NOT NULL REFERENCES renewal_tasks(id),
  version_id INTEGER REFERENCES license_versions(id),
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
  requested_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_type TEXT NOT NULL,
  entity_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  actor_id INTEGER NOT NULL REFERENCES users(id),
  comment TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS store_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id INTEGER NOT NULL REFERENCES stores(id),
  change_type TEXT NOT NULL CHECK (change_type IN ('address','scope')),
  old_value TEXT NOT NULL,
  new_value TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending_recheck' CHECK (status IN ('pending_recheck','rechecked')),
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  rechecked_at TEXT
);

CREATE TABLE IF NOT EXISTS flags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  license_id INTEGER NOT NULL REFERENCES licenses(id),
  version_id INTEGER REFERENCES license_versions(id),
  flag_type TEXT NOT NULL CHECK (flag_type IN
    ('acceptance_rejected','overdue_unsubmitted','original_lost','blurry_scan','validity_gap','recheck_required')),
  message TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  license_id INTEGER NOT NULL REFERENCES licenses(id),
  renewal_task_id INTEGER REFERENCES renewal_tasks(id),
  recipient TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'mock-sms',
  payload TEXT NOT NULL,
  receipt_no TEXT,
  delivery_status TEXT NOT NULL,
  sent_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_versions_license ON license_versions(license_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_license ON renewal_tasks(license_id, status);
CREATE INDEX IF NOT EXISTS idx_flags_license ON flags(license_id, status);
CREATE INDEX IF NOT EXISTS idx_reviews_entity ON reviews(entity_type, entity_id);
`;

function openDb(dbPath) {
  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA journal_mode = WAL;');
  db.exec('PRAGMA foreign_keys = ON;');
  db.exec(SCHEMA);
  return db;
}

/** 同步事务：BEGIN IMMEDIATE 保证写锁，异常回滚 */
function tx(db, fn) {
  db.exec('BEGIN IMMEDIATE');
  try {
    const r = fn();
    db.exec('COMMIT');
    return r;
  } catch (e) {
    try { db.exec('ROLLBACK'); } catch { /* already rolled back */ }
    throw e;
  }
}

module.exports = { openDb, tx };

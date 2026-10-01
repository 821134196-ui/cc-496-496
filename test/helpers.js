'use strict';
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');
const { openDb } = require('../src/db');
const { createApp } = require('../src/app');
const { createMockNotifier } = require('../src/notify');
const { sha256, todayStr, addDays } = require('../src/util');

/**
 * 测试夹具（与演示种子相互独立）：
 *  门店 A（张三）：营业执照 10 天后到期（临期）；食品许可已过期 3 天
 *  门店 B（李四）：营业执照 100 天后到期；食品许可无任何版本
 */
function seedFixture(db, today = todayStr()) {
  const insStore = db.prepare('INSERT INTO stores (code, name, address, business_scope, manager_name) VALUES (?,?,?,?,?)');
  const insUser = db.prepare('INSERT INTO users (username, password_hash, display_name, role, store_id) VALUES (?,?,?,?,?)');
  const insType = db.prepare('INSERT INTO license_types (code, name, remind_days) VALUES (?,?,?)');
  const insLicense = db.prepare('INSERT INTO licenses (store_id, type_id, status) VALUES (?,?,?)');
  const insVersion = db.prepare(`INSERT INTO license_versions
    (license_id, version_no, cert_no, issue_date, expiry_date, address, scope, responsible_person, status, submitted_by)
    VALUES (?,?,?,?,?,?,?,?,?,?)`);

  const sA = insStore.run('TA', '测试门店A', 'A市朝阳区一号路1号', '餐饮服务', '张三').lastInsertRowid;
  const sB = insStore.run('TB', '测试门店B', 'B市海淀区二号路2号', '餐饮服务', '李四').lastInsertRowid;
  const hq = insUser.run('hq', sha256('hq123'), '总部法务', 'hq_legal', null).lastInsertRowid;
  const ma = insUser.run('ma', sha256('store123'), '张三', 'store_manager', sA).lastInsertRowid;
  const mb = insUser.run('mb', sha256('store123'), '李四', 'store_manager', sB).lastInsertRowid;
  const tBiz = insType.run('BIZ', '营业执照', 30).lastInsertRowid;
  const tFood = insType.run('FOOD', '食品经营许可证', 45).lastInsertRowid;

  const mkLicense = (storeId, typeId) => insLicense.run(storeId, typeId, 'active').lastInsertRowid;
  const mkCurrent = (licId, certNo, issue, expiry, storeId, managerUser) => {
    const st = db.prepare('SELECT * FROM stores WHERE id = ?').get(storeId);
    const vid = insVersion.run(licId, 1, certNo, issue, expiry, st.address, st.business_scope,
      st.manager_name, 'current', managerUser).lastInsertRowid;
    db.prepare('UPDATE licenses SET current_version_id = ? WHERE id = ?').run(vid, licId);
    return vid;
  };

  const LA1 = mkLicense(sA, tBiz);
  const vLA1 = mkCurrent(LA1, 'CERT-A-BIZ-1', addDays(today, -355), addDays(today, 10), sA, ma);
  const LA2 = mkLicense(sA, tFood);
  const vLA2 = mkCurrent(LA2, 'CERT-A-FOOD-1', addDays(today, -368), addDays(today, -3), sA, ma);
  const LB1 = mkLicense(sB, tBiz);
  const vLB1 = mkCurrent(LB1, 'CERT-B-BIZ-1', addDays(today, -265), addDays(today, 100), sB, mb);
  const LB2 = mkLicense(sB, tFood); // 无有效版本

  return { today, sA, sB, hq, ma, mb, tBiz, tFood, LA1, LA2, LB1, LB2, vLA1, vLA2, vLB1 };
}

async function startTestApp(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lic-test-'));
  const db = openDb(path.join(dir, 'test.db'));
  const notifier = createMockNotifier();
  const ids = seedFixture(db);
  const { app } = createApp({ db, scansDir: path.join(dir, 'scans'), notifier });
  const server = await new Promise((resolve) => {
    const s = app.listen(0, '127.0.0.1', () => resolve(s));
  });
  t.after(() => new Promise((resolve) => {
    if (server.closeAllConnections) server.closeAllConnections();
    server.close(resolve);
  }));
  return { base: `http://127.0.0.1:${server.address().port}`, db, notifier, ids };
}

async function login(base, username, password) {
  const res = await fetch(`${base}/api/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(`login failed: ${JSON.stringify(data)}`);
  return data.token;
}

/** 返回带状态的简易客户端（不断言，便于测试错误码） */
function client(base, token) {
  return async (p, { method = 'GET', body } = {}) => {
    const res = await fetch(`${base}${p}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    return { status: res.status, data };
  };
}

module.exports = { startTestApp, login, client, addDays, todayStr };

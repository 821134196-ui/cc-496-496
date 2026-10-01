'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startTestApp, login, client } = require('./helpers');
const { runSweep } = require('../src/reminders');

test('过期证照出示被拦截（LICENSE_EXPIRED）', async (t) => {
  const { base, ids } = await startTestApp(t);
  const ma = client(base, await login(base, 'ma', 'store123'));

  const r = await ma(`/api/licenses/${ids.LA2}/use`, { method: 'POST' });
  assert.equal(r.status, 409);
  assert.equal(r.data.error, 'LICENSE_EXPIRED');
  assert.match(r.data.message, /到期/);

  // 未过期证照可正常出示
  const ok = await ma(`/api/licenses/${ids.LA1}/use`, { method: 'POST' });
  assert.equal(ok.status, 200);
  assert.equal(ok.data.ok, true);
});

test('巡检：过期置状态、临期建任务、打「临期未提交」标记、送达回执落库', async (t) => {
  const { db, notifier, ids } = await startTestApp(t);

  const result = runSweep(db, notifier, ids.today);

  // 过期证照状态翻转
  const la2 = db.prepare('SELECT * FROM licenses WHERE id = ?').get(ids.LA2);
  assert.equal(la2.status, 'expired');
  assert.ok(result.expired >= 1);

  // 临期未提交标记（仅过期且未提交新版本的证照）
  const flag = db.prepare(
    "SELECT * FROM flags WHERE license_id = ? AND flag_type = 'overdue_unsubmitted' AND status = 'open'",
  ).get(ids.LA2);
  assert.ok(flag, '应存在临期未提交标记');

  // 临期证照自动建续证任务
  const task = db.prepare('SELECT * FROM renewal_tasks WHERE license_id = ?').get(ids.LA1);
  assert.ok(task, '临期证照应自动建任务');
  assert.equal(task.status, 'pending');

  // 过期任务为 overdue
  const task2 = db.prepare('SELECT * FROM renewal_tasks WHERE license_id = ?').get(ids.LA2);
  assert.equal(task2.status, 'overdue');

  // 提醒送达回执：临期 + 过期各一条
  const reminders = db.prepare('SELECT * FROM reminders ORDER BY id').all();
  assert.equal(reminders.length, 2);
  for (const r of reminders) {
    assert.match(r.receipt_no, /^RCPT-\d+$/);
    assert.equal(r.delivery_status, 'delivered');
    assert.equal(r.channel, 'mock-sms');
  }
  assert.equal(notifier.sent.length, 2);

  // 幂等：24 小时内重复巡检不重复发提醒、不重复建任务
  const again = runSweep(db, notifier, ids.today);
  assert.equal(again.reminders_sent, 0);
  assert.equal(again.tasks_created, 0);
  assert.equal(db.prepare('SELECT COUNT(*) AS c FROM reminders').get().c, 2);
});

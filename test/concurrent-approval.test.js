'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startTestApp, login, client, todayStr } = require('./helpers');

test('并发审批：同一版本仅一人审核成功', async (t) => {
  const { base, db, ids } = await startTestApp(t);
  const mb = client(base, await login(base, 'mb', 'store123'));
  const hq1 = client(base, await login(base, 'hq', 'hq123'));
  const hq2 = client(base, await login(base, 'hq', 'hq123'));

  const submit = await mb(`/api/licenses/${ids.LB1}/versions`, {
    method: 'POST',
    body: {
      cert_no: 'CERT-B-BIZ-2', issue_date: todayStr(), expiry_date: '2027-10-01',
      address: 'B市海淀区二号路2号', scope: '餐饮服务', responsible_person: '李四',
    },
  });
  const vid = submit.data.version_id;

  // 两个审核会话同时点击「通过」
  const [r1, r2] = await Promise.all([
    hq1(`/api/versions/${vid}/review`, { method: 'POST', body: { action: 'approve', comment: '审核人A' } }),
    hq2(`/api/versions/${vid}/review`, { method: 'POST', body: { action: 'approve', comment: '审核人B' } }),
  ]);
  const results = [r1, r2];
  assert.equal(results.filter((r) => r.status === 200).length, 1, '只能有一个审核成功');
  assert.equal(results.filter((r) => r.status === 409).length, 1, '另一个必须收到冲突');
  assert.equal(results.find((r) => r.status === 409).data.error, 'ALREADY_REVIEWED');

  // 审核链上只有一条 approve 记录
  const approves = db.prepare(
    "SELECT COUNT(*) AS c FROM reviews WHERE entity_type = 'license_version' AND entity_id = ? AND action = 'approve'",
  ).get(vid).c;
  assert.equal(approves, 1);

  // 版本只被处理一次，当前版本唯一
  const v = db.prepare('SELECT * FROM license_versions WHERE id = ?').get(vid);
  assert.equal(v.status, 'current');
  const currents = db.prepare(
    "SELECT COUNT(*) AS c FROM license_versions WHERE license_id = ? AND status = 'current'",
  ).get(ids.LB1).c;
  assert.equal(currents, 1);
});

test('并发审批：通过与驳回竞态，同样只有一方生效', async (t) => {
  const { base, db, ids } = await startTestApp(t);
  const mb = client(base, await login(base, 'mb', 'store123'));
  const hq1 = client(base, await login(base, 'hq', 'hq123'));
  const hq2 = client(base, await login(base, 'hq', 'hq123'));

  const submit = await mb(`/api/licenses/${ids.LB2}/versions`, {
    method: 'POST',
    body: {
      cert_no: 'CERT-B-FOOD-1', issue_date: todayStr(), expiry_date: '2027-10-01',
      address: 'B市海淀区二号路2号', scope: '餐饮服务', responsible_person: '李四',
    },
  });
  const vid = submit.data.version_id;

  const [r1, r2] = await Promise.all([
    hq1(`/api/versions/${vid}/review`, { method: 'POST', body: { action: 'approve' } }),
    hq2(`/api/versions/${vid}/review`, { method: 'POST', body: { action: 'reject', comment: '存疑' } }),
  ]);
  const okCount = [r1, r2].filter((r) => r.status === 200).length;
  const conflictCount = [r1, r2].filter((r) => r.status === 409).length;
  assert.equal(okCount, 1);
  assert.equal(conflictCount, 1);

  const v = db.prepare('SELECT * FROM license_versions WHERE id = ?').get(vid);
  assert.ok(['current', 'rejected'].includes(v.status));
  const decisions = db.prepare(
    "SELECT COUNT(*) AS c FROM reviews WHERE entity_type = 'license_version' AND entity_id = ? AND action IN ('approve','reject')",
  ).get(vid).c;
  assert.equal(decisions, 1, '审核链上只能有一条最终结论');
});

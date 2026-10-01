'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startTestApp, login, client, addDays, todayStr } = require('./helpers');

test('版本切换：审核通过后旧版作废、新版成为当前有效版本，断档打标', async (t) => {
  const { base, db, ids } = await startTestApp(t);
  const mb = client(base, await login(base, 'mb', 'store123'));
  const hq = client(base, await login(base, 'hq', 'hq123'));

  // 新证生效日晚于旧证到期日 5 天 → 有效期断档
  const old = db.prepare('SELECT * FROM license_versions WHERE id = ?').get(ids.vLB1);
  const submit = await mb(`/api/licenses/${ids.LB1}/versions`, {
    method: 'POST',
    body: {
      cert_no: 'CERT-B-BIZ-2',
      issue_date: addDays(old.expiry_date, 5),
      expiry_date: addDays(old.expiry_date, 370),
      address: 'B市海淀区二号路2号', scope: '餐饮服务', responsible_person: '李四',
      acceptance_no: 'SL-TEST-100',
    },
  });
  assert.equal(submit.status, 201);
  const vid = submit.data.version_id;

  // 审核前：当前版本不变，出示仍按旧证
  let lic = db.prepare('SELECT * FROM licenses WHERE id = ?').get(ids.LB1);
  assert.equal(lic.current_version_id, ids.vLB1);

  const approve = await hq(`/api/versions/${vid}/review`, {
    method: 'POST', body: { action: 'approve', comment: '同意换发' },
  });
  assert.equal(approve.status, 200);

  lic = db.prepare('SELECT * FROM licenses WHERE id = ?').get(ids.LB1);
  assert.equal(lic.current_version_id, vid, '当前版本应切换到新版');
  const v1 = db.prepare('SELECT * FROM license_versions WHERE id = ?').get(ids.vLB1);
  const v2 = db.prepare('SELECT * FROM license_versions WHERE id = ?').get(vid);
  assert.equal(v1.status, 'superseded', '旧版应作废');
  assert.equal(v2.status, 'current');
  assert.equal(v2.reviewed_by, ids.hq);

  // 同一证照任一时刻只有一个 current 版本
  const currents = db.prepare(
    "SELECT COUNT(*) AS c FROM license_versions WHERE license_id = ? AND status = 'current'",
  ).get(ids.LB1).c;
  assert.equal(currents, 1);

  // 新旧证有效期衔接不一致 → 断档标记
  const gap = db.prepare(
    "SELECT * FROM flags WHERE license_id = ? AND flag_type = 'validity_gap' AND status = 'open'",
  ).get(ids.LB1);
  assert.ok(gap, '断档应打标');
  assert.match(gap.message, /断档 4 天/);

  // 续证任务完成
  const task = db.prepare('SELECT * FROM renewal_tasks WHERE version_id = ?').get(vid);
  assert.equal(task.status, 'completed');
});

test('门店不能自审自批；驳回后当前版本不变', async (t) => {
  const { base, db, ids } = await startTestApp(t);
  const mb = client(base, await login(base, 'mb', 'store123'));
  const hq = client(base, await login(base, 'hq', 'hq123'));

  const submit = await mb(`/api/licenses/${ids.LB1}/versions`, {
    method: 'POST',
    body: {
      cert_no: 'CERT-B-BIZ-3', issue_date: todayStr(), expiry_date: '2027-12-31',
      address: 'B市海淀区二号路2号', scope: '餐饮服务', responsible_person: '李四',
    },
  });
  const vid = submit.data.version_id;

  // 门店试图自己审核 → 403
  const selfReview = await mb(`/api/versions/${vid}/review`, {
    method: 'POST', body: { action: 'approve' },
  });
  assert.equal(selfReview.status, 403);

  // 总部驳回
  const reject = await hq(`/api/versions/${vid}/review`, {
    method: 'POST', body: { action: 'reject', comment: '材料不全' },
  });
  assert.equal(reject.status, 200);
  const lic = db.prepare('SELECT * FROM licenses WHERE id = ?').get(ids.LB1);
  assert.equal(lic.current_version_id, ids.vLB1, '驳回不影响当前有效版本');
  const flag = db.prepare(
    "SELECT * FROM flags WHERE license_id = ? AND flag_type = 'acceptance_rejected' AND status = 'open'",
  ).get(ids.LB1);
  assert.ok(flag, '受理驳回应打标');
});

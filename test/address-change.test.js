'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startTestApp, login, client, todayStr } = require('./helpers');

test('迁址触发全店证照重新核查，不能沿用旧证', async (t) => {
  const { base, db, ids } = await startTestApp(t);
  const ma = client(base, await login(base, 'ma', 'store123'));
  const hq = client(base, await login(base, 'hq', 'hq123'));
  const newAddr = 'A市朝阳区新址路99号';

  // 1. 上报迁址
  const chg = await ma(`/api/stores/${ids.sA}/changes`, {
    method: 'POST',
    body: { change_type: 'address', new_value: newAddr },
  });
  assert.equal(chg.status, 201);

  // 2. 全店证照进入待核查，旧证出示被拦截
  for (const licId of [ids.LA1, ids.LA2]) {
    const lic = db.prepare('SELECT * FROM licenses WHERE id = ?').get(licId);
    assert.equal(lic.recheck_required, 1, `证照 ${licId} 应待核查`);
    const flag = db.prepare(
      "SELECT * FROM flags WHERE license_id = ? AND flag_type = 'recheck_required' AND status = 'open'",
    ).get(licId);
    assert.ok(flag, '应有变更待核查标记');
    const use = await ma(`/api/licenses/${licId}/use`, { method: 'POST' });
    assert.equal(use.status, 409);
    assert.equal(use.data.error, 'RECHECK_REQUIRED');
  }

  // 3. 门店按新地址提交换证材料 → 总部审核通过 → 该证照核查解除
  const today = todayStr();
  const submit = await ma(`/api/licenses/${ids.LA1}/versions`, {
    method: 'POST',
    body: {
      cert_no: 'CERT-A-BIZ-2', issue_date: today, expiry_date: '2027-10-01',
      address: newAddr, scope: '餐饮服务', responsible_person: '张三', acceptance_no: 'SL-TEST-001',
    },
  });
  assert.equal(submit.status, 201);
  const approve = await hq(`/api/versions/${submit.data.version_id}/review`, {
    method: 'POST',
    body: { action: 'approve', comment: '新址材料齐全' },
  });
  assert.equal(approve.status, 200);

  const lic1 = db.prepare('SELECT * FROM licenses WHERE id = ?').get(ids.LA1);
  assert.equal(lic1.recheck_required, 0);
  const use = await ma(`/api/licenses/${ids.LA1}/use`, { method: 'POST' });
  assert.equal(use.status, 200, '核查通过后新证可正常使用');

  // 4. 另一证照未核查前，门店变更记录仍为待核查；全部核查后转为已核查
  let change = db.prepare('SELECT * FROM store_changes WHERE id = ?').get(chg.data.change_id);
  assert.equal(change.status, 'pending_recheck');

  const submit2 = await ma(`/api/licenses/${ids.LA2}/versions`, {
    method: 'POST',
    body: {
      cert_no: 'CERT-A-FOOD-2', issue_date: today, expiry_date: '2027-10-01',
      address: newAddr, scope: '餐饮服务', responsible_person: '张三',
    },
  });
  await hq(`/api/versions/${submit2.data.version_id}/review`, {
    method: 'POST', body: { action: 'approve' },
  });
  change = db.prepare('SELECT * FROM store_changes WHERE id = ?').get(chg.data.change_id);
  assert.equal(change.status, 'rechecked');
  assert.ok(change.rechecked_at);
});

test('沿用旧地址提交不能解除核查', async (t) => {
  const { base, db, ids } = await startTestApp(t);
  const ma = client(base, await login(base, 'ma', 'store123'));
  const hq = client(base, await login(base, 'hq', 'hq123'));

  await ma(`/api/stores/${ids.sA}/changes`, {
    method: 'POST',
    body: { change_type: 'address', new_value: 'A市朝阳区新址路99号' },
  });
  // 门店图省事沿用旧证地址提交
  const submit = await ma(`/api/licenses/${ids.LA1}/versions`, {
    method: 'POST',
    body: {
      cert_no: 'CERT-A-BIZ-2', issue_date: todayStr(), expiry_date: '2027-10-01',
      address: 'A市朝阳区一号路1号', scope: '餐饮服务', responsible_person: '张三',
    },
  });
  assert.equal(submit.status, 201);
  await hq(`/api/versions/${submit.data.version_id}/review`, {
    method: 'POST', body: { action: 'approve' },
  });
  const lic = db.prepare('SELECT * FROM licenses WHERE id = ?').get(ids.LA1);
  assert.equal(lic.recheck_required, 1, '证载地址与门店新址不一致，核查不得解除');
});

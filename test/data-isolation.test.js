'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { startTestApp, login, client } = require('./helpers');

test('数据隔离：门店只能管理本店资料', async (t) => {
  const { base, ids } = await startTestApp(t);
  const ma = client(base, await login(base, 'ma', 'store123'));
  const hq = client(base, await login(base, 'hq', 'hq123'));

  // 跨店读取被拦截
  assert.equal((await ma(`/api/licenses?store_id=${ids.sB}`)).status, 403);
  assert.equal((await ma(`/api/licenses/${ids.LB1}`)).status, 403);

  // 跨店提交续证被拦截
  const submit = await ma(`/api/licenses/${ids.LB1}/versions`, {
    method: 'POST',
    body: {
      cert_no: 'X', issue_date: '2026-01-01', expiry_date: '2027-01-01',
      address: 'B市海淀区二号路2号', scope: '餐饮服务', responsible_person: '李四',
    },
  });
  assert.equal(submit.status, 403);

  // 跨店出示核验被拦截
  assert.equal((await ma(`/api/licenses/${ids.LB1}/use`, { method: 'POST' })).status, 403);

  // 跨店变更被拦截
  assert.equal((await ma(`/api/stores/${ids.sB}/changes`, {
    method: 'POST', body: { change_type: 'address', new_value: '越权地址' },
  })).status, 403);

  // 门店无总部权限
  assert.equal((await ma('/api/matrix')).status, 403);
  assert.equal((await ma(`/api/license-types/${ids.tBiz}`, {
    method: 'PUT', body: { remind_days: 7 },
  })).status, 403);
  assert.equal((await ma('/api/admin/sweep', { method: 'POST' })).status, 403);

  // 门店列表只返回本店
  const mine = await ma('/api/licenses');
  assert.equal(mine.status, 200);
  assert.ok(mine.data.licenses.every((l) => l.store_id === ids.sA));
  assert.equal(mine.data.licenses.length, 2);

  // 续证任务 / 标记只含本店
  const renewals = await ma('/api/renewals');
  assert.ok(renewals.data.renewals.every((r) => r.store_id === ids.sA));
  const flags = await ma('/api/flags');
  assert.ok(flags.data.flags.every((f) => f.store_id === ids.sA));

  // 未登录一律 401
  const anon = client(base, null);
  assert.equal((await anon('/api/licenses')).status, 401);
  assert.equal((await anon('/api/matrix')).status, 401);

  // 总部可见全部门店
  const matrix = await hq('/api/matrix');
  assert.equal(matrix.status, 200);
  assert.equal(matrix.data.rows.length, 2);
});

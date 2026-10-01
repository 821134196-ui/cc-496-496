'use strict';
/* 证照台账前端：原生 JS SPA */

const state = {
  token: localStorage.getItem('lic_token') || null,
  user: JSON.parse(localStorage.getItem('lic_user') || 'null'),
  tab: null,
};

const $ = (sel, el = document) => el.querySelector(sel);
const app = () => $('#app');

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.message || `请求失败（${res.status}）`);
    err.code = data.error;
    err.status = res.status;
    throw err;
  }
  return data;
}

function toast(msg, kind = 'ok') {
  const el = document.createElement('div');
  el.className = `toast${kind === 'error' ? ' error' : ''}`;
  el.textContent = msg;
  $('#toast-root').appendChild(el);
  setTimeout(() => el.remove(), 3600);
}

const run = (p) => Promise.resolve(p).catch((e) => toast(e.message, 'error'));

/* ---------- 展示辅助 ---------- */
const FLAG_LABEL = {
  acceptance_rejected: '受理驳回',
  overdue_unsubmitted: '临期未提交',
  original_lost: '原件遗失',
  blurry_scan: '扫描件模糊',
  validity_gap: '有效期衔接异常',
  recheck_required: '变更待核查',
};
const SEVERE_FLAGS = new Set(['overdue_unsubmitted']);
const TASK_STATUS = {
  pending: '待提交', submitted: '待总部审核', rejected: '已驳回',
  correcting: '补正中', completed: '已完成', overdue: '逾期未提交',
};
const VERSION_STATUS = {
  pending_review: '待审核', current: '当前有效', superseded: '已作废', rejected: '已驳回',
};
const REVIEW_ACTION = {
  submit: '提交材料', approve: '审核通过', reject: '审核驳回',
  request_correction: '要求补正', mark_lost: '登记遗失', mark_blurry: '标记模糊',
  resolve_correction: '补正完成', resolve_flag: '解除标记', config: '配置变更',
};

function daysLabel(days) {
  if (days === null || days === undefined) return '<span class="muted">无有效版本</span>';
  if (days < 0) return `<b style="color:var(--verm)">已过期 ${-days} 天</b>`;
  return `<b>剩余 ${days} 天</b>`;
}

function statusBadge(lic) {
  if (lic.status === 'expired') return '<span class="badge expired">已过期</span>';
  if (lic.recheck_required) return '<span class="badge warn">待核查</span>';
  return '<span class="badge ok">有效</span>';
}

function flagPills(flags) {
  if (!flags || !flags.length) return '';
  return flags.map((f) => {
    const label = f.label || FLAG_LABEL[f.flag_type] || f.type;
    const severe = SEVERE_FLAGS.has(f.flag_type || f.type) ? ' severe' : '';
    return `<span class="flag-pill${severe}">${esc(label)}</span>`;
  }).join(' ');
}

const fmtTime = (s) => (s ? String(s).replace('T', ' ').slice(0, 16) : '—');

/* ---------- 登录 ---------- */
function renderLogin() {
  app().innerHTML = `
    <div class="login-wrap">
      <div class="login-card">
        <h1>证照台账</h1>
        <p class="sub">连锁门店证照登记 · 续证 · 审核 · 到期提醒</p>
        <form id="login-form">
          <div class="field"><label>用户名</label><input id="login-username" autocomplete="username" required></div>
          <div class="field"><label>密码</label><input id="login-password" type="password" autocomplete="current-password" required></div>
          <button class="btn primary" type="submit">登 录</button>
        </form>
        <div class="demo-accounts">
          <p>演示账号（点击填入）</p>
          <div class="chips">
            <button class="chip" data-u="hq" data-p="hq123">总部法务 hq</button>
            <button class="chip" data-u="store1" data-p="store123">北京中关村店</button>
            <button class="chip" data-u="store2" data-p="store123">上海静安店</button>
            <button class="chip" data-u="store3" data-p="store123">广州天河店</button>
            <button class="chip" data-u="store4" data-p="store123">成都高新店</button>
          </div>
        </div>
      </div>
    </div>`;
  $('#login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const { token, user } = await api('/api/login', {
        method: 'POST',
        body: { username: $('#login-username').value.trim(), password: $('#login-password').value },
      });
      state.token = token;
      state.user = user;
      localStorage.setItem('lic_token', token);
      localStorage.setItem('lic_user', JSON.stringify(user));
      renderApp();
    } catch (err) {
      toast(err.message, 'error');
    }
  });
  document.querySelectorAll('.chip').forEach((chip) => chip.addEventListener('click', () => {
    $('#login-username').value = chip.dataset.u;
    $('#login-password').value = chip.dataset.p;
  }));
}

function logout() {
  state.token = null;
  state.user = null;
  localStorage.removeItem('lic_token');
  localStorage.removeItem('lic_user');
  renderLogin();
}

/* ---------- 框架 ---------- */
const TABS_HQ = [
  ['matrix', '证照矩阵'], ['board', '到期看板'], ['renewals', '续证任务'],
  ['corrections', '材料补正'], ['reviews', '审核记录'], ['reminders', '提醒回执'], ['types', '类型配置'],
];
const TABS_STORE = [
  ['store', '我的门店'], ['board', '到期看板'], ['renewals', '续证任务'],
  ['corrections', '材料补正'], ['reviews', '审核记录'], ['reminders', '提醒回执'],
];

function renderApp() {
  const isHQ = state.user.role === 'hq_legal';
  const tabs = isHQ ? TABS_HQ : TABS_STORE;
  if (!state.tab || !tabs.some(([k]) => k === state.tab)) state.tab = tabs[0][0];
  app().innerHTML = `
    <div class="layout">
      <aside class="sidebar">
        <div class="brand"><h1>证照台账</h1><p>LICENSE LEDGER</p></div>
        <nav class="nav">
          ${tabs.map(([k, label]) => `
            <button class="nav-item${state.tab === k ? ' active' : ''}" data-tab="${k}">
              <span class="dot"></span><span class="txt">${label}</span>
            </button>`).join('')}
        </nav>
        <div class="sidebar-foot">
          <div class="who">${esc(state.user.display_name)}</div>
          <div class="role">${isHQ ? '总部法务' : '门店负责人'}</div>
          <button id="btn-logout">退出登录</button>
        </div>
      </aside>
      <main class="main" id="main"></main>
    </div>`;
  document.querySelectorAll('.nav-item').forEach((b) => b.addEventListener('click', () => {
    state.tab = b.dataset.tab;
    renderApp();
  }));
  $('#btn-logout').addEventListener('click', logout);
  renderTab();
}

function renderTab() {
  const main = $('#main');
  const renderers = {
    matrix: renderMatrix,
    store: renderStore,
    board: renderBoard,
    renewals: renderRenewals,
    corrections: renderCorrections,
    reviews: renderReviews,
    reminders: renderReminders,
    types: renderTypes,
  };
  main.innerHTML = '<div class="empty-state">加载中…</div>';
  run(renderers[state.tab](main));
}

/* ---------- 总部：证照矩阵 ---------- */
async function renderMatrix(main) {
  const { types, rows, today } = await api('/api/matrix');
  main.innerHTML = `
    <div class="page-head">
      <h2>证照矩阵</h2>
      <span class="sub">门店 × 证照 · 数据日期 ${today} · 点击单元格查看详情</span>
      <div class="spacer"></div>
      <button class="btn sm" id="btn-sweep">立即巡检提醒</button>
    </div>
    <div class="panel matrix-wrap">
      <table class="matrix">
        <thead><tr><th>门店</th>${types.map((t) => `<th>${esc(t.name)}<br><span style="font-weight:400;font-size:11px">提前 ${t.remind_days} 天提醒</span></th>`).join('')}</tr></thead>
        <tbody>
          ${rows.map(({ store, cells }) => `
            <tr>
              <td><div class="store-cell"><div class="name">${esc(store.name)}</div><div class="code">${esc(store.code)} · 负责人 ${esc(store.manager_name)}</div></div></td>
              ${types.map((t) => matrixCell(cells[t.id])).join('')}
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  $('#btn-sweep').addEventListener('click', () => run((async () => {
    const r = await api('/api/admin/sweep', { method: 'POST' });
    toast(`巡检完成：新建任务 ${r.tasks_created}，发送提醒 ${r.reminders_sent}，新标记过期 ${r.expired}`);
    renderTab();
  })()));
  main.querySelectorAll('.cell[data-lic]').forEach((c) => c.addEventListener('click', () => openLicenseModal(Number(c.dataset.lic))));
}

function matrixCell(cell) {
  if (!cell) return '<td><div class="cell empty">未登记</div></td>';
  const cls = ['cell'];
  if (cell.days_left !== null && cell.days_left < 0) cls.push('expired');
  else if (cell.recheck_required) cls.push('recheck');
  else if (cell.days_left !== null && cell.days_left <= cell.remind_days) cls.push('near');
  if (cell.pending_review) cls.push('reviewing');
  const daysHtml = cell.days_left === null
    ? '<div class="days muted" style="font-size:13px">无有效版本</div>'
    : cell.days_left < 0
      ? `<div class="days">−${-cell.days_left}<small>天</small></div><div class="meta">已于 ${esc(cell.expiry_date)} 到期</div>`
      : `<div class="days">${cell.days_left}<small>天</small></div><div class="meta">${esc(cell.expiry_date)} 到期</div>`;
  return `<td><div class="${cls.join(' ')}" data-lic="${cell.license_id}">
    ${daysHtml}
    ${cell.pending_review ? '<div class="meta">新版本待审核</div>' : ''}
    <div class="assignee">待办：<b>${esc(cell.assignee)}</b></div>
    ${cell.flags.length ? `<div class="flags">${flagPills(cell.flags)}</div>` : ''}
  </div></td>`;
}

/* ---------- 门店：我的门店 ---------- */
async function renderStore(main) {
  const [{ stores }, { licenses }] = await Promise.all([api('/api/stores'), api('/api/licenses')]);
  const store = stores[0];
  main.innerHTML = `
    <div class="page-head">
      <h2>${esc(store.name)}</h2>
      <span class="sub">${esc(store.code)}</span>
      <div class="spacer"></div>
      <button class="btn sm" id="btn-change">上报迁址 / 经营范围变更</button>
    </div>
    <div class="panel panel-pad">
      <div class="kv-list">
        <dt>门店地址</dt><dd>${esc(store.address)}</dd>
        <dt>经营范围</dt><dd>${esc(store.business_scope)}</dd>
        <dt>负责人</dt><dd>${esc(store.manager_name)}</dd>
      </div>
    </div>
    <div class="section-gap cards" id="lic-cards">
      ${licenses.map((lic) => `
        <div class="panel lic-card ${lic.status === 'expired' ? 'expired' : ''}">
          <div class="top">
            <span class="t">${esc(lic.type_name)}</span>
            ${statusBadge(lic)}
            ${lic.pending ? '<span class="badge review">审核中</span>' : ''}
          </div>
          <dl class="kv">
            <dt>证照编号</dt><dd class="mono">${esc(lic.cert_no || '—')}</dd>
            <dt>有效期至</dt><dd class="mono">${esc(lic.expiry_date || '—')}（${lic.days_left === null ? '—' : lic.days_left < 0 ? `已过期 ${-lic.days_left} 天` : `剩余 ${lic.days_left} 天`}）</dd>
            <dt>责任人</dt><dd>${esc(lic.responsible_person || '—')}</dd>
          </dl>
          <div class="flags">${flagPills(lic.flags || [])}</div>
          <div class="actions">
            <button class="btn sm" data-act="detail" data-id="${lic.id}">详情</button>
            <button class="btn sm primary" data-act="submit" data-id="${lic.id}">提交续证</button>
            <button class="btn sm" data-act="use" data-id="${lic.id}">出示核验</button>
            <button class="btn sm ghost" data-act="lost" data-id="${lic.id}">登记遗失</button>
          </div>
        </div>`).join('')}
    </div>`;
  $('#btn-change').addEventListener('click', () => openStoreChangeModal(store));
  $('#lic-cards').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (btn.dataset.act === 'detail') openLicenseModal(id);
    if (btn.dataset.act === 'submit') openSubmitModal(id);
    if (btn.dataset.act === 'lost') run((async () => {
      await api(`/api/licenses/${id}/mark-lost`, { method: 'POST' });
      toast('已登记原件遗失');
      renderTab();
    })());
    if (btn.dataset.act === 'use') run((async () => {
      const r = await api(`/api/licenses/${id}/use`, { method: 'POST' });
      toast(`核验通过：${r.type} ${r.cert_no}，有效期至 ${r.valid_until}`);
    })());
  });
}

/* ---------- 到期看板 ---------- */
async function renderBoard(main) {
  const { items, today } = await api('/api/dashboard/expiry');
  const maxWindow = 120;
  main.innerHTML = `
    <div class="page-head"><h2>到期看板</h2><span class="sub">按剩余天数升序 · 数据日期 ${today}</span></div>
    <div class="panel">
      ${items.length ? items.map((it) => {
        const expired = it.days_left < 0;
        const pct = expired ? 100 : Math.max(2, Math.min(100, 100 - (it.days_left / maxWindow) * 100));
        return `<div class="board-row${expired ? ' expired' : ''}" data-lic="${it.license_id}" style="cursor:pointer">
          <div class="lic">
            <div class="t">${esc(it.type_name)} ${it.recheck_required ? '<span class="badge warn">待核查</span>' : ''}</div>
            <div class="s">${esc(it.store_name)} · ${esc(it.cert_no)}</div>
          </div>
          <div class="bar-wrap"><div class="bar"><i style="width:${pct}%"></i></div></div>
          <div class="days">${expired ? `<b>过期 ${-it.days_left}</b> 天` : `<b>${it.days_left}</b> 天`}</div>
          <div class="who">${esc(it.manager_name)}</div>
        </div>`;
      }).join('') : '<div class="empty-state">暂无证照数据</div>'}
    </div>`;
  main.querySelectorAll('.board-row').forEach((r) => r.addEventListener('click', () => openLicenseModal(Number(r.dataset.lic))));
}

/* ---------- 续证任务 ---------- */
async function renderRenewals(main) {
  const { renewals } = await api('/api/renewals');
  const isHQ = state.user.role === 'hq_legal';
  main.innerHTML = `
    <div class="page-head"><h2>续证任务</h2><span class="sub">门店上传续证材料并登记受理号，总部审核后生效</span></div>
    <div class="panel">
      <table class="tbl">
        <thead><tr>${isHQ ? '<th>门店</th>' : ''}<th>证照</th><th>到期日</th><th>状态</th><th>受理号</th><th>备注</th><th>操作</th></tr></thead>
        <tbody>
          ${renewals.length ? renewals.map((t) => `
            <tr>
              ${isHQ ? `<td>${esc(t.store_name)}</td>` : ''}
              <td>${esc(t.type_name)}</td>
              <td class="num">${esc(t.due_date)}</td>
              <td><span class="badge ${t.status === 'overdue' ? 'expired' : t.status === 'completed' ? 'ok' : t.status === 'submitted' ? 'review' : 'warn'}">${TASK_STATUS[t.status] || t.status}</span></td>
              <td class="mono">${esc(t.acceptance_no || '—')}</td>
              <td class="muted">${esc(t.note || '')}</td>
              <td><button class="btn sm" data-lic="${t.license_id}">查看证照</button></td>
            </tr>`).join('') : `<tr><td colspan="7"><div class="empty-state">暂无续证任务</div></td></tr>`}
        </tbody>
      </table>
    </div>`;
  main.querySelectorAll('button[data-lic]').forEach((b) => b.addEventListener('click', () => openLicenseModal(Number(b.dataset.lic))));
}

/* ---------- 材料补正 ---------- */
async function renderCorrections(main) {
  const { corrections } = await api('/api/corrections');
  const isHQ = state.user.role === 'hq_legal';
  main.innerHTML = `
    <div class="page-head"><h2>材料补正</h2><span class="sub">总部要求补正的材料及处理进度</span></div>
    <div class="panel">
      <table class="tbl">
        <thead><tr>${isHQ ? '<th>门店</th>' : ''}<th>证照</th><th>补正原因</th><th>要求人</th><th>状态</th><th>时间</th><th>操作</th></tr></thead>
        <tbody>
          ${corrections.length ? corrections.map((c) => `
            <tr>
              ${isHQ ? `<td>${esc(c.store_name)}</td>` : ''}
              <td>${esc(c.type_name)}</td>
              <td>${esc(c.reason)}</td>
              <td>${esc(c.requested_by_name)}</td>
              <td><span class="badge ${c.status === 'resolved' ? 'ok' : 'warn'}">${c.status === 'resolved' ? '已补正' : '待补正'}</span></td>
              <td class="num muted">${fmtTime(c.created_at)}</td>
              <td>${c.status === 'open' ? `<button class="btn sm" data-resolve="${c.id}">标记已补正</button>` : ''}
                  <button class="btn sm ghost" data-lic="${c.license_id}">证照</button></td>
            </tr>`).join('') : `<tr><td colspan="7"><div class="empty-state">暂无补正记录</div></td></tr>`}
        </tbody>
      </table>
    </div>`;
  main.querySelectorAll('button[data-resolve]').forEach((b) => b.addEventListener('click', () => run((async () => {
    await api(`/api/corrections/${b.dataset.resolve}/resolve`, { method: 'POST' });
    toast('已标记补正完成');
    renderTab();
  })())));
  main.querySelectorAll('button[data-lic]').forEach((b) => b.addEventListener('click', () => openLicenseModal(Number(b.dataset.lic))));
}

/* ---------- 审核记录 ---------- */
async function renderReviews(main) {
  const { reviews } = await api('/api/reviews');
  main.innerHTML = `
    <div class="page-head"><h2>审核记录</h2><span class="sub">完整审核链（提交 / 通过 / 驳回 / 补正 / 标记）</span></div>
    <div class="panel">
      <table class="tbl">
        <thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>说明</th></tr></thead>
        <tbody>
          ${reviews.length ? reviews.map((r) => `
            <tr>
              <td class="num muted" style="white-space:nowrap">${fmtTime(r.created_at)}</td>
              <td>${esc(r.actor_name)}</td>
              <td><span class="badge ${r.action === 'approve' ? 'ok' : r.action === 'reject' ? 'expired' : 'review'}">${REVIEW_ACTION[r.action] || esc(r.action)}</span></td>
              <td>${esc(r.comment || '—')}</td>
            </tr>`).join('') : '<tr><td colspan="4"><div class="empty-state">暂无审核记录</div></td></tr>'}
        </tbody>
      </table>
    </div>`;
}

/* ---------- 提醒回执 ---------- */
async function renderReminders(main) {
  const { reminders } = await api('/api/reminders');
  const isHQ = state.user.role === 'hq_legal';
  main.innerHTML = `
    <div class="page-head"><h2>提醒回执</h2><span class="sub">进程内提醒引擎发送记录 · 模拟适配器送达回执</span></div>
    <div class="panel">
      <table class="tbl">
        <thead><tr><th>发送时间</th>${isHQ ? '<th>门店</th>' : ''}<th>证照</th><th>接收人</th><th>内容</th><th>渠道</th><th>回执号</th><th>状态</th></tr></thead>
        <tbody>
          ${reminders.length ? reminders.map((r) => `
            <tr>
              <td class="num muted" style="white-space:nowrap">${fmtTime(r.sent_at)}</td>
              ${isHQ ? `<td>${esc(r.store_name)}</td>` : ''}
              <td>${esc(r.type_name)}</td>
              <td>${esc(r.recipient)}</td>
              <td style="max-width:340px">${esc(r.payload)}</td>
              <td class="muted">${esc(r.channel)}</td>
              <td class="mono">${esc(r.receipt_no || '—')}</td>
              <td><span class="badge ${r.delivery_status === 'delivered' ? 'ok' : 'expired'}">${r.delivery_status === 'delivered' ? '已送达' : '失败'}</span></td>
            </tr>`).join('') : '<tr><td colspan="8"><div class="empty-state">暂无提醒记录</div></td></tr>'}
        </tbody>
      </table>
    </div>`;
}

/* ---------- 类型配置（总部） ---------- */
async function renderTypes(main) {
  const { types } = await api('/api/license-types');
  main.innerHTML = `
    <div class="page-head"><h2>证照类型配置</h2><span class="sub">总部法务配置各类证照的提醒提前量</span></div>
    <div class="panel">
      <table class="tbl">
        <thead><tr><th>类型编码</th><th>证照名称</th><th>提醒提前量（天）</th><th>操作</th></tr></thead>
        <tbody>
          ${types.map((t) => `
            <tr>
              <td class="mono muted">${esc(t.code)}</td>
              <td>${esc(t.name)}</td>
              <td><input type="number" min="1" max="365" value="${t.remind_days}" data-days="${t.id}" style="width:90px;padding:5px 8px;border:1px solid var(--line-strong);border-radius:5px"></td>
              <td><button class="btn sm primary" data-save="${t.id}">保存</button></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  main.querySelectorAll('button[data-save]').forEach((b) => b.addEventListener('click', () => run((async () => {
    const days = Number(main.querySelector(`input[data-days="${b.dataset.save}"]`).value);
    await api(`/api/license-types/${b.dataset.save}`, { method: 'PUT', body: { remind_days: days } });
    toast(`提醒提前量已调整为 ${days} 天`);
  })())));
}

/* ---------- 弹窗基础设施 ---------- */
function openModal(html, { narrow = false } = {}) {
  const root = $('#modal-root');
  root.innerHTML = `
    <div class="modal-mask">
      <div class="modal${narrow ? ' narrow' : ''}">${html}</div>
    </div>`;
  const close = () => { root.innerHTML = ''; };
  root.querySelector('.modal-mask').addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-mask')) close();
  });
  root.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', close));
  return close;
}

/* ---------- 证照详情弹窗 ---------- */
async function openLicenseModal(licenseId) {
  try {
    const d = await api(`/api/licenses/${licenseId}`);
    const isHQ = state.user.role === 'hq_legal';
    const current = d.versions.find((v) => v.id === d.license.current_version_id);
    const shown = d.versions.find((v) => v.status === 'pending_review') || current || d.versions[0];
    const openFlags = d.flags.filter((f) => f.status === 'open');

    const close = openModal(`
      <div class="modal-head">
        <h3>${esc(d.license.store_name)} · ${esc(d.license.type_name)}</h3>
        ${statusBadge(d.license)}
        <button class="x" data-close>✕</button>
      </div>
      <div class="modal-body">
        <div class="lic-detail">
          <div class="scan-box">
            ${shown && shown.scan_path
              ? `<img src="${esc(shown.scan_path)}" width="${shown.scan_width || ''}" height="${shown.scan_height || ''}" alt="证照扫描件">`
              : '<div class="empty-state">暂无扫描件</div>'}
          </div>
          <div class="detail-side">
            <h4>证照信息${shown && current && shown.id !== current.id ? '（预览待审版本）' : '（当前有效版本）'}</h4>
            ${shown ? `
            <dl class="kv-list">
              <dt>证照编号</dt><dd class="mono">${esc(shown.cert_no)}</dd>
              <dt>有效期</dt><dd class="mono">${esc(shown.issue_date)} 至 ${esc(shown.expiry_date)}</dd>
              <dt>登记地址</dt><dd>${esc(shown.address)}</dd>
              <dt>经营范围</dt><dd>${esc(shown.scope)}</dd>
              <dt>责任人</dt><dd>${esc(shown.responsible_person)}</dd>
              <dt>受理号</dt><dd class="mono">${esc(shown.acceptance_no || '—')}</dd>
            </dl>` : '<div class="muted">暂无版本</div>'}
            <div class="row-flex" style="margin-top:8px">${daysLabel(d.days_left)}${flagPills(openFlags)}</div>

            <h4>版本记录（总部审核通过后才成为当前有效版本）</h4>
            ${d.versions.map((v) => `
              <div class="ver-item ${v.status === 'current' ? 'current' : v.status === 'pending_review' ? 'pending' : ''}">
                <div class="row1">
                  <b>第 ${v.version_no} 版</b>
                  <span class="badge ${v.status === 'current' ? 'ok' : v.status === 'rejected' ? 'expired' : v.status === 'pending_review' ? 'review' : ''}">${VERSION_STATUS[v.status]}</span>
                  ${v.is_original_lost ? '<span class="flag-pill">原件遗失</span>' : ''}
                  ${v.is_scan_blurry ? '<span class="flag-pill">扫描件模糊</span>' : ''}
                </div>
                <div class="row2 mono">${esc(v.cert_no)} · ${esc(v.issue_date)} ~ ${esc(v.expiry_date)}</div>
                <div class="row2">提交：${esc(v.submitted_by_name)} ${fmtTime(v.submitted_at)}${v.reviewed_by_name ? ` · 审核：${esc(v.reviewed_by_name)} ${fmtTime(v.reviewed_at)}` : ''}</div>
                ${v.review_comment ? `<div class="row2">审核意见：${esc(v.review_comment)}</div>` : ''}
                <div class="ops">
                  ${v.scan_path ? `<button class="btn sm ghost" data-scan="${v.id}">查看扫描件</button>` : ''}
                  ${isHQ && v.status === 'pending_review' ? `
                    <button class="btn sm primary" data-approve="${v.id}">审核通过</button>
                    <button class="btn sm" data-reject="${v.id}">驳回</button>
                    <button class="btn sm" data-correct="${v.id}">要求补正</button>
                    <button class="btn sm ghost" data-blurry="${v.id}">标记模糊</button>` : ''}
                </div>
              </div>`).join('')}

            <h4>审核链</h4>
            <div class="timeline">
              ${d.reviews.length ? d.reviews.map((r) => `
                <div class="ev">
                  <span class="who">${esc(r.actor_name)}</span>
                  <span>${REVIEW_ACTION[r.action] || esc(r.action)}</span>
                  <span class="when">${fmtTime(r.created_at)}</span>
                  ${r.comment ? `<div class="cmt">${esc(r.comment)}</div>` : ''}
                </div>`).join('') : '<div class="muted">暂无记录</div>'}
            </div>
          </div>
        </div>
      </div>`);

    const root = $('#modal-root');
    root.querySelectorAll('[data-scan]').forEach((b) => b.addEventListener('click', () => {
      const v = d.versions.find((x) => x.id === Number(b.dataset.scan));
      const img = root.querySelector('.scan-box img');
      if (img && v) { img.src = v.scan_path; img.width = v.scan_width || 794; img.height = v.scan_height || 1123; }
    }));
    root.querySelectorAll('[data-approve]').forEach((b) => b.addEventListener('click', () => run((async () => {
      await api(`/api/versions/${b.dataset.approve}/review`, { method: 'POST', body: { action: 'approve', comment: '材料齐全，同意换发' } });
      toast('已通过，新版本成为当前有效版本');
      close(); renderTab();
    })())));
    root.querySelectorAll('[data-reject]').forEach((b) => b.addEventListener('click', () => {
      const comment = prompt('驳回原因（将通知门店补正重报）：', '材料不全');
      if (comment === null) return;
      run((async () => {
        await api(`/api/versions/${b.dataset.reject}/review`, { method: 'POST', body: { action: 'reject', comment } });
        toast('已驳回');
        close(); renderTab();
      })());
    }));
    root.querySelectorAll('[data-correct]').forEach((b) => b.addEventListener('click', () => {
      const reason = prompt('补正要求：', '请补充材料');
      if (!reason) return;
      run((async () => {
        await api(`/api/versions/${b.dataset.correct}/corrections`, { method: 'POST', body: { reason } });
        toast('已登记补正要求');
        close(); renderTab();
      })());
    }));
    root.querySelectorAll('[data-blurry]').forEach((b) => b.addEventListener('click', () => run((async () => {
      await api(`/api/versions/${b.dataset.blurry}/mark-blurry`, { method: 'POST' });
      toast('已标记扫描件模糊');
      close(); renderTab();
    })())));
  } catch (e) {
    toast(e.message, 'error');
  }
}

/* ---------- 提交续证材料弹窗（门店） ---------- */
async function openSubmitModal(licenseId) {
  try {
    const d = await api(`/api/licenses/${licenseId}`);
    const lic = d.license;
    const current = d.versions.find((v) => v.id === lic.current_version_id);
    const close = openModal(`
      <div class="modal-head"><h3>提交续证材料 · ${esc(lic.type_name)}</h3><button class="x" data-close>✕</button></div>
      <div class="modal-body">
        <form id="submit-form" class="form-grid">
          <div class="field"><label>证照编号 *</label><input name="cert_no" required value="${esc(current?.cert_no || '')}"></div>
          <div class="field"><label>受理号（续证受理回执编号）</label><input name="acceptance_no" placeholder="如 SL2026-0501"></div>
          <div class="field"><label>发证日期 *</label><input name="issue_date" type="date" required></div>
          <div class="field"><label>有效期至 *</label><input name="expiry_date" type="date" required></div>
          <div class="field full"><label>登记地址 *（迁址门店须填写新地址）</label><input name="address" required value="${esc(lic.store_address)}"></div>
          <div class="field full"><label>经营范围 *</label><input name="scope" required value="${esc(lic.store_scope)}"></div>
          <div class="field"><label>责任人 *</label><input name="responsible_person" required value="${esc(current?.responsible_person || lic.manager_name)}"></div>
          <div class="field"><label>扫描件（可选，不上传则按信息生成）</label><input name="scan" type="file" accept="image/*"></div>
          <div class="form-actions full">
            <button type="button" class="btn" data-close>取消</button>
            <button type="submit" class="btn primary">提交总部审核</button>
          </div>
        </form>
      </div>`, { narrow: true });

    $('#submit-form').addEventListener('submit', (e) => {
      e.preventDefault();
      run((async () => {
        const fd = new FormData(e.target);
        const body = {
          cert_no: fd.get('cert_no'),
          acceptance_no: fd.get('acceptance_no') || undefined,
          issue_date: fd.get('issue_date'),
          expiry_date: fd.get('expiry_date'),
          address: fd.get('address'),
          scope: fd.get('scope'),
          responsible_person: fd.get('responsible_person'),
        };
        const file = fd.get('scan');
        if (file && file.size) {
          const buf = await file.arrayBuffer();
          let bin = '';
          new Uint8Array(buf).forEach((byte) => { bin += String.fromCharCode(byte); });
          body.scan_data = btoa(bin);
          body.scan_mime = file.type || 'image/png';
        }
        await api(`/api/licenses/${licenseId}/versions`, { method: 'POST', body });
        toast('已提交，待总部审核');
        close();
        renderTab();
      })());
    });
  } catch (e) {
    toast(e.message, 'error');
  }
}

/* ---------- 门店变更弹窗 ---------- */
function openStoreChangeModal(store) {
  const close = openModal(`
    <div class="modal-head"><h3>上报变更 · ${esc(store.name)}</h3><button class="x" data-close>✕</button></div>
    <div class="modal-body">
      <p class="muted" style="margin-bottom:14px">迁址或经营范围变更后，全店证照将进入「待核查」状态，须重新提交核查材料，不能沿用旧证。</p>
      <form id="change-form" class="form-grid">
        <div class="field full"><label>变更类型</label>
          <select name="change_type">
            <option value="address">门店迁址</option>
            <option value="scope">经营范围变更</option>
          </select>
        </div>
        <div class="field full"><label>当前值</label><input id="change-old" disabled value="${esc(store.address)}"></div>
        <div class="field full"><label>变更后 *</label><input name="new_value" required></div>
        <div class="form-actions full">
          <button type="button" class="btn" data-close>取消</button>
          <button type="submit" class="btn primary">提交变更</button>
        </div>
      </form>
    </div>`, { narrow: true });
  const sel = $('#change-form select[name="change_type"]');
  sel.addEventListener('change', () => {
    $('#change-old').value = sel.value === 'address' ? store.address : store.business_scope;
  });
  $('#change-form').addEventListener('submit', (e) => {
    e.preventDefault();
    run((async () => {
      const fd = new FormData(e.target);
      await api(`/api/stores/${store.id}/changes`, {
        method: 'POST',
        body: { change_type: fd.get('change_type'), new_value: fd.get('new_value') },
      });
      toast('变更已提交，全店证照进入待核查状态');
      close();
      renderTab();
    })());
  });
}

/* ---------- 启动 ---------- */
(async function boot() {
  if (!state.token) return renderLogin();
  try {
    const { user } = await api('/api/me');
    state.user = user;
    renderApp();
  } catch {
    logout();
  }
})();

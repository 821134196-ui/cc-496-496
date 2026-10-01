'use strict';
const path = require('node:path');
const fs = require('node:fs');
const express = require('express');
const { tx } = require('./db');
const {
  todayStr, daysBetween, nowIso,
  ApiError, badRequest, forbidden, notFound, conflict,
} = require('./util');
const { attachAuth, requireAuth, requireHQ, assertStoreAccess, login } = require('./auth');
const { raiseFlag, resolveFlags, FLAG_LABELS } = require('./flags');
const { runSweep, openTask } = require('./reminders');
const { scanSvg } = require('./scansvg');

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const OPEN_TASK_STATUSES = "('pending','submitted','rejected','correcting','overdue')";

/**
 * 应用工厂：注入 db / 扫描件目录 / 通知适配器，便于测试用临时目录起真实服务。
 */
function createApp({ db, scansDir, notifier }) {
  const app = express();
  app.use(express.json({ limit: '12mb' }));
  app.use(attachAuth(db));

  const h = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);

  // ---------- 通用查询助手 ----------
  const getLicense = (id) => {
    const lic = db.prepare(
      `SELECT l.*, s.name AS store_name, s.address AS store_address, s.business_scope AS store_scope,
              s.manager_name, t.name AS type_name, t.remind_days
       FROM licenses l
       JOIN stores s ON s.id = l.store_id
       JOIN license_types t ON t.id = l.type_id
       WHERE l.id = ?`,
    ).get(id);
    if (!lic) throw notFound('证照不存在');
    return lic;
  };
  const getVersion = (id) => {
    const v = db.prepare('SELECT * FROM license_versions WHERE id = ?').get(id);
    if (!v) throw notFound('证照版本不存在');
    return v;
  };
  const addReview = (entityType, entityId, action, actorId, comment = null) => {
    db.prepare('INSERT INTO reviews (entity_type, entity_id, action, actor_id, comment) VALUES (?,?,?,?,?)')
      .run(entityType, entityId, action, actorId, comment);
  };

  /** 保存扫描件：上传 base64 或按证照信息生成占位扫描件；返回路径与原始宽高 */
  function saveScan(licenseId, versionNo, fields) {
    fs.mkdirSync(scansDir, { recursive: true });
    if (fields.scan_data) {
      const mime = fields.scan_mime || 'image/svg+xml';
      const ext = mime === 'image/png' ? 'png' : mime === 'image/jpeg' ? 'jpg' : 'svg';
      const file = `lic${licenseId}-v${versionNo}-${Date.now()}.${ext}`;
      fs.writeFileSync(path.join(scansDir, file), Buffer.from(String(fields.scan_data), 'base64'));
      return {
        scan_path: `/scans/${file}`,
        scan_width: Number(fields.scan_width) || 794,
        scan_height: Number(fields.scan_height) || 1123,
      };
    }
    const w = 794; const hgt = 1123;
    const file = `lic${licenseId}-v${versionNo}-${Date.now()}.svg`;
    fs.writeFileSync(path.join(scansDir, file), scanSvg({
      title: fields.title,
      certNo: fields.cert_no,
      storeName: fields.storeName,
      address: fields.address,
      scope: fields.scope,
      person: fields.responsible_person,
      issue: fields.issue_date,
      expiry: fields.expiry_date,
      width: w,
      height: hgt,
    }));
    return { scan_path: `/scans/${file}`, scan_width: w, scan_height: hgt };
  }

  // ---------- 认证 ----------
  app.post('/api/login', h(async (req, res) => {
    const { username, password } = req.body || {};
    if (!username || !password) throw badRequest('请输入用户名和密码');
    res.json(login(db, username, password));
  }));

  app.get('/api/me', requireAuth, (req, res) => res.json({ user: req.user }));

  // ---------- 门店 ----------
  app.get('/api/stores', requireAuth, h(async (req, res) => {
    const rows = req.user.role === 'hq_legal'
      ? db.prepare('SELECT * FROM stores ORDER BY id').all()
      : db.prepare('SELECT * FROM stores WHERE id = ?').all(req.user.store_id);
    res.json({ stores: rows });
  }));

  /** 迁址 / 经营范围变更：触发全店证照重新核查，不得沿用旧证 */
  app.post('/api/stores/:id/changes', requireAuth, h(async (req, res) => {
    const store = db.prepare('SELECT * FROM stores WHERE id = ?').get(Number(req.params.id));
    if (!store) throw notFound('门店不存在');
    assertStoreAccess(req.user, store.id);
    const { change_type, new_value } = req.body || {};
    if (!['address', 'scope'].includes(change_type)) throw badRequest('变更类型须为 address 或 scope');
    if (!new_value || !String(new_value).trim()) throw badRequest('请填写变更后的内容');
    const changeId = tx(db, () => {
      const oldValue = change_type === 'address' ? store.address : store.business_scope;
      if (oldValue === String(new_value).trim()) throw badRequest('变更内容与原值相同');
      const id = db.prepare(
        'INSERT INTO store_changes (store_id, change_type, old_value, new_value, created_by) VALUES (?,?,?,?,?)',
      ).run(store.id, change_type, oldValue, String(new_value).trim(), req.user.id).lastInsertRowid;
      db.prepare(change_type === 'address'
        ? 'UPDATE stores SET address = ? WHERE id = ?'
        : 'UPDATE stores SET business_scope = ? WHERE id = ?').run(String(new_value).trim(), store.id);
      const licenses = db.prepare('SELECT id FROM licenses WHERE store_id = ?').all(store.id);
      for (const lic of licenses) {
        db.prepare('UPDATE licenses SET recheck_required = 1 WHERE id = ?').run(lic.id);
        raiseFlag(db, {
          license_id: lic.id,
          flag_type: 'recheck_required',
          message: `门店${change_type === 'address' ? '迁址' : '经营范围变更'}，证照须重新核查，不能沿用旧证`,
        });
      }
      addReview('store_change', id, 'submit', req.user.id,
        `门店${change_type === 'address' ? '地址' : '经营范围'}变更：${oldValue} → ${String(new_value).trim()}`);
      return id;
    });
    res.status(201).json({ change_id: changeId });
  }));

  app.get('/api/store-changes', requireAuth, h(async (req, res) => {
    const base = `SELECT sc.*, s.name AS store_name, u.display_name AS created_by_name
                  FROM store_changes sc
                  JOIN stores s ON s.id = sc.store_id
                  JOIN users u ON u.id = sc.created_by`;
    const rows = req.user.role === 'hq_legal'
      ? db.prepare(`${base} ORDER BY sc.id DESC`).all()
      : db.prepare(`${base} WHERE sc.store_id = ? ORDER BY sc.id DESC`).all(req.user.store_id);
    res.json({ changes: rows });
  }));

  // ---------- 证照类型（总部法务配置提醒提前量） ----------
  app.get('/api/license-types', requireAuth, (req, res) => {
    res.json({ types: db.prepare('SELECT * FROM license_types ORDER BY id').all() });
  });

  app.put('/api/license-types/:id', requireHQ, h(async (req, res) => {
    const t = db.prepare('SELECT * FROM license_types WHERE id = ?').get(Number(req.params.id));
    if (!t) throw notFound('证照类型不存在');
    const days = Number(req.body?.remind_days);
    if (!Number.isInteger(days) || days < 1 || days > 365) throw badRequest('提醒提前量须为 1-365 的整数天数');
    db.prepare('UPDATE license_types SET remind_days = ? WHERE id = ?').run(days, t.id);
    addReview('license_type', t.id, 'config', req.user.id, `提醒提前量调整为 ${days} 天`);
    res.json({ ok: true, remind_days: days });
  }));

  // ---------- 总部证照矩阵：门店行 × 证照列 ----------
  app.get('/api/matrix', requireHQ, h(async (req, res) => {
    const today = todayStr();
    const types = db.prepare('SELECT * FROM license_types ORDER BY id').all();
    const stores = db.prepare('SELECT * FROM stores ORDER BY id').all();
    const licenses = db.prepare(
      `SELECT l.*, v.cert_no, v.expiry_date,
              (SELECT COUNT(*) FROM license_versions pv
               WHERE pv.license_id = l.id AND pv.status = 'pending_review') AS pending_count
       FROM licenses l
       LEFT JOIN license_versions v ON v.id = l.current_version_id`,
    ).all();
    const openTasks = db.prepare(`SELECT * FROM renewal_tasks WHERE status IN ${OPEN_TASK_STATUSES}`).all();
    const openFlags = db.prepare("SELECT * FROM flags WHERE status = 'open'").all();
    const taskByLic = {};
    for (const t of openTasks) taskByLic[t.license_id] = t;
    const flagsByLic = {};
    for (const f of openFlags) (flagsByLic[f.license_id] ??= []).push(f);

    const rows = stores.map((s) => {
      const cells = {};
      for (const t of types) {
        const lic = licenses.find((l) => l.store_id === s.id && l.type_id === t.id);
        if (!lic) { cells[t.id] = null; continue; }
        const daysLeft = lic.expiry_date ? daysBetween(today, lic.expiry_date) : null;
        const task = taskByLic[lic.id];
        let assignee = '—';
        if (lic.pending_count > 0) assignee = '总部法务';
        else if (task || (daysLeft !== null && daysLeft < 0)) assignee = s.manager_name;
        cells[t.id] = {
          license_id: lic.id,
          status: lic.status,
          recheck_required: lic.recheck_required,
          cert_no: lic.cert_no,
          expiry_date: lic.expiry_date,
          days_left: daysLeft,
          remind_days: t.remind_days,
          pending_review: lic.pending_count > 0,
          assignee,
          flags: (flagsByLic[lic.id] || []).map((f) => ({ id: f.id, type: f.flag_type, label: FLAG_LABELS[f.flag_type] })),
        };
      }
      return { store: s, cells };
    });
    res.json({ types, rows, today });
  }));

  // ---------- 证照 ----------
  app.get('/api/licenses', requireAuth, h(async (req, res) => {
    let storeId = req.query.store_id ? Number(req.query.store_id) : null;
    if (req.user.role !== 'hq_legal') {
      if (storeId && storeId !== req.user.store_id) throw forbidden();
      storeId = req.user.store_id;
    }
    const today = todayStr();
    const base = `SELECT l.*, s.name AS store_name, s.manager_name, t.name AS type_name, t.remind_days,
                         v.cert_no, v.expiry_date, v.responsible_person,
                         (SELECT COUNT(*) FROM license_versions pv
                          WHERE pv.license_id = l.id AND pv.status = 'pending_review') AS pending_count
                  FROM licenses l
                  JOIN stores s ON s.id = l.store_id
                  JOIN license_types t ON t.id = l.type_id
                  LEFT JOIN license_versions v ON v.id = l.current_version_id`;
    const rows = storeId
      ? db.prepare(`${base} WHERE l.store_id = ? ORDER BY l.id`).all(storeId)
      : db.prepare(`${base} ORDER BY l.store_id, l.id`).all();
    const flagStmt = db.prepare("SELECT flag_type FROM flags WHERE license_id = ? AND status = 'open'");
    for (const r of rows) {
      r.days_left = r.expiry_date ? daysBetween(today, r.expiry_date) : null;
      r.pending = r.pending_count > 0;
      r.flags = flagStmt.all(r.id).map((f) => ({ type: f.flag_type, label: FLAG_LABELS[f.flag_type] }));
    }
    res.json({ licenses: rows, today });
  }));

  app.get('/api/licenses/:id', requireAuth, h(async (req, res) => {
    const lic = getLicense(Number(req.params.id));
    assertStoreAccess(req.user, lic.store_id);
    const versions = db.prepare(
      `SELECT v.*, u.display_name AS submitted_by_name, r.display_name AS reviewed_by_name
       FROM license_versions v
       JOIN users u ON u.id = v.submitted_by
       LEFT JOIN users r ON r.id = v.reviewed_by
       WHERE v.license_id = ? ORDER BY v.version_no DESC`,
    ).all(lic.id);
    const flags = db.prepare('SELECT * FROM flags WHERE license_id = ? ORDER BY id DESC').all(lic.id);
    const tasks = db.prepare('SELECT * FROM renewal_tasks WHERE license_id = ? ORDER BY id DESC').all(lic.id);
    const reviews = db.prepare(
      `SELECT rv.*, u.display_name AS actor_name FROM reviews rv
       JOIN users u ON u.id = rv.actor_id
       WHERE (rv.entity_type = 'license_version' AND rv.entity_id IN
                (SELECT id FROM license_versions WHERE license_id = ?))
          OR (rv.entity_type = 'renewal_task' AND rv.entity_id IN
                (SELECT id FROM renewal_tasks WHERE license_id = ?))
       ORDER BY rv.id DESC`,
    ).all(lic.id, lic.id);
    const today = todayStr();
    const current = lic.current_version_id ? getVersion(lic.current_version_id) : null;
    res.json({
      license: lic,
      versions,
      flags,
      tasks,
      reviews,
      today,
      days_left: current ? daysBetween(today, current.expiry_date) : null,
    });
  }));

  /** 门店上传续证材料（新版本 + 受理号），进入待审核；总部通过前不影响当前有效版本 */
  app.post('/api/licenses/:id/versions', requireAuth, h(async (req, res) => {
    const lic = getLicense(Number(req.params.id));
    assertStoreAccess(req.user, lic.store_id);
    const b = req.body || {};
    for (const k of ['cert_no', 'issue_date', 'expiry_date', 'address', 'scope', 'responsible_person']) {
      if (!b[k] || !String(b[k]).trim()) throw badRequest(`缺少必填字段：${k}`);
    }
    if (!DATE_RE.test(b.issue_date) || !DATE_RE.test(b.expiry_date)) throw badRequest('日期格式须为 YYYY-MM-DD');
    if (daysBetween(b.issue_date, b.expiry_date) <= 0) throw badRequest('有效期止须晚于发证日期');

    const versionId = tx(db, () => {
      const inflight = db.prepare(
        "SELECT id FROM license_versions WHERE license_id = ? AND status = 'pending_review'",
      ).get(lic.id);
      if (inflight) throw conflict('VERSION_INFLIGHT', '已有待审核版本，请等待总部审核后再提交');
      const versionNo = (db.prepare('SELECT MAX(version_no) AS m FROM license_versions WHERE license_id = ?')
        .get(lic.id).m || 0) + 1;
      const scan = saveScan(lic.id, versionNo, { ...b, title: lic.type_name, storeName: lic.store_name });
      const vid = db.prepare(
        `INSERT INTO license_versions
           (license_id, version_no, cert_no, issue_date, expiry_date, address, scope, responsible_person,
            scan_path, scan_width, scan_height, status, acceptance_no, submitted_by)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending_review',?,?)`,
      ).run(
        lic.id, versionNo, String(b.cert_no).trim(), b.issue_date, b.expiry_date,
        String(b.address).trim(), String(b.scope).trim(), String(b.responsible_person).trim(),
        scan.scan_path, scan.scan_width, scan.scan_height,
        b.acceptance_no ? String(b.acceptance_no).trim() : null, req.user.id,
      ).lastInsertRowid;

      const task = openTask(db, lic.id);
      if (task) {
        db.prepare(
          "UPDATE renewal_tasks SET version_id = ?, acceptance_no = ?, status = 'submitted', updated_at = datetime('now') WHERE id = ?",
        ).run(vid, b.acceptance_no ? String(b.acceptance_no).trim() : null, task.id);
        db.prepare(
          "UPDATE corrections SET status = 'resolved', resolved_at = datetime('now') WHERE renewal_task_id = ? AND status = 'open'",
        ).run(task.id);
      } else {
        db.prepare(
          "INSERT INTO renewal_tasks (license_id, version_id, due_date, status, acceptance_no, note) VALUES (?,?,?,'submitted',?,?)",
        ).run(lic.id, vid, b.expiry_date, b.acceptance_no ? String(b.acceptance_no).trim() : null, '门店提交续证材料');
      }
      resolveFlags(db, lic.id, ['acceptance_rejected', 'overdue_unsubmitted']);
      addReview('license_version', vid, 'submit', req.user.id,
        `提交${lic.type_name}第 ${versionNo} 版续证材料${b.acceptance_no ? `，受理号 ${b.acceptance_no}` : ''}`);
      return vid;
    });
    res.status(201).json({ version_id: versionId });
  }));

  /** 出示 / 核验证照：过期与待核查一律拦截 */
  app.post('/api/licenses/:id/use', requireAuth, h(async (req, res) => {
    const lic = getLicense(Number(req.params.id));
    assertStoreAccess(req.user, lic.store_id);
    if (!lic.current_version_id) throw conflict('NO_CURRENT_VERSION', '该证照暂无有效版本');
    const cur = getVersion(lic.current_version_id);
    if (lic.recheck_required) {
      throw conflict('RECHECK_REQUIRED', '门店迁址或经营范围变更，证照须重新核查，不能沿用旧证');
    }
    if (daysBetween(todayStr(), cur.expiry_date) < 0) {
      throw conflict('LICENSE_EXPIRED', `${lic.type_name}已于 ${cur.expiry_date} 到期，禁止继续使用，请完成续证`);
    }
    res.json({
      ok: true,
      cert_no: cur.cert_no,
      valid_until: cur.expiry_date,
      store: lic.store_name,
      type: lic.type_name,
    });
  }));

  /** 登记证照原件遗失 */
  app.post('/api/licenses/:id/mark-lost', requireAuth, h(async (req, res) => {
    const lic = getLicense(Number(req.params.id));
    assertStoreAccess(req.user, lic.store_id);
    if (!lic.current_version_id) throw conflict('NO_CURRENT_VERSION', '该证照暂无有效版本');
    db.prepare('UPDATE license_versions SET is_original_lost = 1 WHERE id = ?').run(lic.current_version_id);
    raiseFlag(db, {
      license_id: lic.id,
      version_id: lic.current_version_id,
      flag_type: 'original_lost',
      message: `${lic.type_name}原件遗失，需补办并留存补办回执`,
    });
    addReview('license_version', lic.current_version_id, 'mark_lost', req.user.id, '登记证照原件遗失');
    res.json({ ok: true });
  }));

  // ---------- 总部审核 ----------
  /**
   * 审核通过 = 版本切换：条件更新保证并发下只有一人审核成功；
   * 旧版本转 superseded，新版本成为当前有效版本；校验新旧证有效期衔接。
   */
  app.post('/api/versions/:id/review', requireHQ, h(async (req, res) => {
    const v = getVersion(Number(req.params.id));
    const { action, comment } = req.body || {};
    if (!['approve', 'reject'].includes(action)) throw badRequest('action 须为 approve 或 reject');
    const lic = getLicense(v.license_id);
    const now = nowIso();

    tx(db, () => {
      if (action === 'approve') {
        const r = db.prepare(
          "UPDATE license_versions SET status = 'current', reviewed_by = ?, reviewed_at = ?, review_comment = ? WHERE id = ? AND status = 'pending_review'",
        ).run(req.user.id, now, comment ?? null, v.id);
        if (r.changes === 0) throw conflict('ALREADY_REVIEWED', '该版本已被其他审核人处理');

        const old = lic.current_version_id ? getVersion(lic.current_version_id) : null;
        if (old) {
          db.prepare("UPDATE license_versions SET status = 'superseded' WHERE id = ?").run(old.id);
          const gap = daysBetween(old.expiry_date, v.issue_date);
          if (gap > 1) {
            raiseFlag(db, {
              license_id: lic.id, version_id: v.id, flag_type: 'validity_gap',
              message: `旧证 ${old.expiry_date} 到期，新证 ${v.issue_date} 生效，中间断档 ${gap - 1} 天`,
            });
          } else if (gap < -90) {
            raiseFlag(db, {
              license_id: lic.id, version_id: v.id, flag_type: 'validity_gap',
              message: `新证生效日早于旧证到期日 ${-gap} 天，有效期重叠异常`,
            });
          }
        }
        db.prepare('UPDATE licenses SET current_version_id = ? WHERE id = ?').run(v.id, lic.id);
        const expired = daysBetween(todayStr(), v.expiry_date) < 0;
        db.prepare('UPDATE licenses SET status = ? WHERE id = ?').run(expired ? 'expired' : 'active', lic.id);

        if (lic.recheck_required) {
          const store = db.prepare('SELECT * FROM stores WHERE id = ?').get(lic.store_id);
          if (v.address === store.address && v.scope === store.business_scope) {
            db.prepare('UPDATE licenses SET recheck_required = 0 WHERE id = ?').run(lic.id);
            resolveFlags(db, lic.id, ['recheck_required']);
            const remaining = db.prepare(
              'SELECT COUNT(*) AS c FROM licenses WHERE store_id = ? AND recheck_required = 1',
            ).get(lic.store_id).c;
            if (remaining === 0) {
              db.prepare(
                "UPDATE store_changes SET status = 'rechecked', rechecked_at = datetime('now') WHERE store_id = ? AND status = 'pending_recheck'",
              ).run(lic.store_id);
            }
          }
        }
        db.prepare("UPDATE renewal_tasks SET status = 'completed', updated_at = datetime('now') WHERE version_id = ?").run(v.id);
        resolveFlags(db, lic.id, ['overdue_unsubmitted', 'acceptance_rejected']);
        addReview('license_version', v.id, 'approve', req.user.id, comment ?? null);
      } else {
        const r = db.prepare(
          "UPDATE license_versions SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, review_comment = ? WHERE id = ? AND status = 'pending_review'",
        ).run(req.user.id, now, comment ?? null, v.id);
        if (r.changes === 0) throw conflict('ALREADY_REVIEWED', '该版本已被其他审核人处理');
        raiseFlag(db, {
          license_id: lic.id, version_id: v.id, flag_type: 'acceptance_rejected',
          message: `续证申请被驳回${comment ? `：${comment}` : ''}`,
        });
        db.prepare("UPDATE renewal_tasks SET status = 'rejected', updated_at = datetime('now') WHERE version_id = ?").run(v.id);
        addReview('license_version', v.id, 'reject', req.user.id, comment ?? null);
      }
    });
    res.json({ ok: true });
  }));

  /** 总部要求补正材料 */
  app.post('/api/versions/:id/corrections', requireHQ, h(async (req, res) => {
    const v = getVersion(Number(req.params.id));
    const reason = req.body?.reason;
    if (!reason || !String(reason).trim()) throw badRequest('请填写补正原因');
    const lic = getLicense(v.license_id);
    const correctionId = tx(db, () => {
      let task = db.prepare('SELECT * FROM renewal_tasks WHERE version_id = ?').get(v.id)
        ?? openTask(db, lic.id);
      let taskId;
      if (task) {
        taskId = task.id;
        db.prepare("UPDATE renewal_tasks SET status = 'correcting', updated_at = datetime('now') WHERE id = ?").run(taskId);
      } else {
        taskId = db.prepare(
          "INSERT INTO renewal_tasks (license_id, version_id, due_date, status, note) VALUES (?,?,?,'correcting',?)",
        ).run(lic.id, v.id, v.expiry_date, '总部要求补正材料').lastInsertRowid;
      }
      const cid = db.prepare(
        'INSERT INTO corrections (renewal_task_id, version_id, reason, requested_by) VALUES (?,?,?,?)',
      ).run(taskId, v.id, String(reason).trim(), req.user.id).lastInsertRowid;
      addReview('license_version', v.id, 'request_correction', req.user.id, String(reason).trim());
      return cid;
    });
    res.status(201).json({ correction_id: correctionId });
  }));

  /** 总部标记扫描件模糊 */
  app.post('/api/versions/:id/mark-blurry', requireHQ, h(async (req, res) => {
    const v = getVersion(Number(req.params.id));
    const lic = getLicense(v.license_id);
    db.prepare('UPDATE license_versions SET is_scan_blurry = 1 WHERE id = ?').run(v.id);
    raiseFlag(db, {
      license_id: lic.id, version_id: v.id, flag_type: 'blurry_scan',
      message: '扫描件模糊，需重新上传清晰扫描件',
    });
    addReview('license_version', v.id, 'mark_blurry', req.user.id, '扫描件模糊');
    res.json({ ok: true });
  }));

  // ---------- 续证任务 / 补正 ----------
  app.get('/api/renewals', requireAuth, h(async (req, res) => {
    const base = `SELECT rt.*, l.store_id, s.name AS store_name, t.name AS type_name,
                         v.cert_no, v.status AS version_status
                  FROM renewal_tasks rt
                  JOIN licenses l ON l.id = rt.license_id
                  JOIN stores s ON s.id = l.store_id
                  JOIN license_types t ON t.id = l.type_id
                  LEFT JOIN license_versions v ON v.id = rt.version_id`;
    const order = " ORDER BY rt.status = 'completed', rt.due_date, rt.id";
    const rows = req.user.role === 'hq_legal'
      ? db.prepare(base + order).all()
      : db.prepare(`${base} WHERE l.store_id = ?${order}`).all(req.user.store_id);
    res.json({ renewals: rows });
  }));

  app.get('/api/corrections', requireAuth, h(async (req, res) => {
    const base = `SELECT c.*, s.name AS store_name, t.name AS type_name, u.display_name AS requested_by_name,
                         rt.license_id
                  FROM corrections c
                  JOIN renewal_tasks rt ON rt.id = c.renewal_task_id
                  JOIN licenses l ON l.id = rt.license_id
                  JOIN stores s ON s.id = l.store_id
                  JOIN license_types t ON t.id = l.type_id
                  JOIN users u ON u.id = c.requested_by`;
    const rows = req.user.role === 'hq_legal'
      ? db.prepare(`${base} ORDER BY c.status, c.id DESC`).all()
      : db.prepare(`${base} WHERE l.store_id = ? ORDER BY c.status, c.id DESC`).all(req.user.store_id);
    res.json({ corrections: rows });
  }));

  app.post('/api/corrections/:id/resolve', requireAuth, h(async (req, res) => {
    const c = db.prepare('SELECT * FROM corrections WHERE id = ?').get(Number(req.params.id));
    if (!c) throw notFound('补正记录不存在');
    const task = db.prepare('SELECT * FROM renewal_tasks WHERE id = ?').get(c.renewal_task_id);
    const lic = getLicense(task.license_id);
    assertStoreAccess(req.user, lic.store_id);
    db.prepare("UPDATE corrections SET status = 'resolved', resolved_at = datetime('now') WHERE id = ?").run(c.id);
    addReview('renewal_task', task.id, 'resolve_correction', req.user.id, `补正完成：${c.reason}`);
    res.json({ ok: true });
  }));

  // ---------- 标记 ----------
  app.get('/api/flags', requireAuth, h(async (req, res) => {
    const base = `SELECT f.*, s.name AS store_name, t.name AS type_name
                  FROM flags f
                  JOIN licenses l ON l.id = f.license_id
                  JOIN stores s ON s.id = l.store_id
                  JOIN license_types t ON t.id = l.type_id`;
    const rows = req.user.role === 'hq_legal'
      ? db.prepare(`${base} ORDER BY f.status, f.id DESC`).all()
      : db.prepare(`${base} WHERE l.store_id = ? ORDER BY f.status, f.id DESC`).all(req.user.store_id);
    res.json({ flags: rows.map((f) => ({ ...f, label: FLAG_LABELS[f.flag_type] })) });
  }));

  app.post('/api/flags/:id/resolve', requireAuth, h(async (req, res) => {
    const f = db.prepare('SELECT * FROM flags WHERE id = ?').get(Number(req.params.id));
    if (!f) throw notFound('标记不存在');
    const lic = getLicense(f.license_id);
    assertStoreAccess(req.user, lic.store_id);
    db.prepare("UPDATE flags SET status = 'resolved', resolved_at = datetime('now') WHERE id = ?").run(f.id);
    addReview('renewal_task', f.license_id, 'resolve_flag', req.user.id, `解除标记：${FLAG_LABELS[f.flag_type]}`);
    res.json({ ok: true });
  }));

  // ---------- 审核记录 ----------
  app.get('/api/reviews', requireAuth, h(async (req, res) => {
    const base = `SELECT rv.*, u.display_name AS actor_name
                  FROM reviews rv JOIN users u ON u.id = rv.actor_id`;
    let rows;
    if (req.user.role === 'hq_legal') {
      rows = db.prepare(`${base} ORDER BY rv.id DESC LIMIT 300`).all();
    } else {
      rows = db.prepare(
        `${base}
         WHERE (rv.entity_type = 'license_version' AND rv.entity_id IN
                  (SELECT id FROM license_versions WHERE license_id IN
                    (SELECT id FROM licenses WHERE store_id = ?)))
            OR (rv.entity_type = 'renewal_task' AND rv.entity_id IN
                  (SELECT id FROM renewal_tasks WHERE license_id IN
                    (SELECT id FROM licenses WHERE store_id = ?)))
            OR (rv.entity_type = 'store_change' AND rv.entity_id IN
                  (SELECT id FROM store_changes WHERE store_id = ?))
         ORDER BY rv.id DESC LIMIT 300`,
      ).all(req.user.store_id, req.user.store_id, req.user.store_id);
    }
    res.json({ reviews: rows });
  }));

  // ---------- 到期看板 ----------
  app.get('/api/dashboard/expiry', requireAuth, h(async (req, res) => {
    const today = todayStr();
    const base = `SELECT l.id AS license_id, l.status, l.recheck_required,
                         s.name AS store_name, s.manager_name,
                         t.name AS type_name, t.remind_days,
                         v.cert_no, v.expiry_date
                  FROM licenses l
                  JOIN stores s ON s.id = l.store_id
                  JOIN license_types t ON t.id = l.type_id
                  JOIN license_versions v ON v.id = l.current_version_id`;
    const rows = req.user.role === 'hq_legal'
      ? db.prepare(base).all()
      : db.prepare(`${base} WHERE l.store_id = ?`).all(req.user.store_id);
    for (const r of rows) r.days_left = daysBetween(today, r.expiry_date);
    rows.sort((a, b) => a.days_left - b.days_left);
    res.json({ items: rows, today });
  }));

  // ---------- 提醒与送达回执 ----------
  app.get('/api/reminders', requireAuth, h(async (req, res) => {
    const base = `SELECT r.*, s.name AS store_name, t.name AS type_name
                  FROM reminders r
                  JOIN licenses l ON l.id = r.license_id
                  JOIN stores s ON s.id = l.store_id
                  JOIN license_types t ON t.id = l.type_id`;
    const rows = req.user.role === 'hq_legal'
      ? db.prepare(`${base} ORDER BY r.id DESC LIMIT 200`).all()
      : db.prepare(`${base} WHERE l.store_id = ? ORDER BY r.id DESC LIMIT 200`).all(req.user.store_id);
    res.json({ reminders: rows });
  }));

  /** 手动触发一次提醒巡检（演示 / 运维） */
  app.post('/api/admin/sweep', requireHQ, h(async (req, res) => {
    res.json(runSweep(db, notifier));
  }));

  // ---------- 静态资源 ----------
  app.use('/scans', express.static(scansDir, { fallthrough: true }));
  app.use(express.static(path.join(__dirname, '..', 'public')));

  // ---------- 错误处理 ----------
  // eslint-disable-next-line no-unused-vars
  app.use((err, req, res, _next) => {
    if (err instanceof ApiError) {
      return res.status(err.status).json({ error: err.code, message: err.message });
    }
    if (err && (err.type === 'entity.parse.failed' || err.type === 'entity.too.large')) {
      return res.status(400).json({ error: 'BAD_REQUEST', message: '请求体无效' });
    }
    console.error(err);
    return res.status(500).json({ error: 'INTERNAL', message: '服务器内部错误' });
  });

  return { app, db };
}

module.exports = { createApp };

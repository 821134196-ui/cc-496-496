'use strict';
const { daysBetween, todayStr } = require('./util');
const { raiseFlag } = require('./flags');

const OPEN_TASK_STATUSES = "('pending','submitted','rejected','correcting','overdue')";

function openTask(db, licenseId) {
  return db.prepare(
    `SELECT * FROM renewal_tasks WHERE license_id = ? AND status IN ${OPEN_TASK_STATUSES}`,
  ).get(licenseId);
}

/**
 * 提醒巡检（进程内执行）：
 *  - 当前有效版本进入提醒提前量窗口 → 自动建续证任务
 *  - 已过期 → 证照置为 expired，任务置 overdue，未提交新材料则打「临期未提交」标记
 *  - 对窗口内未决任务发送提醒（同一任务 24 小时内不重复），回执落库
 * today 可注入，便于测试。
 */
function runSweep(db, notifier, today = todayStr()) {
  const rows = db.prepare(`
    SELECT l.id AS license_id, l.status, l.store_id,
           s.name AS store_name, s.manager_name,
           t.name AS type_name, t.remind_days,
           v.expiry_date, v.cert_no
    FROM licenses l
    JOIN stores s ON s.id = l.store_id
    JOIN license_types t ON t.id = l.type_id
    JOIN license_versions v ON v.id = l.current_version_id
  `).all();

  const result = { tasks_created: 0, reminders_sent: 0, expired: 0, receipts: [] };
  for (const lic of rows) {
    const days = daysBetween(today, lic.expiry_date);
    if (days > lic.remind_days) continue;

    let task = openTask(db, lic.license_id);
    if (!task) {
      const id = db.prepare(
        'INSERT INTO renewal_tasks (license_id, due_date, status, note) VALUES (?,?,?,?)',
      ).run(lic.license_id, lic.expiry_date, days < 0 ? 'overdue' : 'pending', '系统自动创建的续证任务').lastInsertRowid;
      task = db.prepare('SELECT * FROM renewal_tasks WHERE id = ?').get(id);
      result.tasks_created += 1;
    }

    if (days < 0) {
      if (lic.status !== 'expired') {
        db.prepare("UPDATE licenses SET status = 'expired' WHERE id = ?").run(lic.license_id);
        result.expired += 1;
      }
      if (task.status === 'pending') {
        db.prepare("UPDATE renewal_tasks SET status = 'overdue', updated_at = datetime('now') WHERE id = ?").run(task.id);
      }
      const inflight = db.prepare(
        "SELECT id FROM license_versions WHERE license_id = ? AND status = 'pending_review'",
      ).get(lic.license_id);
      if (!inflight) {
        raiseFlag(db, {
          license_id: lic.license_id,
          flag_type: 'overdue_unsubmitted',
          message: `${lic.type_name}已于 ${lic.expiry_date} 到期，门店尚未提交续证材料`,
        });
      }
    }

    const last = db.prepare(
      'SELECT MAX(sent_at) AS t FROM reminders WHERE renewal_task_id = ?',
    ).get(task.id).t;
    const due = !last || (Date.now() - new Date(last).getTime()) >= 24 * 3600 * 1000;
    if (due) {
      const msg = days < 0
        ? `【证照到期提醒】${lic.store_name} ${lic.type_name}（${lic.cert_no}）已过期 ${-days} 天，请立即提交续证材料`
        : `【证照到期提醒】${lic.store_name} ${lic.type_name}（${lic.cert_no}）将于 ${lic.expiry_date} 到期（剩余 ${days} 天），请尽快提交续证材料`;
      const receipt = notifier.send({ to: lic.manager_name, message: msg });
      db.prepare(
        `INSERT INTO reminders (license_id, renewal_task_id, recipient, channel, payload, receipt_no, delivery_status)
         VALUES (?,?,?,?,?,?,?)`,
      ).run(lic.license_id, task.id, lic.manager_name, notifier.channel, msg, receipt.receipt_no, receipt.status);
      result.reminders_sent += 1;
      result.receipts.push(receipt);
    }
  }
  return result;
}

/** 进程内定时巡检 */
function startScheduler(db, notifier, intervalMs = 60_000) {
  const timer = setInterval(() => {
    try {
      runSweep(db, notifier);
    } catch (e) {
      console.error('[reminder] sweep failed:', e);
    }
  }, intervalMs);
  timer.unref();
  return timer;
}

module.exports = { runSweep, startScheduler, openTask };

'use strict';

const FLAG_LABELS = {
  acceptance_rejected: '受理驳回',
  overdue_unsubmitted: '临期未提交',
  original_lost: '原件遗失',
  blurry_scan: '扫描件模糊',
  validity_gap: '有效期衔接异常',
  recheck_required: '变更待核查',
};

/** 同类未决标记只保留一条（幂等） */
function raiseFlag(db, { license_id, version_id = null, flag_type, message }) {
  const dup = db
    .prepare("SELECT id FROM flags WHERE license_id = ? AND flag_type = ? AND status = 'open'")
    .get(license_id, flag_type);
  if (dup) return dup.id;
  return db
    .prepare('INSERT INTO flags (license_id, version_id, flag_type, message) VALUES (?,?,?,?)')
    .run(license_id, version_id, flag_type, message).lastInsertRowid;
}

function resolveFlags(db, licenseId, types) {
  if (!types.length) return;
  const ph = types.map(() => '?').join(',');
  db.prepare(
    `UPDATE flags SET status='resolved', resolved_at=datetime('now')
     WHERE license_id = ? AND status = 'open' AND flag_type IN (${ph})`,
  ).run(licenseId, ...types);
}

module.exports = { FLAG_LABELS, raiseFlag, resolveFlags };

'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { todayStr, addDays, sha256 } = require('./util');
const { scanSvg } = require('./scansvg');
const { raiseFlag } = require('./flags');

function seedIfEmpty(db, scansDir) {
  const count = db.prepare('SELECT COUNT(*) AS c FROM users').get().c;
  if (count > 0) return false;
  seedDemo(db, scansDir, todayStr());
  return true;
}

/**
 * 多门店样例数据，覆盖全部业务状态：
 *  临期 / 已过期 / 待审核 / 受理驳回 / 材料补正 / 原件遗失 / 扫描件模糊 /
 *  新旧证断档 / 迁址待核查，以及对应审核链与续证任务。
 */
function seedDemo(db, scansDir, today) {
  fs.mkdirSync(scansDir, { recursive: true });

  const insStore = db.prepare('INSERT INTO stores (code, name, address, business_scope, manager_name) VALUES (?,?,?,?,?)');
  const insUser = db.prepare('INSERT INTO users (username, password_hash, display_name, role, store_id) VALUES (?,?,?,?,?)');
  const insType = db.prepare('INSERT INTO license_types (code, name, remind_days) VALUES (?,?,?)');
  const insLicense = db.prepare('INSERT INTO licenses (store_id, type_id, status, recheck_required) VALUES (?,?,?,?)');
  const insVersion = db.prepare(`INSERT INTO license_versions
    (license_id, version_no, cert_no, issue_date, expiry_date, address, scope, responsible_person,
     scan_path, scan_width, scan_height, status, acceptance_no, submitted_by, submitted_at,
     reviewed_by, reviewed_at, review_comment, is_original_lost, is_scan_blurry)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  const insTask = db.prepare('INSERT INTO renewal_tasks (license_id, version_id, due_date, status, acceptance_no, note) VALUES (?,?,?,?,?,?)');
  const insCorrection = db.prepare('INSERT INTO corrections (renewal_task_id, version_id, reason, requested_by) VALUES (?,?,?,?)');
  const insReview = db.prepare('INSERT INTO reviews (entity_type, entity_id, action, actor_id, comment, created_at) VALUES (?,?,?,?,?,?)');
  const insChange = db.prepare('INSERT INTO store_changes (store_id, change_type, old_value, new_value, created_by) VALUES (?,?,?,?,?)');

  // ---- 门店 ----
  const s1 = insStore.run('S001', '北京中关村店', '北京市海淀区中关村大街27号', '餐饮服务；预包装食品销售', '王硕').lastInsertRowid;
  const s2 = insStore.run('S002', '上海静安店', '上海市静安区南京西路1266号', '餐饮服务', '陈静').lastInsertRowid;
  const s3 = insStore.run('S003', '广州天河店', '广州市天河区珠江新城华夏路49号', '餐饮服务；酒类零售', '刘洋').lastInsertRowid;
  const s4 = insStore.run('S004', '成都高新店', '成都市高新区天府大道888号', '餐饮服务', '赵敏').lastInsertRowid;

  // ---- 用户 ----
  const hq = insUser.run('hq', sha256('hq123'), '林岚（总部法务）', 'hq_legal', null).lastInsertRowid;
  const m1 = insUser.run('store1', sha256('store123'), '王硕', 'store_manager', s1).lastInsertRowid;
  const m2 = insUser.run('store2', sha256('store123'), '陈静', 'store_manager', s2).lastInsertRowid;
  const m3 = insUser.run('store3', sha256('store123'), '刘洋', 'store_manager', s3).lastInsertRowid;
  const m4 = insUser.run('store4', sha256('store123'), '赵敏', 'store_manager', s4).lastInsertRowid;

  // ---- 证照类型（提醒提前量由总部法务配置） ----
  const tBiz = insType.run('BUSINESS_LICENSE', '营业执照', 30).lastInsertRowid;
  const tFood = insType.run('FOOD_PERMIT', '食品经营许可证', 45).lastInsertRowid;
  const tFire = insType.run('FIRE_CERT', '消防安全检查合格证', 20).lastInsertRowid;

  let scanSeq = 0;
  const writeScan = (fields, landscape = false) => {
    scanSeq += 1;
    const w = landscape ? 1123 : 794;
    const hgt = landscape ? 794 : 1123;
    const file = `seed-${String(scanSeq).padStart(2, '0')}.svg`;
    fs.writeFileSync(path.join(scansDir, file), scanSvg({ ...fields, width: w, height: hgt }));
    return { p: `/scans/${file}`, w, h: hgt };
  };

  const storeById = {
    [s1]: { name: '北京中关村店', address: '北京市海淀区中关村大街27号', scope: '餐饮服务；预包装食品销售', person: '王硕', manager: m1 },
    [s2]: { name: '上海静安店', address: '上海市静安区南京西路1266号', scope: '餐饮服务', person: '陈静', manager: m2 },
    [s3]: { name: '广州天河店', address: '广州市天河区珠江新城华夏路49号', scope: '餐饮服务；酒类零售', person: '刘洋', manager: m3 },
    [s4]: { name: '成都高新店', address: '成都市高新区天府大道888号', scope: '餐饮服务', person: '赵敏', manager: m4 },
  };
  const typeName = { [tBiz]: '营业执照', [tFood]: '食品经营许可证', [tFire]: '消防安全检查合格证' };

  function mkLicense(storeId, typeId, { status = 'active', recheck = 0 } = {}) {
    return insLicense.run(storeId, typeId, status, recheck).lastInsertRowid;
  }

  function mkVersion(licId, no, storeId, typeId, certNo, issue, expiry, status, opts = {}) {
    const st = storeById[storeId];
    const scan = writeScan({
      title: typeName[typeId], certNo, storeName: st.name,
      address: opts.address ?? st.address, scope: opts.scope ?? st.scope,
      person: opts.person ?? st.person, issue, expiry,
    }, opts.landscape);
    return insVersion.run(
      licId, no, certNo, issue, expiry,
      opts.address ?? st.address, opts.scope ?? st.scope, opts.person ?? st.person,
      scan.p, scan.w, scan.h, status,
      opts.acceptanceNo ?? null, opts.submittedBy ?? st.manager,
      opts.submittedAt ?? `${today}T09:00:00.000Z`,
      opts.reviewedBy ?? null, opts.reviewedAt ?? null, opts.reviewComment ?? null,
      opts.lost ? 1 : 0, opts.blurry ? 1 : 0,
    ).lastInsertRowid;
  }

  const setCurrent = (licId, vid) => db.prepare('UPDATE licenses SET current_version_id = ? WHERE id = ?').run(vid, licId);
  const review = (entityType, entityId, action, actorId, comment, at) => insReview.run(entityType, entityId, action, actorId, comment, at ?? `${today}T10:00:00.000Z`);

  // ================= 北京中关村店 =================
  // 营业执照：临期（剩余 12 天），等待门店提交续证
  const l1 = mkLicense(s1, tBiz);
  setCurrent(l1, mkVersion(l1, 1, s1, tBiz, '91110108MA01C8XU3A', addDays(today, -353), addDays(today, 12), 'current',
    { reviewedBy: hq, reviewedAt: addDays(today, -350) }));

  // 食品经营许可证：已过期 6 天（过期拦截演示）
  const l2 = mkLicense(s1, tFood, { status: 'expired' });
  setCurrent(l2, mkVersion(l2, 1, s1, tFood, 'JY1110108004521', addDays(today, -371), addDays(today, -6), 'current',
    { reviewedBy: hq, reviewedAt: addDays(today, -368) }));

  // 消防合格证：远期有效
  const l3 = mkLicense(s1, tFire);
  setCurrent(l3, mkVersion(l3, 1, s1, tFire, '京海消安检字[2025]第0812号', addDays(today, -165), addDays(today, 200), 'current',
    { reviewedBy: hq, reviewedAt: addDays(today, -160) }));

  // ================= 上海静安店 =================
  // 营业执照：门店已提交续证材料（受理号 SL2026-0417），待总部审核
  const l4 = mkLicense(s2, tBiz);
  const l4v1 = mkVersion(l4, 1, s2, tBiz, '91310106MA1FY3T28B', addDays(today, -205), addDays(today, 160), 'current',
    { reviewedBy: hq, reviewedAt: addDays(today, -200) });
  setCurrent(l4, l4v1);
  const l4v2 = mkVersion(l4, 2, s2, tBiz, '91310106MA1FY3T28B', addDays(today, -2), addDays(today, 363), 'pending_review',
    { acceptanceNo: 'SL2026-0417', submittedAt: `${today}T08:30:00.000Z` });
  insTask.run(l4, l4v2, addDays(today, 160), 'submitted', 'SL2026-0417', '门店提交续证材料');
  review('license_version', l4v2, 'submit', m2, '提交营业执照第 2 版续证材料，受理号 SL2026-0417', `${today}T08:30:00.000Z`);

  // 食品经营许可证：正常有效
  const l5 = mkLicense(s2, tFood);
  setCurrent(l5, mkVersion(l5, 1, s2, tFood, 'JY1310106002388', addDays(today, -275), addDays(today, 90), 'current',
    { reviewedBy: hq, reviewedAt: addDays(today, -270) }));

  // 消防合格证：临期且续证被驳回（受理驳回）
  const l6 = mkLicense(s2, tFire);
  const l6v1 = mkVersion(l6, 1, s2, tFire, '沪静消安检字[2025]第0455号', addDays(today, -355), addDays(today, 10), 'current',
    { reviewedBy: hq, reviewedAt: addDays(today, -350) });
  setCurrent(l6, l6v1);
  const l6v2 = mkVersion(l6, 2, s2, tFire, '沪静消安检字[2026]第0119号', addDays(today, -8), addDays(today, 357), 'rejected',
    {
      acceptanceNo: 'SL2026-0398', submittedAt: addDays(today, -8),
      reviewedBy: hq, reviewedAt: addDays(today, -5), reviewComment: '材料不全：缺少消防设施年度检测报告',
    });
  insTask.run(l6, l6v2, addDays(today, 10), 'rejected', 'SL2026-0398', '续证申请被驳回，待门店补正重报');
  raiseFlag(db, { license_id: l6, version_id: l6v2, flag_type: 'acceptance_rejected', message: '续证申请被驳回：材料不全：缺少消防设施年度检测报告' });
  review('license_version', l6v2, 'submit', m2, '提交消防安全检查合格证第 2 版续证材料，受理号 SL2026-0398', addDays(today, -8));
  review('license_version', l6v2, 'reject', hq, '材料不全：缺少消防设施年度检测报告', addDays(today, -5));

  // ================= 广州天河店（迁址，全店证照待核查） =================
  const s3OldAddress = '广州市天河区体育西路103号';
  insChange.run(s3, 'address', s3OldAddress, '广州市天河区珠江新城华夏路49号', m3);
  review('store_change', 1, 'submit', m3, `门店地址变更：${s3OldAddress} → 广州市天河区珠江新城华夏路49号`, addDays(today, -3));

  const l7 = mkLicense(s3, tBiz, { recheck: 1 });
  setCurrent(l7, mkVersion(l7, 1, s3, tBiz, '91440101MA9W5Q7R6C', addDays(today, -245), addDays(today, 120), 'current',
    { address: s3OldAddress, reviewedBy: hq, reviewedAt: addDays(today, -240) }));
  raiseFlag(db, { license_id: l7, flag_type: 'recheck_required', message: '门店迁址，证照须重新核查，不能沿用旧证' });

  // 食品经营许可证：待核查 + 原件遗失
  const l8 = mkLicense(s3, tFood, { recheck: 1 });
  const l8v1 = mkVersion(l8, 1, s3, tFood, 'JY1440106001277', addDays(today, -285), addDays(today, 80), 'current',
    { address: s3OldAddress, lost: true, reviewedBy: hq, reviewedAt: addDays(today, -280) });
  setCurrent(l8, l8v1);
  raiseFlag(db, { license_id: l8, flag_type: 'recheck_required', message: '门店迁址，证照须重新核查，不能沿用旧证' });
  raiseFlag(db, { license_id: l8, version_id: l8v1, flag_type: 'original_lost', message: '食品经营许可证原件遗失，需补办并留存补办回执' });
  review('license_version', l8v1, 'mark_lost', m3, '登记证照原件遗失', addDays(today, -2));

  // 消防合格证：待核查 + 扫描件模糊 + 材料补正中
  const l9 = mkLicense(s3, tFire, { recheck: 1 });
  const l9v1 = mkVersion(l9, 1, s3, tFire, '粤穗天消安检字[2025]第0670号', addDays(today, -305), addDays(today, 60), 'current',
    { address: s3OldAddress, reviewedBy: hq, reviewedAt: addDays(today, -300) });
  setCurrent(l9, l9v1);
  const l9v2 = mkVersion(l9, 2, s3, tFire, '粤穗天消安检字[2026]第0202号', addDays(today, -4), addDays(today, 361), 'pending_review',
    { acceptanceNo: 'SL2026-0421', submittedAt: addDays(today, -4) });
  const l9Task = insTask.run(l9, l9v2, addDays(today, 60), 'correcting', 'SL2026-0421', '总部要求补正材料').lastInsertRowid;
  insCorrection.run(l9Task, l9v2, '扫描件模糊，无法辨认发证机关印章，请重新上传清晰扫描件', hq);
  db.prepare('UPDATE license_versions SET is_scan_blurry = 1 WHERE id = ?').run(l9v2);
  raiseFlag(db, { license_id: l9, flag_type: 'recheck_required', message: '门店迁址，证照须重新核查，不能沿用旧证' });
  raiseFlag(db, { license_id: l9, version_id: l9v2, flag_type: 'blurry_scan', message: '扫描件模糊，需重新上传清晰扫描件' });
  review('license_version', l9v2, 'submit', m3, '提交消防安全检查合格证第 2 版续证材料，受理号 SL2026-0421', addDays(today, -4));
  review('license_version', l9v2, 'request_correction', hq, '扫描件模糊，无法辨认发证机关印章，请重新上传清晰扫描件', addDays(today, -2));

  // ================= 成都高新店 =================
  // 营业执照：正常有效
  const l10 = mkLicense(s4, tBiz);
  setCurrent(l10, mkVersion(l10, 1, s4, tBiz, '91510100MA6C4P9D1E', addDays(today, -325), addDays(today, 40), 'current',
    { reviewedBy: hq, reviewedAt: addDays(today, -320) }));

  // 食品经营许可证：新旧证有效期断档 40 天（衔接异常）
  const l11 = mkLicense(s4, tFood);
  const l11v1 = mkVersion(l11, 1, s4, tFood, 'JY1510109000633', addDays(today, -575), addDays(today, -210), 'superseded',
    { reviewedBy: hq, reviewedAt: addDays(today, -570) });
  const l11v2 = mkVersion(l11, 2, s4, tFood, 'JY1510109000633', addDays(today, -170), addDays(today, 195), 'current',
    { acceptanceNo: 'SL2026-0210', reviewedBy: hq, reviewedAt: addDays(today, -168) });
  setCurrent(l11, l11v2);
  raiseFlag(db, {
    license_id: l11, version_id: l11v2, flag_type: 'validity_gap',
    message: `旧证 ${addDays(today, -210)} 到期，新证 ${addDays(today, -170)} 生效，中间断档 40 天`,
  });
  review('license_version', l11v2, 'submit', m4, '提交食品经营许可证第 2 版续证材料，受理号 SL2026-0210', addDays(today, -172));
  review('license_version', l11v2, 'approve', hq, '同意换发，注意有效期断档需整改说明', addDays(today, -168));

  // 消防合格证：临期，门店已提交，待总部审核
  const l12 = mkLicense(s4, tFire);
  setCurrent(l12, mkVersion(l12, 1, s4, tFire, '川成高消安检字[2025]第0918号', addDays(today, -350), addDays(today, 15), 'current',
    { reviewedBy: hq, reviewedAt: addDays(today, -345) }));
  const l12v2 = mkVersion(l12, 2, s4, tFire, '川成高消安检字[2026]第0301号', addDays(today, -1), addDays(today, 364), 'pending_review',
    { acceptanceNo: 'SL2026-0433', submittedAt: `${today}T07:50:00.000Z`, landscape: true });
  insTask.run(l12, l12v2, addDays(today, 15), 'submitted', 'SL2026-0433', '门店提交续证材料');
  review('license_version', l12v2, 'submit', m4, '提交消防安全检查合格证第 2 版续证材料，受理号 SL2026-0433', `${today}T07:50:00.000Z`);
}

module.exports = { seedIfEmpty, seedDemo };

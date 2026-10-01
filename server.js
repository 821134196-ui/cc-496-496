'use strict';
const path = require('node:path');
const fs = require('node:fs');
const { openDb } = require('./src/db');
const { createApp } = require('./src/app');
const { createMockNotifier } = require('./src/notify');
const { seedIfEmpty } = require('./src/seed');
const { runSweep, startScheduler } = require('./src/reminders');

const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, 'data');
const SCANS_DIR = path.join(DATA_DIR, 'scans');
fs.mkdirSync(SCANS_DIR, { recursive: true });

const db = openDb(path.join(DATA_DIR, 'license.db'));
const notifier = createMockNotifier();

if (seedIfEmpty(db, SCANS_DIR)) {
  console.log('[init] 已生成多门店样例数据（4 家门店 × 3 类证照）');
}

const sweep = runSweep(db, notifier);
console.log(`[reminder] 首次巡检：新建续证任务 ${sweep.tasks_created}，发送提醒 ${sweep.reminders_sent}，新标记过期 ${sweep.expired}`);
startScheduler(db, notifier, Number(process.env.SWEEP_INTERVAL_MS || 60_000));

const { app } = createApp({ db, scansDir: SCANS_DIR, notifier });
const port = Number(process.env.PORT || 3000);
app.listen(port, () => {
  console.log(`证照管理系统已启动: http://localhost:${port}`);
  console.log('演示账号：总部法务 hq/hq123；门店 store1~store4/store123');
});

'use strict';
const { uuid, sha256, unauthorized, forbidden } = require('./util');

/** 解析 Bearer token → req.user */
function attachAuth(db) {
  return (req, _res, next) => {
    const header = req.headers.authorization || '';
    const token = header.startsWith('Bearer ') ? header.slice(7) : null;
    if (token) {
      const row = db.prepare(
        `SELECT u.id, u.username, u.display_name, u.role, u.store_id
         FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?`,
      ).get(token);
      if (row) req.user = row;
    }
    next();
  };
}

function requireAuth(req, _res, next) {
  if (!req.user) return next(unauthorized());
  return next();
}

function requireHQ(req, _res, next) {
  if (!req.user) return next(unauthorized());
  if (req.user.role !== 'hq_legal') return next(forbidden('仅总部法务可执行该操作'));
  return next();
}

/** 数据隔离：总部放行；门店负责人仅限本店 */
function assertStoreAccess(user, storeId) {
  if (user.role === 'hq_legal') return;
  if (user.store_id !== storeId) throw forbidden();
}

function login(db, username, password) {
  const user = db.prepare('SELECT * FROM users WHERE username = ?').get(username);
  if (!user || user.password_hash !== sha256(password)) {
    throw unauthorized('用户名或密码错误');
  }
  const token = uuid();
  db.prepare('INSERT INTO sessions (token, user_id) VALUES (?,?)').run(token, user.id);
  return {
    token,
    user: {
      id: user.id,
      username: user.username,
      display_name: user.display_name,
      role: user.role,
      store_id: user.store_id,
    },
  };
}

module.exports = { attachAuth, requireAuth, requireHQ, assertStoreAccess, login };

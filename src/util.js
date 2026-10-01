'use strict';
const crypto = require('node:crypto');

/** 本地日期 YYYY-MM-DD */
function todayStr(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function addDays(dateStr, n) {
  const d = new Date(`${dateStr}T00:00:00`);
  d.setDate(d.getDate() + n);
  return todayStr(d);
}

/** to - from，单位天 */
function daysBetween(fromStr, toStr) {
  const a = new Date(`${fromStr}T00:00:00`);
  const b = new Date(`${toStr}T00:00:00`);
  return Math.round((b - a) / 86400000);
}

function nowIso() {
  return new Date().toISOString();
}

function sha256(s) {
  return crypto.createHash('sha256').update(String(s)).digest('hex');
}

function uuid() {
  return crypto.randomUUID();
}

class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const badRequest = (m) => new ApiError(400, 'BAD_REQUEST', m);
const unauthorized = (m = '请先登录') => new ApiError(401, 'UNAUTHORIZED', m);
const forbidden = (m = '门店只能管理本店资料') => new ApiError(403, 'FORBIDDEN', m);
const notFound = (m = '资源不存在') => new ApiError(404, 'NOT_FOUND', m);
const conflict = (code, m) => new ApiError(409, code, m);

module.exports = {
  todayStr, addDays, daysBetween, nowIso, sha256, uuid,
  ApiError, badRequest, unauthorized, forbidden, notFound, conflict,
};

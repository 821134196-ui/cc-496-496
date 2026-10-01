'use strict';

/**
 * 模拟送达适配器：进程内“短信网关”。
 * send() 同步返回送达回执（回执号 + 状态），同时记录在 sent 数组中便于核查与测试。
 * options.failNext = true 可注入一次发送失败。
 */
function createMockNotifier(options = {}) {
  const sent = [];
  let seq = 0;
  return {
    channel: 'mock-sms',
    sent,
    send({ to, message }) {
      if (options.failNext) {
        options.failNext = false;
        const failed = { to, message, receipt_no: null, status: 'failed', delivered_at: null };
        sent.push(failed);
        return failed;
      }
      seq += 1;
      const receipt = {
        to,
        message,
        receipt_no: `RCPT-${String(seq).padStart(6, '0')}`,
        status: 'delivered',
        delivered_at: new Date().toISOString(),
      };
      sent.push(receipt);
      return receipt;
    },
  };
}

module.exports = { createMockNotifier };

'use strict';

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
}[c]));

/**
 * 生成证照扫描件占位 SVG（A4 竖版 794×1123 或横版）。
 * 返回的 SVG 带真实宽高，前端按比例完整展示，不裁剪。
 */
function scanSvg({ title, certNo, storeName, address, scope, person, issue, expiry, width = 794, height = 1123 }) {
  const rows = [
    ['证照编号', certNo],
    ['主体名称', storeName],
    ['经营场所', address],
    ['经营范围', scope],
    ['负责人', person],
    ['发证日期', issue],
    ['有效期至', expiry],
  ];
  const rowSvg = rows.map(([k, v], i) => {
    const y = 300 + i * 92;
    return [
      `<text x="90" y="${y}" font-size="28" fill="#5a6470">${esc(k)}</text>`,
      `<text x="90" y="${y + 44}" font-size="33" fill="#232a32">${esc(v)}</text>`,
      `<line x1="90" y1="${y + 62}" x2="${width - 90}" y2="${y + 62}" stroke="#d8dee4" stroke-width="2"/>`,
    ].join('');
  }).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
<rect width="${width}" height="${height}" fill="#fdfdfb"/>
<rect x="28" y="28" width="${width - 56}" height="${height - 56}" fill="none" stroke="#8a939e" stroke-width="4"/>
<rect x="42" y="42" width="${width - 84}" height="${height - 84}" fill="none" stroke="#c3cad2" stroke-width="2"/>
<text x="${width / 2}" y="150" font-size="56" font-weight="bold" fill="#2c343d" text-anchor="middle" font-family="serif">${esc(title)}</text>
<text x="${width / 2}" y="204" font-size="22" letter-spacing="6" fill="#8a939e" text-anchor="middle">SCANNED COPY · 扫描件</text>
${rowSvg}
<circle cx="${width - 195}" cy="${height - 175}" r="92" fill="none" stroke="#c8402a" stroke-width="6" opacity="0.8"/>
<text x="${width - 195}" y="${height - 168}" font-size="25" fill="#c8402a" text-anchor="middle" opacity="0.8">审核专用章</text>
</svg>`;
}

module.exports = { scanSvg };

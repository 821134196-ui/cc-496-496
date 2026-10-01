"""页面渲染：冷白 / 深灰基调，朱红只标真正过期项。

视图为纯函数：传入 svc、当前 user 与参数，返回 HTML。
"""
from __future__ import annotations

import html
import os
from datetime import date

from .service import parse_date

STATUS_CN = {
    "approved": "当前有效",
    "expired": "已过期",
    "pending_review": "待总部审核",
    "returned": "退回补正",
    "rejected": "审核驳回",
    "superseded": "已被替代",
    "draft": "草稿",
}

KIND_CN = {
    "returned": "审核退回",
    "rejected": "受理驳回",
    "blurry": "扫描件模糊",
    "lost": "原件遗失",
    "gap": "新旧证衔接",
    "address_check": "迁址/范围核查",
    "expired": "证照过期",
}

ACTION_CN = {
    "submit": "提交送审", "approve": "审核通过", "reject": "审核驳回",
    "return": "退回补正", "supersede": "版本替代", "expire": "系统标记过期",
    "recheck": "触发重新核查",
}

CSS = """
:root{
  --bg:#f4f6f8; --card:#ffffff; --ink:#26292d; --sub:#6b7280;
  --line:#e2e6ea; --line2:#eef1f4; --dark:#2b2f33;
  --red:#c63126; --red-bg:#fdf3f2;
  --amber:#8a6d2f; --amber-bg:#f5f1e6;
  --teal:#2f6f5f; --teal-bg:#eef5f2;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
a{color:#33414f;text-decoration:none;border-bottom:1px dotted #b9c1ca}
a:hover{color:var(--dark);border-bottom-style:solid}
header.top{background:var(--card);border-bottom:1px solid var(--line);padding:0 24px;
  display:flex;align-items:center;gap:20px;height:54px}
header.top .brand{font-weight:600;font-size:15px;letter-spacing:1px;color:var(--dark)}
header.top nav{display:flex;gap:4px;flex:1}
header.top nav a{border:none;padding:6px 12px;border-radius:4px;color:var(--sub)}
header.top nav a.active,header.top nav a:hover{background:#eef1f4;color:var(--ink)}
header.top .who{color:var(--sub);font-size:13px}
header.top .who b{color:var(--ink);font-weight:600}
main{max-width:1180px;margin:22px auto 60px;padding:0 24px}
h1{font-size:19px;margin:0 0 4px}
h2{font-size:15px;margin:26px 0 10px;padding-left:9px;border-left:3px solid var(--dark)}
.muted{color:var(--sub)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px 18px;
  margin-bottom:14px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line2);vertical-align:top}
th{color:var(--sub);font-weight:500;background:#fafbfc;white-space:nowrap}
tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;
  border:1px solid var(--line);color:var(--sub);background:#fafbfc;white-space:nowrap}
.badge-ok{color:var(--teal);border-color:#cfe0da;background:var(--teal-bg)}
.badge-expired{color:var(--red);border-color:#e6c3c0;background:var(--red-bg);font-weight:600}
.badge-expiring{color:var(--amber);border-color:#e2d7bd;background:var(--amber-bg)}
.badge-pending{color:#34506e;border-color:#c9d6e3;background:#eef3f8}
.badge-returned{color:#7a4a12;border-color:#e3d2b8;background:#f8f1e6}
.badge-rejected{color:var(--red);border-color:#e6c3c0;background:var(--red-bg)}
.badge-lost{color:var(--red);border-color:#e6c3c0;background:var(--red-bg)}
.flag{display:inline-block;margin:1px 3px 1px 0;padding:0 7px;border-radius:3px;font-size:12px;
  border:1px solid var(--line);color:var(--sub);background:#fafbfc}
.flag.red{color:var(--red);border-color:#e6c3c0;background:var(--red-bg)}
.flag.amber{color:var(--amber);border-color:#e2d7bd;background:var(--amber-bg)}
.days-big{font-size:20px;font-weight:600;color:var(--ink)}
.days-big.red{color:var(--red)}
.btn{display:inline-block;border:1px solid var(--dark);background:var(--dark);color:#fff;
  padding:6px 14px;border-radius:4px;font-size:13px;cursor:pointer;border-bottom:1px solid var(--dark)}
.btn.ghost{background:#fff;color:var(--dark)}
.btn.danger{border-color:var(--red);background:var(--red)}
.btn.mini{padding:2px 9px;font-size:12px}
.btn[disabled]{opacity:.45;cursor:not-allowed}
input[type=text],input[type=password],input[type=date],input[type=number],select,textarea{
  border:1px solid #cfd6dd;border-radius:4px;padding:6px 9px;font-size:13px;width:100%;
  background:#fff;color:var(--ink)}
textarea{min-height:54px}
label.fld{display:block;margin:8px 0;font-size:13px;color:var(--sub)}
label.fld b{color:var(--ink);font-weight:500}
.form-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.form-actions{margin-top:12px;display:flex;gap:8px;align-items:center}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:13px 16px}
.kpi .n{font-size:26px;font-weight:600}
.kpi .n.red{color:var(--red)}
.kpi .l{color:var(--sub);font-size:12px;margin-top:2px}
.matrix th,.matrix td{min-width:190px}
.matrix td.storecol{font-weight:500;background:#fafbfc;min-width:150px}
.cell .no{font-size:12px;color:var(--sub);word-break:break-all}
.cell .dt{font-size:12px;margin-top:2px}
.cell .todo{font-size:12px;color:var(--amber);margin-top:3px}
.cell .todo.red{color:var(--red)}
.scan{border:1px solid var(--line);background:repeating-conic-gradient(#f6f7f9 0 8px,#f0f2f4 0 16px);
  border-radius:6px;padding:8px;margin:8px 0;display:inline-block;max-width:100%}
.scan img{display:block;max-width:300px;max-height:380px;width:auto;height:auto} /* 完整原始比例 */
.scan.full img{max-width:760px;max-height:none}
.timeline{border-left:2px solid var(--line2);margin-left:6px;padding-left:16px}
.tl-item{position:relative;margin:0 0 14px}
.tl-item::before{content:"";position:absolute;left:-23px;top:5px;width:8px;height:8px;border-radius:50%;
  background:#c3c9cf;border:2px solid #fff}
.tl-item.red::before{background:var(--red)}
.tl-item.ok::before{background:var(--teal)}
.ver{border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:12px;background:#fff}
.ver.current{border-color:#bcd2cb}
.ver.retired{opacity:.72}
.ver-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
.ver-head .vno{font-weight:600}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 26px}
.kv{font-size:13px;color:var(--sub);margin:2px 0}.kv b{color:var(--ink);font-weight:500}
.flash{padding:9px 14px;border-radius:6px;margin-bottom:14px;font-size:13px;border:1px solid}
.flash.err{background:var(--red-bg);border-color:#e6c3c0;color:var(--red)}
.flash.ok{background:var(--teal-bg);border-color:#cfe0da;color:var(--teal)}
.login{max-width:360px;margin:80px auto}
.login .card{padding:24px}
.empty{color:var(--sub);padding:18px;text-align:center;font-size:13px}
.small{font-size:12px}
"""


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def status_badge(status: str) -> str:
    cls = {
        "approved": "badge-ok", "expired": "badge-expired",
        "pending_review": "badge-pending", "returned": "badge-returned",
        "rejected": "badge-rejected", "superseded": "", "draft": "",
    }.get(status, "")
    return f'<span class="badge {cls}">{esc(STATUS_CN.get(status, status))}</span>'


def flag_tags(flags) -> str:
    labels = {
        "expired": ("已过期", "red"), "expiring": ("临期", "amber"),
        "recheck": ("迁址待核查", "red"), "lost": ("原件遗失", "red"),
        "returned": ("待补正", "amber"), "blurry": ("扫描模糊", "amber"),
        "rejected": ("受理驳回", "red"), "gap": ("衔接空档", "amber"),
    }
    out = []
    for f in flags:
        text, cls = labels.get(f, (f, ""))
        out.append(f'<span class="flag {cls}">{text}</span>')
    return "".join(out)


def _nav(user, active: str) -> str:
    is_hq = user["role"] == "hq"
    items = [("/", "dashboard", "到期看板"), ("/matrix", "matrix", "证照矩阵"),
             ("/tasks", "tasks", "续证任务"), ("/corrections", "corrections", "材料补正")]
    if is_hq:
        items += [("/audits", "audits", "审核记录"), ("/config", "config", "提醒配置")]
    links = "".join(
        f'<a href="{u}" class="{"active" if k == active else ""}">{t}</a>'
        for u, k, t in items)
    role = "总部法务" if is_hq else f"门店 · {esc(user['display'])}"
    return (f'<header class="top"><div class="brand">证照台账 · LICENSE HUB</div><nav>{links}</nav>'
            f'<div class="who"><b>{esc(user["display"])}</b>（{role}） · '
            f'<a href="/logout">退出</a></div></header>')


def page(user, title, body, active="", flash=None) -> str:
    f = ""
    if flash:
        kind, msg = flash
        f = f'<div class="flash {"err" if kind == "err" else "ok"}">{esc(msg)}</div>'
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · 证照台账</title><style>{CSS}</style></head>
<body>{_nav(user, active)}<main><h1>{esc(title)}</h1>{f}{body}</main></body></html>"""


def login_page(users, err=None) -> str:
    opts = "".join(
        f'<option value="{esc(u["username"])}">{esc(u["display"])}'
        f'（{"总部" if u["role"] == "hq" else "门店"}）</option>' for u in users)
    f = '<div class="flash err">口令不正确</div>' if err else ""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>登录 · 证照台账</title><style>{CSS}</style></head><body><div class="login">
<div class="card"><h1>证照台账</h1><p class="muted small">多门店营业执照 / 食品许可等证照续证管理</p>
{f}<form method="post" action="/login">
<label class="fld"><b>账号</b><select name="username">{opts}</select></label>
<label class="fld"><b>口令</b><input type="password" name="pin" value="1234" autofocus></label>
<div class="form-actions"><button class="btn" type="submit">登录</button></div>
<p class="muted small">样例口令均为 1234</p></form></div></div></body></html>"""


def dashboard(svc, user, dash, receipts) -> str:
    today = parse_date(dash["today"])
    b = dash["buckets"]

    kpis = f"""
    <div class="kpis">
      <div class="kpi"><div class="n red">{len(dash['expired'])}</div><div class="l">真正过期（须立即换证）</div></div>
      <div class="kpi"><div class="n">{b.get(7, 0)}</div><div class="l">7 天内到期</div></div>
      <div class="kpi"><div class="n">{b.get(30, 0)}</div><div class="l">30 天内到期</div></div>
      <div class="kpi"><div class="n">{b.get(90, 0)}</div><div class="l">90 天内到期</div></div>
      <div class="kpi"><div class="n red">{len(dash['recheck'])}</div><div class="l">迁址/范围待重新核查</div></div>
      <div class="kpi"><div class="n">{len(dash['pending'])}</div><div class="l">待总部审核新版本</div></div>
    </div>"""

    def cert_cell_link(r, cert_id, text):
        return f'<a href="/cert/{cert_id}">{esc(text)}</a>'

    rows_exp = "".join(
        f'<tr><td>{esc(r["store"]["name"])}</td>'
        f'<td>{esc(c["type"]["name"])}</td>'
        f'<td class="no">{esc(c["current"]["cert_number"])}</td>'
        f'<td><span class="badge badge-expired">已过期 {-c["days_left"]} 天</span></td>'
        f'<td class="muted">{esc(r["store"]["address"])}</td>'
        f'<td><a href="/cert/{c["cert"]["id"]}">去换证</a></td></tr>'
        for r, c in dash["expired"])
    exp_table = (
        '<table><thead><tr><th>门店</th><th>证照</th><th>编号</th><th>状态</th><th>登记地址</th></tr></thead>'
        f'<tbody>{rows_exp}</tbody></table>' if dash["expired"]
        else '<div class="empty">无真正过期项</div>')

    rows_xp = ""
    for r, c, days in dash["expiring"]:
        v = c["current"]
        todo = esc(v["responsible"]) if v and v["responsible"] else "门店负责人"
        rows_xp += (f'<tr><td>{esc(r["store"]["name"])}</td><td>{esc(c["type"]["name"])}</td>'
                    f'<td><span class="days-big">{days}</span> 天</td>'
                    f'<td>{esc(v["valid_to"])}</td><td>{todo}</td>'
                    f'<td>{flag_tags([f for f in c["flags"] if f in ("gap","blurry","returned","rejected")]) or "—"}</td></tr>')
    xp_table = (
        '<table><thead><tr><th>门店</th><th>证照</th><th>剩余天数</th><th>到期日</th>'
        '<th>待办人</th><th>异常标记</th></tr></thead>'
        f'<tbody>{rows_xp}</tbody></table>' if dash["expiring"]
        else '<div class="empty">暂无 30 天内到期证照</div>')

    rc = "".join(
        f'<tr><td>{esc(r["store"]["name"])}</td>'
        f'<td>{esc(c["type"]["name"])}</td><td>{esc(c["current"]["valid_to"])}</td>'
        f'<td>{flag_tags(["recheck"])}</td>'
        f'<td><a class="btn mini ghost" href="/cert/{c["cert"]["id"]}">去核查</a></td></tr>'
        for r, c in dash["recheck"])
    rc_table = (
        '<table><thead><tr><th>门店</th><th>证照</th><th>旧证有效期</th><th>标记</th><th></th></tr></thead>'
        f'<tbody>{rc}</tbody></table>' if dash["recheck"]
        else '<div class="empty">无待重新核查项（迁址或经营范围变化后旧证不得沿用）</div>')

    pend = "".join(
        f'<tr><td>{esc(r["store"]["name"])}</td>'
        f'<td>{esc(c["type"]["name"])}</td>'
        f'<td>{status_badge(c["pending"]["status"])}</td>'
        f'<td>{esc(c["pending"]["submitted_at"])}</td>'
        f'<td><a href="/cert/{c["cert"]["id"]}">{"前往审核" if user["role"]=="hq" else "查看"}</a></td></tr>'
        for r, c in dash["pending"])
    pend_table = ('<table><thead><tr><th>门店</th><th>证照</th><th>状态</th><th>提交时间</th><th></th></tr></thead>'
                  f'<tbody>{pend}</tbody></table>' if pend else '<div class="empty">无待审核版本</div>')

    corr_rows = "".join(
        f'<tr><td>{esc(k["store_name"])}</td><td>{esc(k["type_name"])}</td>'
        f'<td><span class="flag {"red" if k["kind"] in ("rejected","lost","address_check","expired") else "amber"}">'
        f'{esc(KIND_CN.get(k["kind"], k["kind"]))}</span></td>'
        f'<td class="small">{esc(k["reason"])}</td>'
        f'<td><a href="/cert/{_cert_of_correction(svc, k)}">处理</a></td></tr>'
        for k in dash["corrections"][:8])
    corr_table = (f'<table><thead><tr><th>门店</th><th>证照</th><th>类型</th><th>事由</th><th></th></tr></thead>'
                  f'<tbody>{corr_rows}</tbody></table>'
                  f'<p><a href="/corrections">全部材料补正 →</a></p>'
                  if dash["corrections"] else '<div class="empty">无未闭环补正</div>')

    rcp_rows = "".join(
        f'<tr><td class="small">{esc(r["created_at"])}</td><td>{esc(r["target"])}</td>'
        f'<td class="small">{esc(r["receipt"])}</td><td class="small">{esc(r["content"][:60])}…</td></tr>'
        for r in receipts[:6])
    rcp_table = (f'<h2>送达回执（模拟适配器）</h2><div class="card"><table>'
                 f'<thead><tr><th>时间</th><th>接收方</th><th>回执号</th><th>内容</th></tr></thead>'
                 f'<tbody>{rcp_rows}</tbody></table></div>' if rcp_rows else "")

    body = f"""
    <p class="muted">台账日期（系统时钟）：<b>{esc(dash['today'])}</b>　朱红仅用于真正过期 / 须重新核查项。</p>
    {kpis}
    <h2>真正过期</h2><div class="card">{exp_table}</div>
    <h2>临期证照（30 天内，显示剩余天数与待办人）</h2><div class="card">{xp_table}</div>
    <h2>迁址 / 经营范围变化 · 重新核查</h2><div class="card">{rc_table}</div>
    <h2>待总部审核</h2><div class="card">{pend_table}</div>
    <h2>待处理材料补正</h2><div class="card">{corr_table}</div>
    {rcp_table}
    """
    return page(user, "到期看板", body, "dashboard")


def _cert_of_correction(svc, k):
    row = svc.db.query_one("SELECT cert_id FROM cert_versions WHERE id=?", (k["version_id"],))
    return row["cert_id"]


def matrix_view(user, rows) -> str:
    types = rows[0]["cells"] if rows else []
    head = "".join(f"<th>{esc(c['type']['name'])}</th>" for c in types)
    body_rows = ""
    for r in rows:
        cells = ""
        for cell in r["cells"]:
            cert = cell["cert"]
            if not cert:
                cells += '<td class="cell muted">—</td>'
                continue
            v, p = cell["current"], cell["pending"]
            inner = ""
            if v:
                days = cell["days_left"]
                if "expired" in cell["flags"]:
                    dtxt = f'<div class="todo red">已过期 {-days} 天</div>'
                elif "expiring" in cell["flags"]:
                    dtxt = f'<div class="todo">剩余 <b>{days}</b> 天 · 待办：{esc(v["responsible"] or "门店负责人")}</div>'
                else:
                    dtxt = f'<div class="muted small">剩余 {days} 天</div>'
                inner = (f'<div class="no">{esc(v["cert_number"])}</div>'
                         f'<div class="dt">{esc(v["valid_from"])} ~ {esc(v["valid_to"])}</div>'
                         f'{dtxt}')
            else:
                inner = '<div class="muted small">未建档</div>'
            if p:
                inner += f'<div class="small">{status_badge(p["status"])}</div>'
            if cell["flags"]:
                inner += f'<div>{flag_tags(cell["flags"])}</div>'
            inner += f'<div class="small"><a href="/cert/{cert["id"]}">详情/版本链</a></div>'
            cells += f'<td class="cell">{inner}</td>'
        body_rows += (f'<tr><td class="storecol">{esc(r["store"]["name"])}'
                      f'<div class="no">{esc(r["store"]["address"])}</div></td>{cells}</tr>')
    body = f"""
    <p class="muted">总部视图：行 = 门店，列 = 证照类型。新证须经总部审核通过才会成为当前有效版本。</p>
    <div class="card"><table class="matrix">
    <thead><tr><th>门店 ＼ 证照</th>{head}</tr></thead><tbody>{body_rows}</tbody></table>
    {'<div class="empty">暂无数据</div>' if not rows else ''}</div>"""
    return page(user, "证照矩阵", body, "matrix")


def cert_detail(svc, user, d, flash=None) -> str:
    cert = d["cert"]
    is_hq = user["role"] == "hq"
    today = svc.today()

    # 版本链
    ver_html = ""
    for v in d["versions"]:
        retired = v["status"] in ("superseded", "rejected")
        cls = "ver current" if v["status"] in ("approved", "expired") else \
              "ver retired" if retired else "ver"
        scan = ""
        if v["scan_path"]:
            blur = ' <span class="flag amber">系统标记：模糊</span>' if not v["scan_clear"] else ""
            scan = (f'<div class="scan"><img src="/scan/{v["id"]}" alt="扫描件 V{v["version_no"]}"'
                    f' width="{v["scan_width"] or ""}" height="{v["scan_height"] or ""}"></div>'
                    f'<div class="small muted">原始尺寸 {v["scan_width"]}×{v["scan_height"]} '
                    f'（按完整比例显示）{blur}　<a href="/scan/{v["id"]}" target="_blank">查看原件</a></div>')
        addr_warn = ""
        if v["address_seq"] < cert["store_seq"] and v["status"] in ("approved", "expired", "pending_review"):
            addr_warn = '<div class="flag red">登记地址序号已过期：门店已迁址/变更范围，须重新核查</div>'
        ver_html += f"""
        <div class="{cls}">
          <div class="ver-head"><span class="vno">V{v['version_no']}</span>{status_badge(v['status'])}
            {flag_tags(['lost'] if v['origin_lost'] else [])}{addr_warn}</div>
          <div class="grid2">
            <div>
              <div class="kv"><b>证照编号：</b>{esc(v['cert_number'])}</div>
              <div class="kv"><b>有效期：</b>{esc(v['valid_from'])} 至 {esc(v['valid_to'])}</div>
              <div class="kv"><b>责任人：</b>{esc(v['responsible'])}</div>
              <div class="kv"><b>地址快照：</b>{esc(v['address'])}</div>
              <div class="kv"><b>经营范围快照：</b>{esc(v['business_scope'])}</div>
            </div>
            <div>
              <div class="kv"><b>提交：</b>{esc(v['submitter_display'])} · {esc(v['submitted_at'])}</div>
              <div class="kv"><b>裁决：</b>{esc(v['decider_display'] or '—')} · {esc(v['decided_at'] or '—')}</div>
              <div class="kv"><b>审核意见：</b>{esc(v['decision_note']) or '—'}</div>
            </div>
          </div>
          {scan}
          {_version_actions(svc, user, v, d['renewals'], cert, today)}
        </div>"""

    # 续证受理
    rn = ""
    for r in d["renewals"]:
        st = {'accepted': '<span class="badge badge-pending">已受理</span>',
              'rejected': '<span class="badge badge-rejected">受理驳回</span>',
              'closed': '<span class="badge">已结案</span>'}[r["status"]]
        act = ""
        if not is_hq and r["status"] == "accepted":
            act = (f'<form method="post" action="/renewal/{r["id"]}/reject" style="display:flex;gap:6px">'
                   f'<input type="text" name="reason" placeholder="驳回原因" required>'
                   f'<button class="btn mini danger">登记驳回</button></form>')
        rn += (f'<tr><td>{esc(r["accept_no"])}</td><td>{esc(r["authority"])}</td>'
               f'<td>{esc(r["accepted_at"])}</td><td>{st}</td>'
               f'<td class="small">{esc(r["reject_reason"])}</td>'
               f'<td>{esc(r["created_by_display"])}</td><td>{act}</td></tr>')
    renewals_box = (
        f'<table><thead><tr><th>受理号</th><th>受理机关</th><th>受理日期</th><th>状态</th>'
        f'<th>驳回原因</th><th>登记人</th><th>操作</th></tr></thead><tbody>{rn}</tbody></table>'
        if rn else '<div class="empty">尚未登记续证受理号</div>')

    # 补正
    ck = ""
    for k in d["corrections"]:
        st = ('<span class="badge badge-ok">已闭环</span>' if k["status"] == "resolved"
              else '<span class="flag amber">待补正</span>')
        act = ""
        if k["status"] == "open":
            act = (f'<form method="post" action="/correction/{k["id"]}/resolve" style="display:flex;gap:6px">'
                   f'<input type="text" name="resolution" placeholder="处理说明" required>'
                   f'<button class="btn mini ghost">标记闭环</button></form>')
        ck += (f'<tr><td>{esc(KIND_CN.get(k["kind"], k["kind"]))}</td><td class="small">{esc(k["reason"])}</td>'
               f'<td>{st}</td><td class="small">{esc(k["resolved_display"] or "—")} '
               f'{esc(k["resolved_at"] or "")}</td><td>{act}</td></tr>')
    corr_box = (f'<table><thead><tr><th>类型</th><th>事由</th><th>状态</th><th>闭环</th><th>操作</th></tr></thead>'
                f'<tbody>{ck}</tbody></table>' if ck else '<div class="empty">无补正记录</div>')

    # 审核链
    au = ""
    for a in d["audits"]:
        red = "red" if a["action"] in ("reject", "expire") else \
              "ok" if a["action"] in ("approve",) else ""
        au += (f'<div class="tl-item {red}"><b>{esc(ACTION_CN.get(a["action"], a["action"]))}</b>'
               f' <span class="muted small">{esc(a["actor_display"] or "系统")} · {esc(a["created_at"])}</span>'
               f'<div class="small">{esc(a["note"]) or "—"}</div></div>')
    audit_box = f'<div class="timeline">{au}</div>'

    # 任务 + 回执
    tk = ""
    for t in d["tasks"]:
        st = {'open': '<span class="badge badge-pending">待办</span>',
              'done': '<span class="badge badge-ok">已结案</span>',
              'cancelled': '<span class="badge">已取消</span>'}[t["status"]]
        kind = {'expiring': '临期提醒', 'expired': '过期提醒', 'recheck': '重新核查'}[t["kind"]]
        tk += (f'<tr><td>{kind}</td><td>{esc(t["due_date"])}</td><td>{st}</td>'
               f'<td>{esc(t["assignee_display"] or "门店负责人")}</td><td>{"已送达" if t["notified"] else "—"}</td></tr>')
    task_box = (f'<table><thead><tr><th>任务</th><th>到期日</th><th>状态</th><th>待办人</th><th>送达</th></tr></thead>'
                f'<tbody>{tk}</tbody></table>' if tk else '<div class="empty">暂无任务</div>')

    head_info = f"""
    <div class="card">
      <div class="kv"><b>门店：</b>{esc(cert['store_name'])}　|　<b>证照：</b>{esc(cert['type_name'])}</div>
      <div class="kv"><b>当前登记地址：</b>{esc(cert['store_address'])}</div>
      <div class="kv"><b>当前经营范围：</b>{esc(cert['store_scope'])}</div>
      <div class="kv"><b>地址/范围版本序号：</b>{cert['store_seq']}
        <span class="muted small">（门店迁址或经营范围变化后序号 +1，旧版本不得沿用，须重新核查换证）</span></div>
      <div class="kv"><b>提醒提前量：</b>{esc(cert['remind_days'].replace(',', ' / '))} 天</div>
    </div>"""

    body = f"""
    <p><a href="/matrix">← 证照矩阵</a>　<a href="/store/{cert['store_id']}">门店资料 →</a></p>
    {head_info}
    <h2>版本链（总部审核通过后方为当前有效版本）</h2>
    {ver_html}
    <h2>续证受理</h2><div class="card">{renewals_box}</div>
    <h2>材料补正</h2><div class="card">{corr_box}</div>
    <h2>续证任务</h2><div class="card">{task_box}</div>
    <h2>审核链</h2><div class="card"><div class="timeline">{au}</div></div>
    """
    title = f"{cert['store_name']} · {cert['type_name']}"
    return page(user, title, body, "matrix", flash=flash)


def _version_actions(svc, user, v, renewals, cert, today: date):
    """按角色渲染该版本上的操作表单。"""
    is_hq = user["role"] == "hq"
    parts = []
    vv = dict(v)

    if is_hq and v["status"] == "pending_review":
        parts.append(f"""
        <form method="post" action="/version/{v['id']}/review" class="card" style="margin-top:10px">
          <label class="fld"><b>总部审核意见</b><textarea name="note" placeholder="通过可留空；退回/驳回请注明原因"></textarea></label>
          <div class="form-actions">
            <button class="btn" name="decision" value="approve">审核通过（标为当前有效）</button>
            <button class="btn ghost" name="decision" value="return">退回补正</button>
            <button class="btn danger" name="decision" value="reject">驳回</button>
          </div>
          <p class="muted small">系统硬关卡：已过期 / 地址序号落后（迁址未重核）/ 扫描件模糊，均无法通过。</p>
        </form>""")

    if not is_hq:
        if v["status"] == "returned":
            parts.append(_upload_form(v["cert_id"], returned=v["id"],
                                      prefix="补正后重新送审"))
        if v["status"] in ("pending_review", "returned"):
            accepted = any(r["version_id"] == v["id"] and r["status"] == "accepted"
                           for r in renewals)
            if not accepted:
                parts.append(f"""
                <form method="post" action="/version/{v['id']}/acceptance" class="card" style="margin-top:10px">
                  <b>登记续证受理号</b>
                  <div class="form-row" style="margin-top:6px">
                    <label class="fld"><b>受理号</b><input type="text" name="accept_no" required></label>
                    <label class="fld"><b>受理机关</b><input type="text" name="authority"></label>
                  </div>
                  <div class="form-actions"><button class="btn ghost">保存受理号</button></div>
                </form>""")
    return "".join(parts)


def _upload_form(cert_id, returned=None, prefix="上传续证材料"):
    action = (f"/version/{returned}/resubmit" if returned else f"/cert/{cert_id}/submit")
    extra = '<input type="hidden" name="returned" value="%d">' % returned if returned else ""
    if returned:
        fields = ""
    else:
        fields = """
          <div class="form-row">
            <label class="fld"><b>证照编号</b><input type="text" name="cert_number" required></label>
            <label class="fld"><b>责任人</b><input type="text" name="responsible" required></label>
          </div>
          <div class="form-row">
            <label class="fld"><b>有效期起始</b><input type="date" name="valid_from" required></label>
            <label class="fld"><b>有效期到期</b><input type="date" name="valid_to" required></label>
          </div>"""
    return f"""
    <form method="post" action="{action}" enctype="multipart/form-data" class="card" style="margin-top:10px">
      <b>{prefix}</b>{extra}{fields}
      <label class="fld"><b>扫描件（PNG）</b><input type="file" name="scan" accept="image/png"></label>
      <label class="fld"><input type="checkbox" name="blurry" value="1" style="width:auto">
        标记为低清模糊件（演示模糊拦截）</label>
      <label class="fld"><input type="checkbox" name="sample" value="1" style="width:auto">
        未选择文件时，生成系统样例扫描件（演示用）</label>
      <label class="fld"><b>备注</b><input type="text" name="note"></label>
      <div class="form-actions"><button class="btn">提交并送总部审核</button></div>
      <p class="muted small">过期日期早于今天会被拦截；新旧证有效期不衔接会自动挂补正。</p>
    </form>"""


def store_detail(svc, user, store, cert_rows, flash=None) -> str:
    is_hq = user["role"] == "hq"
    cert_list = ""
    for c in cert_rows:
        cert_list += (f'<tr><td>{esc(c["type_name"])}</td><td>{status_badge(c["status"])}</td>'
                      f'<td>{esc(c["valid_to"] or "—")}</td><td>{esc(c["responsible"] or "—")}</td>'
                      f'<td><a href="/cert/{c["id"]}">进入</a></td></tr>')
    addr_form = ""
    if not is_hq:
        addr_form = f"""
        <h2>迁址 / 经营范围变更</h2>
        <div class="card"><form method="post" action="/store/{store['id']}/change">
          <p class="muted small">提交后地址版本序号 +1，该店全部当前证照自动进入「重新核查」，
          旧地址版本审批时会被拦截，不能沿用旧证。</p>
          <label class="fld"><b>新门店地址</b><input type="text" name="address"
            value="{esc(store['address'])}" required></label>
          <label class="fld"><b>经营范围</b><textarea name="business_scope">{esc(store['business_scope'])}</textarea></label>
          <div class="form-actions"><button class="btn danger">提交变更并触发重新核查</button></div>
        </form></div>"""
    lost_form = ""
    if not is_hq:
        btns = "".join(
            f'<form method="post" action="/cert/{c["id"]}/lost" style="display:inline">'
            f'<button class="btn mini ghost">登记原件遗失</button></form>'
            for c in cert_rows)
        lost_form = f'<h2>原件报失</h2><div class="card">{btns}</div>'
    body = f"""
    <div class="card">
      <div class="kv"><b>门店：</b>{esc(store['name'])}</div>
      <div class="kv"><b>地址：</b>{esc(store['address'])}</div>
      <div class="kv"><b>经营范围：</b>{esc(store['business_scope'])}</div>
      <div class="kv"><b>地址版本序号：</b>{store['address_seq']}</div>
    </div>
    <h2>本店证照</h2><div class="card"><table>
      <thead><tr><th>证照</th><th>当前状态</th><th>到期日</th><th>责任人</th><th></th></tr></thead>
      <tbody>{cert_list}</tbody></table></div>
    {addr_form}{lost_form}"""
    return page(user, f"门店 · {store['name']}", body, "matrix", flash=flash)


def tasks_view(svc, user, tasks) -> str:
    today = svc.today()
    rows = ""
    kind_cn = {"expiring": "临期续证", "expired": "过期换证", "recheck": "重新核查"}
    for t in tasks:
        days = (parse_date(t["due_date"]) - today).days
        st = {'open': '<span class="badge badge-pending">待办</span>',
              'done': '<span class="badge badge-ok">已结案</span>',
              'cancelled': '<span class="badge">已取消</span>'}[t["status"]]
        red = ' class="red"' if (t["kind"] == "expired" or days < 0) and t["status"] == "open" else ""
        rows += (f'<tr><td>{kind_cn[t["kind"]]}</td><td>{esc(t["store_name"])}</td>'
                 f'<td>{esc(t["type_name"])}</td><td>{esc(t["due_date"])}</td>'
                 f'<td><span class="days-big{red}">{days}</span></td>'
                 f'<td>{esc(t["assignee_display"] or "门店负责人")}</td><td>{st}</td>'
                 f'<td>{"已送达" if t["notified"] else "—"}</td></tr>')
    body = f"""
    <p class="muted">提醒引擎在进程内按总部配置的提前量自动生成任务；新证审核通过后任务自动结案。</p>
    <div class="card"><table>
    <thead><tr><th>任务</th><th>门店</th><th>证照</th><th>到期日</th><th>剩余天数</th>
    <th>待办人</th><th>状态</th><th>送达</th></tr></thead><tbody>{rows}</tbody></table>
    {'<div class="empty">暂无任务</div>' if not tasks else ''}</div>"""
    return page(user, "续证任务", body, "tasks")


def corrections_view(user, items) -> str:
    rows = ""
    for k in items:
        st = ('<span class="badge badge-ok">已闭环</span>' if k["status"] == "resolved"
              else '<span class="flag amber">未闭环</span>')
        red = k["kind"] in ("rejected", "lost", "address_check", "expired")
        rows += (f'<tr><td>{esc(k["store_name"])}</td><td>{esc(k["type_name"])}</td>'
                 f'<td>V{esc(k["version_no"])}</td>'
                 f'<td><span class="flag {"red" if red else "amber"}">'
                 f'{esc(KIND_CN.get(k["kind"], k["kind"]))}</span></td>'
                 f'<td class="small">{esc(k["reason"])}</td><td>{st}</td>'
                 f'<td class="small">{esc(k["resolution"]) or "—"}</td></tr>')
    body = f"""<div class="card"><table>
    <thead><tr><th>门店</th><th>证照</th><th>版本</th><th>类型</th><th>事由</th><th>状态</th>
    <th>处理说明</th></tr></thead><tbody>{rows}</tbody></table>
    {'<div class="empty">暂无补正记录</div>' if not items else ''}</div>"""
    return page(user, "材料补正", body, "corrections")


def audits_view(user, logs) -> str:
    rows = ""
    for a in logs:
        rows += (f'<tr><td>{esc(a["created_at"])}</td><td>{esc(a["store_name"])}</td>'
                 f'<td>V{esc(a["version_no"])}</td><td>{esc(ACTION_CN.get(a["action"], a["action"]))}</td>'
                 f'<td>{esc(a["actor_display"] or "系统")}</td><td class="small">{esc(a["note"])}</td></tr>')
    body = f"""<div class="card"><table>
    <thead><tr><th>时间</th><th>门店</th><th>版本</th><th>动作</th><th>操作人</th><th>意见</th></tr></thead>
    <tbody>{rows}</tbody></table></div>"""
    return page(user, "审核记录", body, "audits")


def config_view(user, types, logs, flash=None) -> str:
    cards = ""
    for t in types:
        cards += f"""
        <div class="card"><form method="post" action="/config/type/{t['id']}">
          <b>{esc(t['name'])}</b> <span class="muted small">{esc(t['code'])}</span>
          <label class="fld" style="margin-top:8px"><b>提醒提前量（天，逗号分隔多档）</b>
          <input type="text" name="remind_days" value="{esc(t['remind_days'])}"></label>
          <div class="form-actions"><button class="btn">保存配置</button></div>
        </form></div>"""
    log_rows = "".join(
        f'<tr><td>{esc(x["created_at"])}</td><td>{esc(x["name"])}</td>'
        f'<td>{esc(x["old_days"])} → {esc(x["new_days"])}</td></tr>'
        for x in logs)
    body = f"""
    <p class="muted">总部法务配置各类证照的提醒提前量，支持多档（如 60,30,7）；变更全程留痕。</p>
    {cards}
    <h2>配置变更记录</h2><div class="card"><table>
    <thead><tr><th>时间</th><th>证照类型</th><th>提前量变化</th></tr></thead>
    <tbody>{log_rows}</tbody></table>
    {'<div class="empty">暂无变更</div>' if not logs else ''}</div>"""
    return page(user, "提醒配置", body, "config", flash=flash)

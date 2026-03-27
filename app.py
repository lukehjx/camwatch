#!/usr/bin/env python3
"""办公室摄像头智能巡查 Web 看板 - Dark Theme v2"""
from flask import Flask, render_template_string, request, session, redirect, url_for, jsonify, send_file
import sqlite3, json, os, subprocess, base64
from datetime import datetime, timedelta
import sys as _sys
_sys.path.insert(0, '/root/camwatch')
try:
    from wifi_probe import scan_wifi as _scan_wifi
    _WIFI_OK = True
except ImportError:
    _WIFI_OK = False
from functools import wraps

app = Flask(__name__)
app.secret_key = 'camwatch-sidex-2026-secret'

DB_PATH = '/root/camwatch/camwatch.db'
SNAPSHOT_DIR = '/root/camwatch/snapshots'
LOG_PATH = '/root/camwatch/camwatch.log'
CAMWATCH_SCRIPT = '/root/camwatch/camwatch.py'
CONFIG_PATH = '/root/camwatch/config.json'

def init_db():
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        check_time DATETIME NOT NULL,
        camera_name TEXT DEFAULT '办公室主镜头',
        has_people BOOLEAN,
        people_count INTEGER DEFAULT 0,
        people_desc TEXT,
        lights_on BOOLEAN,
        lights_desc TEXT,
        devices_on BOOLEAN,
        devices_desc TEXT,
        need_attention BOOLEAN,
        summary TEXT,
        snapshot_path TEXT,
        cos_url TEXT,
        raw_result TEXT
    )''')
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ─── SHARED CSS & HEAD ──────────────────────────────────────────────────────
BASE_HEAD = '''
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
:root {
  --bg-main: #0f1117;
  --bg-card: #1a1f2e;
  --bg-card2: #242938;
  --bg-hover: #2d3347;
  --border: #2e3347;
  --accent: #00d4aa;
  --accent-dim: rgba(0,212,170,0.12);
  --accent-glow: rgba(0,212,170,0.3);
  --warn: #ff6b35;
  --warn-dim: rgba(255,107,53,0.12);
  --text-primary: #e8eaf6;
  --text-secondary: #8b92a8;
  --text-muted: #525974;
  --green: #00c896;
  --green-dim: rgba(0,200,150,0.12);
  --red: #ff4757;
  --red-dim: rgba(255,71,87,0.12);
  --yellow: #ffa502;
  --yellow-dim: rgba(255,165,2,0.12);
  --blue: #3d8ef8;
  --blue-dim: rgba(61,142,248,0.12);
  --purple: #9c55f5;
  --purple-dim: rgba(156,85,245,0.12);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: var(--bg-main);
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.6;
}
a { color: inherit; text-decoration: none; }

/* ── SCROLLBAR ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg-main); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

/* ── LAYOUT ── */
.layout { display: flex; height: 100vh; overflow: hidden; }

/* ── SIDEBAR ── */
.sidebar {
  width: 240px; flex-shrink: 0;
  background: var(--bg-card);
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column;
  overflow-y: auto;
}
.sidebar-logo {
  padding: 20px 20px 16px;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 12px;
}
.logo-icon {
  width: 38px; height: 38px;
  background: linear-gradient(135deg, var(--accent) 0%, #0097a7 100%);
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px; flex-shrink: 0;
  box-shadow: 0 4px 12px var(--accent-glow);
}
.logo-text { font-size: 13px; font-weight: 600; color: var(--text-primary); line-height: 1.3; }
.logo-sub { font-size: 11px; color: var(--text-muted); }
.sidebar-nav { padding: 12px 10px; flex: 1; }
.nav-section { font-size: 10px; font-weight: 600; color: var(--text-muted); letter-spacing: 0.08em; text-transform: uppercase; padding: 8px 10px 4px; }
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 12px; border-radius: 8px;
  color: var(--text-secondary); font-size: 13px; font-weight: 500;
  transition: all 0.15s; cursor: pointer; margin-bottom: 2px;
}
.nav-item:hover { background: var(--bg-hover); color: var(--text-primary); }
.nav-item.active {
  background: var(--accent-dim);
  color: var(--accent);
  box-shadow: inset 3px 0 0 var(--accent);
}
.nav-item i { width: 16px; text-align: center; font-size: 13px; }
.sidebar-footer { padding: 12px 10px; border-top: 1px solid var(--border); }

/* ── TOPBAR ── */
.topbar {
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  padding: 14px 24px;
  display: flex; align-items: center; justify-content: space-between;
  flex-shrink: 0;
}
.topbar-title { font-size: 16px; font-weight: 600; color: var(--text-primary); }
.topbar-meta { display: flex; align-items: center; gap: 16px; }
.clock { font-size: 12px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.status-dot { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); box-shadow: 0 0 8px var(--green); animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

/* ── MAIN CONTENT ── */
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.content { flex: 1; overflow-y: auto; padding: 24px; }

/* ── CARDS ── */
.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.card-title { font-size: 13px; font-weight: 600; color: var(--text-secondary); margin-bottom: 16px; display: flex; align-items: center; gap-8px; }
.card-title i { color: var(--accent); margin-right: 8px; }

/* ── STAT CARDS ── */
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap: 16px; margin-bottom: 24px; }
.stat-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  position: relative; overflow: hidden;
  transition: transform 0.2s, box-shadow 0.2s;
}
.stat-card:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.3); }
.stat-card::before { content:''; position:absolute; inset:0; border-radius:12px; opacity:0; transition:opacity 0.2s; }
.stat-card.accent::before { background: linear-gradient(135deg, var(--accent-dim), transparent); }
.stat-card.warn::before { background: linear-gradient(135deg, var(--warn-dim), transparent); }
.stat-card.green::before { background: linear-gradient(135deg, var(--green-dim), transparent); }
.stat-card.purple::before { background: linear-gradient(135deg, var(--purple-dim), transparent); }
.stat-card:hover::before { opacity: 1; }
.stat-icon { font-size: 22px; margin-bottom: 12px; }
.stat-value { font-size: 32px; font-weight: 700; line-height: 1; margin-bottom: 6px; }
.stat-value.accent { color: var(--accent); }
.stat-value.warn { color: var(--warn); }
.stat-value.green { color: var(--green); }
.stat-value.purple { color: var(--purple); }
.stat-label { font-size: 12px; color: var(--text-muted); }
.stat-change { font-size: 11px; color: var(--text-muted); margin-top: 4px; }

/* ── STATUS HERO ── */
.status-hero {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 24px;
  display: flex; align-items: center; gap: 20px;
}
.status-emoji { font-size: 48px; line-height: 1; }
.status-info { flex: 1; }
.status-title { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
.status-sub { font-size: 13px; color: var(--text-secondary); }
.status-time { font-size: 12px; color: var(--text-muted); margin-top: 6px; }
.status-badges { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }

/* ── BADGE ── */
.badge {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 4px 10px; border-radius: 20px;
  font-size: 11px; font-weight: 600;
}
.badge-green { background: var(--green-dim); color: var(--green); border: 1px solid rgba(0,200,150,0.25); }
.badge-red { background: var(--red-dim); color: var(--red); border: 1px solid rgba(255,71,87,0.25); }
.badge-warn { background: var(--warn-dim); color: var(--warn); border: 1px solid rgba(255,107,53,0.25); }
.badge-yellow { background: var(--yellow-dim); color: var(--yellow); border: 1px solid rgba(255,165,2,0.25); }
.badge-blue { background: var(--blue-dim); color: var(--blue); border: 1px solid rgba(61,142,248,0.25); }
.badge-gray { background: rgba(255,255,255,0.06); color: var(--text-muted); border: 1px solid var(--border); }
.badge-accent { background: var(--accent-dim); color: var(--accent); border: 1px solid rgba(0,212,170,0.25); }

/* ── BUTTONS ── */
.btn {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 9px 18px; border-radius: 8px;
  font-size: 13px; font-weight: 600;
  cursor: pointer; border: none; transition: all 0.15s;
}
.btn-primary {
  background: var(--accent); color: #0a1a16;
  box-shadow: 0 4px 12px var(--accent-glow);
}
.btn-primary:hover { background: #00e8bb; box-shadow: 0 6px 20px var(--accent-glow); transform: translateY(-1px); }
.btn-secondary { background: var(--bg-card2); color: var(--text-primary); border: 1px solid var(--border); }
.btn-secondary:hover { background: var(--bg-hover); }
.btn-danger { background: var(--red-dim); color: var(--red); border: 1px solid rgba(255,71,87,0.3); }
.btn-danger:hover { background: rgba(255,71,87,0.2); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none !important; }

/* ── GRID LAYOUTS ── */
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; }
.grid-auto { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px,1fr)); gap: 20px; }
.col-span-2 { grid-column: span 2; }

/* ── TABLE ── */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th { font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); }
td { padding: 12px 14px; border-bottom: 1px solid rgba(255,255,255,0.04); color: var(--text-secondary); font-size: 13px; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(255,255,255,0.02); }

/* ── HISTORY CARDS ── */
.history-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  display: flex; gap: 16px; align-items: flex-start;
  transition: all 0.15s; margin-bottom: 12px;
}
.history-card:hover { border-color: var(--accent); box-shadow: 0 4px 20px rgba(0,212,170,0.08); }
.history-thumb {
  width: 120px; height: 80px;
  border-radius: 8px; overflow: hidden; flex-shrink: 0;
  background: var(--bg-card2); position: relative;
}
.history-thumb img { width: 100%; height: 100%; object-fit: cover; cursor: pointer; transition: transform 0.2s; }
.history-thumb img:hover { transform: scale(1.05); }
.history-thumb .no-img { width:100%; height:100%; display:flex; align-items:center; justify-content:center; color: var(--text-muted); font-size:12px; flex-direction:column; gap:4px; }
.history-body { flex: 1; min-width: 0; }
.history-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.history-time { font-size: 12px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.history-camera { font-size: 11px; color: var(--accent); background: var(--accent-dim); padding: 2px 8px; border-radius: 4px; }
.history-summary { font-size: 13px; color: var(--text-primary); line-height: 1.5; margin-bottom: 8px; }
.history-details { display: flex; gap: 6px; flex-wrap: wrap; }

/* ── SNAPSHOT IMG ── */
.snap-container { position: relative; border-radius: 10px; overflow: hidden; background: var(--bg-card2); }
.snap-container img { width: 100%; display: block; cursor: zoom-in; }
.snap-overlay { position: absolute; bottom: 0; left: 0; right: 0; padding: 8px 12px; background: linear-gradient(transparent, rgba(0,0,0,0.7)); font-size: 11px; color: rgba(255,255,255,0.7); }

/* ── LIVE RESULT ── */
.result-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 16px; }
.result-item { background: var(--bg-card2); border-radius: 8px; padding: 12px 14px; }
.result-item-label { font-size: 11px; color: var(--text-muted); margin-bottom: 4px; }
.result-item-value { font-size: 14px; font-weight: 600; }

/* ── LOG PRE ── */
.log-pre {
  background: #0a0c12; border: 1px solid var(--border); border-radius: 8px;
  padding: 16px; font-family: 'Fira Mono','Consolas',monospace; font-size: 11px;
  color: #a0e8b0; line-height: 1.7; overflow: auto; max-height: 400px;
  white-space: pre-wrap; word-break: break-all;
}

/* ── FORM ── */
.form-input {
  background: var(--bg-card2); border: 1px solid var(--border); color: var(--text-primary);
  border-radius: 8px; padding: 9px 14px; font-size: 13px; width: 100%;
  transition: border-color 0.15s; outline: none;
}
.form-input:focus { border-color: var(--accent); }
.form-label { font-size: 12px; color: var(--text-secondary); font-weight: 500; margin-bottom: 6px; display: block; }

/* ── PAGINATION ── */
.pagination { display: flex; gap: 6px; justify-content: center; margin-top: 20px; }
.page-btn {
  min-width: 32px; height: 32px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--bg-card2); color: var(--text-secondary); font-size: 13px;
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  transition: all 0.15s; padding: 0 8px;
}
.page-btn:hover { border-color: var(--accent); color: var(--accent); }
.page-btn.active { background: var(--accent); color: #0a1a16; border-color: var(--accent); font-weight: 600; }

/* ── DIVIDER ── */
.divider { border: none; border-top: 1px solid var(--border); margin: 20px 0; }

/* ── TOAST ── */
.toast {
  position: fixed; bottom: 24px; right: 24px; z-index: 999;
  background: var(--bg-card2); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 18px; font-size: 13px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  display: flex; align-items: center; gap: 10px;
  transform: translateY(20px); opacity: 0; transition: all 0.3s;
}
.toast.show { transform: translateY(0); opacity: 1; }
.toast.success { border-color: rgba(0,200,150,0.4); color: var(--green); }
.toast.error { border-color: rgba(255,71,87,0.4); color: var(--red); }

/* ── CHART CONTAINERS ── */
.chart-wrap { position: relative; height: 200px; }
.chart-wrap-sm { position: relative; height: 140px; }

/* ── LOADING SPINNER ── */
.spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid rgba(0,212,170,0.3); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ── MODAL ── */
.modal-bg { position: fixed; inset: 0; background: rgba(0,0,0,0.8); z-index: 1000; display: flex; align-items: center; justify-content: center; opacity: 0; pointer-events: none; transition: opacity 0.2s; }
.modal-bg.open { opacity: 1; pointer-events: all; }
.modal-box { background: var(--bg-card); border: 1px solid var(--border); border-radius: 16px; max-width: 90vw; max-height: 90vh; overflow: auto; }

/* ── FAB (Floating Action Button) ── */
.fab {
  position: fixed; bottom: 28px; right: 28px; z-index: 100;
  width: 52px; height: 52px; border-radius: 50%; border: none;
  background: var(--accent); color: #0a1a16; font-size: 20px;
  box-shadow: 0 6px 20px var(--accent-glow); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.2s;
}
.fab:hover { transform: scale(1.1); box-shadow: 0 8px 28px var(--accent-glow); }
.fab-label {
  position: fixed; bottom: 36px; right: 88px; z-index: 100;
  background: var(--bg-card2); border: 1px solid var(--border);
  color: var(--text-primary); padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500;
  opacity: 0; pointer-events: none; transition: opacity 0.2s; white-space: nowrap;
}
.fab:hover + .fab-label { opacity: 1; }

/* ── RESPONSIVE ── */
@media (max-width: 768px) {
  .sidebar { display: none; }
  .grid-2, .grid-3 { grid-template-columns: 1fr; }
  .col-span-2 { grid-column: span 1; }
  .stats-grid { grid-template-columns: repeat(2,1fr); }
  .history-thumb { width: 80px; height: 56px; }
  .content { padding: 16px; }
  .result-grid { grid-template-columns: 1fr; }
}
@media (max-width: 400px) {
  .stats-grid { grid-template-columns: 1fr; }
}
</style>
'''
# ─── NAV TEMPLATE ───────────────────────────────────────────────────────────
def make_page(title, content, active='', extra_head=''):
    nav_items = [
        ('dashboard', '/', 'fa-gauge-high', '仪表盘'),
        ('history', '/history', 'fa-clock-rotate-left', '历史记录'),
        ('stats', '/stats', 'fa-chart-line', '统计分析'),
        ('live', '/live', 'fa-video', '实时截图'),
        ('settings', '/settings', 'fa-sliders', '系统设置'),
    ]
    nav_html = ''
    for key, href, icon, label in nav_items:
        cls = 'active' if active == key else ''
        nav_html += f'<a href="{href}" class="nav-item {cls}"><i class="fa-solid {icon}"></i>{label}</a>'

    _h = ('<!DOCTYPE html>' +
          '<html lang="zh-CN">' +
          '<head>' +
          '<title>' + title + ' · 巡查系统</title>' +
          BASE_HEAD + extra_head +
          '</head>')
    return _h + f'''
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-logo">
      <div class="logo-icon">📹</div>
      <div>
        <div class="logo-text">巡查监控</div>
        <div class="logo-sub">智能摄像头系统</div>
      </div>
    </div>
    <nav class="sidebar-nav">
      <div class="nav-section">导航</div>
      {nav_html}
    </nav>
    <div class="sidebar-footer">
      <a href="/logout" class="nav-item" style="color:var(--red)"><i class="fa-solid fa-right-from-bracket"></i>退出登录</a>
    </div>
  </aside>
  <div class="main">
    <div class="topbar">
      <div class="topbar-title">{title}</div>
      <div class="topbar-meta">
        <div class="status-dot"><div class="dot"></div>系统运行中</div>
        <div class="clock" id="clock">--:--:--</div>
      </div>
    </div>
    <div class="content">
      {content}
    </div>
  </div>
</div>
<div id="toast" class="toast"></div>
<script>
function updateClock(){{
  const now = new Date();
  document.getElementById('clock').textContent = now.toLocaleString('zh-CN', {{hour12:false}});
}}
setInterval(updateClock, 1000); updateClock();
function showToast(msg, type='success') {{
  const t = document.getElementById('toast');
  t.className = 'toast show ' + type;
  t.innerHTML = (type==='success'?'✅ ':'❌ ') + msg;
  setTimeout(()=>{{ t.className='toast'; }}, 3500);
}}
</script>
</body></html>'''
# ─── LOGIN ──────────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET','POST'])
def login():
    error = ''
    if request.method == 'POST':
        if request.form.get('username') == 'sidex' and request.form.get('password') == 'sidex@123':
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        error = '用户名或密码错误'
    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<title>登录 · 巡查系统</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;background:#0f1117;display:flex;align-items:center;justify-content:center;min-height:100vh;}}
.login-wrap{{width:380px;padding:20px;}}
.login-logo{{text-align:center;margin-bottom:32px;}}
.login-logo .icon{{width:64px;height:64px;background:linear-gradient(135deg,#00d4aa,#0097a7);border-radius:18px;display:flex;align-items:center;justify-content:center;font-size:30px;margin:0 auto 14px;box-shadow:0 8px 24px rgba(0,212,170,0.35);}}
.login-logo h1{{font-size:22px;font-weight:700;color:#e8eaf6;}}
.login-logo p{{font-size:13px;color:#525974;margin-top:4px;}}
.login-card{{background:#1a1f2e;border:1px solid #2e3347;border-radius:16px;padding:32px;}}
.err{{background:rgba(255,71,87,0.12);border:1px solid rgba(255,71,87,0.3);color:#ff4757;border-radius:8px;padding:10px 14px;font-size:13px;margin-bottom:18px;}}
.field{{margin-bottom:18px;}}
.field label{{display:block;font-size:12px;font-weight:500;color:#8b92a8;margin-bottom:6px;}}
.field input{{width:100%;background:#242938;border:1px solid #2e3347;color:#e8eaf6;border-radius:8px;padding:11px 14px;font-size:14px;outline:none;transition:border-color .15s;}}
.field input:focus{{border-color:#00d4aa;}}
.field input::placeholder{{color:#525974;}}
.btn-login{{width:100%;background:#00d4aa;color:#0a1a16;font-size:14px;font-weight:700;padding:12px;border-radius:8px;border:none;cursor:pointer;transition:all .2s;box-shadow:0 4px 16px rgba(0,212,170,0.3);margin-top:6px;}}
.btn-login:hover{{background:#00e8bb;box-shadow:0 6px 24px rgba(0,212,170,0.45);}}
.hint{{text-align:center;margin-top:20px;font-size:12px;color:#525974;}}
</style>
</head>
<body>
<div class="login-wrap">
  <div class="login-logo">
    <div class="icon">📹</div>
    <h1>办公室巡查系统</h1>
    <p>智能摄像头监控平台</p>
  </div>
  <div class="login-card">
    {'<div class="err"><i class="fa fa-circle-exclamation"></i> ' + error + '</div>' if error else ''}
    <form method="POST">
      <div class="field">
        <label>用 户 名</label>
        <input name="username" placeholder="请输入用户名" autocomplete="username">
      </div>
      <div class="field">
        <label>密 码</label>
        <input name="password" type="password" placeholder="请输入密码" autocomplete="current-password">
      </div>
      <button type="submit" class="btn-login"><i class="fa fa-lock"></i>&nbsp; 登 录</button>
    </form>
    <div class="hint">Powered by 旺财智能巡查</div>
  </div>
</div>
</body></html>'''

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─── DASHBOARD ──────────────────────────────────────────────────────────────
@app.route('/')
@login_required
def dashboard():
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime('%Y-%m-%d')
    today_count  = db.execute("SELECT COUNT(*) FROM checks WHERE date(check_time)=?", (today,)).fetchone()[0]
    people_count = db.execute("SELECT COUNT(*) FROM checks WHERE date(check_time)=? AND has_people=1", (today,)).fetchone()[0]
    attn_count   = db.execute("SELECT COUNT(*) FROM checks WHERE date(check_time)=? AND need_attention=1", (today,)).fetchone()[0]
    total_count  = db.execute("SELECT COUNT(*) FROM checks").fetchone()[0]
    latest = db.execute("SELECT * FROM checks ORDER BY check_time DESC LIMIT 1").fetchone()
    # 7天趋势
    days7 = [(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6, -1, -1)]
    trend_people = []
    trend_empty  = []
    for d in days7:
        p = db.execute("SELECT COUNT(*) FROM checks WHERE date(check_time)=? AND has_people=1", (d,)).fetchone()[0]
        e = db.execute("SELECT COUNT(*) FROM checks WHERE date(check_time)=? AND has_people=0", (d,)).fetchone()[0]
        trend_people.append(p)
        trend_empty.append(e)
    db.close()

    # 最新截图
    snap_html = '<div style="height:180px;display:flex;align-items:center;justify-content:center;color:var(--text-muted);flex-direction:column;gap:8px;"><i class="fa fa-image" style="font-size:32px;opacity:0.3"></i><div>暂无截图</div></div>'
    snap_cos_url = ''
    if latest:
        # 优先用COS URL
        if latest['cos_url'] if 'cos_url' in latest.keys() else None:
            snap_cos_url = latest['cos_url']
            snap_html = f'<div class="snap-container"><img src="{snap_cos_url}" onclick="openModal(this.src)" alt="最新截图" loading="lazy"><div class="snap-overlay">最新截图 · 点击放大</div></div>'
        elif latest['snapshot_path'] and os.path.exists(latest['snapshot_path']):
            with open(latest['snapshot_path'],'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            snap_html = f'<div class="snap-container"><img src="data:image/jpeg;base64,{b64}" onclick="openModal(this.src)" alt="最新截图"><div class="snap-overlay">最新截图 · 点击放大</div></div>'

    # 状态展示
    if latest:
        if latest['has_people']:
            status_emoji, status_title, status_color = '🚶', '检测到有人', 'var(--warn)'
        else:
            status_emoji, status_title, status_color = '✅', '无人，已下班', 'var(--green)'
        if latest['need_attention']:
            status_title = '⚠️ 需要关注'
            status_color = 'var(--red)'
        summary_text = latest['summary'] or '暂无摘要'
        check_time   = latest['check_time'][:16]
        badges_html  = ''
        if latest['has_people'] is not None:
            badges_html += f'<span class="badge badge-{"warn" if latest["has_people"] else "green"}"><i class="fa fa-person"></i>{"有人" if latest["has_people"] else "无人"}</span>'
        if latest['lights_on'] is not None:
            badges_html += f'<span class="badge badge-{"yellow" if latest["lights_on"] else "green"}"><i class="fa fa-lightbulb"></i>{"灯光开" if latest["lights_on"] else "灯光关"}</span>'
        if latest['devices_on'] is not None:
            badges_html += f'<span class="badge badge-{"blue" if latest["devices_on"] else "green"}"><i class="fa fa-desktop"></i>{"设备运行" if latest["devices_on"] else "设备关"}</span>'
        if latest['need_attention']:
            badges_html += '<span class="badge badge-red"><i class="fa fa-triangle-exclamation"></i>需关注</span>'
    else:
        status_emoji, status_title, status_color = '⚪', '暂无巡查数据', 'var(--text-muted)'
        summary_text = '系统刚刚启动，尚未进行巡查'
        check_time   = '-'
        badges_html  = ''

    days7_labels = json.dumps([d[5:] for d in days7])

    content = f'''
<div class="stats-grid">
  <div class="stat-card accent">
    <div class="stat-icon">📊</div>
    <div class="stat-value accent">{today_count}</div>
    <div class="stat-label">今日巡查次数</div>
    <div class="stat-change">累计 {total_count} 次</div>
  </div>
  <div class="stat-card warn">
    <div class="stat-icon">🚶</div>
    <div class="stat-value warn">{people_count}</div>
    <div class="stat-label">今日发现人员</div>
    <div class="stat-change">次</div>
  </div>
  <div class="stat-card" style="--card-color:var(--red)">
    <div class="stat-icon">⚠️</div>
    <div class="stat-value" style="color:var(--red)">{attn_count}</div>
    <div class="stat-label">今日需关注</div>
    <div class="stat-change">次</div>
  </div>
  <div class="stat-card purple" id="wifi-card" style="cursor:pointer" onclick="refreshWifi()" title="点击刷新">
    <div class="stat-icon">📶</div>
    <div class="stat-value purple" id="wifi-count">--</div>
    <div class="stat-label">WiFi在线设备</div>
    <div class="stat-change" id="wifi-sub" style="font-size:11px">加载中...</div>
  </div>
</div>
<script>
function refreshWifi() {{
  document.getElementById('wifi-sub').textContent = '扫描中...';
  fetch('/api/wifi_scan?cache=1').then(r=>r.json()).then(function(d) {{
    if(d.ok) {{
      document.getElementById('wifi-count').textContent = d.estimated_people;
      var sub = d.from_cache ? '缓存 '+d.cache_age_seconds+'s 前' : '实时扫描';
      document.getElementById('wifi-sub').textContent = '总在线 '+d.total_online+' 台 · '+sub;
    }} else {{
      document.getElementById('wifi-count').textContent = '?';
      document.getElementById('wifi-sub').textContent = d.msg || '探针不可用';
    }}
  }}).catch(function() {{
    document.getElementById('wifi-count').textContent = '?';
    document.getElementById('wifi-sub').textContent = '请求失败';
  }});
}}
refreshWifi();
setInterval(refreshWifi, 120000);
</script>

<div class="status-hero">
  <div class="status-emoji">{status_emoji}</div>
  <div class="status-info">
    <div class="status-title" style="color:{status_color}">{status_title}</div>
    <div class="status-sub">{summary_text}</div>
    <div class="status-time"><i class="fa fa-clock" style="margin-right:4px"></i>最近巡查：{check_time}</div>
    <div class="status-badges">{badges_html}</div>
  </div>
  <a href="/live" class="btn btn-primary" style="flex-shrink:0"><i class="fa fa-camera"></i>立即巡查</a>
</div>

<div class="grid-2">
  <div class="card">
    <div class="card-title"><i class="fa fa-image"></i>最新截图</div>
    {snap_html}
  </div>
  <div class="card">
    <div class="card-title"><i class="fa fa-chart-line"></i>近7天趋势</div>
    <div class="chart-wrap"><canvas id="trendChart"></canvas></div>
  </div>
</div>

<!-- 图片放大 Modal -->
<div class="modal-bg" id="imgModal" onclick="closeModal()">
  <div class="modal-box" style="padding:8px">
    <img id="modalImg" src="" style="max-width:80vw;max-height:80vh;display:block;border-radius:8px">
  </div>
</div>

<script>
function openModal(src) {{
  document.getElementById('modalImg').src = src;
  document.getElementById('imgModal').classList.add('open');
}}
function closeModal() {{
  document.getElementById('imgModal').classList.remove('open');
}}

const ctx = document.getElementById('trendChart').getContext('2d');
new Chart(ctx, {{
  type: 'line',
  data: {{
    labels: {days7_labels},
    datasets: [
      {{
        label: '有人',
        data: {json.dumps(trend_people)},
        borderColor: '#ff6b35',
        backgroundColor: 'rgba(255,107,53,0.1)',
        fill: true, tension: 0.4, pointRadius: 4,
        pointBackgroundColor: '#ff6b35',
      }},
      {{
        label: '无人',
        data: {json.dumps(trend_empty)},
        borderColor: '#00d4aa',
        backgroundColor: 'rgba(0,212,170,0.08)',
        fill: true, tension: 0.4, pointRadius: 4,
        pointBackgroundColor: '#00d4aa',
      }}
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ color: '#8b92a8', font: {{ size: 11 }} }} }} }},
    scales: {{
      x: {{ grid: {{ color: '#1e2433' }}, ticks: {{ color: '#525974', font: {{ size: 11 }} }} }},
      y: {{ grid: {{ color: '#1e2433' }}, ticks: {{ color: '#525974', font: {{ size: 11 }}, stepSize: 1 }}, beginAtZero: true }}
    }}
  }}
}});
</script>
'''
    return make_page('🏠 仪表盘', content, 'dashboard')
# ─── HISTORY ────────────────────────────────────────────────────────────────
@app.route('/history')
@login_required
def history():
    date_filter = request.args.get('date', '')
    attn_filter = request.args.get('attn', '')
    page = int(request.args.get('page', 1))
    per_page = 12
    offset = (page - 1) * per_page
    db = get_db()
    wheres, params = [], []
    if date_filter:
        wheres.append("date(check_time)=?"); params.append(date_filter)
    if attn_filter == '1':
        wheres.append("need_attention=1")
    elif attn_filter == '0':
        wheres.append("(need_attention=0 OR need_attention IS NULL)")
    where = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    params_t = tuple(params)
    total = db.execute(f"SELECT COUNT(*) FROM checks {where}", params_t).fetchone()[0]
    rows  = db.execute(f"SELECT * FROM checks {where} ORDER BY check_time DESC LIMIT ? OFFSET ?",
                       params_t + (per_page, offset)).fetchall()
    db.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    def mk_url(p=page, d=date_filter, a=attn_filter):
        return f"/history?page={p}&date={d}&attn={a}"

    cards_html = ''
    for r in rows:
        # 图片：优先 COS URL
        thumb_html = ''
        cos_url = r['cos_url'] if 'cos_url' in r.keys() else None
        if cos_url:
            thumb_html = f'<img src="{cos_url}" onclick="openModal(\'{cos_url}\')" alt="截图" loading="lazy">'
        elif r['snapshot_path'] and os.path.exists(r['snapshot_path']):
            with open(r['snapshot_path'],'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            thumb_html = f'<img src="data:image/jpeg;base64,{b64}" onclick="openModal(this.src)" alt="截图">'
        else:
            thumb_html = '<div class="no-img"><i class="fa fa-image" style="font-size:20px;opacity:.3"></i><span>无图</span></div>'

        # badges
        if r['has_people'] is None and r['summary'] and ('失败' in (r['summary'] or '') or '离线' in (r['summary'] or '') or '异常' in (r['summary'] or '')):
            status_badge = '<span class="badge badge-gray"><i class="fa fa-circle-xmark"></i>截帧失败</span>'
        elif r['need_attention']:
            status_badge = '<span class="badge badge-red"><i class="fa fa-triangle-exclamation"></i>需关注</span>'
        elif r['has_people']:
            status_badge = '<span class="badge badge-warn"><i class="fa fa-person"></i>有人</span>'
        else:
            status_badge = '<span class="badge badge-green"><i class="fa fa-circle-check"></i>正常</span>'

        lights_badge  = f'<span class="badge badge-{"yellow" if r["lights_on"] else "green"}">{"💡灯光开" if r["lights_on"] else "🌙灯光关"}</span>' if r['lights_on'] is not None else ''
        devices_badge = f'<span class="badge badge-{"blue" if r["devices_on"] else "green"}">{"🖥️设备开" if r["devices_on"] else "✅设备关"}</span>' if r['devices_on'] is not None else ''
        camera_name = r['camera_name'] if 'camera_name' in r.keys() and r['camera_name'] else '办公室主镜头'

        behavior_html = ''
        conf_badge = ''
        night_badge = ''
        last_status_html = ''
        try:
            raw = json.loads(r['raw_result'] or '{}')
            # behavior: 优先从 DB 列，回退 raw_result
            beh_col = ''
            try:
                beh_col = r['behavior'] or ''
            except:
                pass
            behavior = beh_col or raw.get('behavior') or raw.get('people_desc') or ''
            if behavior and behavior != (r['summary'] or ''):
                behavior_html = ('<div style="font-size:11px;color:var(--text-muted);margin-top:3px;font-style:italic">🎯 行为：' + behavior[:80] + '</div>')
            conf = raw.get('body_confidence', '')
            if conf == 'high':
                conf_badge = '<span class="badge badge-green" style="font-size:10px">✅人员确认</span>'
            elif conf == 'low':
                conf_badge = '<span class="badge badge-warn" style="font-size:10px">⚠️人员待确认</span>'
            # 夜间闯入标注
            if raw.get('night_alert'):
                night_badge = '<span class="badge badge-red" style="font-size:10px">🚨夜间闯入</span>'
            # 历史对比
            last_s = raw.get('last_summary', '')
            if last_s:
                last_status_html = f'<div style="font-size:11px;color:var(--text-muted);margin-top:3px">📂 上次状态：{last_s[:40]}</div>'
        except:
            pass

        cards_html += f'''
<div class="history-card">
  <div class="history-thumb">{thumb_html}</div>
  <div class="history-body">
    <div class="history-meta">
      <span class="history-time"><i class="fa fa-clock"></i> {r["check_time"][:16]}</span>
      <span class="history-camera">{camera_name}</span>
      {status_badge}{conf_badge}{night_badge}
    </div>
    <div class="history-summary">{r["summary"] or "暂无摘要"}</div>
    {behavior_html}
    <div class="history-details">
      {lights_badge}{devices_badge}
      {'<span class="badge badge-gray"><i class="fa fa-users"></i>' + str(r["people_count"]) + ' 人</span>' if r["people_count"] else ''}
    </div>
  </div>
</div>'''

    if not cards_html:
        cards_html = '<div style="text-align:center;padding:60px 0;color:var(--text-muted)"><i class="fa fa-inbox" style="font-size:36px;display:block;margin-bottom:12px;opacity:.3"></i>暂无记录</div>'

    # 分页
    pag = ''
    if page > 1:
        pag += f'<a href="{mk_url(page-1)}" class="page-btn"><i class="fa fa-chevron-left"></i></a>'
    for p in range(max(1,page-2), min(total_pages,page+2)+1):
        pag += f'<a href="{mk_url(p)}" class="page-btn {"active" if p==page else ""}">{p}</a>'
    if page < total_pages:
        pag += f'<a href="{mk_url(page+1)}" class="page-btn"><i class="fa fa-chevron-right"></i></a>'

    content = f'''
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:12px">
  <div style="display:flex;align-items:center;gap:10px">
    <input type="date" value="{date_filter}" class="form-input" style="width:180px"
      onchange="window.location='/history?date='+this.value+'&attn={attn_filter}'">
    <a href="/history?page=1&date={date_filter}&attn=" class="btn {'btn-primary' if attn_filter=='' else 'btn-secondary'}" style="padding:6px 12px;font-size:12px">全部</a>
    <a href="/history?page=1&date={date_filter}&attn=1" class="btn {'btn-primary' if attn_filter=='1' else 'btn-secondary'}" style="padding:6px 12px;font-size:12px">🔴异常</a>
    <a href="/history?page=1&date={date_filter}&attn=0" class="btn {'btn-primary' if attn_filter=='0' else 'btn-secondary'}" style="padding:6px 12px;font-size:12px">🟢正常</a>
    {'<a href="/history" class="btn btn-secondary" style="padding:8px 14px">清除</a>' if date_filter else ''}
    <span style="color:var(--text-muted);font-size:12px">共 {total} 条</span>
  </div>
  <a href="/live" class="btn btn-primary"><i class="fa fa-camera"></i>立即巡查</a>
</div>

{cards_html}
<div class="pagination">{pag}</div>

<div class="modal-bg" id="imgModal" onclick="closeModal()">
  <div class="modal-box" style="padding:8px">
    <img id="modalImg" src="" style="max-width:86vw;max-height:86vh;display:block;border-radius:8px">
  </div>
</div>
<script>
function openModal(src){{document.getElementById('modalImg').src=src;document.getElementById('imgModal').classList.add('open');}}
function closeModal(){{document.getElementById('imgModal').classList.remove('open');}}
</script>
'''
    return make_page('📋 历史记录', content, 'history')

# ─── STATS ──────────────────────────────────────────────────────────────────
@app.route('/stats')
@login_required
def stats():
    db = get_db()
    days14 = [(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(13,-1,-1)]
    days7  = [(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6,-1,-1)]
    trend_p, trend_e = [], []
    for d in days14:
        p = db.execute("SELECT COUNT(*) FROM checks WHERE date(check_time)=? AND has_people=1",(d,)).fetchone()[0]
        e = db.execute("SELECT COUNT(*) FROM checks WHERE date(check_time)=? AND has_people=0",(d,)).fetchone()[0]
        trend_p.append(p); trend_e.append(e)
    forget = []
    for d in days7:
        c = db.execute("SELECT COUNT(*) FROM checks WHERE date(check_time)=? AND has_people=0 AND (lights_on=1 OR devices_on=1)",(d,)).fetchone()[0]
        forget.append(c)
    total = db.execute("SELECT COUNT(*) FROM checks").fetchone()[0]
    people_total = db.execute("SELECT COUNT(*) FROM checks WHERE has_people=1").fetchone()[0]
    attn_total   = db.execute("SELECT COUNT(*) FROM checks WHERE need_attention=1").fetchone()[0]
    fail_total   = db.execute("SELECT COUNT(*) FROM checks WHERE has_people IS NULL").fetchone()[0]
    cam_energy = db.execute(
        "SELECT camera_name, COUNT(*) cnt FROM checks WHERE has_people=0 AND lights_on=1 GROUP BY camera_name ORDER BY cnt DESC LIMIT 8"
    ).fetchall()
    hour_people = db.execute(
        "SELECT strftime('%H',check_time) hr, ROUND(AVG(CASE WHEN has_people=1 THEN 1.0 ELSE 0 END)*100,1) pct "
        "FROM checks WHERE check_time >= datetime('now','-30 days') GROUP BY hr ORDER BY hr"
    ).fetchall()
    db.close()
    hour_labels = [r[0]+':00' for r in hour_people]
    hour_data   = [float(r[1] or 0) for r in hour_people]
    cam_el = [r[0] for r in cam_energy]
    cam_ed = [r[1] for r in cam_energy]

    content = f'''
<div class="stats-grid" style="margin-bottom:24px">
  <div class="stat-card accent"><div class="stat-icon">📊</div><div class="stat-value accent">{total}</div><div class="stat-label">总巡查次数</div></div>
  <div class="stat-card warn"><div class="stat-icon">🚶</div><div class="stat-value warn">{people_total}</div><div class="stat-label">有人次数</div></div>
  <div class="stat-card" style=""><div class="stat-icon">⚠️</div><div class="stat-value" style="color:var(--red)">{attn_total}</div><div class="stat-label">需关注次数</div></div>
  <div class="stat-card"><div class="stat-icon">❌</div><div class="stat-value" style="color:var(--text-muted)">{fail_total}</div><div class="stat-label">截帧失败次数</div></div>
</div>

<div class="card" style="margin-bottom:20px">
  <div class="card-title"><i class="fa fa-chart-line"></i>近14天巡查趋势</div>
  <div class="chart-wrap"><canvas id="trendChart"></canvas></div>
</div>

<div class="grid-2" style="margin-bottom:20px">
  <div class="card">
    <div class="card-title"><i class="fa fa-bolt"></i>能耗异常（无人灯亮次数/摄像头）</div>
    <div style="font-size:11px;color:var(--text-muted);margin-bottom:6px">各区域忘记关灯的历史次数</div>
    <div class="chart-wrap-sm"><canvas id="energyChart"></canvas></div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fa fa-clock"></i>各时段有人概率基线（近30天）</div>
    <div style="font-size:11px;color:var(--text-muted);margin-bottom:6px">用于判断某时段有人是否异常</div>
    <div class="chart-wrap-sm"><canvas id="baselineChart"></canvas></div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <div class="card-title"><i class="fa fa-triangle-exclamation"></i>近7天设备遗忘（无人但灯/设备未关）</div>
    <div class="chart-wrap-sm"><canvas id="forgetChart"></canvas></div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fa fa-chart-pie"></i>有人 vs 无人 占比</div>
    <div class="chart-wrap-sm" style="display:flex;align-items:center;justify-content:center"><canvas id="pieChart" style="max-height:140px"></canvas></div>
  </div>
</div>

<!-- 基线分析 Tab -->
<div class="card" style="margin-bottom:20px">
  <div class="card-title"><i class="fa fa-chart-bar"></i>基线分析：各摄像头每小时历史平均人数</div>
  <div style="font-size:11px;color:var(--text-muted);margin-bottom:10px">数据越多分析越准确</div>
  <div class="chart-wrap" style="height:240px"><canvas id="baselinePerCamChart"></canvas></div>
</div>

<!-- 能耗统计 -->
<div class="card" style="margin-bottom:20px">
  <div class="card-title"><i class="fa fa-bolt"></i>能耗统计：本月各区域无人亮灯次数</div>
  <div style="font-size:11px;color:var(--text-muted);margin-bottom:10px">触发预警阈值：20次/月</div>
  <div class="chart-wrap-sm"><canvas id="monthEnergyChart"></canvas></div>
</div>

<!-- 周报预览 -->
<div class="card" style="margin-bottom:20px">
  <div class="card-title"><i class="fa fa-calendar-week"></i>近7天周报趋势</div>
  <div style="display:flex;gap:10px;margin-bottom:12px">
    <button class="btn btn-primary" style="font-size:12px;padding:6px 14px" onclick="sendWeeklyReport()"><i class="fa fa-paper-plane"></i>立即发送周报</button>
    <span id="weeklyMsg" style="font-size:12px;color:var(--text-muted);line-height:32px"></span>
  </div>
  <div class="chart-wrap-sm"><canvas id="weeklyTrendChart"></canvas></div>
</div>

<script>
const gridColor = '#1e2433';
const tickColor = '#525974';

new Chart(document.getElementById('trendChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps([d[5:] for d in days14])},
    datasets: [
      {{ label: '有人', data: {json.dumps(trend_p)}, borderColor:'#ff6b35', backgroundColor:'rgba(255,107,53,0.08)', fill:true, tension:0.4, pointRadius:3, pointBackgroundColor:'#ff6b35' }},
      {{ label: '无人', data: {json.dumps(trend_e)}, borderColor:'#00d4aa', backgroundColor:'rgba(0,212,170,0.08)', fill:true, tension:0.4, pointRadius:3, pointBackgroundColor:'#00d4aa' }}
    ]
  }},
  options: {{ responsive:true, maintainAspectRatio:false,
    plugins: {{ legend: {{ labels: {{ color: tickColor, font:{{size:11}} }} }} }},
    scales: {{
      x: {{ grid:{{color:gridColor}}, ticks:{{color:tickColor,font:{{size:10}}}} }},
      y: {{ grid:{{color:gridColor}}, ticks:{{color:tickColor,font:{{size:10}},stepSize:1}}, beginAtZero:true }}
    }}
  }}
}});

new Chart(document.getElementById('forgetChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps([d[5:] for d in days7])},
    datasets: [{{ label:'遗忘次数', data:{json.dumps(forget)}, backgroundColor:'rgba(255,107,53,0.7)', borderRadius:4 }}]
  }},
  options: {{ responsive:true, maintainAspectRatio:false,
    plugins: {{ legend: {{ display:false }} }},
    scales: {{
      x: {{ grid:{{color:gridColor}}, ticks:{{color:tickColor,font:{{size:10}}}} }},
      y: {{ grid:{{color:gridColor}}, ticks:{{color:tickColor,font:{{size:10}},stepSize:1}}, beginAtZero:true }}
    }}
  }}
}});

new Chart(document.getElementById('pieChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['有人 {people_total}', '无人 {total - people_total - fail_total}', '失败 {fail_total}'],
    datasets: [{{ data:[{people_total},{total - people_total - fail_total},{fail_total}],
      backgroundColor:['#ff6b35','#00d4aa','#525974'],
      borderColor:'#1a1f2e', borderWidth:3 }}]
  }},
  options: {{ responsive:true, maintainAspectRatio:false,
    plugins: {{
      legend: {{ labels: {{ color:tickColor, font:{{size:11}}, padding:12 }}, position:'right' }}
    }}
  }}
}});

new Chart(document.getElementById('energyChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(cam_el)},
    datasets: [{{ label:'无人灯亮次数', data:{json.dumps(cam_ed)},
      backgroundColor:'rgba(0,212,170,0.65)', borderRadius:3 }}]
  }},
  options: {{ responsive:true, maintainAspectRatio:false,
    plugins:{{ legend:{{display:false}} }},
    scales: {{
      x:{{ grid:{{color:gridColor}}, ticks:{{color:tickColor,font:{{size:9}}}} }},
      y:{{ grid:{{color:gridColor}}, ticks:{{color:tickColor,font:{{size:10}},stepSize:1}}, beginAtZero:true }}
    }}
  }}
}});

// 基线分析 per-cam
fetch('/api/baseline_data').then(r=>r.json()).then(function(data){{
  var hours = [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23];
  var cameras = Object.keys(data);
  var colors = ['#00d4aa','#ff6b35','#3d8ef8','#9c55f5','#ffa502','#ff4757','#00c896','#ff6b6b'];
  var datasets = cameras.map(function(cam, i) {{
    return {{
      label: cam,
      data: hours.map(function(h) {{ return data[cam][h] || 0; }}),
      borderColor: colors[i % colors.length],
      backgroundColor: colors[i % colors.length] + '22',
      fill: false, tension: 0.3, pointRadius: 2,
    }};
  }});
  new Chart(document.getElementById('baselinePerCamChart'), {{
    type: 'line',
    data: {{ labels: hours.map(function(h){{ return h+':00'; }}), datasets: datasets }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ color: tickColor, font:{{size:10}} }} }} }},
      scales: {{
        x: {{ grid:{{color:gridColor}}, ticks:{{color:tickColor,font:{{size:9}}}} }},
        y: {{ grid:{{color:gridColor}}, ticks:{{color:tickColor,font:{{size:10}}}}, beginAtZero:true }}
      }}
    }}
  }});
}}).catch(function(){{}});

// 能耗统计
fetch('/api/energy_stats').then(r=>r.json()).then(function(data){{
  var labels = data.map(function(d){{ return d.name; }});
  var values = data.map(function(d){{ return d.count; }});
  new Chart(document.getElementById('monthEnergyChart'), {{
    type: 'bar',
    data: {{
      labels: labels,
      datasets: [{{ label:'无人亮灯次数', data: values,
        backgroundColor: 'rgba(255,165,2,0.7)', borderRadius:4 }}]
    }},
    options: {{
      responsive:true, maintainAspectRatio:false,
      plugins:{{legend:{{display:false}}}},
      scales:{{
        x:{{grid:{{color:gridColor}},ticks:{{color:tickColor,font:{{size:9}}}}}},
        y:{{grid:{{color:gridColor}},ticks:{{color:tickColor,font:{{size:10}}}},beginAtZero:true}}
      }}
    }}
  }});
}}).catch(function(){{}});

// 周报趋势
fetch('/api/weekly_trend').then(r=>r.json()).then(function(data){{
  var labels = data.map(function(d){{ return d.day.slice(5); }});
  var anomalies = data.map(function(d){{ return d.anomalies; }});
  var withPeople = data.map(function(d){{ return d.with_people; }});
  new Chart(document.getElementById('weeklyTrendChart'), {{
    type: 'line',
    data: {{
      labels: labels,
      datasets: [
        {{ label:'异常次数', data: anomalies, borderColor:'#ff4757', backgroundColor:'rgba(255,71,87,0.1)', fill:true, tension:0.4, pointRadius:4 }},
        {{ label:'有人次数', data: withPeople, borderColor:'#ff6b35', backgroundColor:'rgba(255,107,53,0.08)', fill:true, tension:0.4, pointRadius:4 }},
      ]
    }},
    options: {{
      responsive:true, maintainAspectRatio:false,
      plugins:{{legend:{{labels:{{color:tickColor,font:{{size:11}}}}}}}},
      scales:{{
        x:{{grid:{{color:gridColor}},ticks:{{color:tickColor,font:{{size:10}}}}}},
        y:{{grid:{{color:gridColor}},ticks:{{color:tickColor,font:{{size:10}},stepSize:1}},beginAtZero:true}}
      }}
    }}
  }});
}}).catch(function(){{}});

async function sendWeeklyReport() {{
  var msg = document.getElementById('weeklyMsg');
  msg.textContent = '发送中...';
  try {{
    var r = await fetch('/api/send_weekly_report', {{method:'POST'}});
    var d = await r.json();
    msg.textContent = d.ok ? '✅ 已发送' : ('❌ ' + d.msg);
  }} catch(e) {{
    msg.textContent = '❌ 网络错误';
  }}
}}

new Chart(document.getElementById('baselineChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(hour_labels)},
    datasets: [{{ label:'有人概率%', data:{json.dumps(hour_data)},
      backgroundColor:'rgba(255,107,53,0.65)', borderRadius:3 }}]
  }},
  options: {{ responsive:true, maintainAspectRatio:false,
    plugins:{{ legend:{{display:false}} }},
    scales: {{
      x:{{ grid:{{color:gridColor}}, ticks:{{color:tickColor,font:{{size:9}}}} }},
      y:{{ grid:{{color:gridColor}}, ticks:{{color:tickColor,font:{{size:10}}}}, beginAtZero:true, max:100 }}
    }}
  }}
}});
</script>
'''
    return make_page('📊 统计分析', content, 'stats')
# ─── LIVE ───────────────────────────────────────────────────────────────────
@app.route('/live')
@login_required
def live():
    content = '''
<div class="grid-2" style="align-items:start">
  <div class="card">
    <div class="card-title"><i class="fa fa-camera"></i>实时画面</div>
    <div id="snapWrap" class="snap-container" style="background:var(--bg-card2);min-height:200px">
      <div style="height:200px;display:flex;align-items:center;justify-content:center;color:var(--text-muted);flex-direction:column;gap:8px" id="noSnap">
        <i class="fa fa-camera" style="font-size:36px;opacity:.3"></i>
        <div>点击"立即巡查"获取最新截图</div>
      </div>
      <img id="snapImg" src="" style="display:none;width:100%" alt="实时截图" onclick="openModal(this.src)">
      <div class="snap-overlay" id="snapTime" style="display:none"></div>
    </div>
    <div style="margin-top:14px;display:flex;gap:10px;align-items:center">
      <button class="btn btn-primary" id="runBtn" onclick="runCheck()">
        <i class="fa fa-camera" id="btnIcon"></i> 立即巡查
      </button>
      <span id="statusMsg" style="font-size:12px;color:var(--text-muted)"></span>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fa fa-robot"></i>AI 分析结果</div>
    <div id="resultWrap" style="min-height:160px">
      <div style="height:160px;display:flex;align-items:center;justify-content:center;color:var(--text-muted)" id="noResult">
        等待巡查结果...
      </div>
      <div id="resultContent" style="display:none">
        <div id="summaryText" style="font-size:15px;font-weight:600;color:var(--text-primary);margin-bottom:16px;line-height:1.6"></div>
        <div class="result-grid" id="resultGrid"></div>
        <div id="resultBadges" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:14px"></div>
      </div>
    </div>
    <div id="cosLinkWrap" style="margin-top:14px;display:none">
      <div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">COS 图片链接</div>
      <div id="cosLink" style="font-size:11px;color:var(--accent);word-break:break-all;background:var(--bg-card2);padding:8px 10px;border-radius:6px;cursor:pointer" onclick="navigator.clipboard.writeText(this.textContent).then(()=>showToast('已复制链接'))"></div>
    </div>
  </div>
</div>

<div class="modal-bg" id="imgModal" onclick="closeModal()">
  <div class="modal-box" style="padding:8px">
    <img id="modalImg" src="" style="max-width:86vw;max-height:86vh;display:block;border-radius:8px">
  </div>
</div>

<script>
function openModal(src){document.getElementById('modalImg').src=src;document.getElementById('imgModal').classList.add('open');}
function closeModal(){document.getElementById('imgModal').classList.remove('open');}

async function runCheck() {
  const btn = document.getElementById('runBtn');
  const icon = document.getElementById('btnIcon');
  const msg  = document.getElementById('statusMsg');
  btn.disabled = true;
  icon.className = 'spinner';
  msg.textContent = '正在截帧 + AI 分析（约15秒）...';
  document.getElementById('noResult').style.display = 'flex';
  document.getElementById('resultContent').style.display = 'none';

  try {
    const resp = await fetch('/api/snapshot/live', {method:'POST'});
    const data = await resp.json();

    if (data.ok) {
      // 截图
      if (data.cos_url) {
        document.getElementById('snapImg').src = data.cos_url;
        document.getElementById('cosLinkWrap').style.display = 'block';
        document.getElementById('cosLink').textContent = data.cos_url;
      } else if (data.snap_b64) {
        document.getElementById('snapImg').src = 'data:image/jpeg;base64,' + data.snap_b64;
      }
      document.getElementById('snapImg').style.display = 'block';
      document.getElementById('noSnap').style.display = 'none';
      document.getElementById('snapTime').style.display = 'block';
      document.getElementById('snapTime').textContent = '截图时间：' + new Date().toLocaleString('zh-CN');

      // 结果
      const r = data.result || {};
      document.getElementById('summaryText').textContent = r.summary || '分析完成';
      document.getElementById('noResult').style.display = 'none';
      document.getElementById('resultContent').style.display = 'block';

      const grid = document.getElementById('resultGrid');
      grid.innerHTML = [
        ['人员', r.has_people ? '🚶 有人（' + (r.people_count||0) + '人）' : '✅ 无人', r.has_people ? 'var(--warn)' : 'var(--green)'],
        ['灯光', r.lights_on ? '💡 开启' : '🌙 关闭', r.lights_on ? 'var(--yellow)' : 'var(--green)'],
        ['设备', r.devices_on ? '🖥 运行中' : '✅ 已关闭', r.devices_on ? 'var(--blue)' : 'var(--green)'],
        ['综合', r.need_attention ? '⚠️ 需关注' : '✅ 正常', r.need_attention ? 'var(--red)' : 'var(--green)'],
      ].map(([l,v,c]) => `<div class="result-item"><div class="result-item-label">${l}</div><div class="result-item-value" style="color:${c}">${v}</div></div>`).join('');

      const badges = document.getElementById('resultBadges');
      badges.innerHTML = '';
      if (r.people_desc) badges.innerHTML += `<span class="badge badge-gray">${r.people_desc}</span>`;

      msg.innerHTML = '<span style="color:var(--green)">✅ 巡查完成</span>';
      showToast('巡查完成', 'success');
    } else {
      msg.innerHTML = '<span style="color:var(--red)">❌ ' + (data.error||'巡查失败') + '</span>';
      showToast(data.error||'巡查失败', 'error');
    }
  } catch(e) {
    msg.innerHTML = '<span style="color:var(--red)">❌ 网络错误</span>';
    showToast('网络错误', 'error');
  }
  btn.disabled = false;
  icon.className = 'fa fa-camera';
}
</script>
'''
    return make_page('📷 实时截图', content, 'live')

@app.route('/snapshot')
@login_required
def snapshot():
    path = '/tmp/camwatch_snapshot.jpg'
    if os.path.exists(path):
        return send_file(path, mimetype='image/jpeg')
    return '', 404

@app.route('/api/snapshot/live', methods=['POST'])
@login_required
def api_snapshot_live():
    """立即执行一次巡查，返回截图和AI结果"""
    try:
        result = subprocess.run(
            ['python3', CAMWATCH_SCRIPT, 'test'],
            capture_output=True, timeout=90, text=True
        )
        db = get_db()
        latest = db.execute("SELECT * FROM checks ORDER BY check_time DESC LIMIT 1").fetchone()
        db.close()

        resp = {'ok': True}
        if latest:
            resp['result'] = dict(latest)
            # COS URL
            cos_url = latest['cos_url'] if 'cos_url' in latest.keys() else None
            if cos_url:
                resp['cos_url'] = cos_url
            # fallback base64
            snap = latest['snapshot_path']
            if snap and os.path.exists(snap):
                with open(snap,'rb') as f:
                    resp['snap_b64'] = base64.b64encode(f.read()).decode()
        else:
            resp['result'] = {}
        return jsonify(resp)
    except subprocess.TimeoutExpired:
        return jsonify({'ok': False, 'error': '执行超时'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# 旧接口兼容
@app.route('/api/run_check', methods=['POST'])
@login_required
def api_run_check():
    return api_snapshot_live()
# ─── SETTINGS ───────────────────────────────────────────────────────────────
@app.route('/settings')
@login_required
def settings():
    try:
        cron_out = subprocess.run(['crontab','-l'], capture_output=True, text=True).stdout
        cron_lines = [l for l in cron_out.splitlines() if 'camwatch' in l]
    except:
        cron_lines = []
    try:
        with open(LOG_PATH) as f:
            log_lines = f.readlines()[-80:]
        log_content = ''.join(log_lines)
    except:
        log_content = '暂无日志'
    try:
        with open(CONFIG_PATH) as f:
            config_data = json.load(f)
        cameras = config_data.get('cameras', [])
        skip_weekend = config_data.get('schedule', {}).get('skip_weekend', False)
        cos_cfg = config_data.get('cos', {})
        rules = config_data.get('rules', {})
        night_cfg = config_data.get('night_intrusion', {})
        energy_cfg = config_data.get('energy_alert', {})
    except:
        cameras, rules, night_cfg, energy_cfg = [], {}, {}, {}
        skip_weekend = False; cos_cfg = {}

    cron_html = ''.join(
        f'<div style="background:#0a0c12;color:#a0e8b0;padding:10px 14px;border-radius:6px;font-family:monospace;font-size:12px;margin-bottom:6px">{l}</div>'
        for l in cron_lines
    ) or '<div style="color:var(--text-muted);font-size:13px">未找到定时任务</div>'

    # build per-camera notice editors
    cam_notice_html = ''
    for cam in cameras:
        cname = cam.get('name','未命名')
        notice_val = cam.get('notice','').replace('"', '&quot;')
        cam_notice_html += f"""
<div style="margin-bottom:14px">
  <div style="font-size:13px;font-weight:600;color:var(--text-primary);margin-bottom:4px">📹 {cname}</div>
  <textarea name="notice_{cname}" rows="4" class="form-input" style="width:100%;font-size:12px;font-family:monospace;resize:vertical">{cam.get('notice','')}</textarea>
</div>"""

    # rules toggles
    no_ppl_light = 'checked' if rules.get('no_people_lights_on', True) else ''
    no_ppl_dev   = 'checked' if rules.get('no_people_devices_on', True) else ''

    saved_toast = '<div style="background:#0a2e1a;border:1px solid #00d4aa;border-radius:8px;padding:12px 16px;margin-bottom:16px;color:#00d4aa;font-size:13px"><i class="fa fa-circle-check"></i> 设置已保存</div>' if request.args.get("saved") == "1" else ""
    content = f"""
{saved_toast}
<form method="POST" action="/api/save_settings" style="max-width:900px;display:grid;gap:20px">

  <div class="card">
    <div class="card-title"><i class="fa fa-comment-dots"></i>摄像头 NOTICE 配置（各摄像头专属提示词）</div>
    <div style="font-size:12px;color:var(--text-muted);margin-bottom:12px">
      每个摄像头可单独配置 AI 分析提示词。留空则使用全局默认 NOTICE。
    </div>
    {cam_notice_html}
  </div>

  <div class="card">
    <div class="card-title"><i class="fa fa-triangle-exclamation"></i>异常规则配置</div>
    <div style="display:grid;gap:10px">
      <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
        <input type="checkbox" name="rule_no_ppl_light" {no_ppl_light} style="width:16px;height:16px;accent-color:var(--accent)">
        <span style="font-size:13px">无人但灯亮 → 触发异常</span>
      </label>
      <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
        <input type="checkbox" name="rule_no_ppl_dev" {no_ppl_dev} style="width:16px;height:16px;accent-color:var(--accent)">
        <span style="font-size:13px">无人但设备开启 → 触发异常</span>
      </label>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fa fa-moon"></i>夜间闯入检测时段</div>
    <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
      <div>
        <div style="font-size:12px;color:var(--text-muted);margin-bottom:4px">开始时间</div>
        <input type="time" name="night_start" value="{night_cfg.get('start','22:00')}" class="form-input" style="width:120px">
      </div>
      <div style="color:var(--text-muted);margin-top:16px">→</div>
      <div>
        <div style="font-size:12px;color:var(--text-muted);margin-bottom:4px">结束时间</div>
        <input type="time" name="night_end" value="{night_cfg.get('end','07:00')}" class="form-input" style="width:120px">
      </div>
      <label style="display:flex;align-items:center;gap:8px;margin-top:16px;cursor:pointer">
        <input type="checkbox" name="night_enabled" {'checked' if night_cfg.get('enabled',False) else ''} style="width:16px;height:16px;accent-color:var(--accent)">
        <span style="font-size:13px">启用夜间闯入检测</span>
      </label>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fa fa-bolt"></i>能耗预警阈值</div>
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <div>
        <div style="font-size:12px;color:var(--text-muted);margin-bottom:4px">连续异常次数触发预警</div>
        <input type="number" name="energy_streak" value="{energy_cfg.get('streak_threshold',3)}" min="1" max="20" class="form-input" style="width:80px">
      </div>
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="checkbox" name="energy_enabled" {'checked' if energy_cfg.get('enabled',True) else ''} style="width:16px;height:16px;accent-color:var(--accent)">
        <span style="font-size:13px">启用能耗预警</span>
      </label>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><i class="fa fa-sliders"></i>计划设置</div>
    <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
      <input type="checkbox" name="skip_weekend" {'checked' if skip_weekend else ''} style="width:16px;height:16px;accent-color:var(--accent)">
      <span style="font-size:13px">周末跳过巡查</span>
    </label>
  </div>

  <div style="display:flex;gap:12px">
    <button type="submit" class="btn btn-primary"><i class="fa fa-floppy-disk"></i>保存设置</button>
    <a href="/report_preview" class="btn btn-secondary"><i class="fa fa-eye"></i>预览最近报告</a>
  </div>
</form>

<div class="card" style="max-width:900px;margin-top:0">
  <div class="card-title"><i class="fa fa-clock"></i>定时巡查任务</div>
  {cron_html}
</div>

<div class="card" style="max-width:900px">
  <div class="card-title"><i class="fa fa-terminal"></i>系统日志（最近80行）</div>
  <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
    <button class="btn btn-secondary" style="padding:6px 12px;font-size:12px" onclick="document.getElementById('logPre').scrollTop=9999"><i class="fa fa-arrow-down"></i> 跳到底部</button>
  </div>
  <pre class="log-pre" id="logPre">{log_content}</pre>
</div>
<script>document.getElementById('logPre').scrollTop=9999;</script>
"""
    return make_page('⚙️ 系统设置', content, 'settings')


@app.route('/api/save_settings', methods=['POST'])
@login_required
def api_save_settings():
    import json as _json
    data = request.form
    try:
        with open(CONFIG_PATH) as f:
            cfg = _json.load(f)
        for cam in cfg.get('cameras', []):
            key = 'notice_' + cam['name']
            if key in data:
                val = data[key].strip()
                if val: cam['notice'] = val
                else: cam.pop('notice', None)
        cfg.setdefault('rules', {})
        cfg['rules']['no_people_lights_on']  = 'rule_no_ppl_light' in data
        cfg['rules']['no_people_devices_on'] = 'rule_no_ppl_dev'   in data
        cfg.setdefault('night_intrusion', {})
        cfg['night_intrusion']['enabled'] = 'night_enabled' in data
        cfg['night_intrusion']['start']   = data.get('night_start', '22:00')
        cfg['night_intrusion']['end']     = data.get('night_end',   '07:00')
        # 同步 camwatch.py 用的 night_start_hour / night_end_hour
        try:
            ns = data.get('night_start', '22:00')
            ne = data.get('night_end', '06:00')
            cfg['night_start_hour'] = int(ns.split(':')[0])
            cfg['night_end_hour'] = int(ne.split(':')[0])
        except:
            pass
        cfg.setdefault('energy_alert', {})
        cfg['energy_alert']['enabled']          = 'energy_enabled' in data
        cfg['energy_alert']['streak_threshold'] = int(data.get('energy_streak', 3))
        # 同步 energy_alert_threshold
        cfg['energy_alert_threshold'] = int(data.get('energy_streak', 3))
        cfg.setdefault('schedule', {})
        cfg['schedule']['skip_weekend'] = 'skip_weekend' in data
        with open(CONFIG_PATH, 'w') as f:
            _json.dump(cfg, f, ensure_ascii=False, indent=2)
        return redirect('/settings?saved=1')
    except Exception as e:
        return f'保存失败: {e}', 500


@app.route('/report_preview')
@login_required
def report_preview():
    PREVIEW_FILE = '/tmp/camwatch_last_report.txt'
    content_text = ''
    try:
        content_text = open(PREVIEW_FILE).read()
    except:
        pass
    if not content_text:
        content_text = '（暂无缓存报告，下次巡查后自动生成）'

    cross_text = ''
    try:
        cross_text = open('/tmp/camwatch_cross_analysis.txt').read()
    except:
        pass
    import html as _html
    escaped = _html.escape(content_text)
    cross_html = ''
    if cross_text:
        cross_html = (
            '<div class="card" style="margin-top:16px;border-left:3px solid var(--accent)">'
            '<div class="card-title"><i class="fa fa-lightbulb"></i> 💡 跨摄像头综合分析</div>'
            '<div style="font-size:13px;color:var(--text-primary);line-height:1.7;padding:8px 0">'
            + _html.escape(cross_text) +
            '</div></div>'
        )
    card = (
        '<div style="max-width:700px">\n'
        '  <div class="card">\n'
        '    <div class="card-title"><i class="fa fa-eye"></i>最近一次汇总报告预览</div>\n'
        '    <div style="margin-bottom:12px;display:flex;gap:10px">\n'
        '      <a href="/live" class="btn btn-primary" style="font-size:12px;padding:6px 14px"><i class="fa fa-rotate"></i>立即巡查</a>\n'
        '      <a href="/settings" class="btn btn-secondary" style="font-size:12px;padding:6px 14px"><i class="fa fa-gear"></i>返回设置</a>\n'
        '    </div>\n'
        '    <pre style="background:var(--bg-card2);border:1px solid var(--border);border-radius:8px;padding:16px;font-size:13px;line-height:1.7;white-space:pre-wrap;word-break:break-word;color:var(--text-primary)">'
        + escaped +
        '</pre>\n'
        '  </div>\n'
        + cross_html +
        '</div>'
    )
    return make_page('📄 报告预览', card, 'settings')
@app.route('/api/baseline_data')
@login_required
def baseline_data():
    import sqlite3 as _sq
    conn = _sq.connect(DB_PATH)
    rows = conn.execute("""
        SELECT camera_name, CAST(strftime('%H', check_time) AS INTEGER) as hour,
               AVG(COALESCE(people_count, 0)) as avg_people
        FROM checks GROUP BY camera_name, hour ORDER BY camera_name, hour
    """).fetchall()
    conn.close()
    result = {}
    for name, hour, avg in rows:
        result.setdefault(name, {})[hour] = round(avg, 1)
    return jsonify(result)


# ─── API: 能耗统计 ──────────────────────────────────────────────────────────
@app.route('/api/energy_stats')
@login_required
def energy_stats():
    import sqlite3 as _sq
    conn = _sq.connect(DB_PATH)
    rows = conn.execute("""
        SELECT camera_name, COUNT(*) as cnt
        FROM checks
        WHERE need_attention=1 AND lights_on=1 AND has_people=0
          AND check_time >= date('now', 'start of month')
        GROUP BY camera_name ORDER BY cnt DESC
    """).fetchall()
    conn.close()
    return jsonify([{"name": r[0], "count": r[1]} for r in rows])


# ─── API: 周报趋势 ──────────────────────────────────────────────────────────
@app.route('/api/weekly_trend')
@login_required
def weekly_trend():
    import sqlite3 as _sq
    conn = _sq.connect(DB_PATH)
    rows = conn.execute("""
        SELECT date(check_time) as day,
               SUM(CASE WHEN need_attention=1 THEN 1 ELSE 0 END) as anomalies,
               COUNT(*) as total,
               SUM(CASE WHEN has_people=1 THEN 1 ELSE 0 END) as with_people
        FROM checks
        WHERE check_time >= date('now', '-7 days')
        GROUP BY day ORDER BY day
    """).fetchall()
    conn.close()
    return jsonify([{"day": r[0], "anomalies": r[1], "total": r[2], "with_people": r[3]} for r in rows])


# ─── API: 立即发送周报 ───────────────────────────────────────────────────────
@app.route('/api/send_weekly_report', methods=['POST'])
@login_required
def api_send_weekly_report():
    import subprocess as _sp
    try:
        result = _sp.run(
            ['python3', CAMWATCH_SCRIPT, 'weekly'],
            capture_output=True, timeout=30, text=True
        )
        if result.returncode == 0:
            return jsonify({'ok': True, 'msg': '周报已发送'})
        else:
            return jsonify({'ok': False, 'msg': result.stderr[-200:]})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/wifi_scan')
@login_required
def api_wifi_scan():
    """WiFi 探针：扫描内网在线设备，返回人员估算"""
    use_cache = request.args.get('cache', '1') != '0'
    if not _WIFI_OK:
        return jsonify({'ok': False, 'msg': 'wifi_probe 模块未加载', 'estimated_people': 0})
    try:
        result = _scan_wifi(use_cache=use_cache)
        return jsonify({'ok': True, **result})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e), 'estimated_people': 0})


# ─── MAIN ───────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=8088, debug=False)

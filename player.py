"""
Custom Video Player Templates — HTML/CSS/JS

Features:
- Multi-language selector (Hindi, English, Japanese, etc.)
- Multi-quality selector (480p, 720p, 1080p)
- Smart switching (language stays when changing quality and vice versa)
- Seamless source switching (preserves playback position)
- Pre-roll ad support (VAST or custom HTML)
- Dark themed, responsive, anti-download
- Embeddable via iframe

All streaming uses MTProto (NO Bot API getFile, NO 20MB limit).
"""

# ══════════════════════════════════════════════════════════════
# FULL PLAYER PAGE — /watch/{slug}
# ══════════════════════════════════════════════════════════════

WATCH_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — Stream Player</title>
<style>
:root {{
  --bg: #0a0a12; --surface: #12121f; --surface2: #1a1a2e;
  --border: #2a2a40; --text: #e0e0f0; --text2: #8888aa;
  --accent: #7c5cfc; --accent2: #a78bfa;
  --success: #22c55e; --danger: #ef4444;
}}
*{{ margin:0; padding:0; box-sizing:border-box; }}
body{{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif; min-height:100vh; }}

.vp-wrap{{ max-width:960px; margin:0 auto; padding:12px; }}

/* ─── VIDEO CONTAINER ─── */
.vp-container{{ position:relative; width:100%; background:#000; border-radius:12px; overflow:hidden;
  box-shadow:0 4px 24px rgba(0,0,0,.5); aspect-ratio:16/9; }}
.vp-container video{{ width:100%; height:100%; object-fit:contain; display:block; }}

/* ─── AD OVERLAY ─── */
.vp-ad-overlay{{ position:absolute; inset:0; background:#000; z-index:20; display:flex;
  align-items:center; justify-content:center; flex-direction:column; }}
.vp-ad-overlay.hidden{{ display:none; }}
.vp-ad-content{{ width:100%; height:100%; display:flex; align-items:center; justify-content:center; }}
.vp-ad-content iframe{{ width:100%; height:100%; border:none; }}
.vp-ad-timer{{ position:absolute; top:12px; right:12px; background:rgba(0,0,0,.7);
  color:#fff; padding:6px 14px; border-radius:20px; font-size:.8rem; cursor:default; z-index:21; }}
.vp-ad-timer.clickable{{ cursor:pointer; background:var(--accent); }}

/* ─── LOADING ─── */
.vp-loading{{ position:absolute; inset:0; background:rgba(0,0,0,.6); display:flex;
  align-items:center; justify-content:center; z-index:10; }}
.vp-loading.hidden{{ display:none; }}
.vp-spinner{{ width:40px; height:40px; border:3px solid rgba(255,255,255,.2);
  border-top-color:var(--accent); border-radius:50%; animation:spin .8s linear infinite; }}
@keyframes spin{{ to{{ transform:rotate(360deg); }} }}

/* ─── CONTROLS BAR ─── */
.vp-controls{{ display:flex; align-items:center; gap:10px; padding:12px 0; flex-wrap:wrap; }}
.vp-selector{{ position:relative; }}
.vp-selector select{{
  appearance:none; background:var(--surface2); color:var(--text);
  border:1px solid var(--border); border-radius:8px; padding:8px 32px 8px 12px;
  font-size:.85rem; cursor:pointer; outline:none; min-width:120px;
}}
.vp-selector select:focus{{ border-color:var(--accent); }}
.vp-selector::after{{
  content:'▾'; position:absolute; right:10px; top:50%; transform:translateY(-50%);
  color:var(--text2); pointer-events:none; font-size:.75rem;
}}
.vp-label{{ font-size:.7rem; color:var(--text2); text-transform:uppercase;
  letter-spacing:.5px; margin-bottom:4px; display:block; }}

/* ─── INFO BAR ─── */
.vp-info{{ padding:8px 0; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
.vp-title{{ font-size:1.1rem; font-weight:600; color:#fff; }}
.vp-badge{{ display:inline-block; background:linear-gradient(135deg,var(--accent),#9333ea);
  color:#fff; padding:2px 10px; border-radius:12px; font-size:.7rem; font-weight:500; }}
.vp-meta{{ font-size:.8rem; color:var(--text2); }}

/* ─── TOAST ─── */
.vp-toast{{ position:fixed; bottom:20px; left:50%; transform:translateX(-50%) translateY(80px);
  background:var(--surface2); color:var(--text); padding:10px 20px; border-radius:10px;
  font-size:.85rem; opacity:0; transition:all .3s ease; z-index:100;
  border:1px solid var(--border); }}
.vp-toast.show{{ transform:translateX(-50%) translateY(0); opacity:1; }}

.vp-footer{{ text-align:center; padding:20px; color:var(--text2); font-size:.7rem; }}
</style>
</head>
<body>

<div class="vp-wrap">
  <!-- Info -->
  <div class="vp-info">
    <span class="vp-title">{title}</span>
    <span class="vp-badge">MTProto Stream</span>
  </div>

  <!-- Video Container -->
  <div class="vp-container">
    <video id="vpVideo" playsinline preload="auto" controlsList="nodownload" controls></video>
    <div id="vpLoading" class="vp-loading"><div class="vp-spinner"></div></div>
    <div id="vpAdOverlay" class="vp-ad-overlay hidden">
      <div id="vpAdContent" class="vp-ad-content"></div>
      <div id="vpAdTimer" class="vp-ad-timer">Ad · 5s</div>
    </div>
  </div>

  <!-- Controls -->
  <div class="vp-controls">
    <div class="vp-selector">
      <label class="vp-label">Language</label>
      <select id="vpLang"></select>
    </div>
    <div class="vp-selector">
      <label class="vp-label">Quality</label>
      <select id="vpQuality"></select>
    </div>
    <span id="vpCurrentInfo" class="vp-meta"></span>
  </div>
</div>

<div class="vp-footer">Powered by TG Stream — MTProto (No 20MB Limit)</div>
<div id="vpToast" class="vp-toast"></div>

<script>
'use strict';
const SLUG      = '{slug}';
const API_SRC   = '{api_sources_url}';
const API_ADS   = '{api_ads_url}';

const video     = document.getElementById('vpVideo');
const langSel   = document.getElementById('vpLang');
const qualSel   = document.getElementById('vpQuality');
const loading   = document.getElementById('vpLoading');
const adOverlay = document.getElementById('vpAdOverlay');
const adContent = document.getElementById('vpAdContent');
const adTimer   = document.getElementById('vpAdTimer');
const infoSpan  = document.getElementById('vpCurrentInfo');
const toast     = document.getElementById('vpToast');

let allSources  = [];
let currentLang = '';
let currentQual = '';
let adsDone     = false;
let adsData     = [];

// ── Prevent right-click download ──
video.addEventListener('contextmenu', e => e.preventDefault());

// ══════════════════════════════════════════════════
// INITIALIZATION
// ══════════════════════════════════════════════════

async function init() {{
  try {{
    // Fetch sources
    const srcRes = await fetch(API_SRC);
    const srcJson = await srcRes.json();
    if (!srcJson.success || !srcJson.sources.length) {{
      showToast('No video sources available');
      loading.classList.add('hidden');
      return;
    }}
    allSources = srcJson.sources;

    // Fetch ads
    try {{
      const adRes = await fetch(API_ADS);
      adsData = await adRes.json();
    }} catch(e) {{ adsData = []; }}

    // Build dropdowns
    buildDropdowns();

    // Set defaults
    currentLang = allSources[0].language;
    currentQual = allSources[0].quality;
    updateDropdowns();

    // Show pre-roll ad or play
    const preAds = adsData.filter(a => a.position === 'pre' && a.is_active);
    if (preAds.length > 0 && !adsDone) {{
      showPrerollAd(preAds[0], () => {{
        adsDone = true;
        playSource();
      }});
    }} else {{
      playSource();
    }}

  }} catch(err) {{
    console.error('Init error:', err);
    showToast('Failed to load video');
    loading.classList.add('hidden');
  }}
}}

// ══════════════════════════════════════════════════
// DROPDOWN BUILDING
// ══════════════════════════════════════════════════

function buildDropdowns() {{
  const languages = [...new Set(allSources.map(s => s.language))];
  const qualities = [...new Set(allSources.map(s => s.quality))];

  langSel.innerHTML = languages.map(l =>
    `<option value="${{l}}">${{l}}</option>`
  ).join('');

  qualSel.innerHTML = qualities.map(q =>
    `<option value="${{q}}">${{q}}</option>`
  ).join('');

  langSel.addEventListener('change', () => {{
    currentLang = langSel.value;
    switchSource();
  }});
  qualSel.addEventListener('change', () => {{
    currentQual = qualSel.value;
    switchSource();
  }});
}}

function updateDropdowns() {{
  langSel.value = currentLang;
  qualSel.value = currentQual;

  // Highlight available combos
  const availQualities = allSources
    .filter(s => s.language === currentLang)
    .map(s => s.quality);
  Array.from(qualSel.options).forEach(opt => {{
    opt.disabled = !availQualities.includes(opt.value);
    opt.textContent = opt.value + (opt.disabled ? ' ✗' : '');
  }});

  const availLangs = allSources
    .filter(s => s.quality === currentQual)
    .map(s => s.language);
  Array.from(langSel.options).forEach(opt => {{
    opt.disabled = !availLangs.includes(opt.value);
    opt.textContent = opt.value + (opt.disabled ? ' ✗' : '');
  }});

  updateInfo();
}}

function updateInfo() {{
  const src = findSource(currentLang, currentQual);
  if (src) {{
    const sizeMb = src.file_size ? (src.file_size / 1048576).toFixed(0) + ' MB' : '';
    infoSpan.textContent = `${{currentLang}} · ${{currentQual}} ${{sizeMb ? '· ' + sizeMb : ''}}`;
  }}
}}

// ══════════════════════════════════════════════════
// SMART SOURCE FINDING
// ══════════════════════════════════════════════════

function findSource(lang, quality) {{
  // 1. Exact match
  let s = allSources.find(x => x.language === lang && x.quality === quality);
  if (s) return s;

  // 2. Same language, any quality (prefer highest)
  s = allSources.find(x => x.language === lang);
  if (s) {{
    currentQual = s.quality;
    return s;
  }}

  // 3. Same quality, any language
  s = allSources.find(x => x.quality === quality);
  if (s) {{
    currentLang = s.language;
    return s;
  }}

  // 4. Fallback to first
  return allSources[0] || null;
}}

// ══════════════════════════════════════════════════
// SEAMLESS SOURCE SWITCHING
// ══════════════════════════════════════════════════

function switchSource() {{
  const savedTime = video.currentTime || 0;
  const wasPlaying = !video.paused;

  const src = findSource(currentLang, currentQual);
  if (!src) {{
    showToast('Source not available');
    return;
  }}

  currentLang = src.language;
  currentQual = src.quality;
  updateDropdowns();
  loading.classList.remove('hidden');

  video.src = src.url;
  video.load();

  const onReady = () => {{
    video.removeEventListener('loadeddata', onReady);
    loading.classList.add('hidden');
    if (savedTime > 0) video.currentTime = savedTime;
    if (wasPlaying) video.play().catch(() => {{}});
    showToast(`Switched to ${{currentLang}} ${{currentQual}}`);
  }};
  video.addEventListener('loadeddata', onReady);
}}

function playSource() {{
  const src = findSource(currentLang, currentQual);
  if (!src) return;

  currentLang = src.language;
  currentQual = src.quality;
  updateDropdowns();

  video.src = src.url;
  video.load();

  const onReady = () => {{
    video.removeEventListener('loadeddata', onReady);
    loading.classList.add('hidden');
    video.play().catch(() => {{}});
  }};
  video.addEventListener('loadeddata', onReady);
}}

// ══════════════════════════════════════════════════
// PRE-ROLL ADS
// ══════════════════════════════════════════════════

function showPrerollAd(ad, onComplete) {{
  adOverlay.classList.remove('hidden');
  loading.classList.add('hidden');
  let remaining = ad.duration || 5;

  if (ad.ad_type === 'vast' && ad.ad_url) {{
    adContent.innerHTML = `<iframe src="${{ad.ad_url}}" allowfullscreen></iframe>`;
  }} else if (ad.ad_html) {{
    adContent.innerHTML = ad.ad_html;
  }} else {{
    onComplete();
    return;
  }}

  adTimer.textContent = `Ad · ${{remaining}}s`;
  adTimer.classList.remove('clickable');

  const interval = setInterval(() => {{
    remaining--;
    if (remaining > 0) {{
      adTimer.textContent = `Ad · ${{remaining}}s`;
    }} else {{
      clearInterval(interval);
      adTimer.textContent = 'Skip Ad ▶';
      adTimer.classList.add('clickable');
      adTimer.onclick = () => {{
        adOverlay.classList.add('hidden');
        adContent.innerHTML = '';
        onComplete();
      }};
    }}
  }}, 1000);
}}

// ══════════════════════════════════════════════════
// TOAST NOTIFICATION
// ══════════════════════════════════════════════════

function showToast(msg) {{
  toast.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2500);
}}

// ── GO ──
init();
</script>
</body>
</html>"""

# ══════════════════════════════════════════════════════════════
# EMBED PAGE — /embed/{slug} (for iFrame)
# ══════════════════════════════════════════════════════════════

EMBED_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Embed Player</title>
<style>
*{{ margin:0; padding:0; box-sizing:border-box; }}
html,body{{ width:100%; height:100%; overflow:hidden; background:#000; font-family:system-ui,sans-serif; }}
.wrap{{ display:flex; flex-direction:column; height:100%; }}
video{{ flex:1; width:100%; object-fit:contain; }}
.bar{{ display:flex; gap:6px; padding:6px 8px; background:#111; }}
.bar select{{
  appearance:none; background:#222; color:#ddd; border:1px solid #333;
  border-radius:6px; padding:5px 24px 5px 8px; font-size:.78rem; cursor:pointer; outline:none;
}}
.bar select:focus{{ border-color:#7c5cfc; }}
.sel{{ position:relative; }}
.sel::after{{ content:'▾'; position:absolute; right:8px; top:50%; transform:translateY(-50%);
  color:#666; pointer-events:none; font-size:.65rem; }}
.ad-overlay{{ position:fixed; inset:0; background:#000; z-index:10; display:flex;
  align-items:center; justify-content:center; }}
.ad-overlay.hidden{{ display:none; }}
.ad-timer{{ position:fixed; top:8px; right:8px; background:rgba(0,0,0,.7);
  color:#fff; padding:4px 12px; border-radius:12px; font-size:.75rem; z-index:11; cursor:default; }}
.ad-timer.skip{{ cursor:pointer; background:#7c5cfc; }}
</style>
</head>
<body>
<div class="wrap">
  <video id="v" playsinline preload="auto" controls controlsList="nodownload"></video>
  <div class="bar">
    <div class="sel"><select id="sl"></select></div>
    <div class="sel"><select id="sq"></select></div>
  </div>
</div>
<div id="ao" class="ad-overlay hidden"><div id="ac"></div></div>
<div id="at" class="ad-timer" style="display:none"></div>

<script>
'use strict';
const v=document.getElementById('v'), sl=document.getElementById('sl'),
  sq=document.getElementById('sq'), ao=document.getElementById('ao'),
  ac=document.getElementById('ac'), at=document.getElementById('at');
let S=[],cL='',cQ='',aD=false;

v.addEventListener('contextmenu',e=>e.preventDefault());

async function go(){{
  const r=await fetch('{api_sources_url}');
  const j=await r.json();
  if(!j.success||!j.sources.length)return;
  S=j.sources; cL=S[0].language; cQ=S[0].quality;

  let ads=[];
  try{{ ads=await(await fetch('{api_ads_url}')).json(); }}catch(e){{}}

  const langs=[...new Set(S.map(s=>s.language))];
  const quals=[...new Set(S.map(s=>s.quality))];
  sl.innerHTML=langs.map(l=>`<option>${{l}}</option>`).join('');
  sq.innerHTML=quals.map(q=>`<option>${{q}}</option>`).join('');
  sl.value=cL; sq.value=cQ;
  sl.onchange=()=>{{ cL=sl.value; sw(); }};
  sq.onchange=()=>{{ cQ=sq.value; sw(); }};

  const pre=ads.filter(a=>a.position==='pre'&&a.is_active);
  if(pre.length&&!aD){{ showAd(pre[0],()=>{{ aD=true; play(); }}); }}
  else play();
}}

function find(l,q){{
  let s=S.find(x=>x.language===l&&x.quality===q);
  if(s)return s;
  s=S.find(x=>x.language===l); if(s){{ cQ=s.quality; sq.value=cQ; return s; }}
  s=S.find(x=>x.quality===q); if(s){{ cL=s.language; sl.value=cL; return s; }}
  return S[0];
}}

function sw(){{
  const t=v.currentTime||0, p=!v.paused;
  const s=find(cL,cQ); if(!s)return;
  cL=s.language; cQ=s.quality; sl.value=cL; sq.value=cQ;
  v.src=s.url; v.load();
  v.addEventListener('loadeddata',()=>{{ if(t>0)v.currentTime=t; if(p)v.play().catch(()=>{{}}); }},{{once:true}});
}}

function play(){{
  const s=find(cL,cQ); if(!s)return;
  v.src=s.url; v.load();
  v.addEventListener('loadeddata',()=>v.play().catch(()=>{{}}),{{once:true}});
}}

function showAd(ad,cb){{
  ao.classList.remove('hidden'); at.style.display='';
  let rem=ad.duration||5;
  if(ad.ad_type==='vast'&&ad.ad_url) ac.innerHTML=`<iframe src="${{ad.ad_url}}" style="width:100%;height:100%;border:none"></iframe>`;
  else if(ad.ad_html) ac.innerHTML=ad.ad_html;
  else{{ cb(); return; }}
  at.textContent=`Ad · ${{rem}}s`;
  const iv=setInterval(()=>{{
    rem--;
    if(rem>0){{ at.textContent=`Ad · ${{rem}}s`; }}
    else{{ clearInterval(iv); at.textContent='Skip ▶'; at.classList.add('skip');
      at.onclick=()=>{{ ao.classList.add('hidden'); at.style.display='none'; ac.innerHTML=''; cb(); }};
    }}
  }},1000);
}}
go();
</script>
</body>
</html>"""

# ══════════════════════════════════════════════════════════════
# ADMIN PANEL TEMPLATE
# ══════════════════════════════════════════════════════════════

ADMIN_BASE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Admin — {page_title}</title>
<style>
:root{{ --bg:#0d0d1a; --s1:#141425; --s2:#1c1c35; --bdr:#2a2a48; --txt:#d0d0e8;
  --txt2:#7777aa; --acc:#7c5cfc; --acc2:#a78bfa; --ok:#22c55e; --err:#ef4444; }}
*{{ margin:0; padding:0; box-sizing:border-box; }}
body{{ background:var(--bg); color:var(--txt); font-family:'Segoe UI',system-ui,sans-serif;
  display:flex; min-height:100vh; }}
.sidebar{{ width:220px; background:var(--s1); border-right:1px solid var(--bdr);
  padding:20px 0; flex-shrink:0; }}
.sidebar h2{{ padding:0 20px 16px; font-size:1rem; color:var(--acc2); }}
.sidebar a{{ display:block; padding:10px 20px; color:var(--txt2); text-decoration:none;
  font-size:.88rem; border-left:3px solid transparent; transition:all .15s; }}
.sidebar a:hover,.sidebar a.active{{ color:var(--txt); background:var(--s2);
  border-left-color:var(--acc); }}
.main{{ flex:1; padding:24px 32px; overflow-y:auto; }}
.main h1{{ font-size:1.5rem; margin-bottom:20px; color:#fff; }}
table{{ width:100%; border-collapse:collapse; margin:16px 0; font-size:.85rem; }}
th{{ text-align:left; padding:10px 12px; background:var(--s2); color:var(--txt2);
  border-bottom:1px solid var(--bdr); font-weight:500; font-size:.78rem;
  text-transform:uppercase; letter-spacing:.5px; }}
td{{ padding:10px 12px; border-bottom:1px solid var(--bdr); vertical-align:top; }}
tr:hover td{{ background:rgba(124,92,252,.04); }}
.btn{{ display:inline-block; padding:6px 16px; border-radius:8px; font-size:.82rem;
  cursor:pointer; border:none; text-decoration:none; transition:all .15s; }}
.btn-primary{{ background:var(--acc); color:#fff; }}
.btn-primary:hover{{ background:var(--acc2); }}
.btn-danger{{ background:var(--err); color:#fff; }}
.btn-sm{{ padding:4px 10px; font-size:.75rem; }}
.card{{ background:var(--s1); border:1px solid var(--bdr); border-radius:12px;
  padding:20px; margin-bottom:16px; }}
.stat{{ text-align:center; }}
.stat-value{{ font-size:2rem; font-weight:700; color:var(--acc2); }}
.stat-label{{ font-size:.78rem; color:var(--txt2); margin-top:4px; }}
.grid{{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:16px; margin:16px 0; }}
input[type=text],input[type=password],input[type=number],select,textarea{{
  background:var(--s2); border:1px solid var(--bdr); color:var(--txt);
  padding:8px 12px; border-radius:8px; font-size:.85rem; width:100%; outline:none; }}
input:focus,select:focus,textarea:focus{{ border-color:var(--acc); }}
label{{ display:block; font-size:.78rem; color:var(--txt2); margin-bottom:4px;
  text-transform:uppercase; letter-spacing:.3px; }}
.form-group{{ margin-bottom:14px; }}
.badge{{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:.7rem; }}
.badge-ok{{ background:rgba(34,197,94,.15); color:var(--ok); }}
.badge-err{{ background:rgba(239,68,68,.15); color:var(--err); }}
.mono{{ font-family:monospace; font-size:.8rem; word-break:break-all; color:var(--txt2); }}
.flash{{ padding:10px 16px; border-radius:8px; margin-bottom:16px; font-size:.85rem; }}
.flash-ok{{ background:rgba(34,197,94,.12); border:1px solid rgba(34,197,94,.3); color:var(--ok); }}
.flash-err{{ background:rgba(239,68,68,.12); border:1px solid rgba(239,68,68,.3); color:var(--err); }}
@media(max-width:768px){{ .sidebar{{ display:none; }} .main{{ padding:16px; }} }}
</style>
</head><body>
<nav class="sidebar">
  <h2>⚡ TG Stream</h2>
  <a href="/admin" {nav_dashboard}>📊 Dashboard</a>
  <a href="/admin/content" {nav_content}>📁 Content</a>
  <a href="/admin/users" {nav_users}>👤 Users</a>
  <a href="/admin/ads" {nav_ads}>📢 Ads</a>
  <a href="/admin/logs" {nav_logs}>📋 Logs</a>
</nav>
<div class="main">
{body}
</div>
</body></html>"""

ADMIN_LOGIN = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Admin Login</title>
<style>
*{{ margin:0; padding:0; box-sizing:border-box; }}
body{{ background:#0d0d1a; color:#d0d0e8; font-family:'Segoe UI',system-ui,sans-serif;
  display:flex; align-items:center; justify-content:center; min-height:100vh; }}
.card{{ background:#141425; border:1px solid #2a2a48; border-radius:16px;
  padding:40px; width:100%; max-width:380px; text-align:center; }}
h1{{ font-size:1.3rem; margin-bottom:8px; }}
p{{ color:#7777aa; font-size:.85rem; margin-bottom:24px; }}
input{{ background:#1c1c35; border:1px solid #2a2a48; color:#d0d0e8;
  padding:10px 14px; border-radius:8px; width:100%; font-size:.9rem; outline:none; margin-bottom:16px; }}
input:focus{{ border-color:#7c5cfc; }}
button{{ background:#7c5cfc; color:#fff; border:none; padding:10px 24px;
  border-radius:8px; font-size:.9rem; cursor:pointer; width:100%; }}
button:hover{{ background:#a78bfa; }}
.err{{ color:#ef4444; font-size:.82rem; margin-bottom:12px; }}
</style></head><body>
<div class="card">
<h1>⚡ TG Stream Admin</h1>
<p>Enter admin password to continue</p>
{error}
<form method="POST" action="/admin/login">
  <input type="password" name="password" placeholder="Admin Password" required autofocus>
  <button type="submit">Login</button>
</form>
</div>
</body></html>"""

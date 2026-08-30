"""Assemble the interactive map artifact HTML from the generated geometry/data JSON.

Run scripts/build_interactive_map.py first to produce
data/processed/interactive_map_data.json.
"""

from __future__ import annotations

import json

with open("data/processed/interactive_map_data.json") as f:
    DATA = json.load(f)

# Guard against "</script>" appearing inside embedded JSON (e.g. in a name field)
# breaking out of the <script> tag early.
DATA_JSON = json.dumps(DATA, separators=(",", ":")).replace("</", "<\\/")

HTML = f"""<title>US Brewery Density — Interactive Map</title>
<meta name="description" content="Interactive county and metro-area brewery density map with raw, shrunken, and population-floored views.">
<style>
:root {{
  --bg: #f6f3ee;
  --surface: #ffffff;
  --surface-2: #ede8de;
  --text: #241a0f;
  --text-muted: #74675a;
  --border: #ddd4c4;
  --accent: #a8531a;
  --accent-soft: #f0dcc4;
  --nodata: #e3ddd0;
  --ramp-0: #fdf8f0;
  --ramp-1: #f7e2b8;
  --ramp-2: #eab04a;
  --ramp-3: #c97a1f;
  --ramp-4: #9c5514;
  --ramp-5: #6b380d;
  --ramp-6: #3d1f07;
  --shadow: 0 8px 24px rgba(36,26,15,0.12);
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #16130f;
    --surface: #201b15;
    --surface-2: #2b241c;
    --text: #f2e9db;
    --text-muted: #a89680;
    --border: #3a3226;
    --accent: #e0954a;
    --accent-soft: #4a3620;
    --nodata: #2c261e;
    --ramp-0: #2a2015;
    --ramp-1: #4a3620;
    --ramp-2: #7a5528;
    --ramp-3: #a8722f;
    --ramp-4: #d99640;
    --ramp-5: #f5b862;
    --ramp-6: #ffd48a;
    --shadow: 0 8px 28px rgba(0,0,0,0.45);
  }}
}}
:root[data-theme="dark"] {{
  --bg: #16130f; --surface: #201b15; --surface-2: #2b241c; --text: #f2e9db;
  --text-muted: #a89680; --border: #3a3226; --accent: #e0954a; --accent-soft: #4a3620;
  --nodata: #2c261e; --ramp-0: #2a2015; --ramp-1: #4a3620; --ramp-2: #7a5528;
  --ramp-3: #a8722f; --ramp-4: #d99640; --ramp-5: #f5b862; --ramp-6: #ffd48a;
  --shadow: 0 8px 28px rgba(0,0,0,0.45);
}}
:root[data-theme="light"] {{
  --bg: #f6f3ee; --surface: #ffffff; --surface-2: #ede8de; --text: #241a0f;
  --text-muted: #74675a; --border: #ddd4c4; --accent: #a8531a; --accent-soft: #f0dcc4;
  --nodata: #e3ddd0; --ramp-0: #fdf8f0; --ramp-1: #f7e2b8; --ramp-2: #eab04a;
  --ramp-3: #c97a1f; --ramp-4: #9c5514; --ramp-5: #6b380d; --ramp-6: #3d1f07;
  --shadow: 0 8px 24px rgba(36,26,15,0.12);
}}

* {{ box-sizing: border-box; }}
html, body {{ height: 100%; }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  overflow-x: hidden;
}}

header {{
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 14px 22px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  flex-wrap: wrap;
}}
h1 {{
  font-family: "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif;
  font-weight: 600;
  font-size: 1.35rem;
  margin: 0;
  letter-spacing: 0.01em;
  text-wrap: balance;
  white-space: nowrap;
}}
.subtitle {{
  color: var(--text-muted);
  font-size: 0.82rem;
  margin: 2px 0 0;
  white-space: nowrap;
}}
.titleblock {{ display: flex; flex-direction: column; margin-right: 8px; }}

.controls {{ display: flex; gap: 14px; align-items: center; flex-wrap: wrap; margin-left: auto; }}
.toggle-group {{
  display: inline-flex;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--surface-2);
}}
.toggle-group button {{
  border: none;
  background: transparent;
  color: var(--text-muted);
  padding: 7px 13px;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s, color 0.15s;
}}
.toggle-group button + button {{ border-left: 1px solid var(--border); }}
.toggle-group button.active {{ background: var(--accent); color: #fff; }}
.toggle-group button:hover:not(.active) {{ background: var(--border); color: var(--text); }}

.stat-chips {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.chip {{
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 5px 12px;
  font-size: 0.76rem;
  color: var(--text-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}}
.chip b {{ color: var(--text); font-weight: 700; }}

main {{
  position: relative;
  flex: 1;
  min-height: 0;
  background: var(--bg);
}}

#mapwrap {{ position: absolute; inset: 0; overflow: hidden; touch-action: none; cursor: grab; }}
#mapwrap.dragging {{ cursor: grabbing; }}
svg#map {{ width: 100%; height: 100%; display: block; }}

path.unit {{
  stroke: var(--bg);
  stroke-width: 0.6;
  transition: fill 0.25s;
  cursor: pointer;
}}
path.unit:hover {{ stroke: var(--text); stroke-width: 1.4; }}
path.unit.selected {{ stroke: var(--accent); stroke-width: 2.2; }}
path.nodata {{ fill: var(--nodata) !important; cursor: default; }}

.inset-label {{
  font-size: 11px;
  fill: var(--text-muted);
  font-weight: 700;
  pointer-events: none;
}}

#legend {{
  position: absolute;
  left: 18px;
  bottom: 18px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
  box-shadow: var(--shadow);
  font-size: 0.76rem;
  min-width: 150px;
}}
#legend h3 {{ margin: 0 0 8px; font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); font-weight: 700; }}
.legend-row {{ display: flex; align-items: center; gap: 8px; padding: 2px 0; }}
.legend-swatch {{ width: 15px; height: 15px; border-radius: 3px; border: 1px solid var(--border); flex-shrink: 0; }}
.legend-row span {{ color: var(--text-muted); font-variant-numeric: tabular-nums; }}

#detail {{
  position: absolute;
  right: 18px;
  top: 18px;
  width: 258px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  box-shadow: var(--shadow);
  font-size: 0.84rem;
}}
#detail.empty {{ color: var(--text-muted); font-style: italic; }}
#detail h2 {{
  font-family: "Iowan Old Style", Palatino, Georgia, serif;
  font-size: 1.05rem;
  margin: 0 0 2px;
  color: var(--text);
  text-wrap: balance;
}}
#detail .state {{ color: var(--text-muted); font-size: 0.78rem; margin-bottom: 10px; }}
.detail-row {{ display: flex; justify-content: space-between; padding: 5px 0; border-top: 1px solid var(--border); font-variant-numeric: tabular-nums; }}
.detail-row:first-of-type {{ border-top: none; }}
.detail-row .k {{ color: var(--text-muted); }}
.detail-row .v {{ font-weight: 700; }}
.detail-rank {{ margin-top: 10px; padding: 8px 10px; background: var(--accent-soft); border-radius: 8px; font-size: 0.78rem; color: var(--text); }}
.detail-rank b {{ color: var(--accent); }}

#tooltip {{
  position: absolute;
  pointer-events: none;
  background: var(--text);
  color: var(--bg);
  padding: 5px 9px;
  border-radius: 6px;
  font-size: 0.76rem;
  white-space: nowrap;
  transform: translate(-50%, -130%);
  opacity: 0;
  transition: opacity 0.1s;
  z-index: 10;
  font-variant-numeric: tabular-nums;
}}
#tooltip.show {{ opacity: 1; }}

#zoomctl {{
  position: absolute;
  right: 18px;
  bottom: 18px;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  box-shadow: var(--shadow);
}}
#zoomctl button {{
  width: 34px; height: 34px;
  background: var(--surface);
  color: var(--text);
  border: none;
  font-size: 1.1rem;
  cursor: pointer;
  font-family: inherit;
}}
#zoomctl button + button {{ border-top: 1px solid var(--border); }}
#zoomctl button:hover {{ background: var(--surface-2); }}

footer {{
  padding: 8px 22px;
  border-top: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-muted);
  font-size: 0.72rem;
  line-height: 1.5;
}}

@media (max-width: 720px) {{
  #detail {{ width: 200px; font-size: 0.78rem; }}
  .subtitle {{ display: none; }}
  header {{ gap: 10px; padding: 10px 14px; }}
}}
</style>

<header>
  <div class="titleblock">
    <h1>US Brewery Density</h1>
    <p class="subtitle">County &amp; metro-area rates, empirical-Bayes shrunken</p>
  </div>
  <div class="controls">
    <div class="toggle-group" id="levelToggle">
      <button data-level="county" class="active">County</button>
      <button data-level="cbsa">Metro/Micro (CBSA)</button>
    </div>
    <div class="toggle-group" id="modeToggle">
      <button data-mode="shrunk" class="active">Shrunken rate</button>
      <button data-mode="raw">Raw rate</button>
      <button data-mode="floored">Floored (pop ≥ 50k)</button>
    </div>
    <div class="stat-chips">
      <span class="chip"><b id="chipCount">–</b> units shown</span>
      <span class="chip"><b>6,724</b> breweries mapped</span>
    </div>
  </div>
</header>

<main>
  <div id="mapwrap">
    <svg id="map" viewBox="0 0 {DATA['view']['viewW']} {DATA['view']['viewH']}" preserveAspectRatio="xMidYMid meet">
      <g id="unitsLayer"></g>
      <text class="inset-label" id="akLabel"></text>
      <text class="inset-label" id="hiLabel"></text>
    </svg>
  </div>
  <div id="legend">
    <h3>Breweries / 100k adults 21+</h3>
    <div id="legendRows"></div>
  </div>
  <div id="detail" class="empty">Hover or click a county to see its numbers.</div>
  <div id="tooltip"></div>
  <div id="zoomctl">
    <button id="zoomIn" aria-label="Zoom in">+</button>
    <button id="zoomOut" aria-label="Zoom out">−</button>
    <button id="zoomReset" aria-label="Reset view" style="font-size:0.65rem;">RESET</button>
  </div>
</main>

<footer>
  Sources: Open Brewery DB, Census ACS 5-year (2020-2024). Shrunken rate: empirical Bayes
  partial pooling toward the national mean (method-of-moments Poisson-Gamma), calibrated
  against state liquor-license registries in 10 states. OBDB undercounts true brewery count
  by an amount that varies by state (measured 7-38% across calibration states) &mdash; this
  map is not capture-rate-corrected. Full methodology: docs/methods_memo.md in the project repo.
</footer>

<script>
const DATA = {DATA_JSON};

const RAMP = ['var(--ramp-0)','var(--ramp-1)','var(--ramp-2)','var(--ramp-3)','var(--ramp-4)','var(--ramp-5)','var(--ramp-6)'];
const BINS = [0, 1, 3, 6, 10, 15, Infinity];
const BIN_LABELS = ['0–1','1–3','3–6','6–10','10–15','15+'];
const POP_FLOOR = 50000;

let level = 'county';
let mode = 'shrunk';
let selectedId = null;
let viewBox = {{x:0, y:0, w: DATA.view.viewW, h: DATA.view.viewH}};
const baseViewBox = {{...viewBox}};

function binIndex(v) {{
  for (let i = 0; i < BINS.length - 1; i++) {{
    if (v < BINS[i+1]) return i;
  }}
  return BINS.length - 2;
}}

function valueFor(rec) {{
  if (mode === 'raw') return rec.raw;
  if (mode === 'floored') return rec.pop21 >= POP_FLOOR ? rec.shrunk : null;
  return rec.shrunk;
}}

function renderLegend() {{
  const rows = document.getElementById('legendRows');
  rows.innerHTML = '';
  for (let i = 0; i < BIN_LABELS.length; i++) {{
    const row = document.createElement('div');
    row.className = 'legend-row';
    row.innerHTML = `<span class="legend-swatch" style="background:${{RAMP[i]}}"></span><span>${{BIN_LABELS[i]}}</span>`;
    rows.appendChild(row);
  }}
  const nd = document.createElement('div');
  nd.className = 'legend-row';
  nd.innerHTML = `<span class="legend-swatch" style="background:var(--nodata)"></span><span>${{mode === 'floored' ? 'No data / under floor' : 'No data'}}</span>`;
  rows.appendChild(nd);
}}

function buildLayer() {{
  const paths = level === 'county' ? DATA.countyPaths : DATA.cbsaPaths;
  const data = level === 'county' ? DATA.countyData : DATA.cbsaData;
  const layer = document.getElementById('unitsLayer');
  layer.innerHTML = '';
  let shown = 0;
  for (const id in paths) {{
    const d = paths[id];
    if (!d) continue;
    const rec = data[id];
    const el = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    el.setAttribute('d', d);
    el.setAttribute('data-id', id);
    el.classList.add('unit');
    if (rec) shown++;
    layer.appendChild(el);
  }}
  document.getElementById('chipCount').textContent = shown.toLocaleString();
  colorLayer();
}}

function colorLayer() {{
  const data = level === 'county' ? DATA.countyData : DATA.cbsaData;
  document.querySelectorAll('#unitsLayer path.unit').forEach(el => {{
    const rec = data[el.getAttribute('data-id')];
    if (!rec) {{ el.classList.add('nodata'); el.style.fill = ''; return; }}
    const v = valueFor(rec);
    if (v === null || v === undefined) {{ el.classList.add('nodata'); el.style.fill = ''; return; }}
    el.classList.remove('nodata');
    el.style.fill = RAMP[binIndex(v)];
  }});
}}

function showDetail(rec, id) {{
  const panel = document.getElementById('detail');
  if (!rec) {{ panel.className = 'empty'; panel.textContent = 'Hover or click a county to see its numbers.'; return; }}
  panel.className = '';
  const rankLine = level === 'county'
    ? `Rank <b>#${{rec.rank.toLocaleString()}}</b> of ${{Object.keys(DATA.countyData).length.toLocaleString()}} counties nationally` +
      (rec.rankFloored ? `<br>Rank <b>#${{rec.rankFloored}}</b> among counties ≥ 50k adults 21+` : '<br><span style="color:var(--text-muted)">Below 50k-adult population floor</span>')
    : `Rank <b>#${{rec.rank.toLocaleString()}}</b> of ${{Object.keys(DATA.cbsaData).length.toLocaleString()}} CBSAs nationally`;
  panel.innerHTML = `
    <h2>${{rec.name}}</h2>
    <div class="state">${{rec.state ? rec.state : ''}}</div>
    <div class="detail-row"><span class="k">Breweries (OBDB)</span><span class="v">${{rec.count}}</span></div>
    <div class="detail-row"><span class="k">Adults 21+</span><span class="v">${{rec.pop21.toLocaleString()}}</span></div>
    <div class="detail-row"><span class="k">Raw rate /100k</span><span class="v">${{rec.raw.toFixed(1)}}</span></div>
    <div class="detail-row"><span class="k">Shrunken rate /100k</span><span class="v">${{rec.shrunk.toFixed(1)}}</span></div>
    ${{rec.ciLow !== undefined ? `<div class="detail-row"><span class="k">95% interval</span><span class="v">${{rec.ciLow.toFixed(1)}}–${{rec.ciHigh.toFixed(1)}}</span></div>` : ''}}
    <div class="detail-rank">${{rankLine}}</div>
  `;
}}

const mapwrap = document.getElementById('mapwrap');
const svg = document.getElementById('map');
const tooltip = document.getElementById('tooltip');

function applyViewBox() {{
  svg.setAttribute('viewBox', `${{viewBox.x}} ${{viewBox.y}} ${{viewBox.w}} ${{viewBox.h}}`);
}}

document.getElementById('unitsLayer').addEventListener('mousemove', (e) => {{
  const target = e.target.closest('path.unit');
  if (!target) return;
  const data = level === 'county' ? DATA.countyData : DATA.cbsaData;
  const rec = data[target.getAttribute('data-id')];
  const rect = mapwrap.getBoundingClientRect();
  tooltip.style.left = (e.clientX - rect.left) + 'px';
  tooltip.style.top = (e.clientY - rect.top) + 'px';
  if (rec) {{
    tooltip.textContent = `${{rec.name}}${{rec.state ? ', ' + rec.state : ''}} — ${{valueFor(rec) !== null ? valueFor(rec).toFixed(1) : 'n/a'}} /100k`;
    tooltip.classList.add('show');
  }} else {{
    tooltip.classList.remove('show');
  }}
}});
document.getElementById('unitsLayer').addEventListener('mouseleave', () => tooltip.classList.remove('show'));
document.getElementById('unitsLayer').addEventListener('mouseover', (e) => {{
  const target = e.target.closest('path.unit');
  if (!target) return;
  const data = level === 'county' ? DATA.countyData : DATA.cbsaData;
  const rec = data[target.getAttribute('data-id')];
  if (rec && !selectedId) showDetail(rec, target.getAttribute('data-id'));
}});
document.getElementById('unitsLayer').addEventListener('click', (e) => {{
  const target = e.target.closest('path.unit');
  document.querySelectorAll('path.unit.selected').forEach(p => p.classList.remove('selected'));
  if (!target) {{ selectedId = null; showDetail(null); return; }}
  const data = level === 'county' ? DATA.countyData : DATA.cbsaData;
  const id = target.getAttribute('data-id');
  const rec = data[id];
  if (!rec) {{ selectedId = null; showDetail(null); return; }}
  selectedId = id;
  target.classList.add('selected');
  showDetail(rec, id);
}});

// Pan
let dragging = false, dragStart = null, vbStart = null;
mapwrap.addEventListener('pointerdown', (e) => {{
  dragging = true; mapwrap.classList.add('dragging');
  dragStart = {{x: e.clientX, y: e.clientY}};
  vbStart = {{...viewBox}};
  mapwrap.setPointerCapture(e.pointerId);
}});
mapwrap.addEventListener('pointermove', (e) => {{
  if (!dragging) return;
  const rect = mapwrap.getBoundingClientRect();
  const scale = viewBox.w / rect.width;
  viewBox.x = vbStart.x - (e.clientX - dragStart.x) * scale;
  viewBox.y = vbStart.y - (e.clientY - dragStart.y) * scale;
  applyViewBox();
}});
mapwrap.addEventListener('pointerup', (e) => {{ dragging = false; mapwrap.classList.remove('dragging'); mapwrap.releasePointerCapture(e.pointerId); }});

// Zoom
function zoomAt(factor, cx, cy) {{
  const newW = Math.max(80, Math.min(baseViewBox.w * 3, viewBox.w * factor));
  const newH = newW * (viewBox.h / viewBox.w);
  viewBox.x = cx - (cx - viewBox.x) * (newW / viewBox.w);
  viewBox.y = cy - (cy - viewBox.y) * (newH / viewBox.h);
  viewBox.w = newW; viewBox.h = newH;
  applyViewBox();
}}
mapwrap.addEventListener('wheel', (e) => {{
  e.preventDefault();
  const rect = mapwrap.getBoundingClientRect();
  const cx = viewBox.x + (e.clientX - rect.left) / rect.width * viewBox.w;
  const cy = viewBox.y + (e.clientY - rect.top) / rect.height * viewBox.h;
  zoomAt(e.deltaY > 0 ? 1.15 : 0.87, cx, cy);
}}, {{passive: false}});
document.getElementById('zoomIn').addEventListener('click', () => zoomAt(0.8, viewBox.x + viewBox.w/2, viewBox.y + viewBox.h/2));
document.getElementById('zoomOut').addEventListener('click', () => zoomAt(1.25, viewBox.x + viewBox.w/2, viewBox.y + viewBox.h/2));
document.getElementById('zoomReset').addEventListener('click', () => {{ viewBox = {{...baseViewBox}}; applyViewBox(); }});

// Toggles
document.getElementById('levelToggle').addEventListener('click', (e) => {{
  const btn = e.target.closest('button');
  if (!btn) return;
  document.querySelectorAll('#levelToggle button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  level = btn.getAttribute('data-level');
  selectedId = null;
  showDetail(null);
  buildLayer();
}});
document.getElementById('modeToggle').addEventListener('click', (e) => {{
  const btn = e.target.closest('button');
  if (!btn) return;
  document.querySelectorAll('#modeToggle button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  mode = btn.getAttribute('data-mode');
  colorLayer();
}});

renderLegend();
buildLayer();
applyViewBox();
document.getElementById('akLabel').setAttribute('x', DATA.view.akBox[0]);
document.getElementById('akLabel').setAttribute('y', DATA.view.akBox[1] - 4);
document.getElementById('akLabel').textContent = 'AK';
document.getElementById('hiLabel').setAttribute('x', DATA.view.hiBox[0]);
document.getElementById('hiLabel').setAttribute('y', DATA.view.hiBox[1] - 4);
document.getElementById('hiLabel').textContent = 'HI';
</script>
"""

out_path = "/private/tmp/claude-502/-Users-jacobelder-Documents-GitHub-breweryDensityAnalysis/ba2855b0-e31a-40ee-b366-2833f18caa1b/scratchpad/brewery_interactive_map.html"
with open(out_path, "w") as f:
    f.write(HTML)

import os
print(f"Wrote {out_path} ({os.path.getsize(out_path) / 1e6:.2f}MB)")

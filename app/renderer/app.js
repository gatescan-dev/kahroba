/* Kahroba UI - plain JS, no build step. Talks to the WSL backend over HTTP. */
'use strict';
const API = 'http://127.0.0.1:8770/api';
let TOKEN = ''; // per-start access token from the backend (read by the desktop shell)
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
}
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function api(path, opts = {}) {
  const r = await fetch(API + path, {
    method: opts.method || 'GET',
    headers: { 'X-Kahroba-Token': TOKEN, ...(opts.text !== undefined ? { 'Content-Type': 'text/plain' } : (opts.body ? { 'Content-Type': 'application/json' } : {})) },
    body: opts.text !== undefined ? opts.text : (opts.body ? JSON.stringify(opts.body) : undefined),
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r.text();
}
const fileURL = (p) => `${API}/file?path=${encodeURIComponent(p)}&t=${encodeURIComponent(TOKEN)}`;

// Browser mode (UI served by the backend at /ui): no Electron bridge.
if (!window.kahroba) {
  window.kahroba = {
    browser: true,
    boot: async () => ({ state: (await fetch(`${API}/health`).then((r) => r.ok).catch(() => false)) ? 'ready' : 'backend-failed', distro: 'browser' }),
    netlistsvg: async () => { throw new Error('netlistsvg needs the desktop app'); },
    openExternal: async (u) => window.open(u, '_blank'),
    wslPath: async (p) => p, reveal: async () => toast('Available in the desktop app'),
    pickFiles: async () => { toast('Use the desktop app to import files'); return []; },
    surferNative: async () => ({ ok: false }), runSetup: async () => false,
  };
}

function toast(msg, kind = '') {
  const t = h('div', { class: `toast ${kind}` }, msg);
  $('#toastRoot').append(t);
  setTimeout(() => t.remove(), kind === 'err' ? 8000 : 4500);
}

// ------------------------------------------------------------------ state
const S = {
  project: null, cfg: null, tree: [], open: [], active: null, models: {},
  editor: null, monaco: null, history: [], live: {}, last: {}, side: 'explorer',
  collapsed: new Set(), sel: null,
};
const LANG = (p) => (/\.(vhd|vhdl)$/i.test(p) ? 'vhdl' : /\.(sv|svh)$/i.test(p) ? 'systemverilog'
  : /\.(v|vh)$/i.test(p) ? 'verilog' : /\.py$/.test(p) ? 'python' : /\.(sdc|tcl|xdc)$/.test(p) ? 'tcl'
    : /\.json$/.test(p) ? 'json' : /\.md$/.test(p) ? 'markdown' : 'plaintext');
const ICON = (p) => {
  if (/\.(v|sv|vh|svh)$/i.test(p)) return h('span', { class: 'fi v' }, 'V');
  if (/\.(vhd|vhdl)$/i.test(p)) return h('span', { class: 'fi vh' }, 'VH');
  if (/\.py$/.test(p)) return h('span', { class: 'fi py' }, 'PY');
  if (/\.(sdc|tcl|xdc)$/.test(p)) return h('span', { class: 'fi sdc' }, 'C');
  return h('span', { class: 'fi o' }, '·');
};

// ------------------------------------------------------------------ monaco
function initMonaco() {
  return new Promise((resolve) => {
    window.require.config({ paths: { vs: '../node_modules/monaco-editor/min/vs' } });
    window.require(['vs/editor/editor.main'], () => {
      const m = window.monaco;
      m.languages.register({ id: 'vhdl', extensions: ['.vhd', '.vhdl'] });
      m.languages.setMonarchTokensProvider('vhdl', {
        ignoreCase: true,
        keywords: ['library', 'use', 'entity', 'is', 'port', 'generic', 'end', 'architecture', 'of', 'begin', 'signal',
          'variable', 'constant', 'process', 'if', 'then', 'elsif', 'else', 'case', 'when', 'others', 'for', 'loop', 'while',
          'generate', 'component', 'map', 'in', 'out', 'inout', 'buffer', 'downto', 'to', 'type', 'subtype', 'array', 'record',
          'function', 'procedure', 'return', 'and', 'or', 'not', 'xor', 'nand', 'nor', 'xnor', 'all', 'package', 'body',
          'wait', 'until', 'after', 'null', 'with', 'select', 'rising_edge', 'falling_edge', 'open', 'range', 'attribute'],
        types: ['std_logic', 'std_logic_vector', 'unsigned', 'signed', 'integer', 'natural', 'positive', 'boolean', 'bit', 'bit_vector', 'string', 'real', 'time'],
        tokenizer: { root: [
          [/--.*$/, 'comment'],
          [/"[^"]*"/, 'string'], [/'[01xzuXZU\-]'/, 'string'],
          [/\b\d+(\.\d+)?\b/, 'number'],
          [/[a-zA-Z_]\w*/, { cases: { '@keywords': 'keyword', '@types': 'type', '@default': 'identifier' } }],
          [/[<=>:+\-*\/&|]+/, 'operator'],
        ] },
      });
      m.editor.defineTheme('kahroba', {
        base: 'vs-dark', inherit: true,
        rules: [
          { token: 'comment', foreground: '5f6d85', fontStyle: 'italic' },
          { token: 'keyword', foreground: 'f4a83a' }, { token: 'type', foreground: 'b18cff' },
          { token: 'number', foreground: '7ee0b5' }, { token: 'string', foreground: 'ffcf7a' },
          { token: 'identifier', foreground: 'dbe2ee' },
        ],
        colors: { 'editor.background': '#0b0f16', 'editorLineNumber.foreground': '#3a4559',
          'editorLineNumber.activeForeground': '#f4a83a', 'editor.lineHighlightBackground': '#121925',
          'editorCursor.foreground': '#ffcf7a', 'editor.selectionBackground': '#f4a83a33',
          'editorIndentGuide.background1': '#1a2230', 'editorWidget.background': '#121925' },
      });
      S.monaco = m;
      S.editor = m.editor.create($('#editorHost'), {
        theme: 'kahroba', automaticLayout: true, fontFamily: 'Cascadia Code, JetBrains Mono, Consolas',
        fontSize: 13.5, fontLigatures: false, minimap: { enabled: true, scale: 1 }, smoothScrolling: true,
        cursorBlinking: 'smooth', cursorSmoothCaretAnimation: 'on', renderLineHighlight: 'all',
        padding: { top: 12 }, scrollBeyondLastLine: false, bracketPairColorization: { enabled: true },
        model: null,
      });
      S.editor.onDidChangeCursorPosition((e) => { $('#sbCursor').textContent = `Ln ${e.position.lineNumber}, Col ${e.position.column}`; });
      S.editor.addCommand(m.KeyMod.CtrlCmd | m.KeyCode.KeyS, () => saveActive());
      resolve();
    });
  });
}

// ------------------------------------------------------------------ tabs
function tabKey(t) { return t.kind === 'file' ? 'f:' + t.path : t.kind + ':' + (t.id || ''); }
function openTab(t) {
  const k = tabKey(t);
  let ex = S.open.find((x) => tabKey(x) === k);
  if (!ex) { S.open.push(t); ex = t; } else Object.assign(ex, t);
  activate(ex);
}
function activate(t) {
  S.active = t;
  renderTabs();
  $$('#views > .view').forEach((v) => v.classList.add('hide'));
  if (t.kind === 'file') {
    $('#editorHost').classList.remove('hide');
    const model = S.models[t.path];
    if (model) S.editor.setModel(model);
    $('#sbLang').textContent = LANG(t.path);
    S.editor.focus();
  } else {
    let v = $(`#view-${CSS.escape(tabKey(t))}`);
    if (!v) { v = h('div', { class: 'view', id: `view-${tabKey(t)}` }); $('#views').append(v); }
    v.classList.remove('hide');
    if (!t.rendered || t.rerender) { t.rendered = true; t.rerender = false; v.innerHTML = ''; t.render(v); }
    $('#sbLang').textContent = '';
  }
}
function closeTab(t, ev) {
  if (ev) ev.stopPropagation();
  if (t.kind === 'file' && S.models[t.path] && t.dirty && !confirm(`${t.path} has unsaved changes. Close anyway?`)) return;
  const i = S.open.indexOf(t);
  S.open.splice(i, 1);
  if (t.kind === 'file') { S.models[t.path]?.dispose(); delete S.models[t.path]; }
  else $(`#view-${CSS.escape(tabKey(t))}`)?.remove();
  if (S.active === t) { const n = S.open[Math.max(0, i - 1)]; if (n) activate(n); else { S.active = null; S.editor.setModel(null); renderTabs(); showWelcome(); } }
  else renderTabs();
}
function renderTabs() {
  const el = $('#tabs'); el.innerHTML = '';
  for (const t of S.open) {
    el.append(h('div', { class: `tab ${t === S.active ? 'active' : ''} ${t.dirty ? 'dirty' : ''}`, onclick: () => activate(t), title: t.path || t.title },
      t.kind === 'file' ? ICON(t.path) : h('i', { class: `ic ${t.icon || 'ic-chip'}`, style: 'width:14px;height:14px;color:var(--amber)' }),
      t.kind === 'file' ? t.path.split('/').pop() : t.title,
      h('span', { class: 'x', onclick: (e) => closeTab(t, e) }, '×')));
  }
}

async function openFile(path, line) {
  if (!S.models[path]) {
    const text = await api(`/projects/${S.project}/file?path=${encodeURIComponent(path)}`);
    const model = S.monaco.editor.createModel(text, LANG(path));
    S.models[path] = model;
    const tab = { kind: 'file', path, dirty: false };
    model.onDidChangeContent(() => { if (!tab.dirty) { tab.dirty = true; renderTabs(); } });
    openTab(tab);
  } else openTab(S.open.find((x) => x.path === path) || { kind: 'file', path });
  if (line) { S.editor.revealLineInCenter(line); S.editor.setPosition({ lineNumber: line, column: 1 }); }
}
async function saveActive() {
  const t = S.active;
  if (!t || t.kind !== 'file') return;
  await api(`/projects/${S.project}/file?path=${encodeURIComponent(t.path)}`, { method: 'PUT', text: S.models[t.path].getValue() });
  t.dirty = false; renderTabs(); toast(`Saved ${t.path}`, 'ok');
}
async function saveAll() {
  for (const t of S.open.filter((x) => x.kind === 'file' && x.dirty)) {
    await api(`/projects/${S.project}/file?path=${encodeURIComponent(t.path)}`, { method: 'PUT', text: S.models[t.path].getValue() });
    t.dirty = false;
  }
  renderTabs();
}

// ------------------------------------------------------------------ side panes
function setSide(which) {
  S.side = which;
  $$('#activity button').forEach((b) => b.classList.toggle('active', b.dataset.side === which));
  renderSide();
}
function renderSide() {
  const body = $('#sideBody'), tools = $('#sideTools');
  body.innerHTML = ''; tools.innerHTML = '';
  const tb = (icon, title, fn) => tools.append(h('button', { title, onclick: fn }, h('i', { class: `ic ${icon}` })));
  if (S.side === 'explorer') {
    $('#sideTitle').textContent = S.project ? S.project.toUpperCase() : 'PROJECT';
    if (!S.project) { body.append(h('div', { style: 'padding:14px;color:var(--muted)' }, 'Open or create a project.')); return; }
    tb('ic-plus', 'New file', () => newFileDialog());
    tb('ic-folder', 'New folder', () => newFolderDialog());
    tb('ic-import', 'Add existing files', () => importFiles());
    tb('ic-refresh', 'Refresh', () => loadProject(S.project));
    renderTree(body);
  } else if (S.side === 'pdks') {
    $('#sideTitle').textContent = 'TECHNOLOGIES';
    renderPdkSide(body);
  } else if (S.side === 'runs') {
    $('#sideTitle').textContent = 'RUN HISTORY';
    tb('ic-refresh', 'Refresh', () => refreshHistory().then(renderSide));
    for (const j of S.history) {
      body.append(h('div', { class: 'tree-row', style: 'padding-left:14px', onclick: () => openJobView(j) },
        h('span', { class: `dot ${j.status === 'ok' ? 'ok' : j.status === 'failed' ? 'bad' : ''}` }),
        h('span', { class: 'nm' }, j.title), h('span', { class: 'badge' }, j.id.slice(9, 15).replace(/(..)(..)(..)/, '$1:$2:$3'))));
    }
  } else if (S.side === 'tools') {
    $('#sideTitle').textContent = 'TOOLCHAIN';
    body.append(h('div', { style: 'padding:6px 14px;color:var(--muted)' }, 'Loading…'));
    api('/tools').then((r) => {
      body.innerHTML = '';
      for (const [k, v] of Object.entries(r.tools)) {
        body.append(h('div', { class: 'tree-row', style: 'padding-left:14px', title: v.path || '' },
          h('span', { class: `dot ${v.version ? 'ok' : 'bad'}` }), h('b', { style: 'width:84px' }, k),
          h('span', { class: 'nm', style: 'color:var(--muted);font-size:11.5px' }, v.version || 'missing')));
      }
    });
  }
}

function renderTree(body) {
  const items = S.tree;
  const top = S.cfg?.top;
  const topFile = S.units?.find((u) => u.name === top)?.file;
  const kids = (dir) => items.filter((it) => {
    const parent = it.path.includes('/') ? it.path.slice(0, it.path.lastIndexOf('/')) : '';
    return parent === dir;
  }).sort((a, b) => (b.dir - a.dir) || a.path.localeCompare(b.path));
  const walk = (dir, depth) => {
    for (const it of kids(dir)) {
      if (it.path === 'kahroba.json') continue;
      const name = it.path.split('/').pop();
      const open = !S.collapsed.has(it.path);
      const row = h('div', {
        class: `tree-row ${S.sel === it.path ? 'sel' : ''}`, style: `padding-left:${10 + depth * 14}px`,
        onclick: () => { S.sel = it.path; if (it.dir) { open ? S.collapsed.add(it.path) : S.collapsed.delete(it.path); } else openFile(it.path); renderSide(); },
        oncontextmenu: (e) => { e.preventDefault(); treeMenu(it, e); },
      }, h('span', { class: 'tw' }, it.dir ? (open ? '▾' : '▸') : ''),
      it.dir ? h('span', { class: 'fi dir' }, '■') : ICON(name),
      h('span', { class: 'nm' }, name),
      it.path === topFile ? h('span', { class: 'badge' }, 'TOP') : null);
      body.append(row);
      if (it.dir && open) walk(it.path, depth + 1);
    }
  };
  walk('', 0);
}

function treeMenu(it, e) {
  const items = [];
  if (!it.dir && /\.(v|sv|vhd|vhdl)$/i.test(it.path)) {
    const us = (S.units || []).filter((u) => u.file === it.path);
    for (const u of us) items.push([`Set "${u.name}" as top`, async () => { S.cfg.top = u.name; await saveCfg(); }]);
    items.push(['New cocotb testbench for this file', () => newTbDialog(us[0]?.name)]);
  }
  items.push(['Rename…', async () => {
    const n = prompt('New path', it.path); if (!n || n === it.path) return;
    await api(`/projects/${S.project}/rename`, { method: 'POST', body: { src: it.path, dst: n } }); loadProject(S.project);
  }]);
  items.push(['Delete', async () => {
    if (!confirm(`Delete ${it.path}?`)) return;
    await api(`/projects/${S.project}/file?path=${encodeURIComponent(it.path)}`, { method: 'DELETE' }); loadProject(S.project);
  }]);
  items.push(['Reveal in Windows Explorer', () => window.kahroba.reveal(`${S.home}/projects/${S.project}/${it.path}`)]);
  popupMenu(items, e.clientX, e.clientY);
}
function popupMenu(items, x, y) {
  $('.ctx')?.remove();
  const m = h('div', { class: 'ctx card', style: `position:fixed;left:${x}px;top:${y}px;z-index:70;padding:6px;min-width:220px;box-shadow:0 12px 30px rgba(0,0,0,.45)` },
    items.map(([t, fn]) => h('div', { class: 'tree-row', style: 'padding:0 10px;border-radius:6px', onclick: () => { m.remove(); fn(); } }, t)));
  document.body.append(m);
  setTimeout(() => document.addEventListener('click', () => m.remove(), { once: true }), 0);
}

// ------------------------------------------------------------------ flow navigator
const FLOW = [
  { id: 'pm', title: 'PROJECT MANAGER', items: [
    ['Project settings', () => openSettings()],
    ['Add sources…', () => importFiles()],
    ['New design file…', () => newFileDialog()],
  ] },
  { id: 'rtl', title: 'RTL ANALYSIS', kinds: ['lint', 'rtl'], items: [
    ['Run lint', () => runStep('lint'), 'lint'],
    ['Elaborated schematic', () => runStep('rtl'), 'rtl'],
  ] },
  { id: 'sim', title: 'SIMULATION', kinds: ['sim'], items: [
    ['New cocotb testbench…', () => newTbDialog()],
    ['Run simulation', () => runStep('sim'), 'sim', true],
    ['Open waveform (Surfer)', () => openLastWaves()],
  ] },
  { id: 'syn', title: 'SYNTHESIS', kinds: ['synth'], items: [
    ['Run synthesis', () => runStep('synth'), 'synth', true],
    ['Synthesized schematic', () => openSynthSchematic()],
    ['Synthesis report', () => openLast('synth')],
  ] },
  { id: 'impl', title: 'IMPLEMENTATION', kinds: ['impl'], items: [
    ['Choose technology…', () => setSide('pdks')],
    ['Run RTL → GDS', () => runStep('impl'), 'impl', true],
    ['Layout (GDS)', () => openLayout()],
    ['Open in KLayout', () => openKLayout()],
  ] },
  { id: 'sign', title: 'SIGN-OFF REPORTS', kinds: ['thermal'], items: [
    ['Timing / power / area', () => openLast('impl')],
    ['Thermal map', () => runThermal(), 'thermal'],
  ] },
];
function renderFlow() {
  const b = $('#flowBody'); b.innerHTML = '';
  FLOW.forEach((g, gi) => {
    const sts = (g.kinds || []).map((k) => S.live[k]?.status || S.last[k]?.status);
    const cls = sts.includes('running') || sts.includes('queued') ? 'run' : sts.includes('failed') ? 'bad' : sts.length && sts.every((x) => x === 'ok') ? 'ok' : '';
    b.append(h('div', { class: 'fn-group' }, h('div', { class: `fn-title ${cls}` }, h('span', { class: 'num' }, cls === 'ok' ? '✓' : gi + 1), g.title),
      g.items.map(([t, fn, kind, primary]) => h('div', { class: `fn-item ${primary ? 'primary' : ''}`, onclick: () => (S.project ? fn() : toast('Open a project first')) },
        primary ? h('i', { class: 'ic ic-play', style: 'width:11px;height:11px' }) : null, t,
        kind ? h('span', { class: `st ${S.live[kind]?.status || S.last[kind]?.status || ''}` }) : null))));
  });
}

// ------------------------------------------------------------------ projects
async function loadProject(name) {
  const r = await api(`/projects/${name}`);
  S.project = name; S.cfg = r.config; S.tree = r.tree;
  try { S.units = await api(`/projects/${name}/units`); } catch (_) { S.units = []; }
  localStorage.setItem('kahroba.project', name);
  $('#projName').textContent = name;
  $('#sbTop').textContent = `top: ${S.cfg.top || '—'}`;
  $('#sbPdk').textContent = `${S.cfg.target.pdk}`;
  await refreshHistory();
  renderSide(); renderFlow();
}
async function refreshHistory() {
  if (!S.project) return;
  S.history = await api(`/projects/${S.project}/history`);
  S.last = {};
  for (const j of S.history.slice().reverse()) S.last[j.kind] = j;
}
async function saveCfg() {
  S.cfg = await api(`/projects/${S.project}`, { method: 'PUT', body: S.cfg });
  await loadProject(S.project);
  toast('Project settings saved', 'ok');
}

function showWelcome() {
  if (S.open.length) return;
  openTab({ kind: 'welcome', id: 'w', title: 'Welcome', icon: 'ic-files', render: renderWelcome });
}
async function renderWelcome(v) {
  const list = await api('/projects').catch(() => []);
  const ex = await api('/examples').catch(() => []);
  v.append(h('div', { class: 'welcome' }, h('div', { class: 'box' },
    h('div', { class: 'hero' }, h('span', { class: 'gem' }), h('div', {}, h('h1', {}, 'Kahroba'),
      h('p', {}, 'RTL → GDSII studio · Yosys · OpenROAD · OpenSTA · LibreLane · cocotb · Surfer'))),
    h('div', { class: 'wl' },
      h('div', { class: 'card', onclick: () => newProjectDialog() }, h('b', {}, '＋ New project'), h('span', {}, 'Empty project with src/, tb/, constraints/')),
      h('div', { class: 'card', onclick: () => newProjectDialog(true) }, h('b', {}, '◆ Start from an example'), h('span', {}, ex.map((e) => e.id).join(' · ') || 'counter · mac8 · pwm_vhdl'))),
    h('h2', { style: 'color:var(--muted);font-size:12px;letter-spacing:.8px;margin:28px 0 8px' }, 'RECENT PROJECTS'),
    h('div', { class: 'plist' }, list.length ? list.map((p) => h('div', { class: 'tree-row', onclick: () => loadProject(p.name) },
      h('span', { class: 'fi dir' }, '■'), h('b', {}, p.name), h('span', { style: 'color:var(--muted);margin-left:10px' }, `top ${p.top || '—'} · ${p.pdk || ''}`))) : h('div', { style: 'color:var(--dim)' }, 'No projects yet.')))));
}

// ------------------------------------------------------------------ dialogs
function modal(title, body, actions) {
  const back = h('div', { class: 'back', onclick: (e) => { if (e.target === back) back.remove(); } });
  const close = () => back.remove();
  back.append(h('div', { class: 'modal' }, h('h3', {}, title), body,
    h('div', { class: 'actions' }, h('button', { class: 'btn', onclick: close }, 'Cancel'),
      ...actions.map(([t, fn, primary]) => h('button', { class: `btn ${primary ? 'primary' : ''}`, onclick: async () => { try { if ((await fn()) !== false) close(); } catch (e) { toast(e.message, 'err'); } } }, t)))));
  $('#modalRoot').append(back);
  setTimeout(() => back.querySelector('input')?.focus(), 30);
}
async function newProjectDialog(fromExample) {
  const ex = await api('/examples').catch(() => []);
  let pick = fromExample ? (ex[0]?.id || null) : null;
  const name = h('input', { placeholder: 'my_design', value: pick || '' });
  const ch = h('div', { class: 'choice' }, h('button', { class: pick ? '' : 'sel', 'data-id': '' }, h('b', {}, 'Empty'), h('span', {}, 'blank project')),
    ex.map((e) => h('button', { class: pick === e.id ? 'sel' : '', 'data-id': e.id }, h('b', {}, e.id), h('span', {}, e.about))));
  ch.addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; pick = b.dataset.id || null; $$('button', ch).forEach((x) => x.classList.toggle('sel', x === b)); if (pick && !name.value) name.value = pick; });
  modal('New project', h('div', {}, h('label', {}, 'Name'), name, h('label', {}, 'Template'), ch), [['Create', async () => {
    await api('/projects', { method: 'POST', body: { name: name.value.trim(), template: pick } });
    $$('.tab').length && S.open.filter((t) => t.kind === 'welcome').forEach((t) => closeTab(t));
    await loadProject(name.value.trim());
    const top = S.units.find((u) => u.name === S.cfg.top);
    if (top) openFile(top.file);
  }, true]]);
}
function newFileDialog() {
  let kind = 'verilog';
  const name = h('input', { placeholder: 'module_name' });
  const ch = h('div', { class: 'choice' }, [['verilog', 'Verilog', 'src/*.v'], ['vhdl', 'VHDL', 'src/*.vhd'], ['sdc', 'Constraints', 'constraints/*.sdc'], ['cocotb', 'cocotb test', 'tb/*.py']].map(([k, t, d]) =>
    h('button', { class: k === kind ? 'sel' : '', 'data-k': k }, h('b', {}, t), h('span', {}, d))));
  ch.addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; kind = b.dataset.k; $$('button', ch).forEach((x) => x.classList.toggle('sel', x === b)); });
  modal('New file', h('div', {}, h('label', {}, 'Type'), ch, h('label', {}, 'Name (no extension)'), name), [['Create', async () => {
    const r = await api(`/projects/${S.project}/new-file`, { method: 'POST', body: { kind, name: name.value.trim() } });
    await loadProject(S.project); openFile(r.path);
  }, true]]);
}
function newFolderDialog() {
  const name = h('input', { placeholder: 'src/peripherals' });
  modal('New folder', h('div', {}, h('label', {}, 'Path inside the project'), name), [['Create', async () => {
    await api(`/projects/${S.project}/mkdir`, { method: 'POST', body: { path: name.value.trim() } }); loadProject(S.project);
  }, true]]);
}
function newTbDialog(top) {
  const units = S.units || [];
  const sel = h('select', {}, units.map((u) => h('option', { value: u.name, selected: u.name === (top || S.cfg.top) ? 'selected' : null }, `${u.name}  (${u.file})`)));
  const name = h('input', { value: `test_${top || S.cfg.top || 'dut'}` });
  sel.onchange = () => { name.value = `test_${sel.value}`; };
  modal('New cocotb testbench', h('div', {},
    h('p', { style: 'color:var(--muted);margin:0' }, 'Kahroba writes a commented Python testbench for the chosen unit: clock, reset, random stimulus and a place for your checks.'),
    h('label', {}, 'Design under test'), sel, h('label', {}, 'Test module name'), name), [['Create', async () => {
    const r = await api(`/projects/${S.project}/new-file`, { method: 'POST', body: { kind: 'cocotb', name: name.value.trim(), top: sel.value } });
    if (!S.cfg.sim.test_module) { S.cfg.sim.test_module = name.value.trim(); await saveCfg(); }
    await loadProject(S.project); openFile(r.path);
  }, true]]);
}
async function importFiles() {
  const files = await window.kahroba.pickFiles();
  for (const f of files) {
    const dir = /\.py$/.test(f.name) ? 'tb' : /\.(sdc|xdc|tcl)$/.test(f.name) ? 'constraints' : 'src';
    await api(`/projects/${S.project}/file?path=${encodeURIComponent(`${dir}/${f.name}`)}`, { method: 'PUT', text: f.text });
  }
  if (files.length) { toast(`Added ${files.length} file(s)`, 'ok'); loadProject(S.project); }
}

function openSettings() {
  openTab({ kind: 'settings', id: S.project, title: 'Settings', icon: 'ic-gear', render: async (v) => {
    const c = S.cfg;
    const pd = await api('/pdks');
    const f = (label, el) => [h('label', {}, label), el];
    const top = h('select', {}, (S.units || []).map((u) => h('option', { value: u.name, selected: u.name === c.top ? 'selected' : null }, u.name)));
    const clk = h('input', { value: c.clock.port }); const per = h('input', { type: 'number', step: '0.1', value: c.clock.period_ns });
    const rst = h('input', { value: c.reset.port }); const rl = h('select', {}, h('option', { value: '0' }, 'active high'), h('option', { value: '1', selected: c.reset.active_low ? 'selected' : null }, 'active low'));
    const pdk = h('select', {}, pd.map((p) => h('option', { value: p.id, selected: p.id === c.target.pdk ? 'selected' : null }, `${p.id} — ${p.node}${p.tapeout ? '' : ' (no fab)'}${p.installed ? '' : ' · not installed'}`)));
    const scl = h('select', {});
    const fillScl = () => { const p = pd.find((x) => x.id === pdk.value); scl.innerHTML = ''; p.scls.forEach((s) => scl.append(h('option', { value: s, selected: s === c.target.scl ? 'selected' : null }, s))); };
    pdk.onchange = fillScl; fillScl();
    const smode = h('select', {}, h('option', { value: 'pdk' }, 'map to PDK cells (+ STA)'), h('option', { value: 'generic', selected: c.synth.mode === 'generic' ? 'selected' : null }, 'generic gates (technology-free)'));
    const sflat = h('select', {}, h('option', { value: '1' }, 'flatten'), h('option', { value: '0', selected: c.synth.flatten ? null : 'selected' }, 'keep hierarchy'));
    const util = h('input', { type: 'number', value: c.impl.core_util }); const dens = h('input', { type: 'number', step: '0.05', value: c.impl.density });
    const sim = h('select', {}, ['icarus', 'verilator', 'ghdl'].map((s) => h('option', { value: s, selected: s === c.sim.simulator ? 'selected' : null }, s)));
    const tm = h('input', { value: c.sim.test_module, placeholder: `test_${c.top}` });
    v.append(h('div', { class: 'page' }, h('h1', {}, 'Project settings'), h('div', { class: 'sub' }, S.project),
      h('h2', {}, 'Design'), h('div', { class: 'form-grid' }, ...f('Top unit', top), ...f('Clock port', clk), ...f('Clock period (ns)', per), ...f('Reset port', rst), ...f('Reset polarity', rl)),
      h('h2', {}, 'Technology'), h('div', { class: 'form-grid' }, ...f('PDK', pdk), ...f('Standard-cell library', scl)),
      h('h2', {}, 'Synthesis (Yosys)'), h('div', { class: 'form-grid' }, ...f('Mapping', smode), ...f('Hierarchy', sflat)),
      h('h2', {}, 'Implementation'), h('div', { class: 'form-grid' }, ...f('Core utilization (%)', util), ...f('Placement density', dens)),
      h('h2', {}, 'Simulation (cocotb)'), h('div', { class: 'form-grid' }, ...f('Simulator', sim), ...f('Test module (tb/*.py)', tm)),
      h('div', { class: 'row', style: 'margin-top:24px' }, h('button', { class: 'btn primary', onclick: async () => {
        Object.assign(c, { top: top.value, clock: { port: clk.value, period_ns: +per.value }, reset: { port: rst.value, active_low: rl.value === '1' },
          target: { pdk: pdk.value, scl: scl.value }, synth: { ...c.synth, mode: smode.value, flatten: sflat.value === '1' },
          impl: { ...c.impl, core_util: +util.value, density: +dens.value }, sim: { ...c.sim, simulator: sim.value, test_module: tm.value } });
        await saveCfg();
      } }, 'Save settings'))));
  } });
}

// ------------------------------------------------------------------ PDKs
async function renderPdkSide(body) {
  const pd = await api('/pdks');
  for (const p of pd) {
    const cur = S.cfg?.target.pdk === p.id;
    body.append(h('div', { class: 'card pdk', style: `margin:8px 10px;${cur ? 'border-color:var(--amber)' : ''}` },
      h('div', { class: 'row' }, h('span', { class: 'node' }, p.node.split(' ')[0]), h('span', { style: 'color:var(--muted)' }, p.node.split(' ').slice(1).join(' ') || 'nm'),
        h('div', { class: 'grow' }), h('span', { class: `tag ${p.tapeout ? 'fab' : 'nofab'}` }, p.tapeout ? 'tape-out' : 'no fab')),
      h('div', {}, h('b', {}, p.id), h('span', { style: 'color:var(--muted)' }, ` · ${p.vendor}`)),
      h('div', { style: 'color:var(--muted);font-size:12px' }, p.note),
      h('div', { class: 'row' },
        p.installed ? h('span', { class: 'tag inst' }, 'installed') : h('button', { class: 'btn', onclick: () => installPdk(p.id) }, '⬇ Download'),
        S.project && p.installed && !cur ? h('button', { class: 'btn', onclick: async () => { S.cfg.target = { pdk: p.id, scl: p.scls[0] }; await saveCfg(); renderSide(); } }, 'Use for this project') : null,
        cur ? h('span', { class: 'tag', style: 'color:var(--amber)' }, 'current') : null)));
  }
}
async function installPdk(id) {
  const j = await api(`/pdks/${id}/install`, { method: 'POST' });
  toast(`Downloading ${id} into WSL…`);
  follow(j, () => { renderSide(); toast(`${id} ready`, 'ok'); });
}

// ------------------------------------------------------------------ jobs
const STAGE_INFO = {
  Lint: ['Verilator / GHDL', 'Syntax, width and style checks on the RTL', 'messages in Problems'],
  Elaborate: ['Yosys', 'Parse and elaborate the design hierarchy', 'RTL netlist (JSON)'],
  Schematic: ['netlistsvg', 'Draw the elaborated design', 'interactive schematic'],
  'Read RTL': ['Yosys (+ GHDL plugin)', 'Read Verilog/SystemVerilog/VHDL sources', 'RTLIL'],
  Synthesize: ['Yosys synth', 'FSM extraction, memory mapping, logic optimization', 'generic gates'],
  'Map to cells': ['dfflibmap + ABC', 'Map flip-flops and logic to standard cells', 'gate-level netlist'],
  Timing: ['OpenSTA', 'Post-synthesis static timing and power (no wires yet)', 'slack, power'],
  Reports: ['Kahroba', 'Collect area, cells, timing', 'report'],
  Build: ['Icarus / Verilator / GHDL', 'Compile the design for simulation', 'simulation model'],
  'Run tests': ['cocotb', 'Run the Python testbench against the design', 'pass/fail per test'],
  Waves: ['FST / GHW', 'Waveform database for Surfer', 'waves file'],
  Synthesis: ['Yosys + ABC', 'RTL to standard-cell netlist for the chosen PDK', 'netlist'],
  Floorplan: ['OpenROAD ifp · ppl · pdngen', 'Die and core size, rows, I/O pins, power grid', 'floorplan DEF'],
  Placement: ['OpenROAD RePlAce · Resizer · OpenDP', 'Global and detailed placement, buffering, sizing', 'placed DEF'],
  'Clock tree': ['OpenROAD TritonCTS', 'Build and balance the clock tree', 'CTS DEF, skew'],
  Routing: ['OpenROAD FastRoute · TritonRoute · OpenRCX', 'Global and detailed routing, parasitic extraction', 'routed DEF, SPEF'],
  'Sign-off STA': ['OpenSTA', 'Multi-corner setup/hold timing with extracted parasitics', 'timing reports'],
  GDS: ['Magic · KLayout', 'Stream out GDSII and cross-check the two writers (XOR)', 'final GDS'],
  DRC: ['Magic · KLayout', 'Design-rule check against the PDK rule deck', 'violations'],
  LVS: ['Netgen', 'Layout-versus-schematic: extracted layout equals the netlist', 'LVS report'],
  'Power per cell': ['OpenROAD / OpenSTA', 'Per-instance power with routed parasitics', 'power map'],
  'Heat solve': ['Kahroba thermal', 'Spreading in Si + package θJA (estimate)', 'temperature map'],
  Render: ['KLayout', 'Render the GDS with the PDK layer colors', 'PNG'],
  download: ['ciel / git', 'Fetch the PDK into WSL', 'PDK files'], verify: ['Kahroba', 'Check the PDK', ''],
};

async function runStep(kind, body = {}) {
  if (!S.project) return toast('Open a project first');
  if (!S.cfg.top && kind !== 'lint') { openSettings(); return toast('Choose the top unit first', 'err'); }
  await saveAll();
  try {
    const j = await api(`/projects/${S.project}/run/${kind}`, { method: 'POST', body });
    consoleLine(`▶ ${j.title}  [${j.id}]`, 'l-cmd');
    openJobView(j);
    follow(j, (fin) => onJobDone(fin));
  } catch (e) { toast(e.message, 'err'); consoleLine(`✖ ${e.message}`, 'l-err'); }
}
function follow(j, done) {
  S.live[j.kind] = j; renderFlow();
  const es = new EventSource(`${API}/jobs/${j.id}/log?t=${encodeURIComponent(TOKEN)}`);
  es.onmessage = (ev) => { const d = JSON.parse(ev.data); consoleLine(d.line); };
  es.addEventListener('state', (ev) => { const info = JSON.parse(ev.data); S.live[j.kind] = info; updateJobView(info); renderFlow(); });
  es.addEventListener('end', async (ev) => {
    es.close(); const info = JSON.parse(ev.data);
    S.live[j.kind] = info; updateJobView(info);
    consoleLine(info.status === 'ok' ? `✔ ${info.title} finished` : `✖ ${info.title} ${info.status}`, info.status === 'ok' ? 'l-ok' : 'l-err');
    await refreshHistory(); delete S.live[j.kind]; renderFlow();
    if (done) done(info);
  });
}
function consoleLine(line, cls) {
  const c = $('#btConsole');
  const atBottom = c.scrollTop + c.clientHeight >= c.scrollHeight - 30;
  if (!cls) cls = /^\[kahroba\] stage:/.test(line) ? 'l-stage' : /\b(error|ERROR|Error|failed)\b|\bFAIL\b(?!=0)/.test(line) ? 'l-err' : /\b(warning|Warning|WARNING)\b/.test(line) ? 'l-warn' : /\bPASS\b/.test(line) ? 'l-ok' : '';
  c.append(h('div', { class: cls }, line));
  while (c.childNodes.length > 6000) c.firstChild.remove();
  if (atBottom) c.scrollTop = c.scrollHeight;
}

function openJobView(j) {
  openTab({ kind: 'job', id: j.id, title: j.title, icon: j.kind === 'sim' ? 'ic-wave' : 'ic-chip', job: j, render: (v) => renderJob(v, j) });
}
function updateJobView(info) {
  const t = S.open.find((x) => x.kind === 'job' && x.id === info.id);
  if (!t) return;
  t.job = info; t.rerender = true;
  if (S.active === t) activate(t);
}
function fmtT(s) { if (s == null) return ''; s = Math.round(s); return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`; }
function renderJob(v, j0) {
  const t = S.open.find((x) => x.kind === 'job' && x.id === j0.id); const j = t?.job || j0;
  const done = new Set(j.done_stages || []);
  const n = j.stages.length; const k = j.stages.filter((s) => done.has(s)).length;
  const pct = j.status === 'ok' ? 100 : Math.round((k / Math.max(n, 1)) * 100);
  const el = j.started ? ((j.ended || Date.now() / 1000) - j.started) : null;
  const page = h('div', { class: 'page' },
    h('div', { class: 'row' }, h('h1', {}, j.title), h('div', { class: 'grow' }),
      ['running', 'queued'].includes(j.status) ? h('button', { class: 'btn', onclick: () => api(`/jobs/${j.id}`, { method: 'DELETE' }) }, 'Stop') : null),
    h('div', { class: 'sub' }, `${j.status.toUpperCase()} · ${fmtT(el)} · ${j.id}`),
    h('div', { class: 'bar' }, h('i', { style: `width:${pct}%` })),
    h('div', { class: 'pipe' }, j.stages.map((s, i) => {
      const inf = STAGE_INFO[s] || ['', '', ''];
      const st = done.has(s) || j.status === 'ok' ? 'done' : j.current === s ? (j.status === 'failed' ? 'fail' : 'cur') : (j.status === 'failed' && !j.current && i === k ? 'fail' : '');
      return h('div', { class: `stage ${st}` }, h('div', { class: 'node' }, st === 'done' ? '✓' : st === 'fail' ? '!' : i + 1),
        h('div', {}, h('div', {}, h('span', { class: 't' }, s), h('span', { class: 'tool' }, inf[0])), h('div', { class: 'd' }, inf[1]), inf[2] ? h('div', { class: 'o' }, `→ ${inf[2]}`) : null),
        h('div', { class: 'time' }, st === 'cur' ? h('span', { class: 'spin' }) : ''));
    })));
  if (j.status !== 'running' && j.status !== 'queued') page.append(resultPanel(j));
  v.append(page);
}

function M(k, v, u, cls = '') { return h('div', { class: `card metric ${cls}` }, h('div', { class: 'k' }, k), h('div', { class: 'v' }, v ?? '—', u && v != null ? h('span', { class: 'u' }, u) : null)); }
const sig = (x, d = 3) => (x == null || x === '' ? null : Math.abs(+x) >= 1000 ? Math.round(+x).toLocaleString() : (+x).toPrecision(d));
const pw = (w) => (w == null ? [null, ''] : w >= 1e-3 ? [sig(w * 1e3), 'mW'] : w >= 1e-6 ? [sig(w * 1e6), 'µW'] : [sig(w * 1e9), 'nW']);

function resultPanel(j) {
  const r = j.result || {};
  const wrap = h('div', {});
  if (r.error) wrap.append(h('pre', { class: 'rpt' }, r.error));
  if (j.kind === 'lint') {
    wrap.append(h('h2', {}, 'Result'), h('div', { class: 'cards' }, M('Errors', r.errors, '', r.errors ? 'bad' : 'good'), M('Warnings', r.warnings, '', r.warnings ? 'warn' : 'good')));
    showProblems(r.messages || []);
  }
  if (j.kind === 'rtl' && r.json) {
    wrap.append(h('div', { class: 'row', style: 'margin-top:18px' }, h('button', { class: 'btn primary', onclick: () => openSchematic(r.json, 'RTL schematic', r.svg) }, 'Open schematic')),
      h('h2', {}, 'Statistics'), h('pre', { class: 'rpt' }, r.stat || ''));
    openSchematic(r.json, 'RTL schematic', r.svg);
  }
  if (j.kind === 'sim') {
    wrap.append(h('h2', {}, 'Tests'), h('div', { class: 'cards' }, M('Passed', r.passed, '', 'good'), M('Failed', r.failed, '', r.failed ? 'bad' : 'good')),
      h('table', { class: 'kv', style: 'margin-top:12px' }, (r.tests || []).map((t) => h('tr', {}, h('td', {}, t.name), h('td', { style: `color:${t.ok ? 'var(--ok)' : 'var(--err)'}` }, t.ok ? 'PASS' : 'FAIL'), h('td', {}, `${sig(t.sim_time_ns)} ns sim`), h('td', {}, `${sig(t.time_s)} s`)))),
      r.waves ? h('div', { class: 'row', style: 'margin-top:16px' }, h('button', { class: 'btn primary', onclick: () => openWaves(r.waves) }, 'Open waveform in Surfer'),
        h('button', { class: 'btn', onclick: () => window.kahroba.surferNative(r.waves) }, 'Surfer (window)')) : null);
  }
  if (j.kind === 'synth') {
    const [pv, pu] = pw(r.power_w);
    wrap.append(h('h2', {}, 'Summary'), h('div', { class: 'cards' }, M('Cells', r.cells), M('Cell area', sig(r.area_um2), 'µm²'),
      r.slack_ns != null ? M('Worst slack', sig(r.slack_ns), 'ns', r.slack_ns < 0 ? 'bad' : 'good') : null, r.power_w != null ? M('Power (pre-layout)', pv, pu) : null),
    h('div', { class: 'row', style: 'margin-top:16px' }, h('button', { class: 'btn primary', onclick: () => openSchematic(r.json, 'Synthesized schematic') }, 'Synthesized schematic'),
      h('button', { class: 'btn', onclick: () => openFileAbs(r.netlist) }, 'Netlist')),
    h('h2', {}, 'Cell usage'), h('pre', { class: 'rpt' }, r.stat_text || ''),
    r.timing_max ? [h('h2', {}, 'Critical path (setup)'), h('pre', { class: 'rpt' }, r.timing_max)] : null,
    r.power_text ? [h('h2', {}, 'Power'), h('pre', { class: 'rpt' }, r.power_text)] : null);
  }
  if (j.kind === 'impl') wrap.append(implReport(r, j));
  if (j.kind === 'thermal' && r.temp) wrap.append(thermalReport(r));
  return wrap;
}

function implReport(r, j) {
  const [pv, pu] = pw(r.power_w);
  const drc = (r.magic_drc ?? 0) + (r.klayout_drc ?? 0) + (r.route_drc ?? 0);
  const box = h('div', {},
    h('h2', {}, 'Sign-off summary'),
    h('div', { class: 'cards' },
      M('Setup WNS', sig(r.setup_wns_ns), 'ns', r.setup_wns_ns < 0 ? 'bad' : 'good'),
      M('Hold WNS', sig(r.hold_wns_ns), 'ns', r.hold_wns_ns < 0 ? 'bad' : 'good'),
      M('Fmax (est.)', sig(r.fmax_mhz), 'MHz'),
      M('Total power', pv, pu), M('Std cells', sig(r.cells)), M('Flip-flops', r.flops), M('Std-cell area', sig(r.cell_area_um2), 'µm²'),
      M('Die area', sig(r.die_area_um2), 'µm²'), M('Utilization', r.utilization != null ? sig(r.utilization * 100) : null, '%'),
      M('Wirelength', sig(r.wirelength_um), 'µm'),
      r.flow === 'librelane' ? M('DRC errors', drc, '', drc ? 'bad' : 'good') : null,
      r.flow === 'librelane' ? M('LVS errors', r.lvs_errors, '', r.lvs_errors ? 'bad' : 'good') : null,
      r.antenna_nets != null ? M('Antenna nets', r.antenna_nets, '', r.antenna_nets ? 'warn' : 'good') : null),
    h('div', { class: 'row', style: 'margin-top:16px' },
      r.gds ? h('button', { class: 'btn primary', onclick: () => openLayout(r.gds, r.pdk) }, 'View layout') : null,
      r.gds ? h('button', { class: 'btn', onclick: () => openKLayout(r.gds, r.pdk) }, 'Open in KLayout') : null,
      h('button', { class: 'btn', onclick: () => runThermal(r.run_dir) }, 'Thermal map'),
      h('button', { class: 'btn', onclick: () => window.kahroba.reveal(r.run_dir) }, 'Open run folder')),
    r.power_w != null ? [h('h2', {}, 'Power breakdown'), h('div', { class: 'cards' },
      M('Internal', ...pw(r.power_internal_w)), M('Switching', ...pw(r.power_switching_w)), M('Leakage', ...pw(r.power_leakage_w)))] : null,
    r.sta_summary ? [h('h2', {}, 'Multi-corner timing'), h('pre', { class: 'rpt' }, r.sta_summary)] : null,
    r.timing_max ? [h('h2', {}, 'Worst setup path (nominal corner)'), h('pre', { class: 'rpt' }, r.timing_max)] : null);
  return box;
}

// ------------------------------------------------------------------ viewers
function panZoom(host, inner, fit = true) {
  const pz = h('div', { class: 'pz' }); const wrap = h('div', { class: 'inner' }, inner);
  pz.append(wrap); host.append(pz);
  let s = 1, x = 20, y = 20, drag = null;
  const apply = () => { wrap.style.transform = `translate(${x}px,${y}px) scale(${s})`; };
  const doFit = () => {
    const w = inner.scrollWidth || inner.width || 800, hh = inner.scrollHeight || inner.height || 600;
    s = Math.min((pz.clientWidth - 40) / w, (pz.clientHeight - 40) / hh, 4); x = (pz.clientWidth - w * s) / 2; y = (pz.clientHeight - hh * s) / 2; apply();
  };
  pz.addEventListener('wheel', (e) => { e.preventDefault(); const k = Math.exp(-e.deltaY * 0.0015); const r = pz.getBoundingClientRect(); const mx = e.clientX - r.left, my = e.clientY - r.top; x = mx - (mx - x) * k; y = my - (my - y) * k; s *= k; apply(); }, { passive: false });
  pz.addEventListener('mousedown', (e) => { drag = [e.clientX - x, e.clientY - y]; pz.classList.add('drag'); });
  window.addEventListener('mousemove', (e) => { if (drag) { x = e.clientX - drag[0]; y = e.clientY - drag[1]; apply(); } });
  window.addEventListener('mouseup', () => { drag = null; pz.classList.remove('drag'); });
  if (fit) setTimeout(doFit, 30);
  apply();
  return { pz, fit: doFit };
}
async function openSchematic(jsonPath, title, dotSvg) {
  openTab({ kind: 'schem', id: jsonPath, title, icon: 'ic-chip', render: async (v) => {
    v.append(h('div', { style: 'padding:20px;color:var(--muted)' }, h('span', { class: 'spin' }), '  drawing schematic…'));
    let svg;
    try {
      const json = await fetch(fileURL(jsonPath)).then((r) => r.json());
      const cells = Object.values(json.modules || {}).reduce((a, m) => a + Object.keys(m.cells || {}).length, 0);
      if (cells > 2500) throw new Error(`${cells} cells - too large for netlistsvg`);
      svg = await window.kahroba.netlistsvg(json);
    } catch (e) {
      if (dotSvg) svg = await fetch(fileURL(dotSvg)).then((r) => r.text());
      else { v.innerHTML = ''; v.append(h('div', { class: 'page' }, h('h1', {}, title), h('p', { class: 'sub' }, `Schematic not drawn: ${e.message}`))); return; }
    }
    v.innerHTML = '';
    const holder = h('div', { class: 'schem', html: svg });
    const { fit } = panZoom(v, holder);
    v.append(h('div', { class: 'pz-tools' }, h('button', { class: 'btn', onclick: fit }, 'Fit'), dotSvg ? h('button', { class: 'btn', onclick: async () => { holder.innerHTML = await fetch(fileURL(dotSvg)).then((r) => r.text()); fit(); } }, 'Graphviz view') : null));
  } });
}
function openSynthSchematic() { const j = S.last.synth; if (!j?.result?.json) return toast('Run synthesis first'); openSchematic(j.result.json, 'Synthesized schematic'); }
function openLast(kind) { const j = S.last[kind]; if (!j) return toast('Not run yet'); openJobView(j); }
function openLastWaves() { const w = S.last.sim?.result?.waves; if (!w) return toast('Run a simulation first'); openWaves(w); }
async function openFileAbs(p) {
  const rel = p.split(`/projects/${S.project}/`)[1];
  if (rel) openFile(rel);
}
function openWaves(path) {
  openTab({ kind: 'waves', id: path, title: `Waves · ${path.split('/').pop()}`, icon: 'ic-wave', render: (v) => {
    const fr = h('iframe', { class: 'surfer-frame', src: 'http://127.0.0.1:8770/surfer/index.html' });
    // Surfer's web build takes commands by postMessage; wait for the wasm to start
    const top = S.cfg?.top;
    const load = () => {
      fr.contentWindow.postMessage({ command: 'LoadUrl', url: fileURL(path) }, '*');
      // then show every signal of the top scope (Surfer Message::AddScope)
      if (top) setTimeout(() => fr.contentWindow.postMessage({ command: 'InjectMessage', message: JSON.stringify({ AddScope: [{ strs: [top], id: 'None' }, true] }) }, '*'), 2500);
    };
    fr.addEventListener('load', () => setTimeout(load, 2000));
    v.append(fr, h('div', { class: 'pz-tools', style: 'top:auto;bottom:44px' },
      h('button', { class: 'btn', onclick: load }, 'Reload waves'),
      h('button', { class: 'btn', onclick: () => window.kahroba.surferNative(path) }, 'Open in Surfer window')));
  } });
}
async function openLayout(gds, pdk) {
  const r = S.last.impl?.result;
  gds = gds || r?.gds; pdk = pdk || r?.pdk || S.cfg.target.pdk;
  if (!gds) return toast('Run implementation first');
  const png = gds.replace(/\.gds$/, '.png');
  const show = () => openTab({ kind: 'layout', id: gds, title: `Layout · ${gds.split('/').pop()}`, icon: 'ic-layers', render: (v) => {
    const img = h('img', { src: fileURL(png) + `&t=${Date.now()}`, style: 'image-rendering:auto;display:block' });
    img.onload = () => { const { fit } = panZoom(v, img); v.append(h('div', { class: 'pz-tools' }, h('button', { class: 'btn', onclick: fit }, 'Fit'), h('button', { class: 'btn', onclick: () => openKLayout(gds, pdk) }, 'KLayout'))); };
    img.onerror = () => v.append(h('div', { class: 'page' }, 'Layout image missing.'));
  } });
  const ok = await fetch(fileURL(png), { method: 'HEAD' }).then((x) => x.ok).catch(() => false);
  if (ok) return show();
  const j = await api(`/projects/${S.project}/run/layout`, { method: 'POST', body: { gds, pdk } });
  toast('Rendering layout with KLayout…');
  follow(j, (fin) => (fin.status === 'ok' ? show() : toast('Layout render failed - see Console', 'err')));
}
async function openKLayout(gds, pdk) {
  gds = gds || S.last.impl?.result?.gds; if (!gds) return toast('Run implementation first');
  await api(`/projects/${S.project}/klayout`, { method: 'POST', body: { gds, pdk: pdk || S.cfg.target.pdk } });
  toast('KLayout opening (WSLg window)…');
}
async function runThermal(implRun) {
  implRun = implRun || S.last.impl?.result?.run_dir;
  if (!implRun) return toast('Run implementation first');
  runStep('thermal', { impl_run: implRun });
}
function thermalReport(r) {
  const [ny, nx] = r.grid; const T = r.temp;
  const min = r.t_min, max = r.t_max;
  const cv = h('canvas', { width: nx, height: ny, style: `width:${Math.min(640, nx * 9)}px;image-rendering:pixelated;border-radius:8px;border:1px solid var(--line)` });
  const ctx = cv.getContext('2d'); const img = ctx.createImageData(nx, ny);
  const stops = [[27, 42, 107], [44, 123, 182], [171, 217, 233], [255, 255, 191], [253, 174, 97], [215, 25, 28]];
  for (let y = 0; y < ny; y++) for (let x = 0; x < nx; x++) {
    const t = max > min ? (T[y][x] - min) / (max - min) : 0; const f = t * (stops.length - 1); const i = Math.min(Math.floor(f), stops.length - 2); const u = f - i;
    const c = stops[i].map((a, k) => a + (stops[i + 1][k] - a) * u); const o = ((ny - 1 - y) * nx + x) * 4;
    img.data[o] = c[0]; img.data[o + 1] = c[1]; img.data[o + 2] = c[2]; img.data[o + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  const [pv, pu] = pw(r.power_w);
  return h('div', {}, h('h2', {}, 'Thermal estimate'),
    h('div', { class: 'cards' }, M('Total power', pv, pu), M('T max', sig(r.t_max, 5), '°C'), M('T mean', sig(r.t_mean, 5), '°C'), M('Hotspot rise', sig(r.t_max - r.t_min, 3), 'K'), M('Cells mapped', r.cells)),
    h('div', { class: 'row', style: 'margin-top:16px;align-items:flex-start;gap:24px' }, cv,
      h('div', { class: 'card', style: 'max-width:380px' }, h('b', {}, 'Model'), h('p', { style: 'color:var(--muted);margin:6px 0 0' },
        `${r.model}. θJA = ${r.theta_ja} K/W, ambient ${r.t_amb} °C, grid ${nx}×${ny}. Hotspot at (${sig(r.hotspot_um[0])}, ${sig(r.hotspot_um[1])}) µm. Use it to find hotspots and compare designs; it is not a sign-off thermal analysis.`),
      h('div', { class: 'legend', style: 'position:static;margin-top:12px' }, 'temperature', h('div', { class: 'grad' }), h('div', { class: 'row', style: 'justify-content:space-between' }, h('span', {}, `${sig(min, 5)} °C`), h('span', {}, `${sig(max, 5)} °C`))))));
}
function showProblems(msgs) {
  const b = $('#btProblems'); b.innerHTML = '';
  msgs.forEach((m) => b.append(h('div', { class: 'prob', onclick: () => { const rel = m.file.split(`/projects/${S.project}/`)[1] || m.file; openFile(rel, m.line); } },
    h('span', { class: `sev ${m.sev}` }, m.sev), h('span', { class: 'loc' }, `${m.file.split('/').pop()}:${m.line}`), h('span', {}, m.msg))));
  const n = msgs.filter((m) => m.sev === 'error' || m.sev === 'warning').length;
  $('#probCount').textContent = n; $('#probCount').classList.toggle('hide', !n);
  if (msgs.length) $('[data-bt=problems]').click();
}
function onJobDone(j) {
  if (j.kind === 'sim' && j.status === 'ok' && j.result?.failed) toast(`${j.result.failed} test(s) failed`, 'err');
  else if (j.status === 'ok') toast(`${j.title}: done`, 'ok');
  else toast(`${j.title}: ${j.status} - see Console`, 'err');
  renderJobsPane();
}
function renderJobsPane() {
  const b = $('#btJobs'); b.innerHTML = '';
  S.history.slice(0, 50).forEach((j) => b.append(h('div', { class: 'prob', onclick: () => openJobView(j) },
    h('span', { class: `sev ${j.status === 'ok' ? '' : 'error'}`, style: j.status === 'ok' ? 'color:var(--ok)' : '' }, j.status), h('span', { class: 'loc' }, j.id), h('span', {}, j.title))));
}

// ------------------------------------------------------------------ layout splitters
function splitters() {
  const root = document.documentElement;
  $$('[data-split]').forEach((sp) => sp.addEventListener('mousedown', (e) => {
    e.preventDefault(); sp.classList.add('drag');
    const kind = sp.dataset.split;
    const move = (ev) => {
      if (kind === 'sidew') root.style.setProperty('--side-w', `${Math.max(200, Math.min(600, ev.clientX - 46))}px`);
      if (kind === 'bottom') root.style.setProperty('--bottom-h', `${Math.max(60, Math.min(window.innerHeight - 200, window.innerHeight - ev.clientY - 24))}px`);
      if (kind === 'side') { const r = $('#side').getBoundingClientRect(); root.style.setProperty('--flow-h', `${Math.max(15, Math.min(85, 100 - ((ev.clientY - r.top) / r.height) * 100))}%`); }
    };
    const up = () => { sp.classList.remove('drag'); window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
  }));
}

// ------------------------------------------------------------------ boot
async function projectSwitcher(e) {
  const list = await api('/projects');
  popupMenu([...list.map((p) => [p.name, () => loadProject(p.name)]), ['＋ New project…', () => newProjectDialog()]], e.clientX, e.clientY + 14);
}
function setupScreen(info) {
  $('#editorHost').classList.add('hide');
  const v = h('div', { class: 'view setup' }, h('div', { class: 'card' },
    h('div', { class: 'hero' }, h('span', { class: 'gem' }), h('div', {}, h('h1', {}, 'Welcome to Kahroba'), h('p', {}, 'One-time setup of the Linux toolchain in WSL2'))),
    info.state === 'no-distro'
      ? [h('p', {}, `The WSL distribution "${info.distro}" was not found. The setup wizard will:`),
        h('ol', { class: 'steps' }, h('li', {}, 'enable WSL2 (asks for administrator permission, may need one reboot)'), h('li', {}, 'download and import the Kahroba Linux image'),
          h('li', {}, 'install Yosys, OpenROAD, OpenSTA, LibreLane, Magic, Netgen, KLayout, Verilator, Icarus, GHDL, cocotb'), h('li', {}, 'download the SkyWater 130 nm PDK')),
        h('button', { class: 'btn primary', onclick: () => window.kahroba.runSetup() }, 'Run setup')]
      : [h('p', {}, 'The backend inside WSL did not start. Check that the toolchain is installed, then retry.'), h('button', { class: 'btn primary', onclick: () => location.reload() }, 'Retry')]));
  $('#views').append(v);
}
async function boot() {
  splitters();
  $$('#activity button').forEach((b) => b.addEventListener('click', () => setSide(b.dataset.side)));
  $$('.bt-tabs [data-bt]').forEach((b) => b.addEventListener('click', () => {
    $$('.bt-tabs [data-bt]').forEach((x) => x.classList.toggle('active', x === b));
    ['console', 'problems', 'jobs'].forEach((k) => $(`#bt${k[0].toUpperCase() + k.slice(1)}`).classList.toggle('hide', b.dataset.bt !== k));
    if (b.dataset.bt === 'jobs') renderJobsPane();
  }));
  $('#btClear').onclick = () => { $('#btConsole').innerHTML = ''; };
  $('#projBtn').onclick = projectSwitcher;
  $$('.tb-run').forEach((b) => b.addEventListener('click', () => runStep(b.dataset.act)));
  window.addEventListener('keydown', (e) => {
    if (e.key === 'F6') runStep('sim'); if (e.key === 'F7') runStep('synth'); if (e.key === 'F8') runStep('impl');
  });
  renderFlow();
  const info = await window.kahroba.boot();
  TOKEN = info.token || '';
  if (info.state !== 'ready') { $('#sbBackend').innerHTML = '<i class="dot bad"></i>backend offline'; return setupScreen(info); }
  $('#sbBackend').innerHTML = `<i class="dot ok"></i>WSL · ${info.distro}`;
  const t = await api('/tools').catch(() => null);
  S.home = t ? t.projects.replace(/\/projects$/, '') : '/home/kahroba';
  await initMonaco();
  const last = localStorage.getItem('kahroba.project');
  const list = await api('/projects');
  if (last && list.some((p) => p.name === last)) await loadProject(last);
  showWelcome();
  renderSide();
}
boot();

// Kahroba desktop shell: starts the WSL backend, hosts the UI, bridges a few
// Windows-side services (netlistsvg rendering, file dialogs, external tools).
const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const { spawn, execFile } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

const DISTRO = process.env.KAHROBA_DISTRO || 'Kahroba';
const PORT = 8770;
const RES = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..');
let win = null;
let backend = null;

function wsl(args, opts = {}) {
  return new Promise((resolve) => {
    execFile('wsl.exe', args, { encoding: 'buffer', windowsHide: true, timeout: opts.timeout || 0 },
      (err, stdout, stderr) => {
        const dec = (b) => { const s = b.toString('utf16le'); return /\u0000/.test(b.toString('latin1').slice(0, 4)) ? s : b.toString('utf8'); };
        resolve({ code: err ? (err.code ?? 1) : 0, out: dec(stdout || Buffer.alloc(0)), err: dec(stderr || Buffer.alloc(0)) });
      });
  });
}

async function distroExists() {
  const r = await wsl(['-l', '-q'], { timeout: 20000 });
  return r.out.replace(/\u0000/g, '').split(/\r?\n/).map((s) => s.trim()).includes(DISTRO);
}

function health() {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port: PORT, path: '/api/health', timeout: 1500 }, (res) => {
      res.resume(); resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

// Copy server/examples into the distro (fast path: rsync-like via tar over stdin).
async function syncServer() {
  const winSrc = RES.replace(/\\/g, '/');
  const drive = winSrc[0].toLowerCase();
  const lin = `/mnt/${drive}${winSrc.slice(2)}`;
  const cmd = `mkdir -p ~/.kahroba/app && rm -rf ~/.kahroba/app/server ~/.kahroba/app/examples && ` +
    `cp -r "${lin}/server" "${lin}/examples" ~/.kahroba/app/ && ` +
    `([ -d "${lin}/app/vendor/surfer-web" ] && rm -rf ~/.kahroba/app/surfer-web && cp -r "${lin}/app/vendor/surfer-web" ~/.kahroba/app/ || true) && ` +
    `([ -d "${lin}/vendor/surfer-web" ] && rm -rf ~/.kahroba/app/surfer-web && cp -r "${lin}/vendor/surfer-web" ~/.kahroba/app/ || true)`;
  return wsl(['-d', DISTRO, '--', 'bash', '-lc', cmd], { timeout: 120000 });
}

async function startBackend() {
  if (await health()) return true;
  await syncServer();
  backend = spawn('wsl.exe', ['-d', DISTRO, '--', 'bash', '-lc',
    'cd ~/.kahroba/app/server && exec ~/.kahroba/venv/bin/python -m kahroba.app'],
  { windowsHide: true, stdio: 'ignore' });
  for (let i = 0; i < 120; i++) {
    if (await health()) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function createWindow() {
  win = new BrowserWindow({
    width: 1500, height: 920, minWidth: 1000, minHeight: 640,
    backgroundColor: '#0d1117', show: false,
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#0d1117', symbolColor: '#c9d1d9', height: 38 },
    icon: path.join(__dirname, 'renderer', 'icon.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: false },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  win.once('ready-to-show', () => win.show());
  // self-test: KAHROBA_SNAP=<png> [KAHROBA_SNAP_JS=<js run first>] -> screenshot, then quit
  if (process.env.KAHROBA_SNAP) {
    const logf = process.env.KAHROBA_SNAP.replace(/\.png$/, '.log');
    fs.writeFileSync(logf, '');
    win.webContents.on('console-message', (_e, level, message, line, src) => fs.appendFileSync(logf, `[${level}] ${message} (${src}:${line})\n`));
    win.webContents.once('did-finish-load', async () => {
      await new Promise((r) => setTimeout(r, 9000));
      if (process.env.KAHROBA_SNAP_JS) {
        try { await win.webContents.executeJavaScript(process.env.KAHROBA_SNAP_JS); } catch (e) { console.error(e); }
        await new Promise((r) => setTimeout(r, Number(process.env.KAHROBA_SNAP_WAIT || 8000)));
      }
      const img = await win.webContents.capturePage();
      fs.writeFileSync(process.env.KAHROBA_SNAP, img.toPNG());
      app.quit();
    });
  }
}

ipcMain.handle('boot', async () => {
  if (!(await distroExists())) return { state: 'no-distro', distro: DISTRO };
  const ok = await startBackend();
  let token = '';
  if (ok) {
    const r = await wsl(['-d', DISTRO, '--', 'bash', '-c', 'cat ~/.kahroba/token'], { timeout: 20000 });
    token = r.out.trim();
  }
  return { state: ok ? 'ready' : 'backend-failed', port: PORT, distro: DISTRO, token };
});

ipcMain.handle('netlistsvg', async (_e, json) => {
  const netlistsvg = require('netlistsvg');
  const skin = fs.readFileSync(require.resolve('netlistsvg/lib/default.svg'), 'utf8');
  return netlistsvg.render(skin, json);
});

ipcMain.handle('open-external', (_e, url) => shell.openExternal(url));
ipcMain.handle('wsl-path', (_e, p) => `\\\\wsl.localhost\\${DISTRO}${p.replace(/\//g, '\\')}`);
ipcMain.handle('reveal', (_e, p) => shell.openPath(`\\\\wsl.localhost\\${DISTRO}${p.replace(/\//g, '\\')}`));
ipcMain.handle('pick-files', async () => {
  const r = await dialog.showOpenDialog(win, {
    properties: ['openFile', 'multiSelections'],
    filters: [{ name: 'HDL / tests / constraints', extensions: ['v', 'sv', 'vh', 'vhd', 'vhdl', 'py', 'sdc', 'xdc', 'tcl'] }],
  });
  if (r.canceled) return [];
  return r.filePaths.map((f) => ({ name: path.basename(f), text: fs.readFileSync(f, 'utf8') }));
});
ipcMain.handle('surfer-native', async (_e, wave) => {
  const exe = path.join(RES, app.isPackaged ? 'vendor' : 'app/vendor', 'surfer', 'surfer.exe');
  if (!fs.existsSync(exe)) return { ok: false, error: 'surfer.exe not bundled' };
  spawn(exe, [`\\\\wsl.localhost\\${DISTRO}${wave.replace(/\//g, '\\')}`], { detached: true, stdio: 'ignore' }).unref();
  return { ok: true };
});
ipcMain.handle('run-setup', async () => {
  const ps1 = path.join(RES, app.isPackaged ? 'provision' : 'provision', 'setup.ps1');
  spawn('powershell.exe', ['-ExecutionPolicy', 'Bypass', '-NoExit', '-File', ps1], { detached: true, stdio: 'ignore' }).unref();
  return true;
});

app.whenReady().then(createWindow);
app.on('window-all-closed', () => {
  if (backend) {
    try { execFile('wsl.exe', ['-d', DISTRO, '--', 'pkill', '-f', 'kahroba.app']); } catch (_) { /* ignore */ }
  }
  app.quit();
});

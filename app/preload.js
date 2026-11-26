const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('kahroba', {
  boot: () => ipcRenderer.invoke('boot'),
  netlistsvg: (json) => ipcRenderer.invoke('netlistsvg', json),
  openExternal: (url) => ipcRenderer.invoke('open-external', url),
  wslPath: (p) => ipcRenderer.invoke('wsl-path', p),
  reveal: (p) => ipcRenderer.invoke('reveal', p),
  pickFiles: () => ipcRenderer.invoke('pick-files'),
  surferNative: (wave) => ipcRenderer.invoke('surfer-native', wave),
  runSetup: () => ipcRenderer.invoke('run-setup'),
});

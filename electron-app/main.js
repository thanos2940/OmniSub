const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const Store = require('electron-store');

const store = new Store();
let mainWindow;
let backendProcess;

// Backend server management
function startBackend() {
  const backendPath = path.join(__dirname, '..', 'backend');
  const pythonExecutable = process.platform === 'win32' ? 'python' : 'python3';

  backendProcess = spawn(pythonExecutable, ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000'], {
    cwd: backendPath,
    env: {
      ...process.env,
      GOOGLE_API_KEY: store.get('googleApiKey', '')
    }
  });

  backendProcess.stdout.on('data', (data) => {
    console.log(`Backend: ${data}`);
  });

  backendProcess.stderr.on('data', (data) => {
    console.error(`Backend Error: ${data}`);
  });

  backendProcess.on('close', (code) => {
    console.log(`Backend process exited with code ${code}`);
  });
}

function stopBackend() {
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js')
    },
    icon: path.join(__dirname, 'icon.png')
  });

  // Load the frontend
  const frontendPath = path.join(__dirname, '..', 'frontend', 'dist', 'index.html');

  // Wait for backend to start, then load frontend
  setTimeout(() => {
    mainWindow.loadFile(frontendPath);
  }, 3000);

  // Open DevTools in development
  if (process.env.NODE_ENV === 'development') {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// IPC Handlers
ipcMain.handle('get-api-key', () => {
  return store.get('googleApiKey', '');
});

ipcMain.handle('set-api-key', (event, apiKey) => {
  store.set('googleApiKey', apiKey);
  // Restart backend with new API key
  stopBackend();
  startBackend();
  return true;
});

// App lifecycle
app.whenReady().then(() => {
  startBackend();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    stopBackend();
    app.quit();
  }
});

app.on('before-quit', () => {
  stopBackend();
});

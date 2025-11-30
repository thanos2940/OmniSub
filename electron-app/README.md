# OmbiSub Desktop App

Electron-based desktop application for OmbiSub.

## Build Instructions

### Prerequisites
- Node.js 18+
- Python 3.8+ (bundled in production build)
- Backend and frontend already built

### Development
```bash
cd electron-app
npm install
npm start
```

### Build for Windows
```bash
npm run build:win
```
Output: `dist/OmbiSub Setup.exe`

### Build for macOS
```bash
npm run build:mac
```
Output: `dist/OmbiSub.dmg`

### Build for Linux
```bash
npm run build:linux
```
Output: `dist/OmbiSub.AppImage`

## Notes
- The app bundles the Python backend and runs it automatically
- API key is stored securely using electron-store
- First run will require configuring the Google Gemini API key

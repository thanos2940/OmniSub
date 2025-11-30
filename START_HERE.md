# 🚀 START HERE - Complete Setup Guide

## 📋 Prerequisites

- Python 3.11+
- Node.js 18+
- Google Gemini API Key

---

## ⚡ Quick Setup (Copy & Paste)

### 1. Configure API Key

```bash
# In project root directory
echo "GOOGLE_API_KEY=your_actual_api_key_here" > .env
```

⚠️ **IMPORTANT**: Replace `your_actual_api_key_here` with your real key!

Get one here: https://makersuite.google.com/app/apikey

---

### 2. Backend Setup

```bash
cd backend

# Create virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate  # macOS/Linux
# OR
.venv\Scripts\activate     # Windows

# Install ALL dependencies (including aiosqlite)
pip install --upgrade pip
pip install -r requirements.txt

# Verify aiosqlite is installed
pip list | grep aiosqlite
# Should show: aiosqlite 0.19.0 or higher

# Start server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Wait for**: `Application startup complete.`

**Backend running at**: http://localhost:8000

---

### 3. Frontend Setup (NEW TERMINAL)

```bash
cd frontend

# Install dependencies
npm install

# Start dev server
npm run dev
```

**Frontend running at**: http://localhost:5173

---

## ✅ Verify Installation

### Test Backend

Open new terminal:
```bash
curl http://localhost:8000/health
```

**Expected**:
```json
{
  "status": "healthy",
  "service": "OmbiSub API",
  "version": "5.0",
  "adk_enabled": true,
  "api_key_configured": true
}
```

### Test Frontend

1. Open browser: **http://localhost:5173**
2. Should see OmbiSub landing page
3. Click "Create New Project"
4. If project form appears → ✅ **Success!**

---

## 🐛 Common Issues

### ❌ "async driver" Error

**Error**: `The loaded 'pysqlite' is not async`

**Fix**:
```bash
cd backend
pip install aiosqlite>=0.19.0
# Then restart backend
```

See: [INSTALL_AIOSQLITE.md](INSTALL_AIOSQLITE.md)

---

### ❌ "GOOGLE_API_KEY not found"

**Fix**: Check .env file location
```bash
# Should be in PROJECT ROOT, not backend/
ls -la .env
cat .env  # Should show: GOOGLE_API_KEY=...
```

---

### ❌ Port 8000 Already in Use

**Fix**: Use different port
```bash
uvicorn main:app --reload --port 8001

# Update frontend/src/api.js:
# const API_URL = "http://localhost:8001"
```

---

### ❌ CORS Errors in Browser

**Cause**: Backend not running or crashed

**Fix**:
1. Check backend terminal for errors
2. Restart backend
3. Hard refresh browser (`Ctrl+Shift+R`)

---

### ❌ "Module not found" Errors

**Fix**: Reinstall dependencies
```bash
cd backend
pip install -r requirements.txt --force-reinstall
```

---

## 📊 What Should Be Running

You should have **2 terminal windows**:

### Terminal 1: Backend
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete.
```

### Terminal 2: Frontend
```
VITE v5.x.x  ready in XXX ms

➜  Local:   http://localhost:5173/
```

---

## 🎯 First Steps After Setup

1. **Create Project** - Click "Create New Project"
2. **Upload SRT File** - Drag & drop .srt subtitle file
3. **Create Glossary** - Click "Create Glossary" (research mode)
4. **Translate** - Click "Translate" on an episode

---

## 📚 Documentation

- **README.md** - Full feature documentation
- **QUICKSTART.md** - 5-minute setup
- **CLAUDE.md** - Developer guide
- **DEPLOYMENT.md** - Production deployment
- **CRITICAL_FIXES.md** - Recent bug fixes

---

## 🔧 Development Tools

### API Documentation
http://localhost:8000/docs (when backend running)

### Test Endpoints
```bash
./TEST_ENDPOINTS.sh
```

### Backend Logs
Watch backend terminal for real-time logs

### Frontend Errors
Browser Console (F12) → Console tab

---

## 🆘 Still Having Issues?

### 1. Check Requirements

```bash
# Python version
python3 --version
# Should be 3.11+

# Node version
node --version
# Should be 18+
```

### 2. Verify Dependencies

```bash
cd backend
pip list | grep -E "fastapi|uvicorn|google-adk|aiosqlite"
```

Should show:
- fastapi >= 0.100.0
- uvicorn >= 0.22.0
- google-adk >= 0.1.0
- **aiosqlite >= 0.19.0** ⚠️ CRITICAL

### 3. Clear Everything and Retry

```bash
# Backend
cd backend
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Frontend
cd ../frontend
rm -rf node_modules package-lock.json
npm install
```

---

## 🐳 Alternative: Docker

Prefer one-command setup?

```bash
# Configure API key
echo "GOOGLE_API_KEY=your_key" > .env

# Start everything
docker-compose up -d

# Access: http://localhost:8000
```

See: [DEPLOYMENT.md](DEPLOYMENT.md)

---

## ✅ Success Checklist

Before using OmbiSub, verify:

- [ ] Python 3.11+ installed
- [ ] Node.js 18+ installed
- [ ] API key configured in `.env`
- [ ] Backend dependencies installed (including **aiosqlite**)
- [ ] Backend running without errors
- [ ] Frontend running
- [ ] Health check returns `{"status":"healthy"}`
- [ ] Browser shows OmbiSub interface

---

## 🎉 You're Ready!

If all checks pass, you're ready to use OmbiSub!

Start by creating your first project and uploading a subtitle file.

---

**Need Help?**
- Check [BUGFIXES.md](BUGFIXES.md) for known issues
- Review [CRITICAL_FIXES.md](CRITICAL_FIXES.md) for recent fixes
- Read [INSTALL_AIOSQLITE.md](INSTALL_AIOSQLITE.md) for async driver setup

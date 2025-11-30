# 🔧 Troubleshooting Guide

## 🚨 Backend Won't Start

### Check Backend Terminal

Look for these errors:

#### Error 1: SQLAlchemy Async Driver
```
ValueError: Failed to create database engine
sqlalchemy.exc.InvalidRequestError: The asyncio extension requires an async driver
```

**Fix**: Install aiosqlite
```bash
cd backend
source .venv/bin/activate
pip install aiosqlite>=0.19.0
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

#### Error 2: Module Not Found
```
ModuleNotFoundError: No module named 'google.adk'
```

**Fix**: Install all dependencies
```bash
cd backend
pip install -r requirements.txt
```

#### Error 3: API Key Missing
```
GOOGLE_API_KEY not found
```

**Fix**: Check .env file
```bash
# Should be in PROJECT ROOT, not backend/
cat ../.env
# Should show: GOOGLE_API_KEY=...
```

---

## 🌐 CORS Errors in Browser

**Symptom**: `Access to XMLHttpRequest... blocked by CORS policy`

**Root Cause**: Backend crashed (500 error) before sending CORS headers

### Step 1: Check Backend Status

Is backend terminal showing errors? If yes, fix those first.

### Step 2: Restart Backend

```bash
# Stop: Ctrl+C
# Start:
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Step 3: Hard Refresh Browser

`Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac)

---

## 💥 500 Internal Server Error

### Check Episode Names

Long filenames with special characters can cause issues:
- `Snow Queen (2002) DVD x265 AAC 2.0 Radarr.en.srt` ❌ TOO LONG
- `SnowQueen_S01E01.srt` ✅ BETTER

**Recommendation**: Rename episodes to simple names:
- `S01E01.srt`
- `Episode_01.srt`
- `SnowQueen_E01.srt`

### Check Backend Logs

Backend terminal shows exact error. Common issues:

**File not found**:
```python
FileNotFoundError: [Errno 2] No such file or directory
```

**Fix**: Episode doesn't exist or name mismatch

**JSON decode error**:
```python
JSONDecodeError: Expecting value
```

**Fix**: Corrupted data file, delete and re-upload

**Path too long (Windows)**:
```python
OSError: [WinError 206] The filename or extension is too long
```

**Fix**: Use shorter episode names

---

## 📁 Episodes Show 0 Lines

**Cause**: Episode data wasn't saved correctly during upload

**Fix**: Re-upload the episode
1. Delete episode
2. Upload again with shorter filename
3. Check line count updates

---

## 📝 Glossary Shows Empty

**Cause**: Glossary data isn't in session state

**Fix**: Check project.json
```bash
# View glossary
cat backend/projects/YourProject/project.json | grep -A 5 "glossary"
```

Should show:
```json
"glossary": {
  "terms": [
    {"term": "...", "translation": "..."}
  ]
}
```

If empty, create/enhance glossary again.

---

## 🔄 Jobs Fail Immediately

### "list indices must be integers" Error

**Cause**: Wrong ADK method used

**Fix**: Already fixed in latest code
```python
# Wrong:
runner.run_debug(prompt, session_id=...)

# Correct:
runner.run(prompt)
```

Restart backend to load fixed code.

### "No module named 'adk_agents.operations'"

**Cause**: Import error

**Fix**: Check file exists
```bash
ls -la backend/adk_agents/operations.py
# Should exist
```

If missing, file was deleted. Restore from git.

---

## 🖥️ Windows-Specific Issues

### Path Length Limit

Windows has a 260-character path limit.

**Symptom**: `OSError: [WinError 206]`

**Fix**:
1. Use shorter project names
2. Use shorter episode names
3. Move project closer to root (e.g., `C:\OmbiSub\`)

### Line Endings

**Symptom**: Parsing errors, extra characters

**Fix**: Ensure SRT files use LF or CRLF, not mixed
```bash
# Convert CRLF to LF
dos2unix yourfile.srt
```

### Permissions

**Symptom**: `PermissionError: [WinError 32]`

**Fix**: Close any programs accessing the files
- Don't open SRT files in editors while processing
- Close database viewers

---

## 🧹 Nuclear Option: Full Reset

If nothing works, start fresh:

### Backend Reset
```bash
cd backend

# Delete virtual environment
rm -rf .venv  # Linux/Mac
# OR
rmdir /s .venv  # Windows

# Delete caches
find . -type d -name "__pycache__" -delete
rm -rf ombisub_sessions.db

# Recreate
python3 -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt

# Verify aiosqlite
pip list | grep aiosqlite
# Should show: aiosqlite 0.19.0+

# Start
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend Reset
```bash
cd frontend

# Delete node_modules
rm -rf node_modules package-lock.json

# Reinstall
npm install

# Start
npm run dev
```

---

## 🔍 Debug Mode

### Enable Verbose Backend Logging

Edit `backend/main.py`, add at top:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Restart backend. You'll see detailed logs.

### Check Browser Console

1. Open browser (F12)
2. Go to Console tab
3. Look for red errors
4. Network tab shows failed requests with status codes

### Test API Directly

```bash
# Health check
curl http://localhost:8000/health

# List projects
curl http://localhost:8000/projects

# Get specific project
curl http://localhost:8000/projects/ProjectName

# List episodes
curl http://localhost:8000/projects/ProjectName/episodes

# Get episode (use URL encoding for spaces)
curl "http://localhost:8000/projects/ProjectName/episodes/S01E01"
```

---

## 📊 System Requirements Check

### Python Version
```bash
python3 --version
# Need: 3.11 or higher
```

### Node Version
```bash
node --version
# Need: 18 or higher
```

### Disk Space
```bash
df -h .  # Linux/Mac
# OR
dir  # Windows

# Need: At least 500MB free
```

### Memory
```bash
free -h  # Linux
# Need: At least 2GB available
```

---

## 🆘 Last Resort Checks

### Is Port 8000 Accessible?

```bash
# Try accessing from different port
uvicorn main:app --reload --port 8888

# Update frontend/src/api.js:
# const API_URL = "http://localhost:8888"
```

### Firewall Blocking?

Windows Defender or antivirus might block uvicorn.

**Fix**: Add exception for Python/uvicorn

### Antivirus Interfering?

Some antivirus software blocks file writes.

**Fix**: Add exception for project directory

---

## ✅ Successful Startup Checklist

Backend terminal should show:
```
INFO:     Will watch for changes in these directories: [...]
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [...]
INFO:     Started server process [...]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

**No errors** = Backend is healthy!

Health check should return:
```json
{
  "status": "healthy",
  "service": "OmbiSub API",
  "version": "5.0",
  "adk_enabled": true,
  "api_key_configured": true
}
```

---

## 📞 Getting Help

If issues persist:

1. **Check backend terminal** - Copy the full error message
2. **Check browser console** - Copy JavaScript errors
3. **Try API directly** - Use curl commands above
4. **Check file permissions** - Ensure project directory is writable
5. **Check Python/Node versions** - Ensure requirements met

Include all error messages when seeking help!

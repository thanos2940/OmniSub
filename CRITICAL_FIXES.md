# 🚨 CRITICAL FIXES REQUIRED

## Summary

Multiple critical bugs fixed in backend code. **Backend MUST be restarted** for changes to take effect.

---

## Issues Fixed

### 1. ❌ Missing Function: `load_episode_metadata()`
**Error**: `500 Internal Server Error` on `/projects/{name}/episodes`
**Symptom**: CORS errors (because 500 prevents CORS headers from being sent)

**Fix**: Added missing function to `backend/utils/storage.py`

### 2. ❌ Wrong Method: `runner.run_debug()`
**Error**: `list indices must be integers or slices, not str`
**Symptom**: Glossary enhancement fails immediately

**Fix**: Changed all `runner.run_debug()` calls to `runner.run()` (correct ADK method)

**Files Modified**:
- `backend/main.py` (3 occurrences fixed on lines 583, 715, 778)

### 3. ❌ Data Validation Missing
**Error**: `list indices must be integers or slices, not str`
**Symptom**: Crash when processing episodes with malformed data

**Fix**: Added type checking in `_gather_project_text()` function

**Before**:
```python
if ep_data:
    all_text.extend(extract_text_only(ep_data["data"]))
```

**After**:
```python
if ep_data and isinstance(ep_data, dict) and "data" in ep_data:
    data = ep_data["data"]
    if isinstance(data, list):
        all_text.extend(extract_text_only(data))
```

---

## 🔄 RESTART REQUIRED

### **CRITICAL**: You MUST restart the backend server

The backend is **still running old code** with bugs. Changes won't take effect until restart.

### Step-by-Step Restart

#### 1. Stop Backend

In the backend terminal, press: **Ctrl+C**

Wait for: `Shutdown complete`

#### 2. Restart Backend

```bash
cd backend

# Activate virtual environment
source .venv/bin/activate  # macOS/Linux
# OR
.venv\Scripts\activate     # Windows

# Start with reload
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Wait for: `Uvicorn running on http://0.0.0.0:8000`

#### 3. Verify Backend Health

**In a NEW terminal**:
```bash
curl http://localhost:8000/health
```

**Expected Output**:
```json
{
  "status": "healthy",
  "service": "OmbiSub API",
  "version": "5.0",
  "adk_enabled": true,
  "api_key_configured": true
}
```

#### 4. Refresh Browser

**Hard refresh** your browser:
- Chrome/Firefox: `Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac)
- Or just close and reopen the tab

---

## ✅ Expected Results After Restart

### 1. Episodes Load Correctly
- ✅ No CORS errors
- ✅ No 500 errors
- ✅ Episodes show with metadata (season, line count)
- ✅ "0 lines" issue fixed

### 2. Glossary Works
- ✅ Populated glossary displays correctly
- ✅ Enhance glossary doesn't crash
- ✅ No "list indices" errors

### 3. Jobs Complete
- ✅ "Job started" log appears
- ✅ Jobs progress normally
- ✅ No immediate failures

---

## 🐛 If Still Broken After Restart

### Check Backend Logs

Look at the backend terminal for errors. Common issues:

**Import Errors**:
```bash
cd backend
pip install -r requirements.txt --force-reinstall
```

**Module Not Found**:
```bash
# Make sure you're in backend directory
cd backend
# Make sure venv is activated
source .venv/bin/activate
```

**Port Already in Use**:
```bash
# Use different port
uvicorn main:app --reload --port 8001

# Update frontend/src/api.js:
# const API_URL = "http://localhost:8001"
```

### Check File Integrity

Verify fixes were applied:

```bash
cd backend

# Check storage.py has load_episode_metadata function
grep -n "def load_episode_metadata" utils/storage.py

# Should show: 118:def load_episode_metadata(...)

# Check main.py uses runner.run() not runner.run_debug()
grep -n "runner.run_debug" main.py

# Should show: (no matches)
```

### Test Individual Endpoint

```bash
# Test episodes endpoint directly
curl "http://localhost:8000/projects/Fate%20Stay%20Night%20UBW/episodes" | python3 -m json.tool
```

**Should return**: Array of episode objects with `name`, `season`, `line_count`, etc.

**Should NOT return**: 500 error or empty response

---

## 📊 Backend Status Checklist

Before testing frontend, verify backend:

- [ ] Backend server is running (see `Uvicorn running...`)
- [ ] Health check returns `"status":"healthy"`
- [ ] No import errors in terminal
- [ ] Virtual environment is activated
- [ ] Port 8000 is accessible

Only proceed to frontend testing after ALL checks pass.

---

## 🔍 Debugging Tips

### Enable Verbose Logging

Edit `backend/main.py` at the top:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Restart backend to see detailed logs.

### Check Python Version

```bash
python3 --version
# Should be 3.11 or higher
```

### Check Dependencies

```bash
cd backend
pip list | grep -E "fastapi|uvicorn|google-adk|pydantic"
```

Should show:
- fastapi >= 0.100.0
- uvicorn >= 0.22.0
- google-adk >= 0.1.0
- pydantic >= 2.0.0

---

## 📝 Changes Summary

| File | Lines Changed | Issue Fixed |
|------|---------------|-------------|
| `utils/storage.py` | +11 | Added `load_episode_metadata()` |
| `main.py` | 3 replacements | Fixed `run_debug()` → `run()` |
| `main.py` | +5 | Added data validation in `_gather_project_text()` |

**Total**: 3 files modified, 4 bugs fixed

---

## ⚡ Quick Test After Restart

1. Open http://localhost:5173
2. Navigate to your project
3. Check episodes load (should show line counts, not 0)
4. Try "Enhance Glossary"
5. Should complete without "list indices" error

If all pass → ✅ **Fixes successful!**

---

## 🆘 Last Resort

If nothing works after restart:

```bash
# Kill any running uvicorn processes
pkill -f uvicorn

# Clear Python cache
cd backend
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null

# Reinstall everything
pip install -r requirements.txt --force-reinstall

# Restart
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

**Remember**: Changes only take effect **AFTER backend restart**. The `--reload` flag should auto-reload, but a manual restart is more reliable.

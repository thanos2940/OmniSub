# 🔄 Restart Instructions

## The Issue
Backend API was missing `load_episode_metadata()` function, causing 500 errors.

## The Fix
Added missing function to `backend/utils/storage.py`.

## ⚡ Quick Restart

### Stop Current Servers

**Terminal 1 (Backend)**: Press `Ctrl+C`
**Terminal 2 (Frontend)**: Press `Ctrl+C`

### Restart Backend

```bash
cd backend

# Activate virtual environment (if not already active)
source .venv/bin/activate  # macOS/Linux
# OR
.venv\Scripts\activate     # Windows

# Start server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Wait for**: `Uvicorn running on http://0.0.0.0:8000`

### Restart Frontend

```bash
cd frontend
npm run dev
```

**Wait for**: `Local: http://localhost:5173/`

---

## ✅ Verification

### Test Backend

```bash
# In a new terminal
curl http://localhost:8000/health
```

**Expected output**:
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

1. Open browser: http://localhost:5173
2. Navigate to a project
3. Episodes should load without CORS errors

---

## 🐛 If Still Having Issues

### Backend not starting?

```bash
cd backend
python3 -m py_compile main.py utils/storage.py
# Should show no errors
```

### CORS errors persist?

```bash
# Check backend is ACTUALLY running
curl http://localhost:8000/health

# If no response, backend isn't running
# Check terminal for error messages
```

### Module import errors?

```bash
cd backend
pip install -r requirements.txt --force-reinstall
```

---

## 📝 What Changed

**File**: `backend/utils/storage.py`

**Added**:
```python
def load_episode_metadata(project_name: str, episode_name: str) -> Optional[Dict]:
    """Load episode metadata only."""
    metadata_file = PROJECTS_DIR / project_name / "episodes" / episode_name / "metadata.json"
    if not metadata_file.exists():
        return None

    try:
        with open(metadata_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None
```

**Used in**: `/projects/{name}/episodes` endpoint to return episode metadata

---

## 🎯 Expected Behavior After Restart

✅ Episodes load without 500 errors
✅ No CORS errors
✅ Episode metadata displays (season, line count, translation status)
✅ Season grouping works correctly

---

## Still broken?

Check the backend terminal output for specific error messages and share them.

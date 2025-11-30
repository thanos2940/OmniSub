# 🔧 Fix: Install aiosqlite

## The Problem

ADK's `DatabaseSessionService` requires an **async database driver**, but the default SQLite driver (`pysqlite`) is synchronous.

**Error message**:
```
sqlalchemy.exc.InvalidRequestError: The asyncio extension requires an async driver to be used.
The loaded 'pysqlite' is not async.
```

## The Solution

Install `aiosqlite` - the async SQLite driver.

---

## ⚡ Quick Fix

### Step 1: Stop Backend (if running)
Press `Ctrl+C` in backend terminal

### Step 2: Install aiosqlite

```bash
cd backend

# Make sure virtual environment is activated
source .venv/bin/activate  # macOS/Linux
# OR
.venv\Scripts\activate     # Windows

# Install aiosqlite
pip install aiosqlite>=0.19.0
```

### Step 3: Verify Installation

```bash
pip list | grep aiosqlite
```

Should show:
```
aiosqlite    0.19.0 (or higher)
```

### Step 4: Restart Backend

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Should start successfully** without SQLAlchemy errors!

---

## ✅ Verification

After restart, you should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process...
INFO:     Started server process...
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

**No errors** about "async driver" or "pysqlite"!

---

## 🔍 What Changed

### requirements.txt
Added: `aiosqlite>=0.19.0`

### adk_config/session_service.py
Changed database URL:
- Before: `sqlite:///path/to/db`
- After: `sqlite+aiosqlite:///path/to/db`

The `+aiosqlite` tells SQLAlchemy to use the async driver.

---

## 🐛 If Install Fails

### Problem: pip install fails

**Solution**: Update pip first
```bash
pip install --upgrade pip
pip install aiosqlite>=0.19.0
```

### Problem: "No module named 'aiosqlite'"

**Solution**: Verify virtual environment
```bash
# Check you're in the right venv
which python  # Should show .venv/bin/python

# If not, activate it
source .venv/bin/activate
```

### Problem: Still getting "pysqlite" error

**Solution**: Clear Python cache
```bash
cd backend
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
python3 -m py_compile adk_config/session_service.py
```

---

## 📚 Technical Details

### Why Async Drivers?

ADK uses `asyncio` for non-blocking operations. The default `pysqlite` driver is synchronous (blocking), which would freeze the event loop.

`aiosqlite` wraps SQLite in async calls, making it compatible with ADK's async architecture.

### Database URL Format

**Synchronous** (doesn't work with ADK):
```
sqlite:///path/to/database.db
```

**Asynchronous** (works with ADK):
```
sqlite+aiosqlite:///path/to/database.db
         ^^^^^^^^^
         async driver
```

### Alternative: PostgreSQL

For production, consider async PostgreSQL:

```bash
pip install asyncpg
```

Update `session_service.py`:
```python
db_url = "postgresql+asyncpg://user:pass@host/dbname"
```

---

## 🎯 Next Steps

After successful install and restart:

1. Open http://localhost:5173
2. Test project creation
3. Test glossary enhancement
4. Verify sessions are being saved

---

## ℹ️ More Info

- **aiosqlite docs**: https://aiosqlite.omnilib.dev/
- **SQLAlchemy async**: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- **ADK sessions**: Part of Google ADK framework

---

**Remember**: This is a one-time install. Once `aiosqlite` is in requirements.txt, future installations will include it automatically.

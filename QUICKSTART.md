# OmbiSub Quick Start Guide

## 🚀 Fast Setup (5 minutes)

### Prerequisites Check

```bash
# Check Python version (need 3.11+)
python3 --version

# Check Node.js version (need 18+)
node --version

# Get Google Gemini API Key
# Visit: https://makersuite.google.com/app/apikey
```

---

## 🔧 Setup Steps

### 1. Configure API Key

```bash
# In project root directory
echo "GOOGLE_API_KEY=your_api_key_here" > .env
```

⚠️ **Important**: Replace `your_api_key_here` with your actual API key!

### 2. Start Backend

```bash
# Terminal 1
cd backend

# Create virtual environment (first time only)
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate  # macOS/Linux
# OR
.venv\Scripts\activate  # Windows

# Install dependencies (first time only)
pip install -r requirements.txt

# Start server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Backend running at**: http://localhost:8000

**Check it's working**:
- Visit: http://localhost:8000/health
- Should see: `{"status":"healthy",...}`

### 3. Start Frontend

```bash
# Terminal 2
cd frontend

# Install dependencies (first time only)
npm install

# Start development server
npm run dev
```

**Frontend running at**: http://localhost:5173

---

## ✅ Verify Installation

Open browser to: **http://localhost:5173**

You should see the OmbiSub landing page.

### Quick Test

1. Click "Create New Project"
2. Enter project name: "Test Project"
3. Select target language: "Spanish"
4. Click "Create Project"

If you see the project detail page → ✅ **Success!**

---

## 🐛 Troubleshooting

### Backend Issues

**Problem**: `ModuleNotFoundError`
```bash
# Solution: Reinstall dependencies
cd backend
pip install -r requirements.txt
```

**Problem**: `GOOGLE_API_KEY not found`
```bash
# Solution: Check .env file location
ls -la ../.env  # Should exist in project root
cat ../.env     # Should contain your API key
```

**Problem**: Port 8000 already in use
```bash
# Solution: Use different port
uvicorn main:app --reload --port 8001

# Then update frontend/src/api.js:
# const API_URL = "http://localhost:8001"
```

### Frontend Issues

**Problem**: `npm install` fails
```bash
# Solution: Clear cache and retry
cd frontend
rm -rf node_modules package-lock.json
npm cache clean --force
npm install
```

**Problem**: CORS errors
```bash
# Solution: Restart backend server
# Make sure backend is running BEFORE frontend
```

**Problem**: Cannot connect to backend
```bash
# Check backend is running:
curl http://localhost:8000/health

# If nothing, backend isn't running
```

---

## 🔍 Test API Endpoints

```bash
# From project root
./TEST_ENDPOINTS.sh
```

Or manually:

```bash
# Health check
curl http://localhost:8000/health

# List projects
curl http://localhost:8000/projects

# API documentation
# Visit: http://localhost:8000/docs
```

---

## 🎯 Next Steps

1. **Upload subtitle files** (.srt format)
2. **Create glossary** (research mode or analysis mode)
3. **Translate episodes** (single or batch)
4. **Download translations**

See [README.md](README.md) for detailed feature documentation.

---

## 📚 Documentation

- **README.md** - Full documentation
- **CLAUDE.md** - Developer guide
- **DEPLOYMENT.md** - Production deployment
- **API Docs** - http://localhost:8000/docs (when running)

---

## 🆘 Still Having Issues?

1. Check backend logs in terminal
2. Check browser console for errors (F12)
3. Verify .env file contains valid API key
4. Try restarting both servers
5. Check firewall isn't blocking ports 8000/5173

---

## 🐳 Alternative: Docker Setup

Prefer one-command setup? Use Docker:

```bash
# Configure API key
echo "GOOGLE_API_KEY=your_key" > .env

# Start everything
docker-compose up -d

# Access at: http://localhost:8000
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for details.

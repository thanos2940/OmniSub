# OmbiSub Deployment Guide

Complete deployment instructions for all platforms.

## Table of Contents

- [Docker Deployment](#docker-deployment)
- [Windows Desktop App](#windows-desktop-app)
- [Development Setup](#development-setup)
- [Production (Vertex AI)](#production-vertex-ai)
- [GitHub Pages](#github-pages-frontend-only)

---

## Docker Deployment

### Quick Start

```bash
# 1. Clone repository
git clone https://github.com/yourusername/ombisub.git
cd ombisub

# 2. Configure API key
echo "GOOGLE_API_KEY=your_api_key_here" > .env

# 3. Build and run
docker-compose up -d

# 4. Access application
# Frontend: http://localhost:8000
# API Docs: http://localhost:8000/docs
```

### Manual Docker Build

```bash
# Build image
docker build -t ombisub:latest .

# Run container
docker run -d \
  -p 8000:8000 \
  -e GOOGLE_API_KEY=your_api_key \
  -v $(pwd)/backend/projects:/app/projects \
  --name ombisub \
  ombisub:latest

# View logs
docker logs -f ombisub
```

### Docker Compose (Recommended)

```yaml
# docker-compose.yml
version: '3.8'

services:
  ombisub:
    build: .
    ports:
      - "8000:8000"
    environment:
      - GOOGLE_API_KEY=${GOOGLE_API_KEY}
    volumes:
      - ./backend/projects:/app/projects
      - ./backend/ombisub_sessions.db:/app/ombisub_sessions.db
    restart: unless-stopped
```

Commands:
```bash
docker-compose up -d      # Start
docker-compose down       # Stop
docker-compose logs -f    # View logs
docker-compose restart    # Restart
```

---

## Windows Desktop App

### Prerequisites

- Node.js 18+
- Python 3.11+ (will be bundled in executable)

### Build Steps

```bash
# 1. Build frontend first
cd frontend
npm install
npm run build
cd ..

# 2. Build Electron app
cd electron-app
npm install
npm run build:win
```

### Output

Executable located at: `electron-app/dist/OmbiSub Setup.exe`

### Installation

1. Double-click `OmbiSub Setup.exe`
2. Follow installation wizard
3. Launch OmbiSub from Start Menu
4. Enter Google Gemini API key on first run

### Uninstall

Windows Settings → Apps → OmbiSub → Uninstall

---

## Development Setup

### Backend

```bash
cd backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure API key (in project root)
cd ..
echo "GOOGLE_API_KEY=your_api_key" > .env
cd backend

# Run development server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Access:
- API: http://localhost:8000
- Interactive Docs: http://localhost:8000/docs
- Health Check: http://localhost:8000/health

### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev
```

Access: http://localhost:5173

### Full Stack Development

Terminal 1 (Backend):
```bash
cd backend
source .venv/bin/activate
uvicorn main:app --reload
```

Terminal 2 (Frontend):
```bash
cd frontend
npm run dev
```

---

## Production (Vertex AI)

### Prerequisites

- Google Cloud Project with billing enabled
- Vertex AI API enabled
- ADK CLI installed: `pip install google-adk`
- Authenticated: `gcloud auth login`

### Deployment Steps

#### 1. Configure Deployment

```bash
cd backend/deployment

# Edit agent.py with production settings
# Edit .agent_engine_config.json for resource limits
```

#### 2. Deploy to Vertex AI

```bash
adk deploy agent_engine \
  --project=YOUR_PROJECT_ID \
  --region=us-central1 \
  backend/deployment
```

#### 3. Verify Deployment

```bash
# List deployed agents
adk list --project=YOUR_PROJECT_ID

# Test endpoint
curl https://YOUR_PROJECT_ID.us-central1.run.app/health
```

### Vertex AI Configuration

**Resource Limits** (`.agent_engine_config.json`):
```json
{
  "min_instances": 0,
  "max_instances": 10,
  "resource_limits": {
    "cpu": "4",
    "memory": "8Gi"
  }
}
```

**Cost Optimization**:
- Set `min_instances: 0` for auto-scaling to zero
- Use session database for state persistence
- Enable context caching (already configured)

---

## GitHub Pages (Frontend Only)

### Static Frontend Deployment

```bash
cd frontend

# Build for production
npm run build

# Deploy to GitHub Pages
# (Requires backend API hosted separately)
```

### Configuration

Update `frontend/src/api.js` to point to production API:

```javascript
const API_BASE_URL = process.env.NODE_ENV === 'production'
  ? 'https://your-api-domain.com'
  : 'http://localhost:8000';
```

### GitHub Actions Workflow

```yaml
# .github/workflows/deploy-frontend.yml
name: Deploy Frontend

on:
  push:
    branches: [ main ]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: '18'
      - run: |
          cd frontend
          npm ci
          npm run build
      - uses: peaceiris/actions-gh-pages@v3
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./frontend/dist
```

---

## Environment Variables

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `GOOGLE_API_KEY` | Gemini API key | `AIza...` |

### Optional

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Backend server port | `8000` |
| `NODE_ENV` | Environment mode | `development` |

---

## Troubleshooting

### Docker Issues

**Container won't start**:
```bash
docker logs ombisub
# Check for missing GOOGLE_API_KEY
```

**Port already in use**:
```bash
# Change port in docker-compose.yml
ports:
  - "8080:8000"  # Use 8080 instead
```

### Desktop App Issues

**Backend won't start**:
- Check Python is installed: `python --version`
- Check API key is configured in app settings

**App crashes on launch**:
- Check Event Viewer (Windows) for error details
- Reinstall with administrator privileges

### Production Issues

**Vertex AI deployment fails**:
```bash
# Check quotas
gcloud compute project-info describe --project=YOUR_PROJECT_ID

# View deployment logs
adk logs --project=YOUR_PROJECT_ID
```

**High costs**:
- Verify `min_instances: 0` in config
- Check context caching is enabled
- Review API call patterns in logs

---

## Monitoring

### Docker

```bash
# Container stats
docker stats ombisub

# Health check
curl http://localhost:8000/health
```

### Desktop App

- Logs stored in: `%APPDATA%/ombisub/logs/`
- Check Task Manager for resource usage

### Vertex AI

```bash
# View metrics
gcloud logging read "resource.type=cloud_run_revision" \
  --project=YOUR_PROJECT_ID \
  --limit 50

# Monitor costs
gcloud billing projects describe YOUR_PROJECT_ID
```

---

## Scaling

### Docker

Increase container resources:

```yaml
services:
  ombisub:
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 8G
        reservations:
          cpus: '2'
          memory: 4G
```

### Vertex AI

Auto-scales based on traffic. Adjust limits in `.agent_engine_config.json`.

---

## Security

### API Key Management

**Development**:
- Store in `.env` file (gitignored)
- Never commit to repository

**Production**:
- Use Google Secret Manager
- Rotate keys regularly

**Desktop App**:
- Encrypted storage via electron-store
- User-specific configuration

### Network Security

**Docker**:
- Use custom network for isolation
- Enable firewall rules

**Vertex AI**:
- Configure VPC for private access
- Enable Cloud Armor for DDoS protection

---

## Backup

### Docker

```bash
# Backup projects
tar -czf backup-$(date +%Y%m%d).tar.gz backend/projects/

# Backup database
cp backend/ombisub_sessions.db backups/
```

### Desktop App

- Projects stored in: `%APPDATA%/ombisub/projects/`
- Backup this folder regularly

---

## Support

- **Issues**: https://github.com/yourusername/ombisub/issues
- **Documentation**: See README.md and CLAUDE.md
- **API Docs**: http://localhost:8000/docs (when running)

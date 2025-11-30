#!/bin/bash
# Test OmbiSub API Endpoints

API_URL="http://localhost:8000"

echo "🔍 Testing OmbiSub API Endpoints..."
echo ""

# Test 1: Health Check
echo "1️⃣ Testing Health Endpoint..."
curl -s "${API_URL}/health" | python3 -m json.tool || echo "❌ Health check failed"
echo ""

# Test 2: List Projects
echo "2️⃣ Testing List Projects..."
curl -s "${API_URL}/projects" | python3 -m json.tool || echo "❌ List projects failed"
echo ""

# Test 3: Get Specific Project
echo "3️⃣ Testing Get Project (if exists)..."
PROJECT_NAME="Fate%20Stay%20Night%20UBW"
curl -s "${API_URL}/projects/${PROJECT_NAME}" | python3 -m json.tool || echo "❌ Get project failed (may not exist)"
echo ""

# Test 4: List Episodes
echo "4️⃣ Testing List Episodes..."
curl -s "${API_URL}/projects/${PROJECT_NAME}/episodes" | python3 -m json.tool || echo "❌ List episodes failed"
echo ""

echo "✅ Test complete!"

import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from main import app
from utils import storage
from routers.settings import (
    obfuscate_api_key,
    deobfuscate_api_key,
    get_api_key,
    has_api_key
)

@pytest.fixture
def mock_config(monkeypatch, tmp_path):
    # Redirect CONFIG_FILE to a temporary path
    temp_config = tmp_path / "config.json"
    monkeypatch.setattr(storage, "CONFIG_FILE", temp_config)
    yield temp_config


def test_obfuscation_deobfuscation():
    key = "AIzaSyTestKey123456"
    obfuscated = obfuscate_api_key(key)
    assert obfuscated != key
    assert len(obfuscated) > 0
    
    deobfuscated = deobfuscate_api_key(obfuscated)
    assert deobfuscated == key


def test_api_key_persistence(mock_config, monkeypatch):
    # Clear environment variable
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    
    # Verify no key is loaded
    assert get_api_key() is None
    assert has_api_key() is False
    
    # Test through the router/api using test client
    client = TestClient(app)
    
    # Post key
    response = client.post("/api/config/api-key", json={"api_key": "test-key-555"})
    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    
    # Verify environment variable and get_api_key
    assert os.environ.get("GOOGLE_API_KEY") == "test-key-555"
    assert get_api_key() == "test-key-555"
    assert has_api_key() is True
    
    # Get status
    response = client.get("/api/config/api-key")
    assert response.status_code == 200
    assert response.json() == {"has_key": True}
    
    # Delete key
    response = client.delete("/api/config/api-key")
    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    
    # Verify deleted
    assert os.environ.get("GOOGLE_API_KEY") is None
    assert get_api_key() is None
    assert has_api_key() is False


def test_lifespan_key_restoration(mock_config, monkeypatch):
    # Clear environment
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    
    # Persist a key to config.json manually
    obfuscated_key = obfuscate_api_key("startup-restored-key")
    storage.save_global_config({"api_key_obfuscated": obfuscated_key})
    
    # Sanity check
    assert os.environ.get("GOOGLE_API_KEY") is None
    
    # Simulate lifespan startup using synchronous context manager
    with TestClient(app) as client:
        # Client context manager triggers FastAPI's lifespan
        # Verify that GOOGLE_API_KEY was restored into os.environ
        assert os.environ.get("GOOGLE_API_KEY") == "startup-restored-key"

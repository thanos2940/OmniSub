import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from routers.health import full_health


@pytest.mark.asyncio
async def test_health_api_key_configured():
    """Test health check passes when Gemini API key is configured."""
    mock_config = {}

    with patch("utils.storage.load_global_config", return_value=mock_config), \
         patch("routers.health.has_api_key", return_value=True), \
         patch("routers.health._verify_api_key", new_callable=AsyncMock, return_value={"valid": True, "error": None}), \
         patch("routers.health._sample_media_dir", return_value=""), \
         patch("routers.health.shutil.disk_usage", return_value=MagicMock(free=10**10)), \
         patch("routers.health.sonarr_test", new_callable=AsyncMock), \
         patch("routers.health.radarr_test", new_callable=AsyncMock), \
         patch("services.background_worker.worker.running_task", MagicMock(done=lambda: False)), \
         patch("utils.translation_queue.TranslationQueue.get_summary", return_value={}):

        res = await full_health()
        assert res["api_key"]["status"] == "pass"
        assert res["api_key"]["detail"] == "configured"


@pytest.mark.asyncio
async def test_health_api_key_missing_fails():
    """Test health check fails when Gemini API key is missing."""
    mock_config = {}

    with patch("utils.storage.load_global_config", return_value=mock_config), \
         patch("routers.health.has_api_key", return_value=False), \
         patch("routers.health._sample_media_dir", return_value=""), \
         patch("routers.health.shutil.disk_usage", return_value=MagicMock(free=10**10)), \
         patch("routers.health.sonarr_test", new_callable=AsyncMock), \
         patch("routers.health.radarr_test", new_callable=AsyncMock), \
         patch("services.background_worker.worker.running_task", MagicMock(done=lambda: False)), \
         patch("utils.translation_queue.TranslationQueue.get_summary", return_value={}):

        res = await full_health()
        assert res["api_key"]["status"] == "fail"
        assert res["overall"] == "fail"


def _health_patches(verdict):
    return [
        patch("utils.storage.load_global_config", return_value={}),
        patch("routers.health.has_api_key", return_value=True),
        patch("routers.health._verify_api_key", new_callable=AsyncMock, return_value=verdict),
        patch("routers.health._sample_media_dir", return_value=""),
        patch("routers.health.shutil.disk_usage", return_value=MagicMock(free=10**10)),
        patch("routers.health.sonarr_test", new_callable=AsyncMock),
        patch("routers.health.radarr_test", new_callable=AsyncMock),
        patch("services.background_worker.worker.running_task", MagicMock(done=lambda: False)),
        patch("utils.translation_queue.TranslationQueue.get_summary", return_value={}),
    ]


@pytest.mark.asyncio
async def test_health_fails_a_key_gemini_rejects():
    """A present-but-revoked key used to show PASS while every translation failed."""
    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in _health_patches({"valid": False, "error": "400 API key not valid"}):
            stack.enter_context(p)
        res = await full_health()
    assert res["api_key"]["status"] == "fail"
    assert "API key not valid" in res["api_key"]["hint"]
    assert res["overall"] == "fail"


@pytest.mark.asyncio
async def test_health_does_not_fail_the_key_when_gemini_is_unreachable():
    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in _health_patches({"valid": None, "error": "connect timeout"}):
            stack.enter_context(p)
        res = await full_health()
    assert res["api_key"]["status"] == "pass"
    assert "could not verify" in res["api_key"]["detail"]


@pytest.mark.asyncio
async def test_key_verification_classifies_and_caches(monkeypatch):
    from routers import settings as settings_router

    calls = []

    class FakeModels:
        async def list(self, config=None):
            calls.append(1)
            raise RuntimeError("400 INVALID_ARGUMENT. API key not valid. Please pass a valid API key.")

    class FakeClient:
        def __init__(self, api_key=None):
            self.aio = MagicMock(models=FakeModels())

    monkeypatch.setattr("google.genai.Client", FakeClient)
    settings_router._key_checks.clear()

    first = await settings_router.verify_gemini_key("bad-key")
    again = await settings_router.verify_gemini_key("bad-key")
    assert first == again and first["valid"] is False
    assert len(calls) == 1, "the verdict is cached"

    class Offline(FakeModels):
        async def list(self, config=None):
            raise OSError("getaddrinfo failed")

    FakeClient.__init__ = lambda self, api_key=None: setattr(self, "aio", MagicMock(models=Offline()))
    assert (await settings_router.verify_gemini_key("other-key"))["valid"] is None

"""Serving OmbiSub under a reverse-proxy path: the prefix is stripped (or absent), index.html carries
<base href> and window.__OMBISUB_BASE__, assets and the API work under the prefix, and the API-key gate
cannot be bypassed with it."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import run_server
from routers.schemas import SettingsRequest
from utils import storage
from utils.auth import generate_api_key
from utils.spa import inject_base, install_spa
from utils.url_base import UrlBaseMiddleware, normalize_url_base, safe_url_base

INDEX = ('<!doctype html><html><head><meta charset="UTF-8" /><title>T</title>'
         '<script type="module" src="./assets/app.js"></script></head><body><div id="root"></div></body></html>')
BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
AXIOS = {"Accept": "application/json, text/plain, */*"}


# ------------------------------------------------------------------ normalising the setting

@pytest.mark.parametrize("raw, expected", [
    (None, ""), ("", ""), ("/", ""), ("  ", ""), ("//", ""),
    ("/ombisub", "/ombisub"), ("ombisub", "/ombisub"), ("/ombisub/", "/ombisub"), (" /a//b/ ", "/a/b"),
    ("/my-app_1.0/sub~", "/my-app_1.0/sub~"),
])
def test_normalize(raw, expected):
    assert normalize_url_base(raw) == expected


@pytest.mark.parametrize("bad", [
    "/a b", "/a?x=1", "/a#frag", '/"><script>', "/a'b", "/<b>", "/a\\b", "/..", "/a/../b", "/./a", "/é", "/a;b", "http://x/y",
])
def test_normalize_rejects_what_could_break_a_page_or_a_path(bad):
    with pytest.raises(ValueError):
        normalize_url_base(bad)
    assert safe_url_base(bad) == ""


def test_the_setting_is_validated_when_saved():
    assert SettingsRequest(url_base="ombisub/").url_base == "/ombisub"
    with pytest.raises(ValidationError):
        SettingsRequest(url_base='/"><script>alert(1)</script>')
    assert SettingsRequest(update_channel=" MAIN ").update_channel == "main"
    with pytest.raises(ValidationError):
        SettingsRequest(update_channel="nightly")
    assert SettingsRequest().url_base == "" and SettingsRequest().trusted_proxies == "127.0.0.1"


def test_trusted_proxies_default_and_override():
    assert run_server.trusted_proxies({}) == "127.0.0.1"
    assert run_server.trusted_proxies({"trusted_proxies": "  "}) == "127.0.0.1"
    assert run_server.trusted_proxies({"trusted_proxies": "10.0.0.5, 10.0.0.6"}) == "10.0.0.5, 10.0.0.6"
    assert run_server.trusted_proxies({"trusted_proxies": "*"}) == "*"


# ------------------------------------------------------------------ index.html injection

def test_inject_puts_base_and_global_right_after_head():
    out = inject_base(INDEX, "/ombisub")
    assert out.index("<head>") + len("<head>") == out.index('<base href="/ombisub/">')
    assert 'window.__OMBISUB_BASE__ = "/ombisub";' in out
    assert out.index("<base") < out.index('<script type="module"')


def test_inject_without_prefix_still_sets_base_so_deep_links_find_their_assets():
    out = inject_base(INDEX, "")
    assert '<base href="/">' in out and 'window.__OMBISUB_BASE__ = "";' in out


def test_inject_never_writes_raw_user_input():
    for evil in ('/"><script>alert(1)</script>', "/a</script><script>x", "/a'b"):
        out = inject_base(INDEX, evil)  # an invalid value counts as "no prefix"
        assert "alert(1)" not in out and "<script>x" not in out
        assert '<base href="/">' in out and 'window.__OMBISUB_BASE__ = "";' in out


def test_inject_into_a_page_without_head():
    assert inject_base("<p>x</p>", "/o").startswith('<base href="/o/">')


# ------------------------------------------------------------------ a small app wired like main.py

@pytest.fixture
def static_dir(tmp_path):
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return root


def make_app(static_dir, url_base):
    app = FastAPI()

    @app.get("/api/ping")
    async def ping():
        return {"pong": True}

    @app.get("/queue")
    async def queue_alias():
        return {"items": []}

    install_spa(app, static_dir, url_base)
    return TestClient(UrlBaseMiddleware(app, url_base))


@pytest.mark.parametrize("prefix", ["/ombisub", ""], ids=["prefix kept (Caddy handle)", "prefix stripped (handle_path)"])
def test_app_answers_with_or_without_the_prefix(static_dir, prefix):
    client = make_app(static_dir, "/ombisub")
    assert client.get(f"{prefix}/api/ping").json() == {"pong": True}
    asset = client.get(f"{prefix}/assets/app.js")
    assert asset.status_code == 200 and "console.log" in asset.text
    assert client.get(f"{prefix}/favicon.svg").text == "<svg/>"
    for page in (f"{prefix}/", f"{prefix}/project/Some%20Show/episode/S01E01", f"{prefix}/settings"):
        resp = client.get(page, headers=BROWSER)
        assert resp.status_code == 200
        assert '<base href="/ombisub/">' in resp.text and 'window.__OMBISUB_BASE__ = "/ombisub";' in resp.text


def test_prefix_is_stripped_exactly_once_and_only_at_a_segment_boundary(static_dir):
    client = make_app(static_dir, "/ombisub")
    for path in ("/ombisub/ombisub/api/ping", "/ombisubby/api/ping"):
        resp = client.get(path, headers=AXIOS)  # not the API: at most the app's index.html
        assert "pong" not in resp.text
    assert client.get("/ombisub").status_code == 200  # the bare prefix is the app's root


def test_browser_gets_the_app_where_an_api_alias_shares_the_path(static_dir):
    client = make_app(static_dir, "/ombisub")
    for prefix in ("", "/ombisub"):
        assert "<base" in client.get(f"{prefix}/queue", headers=BROWSER).text
        assert client.get(f"{prefix}/queue", headers=AXIOS).json() == {"items": []}


def test_index_is_served_without_a_prefix_setting_too(static_dir):
    client = make_app(static_dir, "")
    resp = client.get("/health", headers=BROWSER)
    assert '<base href="/">' in resp.text
    assert resp.headers["cache-control"] == "no-cache"


def test_unknown_api_paths_stay_404_and_traversal_does_not_escape_static(static_dir, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    client = make_app(static_dir, "/ombisub")
    assert client.get("/ombisub/api/nothing").status_code == 404
    leaked = client.get("/ombisub/..%2fsecret.txt")
    assert "secret" not in leaked.text


def test_a_rebuilt_index_is_picked_up_without_a_restart(static_dir):
    client = make_app(static_dir, "/o")
    assert "v1" not in client.get("/", headers=BROWSER).text
    (static_dir / "index.html").write_text(INDEX.replace("<title>T</title>", "<title>v1</title>"), encoding="utf-8")
    import os
    os.utime(static_dir / "index.html", (1, 2_000_000_000))
    assert "<title>v1</title>" in client.get("/", headers=BROWSER).text


# ------------------------------------------------------------------ the real app: the API-key gate

@pytest.fixture
def real_client(tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "config.json")
    key = generate_api_key()
    storage.save_global_config({"auth_enabled": True, "api_key": key})
    return TestClient(UrlBaseMiddleware(main.app, "/ombisub")), key


@pytest.mark.parametrize("path", [
    "/api/settings", "/ombisub/api/settings", "/ombisub/ombisub/api/settings", "/ombisub/projects", "/ombisub/api/system/logs",
])
def test_the_prefix_does_not_bypass_the_api_key_gate(real_client, path):
    client, key = real_client
    resp = client.get(path, headers=AXIOS)
    # Either refused, or not the API at all (an unknown path falls through to the app's index.html).
    assert resp.status_code in (401, 404) or resp.headers["content-type"].startswith("text/html")
    assert key not in resp.text and "auth_enabled" not in resp.text


def test_the_api_works_under_the_prefix_with_the_key(real_client):
    client, key = real_client
    resp = client.get("/ombisub/api/system/logs/list", headers={"X-Api-Key": key})
    assert resp.status_code == 200
    assert client.get("/ombisub/api/health").status_code == 200


def test_websocket_path_is_stripped_too(real_client):
    client, _ = real_client
    with client.websocket_connect("/ombisub/api/ws") as ws:
        ws.send_text("ping")
        assert ws.receive_text() == "pong"

"""End-to-end web flow in a headless browser: upload a fixture, watch progress, land on the
gallery, play-ready video, download links. Skips when Playwright/Chromium or fixtures are missing."""
import socket
import threading
import time

import pytest

from clipmaker.config import Settings
from clipmaker.web.app import create_app

from conftest import FIXTURES


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_server(tmp_path):
    uvicorn = pytest.importorskip("uvicorn")
    port = _free_port()
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'e2e.db').as_posix()}", data_dir=tmp_path / "data",
                        cache_dir=tmp_path / "cache", host="127.0.0.1", port=port, worker=True)
    app = create_app(settings)
    app.state.worker.poll = 0.2
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


def test_upload_to_gallery_in_browser(live_server, manifest):
    sync_api = pytest.importorskip("playwright.sync_api")
    video = FIXTURES / "portrait_short.mp4"
    if not video.is_file():
        pytest.skip("portrait_short fixture missing")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser binary not installed
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page()
        page.goto(live_server + "/")
        page.set_input_files("#file", str(video))
        page.click("#go")
        page.wait_for_url("**/projects/*", timeout=240_000)
        page.wait_for_selector("article.card video", timeout=10_000)
        assert "best first" in page.inner_text("main")
        src = page.get_attribute("article.card video", "src")
        ready = page.eval_on_selector(
            "article.card video",
            "v => new Promise(r => { if (v.readyState >= 1) r(v.duration); else v.onloadedmetadata = () => r(v.duration); })")
        assert src.endswith("clip_01.mp4") and ready > 5
        assert page.locator("a", has_text="Download all (zip)").count() == 1
        page.goto(live_server + "/")
        assert page.locator("#recent a", has_text="portrait_short").count() == 1
        browser.close()


def test_paste_link_to_gallery_in_browser(live_server, manifest, monkeypatch, tmp_path):
    import functools
    import http.server

    sync_api = pytest.importorskip("playwright.sync_api")
    video = FIXTURES / "portrait_short.mp4"
    if not video.is_file():
        pytest.skip("portrait_short fixture missing")
    monkeypatch.setenv("CLIPMAKER_ALLOW_PRIVATE_URLS", "1")  # the fake media host is on localhost
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES))
    handler.log_message = lambda *a: None
    media = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=media.serve_forever, daemon=True).start()
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium unavailable: {exc}")
            page = browser.new_page()
            page.goto(live_server + "/")
            page.fill("#url", f"http://127.0.0.1:{media.server_address[1]}/portrait_short.mp4")
            page.click("#go-link")
            page.wait_for_url("**/projects/*", timeout=240_000)
            page.wait_for_selector("article.card video", timeout=10_000)
            page.goto(live_server + "/")
            assert page.locator("#recent a", has_text="portrait_short").count() == 1
            page.fill("#url", "https://www.youtube.com/watch?v=x")
            page.click("#go-link")
            page.wait_for_function("document.getElementById('error').textContent.includes('YouTube')")
            page.screenshot(path=str(tmp_path.parent / "home_link_error.png"))
            browser.close()
    finally:
        media.shutdown()

"""The test HTTP server answers byte ranges like a real media host."""
import urllib.request

from rangeserver import serve


def _get(url, rng=None):
    req = urllib.request.Request(url, headers={"Range": rng} if rng else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, b""


def test_ranges_and_byte_count(tmp_path):
    (tmp_path / "f.bin").write_bytes(bytes(range(256)) * 40)  # 10240 bytes
    with serve(tmp_path) as (url, stats):
        status, h, body = _get(f"{url}/f.bin", "bytes=10-19")
        assert status == 206 and body == bytes(range(10, 20))
        assert h["Content-Range"] == "bytes 10-19/10240"
        status, _, body = _get(f"{url}/f.bin", "bytes=-4")
        assert status == 206 and body == bytes(range(252, 256))
        status, _, body = _get(f"{url}/f.bin", "bytes=10200-")
        assert status == 206 and len(body) == 40
        assert stats["bytes"] == 10 + 4 + 40 and stats["requests"] == 3
        status, h, body = _get(f"{url}/f.bin")
        assert status == 200 and len(body) == 10240 and h["Accept-Ranges"] == "bytes"
        assert _get(f"{url}/f.bin", "bytes=99999-")[0] == 416

"""A local static HTTP server that honours `Range: bytes=a-b`, like real media hosts do.

Python's SimpleHTTPRequestHandler ignores Range and always sends the whole file, so ffmpeg
has to read through everything to reach an MP4's `moov` box at the end of the file. Real
sites (YouTube, CDNs) answer 206. The server also counts the bytes it sends, so tests can
check that only part of a file was fetched.
"""
import functools
import http.server
import os
import re
import threading
from contextlib import contextmanager

_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    stats = None  # set per server: {"bytes": int, "requests": int}

    def log_message(self, *a):
        pass

    def send_head(self):
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().send_head()
        if self.stats is not None:
            self.stats["ranges"].append(rng.strip())
        m = _RANGE.match(rng.strip())
        size = os.path.getsize(path)
        if not m or (not m.group(1) and not m.group(2)):
            return super().send_head()
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        else:  # suffix range: the last N bytes
            start, end = max(0, size - int(m.group(2))), size - 1
        end = min(end, size - 1)
        if start >= size or start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_remaining", None)
        self._remaining = None
        try:
            while remaining is None or remaining > 0:
                chunk = source.read(64 * 1024 if remaining is None else min(64 * 1024, remaining))
                if not chunk:
                    break
                outputfile.write(chunk)
                if self.stats is not None:
                    self.stats["bytes"] += len(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # clients (ffmpeg) hang up once they have what they need

    def do_GET(self):
        if self.stats is not None:
            self.stats["requests"] += 1
            self.stats["paths"].append(self.path)
        super().do_GET()


@contextmanager
def serve(directory):
    """Serve `directory` on 127.0.0.1; yields (base_url, stats)."""
    stats = {"bytes": 0, "requests": 0, "ranges": [], "paths": []}
    handler_cls = type("Handler", (RangeHandler,), {"stats": stats})
    handler = functools.partial(handler_cls, directory=str(directory))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", stats
    finally:
        srv.shutdown()
        srv.server_close()

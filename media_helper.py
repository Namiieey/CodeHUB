"""
CodeHUB media helper (Windows 10/11)
Reads whatever is playing in the Windows media panel (Spotify, YouTube Music in
Chrome/Edge, VLC, Groove, etc.) and lets CodeHUB show and control it.

Setup:   pip install winsdk
Run:     python media_helper.py
Then in CodeHUB click the 💻 button in the player bar.

Listens on 127.0.0.1:5959 only. Answers pages opened from your own PC
(file:// or http://127.0.0.1 / http://localhost). To use it from a hosted site, e.g. Vercel:
    python media_helper.py --allow https://your-app.vercel.app
"""
import argparse, asyncio, base64, json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as Manager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
)
from winsdk.windows.storage.streams import DataReader

ap = argparse.ArgumentParser(description="CodeHUB media helper")
ap.add_argument("--allow", action="append", default=[], metavar="ORIGIN",
                help="extra website allowed to use this helper, e.g. https://codehub.vercel.app (repeatable)")
EXTRA = [o.rstrip("/") for o in ap.parse_args().allow]
PORT = 5959
_denied = set()
loop = asyncio.new_event_loop()
_thumb_cache = {"key": None, "data": None}


async def current_session():
    mgr = await Manager.request_async()
    return mgr.get_current_session()


async def read_thumb(ref):
    if ref is None:
        return None
    stream = await ref.open_read_async()
    size = stream.size
    if not size or size > 3_000_000:
        return None
    reader = DataReader(stream)
    await reader.load_async(size)
    buf = bytearray(size)
    reader.read_bytes(buf)
    mime = stream.content_type or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(bytes(buf)).decode()


async def now():
    s = await current_session()
    if s is None:
        return {"active": False}
    props = await s.try_get_media_properties_async()
    info = s.get_playback_info()
    tl = s.get_timeline_properties()
    key = (props.title, props.artist, props.album_title)
    if _thumb_cache["key"] != key:
        try:
            _thumb_cache.update(key=key, data=await read_thumb(props.thumbnail))
        except Exception:
            _thumb_cache.update(key=key, data=None)
    return {
        "active": True,
        "title": props.title or "",
        "artist": props.artist or "",
        "album": props.album_title or "",
        "app": s.source_app_user_model_id or "",
        "playing": info.playback_status == Status.PLAYING,
        "pos": tl.position.total_seconds(),
        "dur": tl.end_time.total_seconds(),
        "thumb": _thumb_cache["data"],
    }


async def act(name):
    s = await current_session()
    if s is None:
        return False
    fn = {
        "toggle": s.try_toggle_play_pause_async,
        "play": s.try_play_async,
        "pause": s.try_pause_async,
        "next": s.try_skip_next_async,
        "prev": s.try_skip_previous_async,
    }.get(name)
    if fn is None:
        return False
    return bool(await fn())


def allowed(origin):
    ok = origin in (None, "", "null") or origin.startswith(("http://127.0.0.1", "http://localhost")) or origin.rstrip("/") in EXTRA
    if not ok and origin not in _denied:
        _denied.add(origin)
        print(f"Blocked request from {origin}\n  -> to allow it, restart with:  python media_helper.py --allow {origin}")
    return ok


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=None):
        origin = self.headers.get("Origin")
        self.send_response(code)
        if origin and allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if body is not None:
            self.wfile.write(json.dumps(body).encode())

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(8)

    def do_OPTIONS(self):
        self._send(204 if allowed(self.headers.get("Origin")) else 403)

    def do_GET(self):
        if not allowed(self.headers.get("Origin")):
            return self._send(403, {"error": "origin not allowed"})
        if self.path.split("?")[0] == "/now":
            try:
                return self._send(200, self._run(now()))
            except Exception as e:
                return self._send(500, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not allowed(self.headers.get("Origin")):
            return self._send(403, {"error": "origin not allowed"})
        try:
            ok = self._run(act(self.path.strip("/")))
            self._send(200, {"ok": ok})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threading.Thread(target=loop.run_forever, daemon=True).start()
    print(f"CodeHUB media helper running on http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        pass

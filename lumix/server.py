"""lumix-server: local HTTP service that owns the camera and serves live view + settings (for the Swift app).

  python -m lumix.server [--port 8765] [--size 1280x960] [--host 127.0.0.1] [--token SECRET]

GET  /m             phone UI (live view, touch AF, shutter, settings, recipes, pre-shot checklist); add it to the
                    home screen. Reach it from a phone with --host 0.0.0.0 (or a Tailscale address).

GET  /stream        MJPEG (multipart/x-mixed-replace), pushes every new frame immediately, never buffers
GET  /stream?raw=1  same bytes as application/octet-stream (for native clients that parse the parts themselves;
                    URLSession splits multipart itself and hands a part over only when the next starts = +1 frame)
GET  /frame         latest JPEG (204 if there is none yet)
GET  /state         JSON: phase (connecting|live|error), status, error, camera (manufacturer/model/firmware), fps,
                    timings, frame_age_ms, hud (battery, shots, mode, shutter, level, histogram), options (labels the camera offers), current values (labels)
POST /set           JSON {"key": ..., "label": ...}   (labels as listed in /state options)
POST /af, /shutter, /undo, /size {"size": "1280x960"}
POST /touch        {"x": 0..1000, "y": 0..1000, "af": true}  move the AF area (touch AF), then AF once
POST /mfassist     {"action": start|move|end, "x": 0..1000, "y": 0..1000, "mode": 1}  magnified MF assist
POST /focus        {"step": near_fast|near_slow|far_slow|far_fast|stop, "ms": 120}  MF drive pulse (focus mode MF)
Commands wait for the camera (up to 8 s) and answer {"ok": true, "message"} or {"ok": false, "error"} (4xx/5xx).
Binds to 127.0.0.1 by default. With another --host every request needs the token (header X-Lumix-Token, ?t=, or
the cookie /m sets); one is generated and printed if none is given. Exit code 2 if the port is taken. SIGTERM stops live view cleanly before exiting.
"""
import argparse
import errno
import hmac
import json
import os
import secrets
import queue
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import liveview, settings
from .ptp import Camera

SIZES = ('640x480', '960x720', '1280x960')
CMD_TIMEOUT = 8.0
LINK_ERRORS = ('no answer', 'no such device', 'entity not found', 'errno 19', 'pipe error')


def is_link_error(e):
    return any(s in str(e).lower() for s in LINK_ERRORS)


def humanize(e):
    """Exception -> one line a user can act on."""
    s = str(e) or e.__class__.__name__
    low = s.lower()
    if 'no lumix camera' in low:
        return 'No camera found. Switch the camera on, set USB mode to PC (Tether) and plug it in.'
    if 'could not claim' in low or 'access denied' in low or 'resource busy' in low:
        return 'Another program is using the camera (lumix-mcp, lumix-monitor, Lightroom, Image Capture?). Close it.'
    if 'recovery failed' in low:
        return 'Camera stopped answering and could not be recovered. Unplug and replug the USB cable.'
    if 'no such device' in low or 'entity not found' in low or 'errno 19' in low or 'pipe error' in low:
        return 'Camera disconnected (USB). Plug it back in or switch it on.'
    if 'live view start refused' in low or 'live view frame refused' in low:
        return f'Camera refuses live view (playback or a menu open?). Half-press the shutter on the camera. [{s}]'
    if 'stayed busy' in low:
        return 'Camera stays busy (menu open or writing to the card?).'
    if 'no answer' in low:
        return f'Camera did not answer; link recovered, reconnecting. [{s}]'
    if 'did not accept' in low:
        return s.replace('rc 0x2019', 'camera busy').replace('rc 0x201d', 'not allowed in this mode')
    return s[:200]


class Command:
    def __init__(self, what):
        self.what, self.done, self.ok, self.message = what, threading.Event(), False, ''


class CameraService:
    def __init__(self, size):
        self.size = size
        self.cond = threading.Condition()
        self.jpg, self.seq, self.t_frame = None, 0, 0.0
        self.cmds = queue.Queue()
        self.opts, self.current, self.original = {}, {}, {}
        self.phase, self.status, self.error = 'connecting', 'connecting to camera…', None
        self.fps, self.t_fetch = 0.0, 0.0
        self.camera = None  # {'manufacturer', 'model', 'firmware'} of the connected camera
        self.hud, self.header, self.af = {}, {}, None  # camera-screen values; live view header (histogram, level)
        self.mf_stop_at = None  # time when a focus drive pulse ends
        self.running = True
        self.stopped = threading.Event()
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        delay = 1.0
        while self.running:
            try:
                self.phase, self.status = 'connecting', 'connecting to camera…'
                with Camera() as cam:
                    try:
                        info = cam.device_info()
                        self.camera = {k: info[k] for k in ('manufacturer', 'model', 'firmware')}
                    except Exception:
                        self.camera = None
                    self.opts = settings.options(cam)
                    self.read_current(cam)
                    if not self.original:
                        self.original = dict(self.current)
                    try:
                        liveview.set_size(cam, *self.size)
                    except Exception as e:  # refused in some camera states: keep the camera's current size
                        if is_link_error(e):
                            raise
                        print(f'live view size not set: {e}', file=sys.stderr, flush=True)
                    liveview.start(cam)
                    self.phase, self.status, self.error, delay = 'live', 'live', None, 1.0
                    n, t_fps, t_read, t_af = 0, time.time(), time.time(), 0.0
                    try:
                        while self.running:
                            self.handle_cmds(cam)
                            if self.mf_stop_at and time.time() >= self.mf_stop_at:
                                self.mf_stop_at = None
                                liveview.mf_drive(cam, 'stop')
                            if time.time() - t_read > 2:
                                self.read_current(cam); t_read = time.time()
                            t0 = time.perf_counter()
                            jpg, hdr = liveview.frame(cam)
                            self.t_fetch = time.perf_counter() - t0
                            self.header = liveview.parse_header(hdr)
                            with self.cond:  # publish the frame (stream clients wait on this)
                                self.jpg, self.seq, self.t_frame = jpg, self.seq + 1, time.time()
                                self.cond.notify_all()
                            n += 1
                            if time.time() - t_fps >= 1:
                                self.fps, n, t_fps = n / (time.time() - t_fps), 0, time.time()
                    finally:
                        liveview.stop(cam)
            except Exception as e:
                self.phase, self.error, self.fps = 'error', humanize(e), 0.0
                self.status = self.error
                self.fail_pending(self.error)
                time.sleep(delay)
                delay = min(delay * 1.5, 5.0)  # retry forever, gently
        self.stopped.set()

    def read_current(self, cam):
        try:
            self.hud = settings.hud(cam)
        except Exception as e:
            if is_link_error(e):
                raise
        cur = {}
        for k in self.opts:
            try:
                cur[k] = settings.read(cam, k)
            except Exception as e:
                if is_link_error(e):
                    raise
                cur[k] = self.current.get(k)
        self.current = cur

    def fail_pending(self, msg):
        while not self.cmds.empty():
            c = self.cmds.get()
            c.ok, c.message = False, f'Camera not ready: {msg}'
            c.done.set()

    def run_cmd(self, cam, c):
        w = c.what
        if w == 'af':
            rc = liveview.autofocus(cam)
            return rc == 0x2001, 'focused' if rc == 0x2001 else f'AF failed (0x{rc:04x}; nothing to focus on, or MF set?)'
        if w == 'shutter':
            rc = liveview.shutter(cam)
            return rc == 0x2001, 'shot taken' if rc == 0x2001 else f'shutter refused (0x{rc:04x}; card full or missing?)'
        if w == 'undo':
            failed = []
            for k, v in self.original.items():
                try:
                    if v is not None and settings.read(cam, k) != v:
                        settings.write(cam, k, v)
                except Exception as e:
                    if is_link_error(e):
                        raise
                    failed.append(k)
            return not failed, 'all settings restored' if not failed else 'could not restore: ' + ', '.join(failed)
        if w[0] == 'focus':
            step, ms = w[1], w[2]
            rc = liveview.mf_drive(cam, step)
            if rc == 0x201D:
                return False, 'Focus drive needs focus mode MF (set it in the HUD or on the camera).'
            if rc != 0x2001:
                return False, f'focus drive refused (0x{rc:04x})'
            self.mf_stop_at = time.time() + ms / 1000 if step != 'stop' else None
            return True, f'focus {step.replace("_", " ")}'
        if w[0] == 'touch':
            rc = liveview.touch_af(cam, w[1], w[2])
            if rc != 0x2001:
                return False, f'AF area not moved (0x{rc:04x}; AF area mode doesn\'t allow it, or MF?)'
            if w[3]:
                rc = liveview.autofocus(cam)
                return rc == 0x2001, 'focus here' if rc == 0x2001 else f'AF area moved, AF failed (0x{rc:04x})'
            return True, 'AF area moved'
        if w[0] == 'mfassist':
            rc = liveview.mf_assist(cam, w[1], w[2], w[3], w[4])
            return rc == 0x2001, f'MF assist {w[1]}' if rc == 0x2001 else f'MF assist {w[1]} refused (0x{rc:04x})'
        if w[0] == 'set':
            settings.write(cam, w[1], w[2])
            return True, f'{w[1].replace("_", " ")} = {settings.label_of(self.opts, w[1], w[2])}'
        if w[0] == 'size':
            liveview.stop(cam)
            try:
                liveview.set_size(cam, *w[1])
                self.size = w[1]
            finally:
                liveview.start(cam)
            return True, f'live view {w[1][0]}x{w[1][1]}'
        return False, f'unknown command {w}'

    def handle_cmds(self, cam):
        changed = False
        while not self.cmds.empty():
            c = self.cmds.get()
            try:
                c.ok, c.message = self.run_cmd(cam, c)
            except Exception as e:
                c.ok, c.message = False, humanize(e)
                if is_link_error(e):
                    raise  # link problem: reconnect (finally still answers the caller)
            finally:
                c.done.set()
                self.status = c.message
            changed = True
        if changed:
            self.read_current(cam)

    def submit(self, what):
        if self.phase != 'live':
            return False, f'Camera not ready: {self.error or self.status}'
        c = Command(what)
        self.cmds.put(c)
        if not c.done.wait(CMD_TIMEOUT):
            return False, 'Camera did not respond in time (busy, or a menu open?).'
        return c.ok, c.message

    def wait_frame(self, after_seq, timeout=2.0):
        with self.cond:
            self.cond.wait_for(lambda: self.seq != after_seq or not self.running, timeout=timeout)
            return self.jpg, self.seq

    def state(self):
        live = self.phase == 'live'
        return {'phase': self.phase, 'status': self.status, 'error': self.error, 'camera': self.camera,
                'fps': round(self.fps, 1) if live else 0.0, 'fetch_ms': round(self.t_fetch * 1000, 1),
                'frame_age_ms': round((time.time() - self.t_frame) * 1000) if self.t_frame else None,
                'size': f'{self.size[0]}x{self.size[1]}',
                'hud': {**self.hud, **({k: self.header[k] for k in ('level', 'histogram') if k in self.header} if live else {}),
},
                'options': {k: [l for l, _ in v] for k, v in self.opts.items()},
                'current': {k: settings.label_of(self.opts, k, v) for k, v in self.current.items() if v is not None}}

    def shutdown(self, timeout=2.0):
        self.running = False
        with self.cond:
            self.cond.notify_all()
        self.stopped.wait(timeout)


MOBILE_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mobile.html')
MANIFEST = {'name': 'LUMIX Remote', 'short_name': 'LUMIX', 'start_url': '/m', 'display': 'standalone',
            'background_color': '#000000', 'theme_color': '#000000', 'icons': []}


def make_handler(svc, token=None):
    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *a):
            pass

        def send(self, code, body, ctype='application/json'):
            if isinstance(body, dict):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def stream(self, raw):
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream' if raw else 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            seq = -1
            try:
                while svc.running:
                    jpg, new = svc.wait_frame(seq)
                    if not jpg or new == seq:
                        continue
                    seq = new
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n' % len(jpg))
                    self.wfile.write(jpg + b'\r\n')
                    self.wfile.flush()
            except OSError:  # client went away
                pass

        def authorized(self, query):
            """No token configured (loopback only) -> open. Otherwise header, ?t= or cookie must match."""
            if not token:
                return True
            got = self.headers.get('X-Lumix-Token') or dict(
                kv.split('=', 1) for kv in query.split('&') if '=' in kv).get('t')
            if not got:
                for part in (self.headers.get('Cookie') or '').split(';'):
                    k, _, v = part.strip().partition('=')
                    if k == 'lumix_t':
                        got = v
            return bool(got) and hmac.compare_digest(got, token)

        def do_GET(self):
            path, _, query = self.path.partition('?')
            if not self.authorized(query):
                return self.send(401, {'ok': False, 'error': 'token required (open /m?t=TOKEN once on this device)'})
            try:
                if path in ('/m', '/m/'):
                    body = open(MOBILE_HTML, 'rb').read()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Cache-Control', 'no-store')
                    if token:
                        self.send_header('Set-Cookie', f'lumix_t={token}; Path=/; Max-Age=31536000; SameSite=Strict')
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == '/manifest.webmanifest':
                    return self.send(200, MANIFEST, 'application/manifest+json')
                if path == '/state':
                    return self.send(200, svc.state())
                if path == '/frame':
                    jpg, _ = svc.wait_frame(-1, 0)
                    return self.send(200, jpg, 'image/jpeg') if jpg else self.send(204, b'')
                if path == '/stream':
                    return self.stream('raw=1' in query.split('&'))
                self.send(404, {'ok': False, 'error': f'unknown path {self.path}'})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                self.send(500, {'ok': False, 'error': humanize(e)})

        def do_POST(self):
            if not self.authorized(self.path.partition('?')[2]):
                return self.send(401, {'ok': False, 'error': 'token required'})
            self.path = self.path.partition('?')[0]
            try:
                n = int(self.headers.get('Content-Length') or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b'{}') if n else {}
                    if not isinstance(body, dict):
                        raise ValueError
                except Exception:
                    return self.send(400, {'ok': False, 'error': 'request body is not a JSON object'})
                if self.path in ('/af', '/shutter', '/undo'):
                    ok, msg = svc.submit(self.path[1:])
                elif self.path == '/set':
                    key, label = body.get('key'), body.get('label')
                    if key not in svc.opts:
                        return self.send(400, {'ok': False, 'error': f'unknown setting {key!r}'})
                    value = next((v for l, v in svc.opts[key] if l == label), None)
                    if value is None:
                        return self.send(400, {'ok': False, 'error': f'{key}: camera does not offer {label!r} now'})
                    ok, msg = svc.submit(('set', key, value))
                elif self.path == '/focus':
                    step = body.get('step', 'stop')
                    if step not in liveview.MF_STEPS:
                        return self.send(400, {'ok': False, 'error': f'step must be one of {", ".join(liveview.MF_STEPS)}'})
                    try:
                        ms = max(20, min(int(body.get('ms', 120)), 3000))
                    except (TypeError, ValueError):
                        return self.send(400, {'ok': False, 'error': 'ms must be a number'})
                    ok, msg = svc.submit(('focus', step, ms))
                elif self.path == '/touch':
                    try:
                        x, y = float(body['x']), float(body['y'])
                    except (KeyError, TypeError, ValueError):
                        return self.send(400, {'ok': False, 'error': 'x and y (0..1000) are required'})
                    if not (0 <= x <= 1000 and 0 <= y <= 1000):
                        return self.send(400, {'ok': False, 'error': 'x and y must be within 0..1000'})
                    ok, msg = svc.submit(('touch', x, y, bool(body.get('af', True))))
                elif self.path == '/mfassist':
                    action = body.get('action')
                    if action not in ('start', 'move', 'end'):
                        return self.send(400, {'ok': False, 'error': 'action must be start, move or end'})
                    try:
                        x, y, mode = float(body.get('x', 500)), float(body.get('y', 500)), int(body.get('mode', 1))
                    except (TypeError, ValueError):
                        return self.send(400, {'ok': False, 'error': 'x, y (0..1000) and mode must be numbers'})
                    ok, msg = svc.submit(('mfassist', action, x, y, mode))
                elif self.path == '/size':
                    size = body.get('size', '1280x960')
                    if size not in SIZES:
                        return self.send(400, {'ok': False, 'error': f'size must be one of {", ".join(SIZES)}'})
                    ok, msg = svc.submit(('size', tuple(int(x) for x in size.split('x'))))
                else:
                    return self.send(404, {'ok': False, 'error': f'unknown path {self.path}'})
                self.send(200 if ok else 409, {'ok': ok, 'message' if ok else 'error': msg})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                self.send(500, {'ok': False, 'error': humanize(e)})
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--size', default='1280x960', choices=SIZES)
    ap.add_argument('--host', default='127.0.0.1', help='0.0.0.0 or a Tailscale address to reach /m from a phone')
    ap.add_argument('--token', default=os.environ.get('LUMIX_TOKEN'), help='required when --host is not loopback')
    a = ap.parse_args()
    token = a.token
    if a.host not in ('127.0.0.1', 'localhost', '::1') and not token:
        token = secrets.token_urlsafe(12)
    try:
        srv = ThreadingHTTPServer((a.host, a.port), BaseHTTPRequestHandler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            print(f'lumix-server: port {a.port} is already in use (another lumix-server running?)', file=sys.stderr, flush=True)
            sys.exit(2)
        raise
    svc = CameraService(tuple(int(x) for x in a.size.split('x')))
    srv.RequestHandlerClass = make_handler(svc, token)
    srv.daemon_threads = True

    def on_term(*_):  # the app stops us with SIGTERM: stop live view on the camera before exiting
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    print(f'lumix-server on http://{a.host}:{a.port}', flush=True)
    if token:
        print(f'phone UI: http://<this-mac>:{a.port}/m?t={token}', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        svc.shutdown()
        print('lumix-server stopped', flush=True)


if __name__ == '__main__':
    main()

"""Offline test of lumix-server's camera loop with a fake camera: frames must be published to stream clients,
commands must answer, and the HTTP API must validate input. Run: python tests/test_server.py"""
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from lumix import liveview, server, settings  # noqa: E402

JPG = b'\xff\xd8' + b'x' * 1000 + b'\xff\xd9'


class FakeCam:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def device_info(self): return {'manufacturer': 'Panasonic', 'model': 'DC-TEST', 'firmware': '1.0', 'serial': 'x'}


def fake_frame(cam, tries=25):
    time.sleep(0.01)
    return JPG, b''


server.Camera = FakeCam
liveview.set_size = liveview.start = liveview.stop = lambda *a, **k: None
liveview.frame = fake_frame
liveview.autofocus = lambda cam: 0x2001
settings.options = lambda cam: {'iso': [('auto', 0xFFFFFFFF), ('800', 800)]}
settings.read = lambda cam, k: 0xFFFFFFFF
settings.write = lambda cam, k, v: (0xFFFFFFFF, v)
settings.hud = lambda cam: {'battery': 50}

svc = server.CameraService((1280, 960))
srv = ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(svc))
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{srv.server_address[1]}'


def post(path, body):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


for _ in range(100):
    if svc.phase == 'live' and svc.seq > 3:
        break
    time.sleep(0.05)
assert svc.phase == 'live', svc.status
jpg, seq = svc.wait_frame(-1, 1)
assert jpg == JPG and seq > 0, 'frames are not published'
with urllib.request.urlopen(base + '/frame', timeout=5) as r:
    assert r.status == 200 and r.read() == JPG
with urllib.request.urlopen(base + '/stream?raw=1', timeout=5) as r:
    assert r.headers['Content-Type'] == 'application/octet-stream'
    head = r.read(200)
    assert head.startswith(b'--frame\r\n') and b'Content-Length: %d' % len(JPG) in head
st = json.load(urllib.request.urlopen(base + '/state', timeout=5))
assert st['camera']['model'] == 'DC-TEST' and st['hud']['battery'] == 50 and st['frame_age_ms'] is not None
assert post('/set', {'key': 'iso', 'label': '800'}) == (200, {'ok': True, 'message': 'iso = 800'})
assert post('/set', {'key': 'iso', 'label': '7'})[0] == 400
assert post('/touch', {'x': 2000, 'y': 1})[0] == 400
assert post('/focus', {'step': 'up'})[0] == 400
assert post('/af', {}) == (200, {'ok': True, 'message': 'focused'})
with urllib.request.urlopen(base + '/m', timeout=5) as r:
    page = r.read()
    assert r.headers['Content-Type'].startswith('text/html') and b'LUMIX Remote' in page and b'/stream' in page
assert json.load(urllib.request.urlopen(base + '/manifest.webmanifest', timeout=5))['start_url'] == '/m'

# with a token (phone access): everything needs it; header, ?t= and the cookie /m sets all work
tsrv = ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(svc, 'sekret'))
threading.Thread(target=tsrv.serve_forever, daemon=True).start()
tbase = f'http://127.0.0.1:{tsrv.server_address[1]}'
for path in ('/state', '/m', '/frame'):
    try:
        urllib.request.urlopen(tbase + path, timeout=5); raise SystemExit(f'{path} open without token')
    except urllib.error.HTTPError as e:
        assert e.code == 401, (path, e.code)
try:
    urllib.request.urlopen(urllib.request.Request(tbase + '/af', b'{}', {'X-Lumix-Token': 'wrong'}), timeout=5)
    raise SystemExit('POST accepted a wrong token')
except urllib.error.HTTPError as e:
    assert e.code == 401
with urllib.request.urlopen(tbase + '/m?t=sekret', timeout=5) as r:
    cookie = r.headers['Set-Cookie']
    assert r.status == 200 and cookie.startswith('lumix_t=sekret')
assert json.load(urllib.request.urlopen(urllib.request.Request(tbase + '/state', headers={'X-Lumix-Token': 'sekret'}), timeout=5))['phase'] == 'live'
assert json.load(urllib.request.urlopen(urllib.request.Request(tbase + '/state', headers={'Cookie': 'lumix_t=sekret'}), timeout=5))['phase'] == 'live'
r = urllib.request.urlopen(urllib.request.Request(tbase + '/af?t=sekret', b'{}', {'Content-Type': 'application/json'}), timeout=5)
assert json.load(r)['ok'] is True
tsrv.shutdown()
svc.shutdown()
print('ok: server tests passed')

#!/usr/bin/env python3
"""Offline tests for control/mps_write_test.py: restore always runs, dark scenes abort before writes, verdict logic."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'control'))
import mps_write_test as t  # noqa: E402
from lumix.ptp import CameraError  # noqa: E402


class Fake:
    """Camera state as a dict of properties; the active look decides the frame stats."""

    def __init__(self, luma=100, lut_works=True, fail_on=None):
        self.p = {t.STYLE_R: 49, t.SELECT: 49, t.REGISTRATION: 0, t.NAMES: 'aa',
                  t.DISPLAY_R: ''.join(f'{c:02x}00{1 if c < 23 else 0:02x}00' for c in range(19, 29))}
        self.slot = {}  # code -> {prop: value}
        self.editing = False
        self.luma, self.lut_works, self.fail_on = luma, lut_works, fail_on
        self.writes = []

    def get(self, cam, prop):
        if prop in t.SLOT_PROPS.values():
            code = self.p[t.SELECT] if self.editing else self.p[t.STYLE_R]
            return self.slot.get(code, {}).get(prop, 0 if 19 <= code <= 28 else 65535)
        return self.p.get(prop)

    def put(self, cam, prop, value, size):
        if prop == self.fail_on:
            raise CameraError('injected failure')
        self.writes.append((prop, value))
        if prop == t.STYLE_W:
            self.p[t.STYLE_R] = value
        elif prop == t.DISPLAY_W:
            code, shown = value & 0xFFFF, value >> 16
            pr = t.pairs(self.p[t.DISPLAY_R])
            self.p[t.DISPLAY_R] = ''.join(f'{c:02x}00{(shown if c == code else s):02x}00' for c, s in pr)
        elif prop in t.SLOT_PROPS.values():
            self.slot.setdefault(self.p[t.SELECT], {})[prop] = value
        else:
            self.p[prop] = value
        return t.OK

    def allowed(self, cam, prop, size):
        return [0, 18, 49] + [c for c, s in t.pairs(self.p[t.DISPLAY_R]) if s]

    def ctrl(self, cam, code, u16=None):
        if code == t.START_EDIT:
            self.editing = True
        elif code == t.END_EDIT:
            self.editing = False
        elif code == t.RESET:
            self.slot.pop(u16, None)
        return t.OK

    def grab(self, cam, outdir, name, settle=10):
        style = self.p[t.STYLE_R]
        s = self.slot.get(style, {})
        look = s.get(t.SLOT_PROPS['lut1_registration'], 0) if (self.lut_works and s.get(t.SLOT_PROPS['lut1_on'])) else 0
        if getattr(self, 'flat', False):
            return {'luma': self.luma, 'contrast': 0.1, 'saturation': 198, 'warmth': 12}
        return {'luma': self.luma, 'contrast': 20 + look, 'saturation': 30 + look, 'warmth': look % 7}


class Sluggish(Fake):
    """Refuses (0x201D) the first `n` style/select/display writes after a style change, like the S9 did."""

    def __init__(self, n=3, **kw):
        super().__init__(**kw)
        self.n, self.cool = n, 0

    def put(self, cam, prop, value, size):
        if prop in (t.STYLE_W, t.SELECT, t.DISPLAY_W) and self.cool > 0:
            self.cool -= 1
            return 0x201D
        rc = super().put(cam, prop, value, size)
        if prop == t.STYLE_W:
            self.cool = self.n
        return rc


class StuckSelect(Fake):
    """The edit session stays on slot 1 (code 19): selecting another slot is refused."""

    def put(self, cam, prop, value, size):
        if prop == t.SELECT and self.editing and value != 19:
            self.p[t.SELECT] = 19
            return 0x201D
        return super().put(cam, prop, value, size)


def install(fake):
    t.time.sleep = lambda s: None
    t.settings.get, t.settings.put, t.settings.allowed = fake.get, fake.put, fake.allowed
    t.ctrl, t.grab = fake.ctrl, fake.grab


def recorded_state(fake):
    return {k: fake.p[k] for k in (t.STYLE_R, t.SELECT, t.REGISTRATION, t.DISPLAY_R, t.NAMES)}


def test_pass_and_restore():
    f = Fake()
    install(f)
    before = recorded_state(f)
    r = t.run(None, tempfile.mkdtemp(), 10, 40, 29, [])
    assert r['lut_applies'] and r['restored'], r
    assert recorded_state(f) == before
    assert 28 not in f.allowed(None, t.STYLE_W, 2)  # slot hidden again


def test_registration_ignored_is_no():
    f = Fake(lut_works=False)
    install(f)
    r = t.run(None, tempfile.mkdtemp(), 10, 40, 29, [])
    assert not r['lut_applies'] and r['restored'], r


def test_failure_mid_run_still_restores():
    f = Fake(fail_on=t.SLOT_PROPS['lut1_strength'])
    install(f)
    before = recorded_state(f)
    try:
        t.run(None, tempfile.mkdtemp(), 10, 40, 29, [])
    except CameraError:
        pass
    else:
        raise AssertionError('expected the injected failure')
    assert recorded_state(f) == before


def test_transient_refusals_are_retried():
    f = Sluggish(n=3)
    install(f)
    before = recorded_state(f)
    r = t.run(None, tempfile.mkdtemp(), 10, 40, 29, [])
    assert r['lut_applies'] and r['restored'], r
    assert recorded_state(f) == before


def test_never_writes_a_slot_it_did_not_select():
    f = StuckSelect()
    install(f)
    before = recorded_state(f)
    try:
        t.run(None, tempfile.mkdtemp(), 10, 40, 29, [])
    except CameraError as e:
        assert 'slot' in str(e)
    else:
        raise AssertionError('expected a refusal to edit')
    assert 19 not in f.slot, f.slot  # STREETWISE MINE untouched
    assert recorded_state(f) == before


def test_flat_frame_aborts_before_writes():
    f = Fake(luma=16)
    f.flat = True
    install(f)
    try:
        t.run(None, tempfile.mkdtemp(), 10, 40, 29, [])
    except CameraError as e:
        assert 'lens cap' in str(e)
    assert f.writes == []


def test_dark_scene_aborts_before_writes():
    f = Fake(luma=2)
    install(f)
    try:
        t.run(None, tempfile.mkdtemp(), 10, 40, 29, [])
    except CameraError as e:
        assert 'black' in str(e)
    assert f.writes == []


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('ok:', name)
    print('ok: mps write tests passed')

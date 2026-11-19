#!/usr/bin/env python3
"""s9check — a READ-ONLY settings checker for the Panasonic LUMIX S9.

It compares what the camera reports over Wi-Fi with s9_spec.yaml (your source
of truth) and flags drift. It never changes a setting: every cam.cgi request
goes through build_url(), which refuses anything outside READ_ONLY_MODES.

Status: EXPERIMENTAL. Panasonic publishes no Wi-Fi spec. This speaks the
reverse-engineered cam.cgi protocol used by LUMIX Sync (documented by the
open-source lumix-remote project, verified on the original S5, untested on
the S9). Start with `identify` (no pairing), then `probe`, then `check`.

Commands
  identify            Read model + firmware (no pairing, nothing on camera changes)
  probe  --out DIR    Pair, then dump getstate/getinfo XML so we learn what the S9 exposes
  check  --bank C2    Pair, read each spec'd setting, print PASS/FAIL/OBSERVED table
         [--save snap.json] [--golden snap.json]
  plan   --bank C2    Print the exact URLs `check` would call (no network)

Exit codes: 0 ok · 1 FAIL or drift found · 2 couldn't connect/pair
Requires: Python 3.9+, PyYAML (pip3 install pyyaml)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

DEFAULT_IP = "192.168.54.1"          # camera's own access point
CLIENT_FILE = os.path.expanduser("~/.s9check.json")
CLIENT_NAME = "s9check"

READ_ONLY_MODES = {"getstate", "getinfo", "getsetting", "accctrl"}   # accctrl = pairing request only
KNOWN_READ_TYPES = {"iso", "focal", "shtrspeed", "exposure", "focusmode",
                    "afmode", "peaking", "lightmetering"}
BANKS = ("base", "C1", "C2", "C3", "night")

F_STOPS = [1, 1.1, 1.2, 1.4, 1.6, 1.8, 2, 2.2, 2.5, 2.8, 3.2, 3.5, 4, 4.5, 5, 5.6,
           6.3, 7.1, 8, 9, 10, 11, 13, 14, 16, 18, 20, 22, 25, 29, 32]
SHUTTER_DENOMS = [8000, 6400, 5000, 4000, 3200, 2500, 2000, 1600, 1250, 1000, 800,
                  640, 500, 400, 320, 250, 200, 160, 125, 100, 80, 60, 50, 40, 30,
                  25, 20, 15, 13, 10, 8, 6, 5, 4, 3, 2]


class SafetyError(RuntimeError):
    """Raised when code tries to build a request that could change the camera."""


# ---------------------------------------------------------------- transport
def build_url(ip: str, mode: str, **params) -> str:
    if mode not in READ_ONLY_MODES:
        raise SafetyError(f"mode={mode!r} can change the camera; s9check is read-only")
    query = urllib.parse.urlencode({"mode": mode, **params})
    return f"http://{ip}/cam.cgi?{query}"


class Camera:
    def __init__(self, ip: str = DEFAULT_IP, fetch=None, timeout: float = 5.0):
        self.ip = ip
        self.session: str | None = None
        self.timeout = timeout
        self._fetch = fetch or self._http_get

    def _http_get(self, url: str, headers: dict) -> str:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8", "replace")

    def cgi(self, mode: str, **params) -> str:
        url = build_url(self.ip, mode, **params)
        headers = {"X-SESSION_ID": self.session} if self.session else {}
        return self._fetch(url, headers)

    def describe(self) -> dict:
        """UPnP device description — answers without pairing."""
        return parse_ddd(self._fetch(f"http://{self.ip}:60606/Lumix/Server0/ddd", {}))


# ---------------------------------------------------------------- parsing
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_ddd(xml_text: str) -> dict:
    wanted = {"friendlyName", "modelName", "modelNumber", "serialNumber", "UDN", "X_FirmVersion"}
    out = {}
    for el in ET.fromstring(xml_text).iter():
        name = _local(el.tag)
        if name in wanted and name not in out and el.text:
            out[name] = el.text.strip()
    return out


def parse_camrply(xml_text: str) -> tuple[str | None, dict]:
    root = ET.fromstring(xml_text)
    result = root.findtext("result")
    attrs: dict = {}
    for el in root.iter():
        if el is root or _local(el.tag) == "result":
            continue
        attrs.update(el.attrib)
        if el.text and el.text.strip() and len(el) == 0:
            attrs.setdefault(_local(el.tag), el.text.strip())
    return result, attrs


def parse_acc(text: str) -> tuple[str, list]:
    text = text.strip()
    if text.startswith("ok_under_research"):
        return "wait", []
    if text.startswith("ok,"):
        return "ok", text.split(",")[1:]
    return "err", [text]


# ---------------------------------------------------------------- decoding
def _frac(value: str) -> float:
    if "/" in value:
        num, den = value.split("/", 1)
        return float(num) / float(den)
    return float(value)


def _nearest(target: float, choices) -> float:
    return min(choices, key=lambda c: abs(math.log2(c) - math.log2(target)))


def _fmt_num(x: float) -> str:
    return f"{x:g}"


def decode(wifi_type: str, raw: str | None) -> str | None:
    """Best-effort: aperture/shutter arrive as APEX fractions (n/256)."""
    if raw is None:
        return None
    value = raw.strip().lower()
    try:
        if wifi_type == "focal" and "/" in value:
            return "f/" + _fmt_num(_nearest(2 ** (_frac(value) / 2), F_STOPS))
        if wifi_type == "shtrspeed" and "/" in value:
            seconds = 2 ** (-_frac(value))
            if seconds >= 1:
                return f"{seconds:.1f}".rstrip("0").rstrip(".") + "s"
            return f"1/{_nearest(1 / seconds, SHUTTER_DENOMS)}"
    except (ValueError, ZeroDivisionError):
        pass
    return value


def normalize_expected(value) -> str:
    return str(value).strip().lower()


def firmware_at_least(found: str, minimum: str):
    pat = re.compile(r"(\d+)\.(\d+)")
    a, b = pat.search(found or ""), pat.search(minimum or "")
    if not a or not b:
        return None
    return (int(a[1]), int(a[2])) >= (int(b[1]), int(b[2]))


# ---------------------------------------------------------------- pairing
def handshake(cam: Camera, client_id: str, name: str, method: str = "auto",
              wait_s: int = 60, sleep=time.sleep) -> str:
    """Pair with the camera. You'll be asked to approve on the S9's screen."""
    methods = ["s5iix", "legacy"] if method == "auto" else [method]
    for m in methods:
        if _pair(cam, m, client_id, name, wait_s, sleep):
            return m
    raise ConnectionError("Pairing failed — is the S9 in Wi-Fi 'Remote Shooting & View' and did you approve?")


def _pair(cam, method, client_id, name, wait_s, sleep) -> bool:
    if method == "s5iix":
        state, _ = parse_acc(cam.cgi("accctrl", type="req_acc_g",
                                     value=client_id.encode().hex(), value2=name.encode().hex()))
        if state != "ok":
            return False
        params = dict(type="req_acc_e", value=client_id.encode().hex(), value2=name.encode().hex())
    else:
        params = dict(type="req_acc", value=client_id, value2=name)

    for _ in range(max(1, int(wait_s))):
        state, fields = parse_acc(cam.cgi("accctrl", **params))
        if state == "ok":
            cam.session = fields[3] if len(fields) >= 4 and fields[2] == "open" else None
            return True
        if state == "err":
            return False
        sleep(1)                       # waiting for on-camera approval
    return False


def client_id() -> str:
    try:
        with open(CLIENT_FILE) as fh:
            return json.load(fh)["client_id"]
    except (OSError, KeyError, ValueError):
        cid = str(uuid.uuid4())
        with open(CLIENT_FILE, "w") as fh:
            json.dump({"client_id": cid}, fh)
        return cid


# ---------------------------------------------------------------- spec
def load_spec(path: str) -> dict:
    try:
        import yaml
    except ImportError:
        sys.exit("PyYAML missing — run: pip3 install pyyaml")
    with open(path) as fh:
        return yaml.safe_load(fh)


def iter_items(spec: dict):
    for bank in BANKS:
        for key, item in (spec.get(bank) or {}).items():
            if isinstance(item, dict):
                yield bank, key, item


def bank_items(spec: dict, bank: str) -> dict:
    merged = {k: v for k, v in (spec.get("base") or {}).items() if isinstance(v, dict)}
    if bank != "base":
        merged.update({k: v for k, v in (spec.get(bank) or {}).items() if isinstance(v, dict)})
    return merged


def plan_urls(spec: dict, bank: str, ip: str) -> list[str]:
    types = sorted({i["wifi"] for i in bank_items(spec, bank).values() if i.get("wifi")})
    return ([f"http://{ip}:60606/Lumix/Server0/ddd", build_url(ip, "getstate")]
            + [build_url(ip, "getsetting", type=t) for t in types])


# ---------------------------------------------------------------- read + compare
def read_settings(cam: Camera, types) -> dict:
    out = {}
    for t in sorted(set(types)):
        try:
            result, attrs = parse_camrply(cam.cgi("getsetting", type=t))
        except (OSError, ET.ParseError) as exc:
            out[t] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        if result != "ok":
            out[t] = {"error": f"result={result}"}
            continue
        raw = attrs.get(t) or attrs.get("value") or next(iter(attrs.values()), None)
        out[t] = {"raw": raw, "decoded": decode(t, raw)}
    return out


def evaluate(items: dict, readings: dict) -> list[dict]:
    rows = []
    for key, item in items.items():
        if not isinstance(item, dict):
            continue
        wifi = item.get("wifi")
        row = {"key": key, "wifi": wifi, "expected": item.get("value"),
               "actual": None, "raw": None}
        if not wifi:
            row["status"] = "MANUAL"
        else:
            reading = readings.get(wifi, {})
            row["actual"], row["raw"] = reading.get("decoded"), reading.get("raw")
            if "error" in reading or not reading:
                row["status"] = "UNREADABLE"
            elif "expect" in item:
                row["status"] = "PASS" if normalize_expected(item["expect"]) == row["actual"] else "FAIL"
            elif "expect_not" in item:
                row["status"] = "PASS" if normalize_expected(item["expect_not"]) != row["actual"] else "FAIL"
            else:
                row["status"] = "OBSERVED"
        rows.append(row)
    return rows


def diff_golden(golden: dict, readings: dict) -> list[dict]:
    drift = []
    for wifi, good in sorted((golden.get("readings") or {}).items()):
        actual = (readings.get(wifi) or {}).get("decoded")
        if good.get("decoded") != actual:
            drift.append({"wifi": wifi, "golden": good.get("decoded"), "actual": actual})
    return drift


def print_table(rows: list[dict]) -> None:
    order = {"FAIL": 0, "UNREADABLE": 1, "PASS": 2, "OBSERVED": 3, "MANUAL": 4}
    print(f"{'STATUS':<11}{'SETTING':<20}{'CAMERA':<14}EXPECTED / CHECK BY EYE")
    for r in sorted(rows, key=lambda r: (order[r["status"]], r["key"])):
        actual = r["actual"] if r["actual"] is not None else "-"
        print(f"{r['status']:<11}{r['key']:<20}{str(actual):<14}{r['expected']}")


# ---------------------------------------------------------------- CLI
def _connect(args) -> Camera:
    cam = Camera(args.ip)
    print("Approve the connection on the S9's screen if asked…")
    method = handshake(cam, client_id(), CLIENT_NAME, args.pairing)
    print(f"Paired ({method}).")
    return cam


def cmd_identify(args, spec) -> int:
    info = Camera(args.ip).describe()
    meta = spec.get("meta", {})
    print(json.dumps(info, indent=2))
    model_ok = meta.get("camera_model", "") in (info.get("modelNumber", "") + info.get("modelName", ""))
    fw_ok = firmware_at_least(info.get("X_FirmVersion", ""), str(meta.get("firmware_min", "")))
    print(f"model {'PASS' if model_ok else 'FAIL'} · firmware ≥ {meta.get('firmware_min')}: "
          f"{'PASS' if fw_ok else 'FAIL' if fw_ok is False else 'CHECK BY EYE'}")
    return 0 if model_ok and fw_ok is not False else 1


def cmd_probe(args, spec) -> int:
    cam = _connect(args)
    os.makedirs(args.out, exist_ok=True)
    dumps = {"getstate": ("getstate", {})}
    for t in ("capability", "allmenu", "curmenu", "lens"):
        dumps[f"getinfo_{t}"] = ("getinfo", {"type": t})
    for name, (mode, params) in dumps.items():
        try:
            text = cam.cgi(mode, **params)
        except OSError as exc:
            text = f"<!-- {exc} -->"
        with open(os.path.join(args.out, f"{name}.xml"), "w") as fh:
            fh.write(text)
        print(f"wrote {name}.xml ({len(text)} bytes)")
    print("Send me getinfo_allmenu.xml — it lists every setting type the S9 exposes.")
    return 0


def cmd_check(args, spec) -> int:
    items = bank_items(spec, args.bank)
    cam = _connect(args)
    result, _ = parse_camrply(cam.cgi("getstate"))      # sanity + keep-alive
    if result != "ok":
        raise ConnectionError(f"getstate returned {result}")
    readings = read_settings(cam, [i["wifi"] for i in items.values() if i.get("wifi")])
    rows = evaluate(items, readings)
    print(f"\nBank {args.bank} — turn the mode dial to {args.bank} BEFORE running.\n")
    print_table(rows)
    failed = any(r["status"] == "FAIL" for r in rows)
    if args.golden:
        with open(args.golden) as fh:
            drift = diff_golden(json.load(fh), readings)
        for d in drift:
            print(f"DRIFT      {d['wifi']:<20}{str(d['actual']):<14}golden: {d['golden']}")
        failed = failed or bool(drift)
    if args.save:
        snap = {"bank": args.bank, "taken": dt.datetime.now().isoformat(timespec="seconds"),
                "readings": readings}
        with open(args.save, "w") as fh:
            json.dump(snap, fh, indent=2)
        print(f"\nSnapshot saved to {args.save}")
    return 1 if failed else 0


def cmd_plan(args, spec) -> int:
    for url in plan_urls(spec, args.bank, args.ip):
        print(url)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Read-only LUMIX S9 settings checker")
    p.add_argument("--spec", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "s9_spec.yaml"))
    p.add_argument("--ip", default=DEFAULT_IP)
    p.add_argument("--pairing", choices=["auto", "s5iix", "legacy"], default="auto")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("identify")
    pr = sub.add_parser("probe"); pr.add_argument("--out", default="probe")
    for name in ("check", "plan"):
        sp = sub.add_parser(name); sp.add_argument("--bank", choices=BANKS, required=True)
        if name == "check":
            sp.add_argument("--save"); sp.add_argument("--golden")
    args = p.parse_args(argv)
    spec = load_spec(args.spec)
    try:
        return {"identify": cmd_identify, "probe": cmd_probe,
                "check": cmd_check, "plan": cmd_plan}[args.cmd](args, spec)
    except (ConnectionError, OSError) as exc:
        print(f"Couldn't reach the camera: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

"""Tests for s9check — written first; they define what "read-only" and "correct" mean.

Run:  python3 -m unittest -v test_s9check.py
No camera needed: every network call goes through a fake transport.
"""
import json
import os
import unittest

import s9check as s9

HERE = os.path.dirname(os.path.abspath(__file__))


class FakeFetch:
    """Replays canned replies by matching a substring of the URL; records calls."""

    def __init__(self, routes):
        self.routes = routes          # list of (substring, reply or list of replies)
        self.calls = []               # (url, headers)

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        for needle, reply in self.routes:
            if needle in url:
                if isinstance(reply, list):
                    return reply.pop(0) if len(reply) > 1 else reply[0]
                return reply
        raise AssertionError(f"unexpected URL: {url}")


def camrply(**attrs):
    inner = "".join(f' {k}="{v}"' for k, v in attrs.items())
    return f"<camrply><result>ok</result><settingvalue{inner}></settingvalue></camrply>"


class SafetyTests(unittest.TestCase):
    def test_write_modes_are_refused(self):
        for mode in ("setsetting", "camcmd", "camctrl", "startstream", "stopstream"):
            with self.subTest(mode=mode), self.assertRaises(s9.SafetyError):
                s9.build_url("192.168.54.1", mode, type="iso", value="400")

    def test_read_modes_build_urls(self):
        url = s9.build_url("192.168.54.1", "getsetting", type="iso")
        self.assertEqual(url, "http://192.168.54.1/cam.cgi?mode=getsetting&type=iso")

    def test_session_header_is_sent_after_pairing(self):
        fake = FakeFetch([("getstate", "<camrply><result>ok</result></camrply>")])
        cam = s9.Camera("10.0.0.5", fetch=fake)
        cam.session = "ABC123"
        cam.cgi("getstate")
        self.assertEqual(fake.calls[0][1], {"X-SESSION_ID": "ABC123"})


class ParsingTests(unittest.TestCase):
    def test_parse_ddd_ignores_namespaces(self):
        xml = (
            '<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:pana="urn:schemas-panasonic-com:pana">'
            "<device><friendlyName>S9-Suhas</friendlyName><modelName>LUMIX</modelName>"
            "<modelNumber>DC-S9</modelNumber><UDN>uuid:4D454930-0100-1000-8001-0123456789AB</UDN>"
            "<pana:X_FirmVersion>2.1</pana:X_FirmVersion></device></root>"
        )
        info = s9.parse_ddd(xml)
        self.assertEqual(info["modelNumber"], "DC-S9")
        self.assertEqual(info["X_FirmVersion"], "2.1")

    def test_parse_camrply_collects_attributes(self):
        result, attrs = s9.parse_camrply(camrply(iso="auto"))
        self.assertEqual(result, "ok")
        self.assertEqual(attrs, {"iso": "auto"})

    def test_parse_acc_reply(self):
        self.assertEqual(s9.parse_acc("ok_under_research_no_msg"), ("wait", []))
        self.assertEqual(s9.parse_acc("ok,S9,remote,open,SID9"), ("ok", ["S9", "remote", "open", "SID9"]))
        self.assertEqual(s9.parse_acc("err_reject")[0], "err")


class DecodeTests(unittest.TestCase):
    def test_aperture_apex(self):
        self.assertEqual(s9.decode("focal", "1536/256"), "f/8")      # Av 6
        self.assertEqual(s9.decode("focal", "2304/256"), "f/22")     # Av 9 -> 22.6

    def test_shutter_apex(self):
        self.assertEqual(s9.decode("shtrspeed", "2048/256"), "1/250")  # Tv 8 -> 1/256
        self.assertEqual(s9.decode("shtrspeed", "-1536/256"), "64s")   # Tv -6

    def test_plain_values_are_lowercased(self):
        self.assertEqual(s9.decode("iso", "AUTO"), "auto")
        self.assertIsNone(s9.decode("iso", None))

    def test_firmware_compare(self):
        self.assertTrue(s9.firmware_at_least("2.1", "2.1"))
        self.assertTrue(s9.firmware_at_least("Ver.2.10", "2.1"))
        self.assertFalse(s9.firmware_at_least("2.0", "2.1"))
        self.assertIsNone(s9.firmware_at_least("unknown", "2.1"))


class HandshakeTests(unittest.TestCase):
    def test_new_style_pairing_waits_for_approval(self):
        fake = FakeFetch([
            ("req_acc_g", "ok,1a2b3c4d"),
            ("req_acc_e", ["ok_under_research_no_msg", "ok,S9,remote,open,SID42"]),
        ])
        cam = s9.Camera("192.168.54.1", fetch=fake)
        method = s9.handshake(cam, "client-uuid", "s9check", wait_s=5, sleep=lambda _: None)
        self.assertEqual(method, "s5iix")
        self.assertEqual(cam.session, "SID42")

    def test_falls_back_to_legacy(self):
        fake = FakeFetch([
            ("req_acc_g", "err_param"),
            ("type=req_acc&", "ok,S9,remote,encrypted"),
        ])
        cam = s9.Camera("192.168.54.1", fetch=fake)
        method = s9.handshake(cam, "client-uuid", "s9check", wait_s=5, sleep=lambda _: None)
        self.assertEqual(method, "legacy")
        self.assertIsNone(cam.session)

    def test_pairing_never_uses_write_modes(self):
        fake = FakeFetch([("req_acc_g", "ok,1"), ("req_acc_e", "ok,S9,remote,open,X")])
        cam = s9.Camera("192.168.54.1", fetch=fake)
        s9.handshake(cam, "c", "n", wait_s=5, sleep=lambda _: None)
        self.assertTrue(all("mode=accctrl" in url for url, _ in fake.calls))


class EvaluateTests(unittest.TestCase):
    BANK = {
        "label": "LANDSCAPE",
        "iso": {"value": "100", "wifi": "iso", "expect": "100"},
        "aperture": {"value": "f/8", "wifi": "focal", "expect": "f/8"},
        "peaking": {"value": "ON", "wifi": "peaking", "expect_not": "off"},
        "metering": {"value": "Multiple", "wifi": "lightmetering"},
        "drive": {"value": "2 s", "wifi": None},
        "shutter": {"value": "per scene", "wifi": "shtrspeed"},
    }
    READINGS = {
        "iso": {"raw": "200", "decoded": "200"},
        "focal": {"raw": "1536/256", "decoded": "f/8"},
        "peaking": {"raw": "low", "decoded": "low"},
        "lightmetering": {"raw": "multi", "decoded": "multi"},
        "shtrspeed": {"error": "result=err_param"},
    }

    def test_statuses(self):
        rows = {r["key"]: r["status"] for r in s9.evaluate(self.BANK, self.READINGS)}
        self.assertEqual(rows, {
            "iso": "FAIL", "aperture": "PASS", "peaking": "PASS",
            "metering": "OBSERVED", "drive": "MANUAL", "shutter": "UNREADABLE",
        })

    def test_golden_drift(self):
        golden = {"readings": {"iso": {"decoded": "100"}, "focal": {"decoded": "f/8"}}}
        drift = s9.diff_golden(golden, self.READINGS)
        self.assertEqual(drift, [{"wifi": "iso", "golden": "100", "actual": "200"}])

    def test_bank_merges_base(self):
        spec = {"base": {"peaking": {"wifi": "peaking"}}, "C2": {"iso": {"wifi": "iso"}}}
        merged = s9.bank_items(spec, "C2")
        self.assertEqual(set(merged), {"peaking", "iso"})


class SpecFileTests(unittest.TestCase):
    """Guards the real spec file against YAML footguns (ON/OFF -> booleans) and typos."""

    def setUp(self):
        self.spec = s9.load_spec(os.path.join(HERE, "s9_spec.yaml"))

    def test_has_all_banks(self):
        for bank in ("meta", "base", "C1", "C2", "C3", "night"):
            self.assertIn(bank, self.spec)

    def test_values_are_strings_not_yaml_booleans(self):
        for bank, item_key, item in s9.iter_items(self.spec):
            with self.subTest(bank=bank, item=item_key):
                self.assertIsInstance(item.get("value"), str)

    def test_wifi_types_are_known(self):
        for bank, item_key, item in s9.iter_items(self.spec):
            if item.get("wifi"):
                self.assertIn(item["wifi"], s9.KNOWN_READ_TYPES, f"{bank}.{item_key}")

    def test_plan_is_read_only(self):
        for url in s9.plan_urls(self.spec, "C2", "192.168.54.1"):
            self.assertRegex(url, r"mode=(getsetting|getstate|getinfo)\b|:60606/")


if __name__ == "__main__":
    unittest.main()

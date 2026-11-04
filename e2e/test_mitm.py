#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline tests of hc_e2e/mitm.py: the no-update reply, the mitmproxy rule and the web API calls.  No VM.

    python3 -I -m unittest discover -s e2e -p 'test_*.py'
"""
import base64
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hc_e2e.beanjar  # noqa: E402
import hc_e2e.cli  # noqa: E402
import hc_e2e.mitm as mitm  # noqa: E402

FW = '33.31.61.01-HE_DTV_W22O_AFABATPU'


class Reply(unittest.TestCase):
    def test_version_from_the_firmware_name(self):
        self.assertEqual(mitm.installed_version(FW), ('33', '31.61'))
        self.assertEqual(mitm.installed_version('04.60.90.01-HE_DTV_W22P_AFADATAA'), ('04', '60.90'))
        self.assertIsNone(mitm.installed_version('rootfs'))
        self.assertIsNone(mitm.installed_version(''))

    def test_reply_offers_the_installed_version_without_force(self):
        xml = mitm.no_update_reply(FW).decode()
        self.assertIn('<RESULT_CD>900</RESULT_CD>', xml)  # a reply the daemon parses, with nothing newer in it
        self.assertIn('<UPDATE_MAJOR_VER>33</UPDATE_MAJOR_VER><UPDATE_MINOR_VER>31.61</UPDATE_MINOR_VER>', xml)
        self.assertIn('<FORCE_FLAG>N</FORCE_FLAG>', xml)
        self.assertNotIn('DELTA', xml)
        self.assertNotIn('CDN_URL>h', xml)

    def test_rule_replaces_the_response_body_of_the_check_only(self):
        spec = mitm.modify_body_spec(FW)
        sep = spec[0]
        flt, regex, repl = spec[1:].split(sep, 2)
        self.assertEqual(flt, '~s & ~u CheckSWAutoUpdate\\.laf')  # ~s: never the request
        self.assertTrue(re.fullmatch(regex, 'any\nbody', re.DOTALL))
        self.assertEqual(base64.b64decode(repl), mitm.no_update_reply(FW))  # the body of LG's protocol is base64
        self.assertNotIn(sep, regex + repl)
        self.assertFalse(repl.startswith('@'))  # '@' would be a file path

    def test_whole_body_pattern_matches_once(self):
        regex = mitm.modify_body_spec(FW)[1:].split('|', 2)[1]
        self.assertEqual(re.sub(regex, lambda _: 'X', 'PGE+\nPGI+', flags=re.DOTALL), 'X')


class Country(unittest.TestCase):
    BODY = ('{"initServices":{"services":{"clientIP":"1.2.3.4","country":{"code":"JP","ricCode":"AIC",'
            '"threecode":"JPN"},"enableCBSPolicy":"N","eulaList":{"x":"_us_"}}}}')

    def apply(self, body, specs):
        for spec in specs:
            flt, regex, repl = spec[1:].split(spec[0], 2)
            body = re.sub(regex, lambda _: repl, body, flags=re.DOTALL)  # mitmproxy: literal
        return body

    def test_only_the_country_object_changes(self):
        out = self.apply(self.BODY, mitm.country_specs('US', 'USA'))
        self.assertEqual(out, self.BODY.replace('"JP"', '"US"').replace('"JPN"', '"USA"'))
        self.assertIn('"ricCode":"AIC"', out)
        self.assertEqual(json.loads(out)['initServices']['services']['country'],
                         {'code': 'US', 'ricCode': 'AIC', 'threecode': 'USA'})

    # 8.x (W22O 13.30.56, /rest/sdp/v14.0/initservices): pretty-printed, threecode before ricCode
    BODY8 = ('{\n  "initServices" : {\n    "services" : {\n      "country" : {\n        "code" : "JP",\n'
             '        "threecode" : "JPN",\n        "ricCode" : "AIC"\n      },\n      "x" : {"code" : "JP"}\n'
             '    }\n  }\n}')

    def test_pretty_printed_reply_with_other_field_order(self):
        out = self.apply(self.BODY8, mitm.country_specs('US', 'USA'))
        self.assertEqual(out, self.BODY8.replace('"JP",\n        "threecode" : "JPN"',
                                                 '"US",\n        "threecode" : "USA"'))
        self.assertEqual(json.loads(out)['initServices']['services']['country'],
                         {'code': 'US', 'ricCode': 'AIC', 'threecode': 'USA'})
        self.assertEqual(json.loads(out)['initServices']['services']['x'], {'code': 'JP'})

    def test_rules_are_response_of_initservices_only(self):
        for spec in mitm.country_specs('US', 'USA'):
            self.assertEqual(spec[1:].split(spec[0], 2)[0], '~s & ~u initservices')
            self.assertNotIn(spec[0], spec[1:].split(spec[0], 2)[1] + spec[1:].split(spec[0], 2)[2][-3:])

    def test_other_bodies_are_untouched(self):
        other = '{"foo":{"code":"JP"},"bar":"threecode"}'
        self.assertEqual(self.apply(other, mitm.country_specs('US', 'USA')), other)

    def test_bad_country_gives_no_rule(self):
        for a, b in (('us', 'USA'), ('US', 'US'), ('', ''), (None, None), ('U|', 'USA')):
            self.assertIsNone(mitm.country_specs(a, b))

    def test_cache_round_trip_and_unknown(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(mitm.load_country('pi', 'v', d))
            mitm.save_country('pi', 'v', 'US', 'USA', d)
            self.assertEqual(mitm.load_country('pi', 'v', d), ('US', 'USA'))
            self.assertIsNone(mitm.load_country('rockpi4a-1', 'v', d))

    def test_read_country(self):
        class Dev:
            def __init__(self, out):
                self.out = out

            def luna(self, uri, payload='{}'):
                return self.out
        ok = '{"returnValue":true,"settings":{"smartServiceCountryCode2":"US","smartServiceCountryCode3":"USA"}}'
        self.assertEqual(mitm.read_country(Dev(ok)), ('US', 'USA'))
        self.assertIsNone(mitm.read_country(Dev('{"returnValue":false}')))
        self.assertIsNone(mitm.read_country(Dev('garbage')))


class FakeInspector:
    log = []
    early = []
    keep = True

    def __init__(self, ref):
        self.ref = ref
        self.options = {}
        FakeInspector.log.append(('connect', ref))

    def set_option(self, name, value):
        self.options[name] = value
        FakeInspector.log.append(('set', name, value))

    def option(self, name):
        return self.options[name] if self.keep else []

    def checks(self):
        return list(self.early)

    def close(self):
        FakeInspector.log.append(('close', self.ref))


class FakeHost:
    name = 'pi'

    def __init__(self, fw=FW):
        self.fw = fw

    def info(self, vm):
        return {'fw': '/srv/beanjar/firmware/' + self.fw}

    def remote_target(self, vm):
        return 'pi/' + vm


class Turn(unittest.TestCase):
    def setUp(self):
        FakeInspector.log, FakeInspector.early, FakeInspector.keep = [], [], True
        self.said = []

    def run_it(self, host=None):
        return mitm.turn_off_update_offer(host or FakeHost(), 'w22o-ponytail-us', self.said.append, FakeInspector)

    def test_sets_the_rule_and_closes_the_connection(self):
        self.assertTrue(self.run_it())
        self.assertEqual(FakeInspector.log[0], ('connect', 'pi/w22o-ponytail-us'))
        self.assertEqual(FakeInspector.log[1], ('set', 'modify_body', [mitm.modify_body_spec(FW)]))
        self.assertEqual(FakeInspector.log[-1], ('close', 'pi/w22o-ponytail-us'))
        self.assertIn('31.61', ' '.join(self.said))
        self.assertNotIn('RACE', ' '.join(self.said))

    def test_a_check_already_answered_is_said_clearly(self):
        FakeInspector.early = [(1793711657.67, 200, 1532)]
        self.assertTrue(self.run_it())
        self.assertIn('RACE LOST', ' '.join(self.said))

    def test_a_rule_the_inspector_did_not_keep_is_not_success(self):
        FakeInspector.keep = False
        self.assertFalse(self.run_it())
        self.assertIn('did not keep', ' '.join(self.said))

    def test_country_rules_join_the_update_rule_in_one_list(self):
        import tempfile
        orig = mitm.COUNTRY_DIR
        with tempfile.TemporaryDirectory() as d:
            mitm.COUNTRY_DIR = d
            try:
                self.assertTrue(self.run_it())  # country not known: only the update rule, and it is said
                self.assertEqual(FakeInspector.log[1], ('set', 'modify_body', [mitm.modify_body_spec(FW)]))
                self.assertIn('not known yet', ' '.join(self.said))
                mitm.save_country('pi', 'w22o-ponytail-us', 'US', 'USA')
                FakeInspector.log.clear()
                self.assertTrue(self.run_it())
                self.assertEqual(FakeInspector.log[1][2],
                                 [mitm.modify_body_spec(FW)] + mitm.country_specs('US', 'USA'))
                self.assertIn('country US/USA', ' '.join(self.said))
                FakeInspector.log.clear()  # the country rule alone
                self.assertTrue(mitm.turn_off_update_offer(FakeHost(), 'w22o-ponytail-us', self.said.append,
                                                           FakeInspector, update=False))
                self.assertEqual(FakeInspector.log[1][2], mitm.country_specs('US', 'USA'))
            finally:
                mitm.COUNTRY_DIR = orig

    def test_unknown_firmware_name_touches_nothing(self):
        self.assertFalse(self.run_it(FakeHost('weird')))
        self.assertEqual(FakeInspector.log, [])

    def test_unreachable_inspector_is_said_not_raised(self):
        def refuse(ref):
            raise RuntimeError('no forward')
        self.assertFalse(mitm.turn_off_update_offer(FakeHost(), 'v', self.said.append, refuse))
        self.assertIn('cannot reach', ' '.join(self.said))


class Wiring(unittest.TestCase):
    def test_connect_command(self):
        self.assertEqual(mitm.connect_command('pi/w22o-ponytail-us'), ['beanjar', 'mitm', 'connect', 'pi/w22o-ponytail-us'])

    def test_option_default_is_off_and_keep_exists(self):
        ap = hc_e2e.cli.build_parser()
        self.assertEqual(ap.parse_args(['--vm', '10.x']).update_offer, 'off')
        self.assertEqual(ap.parse_args(['--vm', '10.x', '--update-offer', 'keep']).update_offer, 'keep')

    def test_country_reply_default_is_off_and_keep_exists(self):
        ap = hc_e2e.cli.build_parser()
        self.assertEqual(ap.parse_args(['--vm', '10.x']).country_reply, 'off')
        self.assertEqual(ap.parse_args(['--vm', '10.x', '--country-reply', 'keep']).country_reply, 'keep')

    def test_host_start_sets_the_rule_only_when_off(self):
        calls = []
        orig = mitm.turn_off_update_offer
        mitm.turn_off_update_offer = lambda host, vm, say, update=True, country=True: calls.append(vm)
        try:
            for mode, expect in (('off', ['v']), ('keep', [])):
                calls.clear()
                h = hc_e2e.beanjar.BeanjarHost('local')
                h.update_offer = mode
                h.running_vms = lambda: []
                h.beanjar = lambda *a, **k: (0, 'ok')
                h.vnc_start = lambda vm: None
                h.start('v')
                self.assertEqual(calls, expect)
        finally:
            mitm.turn_off_update_offer = orig


if __name__ == '__main__':
    unittest.main()

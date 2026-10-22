"""Standalone, game-free regression for the offline CPU surface reporter.

Run: python scripts/check_cpu_surface_report.py
Fixtures are handwritten log-shaped text and independently calculated answers.
No compiler, native process, GPU, platform API or third-party package is used.
"""
import copy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/analysis/cpu_surface_report.py'
FIXTURES = ROOT / 'scripts/fixtures'
SPEC = importlib.util.spec_from_file_location('cpu_surface_report', SCRIPT)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)
OVERLAP = (FIXTURES / 'cpu_surface_report_overlap.txt').read_text(encoding='utf-8')
SERIAL = (FIXTURES / 'cpu_surface_report_serial.txt').read_text(encoding='utf-8')
EXPECTED = json.loads((FIXTURES / 'cpu_surface_report_expected.json').read_text(encoding='utf-8'))
LINES = [line for line in OVERLAP.splitlines() if line.startswith(report.MARKER)]


def changed(line, **fields):
    """Mutate authored text without using the reporter to generate expectations."""
    for key, value in fields.items():
        line, count = re.subn(r'(?<!\S)' + re.escape(key) + r'=\S+', f'{key}={value}', line)
        if count != 1:
            raise AssertionError(f'fixture has {count} occurrences of {key}')
    return line


class CpuSurfaceReportChecks(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='cpu-surface-report-')
        self.addCleanup(self.scratch.cleanup)
        self.path = Path(self.scratch.name) / 'authored.txt'

    def cli(self, text, *args):
        # CRLF and spaces in paths are exercised on every invocation.
        self.path = Path(self.scratch.name) / 'authored input.txt'
        content = text.replace('\n', '\r\n').encode('utf-8')
        self.path.write_bytes(content)
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.path), *map(str, args)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(self.path.read_bytes(), content, 'reporter modified its input')
        return result

    def from_text(self, text, window=None):
        self.path.write_text(text, encoding='utf-8')
        return report.build_report(report.load_records(self.path), window)

    def assert_oracle(self, actual):
        self.assertEqual(actual['status'], 'ok')
        self.assertEqual(actual['frequency_hz'], 1000)
        self.assertEqual(actual['counts'], EXPECTED['counts'])
        self.assertEqual(actual['counters'], EXPECTED['counters'])
        self.assertEqual(actual['coverage']['ticks'], EXPECTED['coverage'])
        self.assertEqual(actual['coverage']['ms'], EXPECTED['coverage'])
        self.assertEqual(set(actual['latencies']), set(EXPECTED['latencies']))
        for key, expected in EXPECTED['latencies'].items():
            self.assertEqual(actual['latencies'][key]['count'], 3)
            self.assertEqual(actual['latencies'][key]['ticks'], expected, key)
            self.assertEqual(actual['latencies'][key]['ms'], expected, key)

    def test_independent_overlapping_oracle_and_output(self):
        result = self.cli(OVERLAP, '--format', 'json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        actual = json.loads(result.stdout)
        self.assert_oracle(actual)
        self.assertEqual(self.cli(OVERLAP, '--format', 'json').stdout, result.stdout)
        text = self.cli(OVERLAP)
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertEqual(self.cli(OVERLAP).stdout, text.stdout)
        self.assertIn('accumulated request latency', text.stdout)
        self.assertIn('request_interval_union: 180 ticks; 180.000000 ms', text.stdout)
        self.assertIn('request_span: 220 ticks; 220.000000 ms', text.stdout)
        self.assertIn('concurrent requests may overlap', text.stdout)
        self.assertIn('not elapsed CPU execution time', text.stdout)
        self.assertIn('GPU utilization or game FPS', text.stdout)
        self.assertIn('nearest-rank', text.stdout)
        for key in EXPECTED['latencies']:
            self.assertRegex(text.stdout, r'(?m)^' + key + r'\s+\d+\.\d{6}')
        # Demonstrate that the fixed oracle rejects the two dangerous confusions.
        swapped = copy.deepcopy(actual)
        swapped['latencies']['queue'], swapped['latencies']['service'] = swapped['latencies']['service'], swapped['latencies']['queue']
        with self.assertRaises(AssertionError):
            self.assert_oracle(swapped)
        accumulated_as_union = copy.deepcopy(actual)
        accumulated_as_union['coverage']['ticks']['request_interval_union'] = 260
        with self.assertRaises(AssertionError):
            self.assert_oracle(accumulated_as_union)

    def test_serial_and_direct_renderer(self):
        actual = self.from_text(SERIAL)
        self.assertEqual(actual['counts']['fully_contained'], 2)
        self.assertEqual(actual['coverage']['ticks'], {'request_span': 220, 'request_interval_union': 120})
        self.assertEqual(actual['latencies']['total']['ticks']['accumulated_request_latency'], 120)
        direct = self.from_text(LINES[2])
        self.assertEqual(direct['latencies']['queue']['ticks']['max'], 0)
        self.assertEqual(direct['latencies']['return']['ticks']['max'], 0)
        self.assertEqual(direct['latencies']['service']['ticks']['max'], 20)

    def test_accepts_emitter_impossible_combinations(self):
        # Documented policy: validation checks arithmetic, not emitter reachability.
        accepted = [
            changed(LINES[1], copies=0),  # copy bytes with no copy requests
            changed(LINES[1], client=2),  # client == renderer with queue=100
            changed(LINES[1], bytes=0),
            changed(LINES[1], **{'returned_at': 9223372036854775807, 'total': 9223372036854775687, 'return': 9223372036854775557}),
        ]
        for line in accepted:
            self.assertEqual(self.from_text(line)['status'], 'ok', line)

    def test_explicit_window_never_clips_crossing_requests(self):
        result = self.cli(OVERLAP, '--window', 110, 300, '--format', 'json')
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(result.stdout)
        expected = EXPECTED['window_110_300']
        self.assertEqual(actual['counts'], expected['counts'])
        self.assertEqual(actual['observation_window'], dict(start_tick=110, end_tick=300, duration_ticks=190, duration_ms=190))
        for key in ('queue', 'service', 'total'):
            self.assertEqual(actual['latencies'][key]['ticks']['accumulated_request_latency'], expected[key])
        self.assertEqual(actual['coverage']['ticks']['request_interval_union'], expected['request_interval_union'])
        self.assertEqual(actual['counters']['bytes'], 4)

    def test_half_open_endpoints_and_zero_duration(self):
        records = report.load_records(FIXTURES / 'cpu_surface_report_overlap.txt')
        for window, expected in [((100, 200), ['fully_contained', 'boundary_crossing', 'excluded']),
                                 ((200, 300), ['excluded', 'boundary_crossing', 'excluded']),
                                 ((320, 400), ['excluded'] * 3),
                                 ((0, 100), ['excluded'] * 3),
                                 ((170, 180), ['boundary_crossing', 'boundary_crossing', 'excluded'])]:
            with self.subTest(window=window):
                self.assertEqual([report.classify(r, window) for r in records], expected)
        zero = changed(LINES[2], finished_at=300, returned_at=300, service=0, total=0,
                       allocation=0, prior_fence=0, commands=0, copy_fence=0, packing=0, other=0)
        record = report.parse_record(zero)
        self.assertEqual(report.classify(record, (300, 301)), 'fully_contained')
        self.assertEqual(report.classify(record, (299, 300)), 'excluded')
        actual = self.from_text(zero)
        self.assertEqual(actual['status'], 'ok')
        self.assertEqual(actual['coverage']['ticks']['request_interval_union'], 0)
        self.assertEqual(actual['latencies']['total']['ticks']['max'], 0)
        # Shutdown may emit zero service; do not require any copy or hit.
        shutdown = changed(LINES[0], finished_at=160, returned_at=170, service=0, total=70,
                           allocation=0, prior_fence=0, commands=0, copy_fence=0, packing=0, other=0,
                           matched=0, hits=0, copies=0, host_bytes=0, guest_bytes=0)
        self.assertEqual(self.from_text(shutdown)['latencies']['service']['ticks']['max'], 0)

    def test_large_qpc_and_small_durations_remain_exact(self):
        base = 2**60 + 17
        # 'return' is a Python keyword, hence the explicit mapping.
        line = changed(LINES[0], **dict(queued_at=base, started_at=base+1, finished_at=base+4,
                                   returned_at=base+6, frequency=10_000_000, queue=1, service=3,
                                   total=6, allocation=1, prior_fence=1, commands=0, copy_fence=1,
                                   packing=0, other=0), **{'return': 2})
        actual = self.from_text(line, (base, base+6))
        self.assertEqual(actual['latencies']['queue']['ticks']['max'], 1)
        self.assertEqual(actual['latencies']['total']['ticks']['max'], 6)
        self.assertEqual(actual['latencies']['total']['ms']['max'], 0.0006)
        self.assertEqual(actual['observation_window']['duration_ticks'], 6)

    def test_nearest_rank_and_conversion_after_summing(self):
        stats = report.distribution(list(range(1, 21)), 3)
        self.assertEqual(stats['ticks'], dict(accumulated_request_latency=210, p50=10, p95=19, max=20))
        self.assertEqual(stats['ms']['accumulated_request_latency'], 70000)
        self.assertEqual(report.distribution(list(range(1, 22)), 1000)['ticks']['p95'], 20)
        self.assertEqual(report.distribution([8, 2], 1000)['ticks']['p50'], 2)
        self.assertIsNone(report.distribution([], 1000))

    def test_log_order_and_prefix_do_not_affect_aggregation(self):
        expected = self.from_text(OVERLAP)
        actual = self.from_text('\n'.join('[stderr] ' + line for line in reversed(LINES)))
        self.assertEqual(actual, expected)

    def test_empty_input_and_empty_window_are_explicit(self):
        for text in ('', 'unrelated native output\n[CPUSURFACE] no timing records\n'):
            with self.subTest(text=text):
                result = self.cli(text, '--window', 100, 200, '--format', 'json')
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = json.loads(result.stdout)
                self.assertEqual(actual['status'], 'no_records')
                self.assertIsNone(actual['frequency_hz'])
                self.assertIsNone(actual['observation_window']['duration_ms'])
                self.assertTrue(all(value is None for value in actual['latencies'].values()))
                self.assertIsNone(actual['coverage'])
                self.assertIsNone(actual['counters'])
                self.assertIn('unavailable, not zero', self.cli(text).stdout)
        for window in ((170, 180), (500, 600)):
            actual = self.from_text(OVERLAP, window)
            self.assertEqual(actual['status'], 'no_fully_contained_requests')
            self.assertTrue(all(value is None for value in actual['latencies'].values()))
            self.assertIn('whole-request statistics are unavailable', report.render_text(actual))

    def test_malformed_candidates_have_line_diagnostics(self):
        cases = [report.MARKER, '[CPUSURFACE_PROFILE', '[CPUSURFACE_PROFILEX] foo',
                 LINES[0].replace(' guest_bytes=8', ''), LINES[0] + ' truncated',
                 LINES[0] + ' queue=60', LINES[0] + ' future_field=1',
                 LINES[0] + LINES[1], LINES[0].replace('] address', ']address'),
                 changed(LINES[0], address='0x020120'), changed(LINES[0], address='0002012G')]
        for key in report.FIELDS:
            cases.append(re.sub(r'(?<!\S)' + key + r'=\S+\s*', '', LINES[0]))
            for value in ('-1', '+1', '1.0', '0x10', 'nan', '18446744073709551616', '9' * 5000):
                cases.append(changed(LINES[0], **{key: value}))
        for line in cases:
            with self.subTest(line=line[:120]):
                self.path.write_text('ignored\n' + line + '\n', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, re.escape(str(self.path)) + r':2:'):
                    report.load_records(self.path)
        result = self.cli('ignored\n' + report.MARKER, '--format', 'json')
        self.assertEqual(result.returncode, 2)
        self.assertIn(':2: missing fields', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_inconsistent_accounting_and_frequencies_rejected_even_if_excluded(self):
        cases = [({'frequency': 0}, 'frequency must be positive'),
                 ({'started_at': 99}, 'boundaries'), ({'finished_at': 159}, 'boundaries'),
                 ({'returned_at': 189}, 'boundaries'),
                 ({'queue': 30, 'service': 60}, 'queue'), ({'queue': 59}, 'queue'),
                 ({'service': 29}, 'service'), ({'return': 9}, 'return'), ({'total': 99}, 'total'),
                 ({'allocation': 31}, 'service stages'), ({'other': 1}, 'service stages'),
                 ({'copies': 3}, 'hits \\+ copies'), ({'hits': 2}, 'hits \\+ copies'),
                 ({'bytes': 2**32}, 'native integer range'), ({'client': 2**32}, 'native integer range'),
                 ({'renderer': 2**32}, 'native integer range')]
        for fields, message in cases:
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(ValueError, message):
                    self.from_text(changed(LINES[0], **fields), (500, 600))
        mixed = LINES[0] + '\n' + changed(LINES[1], frequency=2000)
        result = self.cli(mixed, '--window', 500, 600, '--format', 'json')
        self.assertEqual(result.returncode, 2)
        self.assertIn(':2: incompatible frequency 2000; expected 1000', result.stderr)
        self.assertEqual(result.stdout, '')
        # The production contract permits matched > hits + copies.
        self.assertEqual(self.from_text(changed(LINES[0], matched=3))['counters']['matched'], 3)

    def test_invalid_window_and_missing_input(self):
        for bounds in (('x', '20'), (20, 20), (21, 20), (-1, 20), (0, 2**64)):
            with self.subTest(bounds=bounds):
                result = self.cli(OVERLAP, '--window', *bounds)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.path.parent / 'missing.txt')],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('Traceback', result.stderr)

    def test_format_contract_matches_current_emitter(self):
        source = (ROOT / 'vendor/ps3recomp/libs/video/rsx_cpu_surface.inc').read_text(encoding='utf-8')
        emitted = re.search(r'fprintf\(stderr,"\[CPUSURFACE_PROFILE\] ([^"]+)', source)[1]
        self.assertEqual(tuple(re.findall(r'(\w+)=%', emitted)), report.FIELDS)


if __name__ == '__main__':
    unittest.main(verbosity=2)

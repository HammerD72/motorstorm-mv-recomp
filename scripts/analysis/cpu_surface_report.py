"""Validate and summarize existing CPUSURFACE_PROFILE logs, entirely offline."""
import argparse
import json
from pathlib import Path
import re
import sys

MARKER = '[CPUSURFACE_PROFILE]'
BOUNDARIES = ('queued_at', 'started_at', 'finished_at', 'returned_at')
STAGES = ('allocation', 'prior_fence', 'commands', 'copy_fence', 'packing', 'other')
LATENCIES = ('queue', 'service', 'return', 'total') + STAGES
COUNTERS = ('bytes', 'matched', 'hits', 'copies', 'host_bytes', 'guest_bytes')
FIELDS = ('address', 'bytes', 'client', 'renderer') + BOUNDARIES + ('frequency',) + LATENCIES + COUNTERS[1:]
U32_FIELDS = {'address', 'bytes', 'client', 'renderer'}
PERCENTILE_METHOD = 'nearest-rank: sorted[ceil(p*n)-1]; p50=0.50, p95=0.95; no interpolation'
NOTES = [
    'Distributions, counters and coverage use fully contained requests only; crossing requests are never clipped.',
    'Sums are accumulated request latency; concurrent requests may overlap.',
    'The request interval union is wall-clock coverage of requests, not elapsed CPU execution time.',
    'Fence waits include host operations; neither latency nor request cadence measures exclusive CPU time, GPU utilization or game FPS.',
    'Only emitted records are observed; missing records do not establish zero cost, native gameplay or stable 30 FPS.',
]


def parse_record(line):
    """Return one validated record, or None for an unrelated line."""
    if '[CPUSURFACE_PROFILE' not in line:
        return None
    if line.count('[CPUSURFACE_PROFILE') != 1 or MARKER not in line:
        raise ValueError('malformed or repeated CPUSURFACE_PROFILE marker')
    payload = line.split(MARKER, 1)[1]
    if payload and not payload[0].isspace():
        raise ValueError('expected whitespace after CPUSURFACE_PROFILE marker')
    record = {}
    for token in payload.split():
        key, separator, value = token.partition('=')
        if not separator or key not in FIELDS:
            raise ValueError(f'unknown field or malformed token: {token!r}')
        if key in record:
            raise ValueError(f'duplicate field: {key}')
        pattern = r'[0-9A-Fa-f]{8}' if key == 'address' else r'[0-9]+'
        if not re.fullmatch(pattern, value):
            raise ValueError(f'{key}: expected ' + ('eight hexadecimal digits' if key == 'address' else 'an unsigned decimal integer'))
        # Bound the text before int() as well as the native integer range.
        if len(value) > 20:
            raise ValueError(f'{key}: outside unsigned native integer range')
        number = int(value, 16 if key == 'address' else 10)
        if number >= 1 << (32 if key in U32_FIELDS else 64):
            raise ValueError(f'{key}: outside unsigned native integer range')
        record[key] = number
    missing = sorted(set(FIELDS) - record.keys())
    if missing:
        raise ValueError('missing fields: ' + ', '.join(missing))
    if record['frequency'] <= 0:
        raise ValueError('frequency must be positive')
    q, s, f, r = (record[key] for key in BOUNDARIES)
    if not q <= s <= f <= r:
        raise ValueError('boundaries must satisfy queued_at <= started_at <= finished_at <= returned_at')
    for key, expected in (('queue', s-q), ('service', f-s), ('return', r-f), ('total', r-q)):
        if record[key] != expected:
            raise ValueError(f'{key}={record[key]} does not match boundary duration {expected}')
    if sum(record[key] for key in STAGES) != record['service']:
        raise ValueError('service stages including other must sum to service')
    if record['copies'] + record['hits'] > record['matched']:
        raise ValueError('hits + copies must not exceed matched')
    return record


def load_records(path):
    records = []
    frequency = None
    # Native log text outside these ASCII records may use a different encoding.
    # Replacement characters in a candidate's fields still fail validation.
    with path.open(encoding='utf-8-sig', errors='replace') as stream:
        for line_no, line in enumerate(stream, 1):
            try:
                record = parse_record(line)
                if record is None:
                    continue
                if frequency is not None and record['frequency'] != frequency:
                    raise ValueError(f'incompatible frequency {record["frequency"]}; expected {frequency}')
                frequency = record['frequency']
                records.append(record)
            except ValueError as exc:
                raise ValueError(f'{path}:{line_no}: {exc}') from exc
    return records


def classify(record, window):
    if window is None:
        return 'fully_contained'
    lo, hi = window
    q, r = record['queued_at'], record['returned_at']
    # A zero-duration request belongs to the window containing its timestamp.
    if lo <= q < hi and r <= hi:
        return 'fully_contained'
    if q < hi and r > lo:
        return 'boundary_crossing'
    return 'excluded'


def milliseconds(ticks, frequency):
    # Subtraction, aggregation and percentile selection happen before conversion.
    return ticks * 1000 / frequency


def distribution(values, frequency):
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    ticks = {
        'accumulated_request_latency': sum(ordered),
        'p50': ordered[(50*n + 99)//100 - 1],
        'p95': ordered[(95*n + 99)//100 - 1],
        'max': ordered[-1],
    }
    return {'count': n, 'ticks': ticks,
            'ms': {key: milliseconds(value, frequency) for key, value in ticks.items()}}


def request_coverage(records, frequency):
    if not records:
        return None
    spans = sorted((r['queued_at'], r['returned_at']) for r in records)
    edge = spans[0][0]
    union = 0
    for start, end in spans:
        union += max(0, end - max(start, edge))
        edge = max(edge, end)
    ticks = {'request_span': edge - spans[0][0], 'request_interval_union': union}
    return {'ticks': ticks, 'ms': {key: milliseconds(value, frequency) for key, value in ticks.items()}}


def build_report(records, window=None):
    if window is not None and not 0 <= window[0] < window[1] < 1 << 64:
        raise ValueError('window must satisfy 0 <= START < END < 2^64')
    frequency = records[0]['frequency'] if records else None
    counts = dict(records=len(records), fully_contained=0, boundary_crossing=0, excluded=0)
    contained = []
    for record in records:
        if record['frequency'] != frequency:
            raise ValueError('incompatible frequencies in aggregate')
        category = classify(record, window)
        counts[category] += 1
        if category == 'fully_contained':
            contained.append(record)
    observation = None
    if window is not None:
        lo, hi = window
        observation = dict(start_tick=lo, end_tick=hi, duration_ticks=hi-lo,
                           duration_ms=milliseconds(hi-lo, frequency) if frequency else None)
    return {
        'schema_version': 1,
        'status': 'no_records' if not records else ('ok' if contained else 'no_fully_contained_requests'),
        'frequency_hz': frequency,
        'observation_window': observation,
        'counts': counts,
        'percentile_method': PERCENTILE_METHOD,
        'latencies': {key: distribution([r[key] for r in contained], frequency) for key in LATENCIES},
        'counters': {key: sum(r[key] for r in contained) for key in COUNTERS} if contained else None,
        'coverage': request_coverage(contained, frequency),
        'notes': NOTES,
    }


def render_text(report):
    lines = ['CPU surface latency report', f'Status: {report["status"]}']
    frequency = report['frequency_hz']
    lines.append(f'Clock frequency: {frequency} ticks/s' if frequency else 'Clock frequency: unavailable')
    window = report['observation_window']
    if window is None:
        lines.append('Observation window: none; all validated records are fully contained')
    else:
        duration = f'{window["duration_ms"]:.6f} ms' if frequency else 'ms unavailable'
        lines.append(f'Observation window: [{window["start_tick"]}, {window["end_tick"]}) QPC ticks; {window["duration_ticks"]} ticks; {duration}')
    lines.append('Requests: ' + ', '.join(f'{key}={value}' for key, value in report['counts'].items()))
    lines.append('Percentiles: ' + report['percentile_method'])
    if report['status'] == 'no_records':
        lines.append('No CPUSURFACE_PROFILE records observed; latency is unavailable, not zero.')
    elif report['status'] == 'no_fully_contained_requests':
        lines.append('No fully contained requests; whole-request statistics are unavailable.')
    else:
        lines += ['Whole-request latencies and disjoint service stages (ms; fully contained requests):',
                  'metric       accumulated request latency          p50          p95          max']
        for key, stats in report['latencies'].items():
            m = stats['ms']
            lines.append(f'{key:<13} {m["accumulated_request_latency"]:>27.6f} {m["p50"]:>12.6f} {m["p95"]:>12.6f} {m["max"]:>12.6f}')
        lines.append('Counters (fully contained): ' + ', '.join(f'{key}={value}' for key, value in report['counters'].items()))
        for key, ticks in report['coverage']['ticks'].items():
            lines.append(f'{key}: {ticks} ticks; {report["coverage"]["ms"][key]:.6f} ms')
    lines.extend(report['notes'])
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path, help='existing native log (read only)')
    parser.add_argument('--window', type=int, nargs=2, metavar=('START', 'END'),
                        help='explicit half-open observation window in integer QPC ticks')
    parser.add_argument('--format', choices=('text', 'json'), default='text')
    args = parser.parse_args(argv)
    if args.window is not None and not 0 <= args.window[0] < args.window[1] < 1 << 64:
        parser.error('window must satisfy 0 <= START < END < 2^64')
    try:
        report = build_report(load_records(args.log), args.window)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) if args.format == 'json' else render_text(report))
    return 0


if __name__ == '__main__':
    sys.exit(main())

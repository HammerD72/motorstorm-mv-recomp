# Offline CPU surface latency report

`scripts/analysis/cpu_surface_report.py` reads an existing native log and writes
deterministic text or JSON to stdout. It uses the Python standard library and
does not launch, attach to or control a process. It opens the input read only.
No game, generated code, GPU or Windows host is required.

The format and stage semantics come from the current
[emitter](../vendor/ps3recomp/libs/video/rsx_cpu_surface.inc), its
[accounting contract](../vendor/ps3recomp/libs/video/rsx_cpu_surface_profile.h)
and [profiling documentation](VENDOR.md#cpu-surface-request-latency-issue-179).
This reporter does not change profiling or synchronization. Private logs stay
local under the [repository boundary](REPOSITORY.md).

## Runnable authored examples

Run from the repository root, using Python 3.10 or newer:

```sh
python scripts/analysis/cpu_surface_report.py scripts/fixtures/cpu_surface_report_overlap.txt
python scripts/analysis/cpu_surface_report.py scripts/fixtures/cpu_surface_report_overlap.txt --window 110 300 --format json
python scripts/analysis/cpu_surface_report.py scripts/fixtures/cpu_surface_report_serial.txt --format json
python scripts/check_cpu_surface_report.py
```

The two `.txt` fixtures are handwritten examples, not captured native output.
The overlap fixture has lifetimes `[100,200)`, `[120,260)` and `[300,320)` at
1000 ticks/s. Its direct renderer request has zero queue and return latency.
The independent [expected answers](../scripts/fixtures/cpu_surface_report_expected.json)
are calculated by hand: accumulated total request latency is 260 ticks, the
request interval union is 180 ticks, and the first-to-last request span is
220 ticks. Queue sums to 160 ticks and service to 80 ticks. These quantities
are deliberately different so swapped phases or overlap mistakes fail tests.

The `[110,300)` example has one fully contained request, one crossing request
and one excluded request. Only `[120,260)` contributes statistics: queue 100,
service 30, return 10 and total 140 ticks. No part of the crossing request is
added to distributions, counters or coverage.

For private use, replace the fixture path with an existing log path. Output is
stdout only; shell redirection is optional and must use a different path from
the input (the shell truncates a redirection target before starting Python).

## Validation and observation windows

Unrelated lines are ignored. A line containing `[CPUSURFACE_PROFILE` is a
candidate, including a truncated marker. Candidates must contain exactly one
complete marker followed by all 24 current fields, once each. An optional log
prefix before the marker is accepted. Field order is immaterial. Unknown,
duplicate, missing or malformed fields fail with `PATH:LINE: diagnostic` on
stderr and exit status 2, without a partial report. A complete final record
does not require a trailing newline. Native ASCII fields are read as UTF-8
(optional BOM); undecodable bytes are replaced and fail validation if present
in candidate fields. UTF-16 logs must be converted before use.

`address` requires eight hexadecimal digits; other fields require unsigned
decimal integers within their native 32-bit or 64-bit ranges. Frequency must
be positive and identical across **all** records, including excluded ones.
Boundaries must satisfy `queued_at <= started_at <= finished_at <= returned_at`.
Queue, service, return and total must match their boundary differences. The
five measured stages plus `other` must sum exactly to service; `hits + copies`
must not exceed `matched`, matching the production contract. Zero-duration
service (including shutdown completion) and zero queue/return are valid.

`--window START END` uses absolute integer QPC ticks, with
`0 <= START < END < 2^64`; it is half open, `[START,END)`. Classification uses
each request's lifetime `[queued_at,returned_at)`:

- **Fully contained:** `START <= queued_at < END` and `returned_at <= END`.
  A request finishing exactly at END is complete within the window.
- **Boundary crossing:** intersects the window but is not fully contained.
- **Excluded:** does not intersect the window. A request returning exactly at
  START or starting exactly at END is excluded.

A zero-duration request is contained only if its timestamp is in the window.
Without `--window`, all validated records are fully contained; an observation
window is not inferred from the log. Emission order may differ from queue or
return order, and aggregation does not assume sorted log timestamps.

## Output contract (JSON schema version 1)

Text prints status, clock, window, classification counts, percentile convention,
a latency table in milliseconds, counters, coverage and interpretation notes.
JSON has these fields in a fixed order; it omits paths and generation times:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1`. |
| `status` | `ok`, `no_records`, or `no_fully_contained_requests`. |
| `frequency_hz` | Common integer ticks/s; `null` when no records exist. |
| `observation_window` | `null` without an explicit window; otherwise `start_tick`, `end_tick`, `duration_ticks`, `duration_ms`. The last is `null` if the frequency is unknown. |
| `counts` | Integers `records`, `fully_contained`, `boundary_crossing`, `excluded`; the last three sum to `records`. |
| `percentile_method` | Nearest rank: sort ascending and select `ceil(p*n)-1` (zero based), p50 = 0.50, p95 = 0.95. No interpolation; for two samples p50 is the smaller one. |
| `latencies` | Entries `queue`, `service`, `return`, `total`, `allocation`, `prior_fence`, `commands`, `copy_fence`, `packing`, `other`. Each has `count`, `ticks` and `ms`; both unit objects contain `accumulated_request_latency`, `p50`, `p95`, `max`. An entry is `null` when there are no fully contained requests. |
| `counters` | Sums of `bytes` (requested bytes), `matched`, `hits`, `copies`, `host_bytes`, `guest_bytes` for fully contained requests; `null` without any. Surface copy byte counts can exceed requested bytes. |
| `coverage` | `ticks` and `ms`, each with `request_span` (earliest queue to latest return, including gaps) and `request_interval_union` (union of whole contained request lifetimes, excluding gaps). `null` without contained requests. Neither measures CPU execution. |
| `notes` | Interpretation statements, also printed in text. |

All validation, subtraction, summation, interval merging and percentile selection
use integer ticks. Conversion to milliseconds happens last, as `ticks*1000/frequency`.
JSON retains exact integer ticks; milliseconds are floating point and text rounds
them to six decimal places. Consumers requiring exact large QPC integers must use
an integer-preserving JSON parser.

The six service stages are disjoint within a request. `prior_fence` and
`copy_fence` include host signaling, waiting and status checks; they are not
exclusive GPU execution. Percentiles of individual stages need not sum to a
service percentile because their ranks can refer to different requests.

Sums are **accumulated request latency**; concurrent requests can overlap. The
interval union measures wall-clock coverage of observed requests. Neither sums,
union, span nor request cadence establishes exclusive CPU time, GPU utilization,
game FPS, native gameplay or stable 30 FPS. Coverage is limited to emitted
records; absence is explicit and is not a zero-cost result. Successful empty
reports exit 0 with null statistics; invalid data, arguments and I/O failures
exit 2. Consumers can use `status` to require usable samples.

The standalone regression command above is intended for separate CI integration.
It covers the authored serial/overlap oracle, direct and zero-service requests,
half-open windows, large QPC values, percentile ranks, malformed/truncated records,
accounting/frequency errors, empty observations and deterministic read-only CLI
output. It requires no third-party dependencies and does not change shared CI.

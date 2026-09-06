"""Verified threshold feedback and session accounting, independent of Tk/hardware."""
from collections import deque
from dataclasses import dataclass, field
from statistics import median
import math
import uuid


class FineTuner:
    def __init__(self):
        self.reset()

    def reset(self):
        self.samples = deque(maxlen=16)
        self.context = None
        self.value = None
        self.last_frame = None
        self.last_time = None
        self.wait_until = 0
        self.last_direction = 0
        self.status = 'Waiting for alignment samples'

    def skip(self, reason):
        self.samples.clear()
        self.status = reason

    def observe(self, frame, offset_pct, value, context, now=None):
        if context != self.context or value != self.value:
            self.reset()
            self.context, self.value = context, value
            self.wait_until = frame + 5
        if self.last_frame is not None and frame <= self.last_frame:
            return None
        if self.last_frame is not None and frame - self.last_frame > 15:
            self.samples.clear()
        if now is not None and self.last_time is not None and now - self.last_time > 60:
            self.samples.clear()
        self.last_time = now
        self.last_frame = frame
        if frame < self.wait_until:
            self.status = f'Settling settings: {self.wait_until - frame} frames'
            return None
        if offset_pct is None or not math.isfinite(offset_pct) or abs(offset_pct) > 25:
            self.skip('Skipped uncertain or extreme offset')
            return None
        self.samples.append(offset_pct)
        if len(self.samples) < 8:
            self.status = f'Alignment samples: {len(self.samples)}/8'
            return None
        bias = median(self.samples)
        direction = 1 if bias > 0 else -1
        reversal = self.last_direction and direction != self.last_direction
        required = 16 if reversal else 8
        deadband = 1.5 if reversal else 1.0
        consistent = sum(direction * x >= deadband for x in self.samples)
        if len(self.samples) < required or abs(bias) < deadband or consistent < math.ceil(len(self.samples) * 0.75):
            self.status = f'Watching bias {bias:+.2f}% ({len(self.samples)} samples)'
            return None
        proposed = max(5, min(95, value + direction))
        if proposed == value:
            self.status = f'At limit {value}; bias {bias:+.2f}%'
            return None
        return dict(value=proposed, previous=value, bias_pct=bias,
                    samples=len(self.samples), direction=direction, frame=frame)

    def applied(self, proposal, success):
        self.samples.clear()
        self.wait_until = proposal['frame'] + 20
        if success:
            self.value = proposal['value']
            self.last_direction = proposal['direction']
            self.status = f"Trim {self.value}; waiting 20 frames"
        else:
            self.status = 'Trim send failed; waiting before collecting new evidence'


@dataclass
class FrameRecord:
    number: int
    started: float
    origin: str = 'unobserved'
    settings: dict = field(default_factory=dict)
    offset_pct: float | None = None
    arrival: str = 'unmeasured'
    confirmed: bool = False
    diagnostic: dict | None = None
    source: str = ''
    tolerance_pct: float = 0
    nudges: int = 0
    nudge_steps: int = 0
    paused: bool = False
    accepted_as_is: bool = False
    recovery_started: bool = False
    recovery_failed: bool = False
    recovered: bool = False
    recovery_steps: int = 0
    captured_at: float | None = None


class AlignmentStatistics:
    def __init__(self):
        self.started = None
        self.stopped = None
        self.records = {}
        self.run_id = None

    def start(self, now):
        self.started, self.stopped = now, None
        self.records = {}
        self.run_id = uuid.uuid4().hex[:12]

    def stop(self, now):
        if self.started is not None and self.stopped is None:
            self.stopped = now

    def frame(self, number, now, origin=None, settings=None):
        if number not in self.records:
            self.records[number] = FrameRecord(number, now, origin or 'unobserved', settings or {})
        return self.records[number]

    def arrival(self, number, now, offset_pct, tolerance, confirmed, source):
        record = self.frame(number, now)
        if record.arrival != 'unmeasured':
            return False  # Retry checks do not rewrite the original arrival.
        record.offset_pct = offset_pct
        record.confirmed = confirmed
        record.source = source
        record.tolerance_pct = tolerance
        record.arrival = ('unknown' if offset_pct is None else
                          'aligned' if abs(offset_pct) <= tolerance else
                          'undershoot' if offset_pct > 0 else 'overshoot')
        return True

    def captured(self, number, now):
        record = self.frame(number, now)
        if record.captured_at is None:
            record.captured_at = now

    @staticmethod
    def summarize(records, elapsed):
        pt = [r for r in records if r.origin == 'pt']
        offsets = [r.offset_pct for r in pt if r.offset_pct is not None]
        absolute = sorted(abs(x) for x in offsets)
        pairs = [r.diagnostic for r in pt if r.diagnostic is not None]
        deltas = [abs(p['delta_pct']) for p in pairs if p['delta_pct'] is not None]
        count = lambda name: sum(r.arrival == name for r in pt)
        captured = sum(r.captured_at is not None for r in records)
        return dict(positions=len(records), pt_arrivals=len(pt), captured=captured,
            aligned=count('aligned'), aligned_pct=100 * count('aligned') / len(pt)
                if pt and any(r.arrival != 'unmeasured' for r in pt) else None,
            undershoots=count('undershoot'), overshoots=count('overshoot'), unknown=count('unknown'),
            unmeasured=count('unmeasured'), confirmed=sum(r.confirmed for r in pt),
            diagnostic_pairs=len(pairs),
            diagnostic_disagreements=sum(p['agrees'] is False for p in pairs),
            diagnostic_unknown=sum(p['agrees'] is None for p in pairs),
            diagnostic_median_delta_pct=median(deltas) if deltas else None,
            median_offset_pct=median(offsets) if offsets else None,
            p95_absolute_pct=absolute[math.ceil(len(absolute) * .95) - 1] if absolute else None,
            corrections=sum(r.nudges > 0 for r in records), nudges=sum(r.nudges for r in records),
            nudge_steps=sum(r.nudge_steps for r in records), pauses=sum(r.paused for r in records),
            accepted_as_is=sum(r.accepted_as_is for r in records),
            recovery_attempts=sum(r.recovery_started for r in records),
            recovery_failures=sum(r.recovery_failed for r in records),
            recovered=sum(r.recovered for r in records),
            recovery_steps=sum(r.recovery_steps for r in records),
            tolerance_pct=sorted({r.tolerance_pct for r in pt if r.arrival != 'unmeasured'}),
            fine_tune_values=sorted({r.settings['fine_tune'] for r in pt if 'fine_tune' in r.settings}),
            steps_values=sorted({r.settings['steps'] for r in pt if 'steps' in r.settings}),
            elapsed_s=round(elapsed, 3), effective_fps=captured / elapsed if elapsed > 0 else None)

    def snapshot(self, now):
        end = self.stopped if self.stopped is not None else now
        elapsed = max(0, end - self.started) if self.started is not None else 0
        records = list(self.records.values())
        recent = records[-100:]
        # Include transport into the first recent frame and any subsequent pause.
        previous = records[-101].captured_at if len(records) > 100 else self.started
        recent_start = previous if previous is not None else (recent[0].started if recent else end)
        return dict(run_id=self.run_id, active=self.started is not None and self.stopped is None,
                    first_frame=records[0].number if records else None,
                    last_frame=records[-1].number if records else None,
                    recent=self.summarize(recent, max(0, end - recent_start)),
                    session=self.summarize(records, elapsed))


def format_statistics(snapshot):
    recent, session = snapshot['recent'], snapshot['session']
    def number(value, suffix='', signed=False):
        return '—' if value is None else (f'{value:+.2f}' if signed else f'{value:.2f}') + suffix
    rows = [('PT arrivals', 'pt_arrivals'), ('Undershoots', 'undershoots'), ('Overshoots', 'overshoots'),
            ('Unknown detections', 'unknown'), ('Not measured', 'unmeasured'),
            ('Two-exposure confirmations', 'confirmed'), ('Frames corrected', 'corrections'),
            ('Nudges', 'nudges'), ('Correction steps', 'nudge_steps'), ('Frames paused', 'pauses'),
            ('Recovery attempts', 'recovery_attempts'), ('Recovered frames', 'recovered'),
            ('Recovery steps', 'recovery_steps'),
            ('Failed recoveries', 'recovery_failures'), ('Saved as-is', 'accepted_as_is'), ('Captured', 'captured')]
    text = [f'{"":27} {"Last 100":>11} {"Session":>11}',
            f'{"Aligned on arrival":27} {number(recent["aligned_pct"], "%"):>11} {number(session["aligned_pct"], "%"):>11}',
            f'{"Median offset":27} {number(recent["median_offset_pct"], "%", True):>11} {number(session["median_offset_pct"], "%", True):>11}',
            f'{"95th percentile |offset|":27} {number(recent["p95_absolute_pct"], "%"):>11} {number(session["p95_absolute_pct"], "%"):>11}']
    text.extend(f'{label:27} {recent[key]:>11} {session[key]:>11}' for label, key in rows)
    text.append(f'{"Effective frames/sec":27} {number(recent["effective_fps"]):>11} {number(session["effective_fps"]):>11}')
    return '\n'.join(text)

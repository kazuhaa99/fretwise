"""Turns a stream of transcribed notes into a guitar tab a human can actually play.

The hard constraint this module exists to enforce: a chord voicing is only ever
emitted if every note lands on a distinct string and the fretted notes fit within
a comfortable hand-stretch (`max_fret_span`). Combinations a hand can't form — the
"1-3-15-17-18 in one chord" the project is explicitly trying to avoid — are never
candidates in the first place, not heuristically discouraged.

Beyond that hard filter, a Viterbi-style dynamic program picks, across the whole
piece, the sequence of voicings that keeps the fretting hand in roughly one place
as long as possible (minimizing position jumps) while favoring low frets and open
strings — the things that make a transcribed tab feel idiomatic rather than
note-perfect-but-unplayable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from .fretboard import Fretboard

# Cap on how many candidate voicings we carry forward per chord. The span
# constraint already prunes heavily; this just bounds worst-case DP cost for
# unusual inputs (e.g. dense clusters of near-simultaneous notes).
MAX_VOICINGS_PER_EVENT = 48


@dataclass
class Note:
    """A single transcribed note (e.g. straight from Basic Pitch)."""
    pitch_midi: int
    onset: float
    duration: float
    confidence: float = 1.0
    # Frame-by-frame pitch offset from `pitch_midi` in semitones, and its frame
    # rate — Basic Pitch's pitch-bend contour (see `expression.py`).
    pitch_bends: list[float] | None = None
    contour_fps: float = 0.0
    # Filled in by `expression.annotate_expressions`.
    expression: str | None = None  # "bend" | "vibrato"
    bend_semitones: float = 0.0
    bend_release: bool = False


@dataclass
class TabNote:
    string: int
    fret: int
    technique: str | None = None
    # Only meaningful for technique == "bend": how far the string is bent (in
    # semitones, 1.0 = half-step, 2.0 = full) and whether it comes back down.
    bend_semitones: float | None = None
    bend_release: bool = False


@dataclass
class TabEvent:
    time: float
    duration: float
    notes: list[TabNote] = field(default_factory=list)


Voicing = tuple[tuple[int, int], ...]  # ((string, fret), ...) — one entry per pitch, in pitch order


# A sustained tone's energy wobbles as it rings out (decay, vibrato, harmonic
# beating), and that occasionally drags a pitch tracker's confidence below
# threshold for a frame or two — chopping one note into several. Those
# fragments butt up against each other with essentially no gap, and the wobble
# that caused the split lands at noise-driven, irregular instants. Genuine
# repeated articulations (tremolo, deliberate restrikes), by contrast, are
# mechanically timed: regular intervals with a real, deliberate gap between
# attacks. Regularity is therefore the signal that tells them apart — not gap
# size alone, which can look similar at a glance for both.
_RUN_GAP_MAX = 0.15  # max gap between same-pitch notes to consider them one cluster
_SUSTAIN_GAP_MAX = 0.035  # gaps this tiny mean "no real silence" between fragments
_SUSTAIN_IOI_CV_MIN = 0.25  # boundary timing this irregular reads as model noise


def _is_oversegmented_sustain(run: list[Note]) -> bool:
    gaps = [run[i + 1].onset - (run[i].onset + run[i].duration) for i in range(len(run) - 1)]
    if any(gap > _SUSTAIN_GAP_MAX for gap in gaps):
        return False
    if len(run) == 2:
        return True  # no rhythm to assess; a near-contiguous pair reads as one tone, briefly dipped
    iois = [run[i + 1].onset - run[i].onset for i in range(len(run) - 1)]
    mean_ioi = sum(iois) / len(iois)
    if mean_ioi <= 0:
        return False
    variance = sum((ioi - mean_ioi) ** 2 for ioi in iois) / len(iois)
    return (variance ** 0.5) / mean_ioi >= _SUSTAIN_IOI_CV_MIN


def _merge_oversegmented_sustains(notes: list[Note]) -> list[Note]:
    """Collapse runs of identical-pitch fragments that are really one held tone
    the transcriber chopped up (see module-level note on the heuristic above)
    into a single note spanning the full sustain. Letting the merged note's
    real duration flow into `group_into_events` and on through `gp_export`
    (whose grid already notates a multi-slot sustain as tied continuation
    notes) is what turns a "wall of repeated numbers" into one note that rings.

    Runs that *don't* look over-segmented are passed through untouched — by the
    time they reach `_annotate_techniques`, the only remaining same-pitch runs
    are genuine tremolo or deliberate restrikes, which is exactly what that
    pass (now armed with string/fret information this one doesn't have yet)
    is positioned to tell apart.
    """
    ordered = sorted(notes, key=lambda n: n.onset)
    merged: list[Note] = []
    run: list[Note] = []

    def flush() -> None:
        if not run:
            return
        if len(run) > 1 and _is_oversegmented_sustain(run):
            first, last = run[0], run[-1]
            merged_note = Note(
                pitch_midi=first.pitch_midi,
                onset=first.onset,
                duration=max(last.onset + last.duration - first.onset, first.duration),
                confidence=max(n.confidence for n in run),
                contour_fps=first.contour_fps,
            )
            _merge_expressions(merged_note, run)
            merged.append(merged_note)
        else:
            merged.extend(run)

    for note in ordered:
        if run and (note.pitch_midi != run[-1].pitch_midi
                    or note.onset - (run[-1].onset + run[-1].duration) > _RUN_GAP_MAX):
            flush()
            run = []
        run.append(note)
    flush()

    merged.sort(key=lambda n: n.onset)
    return merged


def _merge_expressions(merged: Note, run: list[Note]) -> None:
    """Carry bend/vibrato information into a note stitched from fragments.

    Vibrato is a classic cause of over-segmentation (the wobble drags the
    tracker's confidence up and down), and each fragment alone is often too
    short to show it. So when every fragment has a contour, the joined contour
    is re-classified as one note; otherwise the strongest expression any
    fragment carried wins (a bend over vibrato over nothing).
    """
    from .expression import apply_expression, classify_contour  # local: expression imports this module

    if merged.contour_fps and all(n.pitch_bends for n in run):
        # Fragments are relative to their own pitch, which is the same for the
        # whole run, so their contours concatenate directly.
        merged.pitch_bends = [value for n in run for value in (n.pitch_bends or [])]
        apply_expression(merged, classify_contour(merged.pitch_bends, merged.contour_fps))
        if merged.expression:
            return
    bends = [n for n in run if n.expression == "bend"]
    if bends:
        strongest = max(bends, key=lambda n: n.bend_semitones)
        merged.expression, merged.bend_semitones, merged.bend_release = "bend", strongest.bend_semitones, strongest.bend_release
    elif any(n.expression == "vibrato" for n in run):
        merged.expression = "vibrato"


_ONSET_EPSILON = 0.045  # how close two onsets must be to read as "the same strike"


def _cluster_by_onset(notes: list[Note], epsilon: float = _ONSET_EPSILON) -> list[list[Note]]:
    """Group notes whose onsets fall within `epsilon` of the cluster's first note.

    Shared building block for anything that needs to reason about "what sounds
    at once" — tab notation fundamentally represents simultaneous strikes, and
    so does the playability question voice-separation has to answer (can one
    instrument actually sound this combination together?).
    """
    ordered = sorted(notes, key=lambda n: n.onset)
    clusters: list[list[Note]] = []
    bucket: list[Note] = []

    for note in ordered:
        if bucket and note.onset - bucket[0].onset > epsilon:
            clusters.append(bucket)
            bucket = []
        bucket.append(note)
    if bucket:
        clusters.append(bucket)
    return clusters


def group_into_events(notes: list[Note], onset_epsilon: float = _ONSET_EPSILON) -> list[tuple[float, float, list[int]]]:
    """Cluster notes with near-simultaneous onsets into chord/strike events.

    Returns (time, duration, [midi_pitch, ...]) tuples sorted by time, with exact
    pitch duplicates within an event collapsed (a transcription model occasionally
    fires twice on the same pitch; that's not a real unison on two strings).
    """
    events: list[tuple[float, float, list[int]]] = []
    for cluster in _cluster_by_onset(notes, onset_epsilon):
        onset = min(n.onset for n in cluster)
        end = max(n.onset + n.duration for n in cluster)
        pitches = sorted(set(n.pitch_midi for n in cluster))
        events.append((onset, max(end - onset, 0.01), pitches))
    return events


def _voicings_for_pitches(fretboard: Fretboard, pitches: list[int], max_span: int) -> list[Voicing]:
    """All ways to put each pitch on its own string within a comfortable stretch.

    Open strings (fret 0) are free — they don't require a finger and don't count
    toward the span — which mirrors how guitarists actually judge "is this
    reachable". Anything wider than `max_span` among fretted notes is excluded
    outright: it is never produced as a candidate, so it can never reach the
    output regardless of how the rest of the scoring goes.
    """
    candidates_per_pitch = [fretboard.candidates(p) for p in pitches]
    if any(not c for c in candidates_per_pitch):
        return []

    results: list[Voicing] = []

    def backtrack(i: int, used_strings: frozenset[int], chosen: list[tuple[int, int]], fretted_min: int | None, fretted_max: int | None) -> None:
        if i == len(pitches):
            results.append(tuple(chosen))
            return
        for string_index, fret in candidates_per_pitch[i]:
            if string_index in used_strings:
                continue
            new_min, new_max = fretted_min, fretted_max
            if fret > 0:
                new_min = fret if fretted_min is None else min(fretted_min, fret)
                new_max = fret if fretted_max is None else max(fretted_max, fret)
                if new_max - new_min > max_span:
                    continue
            chosen.append((string_index, fret))
            backtrack(i + 1, used_strings | {string_index}, chosen, new_min, new_max)
            chosen.pop()

    backtrack(0, frozenset(), [], None, None)
    return results


def _fallback_voicings(fretboard: Fretboard, pitches: list[int], max_span: int) -> tuple[list[Voicing], list[int]]:
    """When the full chord can't physically be fretted, drop the *fewest possible*
    notes until it can. Tries every way to remove 1 note, then every way to remove
    2, etc., and returns the first subset that works — i.e. the largest playable
    subset of the original chord, not an arbitrary simplification.

    Returns (voicings, kept_pitches).
    """
    n = len(pitches)
    for remove_count in range(1, n):
        for to_remove in combinations(range(n), remove_count):
            subset = [p for idx, p in enumerate(pitches) if idx not in to_remove]
            voicings = _voicings_for_pitches(fretboard, subset, max_span)
            if voicings:
                return voicings, subset
    return [], []


def _nearest_reachable_pitch(fretboard: Fretboard, pitch: int) -> int | None:
    """If `pitch` can't be fretted on this instrument at all, shift it by whole
    octaves to the nearest one that can. Re-voicing a note an octave away when
    it falls outside the instrument's range is standard arranging practice —
    far more musical than either crashing or silently producing nothing.
    Returns None only for pitches no octave shift can rescue (practically never
    reached with real audio, but transcription models can emit stray outliers).
    """
    if fretboard.candidates(pitch):
        return pitch
    for shift in (-12, 12, -24, 24):
        shifted = pitch + shift
        if fretboard.candidates(shifted):
            return shifted
    return None


@dataclass(frozen=True)
class FingeringStyle:
    """How to choose among playable voicings of the same notes.

    The same pitches can usually be fingered in several places — an A chord
    as x-0-2-2-2-0 in open position or 5-x-7-6-5-x up the neck with the thumb
    over the top, Hendrix-style; an A-major run in 2nd or in 7th position —
    and the audio alone barely tells them apart (the difference is timbre,
    not pitch). The default leans towards open strings near the nut;
    `open_strings="avoid"` leans towards playing in position, and
    `target_position` (used when the user re-fingers a passage) puts the hand
    at a chosen fret.
    """

    open_strings: str = "auto"  # "auto" | "avoid"
    target_position: int | None = None


DEFAULT_STYLE = FingeringStyle()

# --- The hand model ----------------------------------------------------------
#
# The fretting hand sits at an *anchor* fret: the index finger's fret, with the
# other fingers covering the next three ("one finger per fret"). Notes inside
# that box cost nothing extra; a fret just outside it is a stretch; anything
# else needs the hand to move, which is what the search economises on. The
# anchor is explicit search state, so the hand can't drift up the neck one
# "small" step at a time — a run that walks up a single string pays for every
# shift, exactly like a guitarist's hand does.
_BOX = 3  # frets above the anchor reachable without stretching
# Per fret beyond the box (one fret each side allowed). Reaching back with
# the index finger is the everyday position-playing stretch; reaching up with
# the pinky is harder.
_STRETCH_DOWN_COST = 0.5
_STRETCH_UP_COST = 0.8
_SHIFT_BASE = 1.0  # any move of the hand…
_SHIFT_PER_FRET = 0.5  # …plus this per fret moved; both × position_jump_weight
_NEAR_NUT = 4  # anchors up to here count as "open position"

_OPEN_BONUS = 0.9  # "auto", hand near the nut: an open string is easier than a fretted note
_OPEN_UP_THE_NECK = 1.2  # "auto", hand higher: an open string in a position run is unidiomatic
_OPEN_PENALTY = 4.0  # "avoid": open strings only when nothing fretted fits
_TARGET_WEIGHT = 3.0  # per fret the anchor is away from the requested position
_LOW_FRET_WEIGHT = 0.25  # "auto": positions nearer the nut are a little easier
_LOW_FRET_WEIGHT_IN_POSITION = 0.02  # "avoid"/target: the position barely matters


def _anchors_for(voicing: Voicing, max_anchor: int) -> list[int]:
    """Hand anchors from which every fretted note is reachable (box ± 1 fret)."""
    fretted = [f for _, f in voicing if f > 0]
    if not fretted:
        return list(range(1, max_anchor + 1))
    low = max(1, max(fretted) - _BOX - 1)
    high = min(max_anchor, min(fretted) + 1)
    return list(range(low, high + 1))


def _voicing_cost(voicing: Voicing, anchor: int, style: FingeringStyle) -> float:
    """Cost of playing `voicing` with the hand at `anchor` (lower is easier)."""
    fretted = [f for _, f in voicing if f > 0]
    open_count = len(voicing) - len(fretted)
    in_position = style.open_strings == "avoid" or style.target_position is not None
    cost = 0.0
    if fretted:
        cost += (max(fretted) - min(fretted)) * 1.4
        cost += sum(max(0, anchor - f) for f in fretted) * _STRETCH_DOWN_COST
        cost += sum(max(0, f - (anchor + _BOX)) for f in fretted) * _STRETCH_UP_COST
        cost += anchor * (_LOW_FRET_WEIGHT_IN_POSITION if in_position else _LOW_FRET_WEIGHT)
    if open_count:
        if in_position:
            cost += open_count * _OPEN_PENALTY
        elif anchor <= _NEAR_NUT:
            cost -= open_count * _OPEN_BONUS
        else:
            cost += open_count * _OPEN_UP_THE_NECK
    if style.target_position is not None:
        cost += abs(anchor - style.target_position) * _TARGET_WEIGHT
    return cost


def _static_cost(voicing: Voicing, style: FingeringStyle) -> float:
    """Anchor-independent estimate, only for pruning huge candidate lists."""
    fretted = [f for _, f in voicing if f > 0]
    centre = min(fretted) if fretted else 1
    return _voicing_cost(voicing, max(1, centre), style)


def _prune(
    voicings: list[Voicing],
    limit: int = MAX_VOICINGS_PER_EVENT,
    style: FingeringStyle = DEFAULT_STYLE,
    max_span: int = 4,
) -> list[Voicing]:
    if len(voicings) <= limit:
        return voicings
    # Pruned by (an estimate of) the cost the search uses — otherwise the
    # voicing a style asks for (say, everything at the 5th fret) could be
    # dropped before the search ever sees it.
    return sorted(voicings, key=lambda v: _static_cost(v, style))[:limit]


def _shift_costs(anchors: int, weight: float) -> list[list[float]]:
    return [
        [0.0 if a == b else weight * (_SHIFT_BASE + abs(a - b) * _SHIFT_PER_FRET) for b in range(anchors + 1)]
        for a in range(anchors + 1)
    ]


def _best_voicing_sequence(
    candidates: list[list[Voicing]],
    position_jump_weight: float,
    style: FingeringStyle = DEFAULT_STYLE,
    max_span: int = 4,
    max_anchor: int = 19,
) -> list[Voicing]:
    """Viterbi search over (voicing, hand anchor) states.

    Minimises the sum of voicing costs (`_voicing_cost`, which depends on
    where the hand is) and hand shifts. Because the transition cost depends
    only on the anchors, the best predecessor for every anchor is computed
    once per event — O(events × (voicings × anchors + anchors²)).
    """
    n = len(candidates)
    if n == 0:
        return []
    inf = float("inf")
    max_anchor = max(1, max_anchor)
    shift = _shift_costs(max_anchor, position_jump_weight)

    # dp[i][k] maps anchor -> cost; best_prev[i][a] = (cost, k) over voicings of event i
    dp: list[list[dict[int, float]]] = []
    from_anchor: list[list[int]] = []  # for event i, anchor a: the anchor at event i-1
    best_by_anchor: list[list[tuple[float, int]]] = []

    for i, step in enumerate(candidates):
        if i == 0:
            arrive = [0.0] * (max_anchor + 1)
            arrive_from = list(range(max_anchor + 1))
        else:
            prev = best_by_anchor[i - 1]
            arrive = [inf] * (max_anchor + 1)
            arrive_from = [0] * (max_anchor + 1)
            for a in range(1, max_anchor + 1):
                for b in range(1, max_anchor + 1):
                    cost = prev[b][0] + shift[b][a]
                    if cost < arrive[a]:
                        arrive[a], arrive_from[a] = cost, b
        from_anchor.append(arrive_from)

        layer: list[dict[int, float]] = []
        best = [(inf, -1)] * (max_anchor + 1)
        for k, voicing in enumerate(step):
            costs: dict[int, float] = {}
            for a in _anchors_for(voicing, max_anchor):
                total = arrive[a] + _voicing_cost(voicing, a, style)
                costs[a] = total
                if total < best[a][0]:
                    best[a] = (total, k)
            layer.append(costs)
        dp.append(layer)
        best_by_anchor.append(best)

    # Backtrack from the cheapest final state.
    last = best_by_anchor[-1]
    anchor = min(range(1, max_anchor + 1), key=lambda a: last[a][0])
    path: list[int] = []
    for i in range(n - 1, -1, -1):
        k = best_by_anchor[i][anchor][1]
        if k < 0:  # unreachable anchor (shouldn't happen): fall back to the cheapest voicing
            k = min(range(len(candidates[i])), key=lambda j: min(dp[i][j].values(), default=inf))
        path.append(k)
        anchor = from_anchor[i][anchor]
    path.reverse()
    return [candidates[i][k] for i, k in enumerate(path)]


_LEGATO_GAP = 0.09  # seconds between notes still considered a connected phrase
# Techniques read off the pitch contour (`expression.py`) before this pass runs;
# timing heuristics never overwrite them.
_CONTOUR_TECHNIQUES = frozenset({"bend", "vibrato"})
_SLIDE_FRET_DELTA = 4  # same-string jumps at least this big read as a slide, not h/p

# A run of identical-pitch notes can mean three different things, and the only
# thing distinguishing them is *timing regularity*: a held tone the transcriber
# chopped into fragments lands at irregular, noise-driven boundaries (handled by
# `_merge_oversegmented_sustains`, before strings even exist); deliberate
# tremolo picking lands at a fast, mechanically even rate; deliberate slower
# restrikes are just... restrikes, written as plain repeated numbers.
_TREMOLO_RUN_GAP_MAX = 0.15  # mirrors `_RUN_GAP_MAX` — same "still one cluster" window
_TREMOLO_MIN_REPEATS = 4  # need an actual run, not a couple of restrikes, to call it tremolo
# Tremolo is faster than any written subdivision a song is strummed in: ~11+
# strikes per second. (It used to be 0.16 s — sixteenths at ~95 BPM — which
# labelled ordinary strummed sixteenths "tr" all over real tracks.)
_TREMOLO_MAX_MEAN_IOI = 0.09
_TREMOLO_IOI_CV_MAX = 0.30  # how evenly spaced the strikes must be to read as mechanical


def _run_is_tremolo(onsets: list[float]) -> bool:
    """True if `onsets` (already confirmed same pitch/string/fret) repeat fast and evenly."""
    if len(onsets) < _TREMOLO_MIN_REPEATS:
        return False
    iois = [onsets[i + 1] - onsets[i] for i in range(len(onsets) - 1)]
    mean_ioi = sum(iois) / len(iois)
    if not (0 < mean_ioi <= _TREMOLO_MAX_MEAN_IOI):
        return False
    variance = sum((ioi - mean_ioi) ** 2 for ioi in iois) / len(iois)
    return (variance ** 0.5) / mean_ioi <= _TREMOLO_IOI_CV_MAX


def _annotate_techniques(events: list[TabEvent]) -> None:
    """Heuristically mark hammer-ons / pull-offs / slides / tremolo on same-string runs.

    Two passes over each string's notes (already in time order, since `events`
    is): a pairwise pass connects *changing* pitches into hammer-ons, pull-offs
    or slides; a windowed pass looks for runs of the *same* pitch struck fast
    and evenly — the signature of deliberate tremolo picking, as opposed to a
    slower deliberate restrike (left as plain repeated numbers, which is exactly
    how that's notated) or one held tone the transcriber over-segmented (already
    folded into a single tied note by `_merge_oversegmented_sustains` before any
    of this runs, so it never reaches this function as a "run" at all).

    This is a simplification: it only looks at fret deltas and timing gaps between
    notes on the same string. Genuine bend/vibrato detection needs frame-level
    pitch-contour analysis of the isolated stem (e.g. CREPE on each note's
    audio span) rather than the discretized note list — a natural follow-up
    once this skeleton is in place, not something to fake here.
    """
    by_string: dict[int, list[tuple[TabNote, float, float]]] = {}
    for event in events:
        for note in event.notes:
            by_string.setdefault(note.string, []).append((note, event.time, event.duration))

    for sequence in by_string.values():
        for k in range(1, len(sequence)):
            note, time, _duration = sequence[k]
            prev_note, prev_time, prev_duration = sequence[k - 1]
            gap = time - (prev_time + prev_duration)
            if note.technique in _CONTOUR_TECHNIQUES:
                continue  # measured from the pitch contour — stronger evidence than timing
            if 0 <= gap <= _LEGATO_GAP and note.fret != prev_note.fret:
                delta = note.fret - prev_note.fret
                if abs(delta) >= _SLIDE_FRET_DELTA:
                    note.technique = "slide"
                elif delta > 0:
                    note.technique = "hammer_on"
                else:
                    note.technique = "pull_off"

        run_start = 0
        for k in range(1, len(sequence) + 1):
            continues = (
                k < len(sequence)
                and sequence[k][0].fret == sequence[k - 1][0].fret
                and sequence[k][1] - (sequence[k - 1][1] + sequence[k - 1][2]) <= _TREMOLO_RUN_GAP_MAX
            )
            if not continues:
                run = sequence[run_start:k]
                if _run_is_tremolo([time for _, time, _ in run]):
                    for run_note, _, _ in run:
                        if run_note.technique not in _CONTOUR_TECHNIQUES:
                            run_note.technique = "tremolo"
                run_start = k


# Most mixes need exactly one guitar track; splitting further multiplies the
# chance of carving a "second part" out of plain transcription noise faster
# than it adds value. Two — a rhythm-ish part and a lead-ish part playing at
# once — is the overwhelmingly common case the project explicitly cares about.
MAX_VOICES = 2

# Below this many notes, a leftover stream reads as scattered bleed-through
# (stray pitches the separator didn't fully remove), not a second performance.
_MIN_VOICE_NOTES = 12
# A real second part runs through a meaningful stretch of the piece; one that's
# confined to a short span is more likely a transcription glitch in one section.
_MIN_VOICE_COVERAGE = 0.15


def _split_by_playability(
    notes: list[Note], fretboard: Fretboard, max_span: int
) -> tuple[list[Note], list[Note]]:
    """Partition `notes` into what one guitar could sound together and what's left.

    A guitar can absolutely play chords, so simultaneous notes alone prove
    nothing — but it has exactly `fretboard.string_count` strings and one hand,
    so any *cluster* of simultaneous pitches that needs more strings than that
    or a wider stretch than `max_span` cannot be one instrument's doing. This
    reuses the exact playability rules `build_tab` enforces (via
    `_voicings_for_pitches`/`_fallback_voicings`) to decide, per cluster, which
    pitches a single fretboard could actually sound at once; whatever is left
    over is the part of the polyphony only a second instrument explains.
    """
    fits: list[Note] = []
    leftover: list[Note] = []
    for cluster in _cluster_by_onset(notes):
        pitches = sorted(set(n.pitch_midi for n in cluster))
        if _voicings_for_pitches(fretboard, pitches, max_span):
            kept_pitches = set(pitches)
        else:
            _, kept_subset = _fallback_voicings(fretboard, pitches, max_span)
            kept_pitches = set(kept_subset)
        for note in cluster:
            (fits if note.pitch_midi in kept_pitches else leftover).append(note)
    return fits, leftover


def _is_coherent_second_voice(leftover: list[Note], total_span: float) -> bool:
    """Is `leftover` substantial and spread-out enough to be a real second part,
    rather than scattered noise the source separation/transcription left behind?
    """
    if len(leftover) < _MIN_VOICE_NOTES:
        return False
    if total_span <= 0:
        return True
    span = max(n.onset + n.duration for n in leftover) - min(n.onset for n in leftover)
    return span / total_span >= _MIN_VOICE_COVERAGE


def separate_voices(notes: list[Note], fretboard: Fretboard, max_fret_span: int) -> list[list[Note]]:
    """Best-effort split of a transcribed performance into independent,
    single-guitar parts (rhythm, lead, additional guitars, ...).

    Source separation gets us one mixed "guitar" signal even when a recording
    has several real guitars playing different things at once — Demucs has no
    finer-grained stem than that, and no model does. Feeding everything to one
    fretboard then forces `build_tab` to either crush two parts into one hand's
    reach or quietly drop notes that don't fit.

    The fix is downstream of separation, not in it: ask what *one* guitar could
    plausibly have played (`_split_by_playability`), and — only when what's left
    over looks like a real, sustained second performance rather than stray
    noise (`_is_coherent_second_voice`) — treat it as its own part and arrange
    it exactly as a solo guitar would be. Anything that fits comfortably on one
    instrument (the overwhelming majority of input) comes back as a single
    voice, identical to the pre-split behavior.
    """
    if not notes:
        return [[]]

    fits, leftover = _split_by_playability(notes, fretboard, max_fret_span)
    if not fits or not leftover:
        return [notes]

    total_span = max(n.onset + n.duration for n in notes) - min(n.onset for n in notes)
    if MAX_VOICES >= 2 and _is_coherent_second_voice(leftover, total_span):
        return [fits, leftover]
    return [notes]


def build_multi_track_tab(
    notes: list[Note],
    fretboard: Fretboard,
    *,
    max_fret_span: int = 4,
    position_jump_weight: float = 1.0,
    style: FingeringStyle = DEFAULT_STYLE,
) -> list[list[TabEvent]]:
    """Split a performance into independent guitar parts and arrange each one.

    Every track goes through the exact same `build_tab` — same hard playability
    constraints, same hand-position search, same technique annotation — as if
    it were the only guitar in the mix, because once separated, that's exactly
    what it is. Empty tracks (a voice that, after arranging, produced nothing
    playable) are dropped rather than shipped as blank staves.
    """
    voices = separate_voices(notes, fretboard, max_fret_span)
    tracks = [
        build_tab(voice, fretboard, max_fret_span=max_fret_span, position_jump_weight=position_jump_weight, style=style)
        for voice in voices
    ]
    return [track for track in tracks if track]


def build_tab(
    notes: list[Note],
    fretboard: Fretboard,
    *,
    max_fret_span: int = 4,
    position_jump_weight: float = 1.0,
    style: FingeringStyle = DEFAULT_STYLE,
) -> list[TabEvent]:
    """Convert transcribed notes into a sequence of playable tab events.

    `max_fret_span` is a hard ceiling, not a preference — voicings that would
    require a wider stretch than this are never generated (see
    `_voicings_for_pitches`), so the output can never contain them.
    """
    notes = _merge_oversegmented_sustains(notes)
    clusters = _cluster_by_onset(notes)
    if not clusters:
        return []

    # Build candidate voicings per strike, then drop any strike that has none —
    # *before* running the DP. An empty candidate list at position i would
    # otherwise propagate `inf` through every later position (nothing can
    # transition out of "no valid voicing") and break the backtrack. Treating
    # an unplayable strike as a dropped note (a silent rest) is also the
    # musically sane choice: better to omit one note than to either crash or
    # smuggle an impossible shape through.
    prepared: list[tuple[float, float, list[Voicing], dict[int, Note]]] = []
    for cluster in clusters:
        time = min(n.onset for n in cluster)
        duration = max(max(n.onset + n.duration for n in cluster) - time, 0.01)
        # Which source note (and so which bend/vibrato) ends up at each
        # playable pitch, after octave-shifting out-of-range notes.
        expressive: dict[int, Note] = {}
        for n in cluster:
            shifted = _nearest_reachable_pitch(fretboard, n.pitch_midi)
            if shifted is not None and n.expression and shifted not in expressive:
                expressive[shifted] = n
        pitches = sorted(set(
            p for p in (_nearest_reachable_pitch(fretboard, n.pitch_midi) for n in cluster) if p is not None
        ))
        if not pitches:
            continue

        voicings = _voicings_for_pitches(fretboard, pitches, max_fret_span)
        if not voicings:
            voicings, _ = _fallback_voicings(fretboard, pitches, max_fret_span)
        if not voicings:
            continue

        prepared.append((time, duration, _prune(voicings, style=style, max_span=max_fret_span), expressive))

    if not prepared:
        return []

    chosen = _best_voicing_sequence(
        [voicings for _, _, voicings, _ in prepared],
        position_jump_weight,
        style,
        max_fret_span,
        max_anchor=max(1, fretboard.frets_above_capo - _BOX),
    )

    tab_events: list[TabEvent] = []
    for (time, duration, _, expressive), voicing in zip(prepared, chosen):
        notes_out = []
        for string_index, fret in voicing:
            tab_note = TabNote(string=string_index, fret=fret)
            source = expressive.get(fretboard.open_pitch(string_index) + fret)
            if source is not None:
                tab_note.technique = source.expression
                if source.expression == "bend":
                    tab_note.bend_semitones = source.bend_semitones
                    tab_note.bend_release = source.bend_release
            notes_out.append(tab_note)
        tab_events.append(TabEvent(time=time, duration=duration, notes=notes_out))

    _annotate_techniques(tab_events)
    return tab_events


# When re-fingering the user's own notes, a wider stretch is allowed before
# giving up on keeping every note: dropping notes the user put there is worse
# than an awkward shape they can still fix by hand.
_REFINGER_MAX_SPAN = 7


def refinger(
    events: list[TabEvent],
    fretboard: Fretboard,
    *,
    max_fret_span: int = 4,
    position_jump_weight: float = 1.0,
    style: FingeringStyle = DEFAULT_STYLE,
) -> list[TabEvent]:
    """Choose new strings and frets for existing events, keeping what sounds.

    Pitches, timing and techniques stay exactly as they are; only *where* each
    note is played changes, by the same search `build_tab` uses (so the same
    playability rules and hand-position economy apply), steered by `style` —
    typically "play this passage in 5th position". Used by the editor's
    "re-finger selected bars" action.
    """
    candidates: list[list[Voicing]] = []
    for event in events:
        pitches = sorted({fretboard.open_pitch(n.string) + n.fret for n in event.notes})
        voicings = _voicings_for_pitches(fretboard, pitches, max_fret_span)
        if not voicings:
            voicings = _voicings_for_pitches(fretboard, pitches, max(max_fret_span, _REFINGER_MAX_SPAN))
        if not voicings:
            # Nothing better exists: keep the event exactly as the user had it.
            voicings = [tuple(sorted(((n.string, n.fret) for n in event.notes), key=lambda sf: fretboard.open_pitch(sf[0]) + sf[1]))]
        candidates.append(_prune(voicings, style=style, max_span=max_fret_span))

    chosen = _best_voicing_sequence(
        candidates, position_jump_weight, style, max_fret_span, max_anchor=max(1, fretboard.frets_above_capo - _BOX)
    )

    out: list[TabEvent] = []
    for event, voicing in zip(events, chosen):
        by_pitch = {fretboard.open_pitch(n.string) + n.fret: n for n in event.notes}
        notes_out = []
        for string_index, fret in voicing:
            source = by_pitch.get(fretboard.open_pitch(string_index) + fret)
            notes_out.append(TabNote(
                string=string_index,
                fret=fret,
                technique=source.technique if source else None,
                bend_semitones=source.bend_semitones if source else None,
                bend_release=source.bend_release if source else False,
            ))
        out.append(TabEvent(time=event.time, duration=event.duration, notes=notes_out))
    return out

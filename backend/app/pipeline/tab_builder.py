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


@dataclass
class TabNote:
    string: int
    fret: int
    technique: str | None = None


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
            merged.append(Note(
                pitch_midi=first.pitch_midi,
                onset=first.onset,
                duration=max(last.onset + last.duration - first.onset, first.duration),
                confidence=max(n.confidence for n in run),
            ))
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


def _voicing_position(voicing: Voicing) -> float:
    """A representative fret for the fretting-hand position this voicing implies."""
    fretted = [f for _, f in voicing if f > 0]
    return (sum(fretted) / len(fretted)) if fretted else 0.0


def _voicing_ease_cost(voicing: Voicing) -> float:
    """Lower is easier: rewards open strings and tight, low-fret shapes."""
    fretted = [f for _, f in voicing if f > 0]
    open_count = sum(1 for _, f in voicing if f == 0)
    span = (max(fretted) - min(fretted)) if fretted else 0
    avg_fret = (sum(fretted) / len(fretted)) if fretted else 0.0
    return span * 1.4 + avg_fret * 0.25 - open_count * 0.9


def _prune(voicings: list[Voicing], limit: int = MAX_VOICINGS_PER_EVENT) -> list[Voicing]:
    if len(voicings) <= limit:
        return voicings
    return sorted(voicings, key=_voicing_ease_cost)[:limit]


def _best_voicing_sequence(
    candidates: list[list[Voicing]],
    position_jump_weight: float,
) -> list[Voicing]:
    """Viterbi search: minimize sum of (ease cost + weighted hand-position jumps)."""
    n = len(candidates)
    if n == 0:
        return []

    inf = float("inf")
    positions = [[_voicing_position(v) for v in step] for step in candidates]
    ease = [[_voicing_ease_cost(v) for v in step] for step in candidates]

    dp: list[list[float]] = [[inf] * len(candidates[i]) for i in range(n)]
    backptr: list[list[int]] = [[-1] * len(candidates[i]) for i in range(n)]

    for k in range(len(candidates[0])):
        dp[0][k] = ease[0][k]

    for i in range(1, n):
        for k in range(len(candidates[i])):
            best_cost, best_prev = inf, -1
            for j in range(len(candidates[i - 1])):
                if dp[i - 1][j] == inf:
                    continue
                jump = abs(positions[i][k] - positions[i - 1][j]) * position_jump_weight
                cost = dp[i - 1][j] + jump + ease[i][k]
                if cost < best_cost:
                    best_cost, best_prev = cost, j
            dp[i][k] = best_cost
            backptr[i][k] = best_prev

    last_step = dp[n - 1]
    end_idx = min(range(len(last_step)), key=lambda k: last_step[k])
    path = [end_idx]
    for i in range(n - 1, 0, -1):
        path.append(backptr[i][path[-1]])
    path.reverse()

    return [candidates[i][path[i]] for i in range(n)]


_LEGATO_GAP = 0.09  # seconds between notes still considered a connected phrase
_SLIDE_FRET_DELTA = 4  # same-string jumps at least this big read as a slide, not h/p

# A run of identical-pitch notes can mean three different things, and the only
# thing distinguishing them is *timing regularity*: a held tone the transcriber
# chopped into fragments lands at irregular, noise-driven boundaries (handled by
# `_merge_oversegmented_sustains`, before strings even exist); deliberate
# tremolo picking lands at a fast, mechanically even rate; deliberate slower
# restrikes are just... restrikes, written as plain repeated numbers.
_TREMOLO_RUN_GAP_MAX = 0.15  # mirrors `_RUN_GAP_MAX` — same "still one cluster" window
_TREMOLO_MIN_REPEATS = 3  # need an actual run, not a one-off restrike, to call it tremolo
_TREMOLO_MAX_MEAN_IOI = 0.16  # roughly a sixteenth note at ~95 BPM or faster
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
        build_tab(voice, fretboard, max_fret_span=max_fret_span, position_jump_weight=position_jump_weight)
        for voice in voices
    ]
    return [track for track in tracks if track]


def build_tab(
    notes: list[Note],
    fretboard: Fretboard,
    *,
    max_fret_span: int = 4,
    position_jump_weight: float = 1.0,
) -> list[TabEvent]:
    """Convert transcribed notes into a sequence of playable tab events.

    `max_fret_span` is a hard ceiling, not a preference — voicings that would
    require a wider stretch than this are never generated (see
    `_voicings_for_pitches`), so the output can never contain them.
    """
    notes = _merge_oversegmented_sustains(notes)
    raw_events = group_into_events(notes)
    if not raw_events:
        return []

    # Build candidate voicings per strike, then drop any strike that has none —
    # *before* running the DP. An empty candidate list at position i would
    # otherwise propagate `inf` through every later position (nothing can
    # transition out of "no valid voicing") and break the backtrack. Treating
    # an unplayable strike as a dropped note (a silent rest) is also the
    # musically sane choice: better to omit one note than to either crash or
    # smuggle an impossible shape through.
    prepared: list[tuple[float, float, list[Voicing]]] = []
    for time, duration, raw_pitches in raw_events:
        pitches = sorted(set(
            p for p in (_nearest_reachable_pitch(fretboard, raw) for raw in raw_pitches) if p is not None
        ))
        if not pitches:
            continue

        voicings = _voicings_for_pitches(fretboard, pitches, max_fret_span)
        if not voicings:
            voicings, _ = _fallback_voicings(fretboard, pitches, max_fret_span)
        if not voicings:
            continue

        prepared.append((time, duration, _prune(voicings)))

    if not prepared:
        return []

    chosen = _best_voicing_sequence([voicings for _, _, voicings in prepared], position_jump_weight)

    tab_events: list[TabEvent] = []
    for (time, duration, _), voicing in zip(prepared, chosen):
        notes_out = [TabNote(string=string_index, fret=fret) for string_index, fret in voicing]
        tab_events.append(TabEvent(time=time, duration=duration, notes=notes_out))

    _annotate_techniques(tab_events)
    return tab_events

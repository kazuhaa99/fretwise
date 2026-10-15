/**
 * Automatic section labels (A, B, C…) from repeated groups of bars.
 *
 * Transcription is never exact, so two plays of the same riff rarely produce
 * identical bars: a note is missed here, a fret lands one string over there.
 * Groups of bars are therefore compared by *similarity* (Jaccard overlap of
 * "position@string:fret" tokens, averaged over the aligned bars) against a
 * threshold, not by equality.
 *
 * Labels are a reading aid, and a wrong one actively misleads — so the whole
 * thing is deliberately conservative. No labels at all unless:
 * - the piece is long enough to have structure (`minBars`);
 * - at least one group really repeats;
 * - most of the music falls into repeated groups (`minCoverage`);
 * - no group is ambiguous, i.e. close to two different sections at once.
 */

export interface SectionOptions {
  blockSize: number;
  threshold: number;
  ambiguityMargin: number;
  minNotesPerBlock: number;
  minBars: number;
  minCoverage: number;
}

export const DEFAULT_SECTION_OPTIONS: SectionOptions = {
  blockSize: 4,
  threshold: 0.6,
  ambiguityMargin: 0.12,
  minNotesPerBlock: 4,
  minBars: 8,
  minCoverage: 0.5,
};

export function barSimilarity(a: string[], b: string[]): number {
  if (a.length === 0 && b.length === 0) return 1;
  if (a.length === 0 || b.length === 0) return 0;
  const setA = new Set(a);
  const setB = new Set(b);
  let shared = 0;
  for (const token of setA) if (setB.has(token)) shared++;
  return shared / (setA.size + setB.size - shared);
}

export function blockSimilarity(a: string[][], b: string[][]): number {
  const length = Math.min(a.length, b.length);
  if (length === 0) return 0;
  let total = 0;
  for (let i = 0; i < length; i++) total += barSimilarity(a[i], b[i]);
  // A shorter (final) block can't fully match a longer one.
  return total / Math.max(a.length, b.length);
}

/**
 * Returns one entry per bar: a label ("A", "B", …) on the first bar of each
 * labelled run of blocks, otherwise null. All null when unsure.
 */
export function detectSections(bars: string[][], options: SectionOptions = DEFAULT_SECTION_OPTIONS): (string | null)[] {
  const labels: (string | null)[] = bars.map(() => null);
  if (bars.length < options.minBars) return labels;

  const blocks: { start: number; bars: string[][] }[] = [];
  for (let start = 0; start < bars.length; start += options.blockSize) {
    const chunk = bars.slice(start, start + options.blockSize);
    if (chunk.length * 2 >= options.blockSize) blocks.push({ start, bars: chunk });
  }

  const substantial = (block: { bars: string[][] }) =>
    block.bars.reduce((n, bar) => n + bar.length, 0) >= options.minNotesPerBlock;

  const clusters: { representative: number; members: number[] }[] = [];
  const clusterOf: (number | null)[] = blocks.map(() => null);

  for (let b = 0; b < blocks.length; b++) {
    if (!substantial(blocks[b])) continue;
    const scores = clusters.map((c) => blockSimilarity(blocks[b].bars, blocks[c.representative].bars));
    let best = -1;
    let second = -1;
    scores.forEach((score, i) => {
      if (best < 0 || score > scores[best]) {
        second = best;
        best = i;
      } else if (second < 0 || score > scores[second]) {
        second = i;
      }
    });
    const bestScore = best >= 0 ? scores[best] : 0;
    const secondScore = second >= 0 ? scores[second] : 0;

    if (bestScore >= options.threshold) {
      if (secondScore >= options.threshold && bestScore - secondScore < options.ambiguityMargin) {
        return labels; // could be either section — don't guess
      }
      clusters[best].members.push(b);
      clusterOf[b] = best;
    } else {
      clusterOf[b] = clusters.length;
      clusters.push({ representative: b, members: [b] });
    }
  }

  const substantialCount = blocks.filter(substantial).length;
  const repeatedBlocks = clusters.filter((c) => c.members.length >= 2).reduce((n, c) => n + c.members.length, 0);
  if (repeatedBlocks === 0 || repeatedBlocks / substantialCount < options.minCoverage) return labels;

  const letters = new Map<number, string>();
  let previous: number | null = null;
  for (let b = 0; b < blocks.length; b++) {
    const cluster = clusterOf[b];
    if (cluster === null) {
      previous = null;
      continue;
    }
    if (!letters.has(cluster)) letters.set(cluster, String.fromCharCode(65 + letters.size));
    if (cluster !== previous) labels[blocks[b].start] = letters.get(cluster)!;
    previous = cluster;
  }
  return labels;
}

/**
 * Golden exporter: runs the TypeScript `enharmonic` speller (prod) over every parity input set and writes
 * the per-note spellings the Python port must reproduce exactly.
 *
 * Run through parity/export.sh (it extracts a prod commit into a scratch dir and calls this file with prod's
 * own tsx); direct use:
 *
 *   <prod>/node_modules/.bin/tsx parity/export.mts --src <extracted prod tree> --commit <sha> \
 *       --out parity/goldens --lab-fixtures <dir> --meredith <dir> [--sets fixtures,meredith,random,traces,pure]
 *
 * Every spelling is a line-of-fifths integer (C=0, G=1, F=-1, C#=7, Db=-5; null = no read-back), one per
 * on-event / note in input order. The octave is not stored: the speller computes it from the midi alone.
 *
 * The streaming and two-pass goldens for the fixtures and random sets come from prod's own harness
 * (`test/eval/fixtures.ts` drive / onNotes / predict). Meredith's harness (`test/eval/meredith.ts`) is a
 * script, so its parser and `predict` are copied below verbatim (marked); keep them in step with prod.
 */

import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { gzipSync } from 'node:zlib';

// ── args ──────────────────────────────────────────────────────────────────────
function arg(name: string, def?: string): string {
    const i = process.argv.indexOf(`--${name}`);
    if (i >= 0 && i + 1 < process.argv.length) return process.argv[i + 1]!;
    if (def === undefined) { console.error(`missing --${name}`); process.exit(2); }
    return def;
}
const SRC = resolve(arg('src'));
const COMMIT = arg('commit');
const OUT = resolve(arg('out'));
const LAB = resolve(arg('lab-fixtures', join(process.env.HOME ?? '', 'JavaScript/enharmonic-lab/fixtures')));
const MEREDITH = resolve(arg('meredith', join(process.env.HOME ?? '', 'JavaScript/enharmonic/corpora/meredith')));
const SETS = new Set(arg('sets', 'pure,random,fixtures,meredith,traces').split(','));
const HERE = resolve(new URL('.', import.meta.url).pathname);

// ── prod imports (from the extracted tree) ────────────────────────────────────
const imp = (p: string) => import(pathToFileURL(join(SRC, p)).href);
const index = await imp('src/index.ts');
const lofM = await imp('src/lof.ts');
const intervalM = await imp('src/interval.ts');
const scoringM = await imp('src/scoring.ts');
const engineM = await imp('src/engine.ts');
const coreM = await imp('src/core.ts');
const twoPassM = await imp('src/two-pass.ts');
const kernelM = await imp('src/kernel.ts');
const fixturesM = await imp('test/eval/fixtures.ts');
const { Speller, spellTwoPass } = index;
const { CoreSpeller } = coreM;
const { lofOf } = lofM;

type Ev = { t: number; type: 'on' | 'off'; midi: number };
type Lof = number | null;
const sha256 = (b: Buffer | string) => createHash('sha256').update(b).digest('hex');
const toLof = (p: { step: string; alter: number } | null | undefined): Lof => (p ? lofOf(p) : null);

function writeGz(name: string, payload: unknown): void {
    mkdirSync(OUT, { recursive: true });
    const json = JSON.stringify(payload);
    writeFileSync(join(OUT, name), gzipSync(Buffer.from(json), { level: 9 }));
    console.log(`  wrote ${name} (${(json.length / 1e6).toFixed(2)} MB json)`);
}
function header(set: string, extra: Record<string, unknown> = {}) {
    return { format: 1, set, source: { repo: 'enharmonic', commit: COMMIT }, ...extra };
}

// ── modes ─────────────────────────────────────────────────────────────────────
/** The four shipped modes, exactly as prod's harness runs them (fixtures.ts predict). */
const MODES = ['core', 'rt', 'la', 'tp'] as const;
/** Extra option variants (random set + curated fixtures only) to cover code paths the presets skip. */
const EXTRA: Record<string, (ev: Ev[]) => Lof[]> = {
    rt_tol30: ev => fixturesM.drive(new Speller({ onsetTolerance: 30 }), ev).map(toLof),
    la_tol30: ev => fixturesM.drive(new Speller({ lookAhead: true, onsetTolerance: 30 }), ev).map(toLof),
    rt_key3: ev => fixturesM.drive(new Speller({ keyTonic: 3 }), ev).map(toLof),
    la_keym5: ev => fixturesM.drive(new Speller({ lookAhead: true, keyTonic: -5 }), ev).map(toLof),
    core_g0_r0: ev => fixturesM.drive(new CoreSpeller(0, 3, 0, 0), ev).map(toLof),
    core_dap2: ev => fixturesM.drive(new CoreSpeller(2, 3, 2, 8), ev).map(toLof),
    tp_honor: ev => spellTwoPass(fixturesM.onNotes(ev), { honorResolution: true }).map(toLof),
    tp_bwoff_m2: ev => spellTwoPass(fixturesM.onNotes(ev), { backwardLookAhead: 'off', centreMargin: 2 }).map(toLof),
    tp_nocentre: ev => spellTwoPass(fixturesM.onNotes(ev), { centreTiebreak: false }).map(toLof),
    tp_tol30: ev => spellTwoPass(fixturesM.onNotes(ev), { onsetTolerance: 30 }).map(toLof),
};
const CURATED: readonly string[] = fixturesM.FIXTURES;

function runModes(ev: Ev[], extra: boolean): Record<string, Lof[]> {
    const out: Record<string, Lof[]> = {};
    for (const m of MODES) out[m] = fixturesM.predict(m, ev).map(toLof);
    if (extra) for (const [k, f] of Object.entries(EXTRA)) out[k] = f(ev);
    return out;
}

/** loadEvents (fixtures.ts) from an arbitrary path: keep on/off, rename t_ms → t. */
function loadEventsAt(path: string): Ev[] {
    const raw = JSON.parse(readFileSync(path, 'utf8')) as { t_ms: number; type: string; midi: number }[];
    return raw.filter(e => e.type === 'on' || e.type === 'off').map(e => ({ t: e.t_ms, type: e.type as 'on' | 'off', midi: e.midi }));
}

// ── set: pure functions ───────────────────────────────────────────────────────
function exportPure(): void {
    const ns = Array.from({ length: 81 }, (_, i) => i - 40);
    const spell = Array.from({ length: 35 }, (_, i) => i - 15);
    const cs: number[] = [];
    for (let c = -30; c <= 30; c += 0.25) cs.push(c);
    cs.push(2.5, -2.5, 3.5, -3.5, 0.49999999999999994, -0.5000000000000001, 1 / 3, -1 / 3, 2 / 7, 17 / 7, -17 / 7);
    const pairs: number[][] = [];
    for (const a of spell) for (const b of spell) {
        const pa = lofM.spellingOf(a), pb = lofM.spellingOf(b);
        const iv = intervalM.intervalBetween(pa, pb), raw = intervalM.rawIntervalBetween(pa, pb);
        pairs.push([a, b, raw.quality, raw.number, intervalM.intervalLabel(iv) as unknown as number]);
    }
    const scales = [lofM.C_MAJOR, [-8, -7, -6, -5, -4, -3, -2], [6, 7, 8, 9, 10, 11, 12], [-1, 7, 1, 2, 3, -3, 5], [-15, 19, 2, 0, 11, -8, 5]];
    const midiOf: (number | string)[][] = [];
    for (const n of spell) for (const oct of [-1, 0, 4, 9, 10]) {
        const p = { ...lofM.spellingOf(n), octave: oct };
        let m: number | string; try { m = index.midiOf(p); } catch { m = 'RangeError'; }
        midiOf.push([n, oct, m, index.pitchToString(p), index.pitchClassValue(p)]);
    }
    writeGz('pure.json.gz', header('pure', {
        letter: ns.map(n => lofM.letter(n)), acc: ns.map(n => lofM.acc(n)), pitchClass: ns.map(n => lofM.pitchClass(n)),
        spellingOf: spell.map(n => { const p = lofM.spellingOf(n); return [p.step, p.alter, lofOf(p)]; }),
        candidates: Array.from({ length: 128 }, (_, m) => [...lofM.candidates(m)]),
        enharmonicCandidatesFor: Array.from({ length: 12 }, (_, m) => index.enharmonicCandidatesFor(m).map((p: { step: string; alter: number }) => `${p.step}${p.alter}`)),
        letterAt: [0, 1, 2, 3, 4, 5, 6].map(s => cs.map(c => lofM.letterAt(s, c))), letterAtCs: cs,
        collectionAt: cs.map(c => engineM.collectionAt(c).map((p: { step: string; alter: number }) => lofOf(p))),
        consonance: ns.map(a => ns.map(b => scoringM.consonance(a, b))),
        intervalScore: scales.map(sc => spell.map(n => scoringM.intervalScore(n, sc))), scales,
        intervals: pairs, midiOf,
        resolveStep: Array.from({ length: 128 }, (_, a) => [0, 1, 11, 12, 13, 59, 60, 61, 62, 127].map(b => kernelM.resolveStep(a, b))),
        resolveExactStep: Array.from({ length: 128 }, (_, a) => [0, 1, 11, 12, 13, 59, 60, 61, 62, 127].map(b => kernelM.resolveExactStep(a, b))),
        decay64: Math.pow(0.5, 1 / 64),
    }));
}

// ── set: random streams ───────────────────────────────────────────────────────
function exportRandom(): void {
    const path = join(HERE, 'inputs', 'random.json');
    const buf = readFileSync(path);
    const streams = JSON.parse(buf.toString('utf8')) as Record<string, [number, string, number][]>;
    const modes: Record<string, Record<string, Lof[]>> = {};
    const inputs: Record<string, { notes: number }> = {};
    for (const [id, raw] of Object.entries(streams)) {
        const ev: Ev[] = raw.filter(e => e[1] === 'on' || e[1] === 'off').map(e => ({ t: e[0], type: e[1] as 'on' | 'off', midi: e[2] }));
        inputs[id] = { notes: ev.filter(e => e.type === 'on').length };
        for (const [m, v] of Object.entries(runModes(ev, true))) (modes[m] ??= {})[id] = v;
    }
    writeGz('random.json.gz', header('random', { inputFile: 'parity/inputs/random.json', inputSha256: sha256(buf), inputs, modes }));
}

// ── set: lab fixtures ─────────────────────────────────────────────────────────
function exportFixtures(): void {
    const ids = readdirSync(LAB).filter(d => existsSync(join(LAB, d, 'events.json'))).sort();
    const modes: Record<string, Record<string, Lof[]>> = {};
    const inputs: Record<string, { sha256: string; notes: number }> = {};
    for (const id of ids) {
        const path = join(LAB, id, 'events.json');
        const ev = loadEventsAt(path);
        inputs[id] = { sha256: sha256(readFileSync(path)), notes: ev.filter(e => e.type === 'on').length };
        for (const [m, v] of Object.entries(runModes(ev, CURATED.includes(id)))) (modes[m] ??= {})[id] = v;
        process.stdout.write('.');
    }
    process.stdout.write('\n');
    writeGz('fixtures.json.gz', header('fixtures', { root: LAB, inputs, modes }));
}

// ── set: Meredith (copied from prod test/eval/meredith.ts; keep in step) ─────────
// BEGIN verbatim copy of meredith.ts (parseOpndv, resolveDirs, TATUM_MS, events, predict for core/rt/la/tp)
const STEP: Record<string, number> = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
interface Note { onset: number; dur: number; step: string; alter: number; midi: number; }
function parseOpndv(text: string): Note[] {
    const notes: Note[] = [];
    const re = /\(\s*(\d+)\s+"([A-G])([nsf]+)(\d+)"\s+(\d+)\s+(\d+)\s*\)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
        const onset = Number(m[1]), letter = m[2]!, acc = m[3]!, octave = Number(m[4]), dur = Number(m[5]);
        const alter = acc === 'n' ? 0 : [...acc].reduce((a, c) => a + (c === 's' ? 1 : c === 'f' ? -1 : 0), 0);
        notes.push({ onset, dur, step: letter, alter, midi: (octave + 1) * 12 + STEP[letter]! + alter });
    }
    notes.sort((a, b) => a.onset - b.onset || a.midi - b.midi);
    return notes;
}
function resolveDirs(notes: Note[], exact = false): number[] {
    const step = exact ? kernelM.resolveExactStep : kernelM.resolveStep;
    const d = new Array<number>(notes.length).fill(0);
    for (let a = 0; a < notes.length; a++) {
        let seen = 0;
        for (let b = a + 1; b < notes.length && seen < 16; b++) {
            seen++;
            d[a] = step(notes[a]!.midi, notes[b]!.midi);
            if (d[a] !== 0) break;
        }
    }
    return d;
}
const TATUM_MS = 125;
function events(notes: Note[]): { t: number; on: boolean; midi: number; i: number }[] {
    const evs: { t: number; on: boolean; midi: number; i: number }[] = [];
    notes.forEach((n, i) => {
        evs.push({ t: n.onset * TATUM_MS, on: true, midi: n.midi, i });
        evs.push({ t: (n.onset + n.dur) * TATUM_MS, on: false, midi: n.midi, i });
    });
    evs.sort((a, b) => a.t - b.t || Number(a.on) - Number(b.on) || a.midi - b.midi);
    return evs;
}
function predictMeredith(mode: 'core' | 'rt' | 'la' | 'tp', notes: Note[]): Lof[] {
    if (mode === 'tp') {
        return spellTwoPass(notes.map(n => ({ midi: n.midi, tOn: n.onset * TATUM_MS, tOff: (n.onset + n.dur) * TATUM_MS }))).map(toLof);
    }
    const s = mode === 'core' ? new CoreSpeller() : new Speller({ lookAhead: mode === 'la' });
    const dirs = mode === 'la' ? resolveDirs(notes) : null;
    const exact = mode === 'la' ? resolveDirs(notes, true) : null;
    const pred: Lof[] = new Array(notes.length).fill(null);
    const pend = new Map<number, number[]>();
    for (const e of events(notes)) {
        if (e.on) {
            s.noteOn(e.midi, { t: e.t, resolveDir: dirs ? dirs[e.i]! : 0, resolveExact: exact ? exact[e.i]! : 0 });
            (pend.get(e.midi) ?? pend.set(e.midi, []).get(e.midi)!).push(e.i);
        } else {
            const q = pend.get(e.midi);
            if (q && q.length) pred[q.shift()!] = toLof(s.getSpelling(e.midi));
            s.noteOff(e.midi, { t: e.t });
        }
    }
    return pred;
}
// END verbatim copy

function exportMeredith(): void {
    for (const variant of ['clean', 'noisy'] as const) {
        const stem = variant === 'noisy' ? 'opnd-m-noisy' : 'opnd-m';
        const dir = join(MEREDITH, stem, stem);
        if (!existsSync(dir)) { console.log(`  (skip meredith ${variant}: ${dir} not found)`); continue; }
        const files = readdirSync(dir).filter(f => f.endsWith('.opnd-m')).sort();
        const modes: Record<string, Record<string, Lof[]>> = {};
        const inputs: Record<string, { sha256: string; notes: number }> = {};
        const t0 = performance.now();
        const timing: Record<string, number> = {};
        for (const f of files) {
            const buf = readFileSync(join(dir, f));
            const notes = parseOpndv(buf.toString('utf8'));
            inputs[f] = { sha256: sha256(buf), notes: notes.length };
            for (const m of MODES) {
                const a = performance.now();
                (modes[m] ??= {})[f] = predictMeredith(m, notes);
                timing[m] = (timing[m] ?? 0) + performance.now() - a;
            }
        }
        console.log(`  meredith ${variant}: ${files.length} movements in ${((performance.now() - t0) / 1000).toFixed(1)} s ` +
            Object.entries(timing).map(([m, ms]) => `${m} ${(ms / 1000).toFixed(2)}s`).join(', '));
        writeGz(`meredith-${variant}.json.gz`, header(`meredith-${variant}`, { root: dir, tatumMs: TATUM_MS, inputs, modes, timingMs: timing }));
    }
}

// ── set: decision traces (small subset, for debugging divergences) ─────────────
type Row = number[];
function exportTraces(): void {
    const subset: { id: string; ev: Ev[] }[] = [];
    for (const id of ['mozart_k545', 'chopin_prelude_op28_no4']) {
        const p = join(LAB, id, 'events.json');
        if (existsSync(p)) subset.push({ id, ev: loadEventsAt(p) });
    }
    const streams = JSON.parse(readFileSync(join(HERE, 'inputs', 'random.json'), 'utf8')) as Record<string, [number, string, number][]>;
    for (const id of ['tonal_01', 'spread_00', 'repeats_01']) {
        subset.push({ id: `random/${id}`, ev: streams[id]!.filter(e => e[1] === 'on' || e[1] === 'off').map(e => ({ t: e[0], type: e[1] as 'on' | 'off', midi: e[2] })) });
    }
    const traces: Record<string, unknown> = {};
    for (const { id, ev } of subset) {
        for (const la of [false, true]) {
            const s = new Speller({ lookAhead: la });
            const engine = (s as unknown as { engine: { decision(): { scale: unknown[]; candidates: { c: unknown; base: number; guardDelta: number; laDelta: number; daDelta: number; vertDelta: number; sideDelta: number; leanDelta: number }[]; chosen: unknown } | null } }).engine;
            const rows: unknown[] = [];
            // Same loop as fixtures.ts drive (look-ahead buffer included), recording the decision per note-on.
            const hooked = {
                lookAhead: la,
                noteOn(midi: number, ctx: object) {
                    s.noteOn(midi, ctx);
                    const d = engine.decision();
                    rows.push(d ? [lofOf(d.chosen), d.scale.map(p => lofOf(p)), d.candidates.map(c => [lofOf(c.c), c.base, c.guardDelta, c.laDelta, c.daDelta, c.vertDelta, c.sideDelta, c.leanDelta] as Row)] : null);
                },
                noteOff: (m: number, c: object) => s.noteOff(m, c),
                getSpelling: (m: number) => s.getSpelling(m),
            };
            fixturesM.drive(hooked, ev);
            traces[`${id}|${la ? 'la' : 'rt'}`] = rows;
        }
        const tr = twoPassM.spellTwoPassTraced(fixturesM.onNotes(ev));
        traces[`${id}|tp`] = {
            forward: tr.notes.map((n: { forward: unknown }) => toLof(n.forward as never)),
            backward: tr.notes.map((n: { backward: unknown }) => toLof(n.backward as never)),
            agrees: tr.notes.map((n: { agrees: boolean }) => n.agrees),
            source: tr.notes.map((n: { source: string }) => n.source),
            foldCentre: tr.notes.map((n: { frame: { foldCentre: number } | null }) => n.frame ? n.frame.foldCentre : null),
            firstStable: tr.firstStable, lastStable: tr.lastStable,
        };
    }
    writeGz('traces.json.gz', header('traces', { traces }));
}

console.log(`exporting from ${SRC} @ ${COMMIT} → ${OUT}`);
if (SETS.has('pure')) exportPure();
if (SETS.has('random')) exportRandom();
if (SETS.has('fixtures')) exportFixtures();
if (SETS.has('meredith')) exportMeredith();
if (SETS.has('traces')) exportTraces();

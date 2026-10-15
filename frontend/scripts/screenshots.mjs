/**
 * Screenshots of the key screens, for docs/ and for design review.
 *
 * Runs the production build through `vite preview` and answers /api/* with a
 * built-in demo job (a 16-bar riff/lead piece in 4/4 at 96 BPM), so no backend,
 * model or audio file is needed. Usage:
 *
 *   npm run build && node scripts/screenshots.mjs
 *
 * Needs Playwright with Chromium: `npm i -D playwright` (or point
 * PLAYWRIGHT_MODULE at a global install). Output: ../docs/screenshots/*.png
 * and ../docs/screenshot.png (the README image).
 */
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { preview } from "vite";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(here, "../../docs/screenshots");
mkdirSync(outDir, { recursive: true });

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? "playwright");

const JOB = "0123456789abcdef0123456789abcdef";
const BPM = 96;
const BEAT = 60 / BPM;
const BAR = BEAT * 4;
const BARS = 16;

// ---- demo tab -------------------------------------------------------------
const events = [];
const at = (bar, beat) => +(bar * BAR + beat * BEAT).toFixed(4);
function riffBar(bar, variant) {
  // palm-muted eighths on the low E with a power-chord stab on beat 4
  for (let i = 0; i < 6; i++) {
    events.push({ time: at(bar, i * 0.5), duration: BEAT / 2, notes: [{ string: 0, fret: variant && i === 5 ? 3 : 0, technique: "palm_mute" }] });
  }
  events.push({ time: at(bar, 3), duration: BEAT, notes: [{ string: 0, fret: 5 }, { string: 1, fret: 7 }, { string: 2, fret: 7 }] });
}
function leadBar(bar, k) {
  const shapes = [
    [{ t: 0, s: 3, f: 7, tech: "bend", b: 2 }, { t: 1.5, s: 4, f: 8 }, { t: 2, s: 4, f: 10, tech: "vibrato" }],
    [{ t: 0, s: 3, f: 5 }, { t: 0.5, s: 3, f: 7, tech: "hammer_on" }, { t: 1, s: 4, f: 5 }, { t: 1.5, s: 4, f: 8 }, { t: 2, s: 5, f: 5 }, { t: 2.5, s: 5, f: 8, tech: "hammer_on" }, { t: 3, s: 5, f: 5, tech: "pull_off" }],
    [{ t: 0, s: 2, f: 7 }, { t: 1, s: 2, f: 12, tech: "slide" }, { t: 2, s: 3, f: 9, tech: "bend", b: 1 }, { t: 3, s: 3, f: 7 }],
    [{ t: 0, s: 1, f: 5 }, { t: 0.25, s: 1, f: 7 }, { t: 0.5, s: 2, f: 5 }, { t: 0.75, s: 2, f: 7 }, { t: 1, s: 3, f: 4 }, { t: 1.25, s: 3, f: 5 }, { t: 1.5, s: 3, f: 7 }, { t: 2, s: 2, f: 7, tech: "vibrato" }],
  ];
  for (const n of shapes[k]) {
    events.push({
      time: at(bar, n.t),
      duration: BEAT / 2,
      notes: [{ string: n.s, fret: n.f, technique: n.tech ?? null, bendSemitones: n.b ?? null }],
    });
  }
}
for (let bar = 0; bar < BARS; bar++) {
  const section = Math.floor(bar / 4) % 2; // A B A B
  if (section === 0) riffBar(bar, bar >= 8);
  else leadBar(bar, bar % 4);
}
events.sort((a, b) => a.time - b.time);

const tab = {
  tuning: ["E2", "A2", "D3", "G3", "B3", "E4"],
  capo: 0,
  tempoBpm: BPM,
  durationSeconds: BARS * BAR,
  beatsPerMeasure: 4,
  measureTimes: Array.from({ length: BARS }, (_, i) => +(i * BAR).toFixed(4)),
  beatTimes: Array.from({ length: BARS * 4 }, (_, i) => +(i * BEAT).toFixed(4)),
  tracks: [
    { name: "Guitar 1", events },
    { name: "Guitar 2", events: events.filter((_, i) => i % 3 === 0) },
  ],
};
const done = { id: JOB, stage: "done", progress: 1, message: "Готово!", etaSeconds: 0, queuePosition: null, cancelRequested: false };
const result = {
  job: done,
  title: "Кино — Группа крови (live)",
  revision: 0,
  edited: false,
  tab,
  originalAudioUrl: `/api/jobs/${JOB}/audio/original`,
  guitarAudioUrl: `/api/jobs/${JOB}/audio/guitar`,
  gp5Url: `/api/jobs/${JOB}/export.gp5`,
};

// 1 s of silence, 8 kHz mono PCM — enough for the <audio> element to load.
function silentWav(seconds = BARS * BAR) {
  const rate = 8000;
  const samples = Math.floor(rate * seconds);
  const buf = Buffer.alloc(44 + samples * 2);
  buf.write("RIFF", 0);
  buf.writeUInt32LE(36 + samples * 2, 4);
  buf.write("WAVEfmt ", 8);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);
  buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24);
  buf.writeUInt32LE(rate * 2, 28);
  buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34);
  buf.write("data", 36);
  buf.writeUInt32LE(samples * 2, 40);
  return buf;
}
const wav = silentWav();

// ---- scenarios --------------------------------------------------------------
let jobStatus = done;
async function mockApi(page) {
  await page.route("**/api/**", async (route) => {
    const url = route.request().url();
    if (url.includes("/audio/")) {
      // Answer Range requests like the real backend (Starlette FileResponse):
      // without them Chrome treats the audio as unseekable.
      const range = /bytes=(\d+)-(\d*)/.exec(route.request().headers()["range"] ?? "");
      if (!range) return route.fulfill({ status: 200, contentType: "audio/wav", headers: { "accept-ranges": "bytes" }, body: wav });
      const start = Number(range[1]);
      const end = range[2] ? Number(range[2]) : wav.length - 1;
      return route.fulfill({
        status: 206,
        contentType: "audio/wav",
        headers: { "accept-ranges": "bytes", "content-range": `bytes ${start}-${end}/${wav.length}` },
        body: wav.subarray(start, end + 1),
      });
    }
    if (url.endsWith("/result")) return route.fulfill({ json: result });
    if (url.endsWith("/events")) {
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: `data: ${JSON.stringify(jobStatus)}\n\n` });
    }
    return route.fulfill({ json: jobStatus });
  });
}

const server = await preview({ root: resolve(here, ".."), preview: { port: 4179, strictPort: true }, logLevel: "error" });
const base = "http://localhost:4179";
const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});

const DESKTOP = { width: 1280, height: 860 };
const PHONE = { width: 375, height: 812 };

async function shot(name, { viewport, scheme = "light", status = done, query = "", prepare, fullPage = false }) {
  jobStatus = status;
  const context = await browser.newContext({ viewport, colorScheme: scheme, deviceScaleFactor: 2, reducedMotion: "reduce" });
  const page = await context.newPage();
  await mockApi(page);
  await page.goto(`${base}/${query}`);
  await page.waitForLoadState("networkidle");
  if (prepare) await prepare(page);
  await page.waitForTimeout(250);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (overflow > 0) console.warn(`  ! ${name}: page scrolls horizontally by ${overflow}px`);
  await page.screenshot({ path: resolve(outDir, `${name}.png`), fullPage });
  console.log(`  ✓ ${name}`);
  await context.close();
}

const selectFile = async (page) => {
  await page.setInputFiles('[data-testid="file-input"]', { name: "Кино — Группа крови (live).mp3", mimeType: "audio/mpeg", buffer: wav });
};
const seekToBar = (bar) => async (page) => {
  await page.locator(`[data-bar="${bar}"]`).first().click({ position: { x: 30, y: 80 } });
};

const processing = { id: JOB, stage: "separating", progress: 0.58, message: "Выделяем партию гитары из микса (это самый долгий шаг)", etaSeconds: 172, queuePosition: null, cancelRequested: false };
const failed = { id: JOB, stage: "error", progress: 0, message: "Не получилось обработать файл", error: "Не удалось извлечь звук из файла:\nInvalid data found when processing input", etaSeconds: null, cancelRequested: false };

console.log("Screenshots →", outDir);
await shot("upload-desktop-light", { viewport: DESKTOP, prepare: selectFile });
await shot("upload-desktop-dark", { viewport: DESKTOP, scheme: "dark" });
await shot("upload-phone-light", { viewport: PHONE, prepare: selectFile, fullPage: true });
await shot("settings-desktop-light", {
  viewport: DESKTOP,
  prepare: async (page) => {
    await page.getByRole("button", { name: /Настройки разбора/ }).click();
    await page.getByRole("button", { name: "Свой…" }).click();
    await page.getByLabel(/Ноты открытых струн/).fill("E2 A2 H3 G3 B3 E4");
  },
  fullPage: true,
});
await shot("processing-desktop-light", { viewport: DESKTOP, status: processing, query: `?job=${JOB}` });
await shot("processing-phone-dark", { viewport: PHONE, scheme: "dark", status: processing, query: `?job=${JOB}`, fullPage: true });
await shot("error-desktop-light", { viewport: DESKTOP, status: failed, query: `?job=${JOB}` });
await shot("result-desktop-light", { viewport: DESKTOP, query: `?job=${JOB}`, prepare: seekToBar(5) });
await shot("result-desktop-dark", { viewport: DESKTOP, scheme: "dark", query: `?job=${JOB}`, prepare: seekToBar(5) });
await shot("result-phone-light", { viewport: PHONE, query: `?job=${JOB}`, prepare: seekToBar(1) });
await shot("result-phone-dark", { viewport: PHONE, scheme: "dark", query: `?job=${JOB}`, prepare: seekToBar(1) });
await shot("result-loop-desktop-light", {
  viewport: DESKTOP,
  query: `?job=${JOB}`,
  prepare: async (page) => {
    await seekToBar(4)(page);
    await page.locator('[data-bar="5"]').first().click({ position: { x: 30, y: 80 }, modifiers: ["Shift"] });
    await page.keyboard.press("KeyL");
  },
});
await shot("edit-desktop-light", {
  viewport: DESKTOP,
  query: `?job=${JOB}`,
  prepare: async (page) => {
    await page.getByRole("button", { name: "Править" }).click();
    await page.locator('[data-note="24:0"]').click();
  },
});
await shot("edit-phone-dark", {
  viewport: PHONE,
  scheme: "dark",
  query: `?job=${JOB}`,
  prepare: async (page) => {
    await page.getByRole("button", { name: "Править" }).click();
    await page.locator('[data-note="2:0"]').click();
  },
});
await shot("shortcuts-desktop-dark", {
  viewport: DESKTOP,
  scheme: "dark",
  query: `?job=${JOB}`,
  prepare: async (page) => page.keyboard.press("Shift+Slash"),
});

await browser.close();
await new Promise((r) => server.httpServer.close(r));

const { copyFileSync } = await import("node:fs");
copyFileSync(resolve(outDir, "result-desktop-light.png"), resolve(outDir, "../screenshot.png"));
console.log("README image updated: docs/screenshot.png");

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { JobResult, JobStatus } from "./types";

const JOB_ID = "0123456789abcdef0123456789abcdef";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  emit(status: JobStatus) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(status) }));
  }
}

const status = (stage: JobStatus["stage"], extra: Partial<JobStatus> = {}): JobStatus => ({
  id: JOB_ID,
  stage,
  progress: 0.5,
  message: "…",
  ...extra,
});

const result: JobResult = {
  job: status("done", { progress: 1 }),
  tab: {
    tuning: ["E2", "A2", "D3", "G3", "B3", "E4"],
    capo: 0,
    tempoBpm: 120,
    durationSeconds: 4,
    beatsPerMeasure: 4,
    measureTimes: [0, 2],
    tracks: [{ name: "Guitar", events: [{ time: 0.5, duration: 0.5, notes: [{ string: 0, fret: 3 }] }] }],
  },
  originalAudioUrl: `/api/jobs/${JOB_ID}/audio/original`,
  guitarAudioUrl: `/api/jobs/${JOB_ID}/audio/guitar`,
  gp5Url: `/api/jobs/${JOB_ID}/export.gp5`,
};

type Route = (url: string, init?: RequestInit) => Response | Promise<Response>;

function mockFetch(route: Route) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => route(String(input), init));
  vi.stubGlobal("fetch", fn);
  return fn;
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function setUrl(search: string) {
  window.history.replaceState(null, "", `/${search}`);
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  HTMLMediaElement.prototype.load = vi.fn();
  HTMLMediaElement.prototype.pause = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  setUrl("");
});

describe("restoring a job from ?job=", () => {
  it("shows a finished job's result after a reload", async () => {
    setUrl(`?job=${JOB_ID}`);
    mockFetch((url) => (url.endsWith("/result") ? json(result) : json(status("done", { progress: 1 }))));
    render(<App />);

    expect(await screen.findByText("Готово — вот твоя табулатура")).toBeInTheDocument();
    expect(window.location.search).toBe(`?job=${JOB_ID}`);
  });

  it("explains when the job from the link no longer exists", async () => {
    setUrl(`?job=${JOB_ID}`);
    mockFetch(() => json({ detail: "Задача не найдена" }, 404));
    render(<App />);

    expect(await screen.findByText(/больше не существует/)).toBeInTheDocument();
    expect(screen.getByText("Построить табы")).toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  it("resumes following a job that is still processing", async () => {
    setUrl(`?job=${JOB_ID}`);
    mockFetch((url) => (url.endsWith("/result") ? json(result) : json(status("separating"))));
    render(<App />);

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.instances[0].url).toBe(`/api/jobs/${JOB_ID}/events`);

    act(() => FakeEventSource.instances[0].emit(status("done", { progress: 1 })));
    expect(await screen.findByText("Готово — вот твоя табулатура")).toBeInTheDocument();
  });

  it("ignores malformed job ids", () => {
    setUrl("?job=../../etc");
    const fetchMock = mockFetch(() => json({}));
    render(<App />);
    expect(screen.getByText("Построить табы")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("losing the backend while processing", () => {
  it("stops the stream and offers retry / another file when the server is unreachable", async () => {
    setUrl(`?job=${JOB_ID}`);
    let backendUp = true;
    mockFetch(() => {
      if (!backendUp) return Promise.reject(new TypeError("Failed to fetch"));
      return json(status("transcribing"));
    });
    render(<App />);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const es = FakeEventSource.instances[0];

    backendUp = false;
    await act(async () => {
      es.onerror?.();
    });

    expect(await screen.findByText("Связь с обработкой потеряна")).toBeInTheDocument();
    expect(es.closed).toBe(true);
    expect(screen.getByRole("button", { name: "Попробовать снова" })).toBeInTheDocument();

    backendUp = true;
    fireEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    expect(screen.queryByText("Связь с обработкой потеряна")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Загрузить другой файл" })).not.toBeInTheDocument();
  });

  it("'Загрузить другой файл' returns to an empty upload form and clears the URL", async () => {
    setUrl(`?job=${JOB_ID}`);
    mockFetch(() => json(status("error", { error: "ffmpeg не смог прочитать файл" })));
    render(<App />);
    expect(await screen.findByText("ffmpeg не смог прочитать файл")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Загрузить другой файл" }));
    expect(screen.getByText("Построить табы")).toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  it("keeps the stream open when the server is fine (EventSource reconnects itself)", async () => {
    setUrl(`?job=${JOB_ID}`);
    mockFetch(() => json(status("transcribing")));
    render(<App />);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const es = FakeEventSource.instances[0];
    await act(async () => {
      es.onerror?.();
    });
    expect(es.closed).toBe(false);
    expect(screen.queryByText("Связь с обработкой потеряна")).not.toBeInTheDocument();
  });

  it("reports a job deleted on the server", async () => {
    setUrl(`?job=${JOB_ID}`);
    let gone = false;
    mockFetch(() => (gone ? json({ detail: "Задача не найдена" }, 404) : json(status("arranging"))));
    render(<App />);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    gone = true;
    await act(async () => {
      FakeEventSource.instances[0].onerror?.();
    });
    expect(await screen.findByText(/Задача не найдена на сервере/)).toBeInTheDocument();
  });
});

describe("uploading", () => {
  it("puts the new job id into the URL", async () => {
    mockFetch(() => json(status("queued")));
    render(<App />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "riff.mp3", { type: "audio/mpeg" })] } });
    fireEvent.click(screen.getByText("Построить табы"));

    await waitFor(() => expect(window.location.search).toBe(`?job=${JOB_ID}`));
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("shows the server's message instead of raw JSON", async () => {
    mockFetch(() => json({ detail: "Файл слишком большой (лимит — 500 МБ)" }, 413));
    render(<App />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "big.mp4")] } });
    fireEvent.click(screen.getByText("Построить табы"));
    expect(await screen.findByText("Файл слишком большой (лимит — 500 МБ)")).toBeInTheDocument();
  });
});

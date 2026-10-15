import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  NETWORK_ERROR_MESSAGE,
  describeErrorResponse,
  errorMessage,
  getJobStatus,
  isNotFound,
  uploadJob,
} from "./client";
import { DEFAULT_SETTINGS } from "../types";

describe("describeErrorResponse", () => {
  it("passes through the backend's own Russian detail string", () => {
    const body = JSON.stringify({ detail: "Некорректные настройки: каподастр — значение должно быть не больше 12" });
    expect(describeErrorResponse(422, body)).toBe(
      "Некорректные настройки: каподастр — значение должно быть не больше 12",
    );
  });

  it("turns FastAPI's validation error list into one readable sentence", () => {
    const body = JSON.stringify({
      detail: [
        { type: "missing", loc: ["body", "file"], msg: "Field required" },
        { type: "missing", loc: ["body", "settings"], msg: "Field required" },
      ],
    });
    expect(describeErrorResponse(422, body)).toBe(
      "Некорректные данные запроса: файл — обязательное поле не заполнено; настройки — обязательное поле не заполнено",
    );
  });

  it("never shows raw HTML from a proxy", () => {
    const html = "<html><body><h1>413 Request Entity Too Large</h1></body></html>";
    expect(describeErrorResponse(413, html)).toBe("Файл слишком большой");
    expect(describeErrorResponse(502, "<html>Bad Gateway</html>")).toMatch(/^Ошибка сервера \(502\)/);
  });

  it("falls back to a generic message for unknown statuses and empty bodies", () => {
    expect(describeErrorResponse(418, "")).toBe("Запрос не выполнен (код 418)");
    expect(describeErrorResponse(404, JSON.stringify({ detail: "" }))).toBe("Задача не найдена");
  });
});

describe("request helpers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("throws an ApiError carrying the parsed detail and status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "Файл пуст" }), { status: 422 })),
    );
    const file = new File([""], "empty.wav");
    const error = await uploadJob(file, DEFAULT_SETTINGS).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(422);
    expect((error as ApiError).message).toBe("Файл пуст");
    expect(String((error as ApiError).message)).not.toContain("{");
  });

  it("reports an unreachable server as status 0 with a friendly message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    const error = await getJobStatus("abc").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
    expect((error as ApiError).message).toBe(NETWORK_ERROR_MESSAGE);
  });

  it("recognises a missing job", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "Задача не найдена" }), { status: 404 })),
    );
    const error = await getJobStatus("gone").catch((e: unknown) => e);
    expect(isNotFound(error)).toBe(true);
    expect(errorMessage(error, "fallback")).toBe("Задача не найдена");
    expect(errorMessage(new Error("boom"), "fallback")).toBe("fallback");
  });
});

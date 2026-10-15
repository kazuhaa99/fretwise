/** Small formatting helpers shared by the screens (Russian UI). */

export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/** Russian plural form: plural(5, ["трек", "трека", "треков"]) → "треков". */
export function plural(n: number, forms: [string, string, string]): string {
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return forms[2];
  if (last > 1 && last < 5) return forms[1];
  if (last === 1) return forms[0];
  return forms[2];
}

export function formatSize(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  if (mb < 0.1) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  return `${mb.toFixed(mb < 10 ? 1 : 0).replace(".", ",")} МБ`;
}

/** "осталось ≈ 3 мин" style estimate; deliberately coarse — it's a guess. */
export function formatEta(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds)) return null;
  if (seconds < 45) return "меньше минуты";
  const minutes = Math.round(seconds / 60);
  return `≈ ${minutes} ${plural(minutes, ["минута", "минуты", "минут"])}`;
}

/** "сегодня, 14:32" / "вчера, 09:10" / "3 окт., 18:05" */
export function formatWhen(unixSeconds: number, now = new Date()): string {
  const date = new Date(unixSeconds * 1000);
  const time = date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 24 * 3600 * 1000;
  if (date.getTime() >= startOfToday) return `сегодня, ${time}`;
  if (date.getTime() >= startOfToday - day) return `вчера, ${time}`;
  return `${date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}, ${time}`;
}

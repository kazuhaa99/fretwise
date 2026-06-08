import type { JobResult, JobStatus, TabSettings } from "../types";

const BASE = "/api";

export async function uploadJob(file: File, settings: TabSettings): Promise<JobStatus> {
  const form = new FormData();
  form.append("file", file);
  form.append("settings", JSON.stringify(settings));

  const res = await fetch(`${BASE}/jobs`, { method: "POST", body: form });
  if (!res.ok) throw new Error(`Upload failed: ${res.status} ${await res.text()}`);
  return res.json();
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  const res = await fetch(`${BASE}/jobs/${jobId}`);
  if (!res.ok) throw new Error(`Status check failed: ${res.status}`);
  return res.json();
}

export async function getJobResult(jobId: string): Promise<JobResult> {
  const res = await fetch(`${BASE}/jobs/${jobId}/result`);
  if (!res.ok) throw new Error(`Result fetch failed: ${res.status}`);
  return res.json();
}

export function jobEventsUrl(jobId: string): string {
  return `${BASE}/jobs/${jobId}/events`;
}

export function gp5DownloadUrl(jobId: string): string {
  return `${BASE}/jobs/${jobId}/export.gp5`;
}

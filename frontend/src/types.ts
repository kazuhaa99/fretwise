export type JobStage =
  | "queued"
  | "extracting_audio"
  | "separating"
  | "transcribing"
  | "arranging"
  | "exporting"
  | "done"
  | "error";

export interface JobStatus {
  id: string;
  stage: JobStage;
  progress: number; // 0..1 within the current stage
  message: string;
  error?: string | null;
}

export interface TabSettings {
  tuning: string[]; // e.g. ["E2","A2","D3","G3","B3","E4"], low to high
  capo: number;
  maxFretSpan: number; // playability strictness: comfortable hand stretch in frets
  positionJumpWeight: number; // how strongly to penalize jumping up/down the neck
}

export const DEFAULT_SETTINGS: TabSettings = {
  tuning: ["E2", "A2", "D3", "G3", "B3", "E4"],
  capo: 0,
  maxFretSpan: 4,
  positionJumpWeight: 1.0,
};

export interface TabNote {
  string: number; // 0 = lowest string, 5 = highest (standard 6-string)
  fret: number;
  technique?: "hammer_on" | "pull_off" | "slide" | "bend" | "vibrato" | "palm_mute" | "tremolo" | null;
}

export interface TabEvent {
  time: number; // seconds from start
  duration: number; // seconds
  notes: TabNote[];
}

export interface TabTrack {
  name: string; // "Guitar" for a single detected part, "Guitar 1"/"Guitar 2"/... once auto-split
  events: TabEvent[];
}

export interface TabData {
  tuning: string[];
  capo: number;
  tempoBpm: number;
  durationSeconds: number;
  tracks: TabTrack[];
}

export interface JobResult {
  job: JobStatus;
  tab: TabData;
  originalAudioUrl: string;
  guitarAudioUrl: string;
  gp5Url: string;
}

import type { ApiError, DocumentWire } from "./types";

export class ApiFailure extends Error {
  constructor(
    public status: number,
    public detail: ApiError,
  ) {
    super(detail.message);
  }
  get stale(): boolean {
    return this.status === 409 && this.detail.error === "revision_stale";
  }
  get rejected(): boolean {
    return this.status === 422 && this.detail.error === "edit_rejected";
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = { detail: await res.text() };
    }
    const d = (detail as { detail?: unknown }).detail ?? detail;
    const normalized: ApiError =
      typeof d === "object" && d !== null && "error" in d
        ? (d as ApiError)
        : { error: "http_error", message: String((d as any) ?? res.status) };
    throw new ApiFailure(res.status, normalized);
  }
  return (await res.json()) as T;
}

export const api = {
  getSeed(): Promise<DocumentWire> {
    return call("GET", "/api/scores/seed");
  },
  get(scoreId: string, rev?: string): Promise<DocumentWire> {
    return call("GET", `/api/scores/${scoreId}${rev ? `?rev=${rev}` : ""}`);
  },
  musicXmlUrl(scoreId: string, rev: string): string {
    return `/api/scores/${scoreId}/musicxml?rev=${rev}`;
  },
  setTimeSignature(
    scoreId: string,
    baseRevision: string,
    barIndex: number,
    beats: number,
    beatUnit: number,
  ): Promise<DocumentWire> {
    return call("POST", `/api/scores/${scoreId}/timesig`, {
      base_revision: baseRevision,
      bar_index: barIndex,
      beats,
      beat_unit: beatUnit,
    });
  },
  editEvent(
    scoreId: string,
    baseRevision: string,
    payload: {
      voiceId: string;
      eventId: string;
      action: "pitch" | "duration" | "move" | "toRest";
      pitches?: string[];
      quarterLength?: string;
      tuplet?: { actual: number; normal: number; unit: string };
      newStart?: string;
    },
  ): Promise<DocumentWire> {
    return call("POST", `/api/scores/${scoreId}/events`, {
      base_revision: baseRevision,
      voice_id: payload.voiceId,
      event_id: payload.eventId,
      action: payload.action,
      pitches: payload.pitches,
      quarter_length: payload.quarterLength,
      tuplet: payload.tuplet,
      new_start: payload.newStart,
    });
  },
  importMusicXml(musicxml: string, scoreId?: string): Promise<DocumentWire> {
    return call("POST", "/api/scores/import", { musicxml, score_id: scoreId });
  },
};

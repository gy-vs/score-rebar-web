import type { DocumentPayload } from "./types";

const BASE = "/api";

async function jsonOrThrow(res: Response): Promise<DocumentPayload> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  list: () => fetch(`${BASE}/scores`).then((r) => r.json()),

  createSeed: () =>
    fetch(`${BASE}/scores/seed`, { method: "POST" }).then(jsonOrThrow),

  get: (id: string) =>
    fetch(`${BASE}/scores/${id}`).then(jsonOrThrow),

  setMeter: (id: string, baseVersion: number, fromMeasure: number,
             beats: number, beatUnit: number) =>
    fetch(`${BASE}/scores/${id}/meter`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ baseVersion, fromMeasure, beats, beatUnit }),
    }).then(jsonOrThrow),

  pitch: (id: string, baseVersion: number, itemId: string,
          pitches: { step: string; alter: number; octave: number }[]) =>
    fetch(`${BASE}/scores/${id}/pitch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ baseVersion, itemId, pitches }),
    }).then(jsonOrThrow),

  duration: (id: string, baseVersion: number, itemId: string,
             duration: { num: number; den: number }) =>
    fetch(`${BASE}/scores/${id}/duration`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ baseVersion, itemId, duration }),
    }).then(jsonOrThrow),

  move: (id: string, baseVersion: number, itemId: string,
         onset: { num: number; den: number }) =>
    fetch(`${BASE}/scores/${id}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ baseVersion, itemId, onset }),
    }).then(jsonOrThrow),

  dismissProposal: (id: string, proposalId: string) =>
    fetch(`${BASE}/scores/${id}/proposals/${proposalId}/dismiss`, {
      method: "POST",
    }).then(jsonOrThrow),

  importXml: async (file: File): Promise<DocumentPayload> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${BASE}/import`, { method: "POST", body: fd });
    return jsonOrThrow(res);
  },

  exportUrl: (id: string) => `${BASE}/scores/${id}/export`,
};

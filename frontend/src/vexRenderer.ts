// VexFlow rendering of one *page* of measures.
//
// The renderer only draws the layout the server confirmed; it never edits
// time. Each note head / rest glyph group gets data attributes carrying the
// sounded event id, so clicks map back to the model selection. Slices of the
// same sounded event (possibly across barlines) are joined with ties.

import {
  Renderer, Stave, StaveNote, Formatter, Voice as VfVoice, VoiceMode,
  Tuplet, StaveTie, Beam, ModifierPosition,
} from "vexflow";
import type { Fragment, Measure, Token } from "./types";

// VexFlow resolution: 16384 ticks per whole note (4 quarter notes).
const TICKS_Q = 16384;

export interface RenderOptions {
  measures: Measure[];
  voiceIds: string[];
  voiceNames: Record<string, string>;
  selectedItemId: string | null;
  dimmedVoices: Set<string>;
  zoom: number;
}

export interface ClickTarget {
  itemId: string;
  measureIndex: number;
  voiceId: string;
}

function ticksForQL(ql: number): number {
  // exact rational-friendly scaling; caller passes fractions as number
  return Math.round((ql / 4) * TICKS_Q);
}

function vexKey(step: string, alter: number, octave: number): string {
  const acc = alter > 0 ? "#".repeat(alter) : alter < 0 ? "b".repeat(-alter) : "";
  return `${step.toLowerCase()}${acc}/${octave}`;
}

// server duration codes -> VexFlow codes (VexFlow uses digits for <= eighth)
const VF_CODE: Record<string, string> = {
  w: "w", h: "h", q: "q", e: "8", "16": "16", "32": "32",
};

function tokenDuration(token: Token, frag: Fragment): string {
  let code = frag.tupletGroupId ? "8" : VF_CODE[token.duration];
  if (token.dots > 0) code += "d".repeat(token.dots);
  return code;
}

function tokenQL(token: Token, frag: Fragment): number {
  const base: Record<string, number> = {
    w: 4, h: 2, q: 1, e: 0.5, "16": 0.25, "32": 0.125,
  };
  const b = frag.tupletGroupId ? 2 / 3 : base[token.duration];
  if (token.dots === 0) return b;
  if (token.dots === 1) return b * 1.5;
  return b * 1.75;
}

interface BuiltNote {
  vf: StaveNote;
  groupId: string;
  itemId: string;
  voiceId: string;
  measureIndex: number;
  token: Token;
  frag: Fragment;
}

interface MeasureBuild {
  stave: Stave;
  voices: { voiceId: string; vfVoice: VfVoice; beams: Beam[] }[];
}

/** Render into a host div (VexFlow creates the svg inside it);
 * return a click-decoder. */
export function renderScore(host: HTMLDivElement, opts: RenderOptions):
    (el: Element) => ClickTarget | null {
  host.innerHTML = "";
  const renderer = new Renderer(host, Renderer.Backends.SVG);
  const ctx = renderer.getContext();
  ctx.scale(opts.zoom, opts.zoom);

  const MEASURE_W = 380;
  const STAVE_H = 132;
  const PAD_L = 20;
  const PAD_T = 36;
  const PER_SYSTEM = 2;

  const rows = Math.max(1, Math.ceil(opts.measures.length / PER_SYSTEM));
  const width = PAD_L * 2 + PER_SYSTEM * MEASURE_W;
  const height = PAD_T + rows * STAVE_H;
  renderer.resize(Math.max(width, 780), Math.max(height, 200));

  const built: BuiltNote[] = [];
  const tupletBuckets = new Map<string, { vf: StaveNote }[]>();
  const builds: MeasureBuild[] = [];

  opts.measures.forEach((measure, drawIdx) => {
    const sysCol = drawIdx % PER_SYSTEM;
    const sysRow = Math.floor(drawIdx / PER_SYSTEM);
    const x = PAD_L + sysCol * MEASURE_W;
    const y = PAD_T + sysRow * STAVE_H;

    const stave = new Stave(x, y, MEASURE_W - 24);
    stave.addClef("treble").addTimeSignature(`${measure.beats}/${measure.beatUnit}`);
    stave.setText(`第 ${measure.index + 1} 小节`, ModifierPosition.ABOVE, { shift_y: -2 });
    stave.setContext(ctx).draw();

    const vfVoices: VfVoice[] = [];
    const perVoice: MeasureBuild["voices"] = [];

    opts.voiceIds.forEach((voiceId, vi) => {
      const fragments = measure.voices[voiceId] ?? [];
      const notes: StaveNote[] = [];

      fragments.forEach((frag) => {
        frag.tokens.forEach((token) => {
          const vf = makeStaveNote(frag, token);
          vf.setStemDirection(vi === 0 ? -1 : 1);
          // Scale layout ticks to the true (rational) sounded duration. The
          // glyph is still the notated value; this only sets horizontal
          // space, which is what makes a triplet visually occupy 2:3 space.
          vf.setIntrinsicTicks(ticksForQL(tokenQL(token, frag)));
          notes.push(vf);
          const groupId = vf.getAttribute("id");
          built.push({ vf, groupId, itemId: frag.itemId, voiceId,
                       measureIndex: measure.index, token, frag });
          if (token.tupletGroupId) {
            const bucket = tupletBuckets.get(token.tupletGroupId) ?? [];
            bucket.push({ vf });
            tupletBuckets.set(token.tupletGroupId, bucket);
          }
        });
      });

      const vfVoice = new VfVoice({
        num_beats: measure.beats,
        beat_value: measure.beatUnit,
      });
      // The server layout is authoritative on measure contents; avoid
      // VexFlow rejecting tick sums it computes differently (e.g. ties).
      vfVoice.setMode(VoiceMode.SOFT);
      vfVoice.addTickables(notes);
      vfVoices.push(vfVoice);

      const beams = Beam.generateBeams(notes, {
        stem_direction: vi === 0 ? -1 : 1,
        beam_rests: false,
      });
      beams.forEach((b) => b.setContext(ctx));
      perVoice.push({ voiceId, vfVoice, beams });
    });

    // all voices on the stave positioned by one formatter
    new Formatter().joinVoices(vfVoices).formatToStave(vfVoices, stave);
    vfVoices.forEach((v) => v.draw(ctx, stave));
    perVoice.forEach(({ beams }) => beams.forEach((b) => b.draw()));
    builds.push({ stave, voices: perVoice });
  });

  // ties between consecutive rendered slices of one sounded event
  const byItem = new Map<string, BuiltNote[]>();
  built.forEach((b) => {
    const arr = byItem.get(b.itemId) ?? [];
    arr.push(b);
    byItem.set(b.itemId, arr);
  });
  byItem.forEach((seq) => {
    if (seq[0].frag.kind === "rest") return;
    for (let i = 1; i < seq.length; i++) {
      const tie = new StaveTie({
        first_note: seq[i - 1].vf,
        last_note: seq[i].vf,
      });
      tie.setContext(ctx);
      try { tie.draw(); } catch { /* cross-system endpoint may be off-page */ }
    }
  });

  // tuplet brackets per contiguous run present on this page
  tupletBuckets.forEach((entries) => {
    const tup = new Tuplet(entries.map((e) => e.vf), {
      num_notes: 3,
      notes_occupied: 2,
    });
    tup.setContext(ctx);
    try { tup.draw(); } catch { /* split across pages */ }
  });

  // annotate drawn groups and apply styles. VexFlow prefixes DOM ids, so
  // match the stavenote groups in document order (they were drawn in the
  // same order we collected `built`).
  const noteGroups = host.querySelectorAll<SVGGElement>("g.vf-stavenote");
  built.forEach((b, i) => {
    const g = noteGroups[i];
    if (!g) return;
    g.setAttribute("data-item", b.itemId);
    g.setAttribute("data-measure", String(b.measureIndex));
    g.setAttribute("data-voice", b.voiceId);
    g.classList.add("vf-note");
    if (opts.dimmedVoices.has(b.voiceId)) g.classList.add("vf-dim");
    if (b.itemId === opts.selectedItemId) g.classList.add("vf-selected");
    g.style.cursor = "pointer";
  });

  void builds;
  return (el: Element) => {
    const node = el.closest("[data-item]") as Element | null;
    if (!node) return null;
    return {
      itemId: node.getAttribute("data-item")!,
      measureIndex: Number(node.getAttribute("data-measure")),
      voiceId: node.getAttribute("data-voice")!,
    };
  };
}

function makeStaveNote(frag: Fragment, token: Token): StaveNote {
  const duration = tokenDuration(token, frag);
  if (frag.kind === "rest") {
    return new StaveNote({
      keys: ["b/4"],
      duration,
      type: "r",
      align_center: token.wholeMeasure,
    });
  }
  if (frag.kind === "chord") {
    return new StaveNote({
      keys: frag.pitches.map((p) => vexKey(p.step, p.alter, p.octave)),
      duration,
    });
  }
  const p = frag.pitches[0];
  return new StaveNote({
    keys: [vexKey(p.step, p.alter, p.octave)],
    duration,
  });
}

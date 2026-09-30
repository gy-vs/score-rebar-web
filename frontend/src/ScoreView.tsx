import { useEffect, useMemo, useRef } from "react";
import {
  Renderer,
  Stave,
  StaveNote,
  Formatter,
  Voice as VfVoice,
  Tuplet,
  StaveTie,
  Accidental,
} from "vexflow";
import type { BarWire, RenderNoteWire, SliceWire } from "./types";
import { Q } from "./q";

// Map our note type -> VexFlow duration string (dots added separately).
const VF_DURATION: Record<string, string> = {
  whole: "w",
  half: "h",
  quarter: "q",
  eighth: "8",
  "16th": "16",
  "32nd": "32",
  "64th": "64",
};

function vexDuration(rn: RenderNoteWire): string {
  const base = VF_DURATION[rn.type] ?? "q";
  return base + "d".repeat(rn.dots);
}

function vexKey(sl: SliceWire, idx = 0): string {
  const p = sl.pitches[idx];
  if (!p) return "c/4";
  const acc =
    p.alter === 1 ? "#" : p.alter === 2 ? "##" : p.alter === -1 ? "b" : p.alter === -2 ? "bb" : "";
  return `${p.step.toLowerCase()}${acc}/${p.octave}`;
}

interface Built {
  note: StaveNote;
  slice: SliceWire;
  renderNote: RenderNoteWire;
}

function buildNotes(bar: BarWire, voiceIndex: number): {
  built: Built[];
  tuplets: { notes: StaveNote[] }[];
} {
  const built: Built[] = [];
  for (const sl of bar.slicesByVoice[voiceIndex]) {
    for (const rn of sl.renderNotes) {
      let note: StaveNote;
      if (sl.kind === "rest") {
        note = new StaveNote({ keys: ["b/4"], duration: `${vexDuration(rn)}r` });
      } else if (sl.kind === "chord") {
        const keys = sl.pitches.map((_, i) => vexKey(sl, i));
        note = new StaveNote({ keys, duration: vexDuration(rn) });
        sl.pitches.forEach((p, i) => {
          if (p.alter !== 0) {
            const a = accidentalChar(p.alter);
            if (a) note.addModifier(new Accidental(a), i);
          }
        });
      } else {
        note = new StaveNote({ keys: [vexKey(sl)], duration: vexDuration(rn) });
        const p = sl.pitches[0];
        if (p && p.alter !== 0) {
          const a = accidentalChar(p.alter);
          if (a) note.addModifier(new Accidental(a), 0);
        }
      }
      built.push({ note, slice: sl, renderNote: rn });
    }
  }

  // tuplet groups: consecutive render notes with a tuplet, grouped by the
  // start..stop bracket roles computed on the backend.
  const tuplets: { notes: StaveNote[] }[] = [];
  let grp: StaveNote[] = [];
  for (const b of built) {
    if (b.renderNote.tuplet) {
      grp.push(b.note);
      if (b.renderNote.tuplet.bracketRole === "stop") {
        tuplets.push({ notes: grp });
        grp = [];
      }
    } else if (grp.length) {
      tuplets.push({ notes: grp });
      grp = [];
    }
  }
  if (grp.length) tuplets.push({ notes: grp });

  return { built, tuplets };
}

function accidentalChar(alter: number): string | null {
  if (alter === 1) return "#";
  if (alter === -1) return "b";
  if (alter === 2) return "##";
  if (alter === -2) return "bb";
  return null;
}

interface Props {
  bars: BarWire[];
  clefs: string[];
  voiceCount: number;
  zoom: number;
  selectedEventId: string | null;
  onSelect: (slice: SliceWire, additive: boolean) => void;
  startBar: number;
  barsPerPage: number;
}

const STAVE_W = 760;
const STAVE_GAP = 110;

export function ScoreView({
  bars, clefs, voiceCount, zoom, selectedEventId, onSelect, startBar, barsPerPage,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);

  const visible = useMemo(
    () => bars.slice(startBar, startBar + barsPerPage),
    [bars, startBar, barsPerPage],
  );

  useEffect(() => {
    const container = ref.current;
    if (!container || visible.length === 0) return;
    container.innerHTML = "";

    const scale = zoom / 100;
    const renderer = new Renderer(container, Renderer.Backends.SVG);
    const width = STAVE_W * scale + 80;
    const height = (visible.length * voiceCount * STAVE_GAP + 60) * scale;
    renderer.resize(width, height);
    const ctx = renderer.getContext();
    ctx.scale(scale, scale);

    visible.forEach((bar, displayIdx) => {
      const barIdx = startBar + displayIdx;
      for (let v = 0; v < voiceCount; v++) {
        const x = 20;
        const y = 20 + (displayIdx * voiceCount + v) * STAVE_GAP;
        const stave = new Stave(x, y, STAVE_W);
        if (v === 0) stave.addTimeSignature(bar.timeLabel);
        if (displayIdx === 0) stave.addClef(clefs[v] === "bass" ? "bass" : "treble");
        stave.setContext(ctx).draw();

        const { built, tuplets } = buildNotes(bar, v);
        if (built.length === 0) continue;

        // VexFlow Voice tick durations: use NOTATED durations for layout so a
        // triplet group occupies its normal 1 beat.
        const vfVoice = new VfVoice({
          numBeats: bar.beats,
          beatValue: bar.beatUnit,
        });
        vfVoice.setStrict(false);
        vfVoice.addTickables(built.map((b) => b.note));
        new Formatter().joinVoices([vfVoice]).format([vfVoice], STAVE_W - 80);

        // selection highlight (whole sounding event across all its slices),
        // drawn before the notes so the noteheads stay on top.
        built.forEach((b) => {
          if (b.slice.eventId === selectedEventId) {
            const bb = b.note.getBoundingBox();
            ctx.save();
            ctx.setFillStyle("rgba(37,99,235,0.18)");
            ctx.fillRect(bb.getX() - 4, bb.getY() - 4, bb.getW() + 8, bb.getH() + 8);
            ctx.restore();
            b.note.setStyle({ fillStyle: "#1d4ed8", strokeStyle: "#1d4ed8" });
          }
        });

        vfVoice.draw(ctx, stave);

        // ties between consecutive render notes of the same sounding event;
        // when the tie leaves the bar (no next note in this bar) draw outward.
        for (let i = 0; i < built.length; i++) {
          const b = built[i];
          if (!b.renderNote.tieAfter) continue;
          const next = built[i + 1];
          if (next && next.slice.eventId === b.slice.eventId) {
            new StaveTie({
              firstNote: b.note,
              lastNote: next.note,
              firstIndexes: [0],
              lastIndexes: [0],
            }).setContext(ctx).draw();
          } else {
            new StaveTie({ firstNote: b.note, lastNote: null, firstIndexes: [0] })
              .setContext(ctx).draw();
          }
        }

        tuplets.forEach(({ notes }) => {
          if (notes.length > 1) new Tuplet(notes).setContext(ctx).draw();
        });

        // click targets
        built.forEach((b) => {
          const el = (b.note as unknown as { attr?: { el?: SVGElement } }).attr?.el;
          if (el) {
            el.style.cursor = "pointer";
            el.addEventListener("click", (ev) => {
              ev.stopPropagation();
              onSelect(b.slice, ev.shiftKey);
            });
          }
        });
        void barIdx;
      }
    });
  }, [visible, clefs, voiceCount, zoom, selectedEventId, startBar, barsPerPage, onSelect]);

  return <div ref={ref} className="score-canvas" />;
}

export { Q };

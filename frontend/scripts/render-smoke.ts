// Headless render smoke test: build the seed layout in 3/4 via the live API
// shape (hard-coded), render with VexFlow into jsdom, and assert glyph
// groups carry data-item attributes and ties/tuplets don't throw.

import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  pretendToBeVisual: true,
});
(globalThis as any).window = dom.window;
(globalThis as any).document = dom.window.document;
(globalThis as any).DOMParser = dom.window.DOMParser;
(globalThis as any).XMLSerializer = dom.window.XMLSerializer;
(globalThis as any).navigator = dom.window.navigator;
(globalThis as any).SVGElement = dom.window.SVGElement;

// jsdom does not lay out SVG: provide rough text metrics for VexFlow's
// measureText/getBBox calls.
dom.window.SVGElement.prototype.getBBox = function () {
  const text = (this.textContent ?? "").toString();
  return { x: 0, y: 0, width: text.length * 7, height: 12 };
};
dom.window.SVGElement.prototype.getComputedTextLength = function () {
  return ((this.textContent ?? "").toString().length * 7);
};

import { renderScore } from "../src/vexRenderer";

const Q = (n: number, d = 1) => ({ num: n, den: d });

const measures: any[] = [
  {
    index: 0, onset: Q(0), duration: Q(3), beats: 3, beatUnit: 4,
    voices: {
      v1: [
        { itemId: "a", kind: "note", onset: Q(0), duration: Q(2, 3),
          localOnset: Q(0), head: true, tail: true,
          pitches: [{ step: "G", alter: 0, octave: 4 }],
          tupletGroupId: "g1",
          tokens: [{ id: "a0", seq: 0, duration: "e", dots: 0, tieIn: false,
                     tieOut: false, wholeMeasure: false, tupletGroupId: "g1" }] },
        { itemId: "b", kind: "note", onset: Q(2, 3), duration: Q(2, 3),
          localOnset: Q(2, 3), head: true, tail: true,
          pitches: [{ step: "A", alter: 0, octave: 4 }],
          tupletGroupId: "g1",
          tokens: [{ id: "b0", seq: 0, duration: "e", dots: 0, tieIn: false,
                     tieOut: false, wholeMeasure: false, tupletGroupId: "g1" }] },
        { itemId: "c", kind: "note", onset: Q(4, 3), duration: Q(2, 3),
          localOnset: Q(4, 3), head: true, tail: true,
          pitches: [{ step: "B", alter: 0, octave: 4 }],
          tupletGroupId: "g1",
          tokens: [{ id: "c0", seq: 0, duration: "e", dots: 0, tieIn: false,
                     tieOut: false, wholeMeasure: false, tupletGroupId: "g1" }] },
        { itemId: "d", kind: "note", onset: Q(2), duration: Q(1),
          localOnset: Q(2), head: true, tail: false,
          pitches: [{ step: "G", alter: 0, octave: 4 }], tupletGroupId: null,
          tokens: [{ id: "d0", seq: 0, duration: "q", dots: 0, tieIn: false,
                     tieOut: true, wholeMeasure: false, tupletGroupId: null }] },
      ],
      v2: [
        { itemId: "e", kind: "note", onset: Q(0), duration: Q(3),
          localOnset: Q(0), head: true, tail: false,
          pitches: [{ step: "C", alter: 0, octave: 3 }], tupletGroupId: null,
          tokens: [{ id: "e0", seq: 0, duration: "h", dots: 1, tieIn: false,
                     tieOut: true, wholeMeasure: false, tupletGroupId: null }] },
      ],
    },
  },
  {
    index: 1, onset: Q(3), duration: Q(3), beats: 3, beatUnit: 4,
    voices: {
      v1: [
        { itemId: "d", kind: "note", onset: Q(3), duration: Q(3, 2),
          localOnset: Q(0), head: false, tail: true,
          pitches: [{ step: "G", alter: 0, octave: 4 }], tupletGroupId: null,
          tokens: [{ id: "d1", seq: 0, duration: "q", dots: 1, tieIn: true,
                     tieOut: false, wholeMeasure: false, tupletGroupId: null }] },
      ],
      v2: [
        { itemId: "e", kind: "note", onset: Q(3), duration: Q(1),
          localOnset: Q(0), head: false, tail: false,
          pitches: [{ step: "C", alter: 0, octave: 3 }], tupletGroupId: null,
          tokens: [{ id: "e1", seq: 0, duration: "q", dots: 0, tieIn: true,
                     tieOut: true, wholeMeasure: false, tupletGroupId: null }] },
        { itemId: "f", kind: "note", onset: Q(4), duration: Q(2),
          localOnset: Q(1), head: true, tail: false,
          pitches: [{ step: "C", alter: 0, octave: 3 }], tupletGroupId: null,
          tokens: [{ id: "f0", seq: 0, duration: "h", dots: 0, tieIn: false,
                     tieOut: true, wholeMeasure: false, tupletGroupId: null }] },
      ],
    },
  },
];

const host = document.createElement("div");
document.body.appendChild(host);

const decode = renderScore(host as unknown as HTMLDivElement, {
  measures,
  voiceIds: ["v1", "v2"],
  voiceNames: { v1: "上", v2: "下" },
  selectedItemId: "d",
  dimmedVoices: new Set(["v2"]),
  zoom: 1,
});

const groups = host.querySelectorAll("[data-item]");
const ids = new Set(Array.from(groups).map((g) => g.getAttribute("data-item")));
console.log("rendered groups:", groups.length);
console.log("item ids:", [...ids]);
if (groups.length < 8) throw new Error("too few glyph groups");
for (const want of ["a", "b", "c", "d", "e", "f"]) {
  if (!ids.has(want)) throw new Error(`missing glyph for ${want}`);
}
const sel = host.querySelectorAll(".vf-selected");
if (sel.length !== 2) throw new Error(`expected 2 selected slices, got ${sel.length}`);
const dim = host.querySelectorAll(".vf-dim");
if (dim.length !== 3) throw new Error(`expected 3 dimmed slices, got ${dim.length}`);
const sample = groups[0] as Element;
const target = decode(sample);
if (!target || !target.itemId) throw new Error("click decode failed");
console.log("decoded click:", target);
console.log("RENDER SMOKE OK");

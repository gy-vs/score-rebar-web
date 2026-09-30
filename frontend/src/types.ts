// Mirrors backend JSON (see app/models.py, app/layout.py).
// All times are rational quarter-lengths: { num, den }.

export interface Q {
  num: number;
  den: number;
}

export interface Pitch {
  step: string;
  alter: number;
  octave: number;
}

export interface TupletGroup {
  id: string;
  actual_notes: number;
  normal_notes: number;
  member_ids: string[];
}

export interface VoiceItem {
  id: string;
  kind: "note" | "chord" | "rest";
  onset: Q;
  duration: Q;
  pitches: Pitch[];
  tupletGroupId: string | null;
}

export interface Voice {
  id: string;
  name: string;
  items: VoiceItem[];
}

export interface MeterRegion {
  onset: Q;
  beats: number;
  beatUnit: number;
}

export interface Score {
  id: string;
  title: string;
  version: number;
  voices: Voice[];
  meters: MeterRegion[];
  tupletGroups: TupletGroup[];
}

export interface Token {
  id: string;
  seq: number;
  duration: string;
  dots: number;
  tieIn: boolean;
  tieOut: boolean;
  wholeMeasure: boolean;
  tupletGroupId: string | null;
}

export interface Fragment {
  itemId: string;
  kind: "note" | "chord" | "rest";
  onset: Q;
  duration: Q;
  localOnset: Q;
  head: boolean;
  tail: boolean;
  pitches: Pitch[];
  tupletGroupId: string | null;
  tokens: Token[];
}

export interface Measure {
  index: number;
  onset: Q;
  duration: Q;
  beats: number;
  beatUnit: number;
  voices: Record<string, Fragment[]>;
}

export interface Layout {
  measures: Measure[];
  length: Q;
}

export interface ProposalReason {
  code: string;
  message: string;
  conflict?: unknown;
}

export interface Proposal {
  id: string;
  op: string;
  args: Record<string, unknown>;
  status: "pending";
  reason: ProposalReason;
  baseVersion: number;
  currentVersion?: number;
}

export interface DocumentPayload {
  score: Score;
  layout: Layout;
  proposals: Proposal[];
  rejected?: Proposal;
}

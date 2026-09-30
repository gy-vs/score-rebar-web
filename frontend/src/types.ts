// Wire types shared with the backend (backend/model.py + notation.py).
// All musical time is a rational fraction string "n/d" (quarter-length beats).

export interface PitchWire {
  step: string;
  octave: number;
  alter: number;
  name: string;
  midi: number;
}

export interface TupletWire {
  actual: number;
  normal: number;
  unit: string;
  memberQl: string;
  groupQl: string;
}

export interface RenderNoteWire {
  type: string;
  dots: number;
  ql: string; // sounding quarter length ("1/3" for a triplet eighth)
  notatedQl: string; // written value length ("1/2" for a triplet eighth)
  beatInBar: string;
  rest: boolean;
  tuplet: {
    actual: number;
    normal: number;
    unit: string;
    bracketNumber: number;
    bracketRole: "start" | "continue" | "stop";
  } | null;
  tieBefore: boolean;
  tieAfter: boolean;
}

export interface SliceWire {
  sliceId: string;
  eventId: string;
  voiceId: string;
  barIndex: number;
  beatInBar: string;
  segStart: string;
  segEnd: string;
  segLength: string;
  kind: "note" | "chord" | "rest";
  pitches: PitchWire[];
  tieIn: boolean;
  tieOut: boolean;
  renderNotes: RenderNoteWire[];
  fillRest?: boolean;
}

export interface BarWire {
  index: number;
  start: string;
  end: string;
  duration: string;
  timeLabel: string;
  beats: number;
  beatUnit: number;
  slicesByVoice: SliceWire[][];
}

export interface EventWire {
  eventId: string;
  kind: "note" | "chord" | "rest";
  start: string;
  end: string;
  duration: { quarterLength: string; tuplet?: TupletWire };
  pitches: PitchWire[];
  voiceId: string;
  sliceIds: string[];
}

export interface VoiceMeta {
  voiceId: string;
  name: string;
  clef: "treble" | "bass";
}

export interface ScoreWire {
  scoreId: string;
  title: string;
  voices: { voiceId: string; name: string; events: EventWire[] }[];
  timeSignatures: {
    start: string;
    beats: number;
    beatUnit: number;
    barLength: string;
    label: string;
  }[];
  totalLength: string;
}

export interface ProjectionWire {
  bars: BarWire[];
  voices: VoiceMeta[];
  events: Record<string, EventWire>;
}

export interface DocumentWire {
  scoreId: string;
  revision: string;
  title: string;
  updatedAt: number;
  score: ScoreWire;
  projection: ProjectionWire;
  editResult?: Record<string, unknown>;
}

export interface ApiError {
  error: string;
  message: string;
  headRevision?: string;
}

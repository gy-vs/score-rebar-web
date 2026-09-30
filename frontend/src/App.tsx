import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiFailure } from "./api";
import type { DocumentWire, EventWire, SliceWire } from "./types";
import { Q } from "./q";
import { ScoreView } from "./ScoreView";

interface PendingEdit {
  kind: "stale" | "rejected";
  message: string;
  headRevision?: string;
  // the edit the user attempted, kept so they can adjust and retry
  summary: string;
}

export default function App() {
  const [doc, setDoc] = useState<DocumentWire | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingEdit | null>(null);
  const [selectedSlice, setSelectedSlice] = useState<SliceWire | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [activeVoice, setActiveVoice] = useState(0);
  const [zoom, setZoom] = useState(100);
  const [startBar, setStartBar] = useState(0);
  const [barsPerPage] = useState(4);
  const [meterBeats, setMeterBeats] = useState(3);
  const [busy, setBusy] = useState(false);

  const scoreId = "seed";

  const load = useCallback(async (rev?: string) => {
    const d = await api.get(scoreId, rev);
    setDoc(d);
    return d;
  }, []);

  useEffect(() => {
    api.getSeed().then(setDoc).catch((e) => setError(String(e)));
  }, []);

  // After every confirmed document, keep the selection on the same *event*
  // (slices are regenerated, so the old slice id may not exist anymore).
  const reselectEvent = useCallback(
    (d: DocumentWire, eventId: string | null) => {
      if (!eventId) {
        setSelectedEventId(null);
        setSelectedSlice(null);
        return;
      }
      const ev = d.projection.events[eventId];
      setSelectedEventId(eventId);
      if (ev && ev.sliceIds.length) {
        // pick the slice in the currently visible range, else the first
        const first = findSlice(d, ev.sliceIds[0]);
        setSelectedSlice(first ?? null);
        setActiveVoice(Math.max(0, d.score.voices.findIndex((v) => v.voiceId === ev.voiceId)));
      }
    },
    [],
  );

  const selectedEvent: EventWire | null = useMemo(() => {
    if (!doc || !selectedEventId) return null;
    return doc.projection.events[selectedEventId] ?? null;
  }, [doc, selectedEventId]);

  const onSelect = useCallback(
    (slice: SliceWire) => {
      if (slice.fillRest) return;
      setSelectedSlice(slice);
      setSelectedEventId(slice.eventId);
      setPending(null);
      if (doc) {
        const vi = doc.projection.voices.findIndex((v) => v.voiceId === slice.voiceId);
        if (vi >= 0) setActiveVoice(vi);
      }
    },
    [doc],
  );

  const commit = useCallback(
    async (fn: (base: string) => Promise<DocumentWire>, summary: string) => {
      if (!doc) return;
      setBusy(true);
      setPending(null);
      try {
        const d = await fn(doc.revision);
        setDoc(d);
        reselectEvent(d, selectedEventId);
      } catch (e) {
        if (e instanceof ApiFailure) {
          if (e.stale) {
            setPending({
              kind: "stale",
              message: e.detail.message,
              headRevision: e.detail.headRevision,
              summary,
            });
          } else {
            // rejected: keep the current selection and document exactly as
            // they were; nothing was applied
            setPending({ kind: "rejected", message: e.detail.message, summary });
          }
        } else {
          setError(String(e));
        }
      } finally {
        setBusy(false);
      }
    },
    [doc, selectedEventId, reselectEvent],
  );

  const rebarFrom = useCallback(
    (barIndex: number) => {
      commit(
        (base) => api.setTimeSignature(scoreId, base, barIndex, meterBeats, 4),
        `改 ${meterBeats}/4 从第 ${barIndex + 1} 小节`,
      );
    },
    [commit, meterBeats],
  );

  const selectedBarIndex = selectedSlice?.barIndex ?? 0;

  const changePitch = (name: string) => {
    if (!selectedEvent) return;
    commit(
      (base) =>
        api.editEvent(scoreId, base, {
          voiceId: selectedEvent.voiceId,
          eventId: selectedEvent.eventId,
          action: "pitch",
          pitches: [name],
        }),
      `改音高为 ${name}`,
    );
  };

  const changeDuration = (ql: string) => {
    if (!selectedEvent) return;
    commit(
      (base) =>
        api.editEvent(scoreId, base, {
          voiceId: selectedEvent.voiceId,
          eventId: selectedEvent.eventId,
          action: "duration",
          quarterLength: ql,
        }),
      `改时值为 ${ql} 拍`,
    );
  };

  const moveTo = (start: string) => {
    if (!selectedEvent) return;
    commit(
      (base) =>
        api.editEvent(scoreId, base, {
          voiceId: selectedEvent.voiceId,
          eventId: selectedEvent.eventId,
          action: "move",
          newStart: start,
        }),
      `移动到第 ${start} 拍`,
    );
  };

  const totalBars = doc?.projection.bars.length ?? 0;
  const voiceName = (id: string) =>
    doc?.projection.voices.find((v) => v.voiceId === id)?.name ?? id;

  return (
    <div className="app">
      <header>
        <h1>score-rebar-web</h1>
        <div className="rev">
          {doc && (
            <>
              <span title={doc.revision}>确认状态 {doc.revision.slice(0, 8)}</span>
              <a
                href={api.musicXmlUrl(scoreId, doc.revision)}
                target="_blank"
                rel="noreferrer"
              >
                导出 MusicXML（本状态）
              </a>
            </>
          )}
        </div>
      </header>

      <section className="toolbar">
        <div className="group">
          <label>
            从选中小节起改拍为
            <select value={meterBeats} onChange={(e) => setMeterBeats(+e.target.value)}>
              {[2, 3, 4, 5, 6, 7].map((n) => (
                <option key={n} value={n}>{n}/4</option>
              ))}
            </select>
          </label>
          <button disabled={busy || !doc} onClick={() => rebarFrom(selectedBarIndex)}>
            应用到第 {selectedBarIndex + 1} 小节
          </button>
        </div>

        <div className="group">
          <span>声部：</span>
          {doc?.projection.voices.map((v, i) => (
            <button
              key={v.voiceId}
              className={i === activeVoice ? "active" : ""}
              onClick={() => setActiveVoice(i)}
            >
              {v.name}
            </button>
          ))}
        </div>

        <div className="group">
          <span>缩放</span>
          <input
            type="range" min={60} max={160} value={zoom}
            onChange={(e) => setZoom(+e.target.value)}
          />
          <span>{zoom}%</span>
          <button disabled={startBar === 0} onClick={() => setStartBar((b) => Math.max(0, b - barsPerPage))}>
            上一页
          </button>
          <button
            disabled={startBar + barsPerPage >= totalBars}
            onClick={() => setStartBar((b) => Math.min(totalBars - 1, b + barsPerPage))}
          >
            下一页
          </button>
        </div>
      </section>

      {pending && (
        <div className={`banner ${pending.kind}`}>
          <strong>{pending.kind === "stale" ? "编辑基于旧状态，未被接纳" : "编辑未被接纳"}：</strong>
          {pending.summary} —— {pending.message}
          {pending.kind === "stale" && (
            <button onClick={() => load(pending.headRevision).then((d) => reselectEvent(d, selectedEventId))}>
              刷新到最新确认状态（保留你选中的事件）
            </button>
          )}
          <button onClick={() => setPending(null)}>继续修改</button>
        </div>
      )}
      {error && <div className="banner rejected">{error}</div>}

      <main className="layout">
        <div className="score-area">
          {doc && (
            <>
              <VoiceLaneLabel doc={doc} activeVoice={activeVoice} />
              <ScoreView
                bars={doc.projection.bars}
                clefs={doc.projection.voices.map((v) => v.clef ?? "treble")}
                voiceCount={doc.projection.voices.length}
                zoom={zoom}
                selectedEventId={selectedEventId}
                onSelect={onSelect}
                startBar={startBar}
                barsPerPage={barsPerPage}
              />
              <p className="hint">
                蓝色高亮的是同一个<strong>发声事件</strong>跨小节的全部记谱片段；
                点任意一片都选中整段发声，不会因为多一片音符而多一次发声。
              </p>
            </>
          )}
        </div>

        <aside className="inspector">
          <h2>事件检查器</h2>
          {!selectedEvent && <p className="muted">点击谱面上的音符或休止符。</p>}
          {selectedEvent && (
            <div>
              <Row k="声部" v={voiceName(selectedEvent.voiceId)} />
              <Row k="事件 ID" v={selectedEvent.eventId} />
              <Row k="类型" v={kindLabel(selectedEvent.kind)} />
              <Row
                k="音高"
                v={selectedEvent.pitches.map((p) => p.name).join(" ") || "—"}
              />
              <Row k="开始（拍）" v={selectedEvent.start} />
              <Row k="结束（拍）" v={selectedEvent.end} />
              <Row k="实际时值（拍）" v={selectedEvent.duration.quarterLength} />
              <Row
                k="连音组"
                v={
                  selectedEvent.duration.tuplet
                    ? `${selectedEvent.duration.tuplet.actual}:${selectedEvent.duration.tuplet.normal} ${selectedEvent.duration.tuplet.unit}`
                    : "—"
                }
              />
              <Row k="显示片段数" v={String(selectedEvent.sliceIds.length)} />
              <Row k="片段所在小节" v={sliceBars(doc!, selectedEvent).join(", ")} />

              <div className="edit-block">
                <h3>改音高（同一声部，时间不变）</h3>
                <div className="chips">
                  {["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5", "G#4"].map((n) => (
                    <button key={n} onClick={() => changePitch(n)} disabled={busy}>
                      {n}
                    </button>
                  ))}
                </div>
              </div>

              <div className="edit-block">
                <h3>改实际时值</h3>
                <div className="chips">
                  {["1/2", "1", "3/2", "2", "3", "4"].map((d) => (
                    <button key={d} onClick={() => changeDuration(d)} disabled={busy}>
                      {d}
                    </button>
                  ))}
                </div>
                <p className="muted small">
                  增长会占用后面的休止；若会覆盖别的音，后端拒绝。
                </p>
              </div>

              <div className="edit-block">
                <h3>移动到同一声部的时刻</h3>
                <MoveBox onMove={moveTo} busy={busy} />
              </div>
            </div>
          )}
        </aside>
      </main>
    </div>
  );
}

function MoveBox({ onMove, busy }: { onMove: (s: string) => void; busy: boolean }) {
  const [v, setV] = useState("0");
  return (
    <div className="move-row">
      <input value={v} onChange={(e) => setV(e.target.value)} placeholder="例如 4 或 1/3" />
      <button disabled={busy} onClick={() => onMove(v)}>
        移动
      </button>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="row">
      <span className="k">{k}</span>
      <span className="v" title={v}>{v}</span>
    </div>
  );
}

function kindLabel(k: string) {
  return k === "note" ? "音符" : k === "chord" ? "和弦" : "休止符";
}

function findSlice(d: DocumentWire, sliceId: string): SliceWire | null {
  for (const bar of d.projection.bars)
    for (const sls of bar.slicesByVoice)
      for (const sl of sls) if (sl.sliceId === sliceId) return sl;
  return null;
}

function sliceBars(d: DocumentWire, ev: EventWire): number[] {
  const bars: number[] = [];
  for (const bar of d.projection.bars)
    for (const sls of bar.slicesByVoice)
      for (const sl of sls)
        if (sl.eventId === ev.eventId) bars.push(bar.index + 1);
  return [...new Set(bars)];
}

function VoiceLaneLabel({ doc, activeVoice }: { doc: DocumentWire; activeVoice: number }) {
  return (
    <div className="lanes">
      {doc.projection.voices.map((v, i) => (
        <div key={v.voiceId} className={i === activeVoice ? "lane active" : "lane"}>
          {v.name} {i === activeVoice ? "（当前查看）" : ""}
        </div>
      ))}
    </div>
  );
}

// keep Q referenced for tree-shake clarity of time semantics in the bundle
void Q;

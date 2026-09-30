import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { DocumentPayload, VoiceItem } from "./types";
import { Toolbar } from "./components/Toolbar";
import { ScoreView } from "./components/ScoreView";
import { Inspector } from "./components/Inspector";
import { ProposalList } from "./components/ProposalList";
import { qstr } from "./rational";

export default function App() {
  const [doc, setDoc] = useState<DocumentPayload | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeVoice, setActiveVoice] = useState<string>("v1");
  const [zoom, setZoom] = useState(1);
  const [page, setPage] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const PER_PAGE = 2;

  const load = useCallback(async (payload: DocumentPayload) => {
    setDoc(payload);
    if (payload.rejected) {
      // keep the user's selected event even though the edit was refused
      setError(payload.rejected.reason.message);
    } else {
      setError(null);
    }
  }, []);

  useEffect(() => {
    api.createSeed().then(load).catch((e) => setError(String(e)));
  }, [load]);

  // keep selection valid and pointing at the same *sounded event*
  useEffect(() => {
    if (!doc) return;
    if (selectedId) {
      const found = doc.score.voices.some((v) =>
        v.items.some((i) => i.id === selectedId));
      if (!found) setSelectedId(null);
    }
    if (!doc.score.voices.some((v) => v.id === activeVoice)) {
      setActiveVoice(doc.score.voices[0]?.id ?? "v1");
    }
  }, [doc, selectedId, activeVoice]);

  const selected: VoiceItem | null = useMemo(() => {
    if (!doc || !selectedId) return null;
    for (const v of doc.score.voices) {
      const it = v.items.find((i) => i.id === selectedId);
      if (it) return it;
    }
    return null;
  }, [doc, selectedId]);

  const selectedVoiceId = useMemo(() => {
    if (!doc || !selectedId) return null;
    return doc.score.voices.find((v) =>
      v.items.some((i) => i.id === selectedId))?.id ?? null;
  }, [doc, selectedId]);

  const send = useCallback(async (p: Promise<DocumentPayload>) => {
    setBusy(true);
    try {
      const payload = await p;
      await load(payload);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, [load]);

  const pageMeasures = useMemo(() => {
    if (!doc) return [];
    return doc.layout.measures.slice(page * PER_PAGE, page * PER_PAGE + PER_PAGE);
  }, [doc, page]);

  const pageCount = doc
    ? Math.max(1, Math.ceil(doc.layout.measures.length / PER_PAGE))
    : 1;

  // rebarring can change the number of pages: clamp without mutating music
  useEffect(() => {
    if (page > pageCount - 1) setPage(pageCount - 1);
  }, [page, pageCount]);

  if (!doc) return <div className="loading">载入谱面…</div>;

  const score = doc.score;

  return (
    <div className="app">
      <Toolbar
        score={score}
        zoom={zoom}
        onZoom={(z) => setZoom(Math.min(2.2, Math.max(0.5, z)))}
        page={page}
        pageCount={pageCount}
        onPage={setPage}
        onMeter={(fromMeasure, beats, unit) =>
          send(api.setMeter(score.id, score.version, fromMeasure, beats, unit))}
        onImport={(f) => send(api.importXml(f)).then(() => setPage(0))}
        exportUrl={api.exportUrl(score.id)}
        busy={busy}
      />

      <div className="voice-tabs">
        {score.voices.map((v) => (
          <button
            key={v.id}
            className={v.id === activeVoice ? "tab active" : "tab"}
            onClick={() => setActiveVoice(v.id)}
          >
            {v.name}（{v.id}）
          </button>
        ))}
        <span className="hint">
          切换声部只改变显示焦点，不会改动任一声部的音乐时间。
        </span>
      </div>

      {error && (
        <div className="banner error" onClick={() => setError(null)}>
          {error}（点击关闭；被拒绝的编辑保留在下方「待处理编辑」里）
        </div>
      )}

      <ScoreView
        measures={pageMeasures}
        voiceIds={score.voices.map((v) => v.id)}
        voiceNames={Object.fromEntries(score.voices.map((v) => [v.id, v.name]))}
        activeVoice={activeVoice}
        selectedId={selectedId}
        zoom={zoom}
        onSelect={(itemId) => setSelectedId(itemId)}
      />

      <div className="bottom">
        <Inspector
          score={score}
          item={selected}
          voiceId={selectedVoiceId}
          activeVoiceId={activeVoice}
          layout={doc.layout}
          disabled={busy}
          onPitch={(itemId, pitches) =>
            send(api.pitch(score.id, score.version, itemId, pitches))}
          onDuration={(itemId, d) =>
            send(api.duration(score.id, score.version, itemId, d))}
          onMove={(itemId, onset) =>
            send(api.move(score.id, score.version, itemId, onset))}
        />
        <ProposalList
          proposals={doc.proposals}
          onDismiss={(pid) =>
            send(api.dismissProposal(score.id, pid))}
        />
      </div>

      <footer className="status">
        文档 {score.id} · 已确认版本 v{score.version} · 总长 {qstr(doc.layout.length)} QL
        {" · "}
        {score.voices.length} 个声部 · {doc.layout.measures.length} 小节
        {busy && " · 提交中…"}
      </footer>
    </div>
  );
}

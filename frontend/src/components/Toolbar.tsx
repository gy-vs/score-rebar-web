import { useRef, useState } from "react";
import type { Score } from "../types";

interface Props {
  score: Score;
  zoom: number;
  page: number;
  pageCount: number;
  busy: boolean;
  onZoom: (z: number) => void;
  onPage: (p: number) => void;
  onMeter: (fromMeasure: number, beats: number, unit: number) => void;
  onImport: (file: File) => void;
  exportUrl: string;
}

const METERS = ["2/4", "3/4", "4/4", "6/8"];

export function Toolbar(props: Props) {
  const [measure, setMeasure] = useState(1);
  const [meter, setMeter] = useState("3/4");
  const fileRef = useRef<HTMLInputElement | null>(null);

  return (
    <header className="toolbar">
      <div className="brand">score-rebar-web</div>

      <div className="group">
        <label>
          从第
          <input
            type="number" min={1} value={measure}
            onChange={(e) => setMeasure(Math.max(1, Number(e.target.value)))}
          />
          小节起改为
        </label>
        <select value={meter} onChange={(e) => setMeter(e.target.value)}>
          {METERS.map((m) => <option key={m}>{m}</option>)}
        </select>
        <button
          disabled={props.busy}
          onClick={() => {
            const [b, u] = meter.split("/").map(Number);
            props.onMeter(measure - 1, b, u);
          }}
        >
          改拍号并重排
        </button>
      </div>

      <div className="group">
        <button onClick={() => props.onZoom(props.zoom - 0.1)}>−</button>
        <span className="zoom">{Math.round(props.zoom * 100)}%</span>
        <button onClick={() => props.onZoom(props.zoom + 0.1)}>＋</button>
        <button
          disabled={props.page === 0}
          onClick={() => props.onPage(props.page - 1)}
        >
          上一页
        </button>
        <span>{props.page + 1}/{props.pageCount}</span>
        <button
          disabled={props.page >= props.pageCount - 1}
          onClick={() => props.onPage(props.page + 1)}
        >
          下一页
        </button>
      </div>

      <div className="group">
        <button onClick={() => fileRef.current?.click()}>导入 MusicXML</button>
        <input
          ref={fileRef} type="file" accept=".xml,.musicxml" hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) props.onImport(f);
            e.target.value = "";
          }}
        />
        <a className="btn" href={props.exportUrl}>导出 MusicXML</a>
      </div>
    </header>
  );
}

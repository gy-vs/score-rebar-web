import { useEffect, useState } from "react";
import type { Layout, Score, VoiceItem } from "../types";
import { q, qnum, qstr, qeq } from "../rational";

interface Props {
  score: Score;
  item: VoiceItem | null;
  voiceId: string | null;
  activeVoiceId: string;
  layout: Layout;
  disabled: boolean;
  onPitch: (itemId: string, pitches: { step: string; alter: number; octave: number }[]) => void;
  onDuration: (itemId: string, d: { num: number; den: number }) => void;
  onMove: (itemId: string, onset: { num: number; den: number }) => void;
}

// duration choices in QL
const DURATIONS: { label: string; q: { num: number; den: number } }[] = [
  { label: "全音符 4", q: q(4) },
  { label: "附点二分 3", q: q(3) },
  { label: "二分 2", q: q(2) },
  { label: "附点四分 1½", q: q(3, 2) },
  { label: "四分 1", q: q(1) },
  { label: "附点八分 ¾", q: q(3, 4) },
  { label: "八分 ½", q: q(1, 2) },
  { label: "十六分 ¼", q: q(1, 4) },
];

const STEPS = ["C", "D", "E", "F", "G", "A", "B"];

export function Inspector(props: Props) {
  const { item, score } = props;
  const [moveBeat, setMoveBeat] = useState("");

  useEffect(() => {
    if (item) setMoveBeat(String(qnum(item.onset)));
  }, [item?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!item) {
    return (
      <section className="inspector empty">
        <h3>事件检查器</h3>
        <p className="hint">点击谱面上的音符 / 和弦 / 休止符，这里会显示它的
          <b>实际发声时间</b>、所属声部、显示片段与连音组信息。</p>
      </section>
    );
  }

  const tupletGroup = item.tupletGroupId
    ? score.tupletGroups.find((g) => g.id === item.tupletGroupId)
    : null;

  // fragments (display slices) of this event across measures
  const fragments = props.layout.measures.flatMap((m) =>
    (m.voices[props.voiceId!] ?? [])
      .filter((f) => f.itemId === item.id)
      .map((f) => ({ measure: m.index, f })));

  const pitch = item.pitches[0];
  const inActiveVoice = props.voiceId === props.activeVoiceId;

  return (
    <section className="inspector">
      <h3>事件检查器</h3>
      <div className="kv">
        <span>事件 id</span><code>{item.id}</code>
        <span>类型</span><b>{kindLabel(item.kind)}</b>
        <span>所属声部</span><b>{props.voiceId}</b>
        <span>发声起点</span><b>{qstr(item.onset)} QL</b>
        <span>实际时值</span><b>{qstr(item.duration)} QL</b>
        <span>结束</span><b>{qstr(addQ(item.onset, item.duration))} QL</b>
      </div>

      <div className="fragments">
        <div className="row-title">显示片段（一个发声事件可被切成多片）：</div>
        {fragments.map(({ measure, f }) => (
          <div key={measure} className="frag">
            第 {measure + 1} 小节 · 局部 {qstr(f.localOnset)}
            {" → "}{qstr(f.duration)} QL
            {f.head ? " ·头" : ""}{f.tail ? " ·尾" : ""}
            {f.tokens.length > 1 ? ` ·拆为 ${f.tokens.length} 个记谱音符` : ""}
          </div>
        ))}
        <p className="hint">
          选中的是<b>整个发声事件</b>（以上所有片段同时高亮）；
          片段数量随拍号变化，但发声起点/时值不变。
        </p>
      </div>

      {tupletGroup && (
        <div className="tuplet-box">
          属于 {tupletGroup.actual_notes}:{tupletGroup.normal_notes} 连音组
          （{tupletGroup.member_ids.length} 个成员，作为整体锁定，
          每音实际 {qstr(q(2, 3))} QL）
        </div>
      )}

      {item.kind !== "rest" && (
        <div className="edit-row">
          <span>音高</span>
          <select
            value={pitch?.step ?? "C"}
            disabled={props.disabled || !inActiveVoice}
            onChange={(e) => props.onPitch(item.id,
              item.pitches.map((p, i) =>
                i === 0 ? { ...p, step: e.target.value } : p))}
          >
            {STEPS.map((s) => <option key={s}>{s}</option>)}
          </select>
          <select
            value={pitch?.alter ?? 0}
            disabled={props.disabled || !inActiveVoice}
            onChange={(e) => props.onPitch(item.id,
              item.pitches.map((p, i) =>
                i === 0 ? { ...p, alter: Number(e.target.value) } : p))}
          >
            <option value={-1}>♭</option>
            <option value={0}>♮</option>
            <option value={1}>♯</option>
          </select>
          <input
            type="number" value={pitch?.octave ?? 4}
            disabled={props.disabled || !inActiveVoice}
            onChange={(e) => props.onPitch(item.id,
              item.pitches.map((p, i) =>
                i === 0 ? { ...p, octave: Number(e.target.value) } : p))}
          />
        </div>
      )}

      <div className="edit-row">
        <span>时值</span>
        {DURATIONS.map((d) => (
          <button
            key={d.label}
            className={qeq(item.duration, d.q) ? "chip selected" : "chip"}
            disabled={props.disabled || !inActiveVoice || !!tupletGroup}
            title={tupletGroup ? "连音组成员时值由组锁定" : undefined}
            onClick={() => props.onDuration(item.id, d.q)}
          >
            {d.label}
          </button>
        ))}
      </div>

      <div className="edit-row">
        <span>移动到（QL）</span>
        <input
          value={moveBeat}
          disabled={props.disabled || !inActiveVoice || !!tupletGroup}
          onChange={(e) => setMoveBeat(e.target.value)}
        />
        <button
          disabled={props.disabled || !inActiveVoice || !!tupletGroup}
          onClick={() => {
            const v = Number(moveBeat);
            if (!Number.isFinite(v) || v < 0) return;
            // snap to sixteenth grid on the server; send quarter steps *4
            const steps = Math.round(v * 4);
            props.onMove(item.id, q(steps, 4));
          }}
        >
          移动事件
        </button>
        <span className="hint">按 1/16 网格吸附；只在本声部首尾不重叠时被接纳</span>
      </div>
    </section>
  );
}

function addQ(a: { num: number; den: number }, b: { num: number; den: number }) {
  return q(a.num * b.den + b.num * a.den, a.den * b.den);
}

function kindLabel(k: string) {
  return k === "rest" ? "休止符" : k === "chord" ? "和弦" : "音符";
}

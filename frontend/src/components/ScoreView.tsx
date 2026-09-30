import { useEffect, useRef } from "react";
import { renderScore } from "../vexRenderer";
import type { Measure } from "../types";

interface Props {
  measures: Measure[];
  voiceIds: string[];
  voiceNames: Record<string, string>;
  activeVoice: string;
  selectedId: string | null;
  zoom: number;
  onSelect: (itemId: string, measureIndex: number, voiceId: string) => void;
}

export function ScoreView(props: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const decoderRef = useRef<((el: Element) =>
      { itemId: string; measureIndex: number; voiceId: string } | null) | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const dimmed = new Set(
      props.voiceIds.filter((id) => id !== props.activeVoice));
    decoderRef.current = renderScore(host, {
      measures: props.measures,
      voiceIds: props.voiceIds,
      voiceNames: props.voiceNames,
      selectedItemId: props.selectedId,
      dimmedVoices: dimmed,
      zoom: props.zoom,
    });
  }, [props]);

  return (
    <div className="score-scroll">
      <div
        ref={hostRef}
        className="score-host"
        onClick={(e) => {
          const target = decoderRef.current?.(e.target as Element);
          if (target) {
            propsRef.current.onSelect(
              target.itemId, target.measureIndex, target.voiceId);
          }
        }}
      />
      <div className="legend">
        <span className="swatch selected" /> 整个发声事件（其全部显示片段）
        <span className="swatch dim" /> 非当前声部
        <span className="hint">跨小节的弧线是由布局派生的延音线，不是独立音符</span>
      </div>
    </div>
  );
}

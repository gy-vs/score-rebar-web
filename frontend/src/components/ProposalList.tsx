import type { Proposal } from "../types";

interface Props {
  proposals: Proposal[];
  onDismiss: (id: string) => void;
}

const OP_LABEL: Record<string, string> = {
  set_meter: "改拍号",
  change_pitch: "改音高",
  change_duration: "改时值",
  move_item: "移动事件",
};

export function ProposalList({ proposals, onDismiss }: Props) {
  if (proposals.length === 0) {
    return (
      <section className="proposals empty">
        <h3>待处理编辑</h3>
        <p className="hint">没有被拒绝的编辑。被服务端拒绝的编辑不会抹掉你选中的音，
          会保留在这里，并说明它与当前声部时间的关系。</p>
      </section>
    );
  }
  return (
    <section className="proposals">
      <h3>待处理编辑（{proposals.length}）</h3>
      {proposals.map((p) => (
        <div key={p.id} className="proposal">
          <div className="p-head">
            <b>{OP_LABEL[p.op] ?? p.op}</b>
            <code>基于 v{p.baseVersion}（当前 v{p.currentVersion}）</code>
            <button onClick={() => onDismiss(p.id)}>放弃</button>
          </div>
          <div className="p-reason">
            <span className="code">{p.reason.code}</span>
            {p.reason.message}
          </div>
          {p.reason.conflict != null && (
            <pre>{JSON.stringify(p.reason.conflict as unknown, null, 2) ?? ""}</pre>
          )}
        </div>
      ))}
    </section>
  );
}

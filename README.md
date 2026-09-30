# score-rebar-web

在浏览器里编辑**复声部**谱面、改拍号重排、再导出可重新导入的 MusicXML。
它不是 MusicXML 文本编辑器，也不做播放；它保证的是前端选择、服务端接纳、
排版呈现与 MusicXML 往返之间**一致的音乐时间**。

## 核心模型（为什么不会“多一个图形就多一次发声”）

- **发声事件（VoiceItem）**：每个声部是一条绝对四分音符时间线，
  事件只有 `onset / duration / pitches / kind`，时间用分数（`num/den`）
  存储，三连音成员精确为 `2/3` QL，永不浮点漂移。
- **小节、延音线、连音括号全是派生视图**（`backend/app/layout.py`）：
  布局把事件按当前拍号切成显示片段（Fragment）。跨小节的长音被切成几片、
  片与片之间画延音线——这些 tie **从不存储**。改拍号只重算视图。
- 因此 4/4 → 3/4：
  - 发声起点/时值完全不变（有测试锁定）；
  - 哪些音跨小节、tie 出现在哪里会变；
  - 三连音三个音仍是每个 `2/3` QL；
  - 下声部时间不被上声部重排影响（声部 = 独立时间线，导出时 1 个声部 =
    1 个 MusicXML `<part>`）。
- 选中任意一个片段 → 选中的是**整个发声事件**，它的所有片段同时高亮；
  检查器列出该事件的发声起点/时值/结束，以及它当前被切成了哪些显示片段。

## 服务端确认状态（为什么不会“图重排了导出还是旧的”）

- 所有变更请求带 `baseVersion`；被接纳才原子地写库并把 `version + 1`。
- 同一个响应里返回 `score`（规范模型）+ `layout`（派生谱面）。
  下一次 GET、页面渲染、导出 MusicXML 都来自这一个已确认版本。
- 版本过期 → `409`；编辑与声部时间冲突（如重叠）→ **拒绝并保留为
  pending proposal**：模型不变、版本不增、选中的音不丢，拒绝原因和冲突
  时间区间随文档持久化，重新打开仍在，可继续修改或放弃。
- 缩放、翻页、声部切换只改显示，不发写请求、不触碰版本与模型。
- 文档保存在服务端 SQLite（`SCORE_REBAR_DATA/scores.db`），重开浏览器
  仍能取回拍号、声部与全部编辑结果。

## 范围

音符 / 和弦 / 休止符 / 拍号 / 多声部 / 延音线（派生）/ 连音组。
不做歌词、演奏法库、多人协作、播放。

## 目录

```
backend/
  app/models.py     规范模型：绝对时间线 + 拍号区域（无小节）
  app/layout.py     重排引擎：网格、切片、tie/休止/连音 token 派生
  app/edits.py      编辑操作：改拍/音高/时值/移动 + 冲突规则
  app/musicxml.py   music21 读写：真建立发声事件↔片段关系，不是替换标签
  app/storage.py    SQLite 确认状态 + pending proposals
  app/main.py       FastAPI（/api/scores/..., /api/import, export）
  tests/            不变量与端到端测试
frontend/
  src/vexRenderer.ts  VexFlow 渲染（只画确认布局，标注 data-item）
  src/components/     工具栏 / 谱面 / 事件检查器 / 待处理编辑
  scripts/            jsdom 渲染烟测、Playwright 浏览器 e2e
docs/seed-fragment.musicxml  需求中的 4/4 两小节片段（可直接导入）
```

## 运行

后端：

```bash
cd backend
python3 -m pip install -r requirements.txt   # 已装可跳过
./run-dev.sh            # http://localhost:8000 （生产模式同时托管前端 dist）
# 或: python3 -m uvicorn app.main:app --reload
```

前端开发（热更新，:5173 代理 /api 到 :8000）：

```bash
cd frontend
npm install
npm run dev
```

生产构建后由后端直接托管：

```bash
cd frontend && npm install && npm run build
# 打开 http://localhost:8000
```

## 第一屏即可操作

页面打开即种子素材：上声部八分三连音 G4 A4 B4 + 跨小节 G4，
下声部每小节一个 C3（等价于 `docs/seed-fragment.musicxml`）。
可立刻：从某小节起改 3/4 → 看后续小节重划、tie 位置变化 → 点跨小节音的
任一片确认选中的是整个事件 → 改音高/时值/移动 → 导出 → 再导入核对发声时间。

## 测试

```bash
cd backend && python3 -m pytest -q
cd frontend && npm run smoke     # jsdom 内真实 VexFlow 渲染烟测
# 有系统浏览器时: npx playwright install chromium && npm run e2e
```

测试锁定的关键不变量：发声时间跨拍号不变；tie/全小节休止是派生且随拍号
变化；下声部独立；三连音 `2/3` 精确保持；移动/时值冲突被拒绝且模型不变；
4/4 与 3/4 下导出→导入（含二次往返）的发声事件、拍号、连音组完全一致。

## 关键取舍

- 声部映射为 MusicXML **part** 而非 part 内 `<voice>`，使“各声部时间互不
  影响”在往返后天然成立；导入同 part 内多 voice 的文件时，它们会归入同一
  声部时间线（按 staff-voice 槽位解析 tie 与连音）。
- 连音组的成员作为整体锁定，不能单独改时值/移动，以保住实际发声时值。
- 移动按十六分音符网格吸附；跨声部移动不在当前范围内。

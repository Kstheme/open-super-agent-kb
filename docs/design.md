# 系统设计 v0.1

工作名 `openkb`（可改）。本文是 v0.1 的设计基线：产品边界、模块划分、数据模型、关键流程、部署与评测。词汇以 [CONTEXT.md](../CONTEXT.md) 为准，决策以 [docs/adr/](./adr/) 为准。

图说版本见 [architecture.md](./architecture.md)：同一套决策，换成 Mermaid 图来讲。两份冲突时以本文为准。

## 1. 定位

一个单机可跑、可长期维护的知识库：Agent 从原始资料中产出带证据的声明，并在资料变化后指出哪些产物需要修订。

它交付的不是搜索结果，是**带证据的产物**；它的差异不是"更会搜"，是**资料变了知道该改哪**。

## 2. 已定决策

| 项 | 结论 | 记录 |
|---|---|---|
| 形态 | 应用优先，账本结构单列 `spec/` 并单独版本化 | ADR-0003 |
| 用户 | 单机单人，无账号，写入带 `actor`（恒为 `local`） | ADR-0002 |
| 域 | 制度 / 合规清单维护 | 本文 |
| 界面 | 本地 Web，单进程单端口 | 本文 |
| 技术栈 | Python 先行；React 后期接入，构建产物打进 wheel | 本文 |
| 后端 | SQLite + FTS5 + sqlite-vec；数据访问经 `Store` 端口 | ADR-0001 |
| 模型 | 仅 OpenAI 兼容协议，`base_url` 可配 | 本文 |
| Embedding | 默认走同一 endpoint；可选本地模型（`sentence-transformers`），`init` 时选择 | 本文 |
| 许可 | Apache-2.0 | 本文 |
| 检索 | hybrid（FTS5 + 向量）+ RRF，rerank 默认开 | 本文 |
| 缺口 | 由系统按产物骨架与账本覆盖度计算，Agent 只能认领与关闭 | ADR-0004 |
| 块身份 | 跨版本稳定键（标题路径 + 条款编号 + 归一化文本哈希），序号只作排序 | ADR-0005 |
| 任务分类 | `ingest` 与 `deliver` / `review` 分开，预算与恢复粒度不同 | ADR-0006 |
| 修订 | 差异块确认，默认只呈现实质影响条目 | 本文 |
| 预算 | 硬上限；`deliver` / `review` 见 §10，`ingest` 按文件计；超限落盘 `paused` | 本文 |
| 资料源 | 本地目录，md / txt / docx / pdf | 本文 |
| 变更检测 | 文件 hash 触发 + 块级结构化 diff 定位 | 本文 |
| 入账 | 自动入账，可一键标记错误；确认发生在产物层 | 本文 |
| 评测 | gold set，对手为"重新生成 + 语义 diff" | 本文 |

## 3. 产品边界

### v0.1 承诺的唯一链路

```
导入目录 → 切块（带稳定键）→ 抽取声明（带 span）→ 账本
   ↓
用户委托 → 模板给出产物骨架 → 系统算缺口 → 多步取证（缺口驱动）→ 产物（清单，每条挂证据）
   ↓
资料变更 → 文件 hash → 块级 diff → 受影响声明 → 实质影响判定 → 差异提案 → 按块确认 → 新版本
```

这条链路必须端到端可跑、可演示、可评测。其他一切不做，见 [rejected.md](./rejected.md)。

### 明确不做

账号与权限、多租户、Postgres/Milvus、网页抓取与第三方同步、多 Agent 互聊、自动应用修订、跨领域声明抽取与产物交付、独立规范治理。理由记录在 [rejected.md](./rejected.md)。M1 的本地导入、版本管理、切块和证据回点覆盖多个领域，自动声明入账沿用制度合规模板。

## 4. 功能清单

| 编号 | 功能 | 验收标准 |
|---|---|---|
| F1 | 建库与解析 | 100 份 docx/pdf 建库；显示进度与失败清单，失败可重试 |
| F2 | 委托与取证 | 目标 → 计划 → 工具轨迹 → 产物；每步可看 |
| F3 | 证据审阅 | 产物每条可点回原文段落；可一键标记"错了" |
| F4 | 变更复核 | 改一份资料，只列实质受影响的条目 |
| F5 | 差异确认 | 按差异块接受/拒绝；拒绝可写理由并回写账本 |
| F6 | 版本回溯 | 任一时点的产物版本及当时依据的证据 |
| F7 | 任务续跑 | `deliver` / `review` 超限后带缺口清单继续、不重复计费；`ingest` 从下一个未完成文件继续 |

## 5. 目录骨架

```
openkb/
  core/                 # 确定性，零 LLM 依赖
    ledger/             # claim, evidence, span, validity
    store/              # 端口 + sqlite 实现 + migrations
    task/               # 状态机, checkpoint, 预算
    effects/            # 持久副作用队列
    changes/            # 文件 hash, 块稳定键, 块级 diff, 受影响计算
    gaps/               # 产物骨架 × 有效声明 → 缺口
  agent/
    runtime/            # loop, 预算执行, 工具注册, trace
    profiles/           # 声明模板（声明形态 + 产物骨架）：制度合规
    prompts/
    tools/              # retriever, parser, differ, ledger_query
  ingest/               # 目录扫描, 解析, 结构感知切块
  retrieval/            # fts, vector, hybrid(rrf), rerank
  artifacts/            # 组装, 渲染, 版本, 差异
  server/               # FastAPI, CLI, 静态资源
  spec/                 # ledger.md, events.md, schema.json
tests/
docs/adr/
```

### 三条不变量（进 CI）

1. `core/` 不得 import `agent/`。
2. 所有 LLM 调用只出现在 `agent/`。
3. `Store` 端口之外不出现 `sqlite3`。

这三条是"将来能换模型、换前端、换后端"的物理保证，不是风格建议。

与此配套的一条非物理约束：**缺口只能由 `core/gaps/` 从骨架与账本导出，`agent/` 不得构造缺口**（ADR-0004）。骨架由模板产出、以 JSON 传入 `core/`，因此 `core/` 仍然不需要 import `profiles/`。

## 6. 运行时架构

```
        ┌── 用户委托 ──┐
        │              ├──→ Trigger（不可变，幂等键）──→ Task（状态机 + 预算）
        └── 文件变更 ──┘                                      │
                                                               ↓
                                          Agent loop（读缺口 → 调工具 → 写账本）
                                                               │
                         ┌─────────────────────────────────────┼──────────────┐
                         ↓                                     ↓              ↓
                      Ledger                            Artifact        Effects 队列
                （声明/证据/时效）                    （清单 + 版本）   （通知、重算、落盘）
```

两条触发线：

- **用户委托**：`Trigger(user_message)` → `Task(deliver)`。
- **文件变更**：watcher 或 `openkb sync` → `Trigger(document_changed)` → `changes/` 计算受影响声明 → `Task(review)`。

只有第二条是"主动"的，也最容易做砸，因此**受影响集合由系统计算，实质影响才交给 Agent 判定**。

## 7. 数据模型

### 资料侧

| 表 | 关键字段 | 说明 |
|---|---|---|
| `kb` | id, name, created_at | v0.1 只允许一行，字段先留 |
| `source_document` | id, kb_id, uri, kind | 文件的稳定身份 |
| `document_version` | id, document_id, content_hash, imported_at | 每次内容变化一行，原文另存 |
| `block` | id, version_id, block_key, ordinal, heading_path, text, start_offset, end_offset, norm_hash | 结构感知切块的结果；`block_key` 跨版本稳定，是 diff 对齐的单位，`ordinal` 只用于排序 |

### 账本侧

| 表 | 关键字段 | 说明 |
|---|---|---|
| `claim` | id, kb_id, text, profile, scope_json, valid_from, valid_to, status | status ∈ active / superseded / retracted |
| `evidence` | id, claim_id, block_id, quote_start, quote_end, quote_hash | 一条声明可有多条证据 |
| `edit_mark` | claim_id, reason, actor, created_at | 用户"这条错了"的记录，用于纠错与偏好 |

**依赖不单独建表。** "哪些声明依赖哪份文档"由 `evidence → block → version → document` 反查得出。这样不会出现依赖表与证据不一致的情况，也不需要维护双写。

### 产物侧

| 表 | 关键字段 | 说明 |
|---|---|---|
| `artifact` | id, kb_id, kind, title, version, status | 产物版本只增不改 |
| `artifact_claim` | artifact_id, claim_id, section, ordinal, snapshot_text | 记录当时引用的是哪条声明的哪个快照 |
| `review` | id, task_id, changed_version_id, proposal_json, state | 差异提案与确认状态 |
| `skeleton_proposal` | id, task_id, slot_json, state, created_at | Agent 建议新增的骨架槽位，人确认后才进入下一次委托 |

`snapshot_text` 是刻意的冗余：声明后来被修订，历史产物仍能还原当时的样子。

`artifact_claim` 只由骨架的槽位填充，因此"产物是否完整"等价于"每个槽位是否有有效声明"——这正是缺口清零可被单测的原因。

### 执行侧

| 表 | 关键字段 | 说明 |
|---|---|---|
| `task` | id, kb_id, kind, goal, state, skeleton_json, budget_json, spent_json, checkpoint_json | `kind ∈ ingest / deliver / review`，决定预算与恢复粒度（ADR-0006）；`state ∈ queued / running / paused / done / failed`；`skeleton_json` 是本次委托的产物骨架 |
| `effect` | kind, key, payload, seq, generation, attempts, due, error | 事务内入队、提交后派发 |
| `run_step` | task_id, seq, kind, payload, tokens, duration | 轨迹回放的来源 |

## 8. 关键流程

### 8.1 建库

1. `openkb init` 选目录与模型配置，写 `~/.openkb/config.toml`。
2. 扫描 → 逐文件解析 → 按标题层级/条款编号切块 → 计算 `block_key` 与 `norm_hash` → 存 `block`。
3. Agent 逐块或逐组抽取声明，**无 span 不入账**（校验失败则重试，重试仍失败则记入失败清单）。
4. 每完成一个文件即落 checkpoint。`kind=ingest` 的预算按文件计，中断后从下一个未完成文件继续（ADR-0006）；失败进失败清单，不产出缺口。
5. 抽取后不做人工确认（Q18=A）；产物层才确认。

### 8.2 委托

1. `Trigger(user_message)` → `Task(deliver)`，附带预算。
2. 声明模板把目标解析成**产物骨架**，写入 `task.skeleton_json`。骨架以数据形式传入 `core/`，`core/` 不 import `profiles/`。
3. `core/gaps/` 算缺口：骨架中尚未被 `active` 声明覆盖的槽位，可复算。Agent 只能认领与关闭，不得新增；追加诉求写 `skeleton_proposal`，由人确认后进入下一次委托。
4. 循环：挑最高优先级缺口 → 选工具（账本查询 / 资料检索 / 解析）→ 写回声明或关闭缺口。
5. 缺口清零 → Composer 组装产物，逐条挂证据；预算到顶 → `paused` + 当前缺口清单（例外路径，非常规结尾）。
6. 系统校验：每条有无证据、有无未决冲突。不过则退回补证据。

### 8.3 复核（核心差异）

1. 文件 hash 变化 → `Trigger(document_changed)`。
2. 新版本入库，按 `block_key` 对齐旧块与新块：求交 → 命中的做文本 diff → 未命中的按标题路径与相邻位置归入新增/删除（ADR-0005）。归一化规则的版本写进任务记录，否则两次对齐不可比。
3. 反查：变更块 → `evidence` → `claim`，得到受影响集合（系统计算，可复算）。
4. Agent 逐条判断实质影响：真值或适用范围是否改变。
5. 生成差异提案：只列实质影响的条目，每条给"改成什么 + 依据哪一段"。
6. 人按块接受/拒绝 → 新产物版本；拒绝理由回写 `edit_mark`。

## 9. 检索设计

| 层 | 手段 | 说明 |
|---|---|---|
| 分词 | FTS5 `trigram`（或外挂词典） | 默认 tokenizer 对中文无效 |
| 召回 | BM25 + 向量并行 | 两条通道都要 |
| 融合 | RRF | 不手调权重 |
| 重排 | cross-encoder 或 rerank API，默认开 | 场景是"少而准" |
| 定位 | 块命中后回落到句子级 | 否则证据不精确 |
| 改写 | 由缺口生成查询 | 不直投用户原句 |

**降级**：`sqlite-vec` 加载失败时，退到"FTS5 + numpy 精确检索"。10 万级向量一次矩阵乘几十毫秒，单人交互够用。

**两条检索通道**：先查账本（结构化，精确），不够再查原文（模糊，向量）。已入账的声明优先复用，这既是准确率来源，也是"越用越准"的来源。

## 10. 预算与降级

`deliver` / `review`：

```
单任务：40 步 / 15 万 token / 10 分钟
单步：工具调用 ≤ 3 次，单次返回截断 8k token
80% 预算：Planner 只补最关键缺口
超限：落盘退出，state=paused，输出缺口清单
```

`ingest`（ADR-0006）：预算按文件计；每完成一个文件落 checkpoint；超限或中断后从下一个未完成文件继续，失败进失败清单，**不输出缺口**。

每次运行结束展示本次 token 与估算费用；全局日预算可选。**成本可见，用户才敢随便点。** 建库与委托分开计量，否则单次问答的成本会被首次建库摊薄成噪声。

## 11. 部署

```
pipx install openkb
openkb init            # 选目录、填 base_url/key、选 embedding 后端
openkb serve           # 127.0.0.1:8765
```

- 数据目录 `~/.openkb/`：`kb.db` + `originals/`（原文版本）+ `config.toml`
- 备份 = 复制目录；迁移 = 拷到另一台机器
- 备选入口：`uvx openkb`、`docker run`
- 绑 `0.0.0.0` 需显式 flag 并打印警告
- React 后期：构建产物进 wheel 的 `package_data`，安装期不依赖 node
- `openkb doctor`：检查 SQLite 版本、FTS5、trigram、sqlite-vec、模型连通性、磁盘

## 12. 评测

**顺序：先建评测，再写核心。** 目的不是证明自己好，是先证明自己不该被放弃。

- **gold set**：20-30 条声明 + 5 次制度修订，人工标注哪些受影响。公开可复现。
- **对手（必须打败）**：资料变更后，用同一模型、同一检索索引，把产物整体重新生成一遍，再做语义 diff。它不需要账本。
- **指标**：受影响条目召回率、误报率、变更到可审阅差异的时延、单次成本。
- **判定**：若对手在召回率上接近且成本更低，则账本方案降级为"只提供定位"，不投入依赖机制。

## 13. 里程碑

| 阶段 | 内容 | 完成标准 |
|---|---|---|
| M0 | gold set + 对手 harness | 能跑出四个指标，哪怕本方是空的 |
| M1 | 账本与建库 | 100 份资料入库，证据可回点；块键一致率过 gate（ADR-0005），不过则先修对齐再往下 |
| M2 | 委托与产物 | 一条完整链路出清单 |
| M3 | 复核与差异提案 | 改一份资料后产出可审阅的差异提案，召回率不低于 M0 对手且误报率不高于 M0 对手 |
| M4 | 打包与演示 | pipx 一条命令跑通全流程 |

### M1 业务验收

- **块稳定键先过关。** 在法规和技术文档两个领域各做受控插入。由人工预先标注未改动的原有块，要求这些块 100% 保持稳定键且一对一对应。插入段应识别为新增块。另取公开资料的真实版本对，核对人工标注的对应关系，要求零静默错配，明确列出无法确认的对应关系。固定首批真实样本要求 PDF 正文条款标题 6/6、Markdown 正文锚点 5/5，并分别报告实际得分。[ADR-0005](./adr/0005-block-identity-and-alignment.md)
- **首批资料。** 支持原生 DOCX、可选取文本的 PDF 和 Markdown。扫描 PDF 的 OCR 暂缓，并明确提示原文定位受限。先用 [GovInfo 法规历史年版](https://www.govinfo.gov/help/cfr)、[Kubernetes 中文文档](https://github.com/kubernetes/website/tree/main/content/zh-cn/docs) 和 [Section508.gov 原生 DOCX](https://www.section508.gov/create/documents/) 验证资料获取、版本选择和证据定位。清单记录资料地址、版本或提交号、许可与文件哈希。
- **100 份入库。** 固定一批 100 份不同的原生 DOCX/PDF，要求全部成功入库，DOCX 和 PDF 各至少 10 份。重复文件、同一资料的不同版本、Markdown 单独记录。Markdown 用于跨领域验证。
- **按文件恢复。** 用同一批资料在第 51 份处理中断。恢复后从第 51 份继续，前 50 份不重复处理，最终 100 份成功入库。另用一份损坏文件验收失败清单，检查文件名和原因是否可见，以及替换后能否只重试该文件。[ADR-0006](./adr/0006-two-task-kinds.md)
- **证据回点。** 预先标注 12 条证据，DOCX、PDF 和 Markdown 各 4 条。通过本地验收入口逐条确认正确的资料、版本和位置。样本至少包含 DOCX 表格行、PDF 指定页，以及新版移动段落后仍需回看的旧版证据。技术文档可用人工标注的证据验收定位。

### M3 业务验收

沿用 M2 的 4 项 Word 无障碍自查清单及两份 Section 508 原件。通过本地 CLI 为同一份 Word 检查清单资料提交新版本，查看复核结果，并逐条接受或拒绝差异提案。以下用 `section508-word.docx` 的独立模拟修订副本验收，原件保留用于核对。 [M2 手动验收](./m2-manual-acceptance.md)

| 模拟修订 | 通过条件 |
|---|---|
| 重复表头规则改为仅跨页的数据表格适用 | 系统由变更块反查到依赖该证据的声明。差异提案只列「数据表格」，说明新适用范围并引用新版原文。 |
| 重复表头规则只改措辞，含义不变 | 系统找到该条需要复核，保留「无实质影响」记录，无修改提案。 |
| 插入无关段落，原文位置移动 | 原有证据能正确对应到新版位置，4 个清单条目均无修改提案。 |
| 删除重复表头规则，新版无替代依据 | 「数据表格」标记为待人工核对，列出原声明和失去的旧证据。当前清单保持原样，不生成反向规则。 |

用独立账本分别验收接受和拒绝。接受差异提案后生成新的清单版本，只更新获接受的「数据表格」条目，证据指向新版原文。旧清单的 4 个条目和旧证据仍可查回。拒绝时记录决定并保留当前清单版本，若用户填写理由则写回账本。文件自动监控和浏览器操作留到后续。

### M3 基线验收

业务验收和基线评测分别记录。用 M0 的同一套 gold set 和同一模型配置运行 M3 与 `regenerate`。质量门槛为 M3 的召回率不低于 `regenerate`，误报率不高于 `regenerate`。同时报告时延、token 和估算费用，缺少费用数据时记为不可比。

沿用 M0 的收缩判断。对手召回率达到本方召回率减 5 个百分点的下限，且估算费用低于本方并至多为本方的 80% 时，提示考虑将账本收缩为只提供定位，由人决定是否采用。 [M0 评测规格](./specs/0001-m0-eval-harness.md#报告与判定)

### M4 交付与验收

**安装。** 在干净的 Windows 环境中，用 `pipx` 安装构建好的 wheel，运行 `openkb init` 和 `openkb serve`，浏览器从本机地址打开应用。安装后的流程无需仓库源码、项目虚拟环境或 Node。公开发布到 PyPI 单独安排。

**首次启动。** `openkb init` 设置本地数据目录和模型配置，支持复用已有的 `config.toml`。浏览器可以查看当前模型和连接状态，密钥留在本机配置文件。模型不可达时，用户仍能查看已入库资料、历史清单和证据，需要模型的操作给出明确错误。

**浏览器业务主线。** 从空账本导入 M2 的两份真实 Section 508 原件，生成 4 项 Word 无障碍自查清单，逐项回到原文证据。提交带「模拟修订」标识的 Word 新版，查看「数据表格」差异提案，分别验收接受与拒绝，并查阅清单历史。演示资料单独准备，用户可使用同一入口导入自己的资料。M0 评测和 M1 的 100 份批量验收沿用 CLI。 [M2 手动验收](./m2-manual-acceptance.md) · [M3 手动验收](./m3-manual-acceptance.md)

**页面。** 用 `ui-ux-pro-max` 设计资料、清单、复核三个工作区，采用浅色、高对比和深蓝主色。资料展示导入状态和版本。清单可从条目打开对应版本、位置和原文。复核在宽屏并排展示旧规则和修改建议，新版证据紧跟提案。接受和拒绝可使用键盘，窄屏改为上下布局并保留关键信息与操作。页面资源随 wheel 安装。

**证据回点。** 浏览器显示原文摘录、资料版本和具体位置，并可下载该版本保留的原件。分别打开清单 v1 和 v2 的「数据表格」证据，应看到旧规则与跨页新规则，并能下载对应的两版 DOCX。完整的浏览器 Word 阅读器留到后续。

**安装诊断。** `openkb doctor` 检查数据目录可写、SQLite 可打开、模型配置有效和端点可达。用正常配置与中途断开模型端点两个场景验收。失败项给出明确原因，模型故障期间仍可浏览历史清单和证据。FTS5、trigram 和 sqlite-vec 的诊断随对应功能验收。

## 14. 风险

| 风险 | 表现 | 应对 |
|---|---|---|
| 依赖粒度失准 | 连太粗→全是误报；连太细→漏检 | 块级对齐 + 评测驱动调粒度 |
| 块身份漂移 | 文档插一段，序号全变，受影响集合暴涨成误报 | 稳定键 + M1 一致率 gate（ADR-0005）；无法算键时整篇作一块，不退回序号 |
| 缺口被 Agent 虚增 | 任务永远做不完，几乎全以 paused 结束 | 缺口由系统算，Agent 只能认领（ADR-0004）；paused 占比作为健康度指标 |
| 建库预算不足 | F1 在 40 步 / 15 万 token 下不可能完成 | `kind=ingest` 按文件计预算与 checkpoint（ADR-0006） |
| 首次建库成本高 | 用户等太久而流失 | 增量导入、进度可见、失败可重试 |
| 差距不够明显 | 对手"重新生成 + diff"追平 | M0 先验证，不行就收缩到定位能力 |
| 中文解析质量 | docx 表格、pdf 版式 | 先支持常见版式，失败清单显式暴露 |
| 过度自信 | Agent 判"无实质影响"但确实变了 | 差异提案保留"我判断无影响"的记录，可复查 |

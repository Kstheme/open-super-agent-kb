# openkb 架构（可视化）

本文是 [design.md](./design.md) 的可视化伴读：同一套决策，换成图来讲。**规范源仍是 `design.md` 与 `docs/adr/`**，两边冲突时以它们为准；词汇以 [CONTEXT.md](../CONTEXT.md) 为准。

读法建议：先看第 1 节分层图，再看第 3 节的复核流程——那是整个项目的差异点所在。其余章节服务于这两张图。

## 1. 一句话与三层

> 交付的不是搜索结果，是**带证据的产物**；差异不是"更会搜"，是**资料变了知道该改哪**。

系统分三层，边界是硬的：`server/` 只做入口，`agent/` 是唯一能碰模型的地方，`core/` 是确定性内核、零 LLM 依赖。

```mermaid
flowchart TB
    subgraph EDGE["入口层 server/"]
        WEB["本地 Web<br/>127.0.0.1:8765"]
        CLI["openkb CLI<br/>init / serve / sync / doctor"]
    end

    subgraph AGENT["agent/ — 唯一允许调用 LLM 的层"]
        RT["runtime<br/>loop · 预算执行 · 工具注册 · trace"]
        PROF["profiles<br/>声明模板：制度合规"]
        PR["prompts"]
        TL["tools<br/>retriever · parser · differ · ledger_query"]
    end

    subgraph CORE["core/ — 确定性内核，零 LLM 依赖"]
        LG["ledger<br/>声明 · 证据 · span · 时效"]
        ST["store<br/>端口 + sqlite 实现 + migrations"]
        TK["task<br/>状态机 · checkpoint · 预算"]
        EF["effects<br/>持久副作用队列"]
        CH["changes<br/>文件 hash · 块级 diff · 受影响"]
    end

    subgraph CAP["周边能力"]
        ING["ingest<br/>扫描 · 解析 · 结构感知切块"]
        RET["retrieval<br/>fts · vector · rrf · rerank"]
        ART["artifacts<br/>组装 · 渲染 · 版本 · 差异"]
    end

    DB[("kb.db<br/>SQLite + FTS5 + sqlite-vec")]
    ORIG[("originals/<br/>原文，每个版本都留")]
    LLM["OpenAI 兼容端点<br/>base_url 可配"]

    WEB --> RT
    CLI --> RT
    PROF --> RT
    PR --> RT
    RT --> TL
    TL --> LG
    TL --> RET
    RT --> LLM
    LG --> ST
    TK --> ST
    EF --> ST
    CH --> ST
    ING --> ST
    RET --> ST
    ART --> LG
    ST --> DB
    ING --> ORIG
```

三个观察点：

- **箭头只向一个方向走**：`agent/` 依赖 `core/`，`core/` 从不反过来。这不是风格，是第 3 节三条不变量的第一条。
- **`store/` 是唯一碰到 `kb.db` 的东西**。换 Postgres 时只换它（ADR-0001）。
- **`effects/` 是 outbox**：副作用在同一个事务里入队，提交后才派发。所以进程在"账本写成功"和"通知发出去"之间被杀，重启后不会丢也不会重。

## 2. 概念与表

术语先对齐，代码里用英文名，讨论时用中文名：

| CONTEXT.md | 代码 / 表 | 一句话 |
|---|---|---|
| 资料 | `source_document` / `document_version` | 进库的原始材料，每个版本原样保留 |
| 块 | `block` | 结构感知切分的结果，diff 与 span 对齐的单位 |
| 声明 | `claim` | 一条可独立判断真假或适用范围的断言 |
| 证据 | `evidence` | 指向具体版本具体位置的原文片段 |
| 账本 | 以上各表之和 | Agent 的长期记忆，产物可追溯的依据 |
| 产物 | `artifact` / `artifact_claim` | 交付给人的东西，只增不改 |
| 触发 | `trigger` | 不可变外部事件，幂等的单位 |
| 任务 | `task` | 一次持久可恢复的工作，自己拥有状态 |
| 复核 | `review` | 资料变化后对受影响声明的重新判断 |
| 差异提案 | `review.proposal_json` | 按块组织的修订建议，人逐块确认 |

```mermaid
erDiagram
    KB ||--o{ SOURCE_DOCUMENT : "包含"
    SOURCE_DOCUMENT ||--o{ DOCUMENT_VERSION : "每个版本一行"
    DOCUMENT_VERSION ||--o{ BLOCK : "切块"
    BLOCK ||--o{ EVIDENCE : "被引用"
    CLAIM ||--o{ EVIDENCE : "依据"
    CLAIM ||--o{ EDIT_MARK : "被标记错误"
    ARTIFACT ||--o{ ARTIFACT_CLAIM : "组装"
    CLAIM ||--o{ ARTIFACT_CLAIM : "被引用"
    TASK ||--o{ RUN_STEP : "轨迹回放"
    TASK ||--o{ REVIEW : "产出"
    DOCUMENT_VERSION ||--o{ REVIEW : "触发"

    KB {
        string id
        string name
    }
    DOCUMENT_VERSION {
        string id
        string content_hash
        string imported_at
    }
    BLOCK {
        string id
        int ordinal
        string heading_path
        int start_offset
        int end_offset
    }
    CLAIM {
        string id
        string text
        string profile
        string status
    }
    EVIDENCE {
        string id
        string claim_id
        string block_id
        int quote_start
        int quote_end
        string quote_hash
    }
    ARTIFACT {
        string id
        int version
        string status
    }
    ARTIFACT_CLAIM {
        string artifact_id
        string claim_id
        string section
        int ordinal
        string snapshot_text
    }
    REVIEW {
        string id
        string proposal_json
        string state
    }
    TASK {
        string id
        string kind
        string state
        string checkpoint_json
    }
```

两个刻意的"不建表"：

1. **依赖不建表。** 图上 `EVIDENCE` 直接连 `BLOCK`，没有 `claim_document` 这样的中间表。"哪些声明依赖这份文档"是由 `evidence → block → version → document` 反查出来的。建表就要双写，双写就会不一致。
2. **不存"结论"只存快照。** `artifact_claim.snapshot_text` 是冗余——声明后来被修订，历史产物仍能还原当时的样子，这是版本回溯（F6）的唯一实现方式。

## 3. 三条不变量

```mermaid
flowchart LR
    AG["agent/"] -->|"允许：依赖"| CORE["core/"]
    CORE -.->|"禁止：反向 import，CI 必须红"| AG

    AG -->|"允许：唯一的 LLM 调用点"| LLM["模型端点"]
    CORE -.->|"禁止：core 里出现任何 LLM 调用"| LLM

    CORE -->|"允许：唯一的数据访问"| PORT["Store 端口"]
    PORT --> SQL[("sqlite3")]
    CORE -.->|"禁止：绕过端口直接用 sqlite3"| SQL
```

这三条进 CI，不是评审时靠人眼。它们是"将来能换模型、换前端、换后端"的**物理保证**：

- 换模型 → 只改 `agent/`，`core/` 一行不动。
- 换前端 → React 后期接进来，`server/` 只多一层静态资源。
- 换后端 → 只写一个 `Store` 的第二实现（ADR-0001 预留）。

## 4. 两条触发线

```mermaid
flowchart LR
    U["用户委托<br/>一句自然语言"] --> T1["Trigger(user_message)"]
    F["文件 hash 变化<br/>watcher 或 openkb sync"] --> T2["Trigger(document_changed)"]

    T1 --> K1["Task(deliver)"]
    T2 --> CHG["changes/ 算受影响集合<br/>确定性，可复算"]
    CHG --> K2["Task(review)"]

    K1 --> LOOP["Agent loop"]
    K2 --> NEW["Agent 只判断实质影响"]
    LOOP --> LG[("账本")]
    NEW --> LG
    LG --> ART["产物"]
    LG --> EF["effects 队列"]
```

关键分界在第二条线上：

> **受影响集合由系统算，实质影响由 Agent 判。**

前者是纯粹的图遍历（用到哪条证据、证据在哪个块、块属于哪个版本），错不了也不该让模型猜；后者问的是"这个改动改变了声明的真值或适用范围吗"，只有模型能做，而它做错了要能被人看见——所以差异提案里保留"我判断无影响"的记录。

## 5. 流程一：建库

```mermaid
sequenceDiagram
    autonumber
    participant U as 维护者
    participant CLI as openkb CLI
    participant ING as ingest/
    participant ST as Store
    participant AG as agent/
    participant LG as ledger

    U->>CLI: openkb init 选目录与模型配置
    CLI->>ST: 写 config.toml、建 schema
    U->>CLI: openkb sync
    CLI->>ING: 扫描本地目录
    loop 每个文件
        ING->>ING: 解析 → 按标题层级与条款编号切块
        ING->>ST: 存 document_version 与 block
    end
    ING->>AG: 逐块或逐组抽取声明
    AG->>AG: JSON Schema 结构化输出
    alt 带 span
        AG->>LG: 写 claim 与 evidence
        LG->>ST: 提交
    else 无 span
        LG-->>AG: 拒绝入账
        AG->>AG: 重试一次
        AG-->>U: 仍失败则记入失败清单
    end
```

**"没有 span 就不入账"** 落在 `ledger` 里，不落在 prompt 里。这是账本与"提示词工程"的分水岭：校验在代码里，模型改不掉。

抽取后不设人工确认环节——确认发生在产物层。理由是逐条确认 100 份资料的声明没人受得了，而产物层每条声明都挂证据、都有上下文，人看得懂也判得动。

## 6. 流程二：委托

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant TG as Trigger
    participant TK as Task
    participant PL as Planner
    participant AG as Agent loop
    participant LG as 账本
    participant AR as 产物

    U->>TG: 一个目标，自然语言
    TG->>TG: 生成幂等键
    TG->>TK: Task(deliver)，附带预算
    TK->>PL: 读账本现状与目标
    PL-->>TK: 步骤 + 初始缺口列表
    loop 缺口清零，或预算到顶
        TK->>AG: 取最高优先级缺口
        AG->>LG: 先查账本，结构化且精确
        AG->>AG: 不够才检索原文，模糊且贵
        AG->>LG: 写回新声明，或关闭缺口
        AG->>TK: 每步 checkpoint 落盘
    end
    TK->>AR: Composer 组装产物
    AR->>AR: 系统校验：每条有证据？有无未决冲突？
    alt 校验不通过
        AR-->>AG: 退回补证据
    else 通过
        AR-->>U: 产物，每条都能点回原文段落
    end
```

两个设计意图：

- **缺口清零是唯一停止条件。** 不是"反思三轮"也不是"置信度超过 0.8"——那些都是调参，缺口是能数出来的东西。
- **先查账本再查原文。** 已入账的声明优先复用，这既是准确率来源也是"越用越准"的来源；直接扎进原文是最贵的路径。

## 7. 流程三：复核（核心差异）

其余流程谁都能做，这条只有账本方案能做。

```mermaid
sequenceDiagram
    autonumber
    participant FS as 文件系统 / sync
    participant CH as changes/
    participant TG as Trigger
    participant TK as Task review
    participant AG as agent/
    participant LG as 账本
    participant RV as review
    participant U as 人

    FS->>CH: 内容 hash 变了
    CH->>TG: Trigger(document_changed)
    TG->>TK: Task(review)
    CH->>CH: 新版本入库，块级 diff 对齐旧块新块
    CH->>LG: 反查 变更块 → evidence → claim
    LG-->>CH: 受影响集合，系统算，可复算
    TK->>AG: 逐条判断是否实质影响
    AG-->>TK: material / immaterial + 依据哪一段
    TK->>RV: 生成差异提案，默认只列实质影响
    RV-->>U: 按块呈现
    U->>RV: 接受 / 拒绝，拒绝可写理由
    RV->>LG: 理由回写 edit_mark
    RV->>LG: 新产物版本，只增不改
```

**必须由人确认**，不是产品保守，是信任不可逆：一次错改，用户就永久关掉这个功能。所以拒绝的理由要回写 `edit_mark`——它同时是纠错记录和偏好来源。

## 8. 任务状态机与预算

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running : 调度
    running --> running : 每步 checkpoint
    running --> paused : 预算到顶
    running --> done : 缺口清零
    running --> failed : 不可恢复
    paused --> running : 续跑，不重复计费
    paused --> failed : 放弃
    done --> [*]
    failed --> [*]
```

预算是硬上限，超限**不是抛异常**：

| 层级 | 配额 |
|---|---|
| 单任务 | 40 步 / 15 万 token / 10 分钟 |
| 单步 | 工具调用 ≤ 3 次；单次返回截断 8k token |
| 80% 处 | Planner 只补最关键缺口 |
| 超限 | 落盘退出，`state=paused`，输出缺口清单 |

「续跑不重复计费」能成立，靠的是 `task.checkpoint_json` + `effect` 的 outbox 语义：已经产生的副作用不会重放，已经花掉的 token 记在 `spent_json` 里。

## 9. 检索

```mermaid
flowchart LR
    GAP["缺口"] --> RW["查询改写<br/>由缺口生成，不直投用户原句"]
    RW --> Q["查询"]
    Q --> FTS["FTS5 trigram<br/>BM25"]
    Q --> VEC["向量召回<br/>sqlite-vec"]
    FTS --> RRF["RRF 融合<br/>不手调权重"]
    VEC --> RRF
    RRF --> RR["rerank<br/>默认开"]
    RR --> BLK["块命中"]
    BLK --> SENT["回落到句子级<br/>否则证据不精确"]
    VEC -.->|"sqlite-vec 加载失败"| FALL["降级：FTS5 + numpy 精确检索"]
    FALL --> RRF
```

三个决定：

- **中文必须用 trigram。** FTS5 默认分词器对中文无效，这是硬事实不是偏好。
- **rerank 默认开。** 这个场景要的是"少而准"，不是"多而全"。
- **降级不中断。** 10 万级向量一次矩阵乘几十毫秒，单人交互够用。宁可慢一点，不可用不了。

## 10. 部署与数据

```mermaid
flowchart LR
    P["pipx install openkb"] --> I["openkb init"]
    I --> S["openkb serve"]
    S --> B["浏览器<br/>127.0.0.1:8765"]
    I --> D[("~/.openkb/")]
    S --> D
    D --> DB2["kb.db<br/>账本 · 任务 · 产物"]
    D --> O2["originals/<br/>原文，每个版本"]
    D --> C2["config.toml<br/>base_url · key · embedding 后端"]
```

- 备份 = 复制目录；迁移 = 拷到另一台机器。
- 绑 `0.0.0.0` 需要显式 flag 并打印警告（ADR-0002）。
- React 后期接进来：构建产物进 wheel 的 `package_data`，安装期不依赖 node。
- `openkb doctor` 检查 SQLite 版本、FTS5、trigram、sqlite-vec、模型连通性、磁盘。

## 11. 当前切片在架构里的位置

M0 要建的东西**不在上面任何一张图里**——它是包在系统外面的一把尺子。

```mermaid
flowchart LR
    subgraph H["评测 harness（M0）"]
        GS["gold set<br/>语料 + 5 次修订 + 标注"]
        RUN["eval run"]
        REP["RunReport<br/>四指标 + 四版本号"]
    end

    SUT["ReviewSystem 端口<br/>唯一测试缝"]
    N["noop<br/>零提案，M0 基线"]
    R["regenerate<br/>对手"]
    OR["oracle<br/>仅测试，必须满分"]

    GS --> RUN
    RUN --> SUT
    SUT --> N
    SUT --> R
    SUT --> OR
    RUN --> REP
    REP --> D{"对手够准且更便宜？"}
    D -->|"是"| DOWN["收缩为只提供定位"]
    D -->|"否"| FULL["继续投入依赖机制"]
```

它量的是第 7 节那条流程。M0 存在的意义是：**在写 `core/` 之前，先知道第 7 节值不值得写。**

## 12. 取舍与风险

| 风险 | 表现 | 应对 |
|---|---|---|
| 依赖粒度失准 | 连太粗 → 全是误报；连太细 → 漏检 | 块级对齐 + 评测驱动调粒度 |
| 首次建库成本高 | 用户等太久而流失 | 增量导入、进度可见、失败可重试 |
| 差异不够明显 | 对手"重新生成 + diff"追平 | M0 先验证，不行就收缩到定位能力 |
| 中文解析质量 | docx 表格、pdf 版式 | 先支持常见版式，失败清单显式暴露 |
| 过度自信 | Agent 判"无实质影响"但其实变了 | 差异提案保留"我判断无影响"的记录，可复查 |
| 换域即重构 | 换域就要改内核 | 换域 = 换 `profiles/` 模板，不换内核 |
| 端口被绕过 | `core/` 里冒出 `sqlite3` | CI 三条不变量，见第 3 节 |

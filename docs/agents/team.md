# 团队约定（4 人）

分工模式：**契约持有人 + 三条并行切片**。不按 `core/` `agent/` `server/` 分模块——那样四个水平层都要做完才能 demo 一次。

## 角色

| 角色 | 负责 | 名字 |
|---|---|---|
| **P1 契约持有人兼架构** | 端口契约、报告 schema、`oracle` 校准替身、端到端测试；唯一跑 `/to-spec` 与 `/to-tickets` 的人 | _待填_ |
| **P2 语料与标注** | gold set 格式、合成制度语料、5 次修订与逐条标注、加载校验器 | _待填_ |
| **P3 度量与呈现** | 四个指标的纯函数、`compare`、人读摘要、判定阈值回显 | _待填_ |
| **P4 对手实现** | 模型客户端、预算执行、`regenerate` 对手（重生成 + 条目对齐 + 语义 diff） | _待填_ |

## 硬规则

1. **契约文件第一周只有 P1 能改。** 靠 CODEOWNERS 兜底，不靠自觉。
2. **认领 = assignee。** 对本 ticket 的第一次写操作必须是 `gh issue edit <n> --add-assignee @me`，且发生在动代码之前。
3. **一个 ticket 一个分支一个 PR**，正文 `Closes #<n>`，squash merge。线性历史是 `/code-review` 固定点干净的前提。
4. **只有 P1 跑 `/to-spec` 和 `/to-tickets`。** 四个人各跑一次就是四份 spec、四套互相冲突的 blocking edges。
5. **每个人 `/implement` 之前 `/clear`。** ticket 必须自包含；ticket 里没写清的上下文等于没写。
6. **不 triage 自家 ticket。** `/triage` 只处理外部进来的 bug 与需求。
7. **评测报告的四个版本字段必须齐全**（模型 id、gold set 哈希、声明模板版本、价格表版本）。缺一个即判为无效运行——否则四个人的数字不可比。
8. **领域词汇只认 `CONTEXT.md`。** 新词走 `/domain-modeling` 并写回去，否则四个人的 spec、票名、测试名会各说各话。

## frontier 的定义

**open + 无 assignee + 所有 blocker 已关闭**，编号最小者优先。

blocking 边必须用 GitHub 原生依赖（`gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by`），不要只写正文里的 `Blocked by:` 文本——文本不会被任何查询识别。

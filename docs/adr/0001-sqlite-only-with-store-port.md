# 后端只做 SQLite，Postgres 放在端口之后

企业知识库的默认联想是 Postgres + pgvector，但本项目的首要承诺是 pipx 一条命令跑起来，而 pgvector 是 Postgres 扩展，需要外部实例。决定：v1 只实现 SQLite（FTS5 + sqlite-vec），所有数据访问经过 `Store` 端口，Postgres 作为该端口的第二个实现留给 v2。

## Considered Options

- **Postgres + pgvector**：违背一键安装；且若向量进 PG，账本必须一并迁移，否则跨库不在同一事务里，成本是双份仓储层、双份迁移和双份测试矩阵。
- **Postgres + pgvector + Milvus**：两套向量库需要双写，必然出现一侧未写入的瞬间；且 Milvus 面向千万到亿级分布式场景，与单人语料规模不匹配。
- **只做 SQLite 且不留端口**：仓储层会被 `sqlite3` 调用渗透，将来加 PG 等于重写。

## Consequences

- README 必须给出后端能力矩阵，写清默认、降级路径和 roadmap。
- `sqlite-vec` 加载失败时降级为 FTS5 + numpy 精确检索，功能不中断。
- 检索质量靠结构感知切分、混合召回、rerank 和查询改写，而不是换向量库。
- 核心层不出现任何 `sqlite3` 字样；这正是端口的验收标准。

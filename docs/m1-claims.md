# M1 制度合规声明入账

先使用 M1 的入库入口导入一份制度资料。取入库结果里的版本 ID，将其传给声明抽取命令。真实模型配置沿用 M0 形式，默认从用户目录读取，也可以显式指定。抽取只会把指定资料版本的解析文本发给配置的模型端点。

```powershell
uv run openkb claims extract <version-id> --database ledger.db --config <config.toml>
uv run openkb claims list <version-id> --database ledger.db
uv run openkb ingest evidence-show <evidence-id> --database ledger.db
```

- 抽取返回模型标识、调用用量、已入账声明和被拒绝的声明及原因。可用 `claims list` 在新进程里复查持久化结果。
- 模型应逐字引用指定资料块。系统校验资料版本、块、逐字原文和字符偏移。重复引文需要明确偏移，或者改用块内唯一的引文。证据定位仍由已有证据命令呈现。
- 每组模型输出的证据最多核验三轮，每轮最多两次结构化输出尝试，共用六步、12 万 token 和 180 秒的模型预算。被模型省略的待修正声明仍保留拒绝原因。仍有拒绝项时返回失败状态，已核验声明与各自证据按一次事务写入。
- 核验保证引文确实出现在对应版本的原文。声明是否忠于引文、是否遗漏适用条件，仍需人工核对。

# M0 模型配置与预算

模型适配器读取 ~/.openkb/config.toml。下面的价格和密钥仅用于说明配置形状。

```toml
[model]
base_url = "https://example.com/v1"
api_key = "YOUR_KEY"
model_id = "example-model"

[prices]
version = "your-price-table-v1"

[prices.models.example-model]
input_per_million = 1.0
output_per_million = 2.0
```

金额沿用价格表的币种。价格表没有对应模型时，金额记为 JSON null。

- 调用 load_model_config() 后创建 ModelClient，并在运行任何案例前调用 preflight()。预检通过 OpenAI 兼容的 GET /models 检查端点、凭据与模型。
- 针对每个案例创建一个 session，各阶段在同一 session 上调用 generate(prompt, schema, gaps=...)。调用传入系统已知的缺口，模型客户端负责保留缺口。
- generate 通过 POST /chat/completions 请求 JSON，使用 JSON Schema 本地校验。格式不合法时最多按指定次数重试，每次重试计一步，并记入 session.calls 和 session.usage。
- 预算触顶时返回 paused。默认在 ~/.openkb/paused/ 下原子写入独立 JSON 文件，保存缺口、暂停原因和已知用量。检查点保留任务状态供后续恢复逻辑读取。
- 调用前保守预留输入 token，并用 max_completion_tokens 限制输出。兼容服务必须支持该参数并返回 usage。供应商的分词和远端执行由供应商控制。若服务实际超额，保留已用统计，丢弃结果并暂停。客户端墙钟到限就返回，服务器上已排程的请求可能继续计费。

协议与校验工具见 [OpenAI Chat API](https://developers.openai.com/api/reference/resources/chat) 和 [jsonschema](https://python-jsonschema.readthedocs.io/en/stable/validate/)。

# Word 审阅的百份资料业务验收

在两个独立工作区提交同一个 Word 审阅问题。先用两份原件建立基线，再让系统从完整的 100 份资料中自动选择依据。逐项核对结论、证据位置、资料版本和下载的原件。

## 准备资料

使用 [M1 批量资料清单](../goldsets/m1-batch-samples/manifest.json) 中固定的 16 份 DOCX、84 份 PDF。清单记录原件的出处和 SHA-256。从仓库根目录运行以下 PowerShell 命令。若已准备好资料，把 `$batch` 指向现有目录，执行带 `--verify-only` 的命令。

```powershell
$batch = Join-Path $env:TEMP 'openkb-m1-batch'
uv run --no-project python goldsets/m1-batch-samples/prepare.py $batch
if ($LASTEXITCODE -ne 0) { throw '准备资料失败' }
uv run --no-project python goldsets/m1-batch-samples/prepare.py $batch --verify-only
if ($LASTEXITCODE -ne 0) { throw '原件校验失败' }

$qa = Join-Path $env:TEMP ('openkb-word-audit-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$pair = Join-Path $qa 'pair'
$full = Join-Path $qa 'full'
uv run openkb init --data-dir $pair
if ($LASTEXITCODE -ne 0) { throw '初始化基线工作区失败' }
$word = Join-Path $qa 'section508-word.docx'
Copy-Item -LiteralPath (Join-Path $batch '508-word-checklist.docx') -Destination $word
uv run openkb serve --data-dir $pair --port 8767
```

基线中的 `section508-word.docx` 与批量目录中的 `508-word-checklist.docx` 是同一份原件。

## 在浏览器提交同一个问题

1. 在 [基线工作区](http://127.0.0.1:8767/documents) 导入 `$batch` 下的 `508-applicability-checklist.docx` 和上一步复制的 `$word`，确认资料列表只有这两个版本。记下各自的版本标识。
2. 回到工作区，在「审阅目标」中输入下列问题，提交审阅。「指定资料版本」保留默认设置，让系统从工作区中自行选择。
3. 在两条结论下分别展开「查看声明与原文证据」，再点「打开原文与原件」。核对表格中给出的定位，记录审阅页、证据页和版本标识。下载原件，用 `Get-FileHash -Algorithm SHA256` 与固定清单核对。
4. 停止基线工作区。在同一 PowerShell 窗口按下一节命令创建批量工作区。

> 这份非网页 Word 有一张单页数据表和一张跨页数据表，两张都没设置重复表头。按 Section 508 检查资料，需要检查 Bypass Blocks 吗？哪些表需要改？

| 核对项 | 两次运行都应得到的结论 | 应回到的原文 |
| --- | --- | --- |
| 适用范围 | 非网页 Word 不要求检查 2.4.1 Bypass Blocks。 | `508-applicability-checklist.docx` 第 71 段。摘录包含 `2.4.1 Bypass Blocks (not required for non-web documents and software)`。 |
| 数据表格 | 单页和跨页数据表格均适用重复表头要求，两张表都需要补设。 | 基线的 `section508-word.docx` 或批量的 `508-word-checklist.docx` 第 9 张表第 4 行。摘录为 `Tables repeat their header row.` |

每次运行记录各自工作区的完整版本标识。两次运行的资料版本标识可能不同，下载原件的 SHA-256 应一致。页面应把题目描述的表格情况标为用户提供，并显示是否跨页仍需按实际版式核对。

## 导入全部资料并复跑

```powershell
uv run openkb init --data-dir $full
if ($LASTEXITCODE -ne 0) { throw '初始化批量工作区失败' }
$env:PYTHONIOENCODING = 'utf-8'
$reportPath = Join-Path $qa 'batch-result.json'
uv run openkb ingest batch $batch --database (Join-Path $full 'kb.db') |
    Tee-Object -FilePath $reportPath
if ($LASTEXITCODE -ne 0) { throw '批量入库失败' }
$report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
if ($report.completed -ne 100 -or $report.pending -ne 0 -or @($report.failures).Count -ne 0) {
    throw '需要 100 份全部入库，且无失败或待处理资料'
}
uv run openkb serve --data-dir $full --port 8768
```

在 [批量工作区](http://127.0.0.1:8768/documents) 确认相关资料已入库，然后回到首页复跑同一问题，保持资料版本为默认选择。按表格逐项对照结果。PDF 检查或修复指南中同名的 Bypass Blocks 和重复页眉规则不应出现在本次 Word 审阅的证据里。

## 验收记录

2026-11-25 在 Windows 上通过已安装的 CLI 导入固定批次，100 份全部成功，无失败或待处理。随后使用运行本分支源码的服务，在 Chrome 158 从两份原件和百份资料两个工作区分别提交相同问题。

| 核对项 | 两份原件 | 百份资料 |
| --- | --- | --- |
| 适用范围 | `508-applicability-checklist.docx` 第 71 段，原文与下载哈希通过。 | 同一原文位置与哈希，版本回点为批量工作区记录。 |
| 数据表格 | `section508-word.docx` 第 9 张表第 4 行，两张表均需补设。 | `508-word-checklist.docx` 同一位置，结论与原文一致，下载文件哈希一致。 |
| 证据数量 | 两条，分别来自两份规则原件。 | 仍为两条，未引用其他格式的同词规则。 |

自动回归覆盖相同的两个场景。它在临时工作区经公开入库入口导入资料，再通过真实本机 HTTP 服务提交审阅、打开证据并核对下载字节。资料尚未准备好时，默认测试会跳过这两项。

```powershell
$env:OPENKB_WORD_AUDIT_BATCH = $batch
uv run python -m unittest discover -s tests -p test_word_audit_corpus_web.py
```

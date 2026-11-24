# M1 手动业务验收

用公开法规、技术文档和 Word 检查清单，验收从资料入库到原文回点的完整流程。每个场景记录「通过 / 失败 / 未执行」、实际结果和输出文件。第 5 项会使用真实模型，其余业务命令无需模型。

## 0. 准备工作

**测什么**：确认验收使用固定的公开原文件和可运行的本地环境。用 PowerShell 7，在包含 `pyproject.toml` 的 **M1 工作树根目录**执行。下面各节复用同一终端的变量。新建的验收目录在系统临时目录。

```powershell
if (!(Test-Path -LiteralPath './goldsets/m1-public-samples/prepare.py' -PathType Leaf) -or
    !(Test-Path -LiteralPath './goldsets/m1-batch-samples/prepare.py' -PathType Leaf)) {
    throw '当前目录缺少 M1 样本脚本。请先进入包含这两个脚本的 M1 工作树根目录。'
}
uv sync --python 3.12
if ($LASTEXITCODE -ne 0) { throw 'uv sync 失败' }
$env:PYTHONIOENCODING = 'utf-8'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$run = Join-Path $env:TEMP ('openkb-m1-manual-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$samples = Join-Path $run 'public'
$batch = Join-Path $run 'batch'
$db = Join-Path $run 'ledger.sqlite'
$null = New-Item -ItemType Directory -Path $run
uv run --with 'pymupdf==1.27.2.2' python goldsets/m1-public-samples/prepare.py $samples
if ($LASTEXITCODE -ne 0) { throw '小样本准备失败' }
uv run --no-project python goldsets/m1-batch-samples/prepare.py $batch
if ($LASTEXITCODE -ne 0) { throw '100 份样本准备失败' }

function Invoke-KbJson {
    $result = uv run openkb @args
    if ($LASTEXITCODE -ne 0) { throw ($result -join "`n") }
    ($result -join "`n") | ConvertFrom-Json
}
$run
```

**通过条件**：两个准备脚本均通过 SHA-256 核验。批量目录有 16 份 DOCX、84 份 PDF。[小样本清单](../goldsets/m1-public-samples/README.md) · [批量清单](../goldsets/m1-batch-samples/README.md)

## 1. 导入三种原文件并取回旧版本

**测什么**：法规 PDF、技术 Markdown 和 Word 原文件都能入库。同一内容重导时复用版本，导入新版后仍能取回旧原件。

```powershell
$pdf0 = (Invoke-KbJson ingest add (Join-Path $samples 'cfr-255-2022.pdf') --database $db --document cfr255).id
$pdf1 = (Invoke-KbJson ingest add (Join-Path $samples 'cfr-255-2024.pdf') --database $db --document cfr255).id
$md0 = (Invoke-KbJson ingest add (Join-Path $samples 'k8s-components-before.md') --database $db --document k8s-components).id
$md1 = (Invoke-KbJson ingest add (Join-Path $samples 'k8s-components-after.md') --database $db --document k8s-components).id
$wordFile = Join-Path $samples 'section508-word.docx'
$word = (Invoke-KbJson ingest add $wordFile --database $db --document word-checklist).id
$wordAgain = (Invoke-KbJson ingest add $wordFile --database $db --document word-checklist).id
$retained = Join-Path $run 'retained-2022.pdf'
Invoke-KbJson ingest original $pdf0 --database $db --out $retained
if ($wordAgain -ne $word -or $pdf0 -eq $pdf1 -or $md0 -eq $md1) { throw '版本行为异常' }
if ((Get-FileHash $retained).Hash -ne (Get-FileHash (Join-Path $samples 'cfr-255-2022.pdf')).Hash) {
    throw '旧版原文不一致'
}
$wordBlocks = (Invoke-KbJson ingest show $word --database $db).blocks
$wordBlocks | Where-Object kind -eq 'table_row' | Group-Object { $_.locator.table } | Select-Object Name,Count
```

**通过条件**：三个格式都可导入，新旧版本 ID 不同，重复导入 Word 返回相同 ID。导出的旧版 PDF 哈希相同。Word 样本应检出 17 张表，任选一行用 Word 对照原文和行号。

## 2. 对齐受控插入和真实版本

**测什么**：插入文字造成页码或行号变化时，未修改的块仍能对应。真实修订中的新增条款和歧义有独立记录。

```powershell
$pdfInsert = (Invoke-KbJson ingest add (Join-Path $samples 'cfr-255-controlled-insertion.pdf') --database $db --document cfr255).id
$mdInsert = (Invoke-KbJson ingest add (Join-Path $samples 'k8s-controlled-insertion.md') --database $db --document k8s-components).id
foreach ($pair in @(
    @{name='pdf-insertion'; before=$pdf0; after=$pdfInsert},
    @{name='md-insertion'; before=$md0; after=$mdInsert},
    @{name='pdf-history'; before=$pdf0; after=$pdf1},
    @{name='md-history'; before=$md0; after=$md1}
)) {
    $alignment = Invoke-KbJson ingest align $pair.before $pair.after --database $db
    $alignment | ConvertTo-Json -Depth 30 | Set-Content -Encoding utf8 (Join-Path $run ($pair.name + '.json'))
    [pscustomobject]@{
        Case=$pair.name; Matched=$alignment.matches.Count; Added=$alignment.added.Count
        Removed=$alignment.removed.Count; Ambiguous=$alignment.ambiguous.Count
        Normalization=$alignment.normalization_version
    }
}
```

**通过条件**：受控 PDF 69/69、Markdown 27/27，都是 1 块新增、零移除、零错配。真实版本按[人工锚点表](../goldsets/m1-public-samples/README.md#真实版本对手工标注)逐项核对 `matches`，PDF 正文标题 6/6、Markdown 正文锚点 5/5。核对 §255.6 正文列为新增，实际修订或歧义应可查阅。汇总匹配数不能替代锚点核对。

## 3. 保存 12 条证据并回点

**测什么**：每条证据都绑定正确资料版本、原文片段和位置。下面的片段和预期位置独立取自验收锚点。用原文件抽查显示的位置，特别是 DOCX 表格和 PDF 正文。

```powershell
$checks = @(
    @{v=$word; q='Use this checklist to record MS Word 508 Accessibility results.'; loc=@{paragraph=2}},
    @{v=$word; q='The filename identifies the document or its purpose.'; loc=@{table=1; row=3}},
    @{v=$word; q='The document uses MS Word heading styles.'; loc=@{table=2; row=1}},
    @{v=$word; q='“Text wrapping” is set to “None” in Table Properties.'; loc=@{table=5; row=3}},
    @{v=$pdf0; q='Purpose and definitions.'; loc=@{page=1}; clause='255.0'},
    @{v=$pdf0; q='General considerations.'; loc=@{page=3}; clause='255.1'},
    @{v=$pdf0; q='Consumer endorsements.'; loc=@{page=4}; clause='255.2'},
    @{v=$pdf0; q='Expert endorsements.'; loc=@{page=6}; clause='255.3'},
    @{v=$md0; q='本文档概述了一个正常运行的 Kubernetes 集群所需的各种组件。'; loc=@{line_start=33}},
    @{v=$md0; q='在每个节点上运行，维护运行的 Pod 并提供 Kubernetes 运行时环境：'; loc=@{line_start=106}},
    @{v=$md0; q='确保 Pod 及其容器正常运行。'; loc=@{line_start=108; line_end=109}},
    @{v=$md0; q='Kubernetes 允许灵活地部署和管理这些组件。'; loc=@{line_start=172}}
)
$locations = foreach ($check in $checks) {
    $version = Invoke-KbJson ingest show $check.v --database $db
    $anchors = @($version.blocks | Where-Object {
        $b = $_
        $wrong = @($check.loc.Keys | Where-Object { $b.locator.$_ -ne $check.loc[$_] })
        $b.text.Contains($check.q) -and !$wrong.Count -and
            (!$check.clause -or ($b.clause -eq $check.clause -and $b.kind -eq 'heading'))
    })
    if ($anchors.Count -ne 1) { throw "预标注块无法唯一定位：$($check.q)" }
    $saved = Invoke-KbJson ingest evidence-add $check.v $anchors[0].id --quote $check.q --database $db
    $hit = Invoke-KbJson ingest evidence-show $saved.id --database $db
    if ($hit.version_id -ne $check.v -or $hit.document_id -ne $version.document_id -or $hit.quote -cne $check.q) {
        throw "版本、资料或引文错误：$($check.q)"
    }
    foreach ($key in $check.loc.Keys) {
        if ($hit.locator.$key -ne $check.loc[$key]) { throw "位置错误：$($check.q)" }
    }
    $hit
}
$locations | ConvertTo-Json -Depth 20 | Set-Content -Encoding utf8 (Join-Path $run 'evidence-locations.json')
$locations | Select-Object original_name,quote,locator
```

**通过条件**：12/12 成功。第 106 行的旧版 Markdown 证据仍回到第 106 行。在第 2 项的受控插入报告中，同一段落在新版本位于第 108 行。下面再验证一次，系统是否会拒绝拿旧块的 ID 给新版添加证据：

```powershell
$old = $locations[9]
uv run openkb ingest evidence-add $mdInsert $old.block_id --quote $old.quote --database $db
if ($LASTEXITCODE -eq 0) { throw '错误地接受了跨版本块 ID' }
```

## 4. 跑 100 份批量建库、中断恢复和坏文件重试

**测什么**：实际资料的批量入库规模、按文件保存进度，以及是否只重试指定的坏文件。批量验收使用独立数据库。

```powershell
$batchDb = Join-Path $run 'batch.sqlite'
$first = Invoke-KbJson ingest batch $batch --database $batchDb --limit 50
if ($first.state -ne 'paused' -or $first.completed -ne 50 -or $first.pending -ne 50) {
    throw '第一次暂停的进度不符合预期'
}
$id = $first.batch_id
Invoke-KbJson ingest status $id --database $batchDb
# 下一条命令开始处理第 51 份时按 Ctrl+C。若第 51 份已处理完，以实际检查点为准。
uv run openkb ingest resume $id --database $batchDb
$checkpoint = Invoke-KbJson ingest status $id --database $batchDb
$checkpoint | Select-Object state,completed,pending
$last = Invoke-KbJson ingest resume $id --database $batchDb
$last | Select-Object state,total,completed,pending,failures
$last | ConvertTo-Json -Depth 15 | Set-Content -Encoding utf8 (Join-Path $run 'batch-result.json')
if ($last.state -ne 'done' -or $last.completed -ne 100) { throw '批量入库未完成' }
```

**通过条件**：第一次暂停时 50 完成、50 待处理。中断后恢复日志从第一个未完成文件开始，已确认完成的 50 份不再出现。最终 100/100、无待处理、无失败。如果手动按键错过了第 51 份窗口，使用下方补充回归精确验证该时点。

```powershell
$repair = Join-Path $run 'repair'
$null = New-Item -ItemType Directory -Path $repair
Copy-Item -LiteralPath $wordFile -Destination (Join-Path $repair '01-good.docx')
$broken = Join-Path $repair '02-repair.docx'
'模拟损坏文件' | Set-Content -Encoding utf8 $broken
$failedJson = uv run openkb ingest batch $repair --database $batchDb
if ($LASTEXITCODE -ne 1) { throw '预期该批次因单个坏文件退出 1' }
$failed = ($failedJson -join "`n") | ConvertFrom-Json
$failed.failures
Copy-Item -LiteralPath $wordFile -Destination $broken -Force
$repaired = Invoke-KbJson ingest retry $failed.batch_id $broken --database $batchDb
$repaired | Select-Object state,completed,failures
```

**通过条件**：失败清单只列坏文件，并给出解析原因。替换后，重试日志只处理该文件，批次完成 2/2。

## 5. 用真实模型提取合规声明并回点

**测什么**：真实模型能否把公开合规资料变成可核验的声明。查阅持久化结果可以区分模型输出与已入账记录。以下示例读取你已有项目里的模型配置。若路径不同，只修改 `$config`。

```powershell
$config = 'D:\Practice\work\project\my_project\Open-Super-Agent-KB\.openkb\config.toml'
if (!(Test-Path -LiteralPath $config -PathType Leaf)) { throw '请将 $config 改为你实际使用的配置文件路径' }
$policy = Join-Path $batch '508-applicability-checklist.docx'
$policyVersion = (Invoke-KbJson ingest add $policy --database $db --document accessibility-policy).id
$out = uv run openkb claims extract $policyVersion --database $db --config $config
$exitCode = $LASTEXITCODE
if (!$out) { throw "抽取未返回报告，退出码 $exitCode" }
$out | Set-Content -Encoding utf8 (Join-Path $run 'claims-extract.json')
$extracted = ($out -join "`n") | ConvertFrom-Json
$extracted | Select-Object state,model_id,usage,rejected,error
$persisted = Invoke-KbJson claims list $policyVersion --database $db
if (!$persisted.claims.Count) { throw '没有可供核对的已入账声明' }
foreach ($claim in ($persisted.claims | Select-Object -First 5)) {
    $claim.text
    foreach ($ref in $claim.evidence) {
        $hit = Invoke-KbJson ingest evidence-show $ref.id --database $db
        if ($hit.version_id -ne $policyVersion -or $hit.quote -cne $ref.quote) { throw '声明证据与入账记录不符' }
        $hit | Select-Object original_name,quote,locator
    }
}
```

**通过条件**：正常流程返回 `done`，有实际模型 ID、用量，以及至少一条可在新命令中查出的声明。打开公开原件，抽查上面 5 条声明的语义、适用条件和证据是否吻合，实际数量由模型决定。有拒绝时保留原因，并核对被拒绝项未出现在同一次运行的入账清单中。正常流程与拒绝处理分别记录结果。

## 6. 补充回归

以下命令供你亲自复跑关键异常。它们补足手动操作难以稳定触发的时序，不访问真实模型。

| 用例 | 验收目的 |
|---|---|
| 批量恢复 | 精确在第 51 份处理中断。以后把前 50 份输入移走，恢复仍须到 100/100。 |
| 并发提交 | 后到的恢复操作不能为已完成的文件留下额外版本，事务中断也不能留下半提交的数据。 |
| 声明与证据 | 拒绝无效引文，保留前轮已验证的声明。多引文和重试期间，同一版本只读取一次。 |
| 格式边界 | 验证合并单元格、表格引文边界，以及无文本或有旋转文字时的 PDF 定位提示。 |

```powershell
$env:OPENKB_M1_SAMPLES = $samples
$env:OPENKB_M1_BATCH = $batch
foreach ($pattern in 'test_ingest_batch.py','test_claims_cli.py','test_evidence_cli.py','test_ingest_docx.py','test_ingest_cli.py') {
    uv run python -m unittest discover -s tests -p $pattern -v
    if ($LASTEXITCODE -ne 0) { throw "回归失败：$pattern" }
}
```

验收结束后保留 `$run` 路径、四份对齐报告、证据位置、批量结果和声明抽取报告。失败项记录对应文件、版本 ID 或批次 ID，以及实际错误信息。

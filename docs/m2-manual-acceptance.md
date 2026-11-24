# M2 手动业务验收

本次验收使用 M1 的两份真实 Section 508 原件，验证同一委托能交付有依据的自查清单，也能报告预算用尽时的实际缺口。示例在项目根目录用 PowerShell 运行，新账本和报告保存在临时目录。

`deliver run` 运行固定的 Section 508 非网页 Word 自查模板，保存委托消息和指定的资料版本。清单包含适用范围、标题结构、数据表格和图片说明四项。`deliver propose` 将新增条目建议保存为待人工处理的记录，供后续委托模板调整时参考。

## 1. 准备两份真实原件

**测什么**　确认手里的资料与 M1 验收使用的是同一版本。若原件保存在其他位置，用实际路径替换示例中设置两个文件路径的部分。缺少原件时可用 M1 准备流程获取。 [M1 手动业务验收](m1-manual-acceptance.md#0-准备工作)

```powershell
$m1 = Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter 'openkb-m1-manual-*' |
    Where-Object {
        (Test-Path -LiteralPath (Join-Path $_.FullName 'batch/508-applicability-checklist.docx')) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName 'public/section508-word.docx'))
    } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (!$m1) { throw '请准备 M1 原件，或把 $policyFile 和 $wordFile 指向已有的两份文件。' }
$policyFile = Join-Path $m1.FullName 'batch/508-applicability-checklist.docx'
$wordFile = Join-Path $m1.FullName 'public/section508-word.docx'
if ((Get-FileHash -LiteralPath $policyFile).Hash -ne '66baf3c2df168d60296f080b95be99c631c4b3529f876b9e46d5fa3ce327c82d' -or
    (Get-FileHash -LiteralPath $wordFile).Hash -ne '943fba7431ccc815e8a997937baaa2768bdf0baee318ce077e9cba8a8b028475') {
    throw '原件 SHA-256 与 M1 清单不符'
}
uv sync --python 3.12
if ($LASTEXITCODE -ne 0) { throw 'uv sync 失败' }
$env:PYTHONIOENCODING = 'utf-8'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$run = Join-Path $env:TEMP ('openkb-m2-manual-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$null = New-Item -ItemType Directory -Path $run
$db = Join-Path $run 'ledger.sqlite'
$message = '请基于已入库的 Section 508 资料，为非网页 Word 文档生成一份无障碍自查清单。'

function Invoke-M2Json {
    param([string[]]$Command, [int]$ExpectedExit = 0)
    $output = uv run --no-sync openkb @Command
    if ($LASTEXITCODE -ne $ExpectedExit) { throw "退出码 $LASTEXITCODE，预期 $ExpectedExit" }
    ($output -join [Environment]::NewLine) | ConvertFrom-Json
}

$scope = (Invoke-M2Json @('ingest','add',$policyFile,'--database',$db)).id
$word = (Invoke-M2Json @('ingest','add',$wordFile,'--database',$db)).id
```

## 2. 交付清单并逐项回点

**测什么**　确认系统能在证据充分时交付清单，并且每项检查方法都有可核对的原文依据。

```powershell
$done = Invoke-M2Json @('deliver','run',$message,'--source',$scope,'--source',$word,'--database',$db)
$done | ConvertTo-Json -Depth 16 | Set-Content -Encoding utf8 (Join-Path $run 'done.json')
if ($done.state -ne 'done' -or !$done.artifact -or @($done.covered).Count -ne 4 -or $done.gaps) {
    throw '完整清单验收失败'
}
foreach ($slot in $done.covered) {
    $slot | Format-List slot_id,check,method
    foreach ($evidence in $slot.evidence) {
        $hit = Invoke-M2Json @('ingest','evidence-show',$evidence.id,'--database',$db)
        $hit | Format-List original_name,version_id,quote,locator
    }
}
```

打开两份原件，对照实际报告中的版本、原文和位置。适用范围应回到 `2.4.1 Bypass Blocks`，标题结构应回到 Word 原件的 **Headings**，数据表格应回到 **Data Tables** 的重复表头，图片说明应回到 **Images and other Objects** 的替代文本或正文说明。表格和图片项应写明各自的适用条件。

## 3. 预算用尽与错引回归

**测什么**　验证缺少依据时，系统准确报告缺口，避免未完成清单被当成已交付的产物。

```powershell
$paused = Invoke-M2Json @('deliver','run',$message,'--source',$scope,'--max-steps','4','--database',$db) -ExpectedExit 1
$paused | ConvertTo-Json -Depth 16 | Set-Content -Encoding utf8 (Join-Path $run 'paused.json')
if ($paused.state -ne 'paused' -or $paused.artifact -or
    (($paused.gaps.title -join '、') -cne '标题结构、数据表格、图片说明')) {
    throw '缺口验收失败'
}
Invoke-M2Json @('deliver','show',$paused.id,'--database',$db) | Format-List id,state,gaps
uv run --no-sync python -m unittest discover -s tests -p 'test_deliver*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'M2 回归测试失败' }
$run
```

回归用例在隔离数据中进一步复现只缺「数据表格」的场景。标题引文仍能定位到原文，任务应保留唯一缺口并返回 `paused`。

## 4. 未决冲突与解决

**测什么**　即使 4 项都有依据，一条未决冲突也会阻止交付。人工解决冲突后，重新发起同一委托并核对状态。

```powershell
$conflict = Invoke-M2Json @('deliver','flag-conflict','data_tables',$word,'重复表头规则待人工核对','--database',$db)
$blocked = Invoke-M2Json @('deliver','run',$message,'--source',$scope,'--source',$word,'--max-steps','6','--database',$db) -ExpectedExit 1
$blocked | ConvertTo-Json -Depth 16 | Set-Content -Encoding utf8 (Join-Path $run 'conflict.json')
if ($blocked.state -ne 'paused' -or $blocked.artifact -or
    @($blocked.conflicts.id) -notcontains $conflict.id -or ($blocked.gaps.id -join ',') -cne 'data_tables') {
    throw '未决冲突未被正确拦截'
}
$resolved = Invoke-M2Json @('deliver','resolve-conflict',$conflict.id,'--database',$db)
$rechecked = Invoke-M2Json @('deliver','run',$message,'--source',$scope,'--source',$word,'--database',$db)
if ($resolved.state -ne 'resolved' -or $rechecked.state -ne 'done' -or @($rechecked.artifact.items).Count -ne 4) {
    throw '冲突解决后的重新校验失败'
}
```

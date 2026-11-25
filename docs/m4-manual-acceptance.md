# M4 业务级手动验收

用 pipx 安装的本地应用维护一份 Word 无障碍自查清单。输入是两份真实 Section 508 原件和一份模拟修订。准备 Windows、Python 3.12、pipx 和能调用模型列表及聊天补全的 OpenAI 兼容配置。 [M4 规格](./specs/0005-m4-local-web-and-packaging.md)

## 1. 准备两种交付物

**测什么**　演示资料来源和内容可核对，wheel 可以独立安装。

在有仓库和 uv 的机器上，从仓库根目录执行：

```powershell
$bundle = Join-Path $env:TEMP ('openkb-m4-bundle-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$py = uv python find 3.12
if ($LASTEXITCODE -ne 0) { throw '未找到 Python 3.12' }
& $py goldsets/m4-demo/prepare.py (Join-Path $bundle 'demo')
if ($LASTEXITCODE -ne 0) { throw '准备演示包失败' }
uv build --wheel --out-dir (Join-Path $bundle 'wheel')
if ($LASTEXITCODE -ne 0) { throw '构建 wheel 失败' }
```

脚本用 M1 清单检查两份原件的 SHA-256。如已保留原件，给命令增加 `--word <Word原件路径> --applicability <适用性原件路径>`，可离线准备。将生成的 `demo` 和 `wheel` 目录复制到测试机的同一个目录。若在同一台机器验收，可直接使用刚生成的 `$bundle`。

| 演示文件 | 用途 |
| --- | --- |
| `demo/originals/508-applicability-checklist.docx` | 官方适用性原件。 |
| `demo/originals/section508-word.docx` | 官方 Word 检查原件。 |
| `demo/simulated/section508-word-SIMULATED-REVISION.docx` | 测试用模拟修订。将 “Tables repeat their header row.” 改为 “Only data tables that span multiple pages must repeat their header row.”。 |
| `demo/manifest.json` | 原件来源、复用条款、文件 SHA-256 和模拟规则。 |

**通过条件**　两份原件与 M1 记录的哈希一致，模拟副本单独存放且明确标注。wheel 与演示资料分别交付。

## 2. 用 pipx 安装、初始化并连接真实模型

**测什么**　用户能从源码目录外用安装包启动应用，检查本机数据和模型连接。

在测试 PowerShell 将 `$bundle` 设为上一步生成或复制到的目录，`$py` 设为测试机 Python 3.12 可执行文件路径，`$config` 设为现有的模型 TOML 路径。继续执行：

```powershell
$demo = Join-Path $bundle 'demo'
$wheel = @(Get-ChildItem -LiteralPath (Join-Path $bundle 'wheel') -Filter '*.whl')
if ($wheel.Count -ne 1) { throw '安装目录应含一个 wheel' }
$run = Join-Path $env:TEMP ('openkb-m4-acceptance-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $run | Out-Null
Set-Location -LiteralPath $run
$env:PIPX_HOME = Join-Path $run 'pipx-home'
$env:PIPX_BIN_DIR = Join-Path $run 'pipx-bin'
$env:PYTHONIOENCODING = 'utf-8'
$accept = Join-Path $run 'accept'
$reject = Join-Path $run 'reject'
pipx install --python $py $wheel[0].FullName
if ($LASTEXITCODE -ne 0) { throw 'pipx 安装失败' }
$openkb = Join-Path $env:PIPX_BIN_DIR 'openkb.exe'
& $openkb init --data-dir $accept --config $config
if ($LASTEXITCODE -ne 0) { throw '初始化失败' }
& $openkb doctor --data-dir $accept | Tee-Object (Join-Path $run 'doctor-normal.txt')
if ($LASTEXITCODE -ne 0) { throw '诊断失败' }
& $openkb serve --data-dir $accept --port 8765
```

如果 pipx 提示本机 uv 版本不符合要求，在支持该选项的 pipx 中用 `pipx install --backend pip --python $py $wheel[0].FullName` 安装，再继续初始化。

打开 [本地工作区](http://127.0.0.1:8765)，在「连接状态」查看当前模型。可用浏览器开发者工具检查页面和请求中是否包含模型密钥。终端保持运行，停止服务时按 Ctrl+C。

**通过条件**　页面正常加载样式。doctor 的数据目录、SQLite、模型配置和模型端点四项通过。网页显示正确模型和「端点可达」，密钥留在本机配置。

## 3. 导入、生成清单并逐项回点

**测什么**　入库的原件能交付恰好四项规则，每项都能追溯到出处及资料版本。

1. 在「资料」依次导入 `demo/originals` 下两份 DOCX，记录导入状态和版本标识。
2. 在「清单」勾选这两个版本，点击「生成清单」。记录清单页面地址。
3. 在四项中逐一打开「原文证据」。核对仍显示清单版本 1，证据栏能查看原文、资料版本和具体位置。
4. 下载对应原件，用 `Get-FileHash -Algorithm SHA256 <下载路径>` 与 `demo/manifest.json` 的哈希比对。

| 清单条目 | 应定位的依据 |
| --- | --- |
| 适用范围 | 适用性原件的 `2.4.1 Bypass Blocks`，说明非网页文档不要求此项。 |
| 标题结构 | Word 原件的 **Headings**。 |
| 数据表格 | Word 原件 **Data Tables**，第 9 张表第 4 行，“Tables repeat their header row.”。 |
| 图片说明 | Word 原件的 **Images and other objects**。 |

**通过条件**　仅有这四项，证据可找到真实位置，下载文件哈希与相应原件一致。

## 4. 提交模拟修订并接受提案

**测什么**　只对受影响条目提出修改，人确认后生成新版，旧版仍可查证。

1. 在「复核」选择刚生成的清单及原 `section508-word.docx` 版本，上传 `demo/simulated/section508-word-SIMULATED-REVISION.docx`，点击「提交复核」。记下复核页地址。
2. 核对只有「数据表格」出一条提案，旧规则覆盖全部数据表格，新建议限定跨页表格。「新版证据」显示模拟副本的版本、位置和跨页原句。待确认期间清单仍为 v1。
3. 在宽屏查看并排的旧规则和修改建议。改到约 390 像素宽检查上下阅读顺序和操作。用 Tab 聚焦「接受提案」，检查焦点后按 Enter。
4. 打开清单版本 1 和 2 的「数据表格」证据，核对旧句和跨页新句。下载两版 DOCX，比对哈希。检查其余三项的内容及证据。

**通过条件**　接受只更新「数据表格」，清单生成 v2。两版可各自看到并下载对应证据，键盘和窄屏均可完成操作。

## 5. 从独立账本拒绝提案

**测什么**　拒绝决定和理由可回查，当前规则和证据保留。

停止服务，在同一 PowerShell 运行：

```powershell
& $openkb init --data-dir $reject --config $config
if ($LASTEXITCODE -ne 0) { throw '拒绝场景初始化失败' }
& $openkb serve --data-dir $reject --port 8765
```

重新导入两份原件并生成清单，再提交同一模拟修订。在拒绝理由填「先按现行内部流程保留原检查项」，用 Tab 聚焦「拒绝提案」，按 Enter。回到「复核历史」重开该记录。

**通过条件**　记录可查看「已拒绝」及原理由。清单只有 v1，仍引用旧规则和旧证据。

## 6. 有历史数据后断开模型连接

**测什么**　连接故障可定位，需要模型的新复核给出明确错误，已有资料和证据仍可读取。

停止服务并回到接受场景的 `$accept` 数据目录。若可控制测试模型服务，可停止该服务并使用原配置。下例将独立配置指向 8766，执行前确认端口空闲：

```powershell
$offline = Join-Path $run 'offline.toml'
@'
[model]
base_url = "http://127.0.0.1:8766/v1"
api_key = "acceptance-fixture"
model_id = "acceptance-offline"
[prices]
version = "v1"
'@ | Set-Content -LiteralPath $offline -Encoding ascii
& $openkb doctor --data-dir $accept --config $offline | Tee-Object (Join-Path $run 'doctor-offline.txt')
if ($LASTEXITCODE -ne 1) { throw '预期模型端点检查失败且 doctor 返回 1' }
& $openkb serve --data-dir $accept --config $offline --port 8765
```

1. 打开「连接状态」。核对端点不可达和失败原因。doctor 的前三项仍应通过。
2. 浏览「资料」、清单 v1 和 v2，打开并下载两版「数据表格」证据。打开上次的复核记录确认接受决定仍可查。
3. 另用两份原件生成一份基线清单。在这份清单上提交模拟修订，核对因端点不可达而报告「模型配置或连接检查失败」，页面提供「查看连接状态」。返回之前接受的清单，确认 v2 及两版证据仍可查。

**通过条件**　doctor 与浏览器报告连接故障，新复核显示明确错误。旧历史、证据和原件下载均正常。用原模型配置重启可回到正常配置。

## 验收记录

记录各项通过、失败或未执行，保存 wheel 文件名和哈希、Python 与 pipx 版本、两次 doctor 输出、资料版本、清单及复核地址、证据位置和下载哈希。保存截图时记录窗口宽度、键盘焦点及确认结果。

2026-11-25 在现有 Windows 主机上使用独立的 pipx 目录和空账本，从源码目录外安装并启动 wheel（Python 3.12，pipx 1.17.12，pip 后端）。在浏览器使用已核对哈希的两份 Section 508 原件和模拟修订，连接配置中的真实模型。两份独立账本分别验证接受和拒绝。接受产生 v2，历史原件与模拟副本可分别下载并逐字节匹配；拒绝保留 v1 和理由。宽屏与窄屏布局、Tab 加 Enter、正常配置下 doctor 四项通过均已验证。切换到无监听端点后，新的复核报告连接失败，两版证据和原件仍可查。

M0 报告比较和 M1 百份入库继续使用各自的 CLI 入口。 [M0 评测手册](./eval-harness.md) · [M1 手动验收](./m1-manual-acceptance.md)

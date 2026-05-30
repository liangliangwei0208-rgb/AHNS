# AHNS

AHNS 是一个个人公开数据建模复盘项目，用于生成每日市场 RSI 图、海外/全球基金模型估算表、盘前/盘中/盘后/富途夜盘实时观察图、安全版公开发布图、海外基金节假日/节后观察图，以及面向小白的说明类科普图。项目通过本地缓存、数据质量覆盖、GitHub Actions 自动回推和 Gitee 小电脑快速通道，尽量减少重复请求和人工维护成本。

> 本项目仅供个人学习记录，不构成任何投资建议；非实时净值，最终以基金公司公告和销售平台展示为准。

## 功能概览

- 生成纳斯达克、红利低波、上证指数 ETF 等市场 RSI 分析图；RSI CSV 优先复用最新完整交易日，国内 ETF 保留实时补点。
- 基于公开披露持仓、指数/ETF 代理和完整日线行情，生成海外/全球基金模型估算观察表；国内基金估算业务线已停用。
- 生成 safe 系列公开展示图：不展示基金代码，基金名称脱敏，并保留模型观察限购信息。
- 提供盘前、盘中、盘后、富途夜盘四个实时观察入口，用 15 分钟短缓存降频，不污染正式估算缓存。
- 自动识别海外基金节假日期间的累计观察场景，并在节后第 1 / 第 2 个 A 股交易日生成补更新观察图。
- 支持按基金代码和估值日期打印完整估算拆解表，区分“股票自身涨跌幅”和“对基金贡献”。
- 海外基准表和盘前补偿仓位均支持配置化；默认尽量使用国内更友好的新浪、东方财富和 AKShare 路径，减少对 Yahoo 的依赖。
- A 股、港股、美股持仓日收益按“涨跌幅列优先、复权/调整后价格其次、裸收盘价最后兜底”计算，降低除权、拆股日误算风险。
- 对缓存文件生成说明：安全容器型 JSON 可内嵌 `_cache_info`，其余 key-map JSON、CSV 和图片缓存统一通过 `cache/README.md` 说明。
- 缓存采用数据质量覆盖：`traded/closed` 可复用，`pending/missing/stale` 只记录诊断信息，不阻止下次重试。
- 支持运行前自检，快速检查 Python 环境、关键缓存、水印图片、邮箱配置、依赖和总入口配置。
- 交互终端使用 Rich 进度条和基金表格输出；非交互环境自动退回纯文本，`AHNS_PROGRESS=0` 可关闭进度层。
- 支持 QQ 邮箱自动发送本次运行生成或更新的图片。
- 支持 GitHub Actions 定时运行、手动触发、缓存自动回推和失败图片 artifact。

## 运行环境与分工

本项目现在按三类运行环境维护，Codex 接手时请先判断自己在哪台机器上：

| 环境 | 仓库目录 | Python | 主要职责 | Git 远程 |
| --- | --- | --- | --- | --- |
| 主机电脑 | `G:\AHNS` | `F:\anaconda\envs\py310\python.exe` | 日常改代码、验证、运行 `git_main.py`、执行 `sync_repos.py` 同步三边仓库 | `origin`=GitHub，`gitee`=Gitee |
| 小电脑服务器 | `C:\Users\Administrator\Desktop\AHNS` | `D:\anaconda\envs\py310\python.exe` | 常驻监听 `service_command.json`，触发 `service_main.py`，支持富途夜盘 | 默认只主动使用 `gitee/main` |
| GitHub Actions | GitHub runner workspace | Actions 自带 Python 3.10 | 定时/手动运行 GitHub 版 `git_main.py`，回推运行缓存 | `origin/main` |

GitHub 仍是长期主仓和 Actions 运行源；Gitee 是小电脑服务器的国内快速指令通道。主机电脑改完代码后运行 `sync_repos.py`，把本地、GitHub、Gitee 三边对齐。

## 目录结构

```text
.
├── git_main.py                  # GitHub/主机总控入口：完整日流程 + 命中窗口追加实时观察
├── service_main.py              # 小电脑总控入口：复用 git_main，总流程额外包含富途夜盘窗口
├── main.py                      # 主计算入口：市场 RSI、正式海外/全球基金估算、正式缓存写入
├── safe_fund.py                 # safe 每日基金图
├── safe_holidays.py             # safe 节假日累计图
├── sum_holidays.py              # 节后补更新观察图
├── premarket_fund.py            # 盘前观察图手动入口，不写正式估算缓存
├── intraday_fund.py             # 盘中观察图手动入口，不写正式估算缓存
├── afterhours_fund.py           # 盘后观察图手动入口，不写正式估算缓存
├── futu_night_fund.py           # 富途夜盘观察图手动入口，不写正式估算缓存
├── check_project.py             # 运行前自检，只检查不修改
├── fund_estimate_breakdown.py    # 基金估算完整拆解查询工具
├── stock_analysis.py            # 市场 RSI 图入口
├── service_command_watcher.py    # 小电脑 Gitee command 监听入口
├── service_runner.py             # 小电脑单次同步、运行、提交、推送流程
├── start_ahns_command_watcher.ps1 # 小电脑 Windows 计划任务启动脚本
├── tail_ahns_log.ps1             # UTF-8 模式查看小电脑监听日志
├── sync_repos.py                # 主机电脑同步本地、GitHub、Gitee 三边仓库
├── github_gitee_sync.py         # 通用 GitHub/Gitee 同名仓库初始化和三边同步脚本
├── service_command.json         # Gitee command 指令文件，run_flag=1 触发小电脑运行
├── kepu/                        # 手动科普图与日期条件科普/限额表脚本
├── tools/                       # 基金估算、缓存、绘图、邮件、行情源等内部模块
├── tools/configs/               # 常维护配置：基金池、代理、基准源、safe 样式、映射、RSI、流程等
├── cache/                       # 运行缓存，会被提交并由 Actions 自动更新；说明见 cache/README.md
└── output/                      # 运行输出图片，不提交
```

顶层入口暂不移动，目的是保持 VSCode、Actions、计划任务和你平时手动运行的命令稳定。未来如果继续整理目录，建议只迁移内部实现，顶层保留同名薄入口做兼容转发。

## 主机电脑本地运行

推荐 Python 版本：3.10。

```powershell
Set-Location G:\AHNS
& F:\anaconda\envs\py310\python.exe -m pip install -r requirements.txt
```

运行前自检，不联网、不出图、不发邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\check_project.py
```

完整预演，不发邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --no-send
```

正式运行并发送邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py
```

临时指定收件人：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --receiver someone@example.com
```

查看某只基金在指定估值日或实时观察中的完整估算拆解：

```powershell
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py
```

运行后按提示输入基金代码和估值日期。也可以直接传参：

```powershell
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184 2026-05-06
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184 --latest
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184 2026-05-06 --save-txt
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 012922 --observation 盘中
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 022184 盘后
```

单独生成实时观察图：

```powershell
& F:\anaconda\envs\py310\python.exe .\premarket_fund.py
& F:\anaconda\envs\py310\python.exe .\intraday_fund.py
& F:\anaconda\envs\py310\python.exe .\afterhours_fund.py
& F:\anaconda\envs\py310\python.exe .\futu_night_fund.py
```

非窗口时间调试或人工预览可加 `--force`。富途夜盘例外：`--force` 只绕过北京时间 11:30-16:30 配置窗口，仍会检查美股夜盘是否真实开市；周末或美国节假日没有夜盘时会直接跳过。

```powershell
& F:\anaconda\envs\py310\python.exe .\premarket_fund.py --force
& F:\anaconda\envs\py310\python.exe .\intraday_fund.py --force
& F:\anaconda\envs\py310\python.exe .\afterhours_fund.py --force
& F:\anaconda\envs\py310\python.exe .\futu_night_fund.py --force
```

## 小电脑服务器与仓库同步

当前小电脑服务器仓库根目录是 `C:\Users\Administrator\Desktop\AHNS`，不再使用 `AHNS\AHNS` 嵌套目录。

运行职责分工：

- 小电脑服务器只负责国内快速通道 `gitee/main`：拉取指令、运行服务、提交运行结果、推送回 Gitee。
- GitHub 仍然是主仓库和长期存档，但小电脑默认不主动访问 GitHub，避免国内网络错误拖慢监听。
- 主机电脑改完代码后，手动运行 `sync_repos.py`，把本地、GitHub、Gitee 三边对齐。

小电脑监听入口：

```powershell
Set-Location "C:\Users\Administrator\Desktop\AHNS"
& D:\anaconda\envs\py310\python.exe .\service_command_watcher.py --interval-seconds 60 --primary-remote gitee
```

计划任务约定：

- `Futu OpenD Autostart`：登录 `Administrator` 后启动 `C:\Users\Administrator\AppData\Roaming\Futu_OpenD\Futu_OpenD.exe`。
- `AHNS Command Watcher`：每日 06:00 启动，登录后也会启动；实际执行 `start_ahns_command_watcher.ps1`，脚本在 06:00 前会直接退出。
- `AHNS Command Watcher Stop`：每日 00:00 停止监听任务，并结束仍在运行的 `service_command_watcher.py`。

监听日志：

```text
C:\Users\Administrator\Desktop\AHNS\logs\service_command_watcher.log
```

日志文件占用的是磁盘，不会一直占用内存。`start_ahns_command_watcher.ps1` 每次启动时会检查日志大小；如果超过 20MB，会保留最近 3000 行并裁剪旧内容，避免长期运行后无限变大。

检查小电脑是否正在监听：

```powershell
schtasks /Query /TN "Futu OpenD Autostart"
schtasks /Query /TN "AHNS Command Watcher"
schtasks /Query /TN "AHNS Command Watcher Stop"

Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "Futu_OpenD|service_command_watcher.py" } |
  Select-Object ProcessId, Name, CommandLine

& "C:\Users\Administrator\Desktop\AHNS\tail_ahns_log.ps1"
```

如果手动在 PowerShell 里直接运行 Python 脚本，先执行下面几行，避免中文日志乱码：

```powershell
chcp 65001
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
```

旧日志里已经写坏的乱码无法自动还原；重启 `AHNS Command Watcher` 后，新写入的日志会按 UTF-8 环境输出。

主机电脑同步本地、GitHub、Gitee：

```powershell
Set-Location G:\AHNS
& F:\anaconda\envs\py310\python.exe .\sync_repos.py
```

`sync_repos.py` 默认同步 `main` 分支，默认远程名是 `origin`（GitHub）和 `gitee`（Gitee）。流程是：检查分支和工作区、拉取两个远程、合并远程提交、推送到两个远程、打印最终提交位置。遇到真实 merge 冲突时会停止，不会自动覆盖历史；需要手动解决冲突、`git add`、`git commit` 后再重新运行。

预演同步命令但不修改仓库：

```powershell
& F:\anaconda\envs\py310\python.exe .\sync_repos.py --dry-run
```

通用 GitHub/Gitee 同名仓库同步：

```powershell
& F:\anaconda\envs\py310\python.exe .\github_gitee_sync.py
```

`github_gitee_sync.py` 可以复制到其他本地 Git 仓库根目录使用。它默认读取 `origin` 的 GitHub 地址，支持 `git@github.com:owner/repo.git` 和 `https://github.com/owner/repo.git`，并推导同名 Gitee 仓库 `git@gitee.com:owner/repo.git`。如果本地仓库还没有 GitHub remote，脚本会询问 GitHub owner / 仓库名 / 是否私有，并用 `GITHUB_TOKEN` 或 `GH_TOKEN` 创建 GitHub 仓库，再添加 `origin` remote。如果 Gitee 仓库不存在，会读取环境变量 `GITEE_ACCESS_TOKEN` 并自动创建公开仓库；如需私有 Gitee 仓库，运行时加 `--private`。令牌只从环境变量读取，不要写进仓库文件。

首次使用前可检查本机 Gitee 环境：

```powershell
& F:\anaconda\envs\py310\python.exe .\github_gitee_sync.py --init-gitee
```

预演通用同步但不创建仓库、不修改 remote、不推送：

```powershell
& F:\anaconda\envs\py310\python.exe .\github_gitee_sync.py --dry-run
```

如果脚本运行在非交互环境，且本地还没有 GitHub remote，需要显式传入 GitHub 仓库信息：

```powershell
& F:\anaconda\envs\py310\python.exe .\github_gitee_sync.py --github-owner liangliangwei0208-rgb --github-repo NewRepo
```

GitHub 仓库默认公开；如需创建私有 GitHub 仓库，使用 `--github-private`。Gitee 仓库默认公开；如需创建私有 Gitee 仓库，使用 `--private`。

如果已有 `gitee` remote 但地址和推导目标不一致，先确认目标无误，再使用：

```powershell
& F:\anaconda\envs\py310\python.exe .\github_gitee_sync.py --fix-remote
```

## 常用维护入口

- `service_command_watcher.py`：小电脑服务器长期监听入口，默认监听 `gitee/main` 的 `service_command.json`，不再默认兜底访问 GitHub。
- `service_runner.py`：小电脑服务器一次性服务流程，负责 pull、运行 `service_main.py`、提交安全范围内的变化、push。
- `start_ahns_command_watcher.ps1`：计划任务调用的启动脚本，设置 UTF-8 输出、仓库目录、Python 路径、日志路径、日志裁剪和 `--primary-remote gitee`。
- `tail_ahns_log.ps1`：查看监听日志的 UTF-8 PowerShell 脚本，优先用它替代手写 `Get-Content -Wait`。
- `sync_repos.py`：主机电脑三边同步脚本，用于把本地、GitHub、Gitee 对齐。
- `github_gitee_sync.py`：通用同名仓库同步脚本，可复制到其他仓库使用；会从 GitHub remote 推导 Gitee remote；若缺少 GitHub remote，会询问仓库信息并用 `GITHUB_TOKEN` / `GH_TOKEN` 创建 GitHub 仓库；必要时再用 `GITEE_ACCESS_TOKEN` 自动创建公开 Gitee 仓库。
- `tools/configs/workflow_configs.py`：维护 GitHub 流程和 Service 流程。无时间窗步骤属于完整日流程，始终运行；带 `run_window_bj` 的实时观察步骤只在命中北京时间窗口时追加运行，不会替代完整流程。GitHub 流程不含富途夜盘，Service 流程额外包含富途夜盘。
- `tools/configs/fund_universe_configs.py`：维护海外/全球基金池；新增基金代码优先改这里，基金代码请写 6 位字符串。
- `tools/configs/fund_proxy_configs.py`：维护代理型基金和海外有效披露持仓增强系数。
- `tools/configs/residual_benchmark_configs.py`：维护海外股票持仓型基金的补偿仓位基准；默认纳斯达克100，`007844` 当前使用 `XOP`。
- `tools/configs/market_benchmark_configs.py`：维护 safe 海外基金图底部基准表。这里决定展示哪些指数、ETF 或海外资产，以及使用新浪、AKShare、东方财富还是 Yahoo 路径。
- `tools/configs/premarket_configs.py`：维护盘前观察图配置。这里定义盘前观察项、实时 ticker、默认补偿仓位基准，以及按基金代码指定的盘前补偿基准；例如 `007844`、`006679`、`018852` 当前使用 `oil_gas_ep`。
- `tools/configs/intraday_configs.py`：维护盘中观察图配置和时间窗。
- `tools/configs/afterhours_configs.py`：维护盘后观察图配置和时间窗。
- `tools/configs/futu_night_configs.py`：维护富途夜盘观察图配置、Futu OpenD 连接参数、短缓存和报价校验阈值。
- `tools/configs/safe_image_style_configs.py`：维护 safe 公开图样式。标题文字、标题和表格间距、表头底色、正文底色、表格线、行高、列宽、涨跌颜色、底部备注、水印文字和透明度都优先在这里改。
- `tools/configs/cache_policy_configs.py`：维护缓存有效期。限购 7 天、A 股交易日历 7 天、证券/指数/基金历史保留天数、RSI ETF 实时补点新鲜度等都集中在这里。
- `tools/configs/security_mappings.py`：维护美股 / 韩国证券映射。
- `tools/configs/rsi_configs.py`：维护 RSI 图标的列表。
- `tools/cache_metadata.py`：维护缓存文件说明，并生成 `cache/README.md`。只有安全容器型 JSON 会内嵌 `_cache_info`，key-map JSON 和 CSV 不改变结构。
- `tools/paths.py`：集中维护常用缓存和输出图片路径。

旧导入路径会尽量保留兼容，例如 `tools/fund_universe.py` 仍可导入基金池，但真实配置已移动到 `tools/configs/fund_universe_configs.py`。

## 实时观察图

`premarket_fund.py`、`intraday_fund.py`、`afterhours_fund.py`、`futu_night_fund.py` 都是独立观察入口；它们会读持仓、限购和 15 分钟实时短缓存，但不会写 `cache/fund_estimate_return_cache.json`，也不会覆盖正式每日图 `output/safe_haiwai_fund.png`。

| 入口 | 默认北京时间窗口 | 输出图片 | 排查报告 | 日期口径 |
| --- | --- | --- | --- | --- |
| `afterhours_fund.py` | 08:00-12:00 | `output/safe_haiwai_afterhours.png` | `output/afterhours_failed_holdings_latest.txt` | 主标题使用下一美股估值日，报告保留 `afterhours_quote_date` |
| `futu_night_fund.py` | 11:30-16:30 | `output/safe_haiwai_night.png` | `output/night_failed_holdings_latest.txt` | 使用富途夜盘目标估值日 |
| `premarket_fund.py` | 17:30-21:00 | `output/safe_haiwai_premarket.png` | `output/premarket_failed_holdings_latest.txt` | 使用目标美股交易日 |
| `intraday_fund.py` | 22:40-次日 02:00 | `output/safe_haiwai_intraday.png` | `output/intraday_failed_holdings_latest.txt` | 使用目标美股交易日 |

实时观察估算与正式估算口径相近，但数据边界不同：

- 正式主流程只使用完整日线，并且只有正式主流程写 `fund_estimate_return_cache.json`。
- 盘前美股只接受目标美股交易日的 `pre` 时段报价；`--force` 调试时也不会把 regular/post/closed 数据当作盘前。
- 盘中美股只接受目标美股交易日 regular 报价；盘后图主标题使用下一估值日，但报告保留实际盘后报价日。
- 富途夜盘只保留 Futu OpenAPI 实现；需要本机安装可选依赖 `futu-api` 并启动 Futu OpenD，连接参数在 `tools/configs/futu_night_configs.py`。入口会先判断目标美股夜盘是否处于开市窗口，周末或美国节假日没有夜盘时不加载持仓、不连接 Futu。
- 实时短缓存分别是 `premarket_quote_cache.json`、`intraday_quote_cache.json`、`afterhours_quote_cache.json`、`futu_night_return_cache.json`，TTL 保持 15 分钟；失败结果不跨运行缓存。

## 海外基准表配置

海外基金 safe 图底部的基准表由 `tools/configs/market_benchmark_configs.py` 的 `MARKET_BENCHMARK_ITEMS` 控制。每一项都是一个字典，常用字段如下：

- `enabled`：是否启用。改成 `False` 后不会删除历史缓存，但新图会过滤该基准，也不会主动更新它。
- `label`：图片上显示的名称。
- `kind`：行情读取类型。
  - `us_index`：新浪美股指数，例如 `.NDX`、`.INX`、`.SOX`。
  - `us_security`：美股股票或 ETF，例如 `XOP`。
  - `foreign_futures`：新浪外盘期货 / 东方财富国际期货，例如 `XAU`、`GC00Y`。
  - `yahoo`：Yahoo Chart，例如 `^VIX`。
  - `vix_level`：VIX 恐慌指数点位，优先 CBOE 官方 CSV，失败后回退 FRED。
- `ticker`：主行情代码。
- `fallback_ticker`：备用行情代码；主源失败后才会尝试。
- `display_in_daily_fund`：是否显示在每日海外基金 safe 图底部。
- `display_in_holidays`：是否显示在节假日 / 节后观察图。
- `include_in_cumulative`：是否作为收益率参与区间复利累计；VIX 这类点位指标必须为 `False`。

当前默认配置：

| 名称 | kind | ticker | 默认数据源说明 |
| --- | --- | --- | --- |
| 纳斯达克100 | `us_index` | `.NDX` | 新浪美股指数 |
| 标普500 | `us_index` | `.INX` | 新浪美股指数 |
| 油气开采指数 | `us_security` | `XOP` | AKShare 美股 ETF 日线；XOP 是 ETF 代理，不是指数本体 |
| 费城半导体 | `us_index` | `.SOX` | 新浪美股指数 |
| 现货黄金 | `foreign_futures` | `XAU`，fallback `GC00Y` | 优先新浪外盘期货 XAU；失败后用东方财富国际期货 GC00Y |
| VIX恐慌指数 | `vix_level` | `VIX` | CBOE 官方历史 CSV 优先，FRED 兜底；每日图显示点位，不带 `%` |

注意：配置不会把所有失败基准自动兜到 Yahoo。只有 `kind="yahoo"` 的项目，或者代码里明确写了 Yahoo fallback 的证券路径，才会访问 Yahoo。VIX 当前不走 Yahoo，而是 CBOE/FRED CSV；它展示的是最新完整有效交易日收盘点位，不是涨跌幅。

基准记录会写入 `cache/fund_estimate_return_cache.json` 的 `benchmark_records`。收益率型基准写 `return_pct`；VIX 点位型指标写 `value_type="level"`、`value/display_value`，并保持 `return_pct=null`。如果某个基准失败，只影响该基准行，不会中断主流程，也不会影响基金主表生成。

## Safe 图样式配置

safe 公开图的样式集中在 `tools/configs/safe_image_style_configs.py`。这个文件只管“怎么画图”，不拉行情、不读缓存、不出图，适合后续日常微调。

常用配置项：

- `SAFE_TITLE_STYLE`：标题字号、颜色、粗细、标题和表格的间距。`cumulative_gap` 控制 `safe_holidays.png` / `safe_sum_holidays.png` 的标题到表格距离，数值越小越贴近。
- `SAFE_CANVAS_STYLE`：每日图导出外边距。最上方留白偏大时调 `daily_top_pad_inches`；底部留白偏大时调 `daily_bottom_pad_inches`；左右留白调 `daily_left_pad_inches` / `daily_right_pad_inches`。
- `SAFE_DAILY_TABLE_STYLE`：`safe_haiwai_fund.png` 和节后第 1 天单日观察图的表格样式，包括正文/表头字号、表头底色、表头文字色、正文底色、整图底色、网格线、行高、缩放。
- `SAFE_CUMULATIVE_TABLE_STYLE`：节假日累计图和节后第 2 天累计图的表格样式。
- `SAFE_RETURN_COLORS`：涨跌颜色。当前按国内习惯红涨绿跌，无法获取或无效数据为黑色。
- `SAFE_FOOTER_STYLE`：底部“个人模型……”合规提示和备注文字的颜色、字号、粗细。
- `SAFE_DAILY_COLUMN_WIDTHS`、`SAFE_CUMULATIVE_COLUMN_WIDTHS`、`SAFE_BENCHMARK_COLUMN_WIDTHS`：列宽配置。“列间距”主要靠这里调；每次建议小幅调整 `0.01` 到 `0.03`。
- `SAFE_WATERMARK_STYLE`：居中 `cache/mark.jpg` logo 水印和斜向“鱼师AHNS”文字水印。可改水印文字、字号、颜色、透明度、旋转角度、logo 透明度和大小比例。

修改后可用下面命令单独预览：

```powershell
& F:\anaconda\envs\py310\python.exe .\safe_fund.py
& F:\anaconda\envs\py310\python.exe .\safe_holidays.py
& F:\anaconda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-07
```

如果只是想让标题和表格更近，优先改 `SAFE_TITLE_STYLE["cumulative_gap"]` 或每日图的 `daily_gap_ratio/daily_gap_min/daily_gap_max`。如果是图片边缘留白：顶部改 `SAFE_CANVAS_STYLE["daily_top_pad_inches"]`，底部改 `SAFE_CANVAS_STYLE["daily_bottom_pad_inches"]`。如果文字挤在一起，先调列宽，再考虑降低字号。

## 邮件配置

项目使用 QQ 邮箱 SMTP 发送图片邮件。公开仓库不保存真实授权码。

配置优先级：

1. 函数参数；
2. 环境变量；
3. 本地未跟踪文件 `tools/email_local_config.py`。

环境变量：

- `QQ_EMAIL_ACCOUNT`：发件 QQ 邮箱，必填。
- `QQ_EMAIL_AUTH_CODE`：QQ 邮箱 SMTP 授权码，必填。
- `QQ_EMAIL_RECEIVER`：收件邮箱，可选；缺失时默认发送给 `QQ_EMAIL_ACCOUNT`。

本地配置方式：

```powershell
Copy-Item .\tools\email_local_config.example.py .\tools\email_local_config.py
```

然后在 `tools/email_local_config.py` 中填入自己的邮箱和授权码。该文件已被 `.gitignore` 忽略，不应提交。

## GitHub Actions

workflow 文件：`.github/workflows/ahns-daily.yml`。

触发方式：

- 手动触发：`workflow_dispatch`
- 定时触发：
  - UTC `10 21 * * *`，北京时间次日 05:10

运行环境：

- `ubuntu-24.04`
- Python 3.10
- 安装 `requirements.txt`
- 安装中文字体包，保证图片中的中文正常显示

需要在 GitHub 仓库 Settings -> Secrets and variables -> Actions 中配置：

- `QQ_EMAIL_ACCOUNT`
- `QQ_EMAIL_AUTH_CODE`
- `QQ_EMAIL_RECEIVER` 可选

Actions 运行 `python git_main.py`，使用 GitHub 流程：完整日流程会照常运行，命中盘前/盘中/盘后窗口时追加对应实时观察；不运行富途夜盘，也不自动生成 `first_pic.py`。

Actions 运行后如 `cache/` 或 `investment_quote_history.json` 发生变化，会自动提交回仓库，提交信息为：

```text
Update runtime cache [skip ci]
```

成功运行不会上传图片 artifact；失败时才上传 `output/*.png` 作为 debug artifact，保留 3 天，避免 Actions 存储持续膨胀。

因为 Actions 会回推缓存，本地运行前建议先同步远端，尤其是 `cache/security_return_cache.json` 和 `cache/fund_estimate_return_cache.json`。如果本地和远端同时修改缓存后发生合并冲突或 JSON 破损，应先修复 JSON 结构，再继续运行会写缓存的脚本。

## 输出图片

常见输出：

- `output/first_pic.png`（手动运行 `kepu/first_pic.py` 生成；总入口不再自动生成）
- `output/nasdaq_analysis.png`
- `output/nasdaq.png`
- `output/honglidibo_analysis.png`
- `output/honglidibo.png`
- `output/shangzheng_analysis.png`
- `output/shangzheng.png`
- `output/haiwai_fund.png`（详细版当前在主流程中暂不输出，旧文件可能仍存在）
- `output/safe_haiwai_afterhours.png`（盘后观察图，手动运行 `afterhours_fund.py` 生成）
- `output/safe_haiwai_night.png`（富途夜盘观察图，手动运行 `futu_night_fund.py` 生成）
- `output/safe_haiwai_premarket.png`（盘前观察图，手动运行 `premarket_fund.py` 生成）
- `output/safe_haiwai_intraday.png`（盘中观察图，手动运行 `intraday_fund.py` 生成）
- `output/safe_haiwai_fund.png`
- `output/safe_holidays.png`
- `output/haiwai_holidays.png`
- `output/sum_holidays.png`（详细版已停用，后续不再新生成/覆盖）
- `output/safe_sum_holidays.png`
- `output/kepu_sum_holidays.png`
- `output/kepu_xiane.png`（限额科普图，保留手动入口；总入口只生成周日限额表）
- `output/xiane.png`（海外基金限额表，仅北京时间周日生成）

排查报告：

- `output/failed_holdings_latest.txt`：正式海外基金估算的持仓失败和请求统计报告。
- `output/premarket_failed_holdings_latest.txt`：盘前观察图的实时持仓、补偿基准和失败源报告。
- `output/intraday_failed_holdings_latest.txt`：盘中观察图的实时持仓、补偿基准和失败源报告。
- `output/afterhours_failed_holdings_latest.txt`：盘后观察图的实时持仓、补偿基准和失败源报告。
- `output/night_failed_holdings_latest.txt`：富途夜盘观察图的实时持仓、补偿基准和失败源报告。

Matplotlib 表格和 RSI 图默认使用 180 DPI，科普图使用 Pillow 固定像素并做 PNG 无损压缩。

旧的 `output/guonei*.png` 文件可能仍在本地目录中，但后续主流程不再生成或加入邮件。

## 计算与缓存说明

`cache/` 会提交到仓库，用于减少重复拉取行情和保留基金估算历史。

- 海外/全球基金使用统一 `valuation_anchor_date` 估值锚点；US/CN/HK/KR 都只能使用该锚点对应的完整日线。
- 每个市场先用交易日历判断开闭市，再校验行情接口返回的 `trade_date == valuation_anchor_date`。
- 市场交易日历在单次运行中会按 `(market, start_date, end_date)` 做内存缓存；同一估值日、同一市场不重复计算开闭市和收盘完成状态。
- A 股节假日判断优先读取 `cache/a_share_trade_calendar_cache.json`，缓存 7 天有效；过期才主动请求 AkShare，AkShare 失败时优先使用旧文件缓存，再退到本地行情 CSV 兜底。
- CN/HK 日线按“可信涨跌幅源优先早停、复权价其次、裸 close 最后兜底”执行，命中目标估值日后立即返回，不再无条件请求全部源。
- 个股收益已统一做除权/拆股防错：
  - A 股优先使用官方涨跌幅列；没有涨跌幅列时优先使用新浪 `qfq/hfq` 复权价；最后才用未复权 raw close。
  - 港股会同时尝试新浪 raw/qfq/hfq 和东方财富港股日线；优先涨跌幅列，其次 qfq/hfq，最后 raw close。
  - 美股保留新浪日线、东方财富和 Yahoo fallback 顺序；东方财富路径优先解析 kline 里的日涨跌幅字段，Yahoo fallback 优先用 `adjclose`，raw close 仅作兜底。
  - 如果 Yahoo fallback 也失败，只打印完整错误链，并把对应证券标记为 missing/stale；不会中断后续基金或整套流程。
  - 如果只剩 raw close 且单日绝对涨跌异常大，代码会继续尝试其他源；仍无法确认时宁愿标记为 missing/stale，也不写入明显可疑的大涨大跌。
  - 韩国当前 pykrx 已优先读取“涨跌率”列；指数、期货和黄金没有股票除权/拆股语义，仍用完整日线 close-to-close。
- RSI 行情优先使用本地 `cache/*_index_daily.csv`。如果缓存已经覆盖最新完整交易日，非实时指数会直接复用；国内 ETF 因为需要盘中观察，在 `include_realtime=True` 且历史缓存足够新时只补实时点，不重拉整段历史。
- 普通持仓型海外基金使用“有效持仓增强 + 配置基准补偿仓位”口径，`fund_estimate_breakdown.py` 可打印正式完整日线和盘前/盘中/盘后/富途夜盘观察的逐项明细。
- 默认补偿基准为纳斯达克100；单基金可在 `tools/configs/residual_benchmark_configs.py` 指定其他基准。`007844` 当前使用 `XOP` 作为美国油气开采方向代理，`XOP` 是 ETF 不是指数本身。
- `security_return_cache.json` 对锚点行情使用 `SECURITY:{market}:{ticker}:{valuation_anchor_date}` key，缓存 `traded/closed/pending/missing/stale` 状态。`traded` 和 `closed` 表示已经拿到有效信息，可以复用；`pending/missing/stale` 只用于诊断和失败报告，不再作为 fresh 命中，下一次运行会重新请求接口。
- 旧 A 股裸收盘价来源 `ak_stock_zh_a_daily_sina_close_calc`、旧港股裸收盘价来源 `ak_stock_hk_daily_sina_close_calc` 不再视为新鲜缓存，命中后会自动刷新到更可靠口径。旧缓存文件不会被删除。
- `fund_estimate_return_cache.json` 只由完整日线主流程写海外/全球基金记录，key 为 `overseas:{fund_code}:{valuation_anchor_date}`。实时观察图不写本缓存。写入时按数据质量覆盖：完整记录优先，失败、未确认或陈旧记录不会覆盖更好的旧记录。
- 基准表记录写在 `fund_estimate_return_cache.json` 的 `benchmark_records`；显示端会按 `market_benchmark_configs.py` 的 `enabled=True`、`display_in_holidays`、`include_in_cumulative` 过滤。VIX 只在每日海外基金图展示点位，不进入节假日累计图和区间复利。
- `a_share_trade_calendar_cache.json` 保存 A 股交易日历，字段包含 `fetched_at`、`source`、`trade_dates`。默认 7 天有效；这是节假日判断和节后补更新判断的重要降频缓存。
- `fund_estimate_return_cache.json` 和 `a_share_trade_calendar_cache.json` 会内嵌 `_cache_info` 说明；`security_return_cache.json`、持仓缓存、限购缓存和 CSV 不内嵌说明，统一由 `cache/README.md` 描述，避免破坏读取逻辑。
- `*_index_daily.csv` 是 RSI/指数行情 CSV 缓存。主流程会优先读缓存，只有缓存不满足当前运行需求时才联网刷新。
- 实时观察短缓存 TTL 保持 15 分钟：盘前、盘中、盘后分别使用独立 quote cache，富途夜盘使用 `futu_night_return_cache.json`；旧 `night_quote_cache.json` 是 legacy 缓存，不再由当前代码写入。
- `output/failed_holdings_latest.txt` 每轮海外基金估算后覆盖写入，包含运行汇总、行情请求统计、唯一证券汇总和失败/未完成持仓明细。它是本地排查文件，不进入邮件正文。
- 行情请求统计只保存在当前 Python 进程内，不写 JSON；用于控制台摘要和 `failed_holdings_latest.txt`。
- 指数行情和基金估算历史保留 300 天；证券日缓存、小时桶缓存、限购缓存和 A 股交易日历按各自策略裁剪或覆盖写入，不会无限制追加。
- Actions 运行后会自动回推缓存变化。

## 估算拆解与排错

如果某只基金的估算结果看起来异常，先用拆解工具看缓存中的逐项贡献：

```powershell
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py
```

交互模式会询问基金代码和查询类型；查询类型可输入 `正式`、`盘前`、`盘中`、`盘中实时`、`盘后`、`夜盘`。实时观察拆解只读对应短缓存和最新报告，不联网、不出图、不写正式基金缓存。

建议重点看这些字段：

- `行情交易日`：是否等于本次估值锚点。
- `状态`：`traded` 表示已使用完整日线，`pending` 表示市场收盘未确认或行情尚未更新，`missing/stale` 表示缺失或陈旧。
- `股票自身涨跌幅`：单只持仓自己的涨跌。
- `估算权重`：经过有效持仓增强后的模型权重。
- `对基金贡献`：这只持仓对基金估算收益率的贡献。
- `数据源`：用于判断口径。正常情况下，除权/拆股敏感的股票应优先看到 `pct`、`qfq`、`hfq`、`adjclose` 等来源，而不是裸 close 计算来源。

常见情况：

- Yahoo、新浪、东方财富出现 `SSLEOFError`、`Max retries exceeded` 等网络错误时，通常是接口或链路临时不稳定。代码会记录失败链路并继续处理其他证券；若状态是 `pending/missing/stale`，下次运行不会被这条诊断缓存挡住，会继续重试。
- 单只证券失败，例如某只美股日线源全部失败，只影响持有该证券的基金有效持仓覆盖率；先看 `output/failed_holdings_latest.txt` 的“异常证券”和“失败/未完成持仓明细”。
- Actions 自动回推缓存后，如果本地运行提示 JSON 解析失败，先同步远端完整缓存或按报错行号修复破损 JSON。不要给 `security_return_cache.json` 这类 key-map JSON 手工插入注释字段。
- 国内 ETF 需要实时结果，RSI 缓存优化不会取消 `include_realtime=True` 的实时补点。
- 刚收盘或海外市场尚未完整收盘时，美股可能是 `pending`，贡献暂时为 0，后续重新运行会刷新。
- 如果 `fund_estimate_breakdown.py` 已显示某个持仓修复为正确涨跌幅，但基金合计仍是旧值，需要先运行 `main.py` 或 `git_main.py --no-send` 重新写入基金缓存。
- VIX 在每日图中显示的是点位，不是涨跌幅；如果节假日累计图里出现 VIX，先确认 `include_in_cumulative=False`、`display_in_holidays=False`，并重新运行对应出图脚本。

## 后续优化方向

当前比较值得继续优化的地方：

- 增加一个只读数据源健康检查脚本：集中探测新浪、东方财富、AkShare、CBOE/FRED、Yahoo fallback 是否可用，不写基金缓存，便于 Actions 或本地运行前快速判断网络状态。
- 补强美股特殊代码和基金持仓映射：石油、能源、ADR、改名或退市证券更容易出现行情源滞后，后续可把常见问题 ticker 写入映射或替代代理配置。
- 给 safe 图增加自动视觉回归检查：对输出图片做基础尺寸、非空、水印存在、表格行数和 VIX/累计过滤检查，避免样式配置改动后才在发布时发现异常。
- 第二轮目录整理可考虑把服务端同步/监听实现移动到内部包，例如 `tools/service/`，但必须保留顶层 `service_command_watcher.py`、`service_runner.py`、`sync_repos.py` 作为兼容入口。

## 验证命令

全项目编译：

```powershell
$files = @('.\git_main.py','.\service_main.py','.\service_runner.py','.\service_command_watcher.py','.\sync_repos.py','.\github_gitee_sync.py','.\check_project.py','.\main.py','.\premarket_fund.py','.\intraday_fund.py','.\afterhours_fund.py','.\futu_night_fund.py','.\fund_estimate_breakdown.py','.\safe_fund.py','.\safe_holidays.py','.\holidays.py','.\sum_holidays.py','.\stock_analysis.py','.\kepu\first_pic.py','.\kepu\kepu_sum_holidays.py','.\kepu\kepu_xiane.py') + (Get-ChildItem .\tools -File -Filter *.py | ForEach-Object { $_.FullName }) + (Get-ChildItem .\tools\configs -File -Filter *.py | ForEach-Object { $_.FullName }); & F:\anaconda\envs\py310\python.exe -m py_compile @files
```

主机电脑服务端/同步脚本编译检查：

```powershell
& F:\anaconda\envs\py310\python.exe -m py_compile .\service_main.py .\service_runner.py .\service_command_watcher.py .\sync_repos.py .\github_gitee_sync.py
```

同步脚本预演：

```powershell
& F:\anaconda\envs\py310\python.exe .\sync_repos.py --dry-run
```

总入口预演：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --no-send
```

单独检查 safe 系列图片：

```powershell
& F:\anaconda\envs\py310\python.exe .\safe_fund.py
& F:\anaconda\envs\py310\python.exe .\safe_holidays.py
& F:\anaconda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-07
```

单独生成实时观察图：

```powershell
& F:\anaconda\envs\py310\python.exe .\afterhours_fund.py --force
& F:\anaconda\envs\py310\python.exe .\futu_night_fund.py --force
& F:\anaconda\envs\py310\python.exe .\premarket_fund.py --force
& F:\anaconda\envs\py310\python.exe .\intraday_fund.py --force
```

检查最新失败持仓和唯一证券汇总：

```powershell
Get-Content .\output\failed_holdings_latest.txt -Encoding UTF8 -TotalCount 120
```

检查估算拆解：

```powershell
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 017731 --latest
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 012922 --observation 盘中
```

抽样检查行情口径和缓存降频：

```powershell
@'
from tools.fund_history_io import load_a_share_trade_dates
from tools.get_top10_holdings import fetch_cn_security_return_pct_daily_with_date, fetch_hk_return_pct_akshare_daily_with_date
from tools.rsi_data import get_index_akshare

trade_dates, source = load_a_share_trade_dates(use_akshare=True)
print("A股交易日历", len(trade_dates), source, "2026-05-08" in trade_dates)
print("寒武纪 688256", fetch_cn_security_return_pct_daily_with_date("688256", end_date="2026-05-08"))
print("腾讯控股 00700", fetch_hk_return_pct_akshare_daily_with_date("00700", end_date="2026-05-08"))
df = get_index_akshare(symbol="512890", days=30, cache_dir="cache", use_cache=True, include_realtime=True)
print("RSI缓存样本", df.tail(1).to_string(index=False))
'@ | & F:\anaconda\envs\py310\python.exe -
```

## 免责声明

本项目所有内容均为个人公开数据建模复盘和学习记录。模型估算不等于基金净值公告，不构成任何投资建议、收益承诺或交易依据。基金净值、申购规则、限额信息和公告日期均以基金公司公告及销售平台展示为准。

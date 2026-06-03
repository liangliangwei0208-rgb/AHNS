# AHNS 项目接手说明

更新时间：2026-05-30

本项目用于生成每日市场分析图、海外/全球基金模型估算表、盘前/盘中/盘后/富途夜盘观察图、安全版公开发布图、海外基金节假日累计观察图、节后补更新观察图，以及面向小白的科普说明图。国内基金收益预估业务线已停用，但 A 股/港股/韩国行情能力仍保留用于海外/全球基金持仓估算。正式主流程只使用完整日线；四个实时观察入口均不写正式基金估算缓存。

主机电脑默认运行环境为：

```powershell
& F:\anaconda\envs\py310\python.exe <script.py>
```

三类运行环境请先区分清楚：

- 主机电脑：仓库根目录 `G:\AHNS`，Python `F:\anaconda\envs\py310\python.exe`，用于写代码、验证、运行 `git_main.py`、`sync_repos.py` 和通用 `github_gitee_sync.py`。
- 小电脑服务器：仓库根目录 `C:\Users\Administrator\Desktop\AHNS`，Python `D:\anaconda\envs\py310\python.exe`，用于监听 Gitee command、运行 `service_main.py`，并支持富途夜盘。
- GitHub Actions：Ubuntu runner，Python 3.10，运行 GitHub 版 `git_main.py`，不运行富途夜盘，不自动生成 `first_pic.py`。

## 操作约束

- 禁止批量删除文件或目录。
- 不要使用 `del /s`、`rd /s`、`rmdir /s`、`Remove-Item -Recurse`、`rm -rf`。
- 需要删除文件时，只能一次删除一个明确路径的文件，例如：
  ```powershell
  Remove-Item "C:\path\to\file.txt"
  ```
- 如果需要批量删除文件，应停止操作，并请用户手动删除。
- 尽量只改用户点名的文件；不要改 `main.py`，除非用户明确要求。
- 不要把 `cache/`、`output/`、`__pycache__/` 视为源码依据；它们是运行产物或缓存。
- 不要把真实 QQ 邮箱 SMTP 授权码写入可提交源码；本地使用 `tools/email_local_config.py` 或环境变量。
- 文档和配置文件保持 UTF-8；修改缓存结构前先确认读取方是否按 key-map 遍历，避免把说明字段误当业务数据。

## 小电脑服务器约定

- 小电脑服务器默认只主动使用 `gitee/main`，把 Gitee 当国内快速指令通道；不要在没有用户明确要求时恢复 GitHub fallback。
- GitHub 仍是主仓库和长期存档；主机电脑改完代码后运行 `sync_repos.py`，负责把本地、GitHub、Gitee 三边同步；其他仓库可复制 `github_gitee_sync.py` 做同名 GitHub/Gitee 仓库初始化和三边同步。
- 监听入口是 `service_command_watcher.py --interval-seconds 60 --primary-remote gitee`，计划任务实际调用 `start_ahns_command_watcher.ps1`。
- `service_runner.py` 默认 `DEFAULT_PRIMARY_REMOTE=gitee`，`DEFAULT_FALLBACK_REMOTE=None`；如需临时兜底 GitHub，必须显式传 `--fallback-remote origin` 或设置环境变量。
- 手机端触发小电脑运行优先用 GitHub Actions workflow `Trigger Service Command`。它只把 Gitee `service_command.json` 写成 `run_flag=1` 并推回 Gitee；小电脑仍只监听 `gitee/main`，下一轮轮询后运行服务流程。该 workflow 会打印触发摘要、Gitee fetch、指令提交和每次送达尝试，并按多种 Git/HTTP 参数重试 Gitee 推送；Git push 全部失败时，会用 Gitee API 兜底更新指令文件。
- `Trigger Service Command` 需要 GitHub Actions Secret `GITEE_PRIVATE_CODE`。该值必须放在 Secrets，不要放普通 Variables，不要写入 README、AGENTS、workflow 明文或本地 Git config。
- 小电脑监听运行时间是北京时间 06:00-24:00。`AHNS Command Watcher` 每日 06:00 和登录后启动；`start_ahns_command_watcher.ps1` 在 06:00 前直接退出；`AHNS Command Watcher Stop` 每日 00:00 停止监听。
- `Futu OpenD Autostart` 登录后启动 `C:\Users\Administrator\AppData\Roaming\Futu_OpenD\Futu_OpenD.exe`。富途夜盘依赖 Futu OpenD 已登录并在线；自动启动只能打开程序，不能替代扫码或登录。
- 小电脑监听日志在 `C:\Users\Administrator\Desktop\AHNS\logs\service_command_watcher.log`。日志占用磁盘，不会一直占用内存；`start_ahns_command_watcher.ps1` 启动时会在日志超过 20MB 后只保留最近 3000 行。
- 查看监听日志优先运行 `C:\Users\Administrator\Desktop\AHNS\tail_ahns_log.ps1`，它会先切换 UTF-8，避免 PowerShell 中文乱码。
- 不要把 Gitee 私人令牌、GitHub token、邮箱授权码写进 README、AGENTS、脚本或提交历史；本地凭据走 Git Credential Manager、环境变量或本机私有配置。

常用检查命令：

```powershell
schtasks /Query /TN "Futu OpenD Autostart"
schtasks /Query /TN "AHNS Command Watcher"
schtasks /Query /TN "AHNS Command Watcher Stop"

Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "Futu_OpenD|service_command_watcher.py" } |
  Select-Object ProcessId, Name, CommandLine

& "C:\Users\Administrator\Desktop\AHNS\tail_ahns_log.ps1"
```

## 当前工作流

推荐总入口：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py
```

运行前自检：

```powershell
& F:\anaconda\envs\py310\python.exe .\check_project.py
```

预演不发邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --no-send
```

`git_main.py` 的运行顺序由 `tools/configs/workflow_configs.py` 维护。想调整每日运行脚本、脚本顺序、必要性标记、某一步生成的图片是否进入邮件候选，优先改这个配置文件，不要直接改总入口主逻辑。无时间窗步骤属于完整日流程，始终运行；命中实时观察窗口时，只是在完整日流程后追加对应实时观察脚本，不会替代完整流程。子脚本失败不会中断总流程，会在运行结束后统一打印失败日志；失败日志会写入邮件正文，失败步骤已生成/更新的图片也会按 `collect_images` 纳入邮件。

GitHub / 主机 `git_main.py` 当前完整日流程：

1. `main.py`
2. `safe_fund.py`
3. `safe_holidays.py`
4. `sum_holidays.py`
5. `kepu/kepu_sum_holidays.py`
6. `kepu/kepu_xiane.py --table-only`

自动总入口不再运行 `kepu/first_pic.py`，也不自动生成 `output/kepu_xiane.png` 限额科普图；周日限购表格图 `output/xiane.png` 仍由 `kepu/kepu_xiane.py --table-only` 生成。

实时观察窗口：

- 08:00-11:29：`afterhours_fund.py --force`
- 17:30-21:00：`premarket_fund.py --force`
- 22:40-次日 02:00：`intraday_fund.py --force`

GitHub / 主机 `git_main.py` 不包含富途夜盘；小电脑 `service_main.py` 使用 Service 流程，额外在 11:30-16:30 追加 `futu_night_fund.py --force`。

`git_main.py` 会扫描 `output/` 中本次新生成或更新的图片，并通过 `tools/email_send.py` 发送邮件。邮件发送保留“正文内嵌图片 + 附件图片”的方式；发送前会打印图片数量、单张大小和总大小。

基金估算相关脚本在交互终端中使用 Rich 进度条和表格输出；非交互环境会自动退回纯文本。需要排障并恢复传统逐行缓存日志时，可设置 `AHNS_PROGRESS=0`。

`check_project.py` 是只读体检工具：检查 Python 环境、关键目录、`cache/mark.jpg`、核心缓存、邮箱配置、依赖导入、Git 状态和总入口配置。它不联网、不拉行情、不出图、不写缓存、不发邮件、不删除文件、不提交 Git。

`premarket_fund.py`、`intraday_fund.py`、`afterhours_fund.py`、`futu_night_fund.py` 是独立实时观察入口；在总入口命中对应窗口时追加运行，也可手动用 `--force` 调试。它们不写 `cache/fund_estimate_return_cache.json`。

## 关键文件

- `git_main.py`：项目总控入口，顺序运行全部脚本，收集本次图片并发送邮件；子脚本失败会继续运行后续步骤，并在最后汇总错误输出，同步写入邮件正文；支持 `--no-send` 和 `--receiver`。
- `check_project.py`：运行前自检入口，只检查不修改，用于确认环境、缓存、依赖、邮箱配置和流程配置是否基本正常。
- `service_command_watcher.py`：小电脑服务器长期监听入口，默认监听 `gitee/main` 的 `service_command.json`，根据 `run_flag` 触发服务流程。
- `service_runner.py`：小电脑服务器单次运行流程，负责 pull、运行 `service_main.py`、提交允许范围内的变化、push。
- `service_main.py`：小电脑服务触发后的业务入口，复用 `git_main.py` 总控逻辑，但使用包含富途夜盘窗口的 Service 流程。
- `.github/workflows/trigger-service-command.yml`：GitHub App 手动触发小电脑运行的入口，只更新并推送 Gitee 上的 `service_command.json`，不运行主业务流程。
- `start_ahns_command_watcher.ps1`：Windows 计划任务调用的启动脚本，设置 UTF-8 输出、仓库目录、Python 路径、日志路径、日志裁剪和 `--primary-remote gitee`。
- `tail_ahns_log.ps1`：查看监听日志的 UTF-8 PowerShell 脚本，优先用它替代手写 `Get-Content -Wait`。
- `sync_repos.py`：主机电脑同步本地、GitHub、Gitee 三边仓库的脚本；建议 `origin` 使用 GitHub HTTPS，并只给 `github.com` 走 SakuraCat HTTP 代理和 OpenSSL，`gitee` 保持直连；疑似网络瞬时失败会短暂重试，GitHub 代理重试仍失败时会直连一次；只会自动合并运行缓存白名单冲突，例如 `cache/*_index_daily.csv`、`cache/fund_estimate_return_cache.json`、`cache/security_return_cache.json`，源码、配置和文档冲突仍会停止。
- `github_gitee_sync.py`：通用 GitHub/Gitee 同名仓库初始化和同步脚本；可复制到其他本地 Git 仓库根目录使用，默认读取 `origin` 推导 Gitee remote；缺少 GitHub remote 时会询问仓库信息，并用 `GITHUB_TOKEN` / `GH_TOKEN` 创建公开 GitHub 仓库；缺 Gitee 仓库时用 `GITEE_ACCESS_TOKEN` 创建公开仓库。
- `main.py`：主计算入口，生成市场 RSI 图、海外/全球基金详细估算图，并写入 `cache/fund_estimate_return_cache.json`。
- `premarket_fund.py`：盘前观察图手动入口；生成 `output/safe_haiwai_premarket.png` 和盘前失败报告，不写正式基金估算缓存。
- `intraday_fund.py`：盘中观察图手动入口；生成 `output/safe_haiwai_intraday.png` 和盘中失败报告，不写正式基金估算缓存。
- `afterhours_fund.py`：盘后观察图手动入口；生成 `output/safe_haiwai_afterhours.png` 和盘后失败报告，不写正式基金估算缓存。
- `futu_night_fund.py`：富途夜盘观察图手动入口；生成 `output/safe_haiwai_night.png` 和夜盘失败报告，不写正式基金估算缓存。
- `fund_estimate_breakdown.py`：只读缓存的估算拆解工具；运行后可手工输入基金代码、正式估值日期或实时观察类型，打印完整持仓贡献表；支持 `--latest`、`--save-txt` 和 `--observation 盘中`。
- `safe_fund.py`：只读基金估算缓存，生成安全版海外/全球每日基金估算图。
- `safe_holidays.py`：只读缓存，生成安全版海外节假日累计观察图。
- `holidays.py`：只读缓存，生成详细版海外节假日累计观察图。
- `sum_holidays.py`：只读缓存，生成节后第 1 / 第 2 个 A 股交易日的海外基金补更新观察图。
- `kepu/first_pic.py`：手动生成“基金预估图怎么看？”科普首图；当前总入口不自动运行。
- `kepu/kepu_sum_holidays.py`：生成节后海外基金补更新规则科普图，仅节后第 1 / 第 2 个 A 股交易日出图。
- `kepu/kepu_xiane.py`：手动可生成海外基金限额科普图；总入口只用 `--table-only` 保留北京时间周日限额表格图。
- `tools/email_send.py`：QQ 邮箱发送模块；环境变量优先，其次读取未跟踪的本地配置文件。
- `tools/get_top10_holdings.py`：基金估算、持仓读取、锚点行情收益、缓存写入、基金表格绘图的核心实现。
- `tools/fund_estimator.py`：历史兼容模块，动态转发到 `tools/get_top10_holdings.py`；新增核心逻辑优先看 `tools/get_top10_holdings.py`。
- `tools/fund_history_io.py`：海外基金历史缓存读取、A 股交易日历文件缓存、交易日识别、区间累计和累计表格绘图。
- `tools/premarket_estimator.py`：盘前、盘中、盘后观察图估算核心，复用基金池、持仓缓存、限购缓存、短缓存、脱敏和绘图能力。
- `tools/futu_night_observation.py` / `tools/futu_night_quotes.py`：富途夜盘观察和报价实现，使用 Futu OpenAPI，不走旧 HTTP/Yahoo 夜盘分支。
- `tools/cache_metadata.py`：缓存说明元数据工具，负责给安全容器型 JSON 附加 `_cache_info` 并生成 `cache/README.md`。
- `tools/paths.py`：集中维护 `cache/`、`output/` 和常用缓存/输出图片路径。
- `tools/safe_display.py`：safe 图脱敏、居中 logo 水印和“鱼师AHNS”品牌文字水印工具。
- `tools/configs/`：集中维护常改配置，包括基金池、代理基金、证券映射、RSI 配置、交易日历参数和总入口运行流程。
- `tools/rsi_data.py` / `stock_analysis.py`：市场指数、ETF 行情分析和 RSI 图表。

## 目录与入口分层

当前顶层入口文件刻意保留不移动，避免破坏用户手动命令、GitHub Actions、Windows 计划任务和 VSCode 运行配置。接手时按用途理解即可：

- 日常总入口：`git_main.py`、`service_main.py`、`main.py`、`safe_fund.py`、`safe_holidays.py`、`sum_holidays.py`。
- 实时观察入口：`premarket_fund.py`、`intraday_fund.py`、`afterhours_fund.py`、`futu_night_fund.py`。
- 小电脑与同步入口：`service_command_watcher.py`、`service_runner.py`、`start_ahns_command_watcher.ps1`、`tail_ahns_log.ps1`、`sync_repos.py`、`github_gitee_sync.py`、`service_command.json`。
- 诊断和手动工具：`check_project.py`、`fund_estimate_breakdown.py`、`stock_analysis.py`、`refresh_fund_limit_cache.py`。
- 内部实现模块：优先看 `tools/`；经常维护的常量和配置优先看 `tools/configs/`；科普图脚本放在 `kepu/`。

后续若要进一步整理目录，建议采用“内部实现迁移 + 顶层同名薄入口保留”的方式，例如把服务端实现移动到 `tools/service/`，但仍保留顶层 `service_command_watcher.py` 等入口文件做兼容转发。不要直接移动或改名主流可运行脚本。

## 配置维护入口

- `tools/configs/fund_universe_configs.py`：海外/全球基金池和历史国内基金池。新增、删除基金代码优先改这里；基金代码请写 6 位字符串，避免前导 0 丢失。
- `tools/configs/fund_proxy_configs.py`：代理型基金配置和海外有效披露持仓增强系数。
- `tools/configs/residual_benchmark_configs.py`：海外股票持仓型基金的补偿仓位基准配置。默认使用纳斯达克100；可按基金代码指定其他基准，例如 `007844` 使用 `XOP` 作为美国油气开采方向代理。
- `tools/configs/market_benchmark_configs.py`：safe 海外基金图底部“基准表”的指数、ETF、海外资产和点位观察指标配置。想隐藏某个基准，把 `enabled` 改为 `False`；隐藏不会删除旧缓存，但 safe 图不会继续展示该项。当前默认启用纳斯达克100、标普500、XOP、费城半导体、现货黄金和每日图 VIX 点位。
- `tools/configs/premarket_configs.py`：盘前观察图配置。`PREMARKET_BENCHMARK_SPECS` 定义图片展示名称和实时 ticker；`PREMARKET_DEFAULT_RESIDUAL_BENCHMARK_KEY` 定义默认补偿仓位；`PREMARKET_FUND_RESIDUAL_BENCHMARK_MAP` 按 6 位基金代码指定补偿基准，例如 `007844`、`006679`、`018852` 使用 `oil_gas_ep`。
- `tools/configs/intraday_configs.py`：盘中观察图配置和时间窗。
- `tools/configs/afterhours_configs.py`：盘后观察图配置和时间窗。
- `tools/configs/futu_night_configs.py`：富途夜盘观察图配置、Futu OpenD 连接参数、短缓存和报价时间校验阈值。
- `tools/configs/safe_image_style_configs.py`：safe 公开图的统一样式配置。标题字号/颜色/间距、图片四周留白、表头底色、正文底色、涨跌颜色、表格行距、列宽、备注字号、水印文字、logo 透明度等都从这里维护，优先不要去绘图函数里硬改。
- `tools/configs/cache_policy_configs.py`：缓存有效期配置。限购缓存 7 天、A 股交易日历 7 天、证券/指数/基金历史保留天数、RSI ETF 实时补点新鲜度等都从这里维护。
- `tools/configs/security_mappings.py`：美股 / 韩国证券代码映射；韩国六位数字代码需要配合名称别名匹配，避免误判 A 股。
- `tools/configs/rsi_configs.py`：市场 RSI 图标的配置。
- `tools/configs/market_calendar_configs.py`：市场交易日历名称、收盘缓冲、韩国节假日置零策略。
- `tools/configs/workflow_configs.py`：GitHub / Service 两套总入口流程和实时观察窗口。新增脚本时复制一项并改 `name` / `script`；想让某一步只生成不发邮件，改 `collect_images=False`；`required` 只做必要性日志标记，不再控制中断。
- `tools/cache_metadata.py`：缓存说明维护入口。新增缓存文件时同步补充用途、生产者、消费者、刷新策略、保留策略和注意事项；不要为了说明强行改 key-map 缓存 schema。

旧入口会尽量保留兼容，例如 `tools/fund_universe.py` 仍可导入 `HAIWAI_FUND_CODES`，但真实配置已移动到 `tools/configs/fund_universe_configs.py`。

## 海外基准源维护

海外基金图底部的“基准表”统一由 `tools/configs/market_benchmark_configs.py` 控制。配置列表 `MARKET_BENCHMARK_ITEMS` 的顺序就是图片展示顺序，每一项的核心字段如下：

- `enabled`：是否展示/主动拉取。`False` 表示隐藏该基准，即使 `cache/fund_estimate_return_cache.json` 里还有旧记录，`safe_fund.py`、`safe_holidays.py`、`sum_holidays.py` 也会过滤掉它；但旧缓存不会被删除。
- `label`：图片中展示的名称。
- `kind`：行情类型，目前支持 `us_index`、`us_security`、`foreign_futures`、`yahoo`、`vix_level`。
- `ticker`：主行情代码。
- `fallback_ticker`：备用行情代码，可选；主源失败时才尝试。
- `display_in_daily_fund`：可选，是否显示在每日海外基金 safe 图底部，默认 `True`。
- `display_in_holidays`：可选，是否显示在节假日 / 节后观察图，默认 `True`。
- `include_in_cumulative`：可选，是否作为收益率参与区间累计复利，默认 `True`；VIX 这类点位指标必须设为 `False`。

当前默认基准源偏国内友好：

- `纳斯达克100`：`kind="us_index", ticker=".NDX"`，走新浪美股指数。
- `标普500`：`kind="us_index", ticker=".INX"`，走新浪美股指数。
- `油气开采指数`：`kind="us_security", ticker="XOP"`，优先走 AKShare 美股日线；XOP 是 ETF，不是指数本体，当前作为美国油气开采方向代理。
- `费城半导体`：`kind="us_index", ticker=".SOX"`，走新浪美股指数。
- `现货黄金`：`kind="foreign_futures", ticker="XAU", fallback_ticker="GC00Y"`，优先新浪外盘期货 XAU，失败后用东方财富国际期货 GC00Y 作为 COMEX 黄金代理。
- `VIX恐慌指数`：`kind="vix_level", ticker="VIX"`，`enabled=True`，只在每日海外基金图显示最新完整交易日收盘点位；优先 CBOE 官方历史 CSV，失败后回退 FRED。它不是涨跌幅，`include_in_cumulative=False`，不会进入节假日累计图。

基准读取失败时只影响该基准行，不中断主流程。每个基准的结果会按 `ticker + valuation_anchor_date` 写入锚点缓存；同一估值日再次生成图片会优先读取缓存。配置里不会自动“全部一路兜到 Yahoo”，只有 `kind="yahoo"` 的项目或代码中明确写了 Yahoo fallback 的项目才会访问 Yahoo。VIX 当前不走 Yahoo。

## 输出图片

- 科普首图：`output/first_pic.png`（手动运行 `kepu/first_pic.py` 生成；总入口不再自动生成）
- RSI / 市场图：
  - `output/nasdaq_analysis.png`
  - `output/nasdaq.png`
  - `output/honglidibo_analysis.png`
  - `output/honglidibo.png`
  - `output/shangzheng_analysis.png`
  - `output/shangzheng.png`
- 基金详细估算图：
  - `output/haiwai_fund.png`（详细版当前在主流程中暂不输出，旧文件可能仍在本地）
- 盘前观察图：
  - `output/safe_haiwai_premarket.png`
  - `output/premarket_failed_holdings_latest.txt`（盘前实时持仓、补偿基准和失败源报告）
- 盘中观察图：
  - `output/safe_haiwai_intraday.png`
  - `output/intraday_failed_holdings_latest.txt`（盘中实时持仓、补偿基准和失败源报告）
- 盘后观察图：
  - `output/safe_haiwai_afterhours.png`
  - `output/afterhours_failed_holdings_latest.txt`（盘后实时持仓、补偿基准和失败源报告）
- 富途夜盘观察图：
  - `output/safe_haiwai_night.png`
  - `output/night_failed_holdings_latest.txt`（富途夜盘实时持仓、补偿基准和失败源报告）
- safe 每日图：
  - `output/safe_haiwai_fund.png`
- 海外节假日累计图：
  - `output/haiwai_holidays.png`
  - `output/safe_holidays.png`
- 节后补更新观察图：
  - `output/sum_holidays.png`（详细版已停用，后续不再新生成/覆盖）
  - `output/safe_sum_holidays.png`
- 科普图：
  - `output/kepu_sum_holidays.png`
  - `output/kepu_xiane.png`（限额科普图，保留手动入口；总入口只生成周日限额表）
  - `output/xiane.png`

旧的 `output/guonei*.png` 文件可能仍在本地目录中，但后续主流程不再生成或加入邮件。不要为了清理旧输出而批量删除文件。

Matplotlib 表格和 RSI 图默认使用 `180 DPI`，用于降低图片体积并保持手机端清晰度。`kepu/` 下科普图是 Pillow 固定像素图，保存时使用 PNG 无损压缩，不靠 DPI 控制尺寸。

## 邮件与 GitHub Actions

- `tools/email_send.py` 不保存真实授权码；本地真实配置放在未跟踪的 `tools/email_local_config.py`。
- 环境变量优先级高于本地配置文件：
  - `QQ_EMAIL_ACCOUNT`
  - `QQ_EMAIL_AUTH_CODE`
  - `QQ_EMAIL_RECEIVER` 可选，缺失时默认发送给 `QQ_EMAIL_ACCOUNT`
- GitHub Repository secrets 只有在 workflow 中显式映射成环境变量才会生效。
- 公开仓库提交前必须确认源码中没有真实 SMTP 授权码。
- 当前 SMTP timeout 默认 `120s`。如果 SMTP 登录正常但发送失败，常见原因是邮件体积较大、网络较慢或服务端中途断开。

## safe 系列现状

safe 公开图的视觉样式已集中到 `tools/configs/safe_image_style_configs.py`。后续如果要改标题和表格间距、图片四周留白、文字大小、颜色、底色、水印文字、水印透明度、表格行距或列宽，优先改这个配置文件：

- 标题：`SAFE_TITLE_STYLE` 控制字号、颜色、粗细、每日图标题 gap 和累计图标题 gap。`cumulative_gap` 越小，`safe_sum_holidays.png` / `safe_holidays.png` 的标题和主表越近。
- 画布：`SAFE_CANVAS_STYLE` 控制每日图导出外边距；顶部留白调 `daily_top_pad_inches`，底部留白调 `daily_bottom_pad_inches`，左右留白调 `daily_left_pad_inches` / `daily_right_pad_inches`。
- 表格：`SAFE_DAILY_TABLE_STYLE`、`SAFE_CUMULATIVE_TABLE_STYLE` 控制正文/表头字号、表头底色、表头文字色、正文底色、画布底色、网格色、行高、横纵向缩放。
- 列宽：`SAFE_DAILY_COLUMN_WIDTHS`、`SAFE_CUMULATIVE_COLUMN_WIDTHS`、`SAFE_BENCHMARK_COLUMN_WIDTHS` 控制不同图的列宽。“列间距”主要通过这里调。
- 涨跌颜色：`SAFE_RETURN_COLORS` 控制红涨、绿跌和无效/中性数据颜色。
- 底部文字：`SAFE_FOOTER_STYLE` 控制合规提示和备注字号、颜色、粗细。
- 水印：`SAFE_WATERMARK_STYLE` 控制居中 logo 的透明度和大小比例，以及斜向“鱼师AHNS”文字水印的内容、字号、颜色、透明度和角度。

配置默认只影响 safe 公开图，不影响详细版调试图。`tools/get_top10_holdings.py` 和 `tools/fund_history_io.py` 已支持从调用方传入样式参数，`safe_fund.py`、`safe_holidays.py`、`sum_holidays.py` 会读取同一份配置。

- `safe_fund.py`：
  - 只读取 `cache/fund_estimate_return_cache.json`。
  - 只读取 `market_group == "overseas"` 的最新缓存。
  - 不显示基金代码；基金名称脱敏；保留模型观察限购信息列，便于公开图解释限购状态。
  - 基金名称使用 `tools.safe_display.mask_fund_name()` 脱敏。
  - 海外图保留 benchmark footer。
  - 输出保持基金预估表格风格，并叠加 `cache/mark.jpg` 居中淡 logo 和斜向“鱼师AHNS”文字水印；水印大小、透明度和角度从 `SAFE_WATERMARK_STYLE` 读取。
- `safe_holidays.py`：
  - 自动判断 A 股是否休市：优先读取 7 天有效的 `cache/a_share_trade_calendar_cache.json`，过期才请求 AkShare；AkShare 失败时先用旧文件缓存，再用本地国内行情 CSV 兜底。
  - 只读取 `main.py` 已写入的海外基金和 benchmark 缓存。
  - 只展示 `market_benchmark_configs.py` 中 `enabled=True` 且 `include_in_cumulative=True` 的收益率基准，旧缓存里的禁用基准和 VIX 点位不会出现在累计表格里。
  - 满足条件才出图；否则只打印原因，不生成新图。
- `sum_holidays.py`：
  - 只读取缓存，不拉行情、不重新计算持仓、不写缓存。
  - 只生成 `output/safe_sum_holidays.png`，不再生成或覆盖详细版 `output/sum_holidays.png`。
  - 节后单日图和累计图都会过滤 `enabled=False` 或 `display_in_holidays=False` 的基准；VIX 点位不展示。
  - 普通周六周日不属于节假日累计收益场景。
  - 节后第 1 个 A 股交易日：读取节前最后一个 A 股交易日对应的海外基金估值日，生成单日观察图。
  - 节后第 2 个 A 股交易日：累计节前最后估值日之后到缓存中最新海外估值日的实际存在记录。
  - 节后第 3 个 A 股交易日起：不生成图，回归 `main.py` / `safe_fund.py` 的普通每日节奏。

## 实时观察

盘前、盘中、盘后和富途夜盘观察是独立于正式主流程的轻量入口。它们的目标是“尽可能使用当下已经有效的信息”，而不是生产正式净值估算缓存。

- 入口：`premarket_fund.py`、`intraday_fund.py`、`afterhours_fund.py`、`futu_night_fund.py`。
- 默认时间窗：盘后 08:00-12:00；富途夜盘 11:30-16:30；盘前 17:30-21:00；盘中 22:40-次日 02:00；测试时使用 `--force`。富途夜盘的 `--force` 只绕过北京时间配置窗口，仍会检查美股夜盘是否真实开市。
- 日期口径：盘后图主标题使用下一美股估值日，报告保留盘后报价日；盘前/盘中使用目标美股交易日；富途夜盘使用夜盘目标估值日。
- 数据边界：盘前美股只接受目标美股交易日的 `pre` 时段报价；盘中只接受 regular 报价；盘后只接受 post 报价；富途夜盘只使用 Futu OpenAPI。
- 富途夜盘依赖：需要本地安装可选 `futu-api` 并启动 Futu OpenD；连接参数在 `tools/configs/futu_night_configs.py`。入口会先判断目标美股夜盘是否处于开市窗口，周末或美国节假日没有夜盘时不加载持仓、不连接 Futu。
- 估算口径：有效持仓贡献 = 原始占净值比例 × 有效持仓增强系数 × 实时涨跌幅；增强后有效权重封顶 100%。剩余权重走该入口配置的补偿基准。
- 缓存边界：实时观察可以读取持仓、限购和 15 分钟实时短缓存，但不能写入 `cache/fund_estimate_return_cache.json`。

## 计算口径摘要

- 普通持仓型基金：读取公开披露的季度前十大持仓，按持仓权重和证券涨跌幅估算。
- 代理型基金：若基金在 `DEFAULT_FUND_PROXY_MAP` 中，使用相关 ETF / 指数代理资产和配置权重估算。
- 海外 / QDII 基金：使用统一 `valuation_anchor_date` 作为估值锚点；北京时间运行日记录为 `run_date_bj`。
- 估值锚点由 US/CN/HK/KR 中最近一个已确认完整交易日决定；各市场再分别判断该锚点是 `traded/closed/pending/missing/stale`。
- 所有海外/全球基金估算只使用完整日线，不使用 A 股、港股或韩国盘中实时行情。
- 如果某市场在锚点日休市，该市场持仓贡献为 0；如果应开盘但行情缺失或 stale，也贡献 0，并将基金记录标记为 partial/stale，后续可被更完整数据覆盖。
- 市场交易日历在单次运行中会按 `(market, start_date, end_date)` 做内存缓存；同一估值日、同一市场不重复计算开闭市和收盘完成状态。
- A 股节假日判断优先读取 `cache/a_share_trade_calendar_cache.json`，默认 7 天有效；过期才主动联网刷新，AkShare 失败时优先使用旧文件缓存，旧文件也不可用时再用本地行情 CSV 兜底。
- A 股、港股日线改为“涨跌幅源优先早停、复权价其次、裸 close 最后兜底”：可信涨跌幅源命中目标估值日后立即返回，不再无条件请求全部源。
- 跨市场个股日收益优先级统一为“官方涨跌幅列优先、复权/调整后收盘价其次、裸收盘价最后兜底”：
  - A 股：优先官方涨跌幅列；无涨跌幅列时优先新浪 `qfq/hfq` 复权价；最后才用 raw close。旧 `ak_stock_zh_a_daily_sina_close_calc` 缓存不再视为新鲜，会自动刷新，避免除权日误算。
  - 港股：同时尝试新浪 raw/qfq/hfq 和东方财富港股日线；优先任意数据源的涨跌幅列，其次 `qfq/hfq`，最后 raw close。旧 `ak_stock_hk_daily_sina_close_calc` 缓存会自动刷新。
  - 美股：保留新浪日线、东方财富、Yahoo 的兜底顺序；东方财富美股 kline 优先解析日涨跌幅字段，Yahoo fallback 优先使用 `adjclose`，裸 close 只作为兜底。若 Yahoo 也失败，只打印完整错误链并把该证券标为 missing/stale，不中断后续基金。若裸 close 计算出的单日绝对涨跌超过当前阈值 `35%` 且没有复权/调整后口径确认，会继续尝试其他源，仍无法确认时标为 missing/stale，避免拆股日误写入暴涨暴跌。
  - 韩国：当前 pykrx 已优先读取“涨跌率”列，暂不改主逻辑。
  - 指数、期货、黄金：没有股票除权/拆股语义，仍按完整日线 close-to-close 计算。
- RSI 行情优先使用本地 `cache/*_index_daily.csv`：缓存已经覆盖最新完整交易日时直接复用；国内 ETF 需要盘中观察，在历史缓存足够新且 `include_realtime=True` 时只补实时点，不重拉整段历史。
- 普通海外股票持仓型基金保留“有效持仓增强 + 配置基准补偿仓位 + 100% 权重封顶”口径；默认补偿基准为纳斯达克100，单基金可在 `tools/configs/residual_benchmark_configs.py` 指定其他基准。
- `007844` 当前使用 `XOP` 作为美国油气开采方向补偿仓位代理。`XOP` 是跟踪美国油气勘探与生产方向指数的 ETF，不是指数本身；仍按统一估值锚点读取完整日线。
- 区间累计收益使用复利：
  `累计 = (prod(1 + 每日估算收益率 / 100) - 1) * 100`
- 同一基金、同一 `valuation_anchor_date` 只计入一次；优先数据质量更高、`complete` 和更高 `completeness_score` 的记录。
- 海外六位数股票代码可能会被识别为 A 股；当前按用户选择不修复，允许失败后走配置基准补偿口径。

## 缓存策略

- `security_return_cache.json`：
  - 新锚点 key 为 `SECURITY:{market}:{ticker}:{valuation_anchor_date}`。
  - `traded` / `closed` 表示已拿到有效信息，可长期复用。
  - `pending` / `missing` / `stale` 会写入用于诊断和报告，但读取侧不再把它们视为 fresh；下一次运行必须重新请求交易日历和行情源。
  - 已有 `traded` 记录不被 `pending` / `missing` / `stale` 覆盖。
  - A 股旧裸收盘价来源 `ak_stock_zh_a_daily_sina_close_calc`、港股旧裸收盘价来源 `ak_stock_hk_daily_sina_close_calc` 不再视为新鲜；命中后会触发刷新，优先写入涨跌幅列或复权口径结果。
  - 美股旧裸收盘价缓存仍保持兼容；如果单日绝对涨跌异常大，会触发刷新并尝试更可靠的数据源。
  - 小时桶缓存保留 15 天。
  - 普通证券日缓存保留 30 天。
  - 指数行情缓存保留 300 天。
  - 无法解析日期的缓存项保留，避免误删有效缓存。
- `fund_estimate_return_cache.json`：
  - 只由完整日线主流程缓存海外/全球基金，不再缓存国内基金。
  - 盘前、盘中、盘后、富途夜盘实时观察入口均不写本文件。
  - 基金 key 为 `overseas:{fund_code}:{valuation_anchor_date}`，同时保留兼容字段 `valuation_date`。
  - 基准表结果写入 `benchmark_records`，由 `tools/configs/market_benchmark_configs.py` 的 `enabled=True` 项主动更新；显示端会过滤禁用基准和不适用场景的旧记录。收益率基准使用 `return_pct`，VIX 点位使用 `value_type="level"`、`value/display_value`，并保持 `return_pct=null`。
  - 覆盖规则由数据质量驱动，不再使用 15:30 冻结逻辑。
  - 安全内嵌 `_cache_info`，真实业务数据仍在 `records` / `benchmark_records` 下；读取方不应遍历顶层所有 key 当业务记录。
  - `records` 和 `benchmark_records` 保留最近 300 天。
  - 按 `valuation_anchor_date` / 兼容字段 `valuation_date` 裁剪；缺失时回退 `run_date_bj`。
- `a_share_trade_calendar_cache.json`：
  - 用于 A 股交易日历降频，默认 7 天有效。
  - 字段包含 `fetched_at`、`source`、`trade_dates`。
  - 安全内嵌 `_cache_info`；读取方只取 `fetched_at`、`source`、`trade_dates`。
  - AkShare 刷新失败时优先用过期旧缓存；旧缓存也没有时才回退本地行情 CSV。
- `*_index_daily.csv`：
  - RSI / 指数行情 CSV 缓存。
  - 缓存已经覆盖最新完整交易日时优先直接使用。
  - 国内 ETF 且 `include_realtime=True` 时只补实时点，不重拉整段历史。
  - CSV 按标的整文件更新，不在文件尾无限追加无关历史；读取方使用 `pandas.read_csv()`，不要插入注释行。
- `fund_holdings_cache.json`：
  - 按基金代码和持仓类型保存公开披露持仓，非披露窗口优先复用。
  - 更新时覆盖同一基金/类型的缓存项，不把每次运行都追加成历史版本。
- `fund_purchase_limit_cache.json`：
  - 按基金代码保存申购限额信息，默认 7 天有效。
  - 刷新时覆盖同一基金代码记录，不做无限历史留存。
- 实时观察短缓存：
  - `premarket_quote_cache.json`、`intraday_quote_cache.json`、`afterhours_quote_cache.json` 均由 `tools/premarket_estimator.py` 写入，TTL 15 分钟，失败结果不跨运行缓存。
  - `futu_night_return_cache.json` 由富途夜盘模块写入，TTL 15 分钟，并按目标夜盘估值日和报价时间重新校验。
  - `night_quote_cache.json` 是旧 HTTP/Yahoo 夜盘 legacy 缓存，当前代码不再写入；保留旧文件，不自动清理 `cache/`。
- `output/failed_holdings_latest.txt`：
  - 每轮海外基金估算后覆盖写入，不追加历史。
  - 包含运行汇总、行情请求统计、唯一证券汇总、失败/未完成持仓明细。
  - 这是本地排查文件，不进入邮件正文，不影响图片生成。
- `output/premarket_failed_holdings_latest.txt`：
  - 每轮盘前观察后覆盖写入，不追加历史。
  - 用于区分盘前实时持仓失败、补偿基准失败和底层数据源错误。
- `output/intraday_failed_holdings_latest.txt`、`output/afterhours_failed_holdings_latest.txt`、`output/night_failed_holdings_latest.txt`：
  - 每轮对应实时观察后覆盖写入，不追加历史。
  - 用于区分实时持仓失败、补偿基准失败和底层数据源错误。
- 行情请求统计：
  - 只存在当前 Python 进程内，不写 JSON。
  - 控制台只打印摘要，完整明细写入 `output/failed_holdings_latest.txt`。
- `cache/README.md` 由 `tools/cache_metadata.py` 生成，负责说明 key-map JSON、CSV、图片缓存和报告文件。不要给 `security_return_cache.json`、`fund_holdings_cache.json`、`fund_purchase_limit_cache.json` 或 `*_index_daily.csv` 手工插入注释字段，否则可能破坏遍历或 `pandas.read_csv()`。

## 后续可优化方向

- 增加只读数据源健康检查脚本：集中探测新浪、东方财富、AkShare、CBOE/FRED、Yahoo fallback 是否可用，不写基金缓存。
- 补强美股特殊代码和持仓映射：石油、能源、ADR、改名或退市证券更容易出现行情源滞后，可逐步沉淀到映射或代理配置。
- 给 safe 图增加自动视觉回归检查：检查图片尺寸、非空、水印、表格行数、VIX 是否只在每日图出现，减少样式配置改动后的人工检查成本。

## 抖音发布注意

- safe 图降低风险，但不能保证账号一定不被误判。
- 发文避免“推荐、买入、卖出、加仓、跟投、稳赚、私信、进群、课程、带单、领取资料”等表达。
- 文案建议保持：
  `个人公开数据建模复盘，不收费、不荐基、不带单、不拉群，不构成任何投资建议。非实时净值，最终以基金公司公告为准。`
- 第一张建议放 `kepu/first_pic.py` 生成的说明图，后续再放 safe 系列估算图或科普图。
- 不建议公开展示完整基金代码、强排序榜单或过强红绿刺激。

## 常用验证命令

全项目编译：

```powershell
$files = @('.\git_main.py','.\service_main.py','.\service_runner.py','.\service_command_watcher.py','.\sync_repos.py','.\github_gitee_sync.py','.\check_project.py','.\main.py','.\premarket_fund.py','.\intraday_fund.py','.\afterhours_fund.py','.\futu_night_fund.py','.\fund_estimate_breakdown.py','.\safe_fund.py','.\safe_holidays.py','.\holidays.py','.\sum_holidays.py','.\stock_analysis.py','.\kepu\first_pic.py','.\kepu\kepu_sum_holidays.py','.\kepu\kepu_xiane.py') + (Get-ChildItem .\tools -File -Filter *.py | ForEach-Object { $_.FullName }) + (Get-ChildItem .\tools\configs -File -Filter *.py | ForEach-Object { $_.FullName }); & F:\anaconda\envs\py310\python.exe -m py_compile @files
```

小电脑服务端脚本编译检查：

```powershell
& D:\anaconda\envs\py310\python.exe -m py_compile .\service_main.py .\service_runner.py .\service_command_watcher.py .\sync_repos.py .\github_gitee_sync.py
```

同步脚本预演：

```powershell
Set-Location G:\AHNS
& F:\anaconda\envs\py310\python.exe .\sync_repos.py --dry-run
```

通用 GitHub/Gitee 同名仓库同步脚本检查：

```powershell
& F:\anaconda\envs\py310\python.exe .\github_gitee_sync.py --init-gitee
& F:\anaconda\envs\py310\python.exe .\github_gitee_sync.py --dry-run
```

运行前自检：

```powershell
& F:\anaconda\envs\py310\python.exe .\check_project.py
```

总入口预演：

```powershell
& F:\anaconda\envs\py310\python.exe .\git_main.py --no-send
```

单独生成常用图：

```powershell
& F:\anaconda\envs\py310\python.exe .\main.py
& F:\anaconda\envs\py310\python.exe .\afterhours_fund.py --force
& F:\anaconda\envs\py310\python.exe .\futu_night_fund.py --force
& F:\anaconda\envs\py310\python.exe .\premarket_fund.py --force
& F:\anaconda\envs\py310\python.exe .\intraday_fund.py --force
& F:\anaconda\envs\py310\python.exe .\kepu\first_pic.py
& F:\anaconda\envs\py310\python.exe .\safe_fund.py
& F:\anaconda\envs\py310\python.exe .\safe_holidays.py
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py
& F:\anaconda\envs\py310\python.exe .\fund_estimate_breakdown.py 012922 --observation 盘中
```

检查最新失败持仓和唯一证券汇总：

```powershell
Get-Content .\output\failed_holdings_latest.txt -Encoding UTF8 -TotalCount 120
```

节后补更新测试：

```powershell
& F:\anaconda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-06
& F:\anaconda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-07
& F:\anaconda\envs\py310\python.exe .\sum_holidays.py --today 2026-05-08
```

科普图测试：

```powershell
& F:\anaconda\envs\py310\python.exe .\kepu\kepu_sum_holidays.py --today 2026-05-06
& F:\anaconda\envs\py310\python.exe .\kepu\kepu_xiane.py --today 2026-05-08
& F:\anaconda\envs\py310\python.exe .\kepu\kepu_xiane.py --today 2026-05-10
```

行情口径和降频缓存抽样：

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

## 常见排障

- Yahoo、新浪、东方财富出现 `SSLEOFError`、`Max retries exceeded` 或 HTTPS 连接失败：通常是网络链路、服务端限流或接口临时不稳定，不代表代码一定坏了。正式流程会记录失败链路并继续处理其他证券；若没有有效缓存，该证券会在下次运行继续重试。
- `CTRA` 这类单只美股失败：只影响持有该证券的基金和对应补偿前的有效持仓覆盖率。优先看 `output/failed_holdings_latest.txt` 或盘前的 `output/premarket_failed_holdings_latest.txt`，不要为了单券失败中断整套流程。
- VIX 每日图显示的是恐慌指数点位，不是涨跌幅；正常情况下 `safe_holidays.py`、`sum_holidays.py` 不展示 VIX。如果累计图里出现 VIX，先确认配置中 `display_in_holidays=False`、`include_in_cumulative=False`，再重新运行对应出图脚本。
- 基准源失败：不会中断主流程，只会让对应基准行显示无有效数据或不参与累计。配置不会自动把所有基准都兜到 Yahoo；只有 `kind="yahoo"` 或明确写了 Yahoo fallback 的路径才会访问 Yahoo。
- 国内运行访问 Yahoo 慢或失败：当前默认基准里不再主动依赖 Yahoo。纳斯达克100、标普500、费城半导体优先新浪美股指数，XOP 优先 AKShare 美股日线，黄金优先新浪外盘期货/东方财富国际期货，VIX 优先 CBOE 官方 CSV 并用 FRED 兜底。
- 国内 ETF 需要实时结果：RSI CSV 历史缓存足够新时，国内 ETF 仍会通过 `include_realtime=True` 补实时点；不要把这类请求误改成纯日线复用。
- A 股或港股单日涨跌异常大：优先怀疑除权、拆股、送转、复权口径或旧缓存。先运行 `fund_estimate_breakdown.py` 查看该持仓的数据源字段；正常情况下应优先看到 `pct`、`qfq`、`hfq`、`adjclose` 等来源，而不是旧裸 close 计算来源。
- `fund_estimate_breakdown.py` 只读缓存：如果刚修复了个股口径但基金合计仍是旧数，需要先运行 `main.py` 或 `git_main.py --no-send` 重算基金缓存，再用拆解工具查看。
- safe 图文字大小、颜色、表头色、底色、水印不满意：优先改 `tools/configs/safe_image_style_configs.py`，再单独运行 `safe_fund.py`、`safe_holidays.py` 或 `sum_holidays.py --today <日期>` 预览。
- A 股节假日判断频繁联网：检查 `cache/a_share_trade_calendar_cache.json` 是否存在、`fetched_at` 是否在 7 天内；缓存新鲜时脚本日志应显示 `fresh`。
- RSI 图仍频繁重拉历史：检查对应 `cache/*_index_daily.csv` 是否存在、最新日期是否足够新，以及文件是否已在当天检查过。
- 需要查看本轮异常持仓：打开 `output/failed_holdings_latest.txt`，先看“运行汇总”和“唯一证券汇总”，再看底部“失败/未完成持仓明细”。
- Actions 自动回推缓存后，本地运行出现 JSON 解析失败：先停止继续写缓存，检查是否处于 Git merge 冲突。运行缓存冲突优先运行 `sync_repos.py --resolve-cache-conflicts` 自动合并；如果不是 merge 冲突，再按报错文件和行号定位破损片段。不要给 key-map JSON 手工加入注释字段。
- 小电脑监听日志里如果还出现主动访问 GitHub，先检查计划任务参数和 `start_ahns_command_watcher.ps1`，应只传 `--primary-remote gitee`。
- `sync_repos.py` 只自动处理运行缓存白名单冲突；遇到源码、配置、文档或非白名单缓存冲突会停止。不要自动 reset 或覆盖远端；正确流程是人工解决冲突、`git add`、`git commit`，然后重新运行同步脚本。
- `github_gitee_sync.py` 创建 GitHub 仓库失败时，先检查 `GITHUB_TOKEN` 或 `GH_TOKEN` 是否存在、令牌是否有创建仓库权限、GitHub 用户/组织是否和 `--github-owner` 一致；默认创建公开 GitHub 仓库，私有 GitHub 仓库才加 `--github-private`。
- `github_gitee_sync.py` 创建 Gitee 仓库失败时，先检查 `GITEE_ACCESS_TOKEN` 是否存在、令牌是否有创建仓库权限、Gitee 命名空间是否和 `--gitee-owner` 一致；默认创建公开 Gitee 仓库，私有 Gitee 仓库才加 `--private`。

## 当前状态摘要

- 总控入口是 `git_main.py` / `service_main.py`，流程由 `tools/configs/workflow_configs.py` 管理；`main.py` 保持主计算职责，不作为日常配置入口。
- GitHub / 主机流程不含富途夜盘，也不自动生成 `first_pic.py`；小电脑 Service 流程额外包含富途夜盘窗口。
- 海外/全球基金估算已经统一为 `valuation_anchor_date` 锚点口径；正式主流程只用完整日线，盘前/盘中/盘后/富途夜盘实时观察由独立入口承担。
- safe 公开图、水印、标题、颜色、表格行距和列宽已集中到 `tools/configs/safe_image_style_configs.py`。
- 海外基准表和补偿仓位均配置化；正式图看 `market_benchmark_configs.py` 与 `residual_benchmark_configs.py`，实时观察图看 `premarket_configs.py`、`intraday_configs.py`、`afterhours_configs.py`、`futu_night_configs.py`。
- 缓存覆盖已经改为数据质量驱动：`complete/traded/closed` 优先，失败、未确认或陈旧记录不会覆盖更好的旧记录；`pending/missing/stale` 不再阻止下次请求。
- 缓存说明由 `tools/cache_metadata.py` 维护；安全 JSON 内嵌 `_cache_info`，其他缓存通过 `cache/README.md` 说明。
- 行情失败报告分为正式估算的 `output/failed_holdings_latest.txt` 和四个实时观察失败报告。
- GitHub Actions 会定时或手动运行并自动回推缓存；本地提交前要注意先同步远端缓存，避免 JSON 合并损坏。
- 小电脑服务器当前只主动监听和同步 `gitee/main`；GitHub 同步由主机电脑运行 `sync_repos.py` 负责；复制到其他仓库的一次性同名仓库初始化可用 `github_gitee_sync.py`。
- `C:\Users\Administrator\Desktop\AHNS` 是当前仓库根目录；旧的 `AHNS\AHNS` 嵌套目录不要再写入脚本或计划任务。

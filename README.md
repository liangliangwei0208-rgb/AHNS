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


## 限购每 3 天更新与变化图

`fund_limit_change.py --auto` 已加入 GitHub / 主机 / Service 的共享总流程，放在 RSI 之后、正式估算之前，所有实时窗口与假期流程均检查。每只基金从上次成功抓取起满 72 小时后，在下一次流程运行时刷新；不新增定时任务。单独运行该入口会更新到期缓存并生成变化图，但不会自行发送邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\fund_limit_change.py --auto
```

- 首次先用已有有效限购缓存建立基线，再刷新到期数据；无旧记录的基金仅初始化。金额格式差异不算变化，暂停 / 恢复申购、限购金额调整等真实变化才出图。
- 手动强刷仍不出图、不发邮件；产生的变化由下一次总流程检测。请求失败或未知不覆盖有效值、不更新成功抓取时间，下次运行重试。
- 独立状态 `cache/fund_limit_change_state.json` 按基金记录比较基线和最近事件；先持久化待出图事件，图片保存成功再标记完成。无变化或事件已完成不重复出图；绘图失败保留待处理事件。
- 自动图片输出到 `output/fund_limit_change/latest/<基金代码>.png`，每只基金覆盖最新变化图，不删除旧图片。总流程只把本次新增或更新图片纳入邮件。
- 1080 像素宽浅色竖图展示完整基金名称、前后限购信息、北京时间检测时间，以及现有缓存中最新披露的前十大持仓。检测时间不是公告生效时间；持仓不联网刷新，缺失则出占位区域。
- 独立样式入口是 `tools/configs/fund_limit_change_style_configs.py`，控制米白背景、深蓝标题、卡片、字号、边距与水印透明度；复用 `cache/mark.jpg` logo 和“鱼师AHNS”水印。logo 缺失时保留待出图事件并报告失败。
- 缓存自检只读检查新状态，`sync_repos.py` 按基金合并新状态；优先较新的有效观察，同一变化保留成功出图标识。原限购缓存仍为 key-map，不增加顶层说明字段。

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
├── git_main.py                  # GitHub/主机总控入口：RSI 全天运行；实时窗口优先
├── service_main.py              # 小电脑总控入口：复用 git_main，总流程额外包含富途夜盘窗口
├── main.py                      # 主计算入口：手动运行含 RSI；总入口用 --skip-rsi 做正式基金估算
├── safe_fund.py                 # safe 收盘观察图，自动总入口仅 06:00-13:40 运行
├── safe_holidays.py             # safe 节假日累计图
├── sum_holidays.py              # 节后补更新观察图
├── premarket_fund.py            # 盘前观察图手动入口，不写正式估算缓存
├── intraday_fund.py             # 盘中观察图手动入口，不写正式估算缓存
├── afterhours_fund.py           # 盘后观察图手动入口，不写正式估算缓存
├── futu_night_fund.py           # 富途夜盘观察图手动入口，不写正式估算缓存
├── check_project.py             # 运行前自检，只检查不修改
├── fund_estimate_breakdown.py    # 基金估算完整拆解查询工具
├── fund_limit_change.py          # 全天检查限购，满 72 小时刷新，有变化时生成带缓存持仓的竖图
├── fund_holding_change.py        # 基金前十大持仓变化解读图；总入口自动检测，亦可手动生成
├── fund_region_allocation.py     # 晨星股票地区分布对比图；总入口仅在收盘流程自动检查
├── stock_analysis.py            # 市场 RSI 图入口；总入口全天固定运行
├── service_command_watcher.py    # 小电脑 Gitee command 监听入口
├── service_runner.py             # 小电脑单次同步、运行、提交、推送流程
├── service_gui.py                # 小电脑一键运行图形界面，按钮触发 service_runner
├── start_ahns_command_watcher.ps1 # 小电脑 Windows 计划任务启动脚本
├── start_service_gui.ps1         # 小电脑双击打开一键运行界面
├── sleep_ahns_server.ps1         # 小电脑 00:00 停止 AHNS/Futu 并进入 S3 睡眠
├── wake_ahns_server.ps1          # 小电脑 06:00 唤醒后启动 Futu、监听器和 GUI
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

生成 012922 最新一期和上一期真实前十大持仓变化解读图：

```powershell
& F:\anaconda\envs\py310\python.exe .\fund_holding_change.py
& F:\anaconda\envs\py310\python.exe .\fund_holding_change.py 012922 --save-csv
& F:\anaconda\envs\py310\python.exe .\fund_holding_change.py --auto
```

`--auto` 用于总入口：读取基金库和 `cache/fund_holdings_cache.json`，只有持仓季度或真实披露字段指纹变化时才生成图片并纳入邮件。首次没有 `cache/fund_holding_change_state.json` 时只初始化状态，不批量出图；需要手动强制某只基金时可设置 `AHNS_HOLDING_CHANGE_FUND_CODE=012922` 后运行 `--auto`。

生成晨星公开页的海外基金**股票地区分布**图（不包含现金、债券等非股票资产）：

```powershell
# 全部基金池分页生成；默认每页 8 只基金。
& F:\anaconda\envs\py310\python.exe .\fund_region_allocation.py --all

# 指定基金单页，并忽略 7 天地区缓存直连晨星刷新。
& F:\anaconda\envs\py310\python.exe .\fund_region_allocation.py --fund-code 012922 --refresh
```

总入口使用 `fund_region_allocation.py --auto`，首次会生成全量基线页，之后只有晨星披露日期或地区权重变化时才更新对应分页并随邮件发送。晨星请求使用专用直连会话 `trust_env=False`，不会继承 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`；若 VPN 开启 TUN 全局接管，仍需在 VPN 软件里为 `www.morningstar.cn` 配置直连规则。

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
- 手机端可在 GitHub App 手动运行 `Trigger Service Command`；`holding_fund_code` 留空表示按基金库自动检测持仓变化，填写 6 位基金代码会让小电脑本轮强制生成该基金持仓变化图。
- 坐在小电脑服务器前时，也可以直接运行 `service_gui.py` 或双击 `start_service_gui.ps1`。GUI 的“立即运行 service_main”会按当前时间窗口运行 Service 流程；“强制刷新限购+持仓缓存”会刷新全基金池限购状态和前十大持仓，然后提交推送到 Gitee，不生成图片、不发邮件。两项操作都不修改 `service_command.json`，也不依赖 `run_flag=1`。

小电脑监听入口：

```powershell
Set-Location "C:\Users\Administrator\Desktop\AHNS"
& D:\anaconda\envs\py310\python.exe .\service_command_watcher.py --interval-seconds 60 --primary-remote gitee
```

小电脑一键按钮界面：

```powershell
Set-Location "C:\Users\Administrator\Desktop\AHNS"
& D:\anaconda\envs\py310\python.exe .\service_gui.py
```

仅强制刷新全基金池限购与持仓缓存：

```powershell
& D:\anaconda\envs\py310\python.exe .\service_runner.py --refresh-fund-limit-cache --primary-remote gitee
```

主机电脑调试界面时建议跳过 Git 且不发邮件：

```powershell
& F:\anaconda\envs\py310\python.exe .\service_gui.py --python-exe F:\anaconda\envs\py310\python.exe --skip-git --no-send
```

计划任务约定：

- `Futu OpenD Autostart`：登录 `Administrator` 后启动 `C:\Users\Administrator\AppData\Roaming\Futu_OpenD\Futu_OpenD.exe`。
- `AHNS Service GUI`：登录 `Administrator` 后打开 `service_gui.py` 图形界面；GUI 必须依附桌面会话，所以不是无人登录前后台启动。
- `AHNS Command Watcher`：每日 06:00 启动，登录后也会启动；实际执行 `start_ahns_command_watcher.ps1`，脚本在 06:00 前会直接退出。
- `AHNS Command Watcher Stop`：每日 00:00 停止监听任务，并结束仍在运行的 `service_command_watcher.py`。
- `AHNS Health Monitor`：06:00-24:00 每 5 分钟检查监听器、Futu OpenD、ToDesk 和 GUI；缺失时优先重启对应进程。监听器 15 分钟内连续 3 次无法存活时才请求重启 Windows，并限制 6 小时内最多重启一次。
- `AHNS Server Sleep`：当前已停用，不再于每日 00:00 让小电脑进入 S3 睡眠。
- `AHNS Server Wake And Start`：当前随睡眠策略一并停用；系统交流/直流自动睡眠均设为“从不”。脚本和任务仍保留，后续需要时可以重新启用。

系统蓝屏保持内核转储，并在转储完成后自动重启。普通业务脚本返回非 0 只记录失败，不会重启整台电脑；监听器进程异常退出由计划任务和健康监控负责恢复。

监听日志：

```text
C:\Users\Administrator\Desktop\AHNS\logs\service_command_watcher.log
C:\Users\Administrator\Desktop\AHNS\logs\health_monitor.log
C:\Users\Administrator\Desktop\AHNS\logs\server_power.log
C:\Users\Administrator\Desktop\AHNS\logs\diagnostics\
```

日志文件占用的是磁盘，不会一直占用内存。`start_ahns_command_watcher.ps1` 每次启动时会检查日志大小；如果超过 20MB，会保留最近 3000 行并裁剪旧内容，避免长期运行后无限变大。

检查小电脑是否正在监听：

```powershell
schtasks /Query /TN "Futu OpenD Autostart"
schtasks /Query /TN "AHNS Service GUI" /V /FO LIST
schtasks /Query /TN "AHNS Command Watcher"
schtasks /Query /TN "AHNS Command Watcher Stop"
schtasks /Query /TN "AHNS Health Monitor" /V /FO LIST
schtasks /Query /TN "AHNS Server Sleep" /V /FO LIST
schtasks /Query /TN "AHNS Server Wake And Start" /V /FO LIST

Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "Futu_OpenD|service_command_watcher.py" } |
  Select-Object ProcessId, Name, CommandLine

Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match "python" -and $_.CommandLine -match "service_gui.py" } |
  Select-Object ProcessId, Name, CreationDate, CommandLine

& "C:\Users\Administrator\Desktop\AHNS\tail_ahns_log.ps1"
Get-Content "C:\Users\Administrator\Desktop\AHNS\logs\service_gui.log" -Tail 80
Get-Content "C:\Users\Administrator\Desktop\AHNS\logs\health_monitor.log" -Tail 80
Get-Content "C:\Users\Administrator\Desktop\AHNS\logs\server_power.log" -Tail 80

Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl" |
  Select-Object CrashDumpEnabled, AutoReboot, LogEvent, DumpFile, MinidumpDir
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

旧的 UTF-16 混写日志会在维护时移动为带时间戳的备份；重启 `AHNS Command Watcher` 后，`watcher_supervisor.py` 会以 UTF-8 收集输出，不再使用 Windows PowerShell 5.1 的 `*>>`。监听器异常退出后，监督器会等待 60 秒重新启动。

主机电脑同步本地、GitHub、Gitee：

```powershell
Set-Location G:\AHNS
& F:\anaconda\envs\py310\python.exe .\sync_repos.py
```

`sync_repos.py` 默认同步 `main` 分支，默认远程名是 `origin`（GitHub）和 `gitee`（Gitee）。主机电脑建议让 `origin` 使用 `https://github.com/liangliangwei0208-rgb/AHNS.git`，并只给 `github.com` 配置 SakuraCat HTTP 代理和 OpenSSL；`gitee` 保持直连。流程是：检查分支和工作区、拉取两个远程、合并远程提交、推送到两个远程、打印最终提交位置。遇到疑似代理/网络瞬时失败会短暂重试；GitHub 代理重试仍失败时会尝试直连一次。若 GitHub Actions 和小电脑同时写运行缓存，`sync_repos.py` 会自动合并白名单缓存冲突：`cache/*_index_daily.csv` 按日期合并；基金估算、证券收益和实时短缓存按 key 保留数据质量更好、时间更新的记录；持仓变化和晨星地区缓存按基金、有效性、披露日期及刷新时间合并。源码、配置、文档或非白名单文件冲突不会自动处理，需要手动解决冲突、`git add`、`git commit` 后再重新运行。若 GitHub 中途网络失败但最后刷新后本地、GitHub、Gitee 三边提交一致，脚本会保留 WARN 并按同步成功处理。

如果仓库已经卡在运行缓存 merge 冲突状态，可只恢复当前冲突，不重新拉取或推送：

```powershell
& F:\anaconda\envs\py310\python.exe .\sync_repos.py --resolve-cache-conflicts
```

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

### 手机端触发小电脑运行

如果不方便在手机浏览器里打开 Gitee 修改 `service_command.json`，可以直接用 GitHub App 手动运行 workflow：`Trigger Service Command`。这个 workflow 只做一件事：把 Gitee `main` 上的 `service_command.json` 改成 `run_flag=1` 并推回 Gitee；小电脑 watcher 下一轮轮询到后会照常运行 `service_main.py`，结束后再把 `run_flag` 改回 `0` 并回写状态。

GitHub 仓库需要在 Settings -> Secrets and variables -> Actions -> Secrets 中配置：

- `GITEE_PRIVATE_CODE`：Gitee 私人令牌。必须放在 Secrets，不建议放普通 Variables，避免日志遮盖规则不生效。

手机端使用方式：

1. 打开 GitHub App，进入仓库的 Actions。
2. 选择 `Trigger Service Command`。
3. 点 `Run workflow`，可选设置 `no_send`、`receiver` 和 `message`。
4. 等待小电脑 watcher 轮询，当前默认最多约 60 秒响应。

这个 workflow 的复杂逻辑已放在 `tools/trigger_service_command.py`，workflow 文件本身只负责 checkout、编译 helper、运行 helper 和写 Step Summary。helper 会基于 Gitee 最新 `main` 提交修改指令文件，只 stage `service_command.json`，不会提交缓存、输出图或源码，也不会把 Gitee 令牌写入仓库、Git config 或日志明文。Actions 日志会打印触发人、输入摘要、Gitee fetch、指令提交和每次送达尝试；如果 Gitee 推送临时失败，会按默认 HTTPS、HTTP/1.1、刷新最新 Gitee main 后重建提交、放宽 HTTP 低速保护的顺序重试，最后再用 Gitee API 直接更新 `service_command.json` 作为兜底。

## 常用维护入口

- `service_command_watcher.py`：小电脑服务器长期监听入口，默认监听 `gitee/main` 的 `service_command.json`，不再默认兜底访问 GitHub。
- `service_runner.py`：小电脑服务器一次性服务流程，负责 pull、运行 `service_main.py`、提交安全范围内的变化、push。
- `service_gui.py`：小电脑服务器一键运行界面；按钮调用 `service_runner.py`，不改 command 文件。
- `start_ahns_command_watcher.ps1`：计划任务调用的启动脚本，设置 UTF-8 输出、仓库目录、Python 路径、日志路径、日志裁剪和 `--primary-remote gitee`。
- `start_service_gui.ps1`：小电脑双击启动 GUI 的脚本，只负责切换目录和启动 `service_gui.py`。
- `sleep_ahns_server.ps1` / `wake_ahns_server.ps1`：小电脑每日 00:00 睡眠收尾和 06:00 唤醒启动脚本；只停止 AHNS/Futu 相关进程，不批量删除文件。
- `tail_ahns_log.ps1`：查看监听日志的 UTF-8 PowerShell 脚本，优先用它替代手写 `Get-Content -Wait`。
- `sync_repos.py`：主机电脑三边同步脚本，用于把本地、GitHub、Gitee 对齐。
- `github_gitee_sync.py`：通用同名仓库同步脚本，可复制到其他仓库使用；会从 GitHub remote 推导 Gitee remote；若缺少 GitHub remote，会询问仓库信息并用 `GITHUB_TOKEN` / `GH_TOKEN` 创建 GitHub 仓库；必要时再用 `GITEE_ACCESS_TOKEN` 自动创建公开 Gitee 仓库。
- `tools/configs/workflow_configs.py`：维护 GitHub 流程和 Service 流程。`stock_analysis.py` 配置为 `always_run=True`，无论是否命中实时窗口都会先运行 RSI；命中盘前、盘中、盘后或富途夜盘实时窗口时，总入口默认只额外运行对应实时观察步骤。若盘后/富途夜盘窗口与 `safe_fund.py` 的 06:00-13:40 收盘窗口重叠，则会同时运行 `close_observation_group=True` 的收盘必要步骤，先刷新收盘观察再生成实时观察。未命中实时窗口时，才运行完整日流程中符合 `run_window_bj` 的步骤；GitHub 流程不含富途夜盘，Service 流程额外包含富途夜盘。
- `tools/configs/fund_universe_configs.py`：维护海外/全球基金池；新增基金代码优先改这里，基金代码请写 6 位字符串。
- `tools/configs/fund_proxy_configs.py`：维护代理型基金和海外有效披露持仓增强系数。
- `tools/configs/residual_benchmark_configs.py`：维护海外股票持仓型基金的补偿仓位基准；默认纳斯达克100，`007844` 当前使用 `XOP`。
- `tools/configs/market_benchmark_configs.py`：维护 safe 海外基金图底部基准表。这里决定展示哪些指数、ETF 或海外资产，以及使用新浪、AKShare、东方财富还是 Yahoo 路径。
- `tools/configs/premarket_configs.py`：维护盘前观察图配置。这里定义盘前观察项、实时 ticker、默认补偿仓位基准，以及按基金代码指定的盘前补偿基准；例如 `007844`、`006679`、`018852` 当前使用 `oil_gas_ep`。
- `tools/configs/intraday_configs.py`：维护盘中观察图配置和时间窗。
- `tools/configs/afterhours_configs.py`：维护盘后观察图配置和时间窗。
- `tools/configs/futu_night_configs.py`：维护富途夜盘观察图配置、Futu OpenD 连接参数、短缓存和报价校验阈值。
- `tools/configs/safe_image_style_configs.py`：维护 safe 公开图样式。标题文字、标题和表格间距、表头底色、正文底色、表格线、行高、列宽、涨跌颜色、底部备注、水印文字和透明度都优先在这里改。
- `tools/configs/fund_holding_change_style_configs.py`：维护前十大持仓变化图的竖屏发布尺寸、四边安全边距和 PNG 导出 DPI；不影响 safe 或实时观察图。
- `tools/configs/fund_region_allocation_configs.py`：维护晨星地区分布直连地址、超时、重试、地区层级及颜色；请求固定不继承环境代理。
- `tools/configs/fund_region_allocation_style_configs.py`：维护地区图的 1080 竖版尺寸、安全边距、每页基金数、卡片尺寸、字号、鱼师图像水印和 PNG DPI。
- `tools/configs/cache_policy_configs.py`：维护缓存有效期和容量上限。限购 3 天、A 股交易日历 7 天、VIX 日线 1000 条与 2 小时落后重试、基金池外手动 key 365 天保留、证券/指数/基金历史保留天数、RSI ETF 实时补点新鲜度等都集中在这里。
- `tools/configs/security_mappings.py`：维护美股 / 韩国证券映射。
- `tools/configs/rsi_configs.py`：维护 RSI 图标的列表。
- `tools/configs/a_share_valuation_configs.py`：维护走势图 MC/GDP 阈值、曲线配色与刷新频率，默认阈值为 `0.775 / 0.60 / 0.55`。
- `tools/cache_metadata.py`：维护缓存文件说明，并生成 `cache/README.md`。只有安全容器型 JSON 会内嵌 `_cache_info`，key-map JSON 和 CSV 不改变结构。
- `tools/paths.py`：集中维护常用缓存和输出图片路径。

旧导入路径会尽量保留兼容，例如 `tools/fund_universe.py` 仍可导入基金池，但真实配置已移动到 `tools/configs/fund_universe_configs.py`。

### 走势图两行布局与 MC/GDP

统一分析图只显示 Price + RSI/50D 两行；关闭 `show_rsi_panel` 时仅显示 Price。成交量子图已移除，但行情 `volume`、成交量历史、`volume_ratio_20` 和邮件成交量摘要仍保留。日周月 RSI、日周 BOLL、信号标记及 VIX/50D 边缘色带保持原有口径。

仅 `159943` 和 `560220` 在 RSI 配置中开启 `show_mc_gdp`。两张图使用相同的沪深市场级 MC/GDP，仍分别使用 ETF 自身价格和深证成指/中证2000成分广度，展示窗口保持300日。

- 数据：AKShare `macro_china_stock_market_cap()` 的上海、深圳市价总值相加，不含北交所；`macro_china_gdp()` 的年内累计名义GDP转单季，再求连续四季度TTM。两者都是亿元，比例不额外缩放。
- 状态：`ratio >= 0.775` 为 OVER；`0.60 < ratio < 0.775` 为 NEUTRAL；`0.55 < ratio <= 0.60` 为 LOW；`ratio <= 0.55` 为 DEEP LOW。单图阈值 `None` 沿用全局。
- 视觉：Price 不再绘制 MC/GDP 背景或英文状态；第二行直接复用 `mc_gdp_aligned` 绘制真实 ratio 的右轴 step-post 阶梯线，OVER/LOW/DEEP LOW/NEUTRAL 分别使用砖红 `#C43C39` /绿色 `#2E8B57` /深青绿 `#005F63` /暖灰 `#7A746B`，提高状态之间的区分度。宽区间可在曲线附近标一次英文状态，NEUTRAL 不标；缺口断线，不插值。
- 布局：仍为两行，右轴与 RSI/50D 共用第二行位置；右轴范围包含实际值及配置阈值并留边距，无估值阈值横线。图例合并 R/50D/MC/GDP，保留盘中估算项；右侧第三行 `MC/GDP: x.xx · STATE` 与其它最新值统一左对齐并保留6pt右内边距，极小白色衬底避免曲线穿过读数。少于两个有效日期时仅显示N/A、不创建空右轴。
- 缓存：`cache/a_share_mc_gdp.json`，市值48小时、GDP96小时，失败24小时后重试；每轮共享刷新一次，网络子进程硬预算15秒。源失败用可信旧值并打印数据期/缓存年龄；无有效数据则N/A，不影响其它图层。
- 日期限制：当前两个接口没有真实发布日期或历史版本。初始化旧历史明确标记 `historical revised series`，仅作按统计期回顾的估值环境，不能用于声称严格无前视的回测。此基线固定；后续新增/修订按首次成功观测时间向后匹配，不倒填旧状态、不插值。
- 边界：模块导入不联网；只读校验 `strategy/gdp.py` 的参考CSV，不改参考策略的缓存与数据口径。该指标不参与任何基金收益预估或benchmark业务。

### 独立十年 MC/GDP 出版图

`strategy/gdp.py` 使用单绘图区双纵轴：左轴为沪深总市值/中国名义GDP(TTM)，右轴为深证成指真实点位。图中不再把指数除以10000；月度复核CSV中的原缩放列仍保留。绘图接口 `plot_chart(ratio, index_df)` 只读输入，不抓取数据、不改缓存，也不重算GDP或修订历史口径。

独立图的阈值保留 `0.765 / 0.60 / 0.55`，在脚本顶部维护：高估≥0.765为砖红，中性为灰色，0.55<比例≤0.60为绿色，比例≤0.55为深绿色。月度比例使用step-post阶梯线，跳变使用新状态颜色，缺值断线；三个非中性区间配有很淡的横向底色与中文名称。深证成指按已经观测到的月度MC/GDP向后映射相同状态，采用较浅的红/灰/绿/深绿色细虚线，与MC/GDP深色实线区分。缺失估值使用浅灰，指数不使用未来月份；MC/GDP曲线不延长到指数最新交易日。图例及底部最新数据标明两种指标各自的日期；历史保留 `historical revised series` 说明，不是严格无前视回测信号。

默认纸面尺寸7.2×4.2英寸，局部设置微软雅黑/Arial，不改变其它图的样式。**仅输出PNG**：`output/a_share_market_cap_gdp_10y.png`，600 DPI，4320×2520像素，不再生成PDF/SVG。数据获取入口、缓存文件及月度复核CSV保持原样，基金业务无变化。

小电脑Service在北京时间11:30–23:50（含23:50整分钟）的首次有效触发中执行 `strategy/gdp.py --no-show`，成功更新指定PNG后当天跳过；失败或没有新PNG时，后续触发可重试。`tools/service_daily_step.py` 使用本机进程锁及 `cache/service_daily_steps/` 成功日期防止重复运行；此目录不参与Git同步。已启动的任务允许完成。主机/GitHub不自动运行该图；这沿用Service触发机制，不新增独立定时任务。

## 实时观察图

`safe_fund.py` 是收盘观察图入口，只读正式缓存生成 `output/safe_haiwai_fund.png`；平日自动总入口只在 06:00-13:40 运行它。早间盘后/富途夜盘窗口与收盘窗口重叠时，会先运行 `main.py --skip-rsi`、`fund_holding_change.py --auto`、`fund_region_allocation.py --auto` 和 `safe_fund.py`，再运行对应实时观察；平日晚间盘前/盘中窗口不会输出收盘观察图。

小电脑 Service 的**A 股节假日例外**：交易日历确认连续休市区间含工作日休市时（普通周末不算），无论当前窗口都运行 RSI、正式估算、收盘观察、节假日累计图，并保留命中的实时观察。正式估算使用中美港韩当天开市市场均完整收盘后的同一估值日；某市场休市不等于行情失败。累计图只统计假前最后一个 A 股交易日之后的有效估值日，每次运行均更新并进入邮件；尚无完整交易日时显示“暂无可累计的完整交易日”。节后首个 A 股交易日另行保留 T+2 补更新图，同时补发截至假期末的独立累计图。日历过期且刷新失败时不猜测假期，错误会进入邮件摘要。此例外只扩展 Service 流程，不扩大 GitHub 的运行窗口。

`premarket_fund.py`、`intraday_fund.py`、`afterhours_fund.py`、`futu_night_fund.py` 都是独立观察入口；它们会读持仓、限购和 15 分钟实时短缓存，但不会写 `cache/fund_estimate_return_cache.json`，也不会覆盖正式每日图 `output/safe_haiwai_fund.png`。

| 入口 | 默认北京时间窗口 | 输出图片 | 排查报告 | 日期口径 |
| --- | --- | --- | --- | --- |
| `afterhours_fund.py` | 08:00-11:29 | `output/safe_haiwai_afterhours.png` | `output/afterhours_failed_holdings_latest.txt` | 主标题使用下一美股估值日，报告保留 `afterhours_quote_date` |
| `futu_night_fund.py` | 11:30-16:30 | `output/safe_haiwai_night.png` | `output/night_failed_holdings_latest.txt` | 使用富途夜盘目标估值日 |
| `premarket_fund.py` | 17:30-21:00 | `output/safe_haiwai_premarket.png` | `output/premarket_failed_holdings_latest.txt` | 使用目标美股交易日 |
| `intraday_fund.py` | 22:40-次日 01:30 | `output/safe_haiwai_intraday.png` | `output/intraday_failed_holdings_latest.txt` | 使用目标美股交易日 |

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
  - `vix_level`：VIX 恐慌指数点位。收盘观察展示实时优先；正式缓存仍用 CBOE/FRED 最新完整收盘。
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
| VIX恐慌指数 | `vix_level` | `VIX` | 收盘观察图优先复用实时观察 VIX 源；实时失败后回退 CBOE/FRED 最新完整收盘；显示点位，不带 `%` |

注意：配置不会把所有失败基准自动兜到 Yahoo。只有 `kind="yahoo"` 的项目，或者代码里明确写了 Yahoo fallback 的证券路径，才会访问 Yahoo。VIX 是例外的点位观察项：收盘观察图会优先复用盘前、盘中、盘后、夜盘的实时 VIX 源（Yahoo Chart 1m/5m、yfinance），全部失败后再回退 CBOE/FRED 最新完整收盘。它展示的是点位，不是涨跌幅。

基准记录会写入 `cache/fund_estimate_return_cache.json` 的 `benchmark_records`。收益率型基准写 `return_pct`；VIX 点位型指标写 `value_type="level"`、`value/display_value`，并保持 `return_pct=null`。如果某个基准失败，只影响该基准行，不会中断主流程，也不会影响基金主表生成。

NDX 基准与走势图共用 `cache/dot_NDX_index_daily.csv`。基准入口虽然默认 `days=15`，NDX 实际请求长度使用 `tools/rsi_data.py` 的 `NDX_MIN_INDEX_HISTORY_ROWS`；读取足够历史后，仍只用目标日和前一真实交易日的两个收盘价计算单日收益，严格排除未来日期。图表的 220 行预热要求保持不变。已有历史锚点可直接读完整 CSV，不为旧日期重复下载行情。

正常基准加载会用本地指数 CSV、完整的证券锚点缓存自愈最近 **14 个自然日**的 `us_index` 缺口（`BENCHMARK_REPAIR_LOOKBACK_DAYS`），不增加网络请求。仅处理美国已完整收盘的交易日，不填周末、不替换有效正式记录、不裁剪或重算基金记录。`NDX/^NDX/.NDX` 和 `SPX/GSPC/^GSPC/.INX` 在读取、修复和累计时归一，同日别名只计一次；原始旧记录保留。`safe_holidays.py` 仍然只读缓存，累计仍按日收益复利计算。

手动补指定基准区间（默认只读本地行情；确需刷新时可显式传 `allow_network=True`，每个指数最多刷新一次）：

```powershell
& F:\anaconda\envs\py310\python.exe -c "from tools.fund_cache_maintenance import repair_missing_us_index_benchmark_records as repair; repair('2026-10-01', '2026-10-06', symbols=['.NDX'])"
```

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

## 持仓变化图竖屏样式

`fund_holding_change.py` 的前十大持仓变化图使用 Pillow 固定像素绘制，不读取上述 safe 图配置。短视频发布相关样式集中在 `tools/configs/fund_holding_change_style_configs.py`：

- `canvas_width_px`：导出图片的实际宽度；默认 `1080`，适合手机竖屏和平台二次压缩。
- `top_margin_px`：顶部安全区；默认 `120`，标题从该安全区之后开始绘制，用于避开 iPhone 灵动岛和抖音顶部控件。
- `bottom_margin_px`、`left_margin_px`、`right_margin_px`：其余三边留白，均可独立调整，单位为像素。
- `export_dpi`：PNG 的 DPI 元数据；实际清晰度主要由 `canvas_width_px` 决定，PNG 本身无损保存。

布局的字号、表格行高、摘要卡片和风险提示会随 `canvas_width_px` 等比例缩放；调整四边边距不会改变图片内容或自动生成逻辑。

地区分布图的独立样式在 `tools/configs/fund_region_allocation_style_configs.py`：支持 `1080px` 宽、顶部 `120px` 安全区、四边独立边距和 `300 DPI`；每页基金数调 `funds_per_page`，卡片高度/间距调 `card_height_px`、`card_gap_px`、`card_padding_px`，字号调 `font_sizes`，鱼师图像水印调 `logo_width_ratio`、`logo_opacity`。地区顺序、展示名称和固定配色维护在 `tools/configs/fund_region_allocation_configs.py`。

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
手机端触发小电脑运行的 workflow 文件：`.github/workflows/trigger-service-command.yml`。

触发方式：

- 手动触发：`workflow_dispatch`
- 定时触发：
  - UTC `10 21 * * *`，北京时间次日 05:10
- `Trigger Service Command` 仅手动触发，不设置定时任务；它只向 Gitee 写入 `service_command.json` 指令，不运行 `git_main.py`。该 workflow 会在日志和 Step Summary 里打印触发状态，并对 Gitee push 做多轮重试；Git push 全部失败时，会用 Gitee API 兜底更新指令文件。

运行环境：

- `ubuntu-24.04`
- Python 3.10
- 安装 `requirements.txt`
- 安装中文字体包，保证图片中的中文正常显示

错误处理：总入口会把子脚本失败记录到邮件正文，并继续运行后续步骤；邮件成功发出后，即使本轮有子脚本失败，Actions / 小电脑服务端也会按退出码 0 结束。`--no-send` 预演仍保留非 0 退出码，方便本地调试。

需要在 GitHub 仓库 Settings -> Secrets and variables -> Actions 中配置：

- `QQ_EMAIL_ACCOUNT`
- `QQ_EMAIL_AUTH_CODE`
- `QQ_EMAIL_RECEIVER` 可选
- `GITEE_PRIVATE_CODE`：Gitee 私人令牌，仅 `Trigger Service Command` 使用，必须配置为 Secret。

Actions 运行 `python git_main.py`，使用 GitHub 流程：RSI / 市场分析始终由 `stock_analysis.py` 先生成；命中盘前/盘中窗口时只额外运行对应实时观察；命中盘后且仍在 06:00-13:40 收盘窗口时，会先刷新并生成收盘观察图，再生成盘后观察图；未命中实时窗口时才运行完整日流程，其中 `main.py` 在总入口里使用 `--skip-rsi` 避免重复生成 RSI；不运行富途夜盘，也不自动生成 `first_pic.py`。

Actions 运行后如 `cache/` 运行缓存发生变化，会自动提交回仓库，提交信息为：

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
- `output/fund_holding_change/latest/1_012922.png`（自动检测到披露变化时按本轮批次编号生成，示例为第 1 张）
- `output/fund_holding_change/manual/012922.png`（手动指定基金时生成，不计入自动披露批次编号）
- `output/fund_region_allocation/latest/1_海外基金地区分布.png`（晨星地区数据变化时更新对应稳定页码）
- `output/fund_region_allocation/manual/012922_基金地区分布.png`（手动指定基金的单页图）
- `output/safe_haiwai_afterhours.png`（盘后观察图，手动运行 `afterhours_fund.py` 生成）
- `output/safe_haiwai_night.png`（富途夜盘观察图，手动运行 `futu_night_fund.py` 生成）
- `output/safe_haiwai_premarket.png`（盘前观察图，手动运行 `premarket_fund.py` 生成）
- `output/safe_haiwai_intraday.png`（盘中观察图，手动运行 `intraday_fund.py` 生成）
- `output/safe_haiwai_fund.png`（收盘观察图，自动总入口仅 06:00-13:40 生成）
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
- `vix_index_daily.csv` 是 VIX 风险状态带的独立日线缓存：按最近完整美股交易日判断是否命中；缓存落后时两小时最多联网重试一次，失败继续使用有效旧日线。主程序与策略试验各自保留最近 1000 条交易日，纳指策略图仍只展示最新 220 个交易日。
- 实时观察短缓存 TTL 保持 15 分钟：盘前、盘中、盘后分别使用独立 quote cache，富途夜盘使用 `futu_night_return_cache.json`；旧 `night_quote_cache.json` 是 legacy 缓存，不再由当前代码写入。
- `output/failed_holdings_latest.txt` 每轮海外基金估算后覆盖写入，包含运行汇总、行情请求统计、唯一证券汇总和失败/未完成持仓明细。它是本地排查文件，不进入邮件正文。
- 行情请求统计只保存在当前 Python 进程内，不写 JSON；用于控制台摘要和 `failed_holdings_latest.txt`。
- 指数行情和基金估算历史保留 300 天；VIX 日线固定最多 1000 条；证券日缓存、小时桶缓存、限购缓存和 A 股交易日历按各自策略裁剪或覆盖写入。基金 key 型持仓、限购和地区分布缓存会保留当前基金池及 365 天内手动基金记录，不会无限制追加。
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
- Actions 自动回推缓存后，如果本地运行提示 JSON 解析失败，先看是否处于 merge 冲突。运行缓存冲突优先用 `sync_repos.py --resolve-cache-conflicts` 自动合并；若仍有 JSON 解析错误，再按报错行号修复破损 JSON。不要给 `security_return_cache.json` 这类 key-map JSON 手工插入注释字段。
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
$files = @('.\git_main.py','.\service_main.py','.\service_runner.py','.\service_gui.py','.\service_command_watcher.py','.\sync_repos.py','.\github_gitee_sync.py','.\check_project.py','.\main.py','.\premarket_fund.py','.\intraday_fund.py','.\afterhours_fund.py','.\futu_night_fund.py','.\fund_estimate_breakdown.py','.\safe_fund.py','.\safe_holidays.py','.\holidays.py','.\sum_holidays.py','.\stock_analysis.py','.\kepu\first_pic.py','.\kepu\kepu_sum_holidays.py','.\kepu\kepu_xiane.py') + (Get-ChildItem .\tools -File -Filter *.py | ForEach-Object { $_.FullName }) + (Get-ChildItem .\tools\configs -File -Filter *.py | ForEach-Object { $_.FullName }); & F:\anaconda\envs\py310\python.exe -m py_compile @files
```

主机电脑服务端/同步脚本编译检查：

```powershell
& F:\anaconda\envs\py310\python.exe -m py_compile .\service_main.py .\service_runner.py .\service_gui.py .\service_command_watcher.py .\sync_repos.py .\github_gitee_sync.py
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

## 道琼斯与50DMA市场广度（2026-09-30）

新增 `output/dow_jones_analysis.png`。纳斯达克、道琼斯、红利低波512890、中证2000ETF560220、深证成指ETF159943的底部RSI面板叠加紫色虚线 `% Above 50DMA`。不新增上证图。原ETF价格不替换为指数价格。

```powershell
# 大规模首次建库，1800秒后保存已有进度退出；再次运行自动续跑。
& F:\anaconda\envs\py310\python.exe .\market_breadth.py --bootstrap --budget 1800
# 指定市场；小电脑把解释器改为 D:\anaconda\envs\py310\python.exe
& F:\anaconda\envs\py310\python.exe .\market_breadth.py --bootstrap --market nasdaq100 dow dividend shenzhen csi2000 --budget 1800
& F:\anaconda\envs\py310\python.exe .\market_breadth.py --bootstrap --market nasdaq100 --budget 1800
# 只读状态 / 日常增量 / 修复缺口
& F:\anaconda\envs\py310\python.exe .\market_breadth.py --status
& F:\anaconda\envs\py310\python.exe .\market_breadth.py --update
& F:\anaconda\envs\py310\python.exe .\market_breadth.py --repair --market dow
```

`tools/configs/market_breadth_configs.py`维护开关、95%有效覆盖率、30分钟快照复用（可调15–60）和300秒日常总预算。价格图中，50D达到上限及以上显示顶部紫带、达到下限及以下显示底部黄带；VIX深青带在顶部、红带在底部，同侧色带约隔2像素。全局上下限在该配置的 `BREADTH_BAND_HIGH_THRESHOLD` / `BREADTH_BAND_LOW_THRESHOLD` 修改；如需某张图单独设置，可在 `tools/configs/rsi_configs.py` 对应标的的 `breadth_band_high_threshold` / `breadth_band_low_threshold` 填数字，留 `None` 则沿用全局值。够宽的色段在右端带内标“V”或“50D”，短段只显示颜色。底部图例横向紧凑显示 `R`、`50D`，右侧两行最新值左对齐。日常流程不从零下载数千只股票；未建库的市场提示数据不足，RSI照常出图。`--no-futu`可关闭富途兜底。

A股收盘日线尚未到齐时，该交易日15:15之后（包括次日清晨）可用富途15:00正式时点快照补该日价格，按证券昨收与现价比例接续原复权基准；随后继续核验国内正式日线。北交所股票不送富途快照，由腾讯等国内日线补齐。只有至少50日有效价格且成分覆盖率达到95%才显示新值；未达到时，图右侧显示最近5个交易日内可信收盘值及日期，过期后提示数据不足，旧盘中点不会充当当前值。诊断文件记录快照来源、覆盖率和失败原因。

纳斯达克图现用 `.NDX` 指数价格、RSI、BOLL 与 Nasdaq-100 成分的 50D 广度，图片路径仍为 `output/nasdaq_analysis.png`。`nasdaq100` 使用独立的成员、结果、快照和 `$NDXA50R` 对照缓存，共同证券的真实价格分片继续复用；旧 `nasdaq` Composite 缓存不改名、不删除。首次切换以 Nasdaq 官方 JSON 的 101 只证券、官方总览 101 只和真实价格 101/101 覆盖完成核验。以后约每15天复核成分：富途完整指数名单可优先使用；富途 NDX 不可用时使用 Nasdaq 官方 JSON，官网不可达但富途名单完整且与 101 只参考数量相差不超过5只时可继续使用富途。当前本机富途 SDK 对 `US.NDX` 和 `US.DJI` 的板块直查返回未知股票，故 NDX 使用 Nasdaq JSON。道指可用富途或 DIA 每日持仓候选，S&P DJI 页面可访问完整表时交叉核验。A股优先尝试富途对应指数，失败时读取原官方成分文件；本机已验证深证成指 500 只和中证2000 2000 只。收盘比较50根完整日线，盘中用前49日加最新价；覆盖率低于95%不发布新数值。

NDX 图在 2026-09-29 正式成分版本之前的 50D 曲线，用首次核验的 101 只名单与真实历史价格回算；图内标注历史口径，缺少完整 50 日窗口或覆盖率不足 95% 的交易日留空。回算仅在绘图读取时生成，不写入正式结果，后来调样也不改变其基线。五市场绘图支持按交易日优先使用经核验且获准自动访问的同口径现成广度，缺口回退成分自算；现阶段没有登记此类直接源。`tools/configs/market_breadth_configs.py` 的 `BREADTH_DIRECT_INDICATOR_SOURCES` 只有确认许可、成分范围和指标定义后才能登记 `source_id`、`universe`、`access_approved=True`；适配器将带 `date`、`percent`、`kind`、`source`（盘中另带 `observed_at`）的记录保存到 `cache/market_breadth/direct_indicators/<市场>.json`。StockCharts 自动对照请求已停用，旧 `$NDXA50R` / `$NAA50R` 缓存只读留存，不参与绘图优先级。

成分缓存记录发现、验证、生效时间、来源证据及调样事件。已知未来生效日的调入证券先下载真实日线，生效前不进入分母，调出证券价格缓存保留。初次启用逐日版本前的旧历史保留并标记为“当前成分回算”；启用后只按当日生效名单计算，不以新名单重写既有正式收盘结果。官方文件自带唯一生效日期，或正式公告中的调入、调出代码与新旧名单完全吻合时，可自动定时切换；深证成指会尝试核对深交所公开指数动态。Nasdaq IR 公告归档会尽力核验未来调样，但本机访问可能超时；中证公告检索尚未自动核验，缺少可信日期时保持待确认，可在核实官方公告后运行 `--activate-pending --market <市场> --effective-date YYYY-MM-DD --evidence-url <官方HTTPS公告>`。异常缩减、重复代码或数量不对的名单不启用；`--repair` 才允许用修复后的有效价格更正已发布结果。

美股收盘优先尝试富途常规时段收盘快照，并按实际交易所日历处理提前收市；快照价格接续原复权基准、标记临时来源，正式日线到达后核对替换。美股建库及增量先试 Yahoo，遇到限流或连接失败依次试新浪美股前复权日线、东方财富美股直连日线；换源时重取整个价格窗口，避免混合复权基准。富途历史回退处理全部未完成证券，不再限制前10只；首次查询额度后按新证券维护本轮消耗，结束复核，继续保留10只余量；已占额度证券可复用，分批不能绕过七天额度。快照分批不增加历史额度。国内腾讯、东方财富、新浪及北交所日线链保留。外部网络受限时保留可信旧值与日期，并在诊断中列出缺口，不用推造价格。

两台电脑同步 `cache/market_breadth/`，日线分片按证券共用；本机锁等放在忽略目录 `cache/market_breadth_local/`。富途默认本机11111端口，支持环境变量 `AHNS_BREADTH_FUTU_HOST/PORT`，不提交本机配置。快照每批200只，至少间隔1秒；历史补齐保留10个未用额度。不要通过分批声称绕过7天股票数量额度。

诊断：`output/market_breadth_diagnostics.json`。`check_project.py`只读检查容量与覆盖率。GitHub已暂停workflow保持原状；`main.py`不修改。回退广度可将 `BREADTH_ENABLED`设为False。

此前五市场初版验收记录见 `docs/superpowers/plans/2026-09-27-dow-50dma-breadth.md`；本次成分时间口径与数据链重构的验证以本轮测试及 `output/market_breadth_diagnostics.json` 为准。开盘现场实测仍需在可用交易时段观察。

邮件发送规则：16张及以下正文内嵌并附附件，超过16张仅发附件；主流程SMTP响应超时450秒，异常通知180秒。假期图备注不重复截止日期；有数值的收盘估算均参与累计，包括部分持仓行情缺失或过期的记录，盘中记录及全市场休市零值不参与。

### ETF 的 EBS 状态带、Service 股债利差图与邮件短名

仅159943、560220启用 `show_ebs_state_band`，整轮 `stock_analysis` 共享一次全A数据加载。`tools/equity_bond_spread.py` 复用 `strategy/gu_zhai_xi.py` 的正式计算：`spread=100/pe_ttm-cn10y`，500交易日均值及总体标准差（ddof=0），通道为±1.95σ。参数集中于 `tools/configs/equity_bond_spread_configs.py`。先读长历史完成500日预热，再对齐ETF展示日期；盘中没有同日正式状态时不延用昨日状态。

EBS只在第一行Price面板显示：严格高于上轨为底部绿色 `#2F9E44`，严格低于下轨为顶部红色 `#D9534F`，NORMAL不画。连续状态合并，短段不标字，够宽的段完全复用带内 `EBS` 标签。第二行和右侧最新值始终保持R/50D/MC-GDP，不接入EBS曲线、图例或文字；EBS失败只输出诊断并跳过色带。

六张统一技术图（Nasdaq-100、道琼斯、DAX ETF 159561、红利低波ETF 512890、深证成指ETF 159943、中证2000ETF 560220）均通过 `show_ene_state_band` 启用周线ENE，独立RSI入口同步开启，仅进入Price；公共函数仍可显式关闭。参数集中于 `tools/configs/ene_configs.py`：N/M1/M2=10/11/9；周收盘的10周均值×1.11为上轨、×0.91为下轨。收盘≥上轨为顶部红色 `#E5675E`，收盘≤下轨为底部绿色 `#2FA36B`，NORMAL/预热不足/无效价格不画。带内 `E2.4` 表示高于上轨2.4%，`E1.8` 表示低于下轨1.8%，由位置与颜色区分方向，保留一位小数。短段标签可向左伸出色段但不超出绘图区；碰撞时保留较新周标签，所有色段仍保留。

色带统一使用16px带高、2px间距、5px边距；从边缘向内，不含EBS的四图顶部ENE/50D/V、底部V/50D/ENE，159943和560220顶部ENE/EBS/50D/V、底部V/50D/EBS/ENE。显式关闭ENE时保留原两层/三层位置；EBS和MC/GDP仍只在原有两ETF启用。ENE按各图自身完整已取得的行情先预热再截取展示期，DAX使用159561 ETF价格，不新增行情请求或磁盘缓存。美国指数使用纽约时间，四只境内ETF使用北京时间；本地交易日历确认该周最后交易时段收盘，覆盖夏令时及节假日短周，无法核实时保守标为临时。当前周只使用本周已有最新日线/原流程接纳的实时价格，形成一根临时周K，不造整周休市行情。历史周内状态使用最终周收盘作回顾展示，不是严格无前视日频信号。周内精确映射，只有连续相邻周的状态和偏离文字完全相同时才合并。ENE不出现在第二行、图例、右轴或最新值中，数据失败仅跳过色带。

小电脑 `SERVICE_WORKFLOW_STEPS` 额外运行 `strategy/gu_zhai_xi.py --no-show`，北京时间每天11:30至23:50（含23:50整分钟）内每次触发均运行，包括实时、假期及节后流程。`independent_window=True` 让该步骤独立参与窗口选择，并在实际启动前再次检查时间；已启动的作业允许完成。这里只接入现有Service触发流程，不新增定时任务；主机/GitHub工作流不自动运行独立股债利差图。脚本出图不弹窗，本轮更新图片进入Service邮件。

股债利差图默认采用上下对齐的两个区域：上方为三指数共同起点的相对走势（深成指等价点位），下方为真实股债利差（百分点）及500D、±1.95σ通道。上方以实线/长虚线/短虚线区分深证成指/中证2000/上证指数；共用利差高于上轨的绿色、低于下轨的红色状态，常态使用各指数独立中性色。通道淡灰填充，均值细虚线，利差琥珀色；不再把利差缩放成指数点位。三个显示开关、计算公式与缓存保持不变。仍输出原 `equity_bond_spread_*_video.png`，7.2×4.9英寸、600 DPI，仅PNG；`--dual-axis`兼容入口保留。

邮件图片统一使用短中文名称，原始output路径不变。`tools/configs/email_image_name_configs.py` 维护名称，例如“深成指ETF.png”“中证2000ETF.png”“股债利差.png”“海外基金盘前.png”；动态图采用“持仓_012922.png”“限购_007844.png”“地区分布_3.png”。未知图片按简短步骤名和序号命名，重名追加序号；内嵌图加短标题、附件使用同名，发图顺序保持原样。`send_email/build_message` 新增可选 `image_names` 参数，与 `image_paths` 一一对应；未传时保持原文件名。

### 美股50D增量修复诊断

默认总预算300秒（`--budget`可覆盖），按缺口证券数分配市场预算，普通美股源最多使用网络份额的60%，余量给富途。Yahoo/东方财富/富途同源仅请求缺口与重叠校验区间，换源或复权变化须获取完整窗口；三个普通美股源均用可终止进程约束总请求时间（HTTP连接3秒/读取最多8秒），以免慢速响应拖住回退；新浪接口只能返回全量，沿用原AKShare解析并用独立进程限制15秒，不能称为网络增量下载。

队列从真实缓存重建，包含尾部、最近50日窗口内部缺口和待核验临时价格；熔断、旧响应、未提交证券均保留。逐证券原子保存；本机 `cache/market_breadth_local/<市场>_attempts.json` 只保存尝试顺序，不参与Git同步。`output/market_breadth_diagnostics.json` 每批原子保存剩余任务、来源成功数量、富途连接/额度及原始错误摘要。

只有预期完整交易日存在有效、正式且覆盖率至少95%的收盘结果才报告 `complete`；旧日期报告 `stale`，CLI退出非零，RSI仍继续使用注明真实日期的可信旧值。临时快照不能满足正式完成判定。

走势图包装器的300秒总上限包含启动及退出：CLI子流程预留5秒余量；独立广度CLI默认仍为300秒。已初始化美股市场的空分片全部进入预算队列，首次未建库市场仍不自动大规模bootstrap。长尾从旧末日续接，最多覆盖受管400日窗口，换源响应不得倒退缓存日期。

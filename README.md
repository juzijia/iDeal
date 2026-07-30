# iDeal

为青龙面板设计的 iOS 优惠精选工具。它从多个渠道发现候选，使用 Apple 官方
接口核价，再让 AI 严格筛选，只推送当前真正值得看的优惠。

## 特点

- 一个入口 `ideal.py`，一个青龙任务。
- 默认监控中国、土耳其、美国区。
- Apple 官方批量核价，不会把 300 个 App 拆成 300 次请求。
- AI 入选数量不设上限：优秀的全部推送，不优秀的一款也不推。
- 百炼 Qwen → DeepSeek → Google Gemini 自动回退。
- AI 结果默认缓存 12 小时；价格或候选信息变化后自动重新评估。
- 通知接口返回成功才记录去重，接口失败会在下次任务自动重试。
- 数据源探针按成功报告时间判断，超过 8 小时才运行。
- 所有运行数据和用户配置保存在 `/ql/data/db/ideal`，订阅更新不会覆盖。
- 仅使用 Python 标准库，无需安装第三方依赖。

## 青龙订阅使用教程

先复制下面**整条命令**：

```bash
ql repo "https://github.com/juzijia/iDeal.git" "^ideal\.py$" "" "ideal_core|config" "main" "py json" "" "true" "true"
```

推荐直接使用青龙面板：

1. 打开 **订阅管理**，点击 **创建订阅**。
2. 将上面的整条命令粘贴到 **链接** 输入框。青龙会自动识别并填充仓库、
   分支、白名单、依赖文件和文件后缀。
3. 手动补充 **名称：`iDeal`**、**定时类型：`crontab`**、
   **定时规则：`0 3 * * *`**。
4. 确认 **自动添加任务** 和 **自动删除任务** 已开启，点击 **确定**。
5. 手动运行一次刚创建的 `iDeal` 订阅。拉取完成后，定时任务会自动出现。

这条命令也可以直接在青龙终端执行，作为面板订阅的备用方式。

自动填充后可按下表复核：

| 青龙项目 | 应显示的内容 |
|---|---|
| 名称 | `iDeal` |
| 类型 | `公开仓库` |
| 链接 | `https://github.com/juzijia/iDeal.git` |
| 定时类型 | `crontab` |
| 定时规则（订阅更新） | `0 3 * * *` |
| 分支 | `main` |
| 白名单 | `^ideal\.py$` |
| 黑名单 | 留空 |
| 依赖文件 | `ideal_core\|config` |
| 文件后缀 | `py json` |

> **必须包含 `json`。** 青龙默认只拉取脚本后缀；缺少 `json` 会导致
> `settings.json`、`sources.json` 和 `watchlist.json` 没有下载。

`更新定时` 只负责每天检查一次 GitHub 更新，不是 iDeal 的运行时间。iDeal 入口文件已经内置任务定时：

```cron
15 7-22/3 * * *
```

每天在 `07:15、10:15、13:15、16:15、19:15、22:15` 运行。请把青龙时区设置为
`Asia/Shanghai`。

仓库链接本身不能携带青龙的订阅名称或更新定时。任务运行定时由
`ideal.py` 顶部的 `name:` 和 `cron:` 元数据提供。

从旧版更新后，如果现有任务名称显示为 `str,`、定时显示为随机时间，订阅更新不会自动
改写已存在的任务。请在 **定时任务** 中直接编辑：

```text
名称：iDeal
命令：task juzijia_iDeal_main/ideal.py
定时：15 7-22/3 * * *
```

如果订阅完全没有自动创建任务，也可以使用以上内容手动建立。

请使用青龙生成的 `task .../ideal.py` 命令，不要改成裸
`python3 /ql/data/scripts/.../ideal.py`，以免绕过青龙任务环境的加载过程。
开启“自动添加任务”后不需要手动填写启动命令；如果手动创建任务，则不能省略 `task`。
它是青龙的任务执行入口，会选择 `python3` 运行 `.py`、加载青龙环境并管理日志和任务状态。

## 必需环境变量

至少配置一条 AI 路线：

| 变量 | 说明 |
|---|---|
| `QWEN_API_KEY` 或 `DASHSCOPE_API_KEY` | 百炼 Qwen，默认首选 |
| `DEEPSEEK_API_KEY` | DeepSeek，默认第二顺位 |
| `GEMINI_API_KEY` | Google Gemini，默认第三顺位 |

可选变量：

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `IDEAL_REPEAT_PUSH` | `0` | `0`：同一优惠成功推送一次；`1`：每轮推送所有仍符合条件的精选优惠 |
| `IDEAL_MONITOR_REGIONS` | `cn,tr,us` | 监控区服，英文逗号分隔 |
| `IDEAL_PROBE_MAX_AGE_HOURS` | `8` | 探针最大间隔时长，单位：小时 |
| `IDEAL_AI_PROVIDER` | `auto` | `auto`、`qwen`、`deepseek`、`gemini` 或 `custom` |
| `IDEAL_AI_PROVIDER_ORDER` | `qwen,deepseek,gemini,custom` | 自动模式调用顺序 |

高级目录变量通常不需要设置：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `IDEAL_DATA_DIR` | `/ql/data/db/ideal` | 持久化保存数据库、价格历史、AI 缓存、通知去重记录和探针报告 |
| `IDEAL_CONFIG_DIR` | `/ql/data/db/ideal/config` | 保存实际生效的 `settings.json`、`sources.json`、`watchlist.json`，避免订阅更新覆盖用户修改 |

模型名和接口地址也可分别通过 `QWEN_MODEL`、`QWEN_BASE_URL`、
`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`、`GEMINI_MODEL`、
`GEMINI_BASE_URL` 覆盖。

自定义 OpenAI 兼容接口需要同时设置：

```text
IDEAL_AI_API_KEY
IDEAL_AI_MODEL
IDEAL_AI_BASE_URL
```

变量名称区分大小写。

## 默认运行流程

每次定时任务依次执行：

1. 抓取优惠来源。
2. 按区服批量调用 Apple Lookup 核价。
3. 规则过滤、价格状态判断与通知去重。
4. AI 对少量候选 App 做严格精选。
5. 发送美化通知。
6. 检查 `/ql/data/db/ideal/reports/source_probe.json` 的
   `generated_at`。
7. 报告缺失、损坏、没有任何来源结果、时间异常，或距上次成功探针达到 8 小时时运行探针。

探针失败时不会更新成功时间，下次定时任务会自动重试。

`source_probe.json` 和 `source_probe.csv` 每次成功探针都会**覆盖写入**，只保留最新一次
详细报告，不会持续追加。SQLite 数据库会保留来源健康历史，默认清理 90 天以前的记录；
青龙自身的任务日志保留时间由青龙设置控制。

## 重复推送

默认：

```text
IDEAL_REPEAT_PUSH=0
```

已经成功推送过的同一优惠不会重复提醒。通知渠道失败时不写入提醒记录，下次会
自动重试，不需要手动清缓存。

不介意重复提醒时设置：

```text
IDEAL_REPEAT_PUSH=1
```

每轮都会推送本轮仍然真实降价、仍符合规则且达到 AI 门槛的所有 App。恢复原价、
线索过期或不再达到 AI 门槛的 App 不会推送。该开关只影响全网优惠精选；
Watchlist 仍只提醒新的价格变化。

## 通知格式

通知使用普通文本、Emoji 和 Apple 官方精简链接，兼容青龙常见通知渠道：

```text
🎯 iDeal｜1 款优质优惠
🕒 07-30 10:15｜CN · TR · US
🔎 共 1 款，涉及 3 个区服

1️⃣ Parachute Backup Mobile
🏆 历史低价｜📉 降价｜🤖 AI 9/10｜可信度 B
💰 CN ¥68→¥58｜TR ₺499.99→₺399.99｜US $9.99→$7.99
⭐ 4.4（72）｜工具
💡 可将 iCloud 数据备份至 NAS 或本地，实用且无订阅
🔍 Apple 已核验｜来源：appstore-discounts
📲 CN App Store｜https://apps.apple.com/cn/app/id6749824842
```

不使用第三方短链，避免失效、跟踪和通知渠道屏蔽。优先生成 CN 区 Apple 官方
精简链接；该 App 不在 CN 区时依次使用 US、TR 中实际核验可用的区服。

日志出现 `bark 推送成功` 和 `青龙通知接口已接受`，表示青龙 `notify.py` 调用 Bark
服务端成功。iDeal 无法确认手机是否实际弹出通知；如果手机没有收到，请检查 Bark Key、
App 通知权限、专注模式和设备网络。

## Watchlist

首次运行后编辑：

```text
/ql/data/db/ideal/config/watchlist.json
```

每个 App 可配置区服、目标价、是否提醒任意降价及自定义标签。仓库中的
`config/watchlist.json` 只是首次运行模板；订阅更新不会覆盖运行目录中的文件。

## 手动命令

```bash
# 当前公开仓库 main 分支的订阅目录名

# 默认统一任务
task juzijia_iDeal_main/ideal.py

# 环境自检
task juzijia_iDeal_main/ideal.py -- self-check

# 只运行优惠精选
task juzijia_iDeal_main/ideal.py -- digest

# 只运行 Watchlist
task juzijia_iDeal_main/ideal.py -- watchlist

# 只运行数据源探针
task juzijia_iDeal_main/ideal.py -- probe --rounds 2

# 试运行：打印通知但不发送
task juzijia_iDeal_main/ideal.py -- digest --dry-run

# 统一任务并强制探针
task juzijia_iDeal_main/ideal.py -- --force-probe
```

## 目录

```text
iDeal/
├─ ideal.py
├─ ideal_core/
├─ config/
│  ├─ settings.json
│  ├─ sources.json
│  └─ watchlist.json
├─ README.md
├─ SOURCES.md
├─ LICENSE
└─ .gitignore
```

数据来源、归属和第三方说明见 [SOURCES.md](SOURCES.md)。

## 许可证

项目代码采用 [MIT License](LICENSE)。第三方数据和商标不包含在本项目的 MIT
授权范围内。

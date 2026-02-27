## 个人推送助理 v1 规格说明（spec）

本规格说明约束本次实现必须满足的行为与接口，后续开发应严格按照本 spec 实现，以保证未来可扩展性与可维护性。

---

### 1. 范围说明

- **实现语言**：Python 3.10+（具体版本可在 README 中说明）。
- **推送通道**：仅实现 PushPlus 通道（后续可扩展其它通道，但不在本 spec 范围内）。
- **目标能力**：
  - 支持多个「接收人」（每个接收人对应一套 PushPlus 配置）。
  - 支持配置多条调度计划（schedules），每条计划有独立 cron，下面可挂多个 job。
  - 每个 job = 接收人 + 插件 + 插件配置引用，允许不同接收人对同一插件有不同配置（从而得到不同内容）。
  - 支持在一天内不同时间执行不同插件，并发送到对应接收人。
  - 插件：`stocks.daily-brief`、`gold.daily-brief`。

---

### 2. 核心数据模型规范（`src/models.py`）

#### 2.1 PushMessage

用来在插件层与通道层之间传递消息的标准结构。

字段要求：

- `title: str`  
  - 消息标题，要求为非空字符串。
- `body: str`  
  - 消息正文，可以是纯文本或 Markdown 文本。
- `format: str`  
  - 取值限定：`"text"` 或 `"markdown"`。
  - v1 中建议 `stocks.daily-brief` 使用 `"markdown"`。
- `target_recipient: str | None`  
  - 目标接收人 id。
  - 插件返回时可以为 `None`，由 runner 在投递前统一设置为当前 job 的 `recipient_id`。
- 可选字段（可使用 `@dataclass` 默认值实现）：
  - `priority: str | None`（可选：`"low" | "normal" | "high"`，v1 可不使用）。
  - `tags: list[str] | None`（如 `["stock", "daily"]`，v1 可选）。

实现要求：

- 推荐使用 `@dataclass` 定义，字段类型在代码中须明确标注。

#### 2.2 PluginContext

插件执行时的上下文信息。

字段要求：

- `now: datetime`  
  - 执行时的当前时间（由 runner 传入）。
- `recipient_id: str`  
  - 当前 job 对应的接收人 id。
- `plugin_config: dict`  
  - 从配置文件中解析出的插件配置（该 job 专属），插件应只依赖此配置生成内容。
- `global_config: dict`  
  - 全局配置（如某些默认参数），v1 可以简化为空或仅少量字段。

实现要求：

- 同样推荐使用 `@dataclass` 或定义明确字段的类。

#### 2.3 插件协议：ContentPlugin

以抽象基类（ABC）或 typing.Protocol 的形式定义。

要求：

- 属性/特征：
  - `id: str`：插件的唯一标识，例如 `"stocks.daily-brief"`。
- 方法：
  - `run(ctx: PluginContext) -> list[PushMessage]`
    - 输入：`PluginContext`。
    - 输出：一组 `PushMessage`（可以为空列表），其中 `target_recipient` 可以暂为 `None`，由 runner 注入。

约束：

- 插件不应依赖运行环境的全局变量（如全局配置、环境变量），除非在 spec 中明确约定；主要依赖 `ctx.plugin_config`。
- 插件不得直接调用任何推送通道，应仅生成消息，由 runner/通道层负责发送。

---

### 3. 配置文件规范（`config/config.yaml`）

**注意**：仓库中只保存 `config.example.yaml`，真实 `config.yaml` 由使用者复制并添加到 `.gitignore` 中。

整体结构：

```yaml
recipients:        # 接收人列表，以接收人 id 为 key
  <recipient_id>:
    channel:
      type: <string>        # v1 固定为 "pushplus"
      token: <string>       # PushPlus token，可支持 ${ENV_VAR} 占位
      topic: <string?>      # 可选，用于指定 PushPlus topic

schedules:         # 调度计划列表
  - id: <string>           # schedule 唯一 id，如 "morning_8"
    cron: <string>         # 标准 cron 表达式（5 字段），使用 UTC 时间
    jobs:
      - recipient_id: <string>   # 对应 recipients 中的某个 id
        plugin_id: <string>      # 如 "stocks.daily-brief"
        config_ref: <string>     # 引用 plugin_configs 中的某个配置名
        # （可选扩展）config: { ... }  # 若不使用 config_ref，可内联配置

plugin_configs:    # 插件配置模板
  <config_name>:   # 如 "stocks-me", "stocks-alice"
    # 具体字段由插件定义，以下为 stocks.daily-brief 的约定示例
    symbols: [<string>, ...]     # 股票代码列表，如 ["600519.SH", "000858.SZ"]
    with_news: <bool>            # 是否拉取相关新闻
    news_per_symbol: <int>       # 每只股票最多新闻条数（>=0）
```

约束：

- `recipients` 中的所有 `recipient_id` 必须唯一。
- 每个 schedule 的 `id` 必须唯一。
- `jobs[*].recipient_id` 必须能在 `recipients` 中找到对应条目，否则在启动时应报错或在加载时校验失败。
- `jobs[*].plugin_id` 必须能在插件注册表中找到实现，否则在启动时应报错。
- `jobs[*].config_ref` 必须能在 `plugin_configs` 中找到对应配置；如使用内联 `config`，则可约定二者互斥（v1 只需支持 `config_ref` 即可）。

---

### 4. 插件接口与内容插件规格

#### 4.1 插件注册约定

- 在 `src/plugins/__init__.py` 中暴露一个插件注册表，例如：
  - `PLUGINS: dict[str, type[ContentPlugin]]`，或
  - 提供一个 `get_plugin(plugin_id: str) -> ContentPlugin` 工厂方法。
- `stocks.daily-brief` 插件的 `id` 必须固定为 `"stocks.daily-brief"`。
- `gold.daily-brief` 插件的 `id` 必须固定为 `"gold.daily-brief"`。

#### 4.2 `stocks.daily-brief` 配置约定

插件从 `ctx.plugin_config` 读取以下字段（以 dict 形式）：

- `symbols: list[str]`（必填）
  - 要关注的股票代码列表（具体格式由数据源决定，如 `"600519.SH"`）。
- `with_news: bool`（必填）
  - 是否同时抓取相关新闻。
- `news_per_symbol: int`（必填）
  - 每只股票最多呈现的新闻条数（0 表示不展示新闻，即使 `with_news=true`）。

插件如遇字段缺失或类型不符，可按以下策略之一处理（需在实现中统一）：

- 明确抛出异常并由 runner 记录日志；
- 或应用合理默认值（建议在 spec 中显式约定，例如：`with_news` 默认为 `false`，`news_per_symbol` 默认为 `3`）。

建议（可选）默认值：

- 若 `with_news` 未提供：视为 `false`。
- 若 `news_per_symbol` 未提供：视为 `3`。

#### 4.3 行情与新闻行为（高层约束）

本 spec 不强制具体数据源，但约束行为形态：

- 行情：
  - 对 `symbols` 中的每只股票，至少获取：
    - 昨收价、今开价、当前价、涨跌幅（百分比或小数，约定在实现中统一）。
  - 如某只股票数据获取失败，应在输出中标记为失败（例如在对应行中提示），而不是直接丢弃整条消息。

- 新闻（当 `with_news=true` 且 `news_per_symbol>0` 时）：
  - 以股票名称或简写为关键字，从单一新闻源拉取最近的若干条新闻。
  - 最终展示时，每只股票最多展示 `news_per_symbol` 条新闻（标题 + 链接，必要时可以简短摘要）。

#### 4.4 输出消息格式（建议）

插件返回的 `PushMessage` 列表中，v1 约定为 **单条消息**，其 `body` 为 Markdown，示例结构：

```markdown
# 今日股票简报（2026-02-24）

## 股票概览
- 600519.SH 贵州茅台：现价 1800.50（+2.31%），昨收 1760.00，今开 1775.00
- 000858.SZ 五粮液：现价 150.20（-1.05%），昨收 151.80，今开 152.00

## 新闻（如开启）
### 600519.SH 贵州茅台
1. 标题 A （来源 / 时间）- [链接](https://...)
2. 标题 B - [链接](https://...)
```

实现中可以不完全一致，但应：

- 结构清晰、可读性良好；
- 避免过长（例如对总新闻条数做上限控制）。

#### 4.5 `gold.daily-brief` 配置约定（`src/plugins/gold_daily.py`）

插件从 `ctx.plugin_config` 读取以下字段（dict）：

- `symbols: list[str]`（必填）
  - 要关注的金价代码列表；v1 内置支持：
    - `XAUUSD`：现货黄金（美元/盎司）
    - `XAUCNY` / `XAUUSD_CNY`：现货黄金（人民币/盎司）
- `symbol_names: dict[str, str]`（可选）
  - 自定义展示名称，优先级高于默认名称。
- `provider: dict`（可选，未配置时使用默认）
  - `type: str`：v1 固定支持 `metalpriceapi`。
  - `api_key_env: str`：金价 API key 对应的环境变量名（例如 `METALPRICE_API_KEY`）。
  - `endpoint: str`：金价接口地址，默认 `https://api.metalpriceapi.com/v1/latest`。
  - `base_currency: str`：默认 `XAU`。
- `display: dict`（可选）
  - `price_precision: int`：价格显示小数位，默认 `2`。

行为约束：

- 输出为单条 `PushMessage`，`format="html"`，由 PushPlus 使用 `html` 模板发送。
- 主体展示一个「品种 / 现价 / 涨跌 / 昨今」表格；如接口未返回昨收/今开，字段可显示 `--`。
- 单个 symbol 获取失败时，不中断整条消息；在表格下方以红字列出失败项和错误原因。

---

### 5. Runner 行为规范（`src/runner.py` + `src/cli.py`）

#### 5.1 CLI 入口（`src/cli.py`）

要求提供一个命令行入口，例如：

- 命令：`python -m src.cli run [--config <path>] [--schedule <id>]`

行为：

- 默认 `--config` 为 `config/config.yaml`。
- 如提供 `--schedule <id>`，则仅执行该 schedule；否则由 runner 根据当前时间匹配应执行的 schedule（见下文）。

#### 5.2 Runner 主流程

伪代码级别行为：

1. 加载配置文件（路径由 CLI 传入或采用默认）。
2. 校验配置（recipients、schedules、plugin_configs 的基本合法性）。
3. 决定本次要执行哪些 schedule：
   - 若 CLI 显式指定 `--schedule <id>`，则只执行该 id 的 schedule（不存在则报错）。
   - 否则：获取当前时间 `now`（UTC），遍历所有 schedule，用其 `cron` 表达式判断本次是否应执行（可按「当前时间与上次执行时间」或「精确匹配」策略实现，具体在代码中约定）。
4. 对于每个应执行的 schedule，遍历其所有 `jobs`：
   - 确认 `recipient_id` 在 `recipients` 中存在。
   - 确认 `plugin_id` 在插件注册表中存在。
   - 根据 `config_ref` 从 `plugin_configs` 中取出对应配置。
   - 构造 `PluginContext(now, recipient_id, plugin_config, global_config)`。
   - 实例化插件并调用 `run(ctx)`，得到一组 `PushMessage`。
   - 对每个 `PushMessage`：
     - 若 `target_recipient` 为空，则设置为该 job 的 `recipient_id`。
     - 从 `recipients[target_recipient].channel` 解析出 `type` 与 `channel_config`。
     - 根据 `type` 选择 `Channel` 实现（v1 仅 `"pushplus"`）。
     - 调用 `channel.send(msg, channel_config)`。

5. 对于任何一步中出现的异常，runner 至少需要记录错误日志（可以先简单打印），并尽量不影响其他 job 的执行（即单 job 失败不导致整个进程直接退出，除非配置错误导致无法继续）。

---

### 6. PushPlus 通道规范（`src/channel/pushplus.py`）

#### 6.1 基本配置

`channel_config` 字段：

- `type: "pushplus"`（固定字符串，用于路由到 PushPlusChannel）。
- `token: str`  
  - PushPlus 的用户 token，可直接写死或通过 `${ENV_VAR}` 占位，具体解析逻辑在实现中固定（例如 `${PUSHPLUS_TOKEN_ME}` → 从环境变量读取）。
- `topic: str | None`（可选）  
  - PushPlus topic 编码，用于群组等场景；为空则按 PushPlus 默认行为处理（如发送到主通道）。

#### 6.2 HTTP 协议（以官方文档为准，这里约定通用行为）

- 请求方法：`POST`
- URL：`https://www.pushplus.plus/send`
- 请求体：JSON（推荐）或表单，至少包含字段：
  - `token`: 对应 `channel_config.token`。
  - `title`: 使用 `PushMessage.title`。
  - `content`: 使用 `PushMessage.body`。
  - `template`: 根据 `PushMessage.format` 映射：
    - `"markdown"` → `"markdown"`
    - `"text"` → `"txt"` 或官方指定的文本模板名（以官方文档为准）。
  - 如配置了 `topic`，则附加 `topic` 字段。

#### 6.3 send 行为

`PushPlusChannel.send(msg: PushMessage, channel_config: dict) -> None`：

- 必须构造上述请求并发送到 PushPlus。
- 如 HTTP 请求失败或返回表示错误的状态码：
  - 至少记录一条错误日志，包含 `recipient_id`（可通过 msg 或上层传入）、HTTP 响应状态码和部分响应内容。
  - v1 可以选择不抛异常（避免中断其他 job），或抛异常由上层捕获记录；需在实现中保持风格一致。

---

### 7. GitHub Actions 触发规范（`.github/workflows/daily-push.yml`）

本 spec 对 Actions 只做最低限度约束：

- 必须存在一个 workflow 文件（建议命名为 `daily-push.yml`），其行为包括：
  - 按固定 cron 周期性触发（例如每 15 分钟一次 `*/15 * * * *`，UTC）。
  - 在 job 中：
    - checkout 仓库。
    - 设置 Python 运行环境。
    - 安装 `requirements.txt` 中的依赖。
    - 从仓库 Secrets 读取至少一个 PushPlus token（如 `PUSHPLUS_TOKEN_ME`），注入为环境变量。
    - 调用 `python -m src.cli run`（可选带 `--schedule`）。

其它细节（如缓存依赖、并发控制）可按实际需要实现，不在本 spec 强制范围内。

---

### 8. 非目标（本期不实现）

- 不实现 Web 界面或 API 服务，仅通过配置文件与 CLI + Actions 使用。
- 不实现数据库存储，一切配置均来自 YAML 文件。
- 不实现权限系统、多租户隔离等复杂能力。
- 不强制实现单元测试，但实现时应尽量保持模块内逻辑清晰、易于后续补测。


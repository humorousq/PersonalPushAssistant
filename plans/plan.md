## 个人推送助理开发计划（spec 模式）

### 1. 目标与范围

- **总体目标**：在本仓库中实现一个可扩展的「个人推送助理」基础设施，当前仅供少量个人使用，但设计上要求未来扩展为多用户小服务时无需大重构。
- **本期范围（v1）**：
  - 仅支持 **Python** 实现。
  - 仅支持 **PushPlus** 作为推送通道。
  - 支持多个「接收人」（每人一个 PushPlus token/配置），同一插件对不同接收人可产出不同内容。
  - 支持在一天内 **不同时间段** 推送 **不同插件**（通过调度配置控制）。
  - 首个内容插件：`stocks.daily-brief`（股票早报）。

### 2. 架构分层（高层）

- **调度层（Scheduler / Runner）**
  - 负责按时间（cron）和配置执行「调度任务（jobs）」。
  - 不关心具体推送通道实现细节。

- **接收人 + 通道层（Recipients + Channels）**
  - 以「接收人 id」为中心，每个接收人绑定一条通道配置（如 PushPlus token、topic）。
  - 通道层对外暴露统一接口 `send(msg, channel_config)`，对内实现不同类型通道（目前仅 PushPlus）。

- **插件层（Content Plugins）**
  - 每类业务内容实现一个插件，如 `stocks.daily-brief`。
  - 插件只依赖 `PluginContext`（时间、接收人 id、插件配置等）生成一组标准化 `PushMessage`。
  - 插件不直接调用通道，也不关心推送给谁，由 runner 负责绑定接收人并投递。

- **配置层（Config）**
  - 使用单一 YAML 文件（如 `config/config.yaml`）描述：
    - `recipients`：接收人及其通道配置。
    - `schedules`：调度计划（cron + jobs）。
    - `plugin_configs`：插件配置模板（可按人区分）。

### 3. 目录结构（约定）

```text
PersonalPushAssistant/
├── plans/
│   ├── plan.md                 # 本文件：高层计划
│   └── spec.md                 # 详细规格（实现需严格遵守）
├── config/
│   └── config.example.yaml     # 配置示例（不含真实 token）
├── src/
│   ├── __init__.py
│   ├── models.py               # PushMessage, PluginContext 等数据模型
│   ├── channel/
│   │   ├── __init__.py
│   │   ├── base.py             # Channel 抽象
│   │   └── pushplus.py         # PushPlus 通道实现
│   ├── plugins/
│   │   ├── __init__.py         # 插件注册/发现
│   │   └── stocks_daily.py     # 股票早报插件
│   ├── runner.py               # 核心调度执行逻辑
│   └── cli.py                  # 命令行入口（如 python -m src.cli run）
├── .github/workflows/
│   └── daily-push.yml          # GitHub Actions 定时触发
├── requirements.txt
├── README.md
└── .env.example                # 示例环境变量（如 PUSHPLUS_TOKEN_ME 等）
```

### 4. 开发里程碑（阶段划分）

- **阶段 1：基础骨架**
  - 搭建目录结构与基础文件。
  - 定义 `models.py` 中的核心数据结构与接口（PushMessage、PluginContext、ContentPlugin 协议等）。

- **阶段 2：通道层（PushPlus）**
  - 实现通用 `Channel` 抽象与 `PushPlusChannel`。
  - 能在本地通过简单脚本向某个接收人的 PushPlus 发送一条测试消息。

- **阶段 3：配置加载与 Runner**
  - 确定 `config/config.yaml` 的最终结构（与 spec 保持一致）。
  - 实现配置加载、解析接收人、调度计划（schedules + jobs）。
  - 实现 runner：根据当前时间和 schedule 决定执行哪些 jobs，调用插件并通过通道发送消息。

- **阶段 4：股票插件 `stocks.daily-brief`**
  - 选定行情与新闻数据源（尽量使用公开/免费 API）。
  - 实现插件逻辑：根据 `symbols`、`with_news` 等配置生成 Markdown 文本。
  - 验证在至少 1~2 个接收人、不同配置下输出不同内容。

- **阶段 4.1：金价插件 `gold.daily-brief`**
  - 新增金价简报插件，复用现有 Runner + PushPlus 通道能力。
  - 通过高质量金价 API（API key 配置化）拉取多品种报价，输出 HTML 表格简报。
  - 支持通过 `symbols` + `symbol_names` 做展示层配置，并在单个品种失败时保留整体推送。

- **阶段 4.2：银行汇率插件 `exchange.daily-brief`**
  - 新增银行汇率简报插件，基于探数数据银行汇率 API。
  - 支持配置多银行（banks）、多币种（currencies），按银行分组展示中间价、现汇买入、现汇卖出。
  - 复用 TANSHUAPI_KEY，输出 HTML 格式简报。

- **阶段 5：集成与自动化**
  - 编写 GitHub Actions workflow，定时触发 CLI。
  - 完善 README（说明配置方法、环境变量、如何扩展新插件/新接收人）。

### 5. 未来扩展预留

- 可以在不破坏现有协议的前提下：
  - 增加新的插件（新增文件到 `src/plugins/`）。
  - 增加新的通道类型（新增文件到 `src/channel/`）。
  - 把 `recipients`、`schedules`、`plugin_configs` 从 YAML 迁移到数据库或远程配置中心。
- 以上扩展都不应要求改动插件抽象、Channel 抽象或 PushMessage/PluginContext 的基本形态。


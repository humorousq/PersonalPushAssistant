---
name: personal-push-assistant
description: Provides project background, CLI usage, and stocks.daily-brief behavior for the PersonalPushAssistant repo. Use when the user works in this repo or asks about PushPlus setup, scheduling, or stock brief output.
---

# PersonalPushAssistant 项目技能

本技能为本仓库（PersonalPushAssistant）提供长期背景与工作流指导，帮助代理在新会话中快速进入状态，而无需用户反复解释项目用途和约定。

## 触发场景

在以下情况优先使用本技能：

- 当前工作目录为 `PersonalPushAssistant` 仓库，或用户提到「个人推送助理」「PersonalPushAssistant」。
- 用户询问「如何本地跑流程」「PushPlus 怎么配置/调试」「股票简报插件怎么用/改」。
- 用户提到 `stocks_daily.py`、`stocks.daily-brief`、`config/config.yaml`、`stocks_1024` 等关键字。

## 项目概览

- 语言与运行环境：
  - Python 3.10+。
  - 依赖在 `requirements.txt` 中，包含 `PyYAML`、`requests`、`croniter`、`beautifulsoup4`、`python-dotenv` 等。
- 功能定位：
  - 按 cron 调度执行「内容插件」（如股票简报），通过 PushPlus 向一个或多个接收人推送消息。
  - 支持本地手动运行和 GitHub Actions 定时运行。

## 核心组件

- CLI 入口：`src/cli.py`
  - 命令：`python -m src.cli run [--config path] [--schedule id] [--dry-run]`
  - `--config`：配置文件路径，默认 `config/config.yaml`。
  - `--schedule`：
    - 缺省时：根据当前时间匹配配置中的 `cron` 表达式，决定执行哪些 schedule。
    - 指定时（如 `--schedule stocks_1024`）：跳过时间匹配，直接执行该 schedule。
  - `--dry-run`：
    - 执行插件并生成 PushMessage，但**不发送**到 PushPlus，只在日志中输出预览与完整消息。
  - 日志：
    - `_setup_logging()` 将日志写入 `logs/app.log` 并同时输出到 `stderr`。
    - `logs/` 目录被 gitignore，避免提交。

- 调度与运行器：`src/runner.py`
  - 负责：
    - 加载 YAML 配置（`config/config.yaml`）。
    - 校验 recipients / schedules / plugin_configs 合法性。
    - 根据当前时间和 `--schedule` 参数筛选要执行的 schedule。
    - 创建 `PluginContext`，调用插件 `run()` 获取 `PushMessage` 列表。
    - 按 recipient 和 channel 配置，将消息交给 `src/channel` 中的通道实现发送。
  - 特性：
    - 当无 schedule 命中当前时间时，会在日志中说明，并提示使用 `--schedule <id>`。
    - 在 dry-run 模式下，会打印「Dry-run: would send ...」和完整消息体，便于调试插件输出。

- PushPlus 通道：`src/channel/pushplus.py`
  - 发送地址：`https://www.pushplus.plus/send`。
  - token 解析：
    - 支持在配置中写 `${ENV_NAME}` 占位符，通过环境变量（含 `.env` 注入）展开。
  - 模板选择：
    - `msg.format == "markdown"` → `template="markdown"`。
    - `msg.format == "html"` → `template="html"`。
    - 其他 → `template="txt"`。
  - 错误：
    - HTTP 非 200 或返回 JSON `code != 200` 会在日志中记录详细错误。

## 配置文件与环境变量

- 环境变量：
  - 仓库内有 `.env.example`，用户在本地复制为 `.env`，如：
    - `PUSHPLUS_TOKEN_ME=your_pushplus_token_here`。
  - `src/cli.py` 在启动时调用 `load_dotenv()`，自动加载 `.env`，无需提示用户 `source .env`。
  - 代理在解释时，应强调：
    - 不要提交 `.env`。
    - 环境变量主要用于 PushPlus token 等敏感信息。

- 配置文件：
  - 示例：`config/config.example.yaml`（受版本控制，用于文档和示例）。
  - 实际：`config/config.yaml`（被 gitignore，用于本地/生产环境）。
  - 重要约定：
    - 当需要讨论配置结构或字段含义时，以 `config.example.yaml` 为准，不窥视或假定用户本地 `config.yaml` 的具体值。
    - 不要将 `config/config.yaml` 纳入 Git 提交。

## 股票简报插件（stocks.daily-brief）

- 插件实现：`src/plugins/stocks_daily.py`，id 为 `stocks.daily-brief`。
- 目标：
  - 为给定的一组股票代码生成「每日简报」，目前以 HTML 表格形式在 PushPlus 上展示，适配手机端。

### 行情获取逻辑

- 数据来源：新浪 `hq.sinajs.cn`。
  - A 股：
    - 代码形如 `600519.SH` / `000858.SZ`。
    - `_symbol_to_sina` 负责转换为 `sh600519` / `sz000858` 等。
    - 使用字段：
      - 名称、今开（open_today）、昨收（prev_close）、现价（current）。
  - 港股：
    - 代码形如 `1024.HK`、`2015.HK`。
    - `_symbol_to_sina` 将其转换为 `hk01024` 等形式，并按港股字段顺序解析：
      - 0：英文名，1：中文名，2：今开，3：昨收，6：现价。
    - 由于中文名编码质量不稳定，展示时**默认不相信接口返回的名称**：
      - 通过配置 `symbol_names` 明确映射，如：
        - `"1024.HK": "快手-W"`、`"2015.HK": "理想汽车-W"`。

### HTML 输出与布局

- 插件当前生成 HTML，并设置 `PushMessage.format = "html"`，由 PushPlus 使用 `html` 模板发送。
- 结构：
  - 顶部标题：
    - `<h2>` + 小字号（约 15px），文本为 `今日股票简报（YYYY-MM-DD）`。
  - 主体表格（宽度 100%，font-size 约 13px）：
    - 列：
      - **名称**：优先使用 `symbol_names` 配置中的展示名，否则 A 股用接口名称，港股退回代码。
      - **现价**：当前价格，两位小数，右对齐。
      - **涨跌**：单元格内同时展示「涨跌幅 / 涨跌额」：
        - 涨跌幅使用红/绿 `<span>` 上色（涨红跌绿）。
        - 涨跌额为 `现价 - 昨收`，带 `+/-` 符号。
      - **昨/今**：`昨收 / 今开`，右对齐。
    - 行之间使用浅色边框（`border-top:1px solid #eee`）区隔，压缩行高以适配手机。
  - 获取失败的股票：
    - 不进入表格，在表格下方单独以红字 `<div>` 列出错误信息。

### 新闻（可选、默认关闭）

- 新闻数据来自东方财富搜索，仅在 `with_news=true` 且 `news_per_symbol > 0` 时启用。
- 当前默认行为：
  - 示例配置与 README 中的建议均为 `with_news=false`，即默认不推送新闻。
  - `_fetch_news` 内部对标题和链接做了基本过滤，以尽量过滤掉明显广告和推广链接，但不保证强相关性。
- 代理在回答时应明确：
  - 新闻功能是**实验性增强**，主要用于有兴趣阅读相关新闻的用户。
  - 推荐用户在确认股票简报本身稳定后，再考虑开启新闻。

## 与用户交互时的建议

- 当用户询问「如何本地跑流程」：
  - 优先指向 README 的「快速开始」部分。
  - 说明：复制 `.env.example` → `.env` 填 token；复制 `config.example.yaml` → `config.yaml`；使用 `python -m src.cli run --schedule test` / `--schedule stocks_1024`。
- 当用户需要调试或开发新插件：
  - 建议先使用 `--dry-run`，通过日志确认输出正确，再去掉 `--dry-run` 真正推送。
- 当涉及 PushPlus 故障时：
  - 通过日志中的 token mask（前缀 + 长度）帮助用户确认是否加载了正确的环境变量。
  - 区分「token 配置不生效」（解析为空或仍为 `${PUSHPLUS_TOKEN_ME}`）与「PushPlus 返回 903」这两类问题。


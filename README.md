# 个人推送助理（Personal Push Assistant）

按时间调度执行内容插件，并通过 PushPlus 推送到指定接收人。支持多接收人、多调度计划、多插件；本期实现 PushPlus 通道与股票早报插件。

---

## 快速开始（本地跑通一条推送）

按下面几步即可在本地发出一条 PushPlus 推送，用于验证 token 和流程。

1. **克隆并进入项目**
   ```bash
   git clone <本仓库地址>
   cd PersonalPushAssistant
   ```

2. **安装依赖**（建议先建虚拟环境：`python3 -m venv .venv` 再 `source .venv/bin/activate`）
   ```bash
   pip install -r requirements.txt
   ```

3. **准备配置文件**
   ```bash
   cp config/config.example.yaml config/config.yaml
   ```
   无需改 `config.yaml`，默认已用环境变量 `PUSHPLUS_TOKEN_ME` 作为 token。

4. **设置 PushPlus Token 并执行**
   - 将下面的 `你的PushPlus的token` 换成你在 [PushPlus](https://www.pushplus.plus/) 获取的 token。
   ```bash
   export PUSHPLUS_TOKEN_ME=你的PushPlus的token
   python -m src.cli run --schedule test
   ```

5. **预期结果**  
   终端无报错，且你的 PushPlus 会收到一条「占位」测试消息，即表示本地流程已跑通。

> 提示：不想每次输入 token，可复制 `.env.example` 为 `.env`，把里面的 token 填好；在终端执行 `source .env`（仅 Linux/macOS）后再运行上面的 `python -m src.cli run --schedule test`。

---

## 环境要求

- Python 3.10+
- 依赖见 `requirements.txt`

## 安装

```bash
pip install -r requirements.txt
```

## 配置

1. 复制配置示例并填写 token：
   ```bash
   cp config/config.example.yaml config/config.yaml
   ```
2. 在 `config/config.yaml` 中配置：
   - **recipients**：接收人 id 与 PushPlus 通道（`type: pushplus`、`token`，可选 `topic`）。
   - **schedules**：调度计划列表，每项含 `id`、`cron`（5 字段，UTC）、`jobs`。
   - **plugin_configs**：插件配置模板，供 job 的 `config_ref` 引用。

3. Token 支持环境变量占位，例如 `token: \${PUSHPLUS_TOKEN_ME}`，运行时从环境变量读取。可参考 `.env.example` 配置本地或 CI 环境变量。

## 运行

```bash
# 按当前时间匹配应执行的 schedule（cron 命中则执行）
python -m src.cli run

# 指定配置文件
python -m src.cli run --config config/config.yaml

# 仅执行指定 schedule（不按时间判断），例如本地测试
python -m src.cli run --schedule test
```

## GitHub Actions

仓库提供 `.github/workflows/daily-push.yml`：

- 按 cron 每 15 分钟触发一次（UTC）；Runner 内部根据各 schedule 的 cron 判断本次是否执行。
- 在仓库 Settings → Secrets 中配置 `PUSHPLUS_TOKEN_ME`（PushPlus token），workflow 会将其注入环境变量并执行 `python -m src.cli run`。
- 首次使用需将 `config/config.example.yaml` 复制为 `config/config.yaml`（workflow 中已通过 step 复制），确保配置中 token 使用 `\${PUSHPLUS_TOKEN_ME}`，由 Actions 注入。

## 插件说明

- **placeholder**：占位插件，返回一条固定文本，用于验证 Runner 与通道。
- **stocks.daily-brief**：股票早报。配置 `symbols`（如 `["600519.SH", "000858.SZ"]`）、`with_news`、`news_per_symbol`。行情来自新浪，新闻来自东方财富搜索。

## 扩展

- **新插件**：在 `src/plugins/` 下实现 `ContentPlugin`（`id` + `run(ctx) -> list[PushMessage]`），并在 `src/plugins/__init__.py` 的 `PLUGINS` 中注册；在 schedules 的 jobs 中增加 `plugin_id` 与 `config_ref` 即可。
- **新接收人**：在 `recipients` 中增加 id 及其 `channel`（如新 token）；在对应 schedule 的 jobs 中增加该 `recipient_id` 的 job。
- **新通道**：在 `src/channel/` 中实现 `Channel.send(msg, channel_config)`，并在 `get_channel` 中按 `type` 注册。

# Northstar · 个人量化工作台

React / TypeScript 前端 + Python / FastAPI 后端 + PostgreSQL + 独立 Worker。

这是计划的首个工程版本（v0.1）：实现标的维护、受限 Python 策略、多源配置、回测、模拟交易、模型研究、人工批准与任务追踪。真实券商未接入，真实订单入口强制拒绝；行情接入目前提供演示行情和本地 Parquet 快照两种驱动。系统没有假装连接真实交易账户。

**交付验证状态：已进行源码人工检查；未安装依赖、未启动服务、未执行构建、测试、类型检查或浏览器联调。下方命令用于你自己的部署环境，不表示已在本会话执行。**

## 快速启动

需要 Docker Engine 和 Compose。将源码包解压到独立目录。

1. 将 `.env.example` 复制为 `.env`。
2. 修改 `POSTGRES_PASSWORD`、`APP_PASSWORD`、`SESSION_SECRET`。数据库密码使用 URL 安全字符；账户密码至少12字符；签名密钥至少32字符；不能保留 `CHANGE_` 前缀。
3. 在你的部署主机执行：

```bash
docker compose up -d --build
```

4. 打开 `http://127.0.0.1:8080`，用 `APP_PASSWORD` 登录。

应用默认只绑定部署主机回环地址。远程使用需要自行配置 HTTPS 反向代理与访问控制。不要直接将无 TLS 的入口暴露到公网。

服务组成：`web` 提供编译后的前端和 API 反向代理；`api` 处理交互；`worker` 执行任务；`db` 保存业务数据。数据库与快照分别写入 `quant_db`、`quant_data` 两个持久卷。前端使用系统字体，不依赖外部 CDN。

## 第一次使用

1. **投资标的**：人工添加代码、名称、市场、每手数量。系统不会预先替你挑选标的。演示数据接受任意合法代码，但真实快照必须与代码完全一致。
2. **回测实验室**：选择标的和初始双均线策略，日期范围选一年，数据环境选择“演示数据”，提交回测。
3. **任务详情**：查看净值、回撤、成交明细，导出报告和运行清单。
4. **策略工作台**：确认评估结果后点击“批准发布”。批准仅允许模拟运行，不会启用实盘。
5. **交易与持仓**：运行策略信号，检查订单内容后人工确认模拟成交。若策略给出零仓位且当前无持仓，不会生成订单。
6. **模型协作**：选择一个或多个模型发起研究。默认“演示研究器”只调整参数，不调用真实大模型。研究产出新候选版本，不自动批准，也不自动替换调度版本。
7. **系统设置**：选择标的、版本、数据环境和研究模型后，启用日频信号或周研究。

默认模拟账户为 CNY / HKD / USD，各10万初始资金；演示数据和真实数据使用独立模拟账户。不同币种不自动合并，不虚构汇率。

## 真实行情与新闻

真实数据不等于真实交易。首版真实行情驱动从你提供的 Parquet 文件读取，并不持续连接供应商。

CSV 列必须为：`date,open,high,low,close,volume`。日期使用 `YYYY-MM-DD`；数据必须按日期严格递增、无重复，至少40个有效日期。原始成交价格与分红、复权口径需要自行核验。

在 API 容器中放入 CSV 后导入：

```bash
docker compose cp your-authorized-bars.csv api:/data/input.csv
docker compose exec api python -m app.import_data --csv /data/input.csv --symbol US.SPY --source your-data-vendor
```

随后在“行情与消息源”添加：

```json
{
  "name": "我的行情快照",
  "kind": "market",
  "driver": "parquet",
  "priority": 10,
  "enabled": true,
  "config": {
    "directory": "imports",
    "mode": "historical",
    "price_basis": "raw"
  }
}
```

界面有独立名称、驱动、优先级输入框；配置框只填写 `config` 的内容。将 `mode` 设为 `live` 表示你负责更新的真实市场快照；在线信号仍会检查报价新鲜度。系统不会在源失败时改用合成行情。

新闻使用 `json_news` 驱动，配置 `{"file":"imports/news.json"}`。文件内容为数组：

```json
[
  {
    "title": "已授权获取的政策或新闻标题",
    "body": "正文摘要",
    "published_at": "2026-09-18T08:00:00+08:00",
    "first_seen_at": "2026-09-18T08:02:00+08:00",
    "url": "https://example.com/news/source",
    "synthetic": false
  }
]
```

缺少时间、时间不含时区、决策时点以后才可获取的消息不会进入研究上下文。消息按 URL 或标题去重。行情与新闻均显示实际来源，不会将新闻文本当作执行指令。

## 接入真实模型

在 `.env` 中设置密钥及部署者允许的地址，例如：

```dotenv
MODEL_KEY_PRIMARY=your-provider-key
MODEL_KEY_SECONDARY=another-provider-key
MODEL_ALLOWED_BASE_URLS=https://your-provider.example/v1,https://another-provider.example/v1
```

通过 Compose 重建 API / Worker 容器使环境变量生效，然后在“模型协作”添加 `openai_compatible` 驱动：

```json
{
  "base_url": "https://your-provider.example/v1",
  "model": "your-model-name",
  "key_env": "MODEL_KEY_PRIMARY"
}
```

供应商必须支持 Chat Completions 风格请求及 JSON object 输出。不同协议需实现新适配器，不宣称所有模型天然兼容。前端不能写入明文密钥，也不能通过配置引用任意系统环境变量。请求只发往部署者明确允许的 HTTPS 地址，不跟随重定向。

每次研究最多4个模型，每模型1次调用，最多2000输出 token，单次请求超时45秒；不自动付费重试。实际 usage 保存在报告中。首版没有美元/人民币费用换算与账户级月度预算。

## 可复现回测脚本

安装后端依赖后，在源码根目录运行，或者在 API 容器中使用同样的模块命令：

```bash
PYTHONPATH=backend python -m app.cli --symbol US.SPY --start 2024-01-01 --end 2024-12-31 --out data/report.json
```

可指定 `--code`、`--params`、`--initial`、`--lot`、`--fee-bps`、`--slippage-bps`。不提供运行清单时使用演示数据。

页面导出的运行清单可直接重放：

```bash
PYTHONPATH=backend python -m app.cli --manifest data/manifest.json --out data/replayed.json
```

成功任务清单包含 `frozen_data`，重放读取对应 Parquet 快照并校验 SHA-256。异机重放时要同时复制 `DATA_DIR` 中的快照；缺失或哈希不同会失败，不改用新行情。策略、参数、费用及数据配置都来自清单。研究模型本身可能有非确定性；“可复现”指固定策略和数据的回测结果，不保证重新调用模型得到相同代码。

## 工程入口

策略自主研究支持「提出假设 → 最小代码／参数修改 → 固定评估 → 诊断 → 保留／回滚 → 记录 → 下一轮」。可选择均衡日线、长线趋势或短线波段，并导入带实际可用时间的财报、宏观、公告和盘口衍生数值特征；详见 [策略自主研究与多源特征](docs/AUTORESEARCH.md)。当前新增特征通过离线JSON快照接入，盘口仅支持日级因子研究。

- `backend/app/main.py`：API 与登录、审批、配置。
- `backend/app/worker.py`：持久化任务队列、心跳、取消、调度。
- `backend/app/runner.py`：独立计算进程和模型研究流水线。
- `backend/app/strategy.py`：受限 Python AST 解释器，不使用 `eval` 或 `exec`。
- `backend/app/backtest.py`：日频回测引擎。
- `backend/app/providers.py`、`contracts.py`：数据、模型、新闻和券商协议。
- `frontend/src/`：真实 API 驱动的 React 应用；无前端伪造收益数据。
- `docs/ARCHITECTURE.md`：边界、数据流程、协议、扩展与并发约定。
- `docs/ACCEPTANCE.md`：已提供的测试与人工验收步骤。
- `docs/ROADMAP.md`：首版与完整计划之间的差距。

## 本地开发与验证

后端兼容 SQLite 作为单进程本地开发存储；Compose 默认 PostgreSQL，是并发运行推荐路径。启动服务前需设置 `.env.example` 中对应环境变量；Python 不会自动读取 `.env` 文件。

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
npm install
```

在自己的开发环境分别运行 API、Worker 和 Vite：

```bash
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
PYTHONPATH=backend .venv/bin/python -m app.worker
npm run dev
```

验证命令（本次尚未执行）：

```bash
PYTHONPATH=backend .venv/bin/pytest backend/tests -q
npm run build
```

首次安装后应生成并提交前端锁文件、后端精确依赖锁文件，再作为后续持续集成基准。当前使用依赖版本范围，尚未完成依赖解析及供应链审计。

## 备份与恢复

先暂停 Worker，避免任务运行中跨卷数据不一致。在部署环境执行：

```bash
docker compose stop worker
docker compose exec -T db pg_dump -U quant -d quant > quant-db.sql
docker compose cp api:/data ./quant-data-backup
docker compose start worker
```

将数据库备份与快照目录一起保存，并单独保管密钥配置。恢复须先停止 API 与 Worker、启动数据库，将 SQL 导入空数据库，再恢复 `quant_data` 卷中的文件并确认运行用户权限，最后启动 API 与 Worker。恢复后先核对任务、策略、订单、快照哈希，再开放操作。不要将恢复演练视为已经完成。

## 重要边界

这是研究与模拟交易工程，不是已验证的盈利系统或可直接投入资金的实盘系统。简化撮合尚未完整实现交易所节假日、涨跌停、公司行动、结算与各市场费用。策略为受限 Python 子集，并不支持任意 Python 包；生产沙箱和多资产组合引擎尚待实现。

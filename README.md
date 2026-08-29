# BlockMarket

BlockMarket 是一套可复现、可审计的双人合成市场策略游戏。两个 Bot 每轮同时报价，竞争同一笔有限客户订单；比赛不连接真实市场、账户、资金、区块链或外部模型。

当前规则是 `blockmarket-v1-prototype.2`，Python 包版本为 `0.3.0`。Gate 0–3 已完成；Gate 4 的锦标赛 orchestrator 和审计包代码也已完成，等待 5–10 人真人封闭 Alpha。它仍不是正式赛事系统；OCI 无公网沙箱、双机复放、真人验证和校内审批尚未完成。

第一次接触请先读 [玩家指南](docs/PLAYER_GUIDE.md)，完整规则见 [规则契约](docs/RULES_V1.md)。

三屏可视化规则站源码位于 [`site/`](site/)，包含真实引擎回放、策略克制关系和公平审计流程；网页发布在 [BlockMarket GitHub Pages](https://felix-robot.github.io/BlockMarket/)。

## 30 秒玩法

- 目标：结束时净资产高于对手；双方得分严格互为相反数。
- 每轮可提交买价 `bid`、卖价 `ask` 和各自数量，也可以单边报价或 `HOLD`。
- 只有更优报价获得客户订单；同价按实际容量比例分配。
- 窄价差容易成交，但可能接到知道下一步走势的“知情订单”；65% 准确的公开方向信号能帮助 Bot 规避一侧风险，但不保证正确。
- 现金、库存、费用和终值持仓共同决定结果。对手会改变你的成交机会，所以不存在只看价格预测的固定答案。

## 快速开始

要求：CPython 3.12；TypeScript starter 的可运行版本要求 Node.js 20+。核心引擎只使用 Python 标准库。

```bash
uv run --python 3.12 python -m unittest discover -s tests -v
uv run --python 3.12 python -m blockmarket demo --blocks 50 --output replays/demo.json
uv run --python 3.12 python -m blockmarket verify replays/demo.json
uv run --python 3.12 python -m blockmarket matrix --blocks 100 --seeds 10
```

运行两个独立 Bot 进程：

```bash
uv run --python 3.12 python -m blockmarket run \
  --bot-a python3 starter_kits/python/bot.py \
  --bot-b node starter_kits/typescript/bot.mjs \
  --blocks 50 --output replays/subprocess.json
uv run --python 3.12 python -m blockmarket verify replays/subprocess.json
```

JSONL 协议和故障语义见 [Bot 协议](docs/BOT_PROTOCOL.md)。

运行两人示例锦标赛并独立验证审计包：

```bash
uv run --python 3.12 python -m blockmarket tournament \
  examples/tournament.request.json --output audit/alpha-demo
uv run --python 3.12 python -m blockmarket verify-tournament audit/alpha-demo
```

提交格式、种子生命周期、排名与审计包结构见 [锦标赛指南](docs/TOURNAMENT_GUIDE.md)；真人测试使用 [Alpha 报告模板](docs/ALPHA_REPORT_TEMPLATE.md)，不能用参考 Bot 数据代填。

## 已实现

- 50 位 `Decimal` 记账、严格动作校验、最优价成交与同价确定性分配。
- 稳定二阶合成基线、隐藏确定性噪声、普通/知情订单和公开带噪信号。
- 现金/库存风险约束、费用、终值财富和严格零和得分。
- canonical JSON、事件 SHA-256 哈希链、完整回放和不调用主引擎的独立复核器。
- 七个参考策略、换边收益矩阵、逐配对胜负分布、循环与位置偏差检测。
- 双 Bot 同轮并发 JSONL runner；单方超时、崩溃、非法 JSON 或日志洪泛不会中止对手。
- Python 与 TypeScript starter kit。
- Gate 4 round-robin：提交快照哈希、赛前种子承诺、赛后揭示、同种子换边、排名和完整审计包。
- 50 项自动化测试、跨语言锦标赛 CLI smoke test、三屏交互规则站和 GitHub Actions CI / Pages 部署。

## 当前证据与边界

- 100 个公开开发初值、300 轮、七策略双局换边矩阵共 4,200 场：无严格正收益统治策略，检测到 8 个三策略循环。详见 [游戏性审计](docs/GAMEPLAY_AUDIT.md)。
- runner 是本地进程边界，不是安全沙箱；它尚未禁止文件、网络、子进程或系统调用。
- Gate 4 的代码层已完成，但参考 Bot 测试不能代替 5–10 名真人对规则理解和策略空间的封闭验证。
- 收益矩阵是当前参考策略集合上的实证验收，不是“不存在纳什均衡”的证明。
完成度与下一关闭条件见 [STATUS.md](STATUS.md)。

## License

BlockMarket 采用 [Apache License 2.0](LICENSE) 开源。

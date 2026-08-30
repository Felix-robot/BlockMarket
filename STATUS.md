# BlockMarket 实现状态

更新日期：2026-08-30

规则版本：`blockmarket-v1-prototype.2`

Python 包版本：`0.3.0`

## 已完成

- Gate 0：manifest、observation、action、event、summary、replay schema；正常/异常动作样例；12 个手算结算案例。
- Gate 1：确定性订单流、严格报价校验、成交账本、终局摘要、哈希回放、独立复核器。
- Gate 2：七个参考 Bot；100 初值 × 300 轮 × 双局换边的完整矩阵；无严格统治策略；8 个三策略循环；规则升级至 prototype.2。
- Gate 3 本地运行层：双 Bot JSONL 子进程、同轮并发决策、250 ms 默认硬超时、stdout/stderr 上限、单方故障隔离、Python/TypeScript starter kit、恶意进程回归测试。
- Gate 4 代码层：最多 16 人 round-robin、提交快照哈希、赛前种子承诺、HMAC 确定性派生、同种子 AB/BA 换边、排名、提交/回放/诊断/整包哈希与独立审计命令。
- 秘密生命周期：全部对局结束前不写种子揭示、完整回放或提交副本；提交自修改会在揭示前中止。
- 工程验证：51 项标准库单元测试、Python↔Node 跨语言对局、三人 round-robin、确定性审计包、分层篡改检测、三屏规则站、CLI smoke test、Ruff 与 GitHub Actions CI。
- 公开展示：干净源码仓库 <https://github.com/Felix-robot/BlockMarket> 与 GitHub Pages 三屏规则站 <https://felix-robot.github.io/BlockMarket/> 已发布；回放页直达 A/B 参考 Bot 源码，证据页公开市场生成公式与 65% 信号、10% 知情客户等参数；桌面与手机端均采用大字号且保持固定三屏切换。
- 公开提交：<https://github.com/Felix-robot/BlockMarket-Bots> 采用开源 Pull Request；玩家 Fork 后只需在个人目录提交一个 Python 或 Node.js Bot。外部贡献者的检查需维护者先批准，且只在 GitHub 托管的临时环境中使用只读权限、无密钥的公开样例；正式比赛仍使用隐藏市场种子。

## 已确认的玩法结论

主要循环为：`SignalFollower > TightSpread > OpponentAdaptive > SignalFollower`。

在 100 个公开开发初值上，三条边的换边平均得分与逐初值胜负为：

| 优势方 | 劣势方 | 平均得分 | 胜–负 |
| --- | --- | ---: | ---: |
| SignalFollower | TightSpread | +0.030718083332 | 75–25 |
| TightSpread | OpponentAdaptive | +0.246719308682 | 100–0 |
| OpponentAdaptive | SignalFollower | +0.060065141572 | 98–2 |

完整矩阵的位置差绝对值均值为 `0.000741668958`，P95 为 `0`，最大单初值未换边差为 `0.086001398225`；正式比较仍必须双局换边。

## 尚未声称完成

- Gate 4 产品验证：尚未邀请 5–10 名真实参与者完成封闭 Alpha，不能把参考 Bot 运行等同于真人反馈。
- Gate 5：Linux/OCI 文件与网络隔离、系统资源限制、双机确定性复放、两倍规模压力测试、独立盲审和校内审批。
- 当前公开页和公开 Bot PR 仓库仍不是正式赛事系统；自动锦标赛、排行榜、奖项、合作方接入与正式赛事资格尚未完成。
- 对更大策略空间的数学结论；当前只证明参考集合与公开开发初值上的实证性质。

## 下一关闭条件

1. 通过公开 Bot PR 仓库邀请 5–10 名测试者，记录规则理解、首次合法提交耗时、策略多样性、故障率和回放可读性。
2. 形成独立 Alpha 报告，检查真人策略是否出现新统治关系或不可接受的位置尾部。
3. 根据封闭 Alpha 冻结或再次升级规则；在此之前不对外承诺正式比赛。
4. 若要把公开提交接入个人电脑或正式赛事评测，而不只是在 GitHub 托管的临时环境跑公开样例，再完成 Gate 5 OCI 无公网沙箱、双机复放和两倍规模压力测试。

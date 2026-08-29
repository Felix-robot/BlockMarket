# BlockMarket 封闭 Alpha 锦标赛指南

Gate 4 orchestrator 用于可信内部参与者的封闭 Alpha。它会冻结提交、承诺隐藏种子、对每个配对执行同种子 AB/BA 换边、汇总排名，并生成可独立校验的审计包。

它仍不等于安全沙箱；来源不明的公开代码必须等 Gate 5 OCI 隔离完成后再接收。

## 1. 准备请求

复制 `examples/tournament.request.json`：

```json
{
  "tournament_id": "alpha-demo",
  "blocks": 300,
  "seed_count": 3,
  "runner_limits": {
    "decision_timeout_ms": 250,
    "max_stdout_bytes": 65536,
    "max_stderr_bytes": 65536
  },
  "participants": [
    {
      "participant_id": "alice",
      "source": "submissions/alice/bot.py",
      "command": ["python3", "-u", "{entrypoint}"]
    },
    {
      "participant_id": "bob",
      "source": "submissions/bob",
      "entrypoint": "bot.mjs",
      "command": ["node", "{entrypoint}"]
    }
  ]
}
```

`source` 可以是单文件或目录。目录提交必须给出内部 `entrypoint`。命令必须包含 `{entrypoint}` 或 `{submission}`；runner 实际执行的是冻结副本，不是原始路径。

单个提交最多 1,000 个普通文件、20 MiB；拒绝符号链接和特殊文件。一次锦标赛最多 16 人、100 个种子和 1,000,000 个区块事件，超过限制直接拒绝。

## 2. 运行与验证

自动生成 256-bit 主种子：

```bash
uv run --python 3.12 python -m blockmarket tournament \
  examples/tournament.request.json --output audit/alpha-demo
```

若操作方已有独立生成的 64 位小写十六进制种子文件：

```bash
uv run --python 3.12 python -m blockmarket tournament \
  examples/tournament.request.json --output audit/alpha-demo \
  --master-seed-file operator-seed.txt
```

输出目录必须不存在，工具绝不覆盖已有目录。完成后可在另一台环境运行：

```bash
uv run --python 3.12 python -m blockmarket verify-tournament audit/alpha-demo
```

## 3. 公平与种子生命周期

1. 冻结提交后、比赛前写 `tournament.json` 和 `seed-commitment.json`；承诺同时绑定赛后种子揭示与全部提交哈希/赛制配置。
2. 主种子通过 HMAC-SHA-256 确定性派生每个初值、订单流密钥和同价轮换偏移。
3. 每个参与者配对在同一派生环境运行 AB、BA 两局；配对得分为两局中同一参与者得分的平均。
4. 比赛期间不会写出 `seed-reveal.json`、完整回放或提交副本，避免后续 Bot 从输出目录读取秘密或对手源码。
5. 每场结束都重新计算全部冻结提交哈希；发现自修改立即中止，且不揭示种子。
6. 全部对局完成后才揭示主种子并导出回放。

Gate 3 尚未隔离文件系统，因此恶意进程理论上仍可能扫描宿主机；这里提供的是可审计生命周期，不是系统级安全保证。

## 4. 排名

每个“配对 × 种子”先用 AB/BA 平均得到严格零和分数。排名依次按：

1. 所有配对种子的平均得分降序；
2. 换边配对胜场数降序；
3. participant ID 字典序，仅用于稳定输出。

完全相同的平均分与胜/平/负记录共享名次。结果同时记录非法动作数和 runner 故障场数，但它们当前不作为额外裁决规则。

## 5. 审计包

```text
audit-pack/
├── tournament.json
├── seed-commitment.json
├── seed-reveal.json
├── result.json
├── audit-manifest.json
├── submissions/<participant>/...
├── replays/<match>.json
└── diagnostics/<match>.json
```

独立验证会检查：整包文件集合、长度与 SHA-256；提交快照哈希；承诺—揭示关系；种子重新派生；赛程和换边；每个 replay 的订单流、账本与哈希链；排名重算；result/audit hash。即使篡改者重新计算外层文件清单，改变种子仍会与回放 manifest 冲突。

## 6. 真人封闭 Alpha 的记录项

代码完成不等于产品验证。邀请 5–10 名参与者时，每人至少记录：规则读懂耗时、首次合法运行耗时、策略类型、非法动作/runner 故障、是否理解换边排名、回放是否足以解释胜负，以及是否发现新的统治策略。结论应进入单独的 Alpha 报告，不能用参考 Bot 测试代替真人反馈。

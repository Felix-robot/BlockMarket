# BlockMarket Bot JSONL 协议

协议标识：`blockmarket-jsonl-v1`。

runner 启动 A、B 两个独立进程。每个方向都是 UTF-8 JSON Lines：一条消息必须完整位于一行，stdout 只能用于协议响应，调试信息写 stderr。

## 1. 初始化

runner 先向 stdin 写一条 `init`，Bot 不回复：

```json
{"type":"init","protocol":"blockmarket-jsonl-v1","player_id":"A","manifest":{}}
```

公开 manifest 包含规则、位置、环境类型和隐藏环境承诺，但不包含 `x_prev`、`x_cur` 或 `orderflow_key`。完整秘密只在赛后 replay manifest 中揭示。

## 2. 决策

每轮 runner 同时向两个 Bot 写入：

```json
{"type":"decision","observation":{"schema":"observation.v1"}}
```

Bot 必须在本轮超时内向 stdout 恰好写一行 `action.v1` 并 flush：

```json
{"decision_seq":1,"bid":{"price":"99.55","quantity":"7"},"ask":null}
```

Bot 不得提前输出下一轮动作。重复 JSON key、NaN/Infinity、非 UTF-8、额外协议行或超过输出上限均按故障处理。

## 3. 结束

正常结束时 runner 写入 `{"type":"end"}` 并关闭 stdin。Bot 无需回复，应尽快退出。

## 4. 默认限制与故障语义

| 项目 | 默认值 |
| --- | ---: |
| 单轮决策超时 | 250 ms |
| 单条 stdout 上限 | 65,536 bytes |
| 整场 stderr 保留上限 | 65,536 bytes |

出现无法启动、崩溃、超时、管道断开、非法 UTF-8、非法 JSON、stdout/stderr 洪泛后：

1. 只终止故障 Bot；
2. 当前及后续轮次给该 Bot 注入稳定的 `runner_error` submission；
3. 动作校验将其转成 `HOLD` 并写入回放；
4. 对手继续正常决策和结算；
5. replay 仍可由独立复核器完整验证。

## 5. 安全边界

当前 Gate 3 runner 只提供进程和协议故障隔离。它**没有**限制文件系统、网络、子进程、CPU 总量、内存或系统调用，因此不能直接承载来源不明的公开提交。正式不可信代码必须进入后续 Linux/OCI 无公网沙箱并设置 cgroup/进程/文件限制。

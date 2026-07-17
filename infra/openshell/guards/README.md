# SIQ 破坏性操作守卫

本目录说明 `siq_analysis` OpenShell 运行面的宿主侧删除守卫。实现文件为 `scripts/openshell/destructive_action_guard.py`。

该守卫接入正式 `siq_analysis` 生命周期 worker。触发事件会在 sandbox fencing 前持久写入；worker 随后移除 forwarder、sandbox 和一次性凭据，将守卫进程记录为 pending exit；独立无锁 watchdog 会在任何非干净退出后执行幂等生命周期恢复。长驻 guard 和 forward 进程不会继承 start 操作的维护锁；触发或 watchdog 会在改变生命周期状态前获取新的有界锁。默认 `start_all.sh` 的 Host 运行面仍未改变，切流前仍需要真实业务 sandbox 验收运行。

## 固定范围

一个 guard 实例只拥有一个任务和一个公司的直接 `analysis/` bind root。可接受 root 形态：

```text
data/wiki/companies/<company>/analysis
data/wiki/{eu,hk,jp,kr,us}/companies/<company>/analysis
```

守卫会拒绝通过 symlink 到达的项目根、更宽的 Wiki root、嵌套 analysis 目录或第二家公司。恢复快照始终写入：

```text
var/openshell/siq-analysis/deletion-snapshots/<siq-run-id>/
```

状态目录和锁只在宿主侧存在，由 owner 控制并保持私有。sandbox 只能接收该任务的 analysis bind，不能接收该 snapshot root。

## 生命周期合同

最终运行集成必须按以下顺序执行：

1. 用固定 sandbox API 构造 `SandboxTerminator` 实现。
2. 用已验证 `SecurityRunContext` 和精确 analysis root 构造 guard。
3. 启动 sandbox 前调用 `prepare()`；只有 snapshot 创建和递归 watch 安装都成功时才能启动 sandbox。
4. 只启动 run context 指定的 sandbox，并在任务生命周期内保持 `monitor()` 活跃。
5. 任务结束后调用 `close()`。
6. 触发时，在终止前持久化 `guard.trigger.json`，并让 transaction 保持 `stopping`，直到 guard 进程退出且恢复完成。watchdog 也会把缺失 guard event 视为失败关闭的 guard failure，并恢复已验证基线 snapshot。

`SandboxTerminator.terminate()` 没有 command 或 script 参数。生产实现必须在返回前同步停止并隔离指定 sandbox。guard 只在该调用成功后恢复文件。

## 事件与阈值

实现使用 Python 标准库和 libc 的 Linux inotify。它监听完整树，在目录出现时新增 watch，并处理 `DELETE`、`MOVED_FROM`、`DELETE_SELF` 和 `MOVE_SELF` 事件。它不检查 shell 命令文本，因此 shell、Python、Node 和直接 syscall 删除共享同一事件路径。

只有 `prepare()` 运行时已经存在的普通持久文件会计入阈值。任务期间创建的文件不计入。任务前和新建的 `.cache`、`.work`、`cache`、`tmp`、`temp`、`__pycache__` 下文件作为可丢弃任务状态排除。

第一个满足条件的事件会终止 sandbox：

| 条件 | 结果代码 |
| --- | --- |
| 超过 500 个基线文件被删除或移动 | `deletion_count_gt_500` |
| 至少 20 个基线文件且至少 50% 基线被删除或移动 | `deletion_ratio_threshold` |
| 受保护 analysis root 被删除或移动 | `analysis_root_self_deleted` |
| inotify 队列溢出 | `inotify_queue_overflow` |
| inotify 监控失败 | `inotify_monitor_failure` |

小规模正常删除会保留原状，不会终止 sandbox。

## 快照与恢复安全

快照发布使用私有 staging 目录，并在持有每个 analysis root 独占锁时原子 rename。快照文件权限为 `0600`，内部目录为 `0700`。每个持久文件都会记录 size、mode 和 SHA-256 digest。

如果 analysis 树包含 symlink、hard link、特殊文件、set-ID 文件、类似凭据的名称、私钥材料、bearer 凭据或携带凭据的 URL，准备阶段会失败关闭。临时目录不会复制，但仍会扫描不安全 entry 类型和名称。

触发后，恢复仅限于同一 analysis root。它会重建缺失基线目录，并用 digest 验证过的 snapshot 原子替换每个缺失、移动或变化的基线路径。现有 symlink、hard-link 或特殊文件目标会导致恢复失败关闭。guard 不会改变 Hermes sessions、宿主代码、其他公司、移动目的地或受保护 analysis root 外的任何路径。

恢复会通过 `scripts/openshell/security_audit.py` 写入一条最小 `filesystem.delete` 拒绝记录。目标会投影而不是保存为路径，文件内容永远不会写入审计记录。

## 运行边界

- 需要 Linux inotify 和足够的 `max_user_watches` 容量。
- 检测发生在文件系统事件之后。部分文件可能在 sandbox 终止前消失，符合条件的基线文件随后会恢复。
- 恢复是逐文件原子，不是整树事务。若宿主在恢复中途退出，snapshot 会保留。
- 并发 operator stop 或 rollback 会保留其已经持久化的终态动作，但会尊重此前持久化的 guard trigger，并在 finalization 前恢复 snapshot。
- 移动目的地有意不删除。恢复只还原基线源路径，不触碰未批准目的地。
- 低于两个阈值的小规模删除有意允许，且不会恢复。
- 未伴随 delete 或 move 事件的文件修改不属于 T6.1 范围。
- 运行态 snapshot 仍被 Git 忽略。每个 deletion snapshot 应保留到 transaction 终态、脱敏验收证据已评审且对应 analysis artifact 已备份。垃圾回收必须在维护锁下运行，且绝不能删除 active run 或任何非终态 transaction snapshot。

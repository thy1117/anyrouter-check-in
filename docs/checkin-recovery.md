# 签到故障修复与验证

## 本次修复范围

修复 2026-09-30 合并 #63 引入的认证/配置回退，保留 EXTRA_ACCOUNTS_58、EXTRA_ACCOUNTS_59 及当前生产代理分配逻辑：

- 小白使用 /api/v1/auth/me 验证身份、/api/v1/auth/refresh 刷新；已验证会话遇到签到 502 不再盲目轮换令牌，也不会在状态请求失败后直接提交签到。
- 小白和 token-only Bearer 账号读取加密的最新状态。刷新前写入 pending 标记，刷新结果持久化成功后才继续请求。不能读写状态、响应不确定或刷新中断时停止，不回退到旧 Secret、也不重放刷新。
- 恢复 llmpm Provider、SuperAPI 的用户名密码登录路径和 cryptography 依赖。
- 恢复共享认证状态的工作流互斥、单账号诊断入口、加密恢复检查点。
- 修复余额/浏览器缓存 key，用 v2 前缀避开永久陈旧缓存；运行 ID 加运行次数确保重跑也能更新。余额、浏览器及代理分配记录在部分账号失败后仍保存，取消的运行不保存。
- 任一账号失败时返回非零退出码，并输出 Actions Summary；已签到仍算成功。全量循环不会因为一个账号失败就中断。
- CI 显式使用 bash/pipefail，通过 if 管道捕获真实检查结果，不再判断 tee 的退出码；关键检查失败会阻止绿色质量门禁。

## 状态存储要求

继续使用已有的 production 环境 Secret XIAOBAI_TOKEN_STATE_KEY 和 checkin-token-state 分支，不要重新生成密钥或覆盖已有状态。工作流将短期 github.token 作为 XIAOBAI_STATE_GITHUB_TOKEN，并恢复 contents: write 权限。明文令牌不会提交到代码、摘要或恢复附件。

如持久化失败，恢复附件只包含 .xiaobai-token-recovery/*.fernet 密文。pending 状态表示上次刷新结果不确定：先恢复加密检查点，或由账号所有者重新登录并更新该账号凭据，不应清除 pending 后直接重放旧刷新令牌。

密码登录的 Bearer 账号继续使用原有登录流程，不强制依赖 token-only 状态库。token-only 账号没有配置状态库时可以使用仍有效的 access token，但拒绝不可持久化的刷新。

## 验证与上线

1. 离线运行 uv sync --frozen、uv run pytest、uv run ruff check . --no-fix、uv run ruff format --check .。测试禁止真实网络连接，通知接口实测默认跳过。
2. 合并后可手动选择 xiaobai_config_only，只检查指定账号来源；不会配置代理、加载令牌状态、请求站点或发送通知。
3. 单账号 xiaobai_status_only 会验证身份和签到状态，必要时会刷新并持久化令牌，但不会提交签到。它不是严格只读操作，运行前应明确授权。
4. 生产执行须另外授权，或等待现有定时计划。缓存 v2 首次运行没有旧浏览器缓存，可能需要重新建立登录会话。

SheApi 的 IP/账号数量限制、MotoMoto 的 Turnstile 挑战不属于可以保证用此补丁消除的故障。本补丁没有更换出口、绕过站点限制、增加验证码点击次数或自动重跑失败账号。仍被拒绝时应按站点规则等待或人工处理；此时 Actions 显示失败是预期的真实状态，不再伪装为全量成功。

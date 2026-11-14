# Agent Loop on GitHub：自己发现问题、提需求、修、测、部署

```
   ┌──────────────────────────────────────────────────────────────────┐
   ▼                                                                  │
 Deploy (push 到 main → 测试 → 打 prod-N tag)                          │
   │ workflow_run                                                     │
   ▼                                                                  │
 Monitor & Analyze (对线上版本跑流量、分析日志)                          │
   ├─ 崩溃 / 体验问题 → Issue [bug] + ready                            │
   ├─ 高频未知优惠码 → Issue [feature] + needs-approval（人加 ready 才做）│
   └─ 没有在途工单时，把影响最大的 ready issue 打上 agent-fix             │
   │ issues: labeled                                                  │
   ▼                                                                  │
 Agent Fix (Claude Code 改代码并补回归测试 → 本地测试把关 → 开 PR)        │
   │      └─ 失败 → needs-human，交给人，然后继续挑下一张                 │
   │ pull_request                                                     │
   ▼                                                                  │
 CI (pytest) ── 通过 → 自动 squash merge（"Fixes #N" 自动关 issue）──────┘
```

每次只处理一张工单，避免多个 PR 互相冲突。合并以后会自动部署，然后再触发监控，就这样一直转下去，直到没有新问题。

## 文件

| 文件 | 作用 |
|---|---|
| `.github/workflows/monitor.yml` | 监控 + 分析 + 开 issue + 派单 |
| `.github/workflows/agent.yml` | agent 修复、测试把关、开 PR / 转人工 |
| `.github/workflows/ci.yml` | PR 测试 + agent PR 自动合并 |
| `.github/workflows/deploy.yml` | 合并到 main 后部署（这里用打 tag 模拟） |
| `loop/gh_monitor.py` | issue 去重、刷新、回归时重开，派单逻辑 |
| `loop/traffic.py`, `loop/analyst.py` | 模拟流量、日志分析规则 |
| `loop/mock_agent.py` | 离线模拟 agent（不需要 API key） |
| `loop/run_loop.py` | 纯本地版本（不需要 GitHub） |

## 设置（约 5 分钟）

1. **新建一个 GitHub 仓库**，把本目录内容推到 `main`。
2. **建一个 Fine-grained PAT**（只授权这个仓库），权限：Contents、Issues、Pull requests、Actions、Workflows 设为 Read and write。
   > 为什么需要 PAT：用默认 `GITHUB_TOKEN` 打的标签、开的 PR、做的合并**不会触发其他 workflow**，循环会断。
3. **Settings → Secrets and variables → Actions**：
   - Secret `LOOP_TOKEN` = 上面的 PAT
   - Secret `ANTHROPIC_API_KEY` = 你的 Claude API key（用 mock 模式可以先不设）
   - Variable `AGENT_MODE` = `mock`（先免费跑通；之后删掉或改成 `claude` 就会用真的 Claude Code）
   - 可选 Variable `AUTO_APPROVE_FEATURES` = `true`：新需求不用人批准
   - 可选 Variable `AUTO_MERGE` = `false`：agent 的 PR 改成由人审核后合并
4. 可选：Settings → Environments → `production` 加 Required reviewers，这样每次上线都要人点批准。
5. **Actions 页面 → Deploy → Run workflow**（或者直接 push 一次），循环就开始了。

### 你会看到

- Issues 里自动出现 4 张工单：3 个 bug，1 个新需求 `FREESHIP`
- bug 按影响大小一张一张被 agent 接走，开出 PR，CI 通过后自动合并，接着自动部署
- 修空购物车那张，mock agent 第一次故意修错，会被测试拦下来再重试
- `FREESHIP` 那张停在 `needs-approval`。你给它加上 `ready` 标签，它就会被做掉
- 每次监控的 Job Summary 里有线上指标表

### 换成 Claude Code

把 `AGENT_MODE` 改成 `claude`，再设好 `ANTHROPIC_API_KEY`，`agent.yml` 就会用 `anthropics/claude-code-action@v1`。
Claude 只允许读写文件、运行 pytest；commit、开 PR、合并都由 workflow 来做，权限边界清楚。
可以在 `claude_args` 里加 `--model` 或调 `--max-turns`。

## 用到真实项目

| Demo | 真实环境 |
|---|---|
| `traffic.py` 回放模拟流量 | 读 Sentry / Datadog / 日志 / 用户反馈 / 埋点 |
| `analyst.py` 规则 | 规则先做聚合，再交给 Claude 写需求说明 |
| 打 tag = 部署 | 预览环境 → 灰度 → 看指标 → 自动回滚 |
| pytest | 单元 + 集成 + E2E，最好有 agent 不能修改的验收测试（用 CODEOWNERS 保护 `tests/acceptance/`） |

## 安全提醒

- Issue 内容会进入 agent 的 prompt。这个 demo 只处理带 `loop` 标签、由监控自动开的 issue；而加标签需要 triage 权限，外人不能直接派单。公开仓库要特别注意 prompt injection。
- PAT 只授权这一个仓库，并设置过期时间。
- 想要更保守，可以设置 `AUTO_MERGE=false`，再给 production 环境加 reviewer，变成 agent 干活、人按两次按钮。

# 公开前敏感信息审核（2026-10-21）

## 结论与放行条件

本次离线审核未发现确认的真实 API Key、GitHub Token、数据库生产凭据、私钥或真实业务用户数据泄露。扫描器存在测试数据误报，已保留原始退出码并逐项复核，没有新增忽略规则。

**这不等于批准转为 Public。** 历史提交元数据包含个人邮箱，历史验收报告包含本机目录信息；负责人应先确认接受这些内容公开。许可证、依赖风险、最终 CI 与当前验收结果另见本次对应报告及验收索引。仓库可见性仍须负责人独立决定。

如果后续发现真实密钥，应立即暂停公开流程，先在对应服务吊销或轮换，再决定后续处理；仅删除当前文件不能撤回 Git 历史中的泄露。本次没有验证密钥有效性，没有调用模型或操作 Railway。

## 范围与快照

2026-10-21 从 GitHub 创建临时 mirror，同时导入本地全部 refs 作扩大覆盖。原仓库不是 shallow clone。官方扫描器、临时脚本、完整对象导出及脱敏扫描 JSON 均放在工作树外，未提交到仓库。没有强推、改写或删除 Git 历史。

远程已公告的 refs 全部纳入扫描：

| ref | SHA |
| --- | --- |
| `refs/heads/main` | `ca6c423d74dd87b9f9dddd18576c6ef4aae8da59` |
| `refs/heads/codex/docs-production-readme` | `2a2f63f5b46a21e7954f47c0d4788013a23b5aa3` |
| `refs/heads/codex/phase5-real-product` | `a8735d817022d12e4ba68fb2a15c908a1c3186ca` |
| `refs/pull/1/head` | `a8735d817022d12e4ba68fb2a15c908a1c3186ca` |
| `refs/pull/2/head` | `2a2f63f5b46a21e7954f47c0d4788013a23b5aa3` |
| `refs/pull/2/merge` | `c764bd60497e70f472cdc4100266157a65a53c4c` |

无远程 tag。扩大范围还包括本地 `main`、两个 `codex/` 分支、远程跟踪 refs、21 个工具快照 refs 和 1 个工具 diff ref。合计 35 个 refs，去重后 31 个 commit、471 个 blob、672 个可达对象；blob 原始字节共 8,074,087。仅远程分支、tags 和 PR refs 可达提交为 30 个。本地 `main` 为 `ab9a7c4aa0692a60ac23a444727668420547de3d`，不能用它代替远程 main 的审核基线。

通过 `git rev-list --objects --all` 与 `git cat-file` 导出所有可达 blob，以及完整 commit 对象（作者、提交者、提交信息）。这覆盖旧版本文件，而非只检查当前 checkout 或新增 diff。另以 `git log --all --full-history --root -m --diff-filter=D` 检查删除记录；该快照没有文件删除记录。未发现可达 `.env`、数据库文件、日志文件、私钥或归档包等敏感文件名候选。

## 成熟工具选择与结果

使用前检索了 [Gitleaks 官方仓库](https://github.com/gitleaks/gitleaks)、[发布记录](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1)、[MIT 许可证](https://github.com/gitleaks/gitleaks/blob/v8.30.1/LICENSE)，并对比了 [TruffleHog 官方说明](https://github.com/trufflesecurity/trufflehog)。Gitleaks 有持续维护的规则和 issue/PR 活动，可直接使用官方 Windows 独立二进制、离线扫描，无需增加项目依赖或服务账号。TruffleHog 也持续维护，但采用 AGPL-3.0，默认验证型工作流涉及额外网络行为，本次未集成或使用。

本次固定使用 Gitleaks `8.30.1`。官方 Windows x64 ZIP 的 SHA256 与官方 checksums 文件一致：`d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e`。扫描参数使用 `--redact=100`；显式启用官方默认规则，使用空的外部 ignore 目录和 `--ignore-gitleaks-allow`，未使用 baseline、仓库忽略文件或自定义放行规则。默认规则自身的通用过滤行为仍然存在，因此增加了人工复核与本地已知值比对。

| 检查 | 原始结果 | 复核 |
| --- | --- | --- |
| 原有 `python scripts/audit_secrets.py` | 退出 1；671 个对象；1 个文件的当前及历史命中；禁止跟踪文件为 0 | 明确的 BYOK 测试 fixture 触发 `sk-` 规则，未确认真实密钥 |
| Gitleaks Git 全 refs / full-history | 退出 1；28 个有可扫描 diff 的提交；约 1.91 MB；5 条告警 | 3 条数据库迁移 revision 字符串、2 条测试 fixture |
| Gitleaks 全 blob + 完整 commit 对象 | 退出 1；约 4.37 MB 可扫描文本；14 条告警 | 3 个迁移测试 blob、10 个同一短 fixture 的历史 blob、1 个 BYOK fixture blob；均为测试数据 |
| 与原本机 `.env` 中已知秘密值的精确比对 | 在内存中比对 1 个去重后的秘密值；全部可达 blob 与 commit 为 0 命中 | 不输出该值，不把本机 `.env` 纳入提交 |
| 补充候选检查 | 私钥头、中国手机号、中国身份证规则均为 0；凭据赋值、URL、邮箱与绝对路径候选已分类检查 | 匹配以变量表达式、测试数据、示例 URL、测试数据库配置及下面列明的元数据为主 |

可扫描文本量小于原始 blob 总量，主要因为 PNG 为压缩二进制；含 NUL 字节的 33 个 blob 全为这些 PNG，没有其他二进制文件。图片像素的人工检查单独记录在下文。没有用文件大小阈值排除目标，最大单个 blob 为 197,679 字节。

告警定位：

- `tests/test_production_compatibility.py:84`：迁移 revision 名称触发 `generic-api-key`；相关提交 `a8735d8`、`6afe0e9`、`3b021c6`。
- `tests/test_product.py:29`：短测试 fixture；最早相关提交 `be546ef`，历史上 10 个不同内容版本重复出现。
- `tests/test_provider_center_compat.py:16`：名称中明确含 disposable / fixture 的测试 key；相关提交 `b971f94`。

现有 `scripts/audit_secrets.py` 能扫描当前已跟踪文件及全 refs 可达 blob，并且仅打印位置；但 token 规则只覆盖部分 `sk-`/GitHub 格式、已知秘密值仅从当前工作树 `.env` 读取、禁止文件检查仅针对当前 tracked files。它没有检查 commit 对象/消息、广泛云服务凭据、个人信息、图片像素，也不能自动把上述测试 fixture 判为误报。因此不能将它单独作为公开放行证明，也不能把退出 1 写成通过。本次由成熟规则、全对象导出与人工检查补足，没有为了变绿屏蔽告警。

## 截图、报告与用户数据

人工查看了全部 33 个唯一 PNG blob（也是当前 tracked 的全部 33 张图片），包含 `docs/assets/`、`docs/assets/phase6-2a/`、`docs/assets/phase6-2b/`、`docs/assets/phase6-2c-1b/` 及 `docs/screenshots/`。使用临时联系表检查全部图片，并单独查看长页面。全部图片均未携带 Pillow 可读的文本/EXIF 元数据。

截图内容为项目页面、演示流程、浏览器测试账号、loopback 地址及掩码后的 fixture key；没有看到未掩码密钥、真实业务用户记录或个人邮箱。账号与测试代码中的 `example.com` 合成用户一致，注册页姓名也是测试固定样本。模型配置图展示 fixture model 与本机模拟端点；测试计划图明确显示 mock-planner，不能用这些图声称付费真实模型验证成功。历史 API/浏览器报告中的故障与失败结果保留原样，未生成或替换验收截图。

对历史 JSON、JUnit XML、测试输出及示例 URL 做了补充文本检查。发现的数据库 URL/认证数据属于示例、测试或 CI 隔离数据库配置；当前 CI 使用随机并被 mask 的临时 MySQL 凭据。没有发现提交的真实数据库、原始访问日志、真实用户导出或部署环境文件。

## 负责人需要确认的历史公开面

1. **个人邮箱元数据**：7 个可达提交的 author 或 committer 字段含同一 Gmail 地址（合计 12 个字段），其余为 GitHub/noreply 元数据。为避免扩散，本报告不复制地址。涉及 `1a6813d`、`2a2f63f`、`35743a2`、`a9ca3b1`、`b6c68ad`、`c764bd6`、`ca6c423`。这是联系人隐私风险，不是密钥；负责人必须确认接受随 Git 历史公开。后续提交可使用 GitHub noreply 邮箱，但不能消除既有记录。
2. **本机路径**：历史 JUnit XML 与阶段说明包含 Windows 用户目录、工具工作树及本地项目目录。典型位置为 `docs/assets/phase6-2b/all-tests.xml`、`docs/assets/phase6-2b/byok-tests.xml`、`docs/phase3-test-results.xml`、`docs/phase5-test-results.xml`、`docs/phase6-2a-test-results.xml`、`docs/phase6-test-results.xml`、`docs/phase6-1.md`、`docs/phase6-2b.md`、`docs/phase6-2c-1a.md`、`docs/studio-guide.md`，以及历史 README。为遵守保留历史证据与禁止改写历史的要求，未改动这些报告。该信息会暴露本机用户名/目录结构，负责人应确认接受。今后新增验收报告应使用相对路径或脱敏输出。

本报告仅给出公开面类型、位置与脱敏计数，不再次复制私人绝对路径、个人邮箱或疑似秘密值。

## 边界

审核证明的是上述时间和 refs 快照中“未发现确认泄露”，不是对所有未知格式秘密的数学保证。图像采用人工检查，没有 OCR 或隐写分析。未穷举 GitHub 隐藏/悬空对象、已删除且无可达 ref 的远端提交、fork、Actions 缓存/历史附件、Issue/PR 评论及第三方托管记录；不能把这些列为已扫描。原始扫描报告留在工作树外，不作为公开附件上传。

最终新增或修改的文件和最终提交仍须随本次验收再扫描；远程 refs 一旦变化，需要确认新增可达对象也已覆盖。公开动作应等待负责人处理/接受上述隐私项、确认许可证与内容授权，并确认最终 CI 结果。

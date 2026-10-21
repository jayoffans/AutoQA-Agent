# 公开前依赖与授权审查（2026-10-21）

本报告是本轮新增审查记录；不替代历史验收，不声明本项目版权归属，也不代负责人授予开源许可。审查对象为本次收尾分支的 `pyproject.toml`、`requirements.lock`、源码、前端资源、截图来源记录及部署配置。

## 2026-10-22 最终复查（Asia/Shanghai）

对同一最终锁定清单再次执行只读公告复查；未更改依赖版本、扫描忽略项或产品功能。

| 公告源 | 精确版本包数 | 已知漏洞 | 跳过 | 退出码 |
| --- | --- | --- | --- | --- |
| PyPI | 101 / 101 | 0 | 0 | 0 |
| OSV | 101 / 101 | 0 | 0 | 0 |

锁定清单 SHA-256 仍为 `aa97229a63d3c217a4f6c8a959674f854bb567e628c2f90479544c69fe6fac99`；本次两个 JSON 的 SHA-256 均为 `cf301e8f5bd70995cbe333d9d32d7802e05bc18262d46e33bb978b1fce75cea4`。三个定向安全更新仍为 certifi 2026.10.19、cryptography 50.0.2、Playwright 1.64.0。

本次结论仅覆盖锁定 Python 包的当前公告记录。下文宿主 Python TLS 运行时、可选 MCP 捆绑组件、构建依赖及 Scout / 完整镜像审查限制继续适用；版权归属、项目许可证与最终 Public 批准仍待负责人确认。检查全部仓内 Markdown 后，未发现本轮审计“待补录 / 进行中”等结果占位；历史阶段记录内“执行中 / 正在执行”是产品运行行为描述，予以保留。

## 结论与负责人待决项

- Python 锁定清单共 **101 个包**：20 个直接运行依赖、3 个直接开发依赖、78 个传递依赖，逐项核对许可证。初次比对原本地 venv 时，101 个包版本均与更新前 lock 一致；`pip check` 无冲突。
- 审查发现 certifi 的 TLS 信任风险、cryptography 捆绑 OpenSSL 与 Playwright 捆绑 Chromium 的安全补丁缺口，收尾分支定向更新 **certifi 2026.10.19、cryptography 50.0.2、Playwright 1.64.0**。最终清单由 PyPI 与 OSV 两个公告源各扫描 **101/101 包，0 个已知漏洞，0 个跳过项**，未使用 ignore 或自动批量升级。
- 未发现 Python 清单中有要求本项目整体改用 GPL/AGPL 的包；**certifi 与 orjson 包含 MPL-2.0 条款**，不能把全部依赖笼统标注为 MIT。依赖许可与项目原创部分的许可是不同事项。
- 仓库没有顶层 `LICENSE` / `NOTICE`，`pyproject.toml` 未声明项目许可。**负责人仍需确认原创代码与素材的公开权利，以及选择何种授权方式；本报告不构成 Public 放行。** 本轮未添加 LICENSE、版权主体姓名或未经确认的授权声明。
- 已对精确 wheel 的 OpenSSL/Chromium 版本与官方公告补充复核并定向修复；完整镜像 SBOM/CVE 扫描仍未完成。宿主 Python `ssl` 的 OpenSSL 3.5.8 与可选 Node MCP 捆绑组件仍有下文明确记录的风险，不能据 Python 包扫描声称整个运行环境安全。

## 工具选型与执行边界

使用新工具前已先检索 GitHub 的成熟实现，并核对官方文档、维护记录与许可。未为项目增加扫描脚本或运行依赖，审计工具、完整原始输出与临时环境均在工作树外。

| 工具 | 维护与许可评估 | 集成成本与本轮使用 |
| --- | --- | --- |
| [pypa/pip-audit](https://github.com/pypa/pip-audit)，2.10.1 | PyPA 项目；官方 changelog 持续维护；Apache-2.0 | 仅外部临时 venv；`--no-deps --disable-pip` 按精确 lock 查询，无自动修复、无忽略 |
| [raimon49/pip-licenses](https://github.com/raimon49/pip-licenses)，5.5.5 | 官方有稳定版与 6.x 预发布维护；MIT；元数据与多许可文件有已知边界 | 使用稳定版；导出已安装 wheel 元数据、LICENSE/NOTICE，再人工复核特殊许可及缺失文件 |
| [npm CLI](https://github.com/npm/cli)，10.9.2 | 官方持续维护的现有工具；工具本身与其捆绑组件许可见[官方 LICENSE](https://github.com/npm/cli/blob/latest/LICENSE) | 只在外部临时目录解析可选 MCP 的公开元数据；禁止 lifecycle scripts；未改项目依赖 |
| Docker Scout，1.24.0 | 已安装工具；[官方仓库](https://github.com/docker/scout-cli)声明适用 Docker 服务协议 | 对本地公共 Python base 尝试只读扫描即要求登录；未登录、未启用服务、未上传源码或镜像，因此无可用镜像扫描结果 |

pip-licenses 的跨解释器参数在含空格、中文的 Windows 路径发生解码错误；随后在被审计解释器内载入外部工具模块、恢复原始搜索路径后执行，清单限制为 lock 的 101 个包。未修改扫描器源码。工具结果再与 `importlib.metadata` 核对，避免把审计环境自己的依赖计入项目。

## 已知漏洞与最小修复证据

| 包 | 更新前结果 | 最小修复与更新后结果 |
| --- | --- | --- |
| certifi | 2026.7.22；[GHSA-6q48-6c7w-9gw2](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2) / [PYSEC-2026-4320](https://github.com/pypa/advisory-database/blob/main/vulns/certifi/PYSEC-2026-4320.yaml)，上游 2026-10-20 公告，Critical；公告未列 CVE，不能虚构 CVE 编号 | 定向更新 2026.10.19；两个公告源最终均无命中；修复源见[官方版本](https://github.com/certifi/python-certifi/releases/tag/2026.10.19) |
| cryptography / OpenSSL | 50.0.1 的 Windows wheel 实读 OpenSSL 4.0.2；原生公告不会自动出现在 PyPI 审计结果里 | 50.0.2 的精确 wheel 实读 OpenSSL 4.0.3；[官方 changelog](https://cryptography.io/en/latest/changelog/#v50-0-2)确认补丁；原许可表达式不变 |
| Playwright / Chromium | 1.63.0 实际浏览器为 153.0.8010.12 / revision 1243，早于 Google 已发布安全修复 | Python 1.64.0 对应 Chromium 156.0.8078.4 / revision 1248；新 wheel Apache-2.0，不增加 Agent/API 功能；[官方 Python release](https://github.com/microsoft/playwright-python/releases/tag/v1.64.0) |

公告受影响范围为 `>=2019.03.09,<2026.10.19`。旧 CA 包含受损中间 CA 所属的 emSign 根证书信任链；攻击者若持有相应受损私钥且位于网络路径，可伪造目标证书并读取或修改 TLS 流量。HTTPX/requests 出站请求、模型 Provider 请求等可能接触该信任库，因此属于项目有现实关联的风险；本轮没有证据表明项目已遭受攻击或模型密钥已泄露。[上游公告](https://github.com/advisories/GHSA-6q48-6c7w-9gw2)

certifi 是传递依赖，现有约束 `httpcore: certifi`、`httpx: certifi`、`requests: certifi>=2023.5.7` 均允许修复版。本轮只定向更改三个已核实安全问题对应的锁定版本，不放宽其他约束、不使用漏洞忽略规则。旧镜像与旧本地环境仍须重新安装/重建才能得到修复，修改 lock 不会追溯修改已运行的 Railway 服务。本轮未操作 Railway。

| 证据 | 更新前 | 更新后 |
| --- | --- | --- |
| pip-audit / PyPI | 101 包；1 项风险，退出码 1 | 101 包；0 漏洞 / 0 跳过，退出码 0 |
| pip-audit / OSV | 同一 PYSEC/GHSA 返回 2 个重复条目，去重后 1 项，退出码 1 | 101 包；0 漏洞 / 0 跳过，退出码 0 |

复现命令（在单独审计环境安装工具，不必加入应用环境）：

```powershell
python -m pip_audit -r requirements.lock --no-deps --disable-pip --progress-spinner off --format json --desc on --aliases on --output audit-pypi.json
python -m pip_audit -r requirements.lock --no-deps --disable-pip --progress-spinner off --vulnerability-service osv --format json --desc on --aliases on --output audit-osv.json
```

最终 `requirements.lock` 保留 101 行 CRLF，SHA-256：`aa97229a63d3c217a4f6c8a959674f854bb567e628c2f90479544c69fe6fac99`。两个最终 JSON 的 SHA-256 均为 `cf301e8f5bd70995cbe333d9d32d7802e05bc18262d46e33bb978b1fce75cea4`。原始 JSON 仅含包名、版本和公告，没有凭据；本报告只公开摘要。

## 精确原生版本、调用路径与补丁复核

`cryptography` 的官方 wheel 是[静态链接 OpenSSL 的构建](https://cryptography.io/en/latest/installation/#static-wheels)。旧 wheel 实读 `OpenSSL 4.0.2`，新 50.0.2 wheel 实读 `OpenSSL 4.0.3`；OpenSSL 3.0 及后续版本适用 [Apache-2.0](https://openssl-library.org/source/license/index.html)，与 cryptography 自身的双许可分别保留。

[OpenSSL 2026-09-29 官方公告](https://openssl-library.org/news/secadv/20260929.txt)记录了 4.0.3 修复的 DTLS、X.509、QUIC、SM2 等问题，包括 High **CVE-2026-84782**（DTLS 缓冲区读取）、Moderate **CVE-2026-84783**（共享 X.509 并发使用）、Low **CVE-2026-35189**（恶意证书 CRLDP 内存消耗）、**CVE-2026-54872**（特定非 NIST 曲线时序）等。项目对 cryptography 的直接使用位于 `autoqa/crypto.py`，仅 AESGCM 加解密，不做上述握手、证书解析或 EC 签名；未找到这些旧原生问题在此 AESGCM 路径的触发条件，仍以官方同次补丁修复捆绑库。

**这不修复宿主 Python 的 TLS 库。** 本轮 Windows 验证解释器的 `ssl.OPENSSL_VERSION` 为 **OpenSSL 3.5.8**；HTTPX / httpcore / AnyIO 的 TLS context 由 Python `ssl` 创建，与 cryptography 的静态 4.0.3 分开。上述官方公告的 CVE-2026-35189 涉及恶意 TLS 对端证书，本项目出站 HTTPS 客户端有潜在关联；3.5 分支修复于 **3.5.9**。其他 DTLS/QUIC/SM2 专用前提未在当前 HTTPX 路径发现。后续自托管应升级受支持 Python/系统运行时并实读复核，不能以升级 cryptography 或 certifi 声称已消除这一运行时风险；Docker 实际链接版本也需独立确认。

Chromium 旧版本的安全缺口依据 [Google 2026-09-15 公告](https://chromereleases.googleblog.com/2026/09/stable-channel-update-for-desktop_0541751186.html)：153.0.8010.47/.48 修复了含 **CVE-2026-91721、CVE-2026-91749、CVE-2026-91726** 的 Critical 问题。旧 153.0.8010.12 早于该修复；新 1.64.0 [官方 browsers.json](https://github.com/microsoft/playwright/blob/v1.64.0/packages/playwright-core/browsers.json)指定 156.0.8078.4。最终验收副本已实际启动并读得同一 156.0.8078.4。此版本及公告核对不等于逐项 Chromium 可利用性验证，也不保证不存在其他未公开问题。

本次选择 1.64.0 因 Python 没有已发布 1.63.x 补丁可换用，现有 `playwright>=1.51,<2` 范围允许，pyee 13.0.1 / greenlet 3.5.6 仍满足依赖；无需新增直接或传递包。已特别检查 [上游 #43242](https://github.com/microsoft/playwright/issues/43242) 的路由/跨域预检重定向回归，因为 AutoQA 会注册 context route：精确 Python wheel 已包含 `AvoidCorsURLLoaderRestartOnRedirect` 禁用补丁。使用最终 Python 1.64.0、Chromium 156.0.8078.4 在三个本地随机端口 origin 复现“带自定义头的 credentialed fetch → 预检 → 302 跨域重定向”，无 route / 有 route 均返回 **200 / 16464 bytes**，文件目标收到 2 次请求，退出码 0。未复现该回归，未给产品增加 workaround 或外网请求。

## 101 包完整许可清单

来源为精确版本已安装 wheel 的 `License-Expression` / metadata、随包许可文本，个别缺失文件再核对上游。表中链接指向精确 PyPI 版本；通用 BSD 标注经许可正文核对为 BSD-3-Clause。证据列表示已经检查的文件来源，不表示许可证法务认证。certifi 更新版沿用上游 MPL-2.0；其余 98 包版本未变；cryptography 与 Playwright 的新精确 wheel 已另行读取许可文件复核，许可表达式与原版本一致。

| 包 | 最终锁定版本 | 类型 | 许可 | 证据 |
| --- | --- | --- | --- | --- |
| [aiosqlite](https://pypi.org/project/aiosqlite/0.22.1/) | 0.22.1 | 传递 | MIT | wheel 许可文件 |
| [alembic](https://pypi.org/project/alembic/1.20.0/) | 1.20.0 | 直接 | MIT | wheel 许可文件 |
| [annotated-doc](https://pypi.org/project/annotated-doc/0.0.5/) | 0.0.5 | 传递 | MIT | wheel 许可文件 |
| [annotated-types](https://pypi.org/project/annotated-types/0.8.0/) | 0.8.0 | 传递 | MIT | wheel 许可文件 |
| [anyio](https://pypi.org/project/anyio/4.15.1/) | 4.15.1 | 传递 | MIT | wheel 许可文件 |
| [attrs](https://pypi.org/project/attrs/26.1.0/) | 26.1.0 | 传递 | MIT | wheel 许可文件 |
| [backoff](https://pypi.org/project/backoff/2.2.1/) | 2.2.1 | 传递 | MIT | wheel 许可文件 |
| [certifi](https://pypi.org/project/certifi/2026.10.19/) | 2026.10.19 | 传递 | MPL-2.0 | wheel 许可文件 |
| [cffi](https://pypi.org/project/cffi/2.1.1/) | 2.1.1 | 传递 | MIT-0 | wheel 许可文件 |
| [chardet](https://pypi.org/project/chardet/7.6.0/) | 7.6.0 | 传递 | 0BSD | wheel 许可文件 |
| [charset-normalizer](https://pypi.org/project/charset-normalizer/3.5.2/) | 3.5.2 | 传递 | MIT | wheel 许可文件 |
| [click](https://pypi.org/project/click/8.5.0/) | 8.5.0 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [colorama](https://pypi.org/project/colorama/0.4.6/) | 0.4.6 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [cryptography](https://pypi.org/project/cryptography/50.0.2/) | 50.0.2 | 直接 | Apache-2.0 OR BSD-3-Clause | wheel 许可文件 |
| [distro](https://pypi.org/project/distro/1.9.0/) | 1.9.0 | 传递 | Apache-2.0 | wheel 许可文件 |
| [fastapi](https://pypi.org/project/fastapi/0.142.2/) | 0.142.2 | 直接 | MIT | wheel 许可文件 |
| [googleapis-common-protos](https://pypi.org/project/googleapis-common-protos/1.75.5/) | 1.75.5 | 传递 | Apache-2.0 | wheel 许可文件 |
| [greenlet](https://pypi.org/project/greenlet/3.5.6/) | 3.5.6 | 传递 | MIT AND PSF-2.0 | wheel 许可文件 |
| [h11](https://pypi.org/project/h11/0.16.0/) | 0.16.0 | 传递 | MIT | wheel 许可文件 |
| [httpcore](https://pypi.org/project/httpcore/1.0.9/) | 1.0.9 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [httpcore2](https://pypi.org/project/httpcore2/2.13.1/) | 2.13.1 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [httptools](https://pypi.org/project/httptools/0.8.0/) | 0.8.0 | 传递 | MIT | wheel 许可文件 |
| [httpx](https://pypi.org/project/httpx/0.28.1/) | 0.28.1 | 直接 | BSD-3-Clause | wheel 许可文件 |
| [httpx-sse](https://pypi.org/project/httpx-sse/0.4.3/) | 0.4.3 | 传递 | MIT | wheel 许可文件 |
| [httpx2](https://pypi.org/project/httpx2/2.13.1/) | 2.13.1 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [idna](https://pypi.org/project/idna/3.20/) | 3.20 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [iniconfig](https://pypi.org/project/iniconfig/2.3.0/) | 2.3.0 | 传递 | MIT | wheel 许可文件 |
| [jsonpatch](https://pypi.org/project/jsonpatch/1.33/) | 1.33 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [jsonpointer](https://pypi.org/project/jsonpointer/3.1.1/) | 3.1.1 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [jsonschema](https://pypi.org/project/jsonschema/4.26.0/) | 4.26.0 | 直接 | MIT | wheel 许可文件 |
| [jsonschema-path](https://pypi.org/project/jsonschema-path/0.4.6/) | 0.4.6 | 直接 | Apache-2.0 | wheel 许可文件 |
| [jsonschema-specifications](https://pypi.org/project/jsonschema-specifications/2025.9.1/) | 2025.9.1 | 传递 | MIT | wheel 许可文件 |
| [langchain-core](https://pypi.org/project/langchain-core/1.6.6/) | 1.6.6 | 传递 | MIT | wheel 许可文件 |
| [langchain-protocol](https://pypi.org/project/langchain-protocol/0.0.19/) | 0.0.19 | 传递 | MIT | wheel 许可文件 |
| [langfuse](https://pypi.org/project/langfuse/4.16.0/) | 4.16.0 | 直接 | MIT | wheel 许可文件 |
| [langgraph](https://pypi.org/project/langgraph/1.2.12/) | 1.2.12 | 直接 | MIT | wheel 许可文件 |
| [langgraph-checkpoint](https://pypi.org/project/langgraph-checkpoint/4.2.0/) | 4.2.0 | 传递 | MIT | wheel 许可文件 |
| [langgraph-checkpoint-sqlite](https://pypi.org/project/langgraph-checkpoint-sqlite/3.1.1/) | 3.1.1 | 直接 | MIT | wheel 许可文件 |
| [langgraph-prebuilt](https://pypi.org/project/langgraph-prebuilt/1.1.0/) | 1.1.0 | 传递 | MIT | wheel 许可文件 |
| [langgraph-sdk](https://pypi.org/project/langgraph-sdk/0.4.5/) | 0.4.5 | 传递 | MIT | wheel 许可文件 |
| [langsmith](https://pypi.org/project/langsmith/0.14.1/) | 0.14.1 | 传递 | MIT | metadata + 上游许可 |
| [lazy-object-proxy](https://pypi.org/project/lazy-object-proxy/1.12.0/) | 1.12.0 | 传递 | BSD-2-Clause | wheel 许可文件 |
| [Mako](https://pypi.org/project/Mako/1.4.3/) | 1.4.3 | 传递 | MIT | wheel 许可文件 |
| [MarkupSafe](https://pypi.org/project/MarkupSafe/3.0.3/) | 3.0.3 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [mcp](https://pypi.org/project/mcp/1.30.0/) | 1.30.0 | 直接 | MIT | wheel 许可文件 |
| [openapi-schema-validator](https://pypi.org/project/openapi-schema-validator/0.8.1/) | 0.8.1 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [openapi-spec-validator](https://pypi.org/project/openapi-spec-validator/0.8.5/) | 0.8.5 | 直接 | Apache-2.0 | wheel 许可文件 |
| [opentelemetry-api](https://pypi.org/project/opentelemetry-api/1.45.0/) | 1.45.0 | 传递 | Apache-2.0 | wheel 许可文件 |
| [opentelemetry-exporter-http-transport](https://pypi.org/project/opentelemetry-exporter-http-transport/0.66b0/) | 0.66b0 | 传递 | Apache-2.0 | wheel 许可文件 |
| [opentelemetry-exporter-otlp-common](https://pypi.org/project/opentelemetry-exporter-otlp-common/0.66b0/) | 0.66b0 | 传递 | Apache-2.0 | wheel 许可文件 |
| [opentelemetry-exporter-otlp-proto-common](https://pypi.org/project/opentelemetry-exporter-otlp-proto-common/1.45.0/) | 1.45.0 | 传递 | Apache-2.0 | wheel 许可文件 |
| [opentelemetry-exporter-otlp-proto-http](https://pypi.org/project/opentelemetry-exporter-otlp-proto-http/1.45.0/) | 1.45.0 | 传递 | Apache-2.0 | wheel 许可文件 |
| [opentelemetry-proto](https://pypi.org/project/opentelemetry-proto/1.45.0/) | 1.45.0 | 传递 | Apache-2.0 | wheel 许可文件 |
| [opentelemetry-sdk](https://pypi.org/project/opentelemetry-sdk/1.45.0/) | 1.45.0 | 传递 | Apache-2.0 | wheel 许可文件 |
| [opentelemetry-semantic-conventions](https://pypi.org/project/opentelemetry-semantic-conventions/0.66b0/) | 0.66b0 | 传递 | Apache-2.0 | wheel 许可文件 |
| [orjson](https://pypi.org/project/orjson/3.12.0/) | 3.12.0 | 传递 | MPL-2.0 AND (Apache-2.0 OR MIT) | wheel 许可文件 |
| [ormsgpack](https://pypi.org/project/ormsgpack/1.12.2/) | 1.12.2 | 传递 | Apache-2.0 OR MIT | wheel 许可文件 |
| [packaging](https://pypi.org/project/packaging/26.3/) | 26.3 | 传递 | Apache-2.0 OR BSD-2-Clause | wheel 许可文件 |
| [pathable](https://pypi.org/project/pathable/0.5.0/) | 0.5.0 | 传递 | Apache-2.0 | wheel 许可文件 |
| [playwright](https://pypi.org/project/playwright/1.64.0/) | 1.64.0 | 直接 | Apache-2.0 | wheel 许可文件 |
| [pluggy](https://pypi.org/project/pluggy/1.6.0/) | 1.6.0 | 传递 | MIT | wheel 许可文件 |
| [prance](https://pypi.org/project/prance/25.4.8.0/) | 25.4.8.0 | 直接 | MITNFA | wheel 许可文件 |
| [protobuf](https://pypi.org/project/protobuf/7.36.2/) | 7.36.2 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [pycparser](https://pypi.org/project/pycparser/3.0/) | 3.0 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [pydantic](https://pypi.org/project/pydantic/2.13.5/) | 2.13.5 | 传递 | MIT | wheel 许可文件 |
| [pydantic_core](https://pypi.org/project/pydantic_core/2.46.5/) | 2.46.5 | 传递 | MIT | wheel 许可文件 |
| [pydantic-settings](https://pypi.org/project/pydantic-settings/2.15.0/) | 2.15.0 | 直接 | MIT | wheel 许可文件 |
| [pyee](https://pypi.org/project/pyee/13.0.1/) | 13.0.1 | 传递 | MIT | wheel 许可文件 |
| [Pygments](https://pypi.org/project/Pygments/2.21.0/) | 2.21.0 | 传递 | BSD-2-Clause | wheel 许可文件 |
| [PyJWT](https://pypi.org/project/PyJWT/2.15.1/) | 2.15.1 | 直接 | MIT | wheel 许可文件 |
| [PyMySQL](https://pypi.org/project/PyMySQL/1.2.3/) | 1.2.3 | 直接 | MIT | wheel 许可文件 |
| [pytest](https://pypi.org/project/pytest/9.1.1/) | 9.1.1 | 开发 | MIT | wheel 许可文件 |
| [pytest-asyncio](https://pypi.org/project/pytest-asyncio/1.4.0/) | 1.4.0 | 开发 | Apache-2.0 | wheel 许可文件 |
| [python-dotenv](https://pypi.org/project/python-dotenv/1.2.3/) | 1.2.3 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [python-multipart](https://pypi.org/project/python-multipart/0.0.32/) | 0.0.32 | 直接 | Apache-2.0 | wheel 许可文件 |
| [PyYAML](https://pypi.org/project/PyYAML/6.0.3/) | 6.0.3 | 直接 | MIT | wheel 许可文件 |
| [referencing](https://pypi.org/project/referencing/0.37.0/) | 0.37.0 | 传递 | MIT | wheel 许可文件 |
| [requests](https://pypi.org/project/requests/2.34.2/) | 2.34.2 | 传递 | Apache-2.0 | wheel 许可文件 |
| [requests-toolbelt](https://pypi.org/project/requests-toolbelt/1.0.0/) | 1.0.0 | 传递 | Apache-2.0 | wheel 许可文件 |
| [rfc3339-validator](https://pypi.org/project/rfc3339-validator/0.1.4/) | 0.1.4 | 传递 | MIT | wheel 许可文件 |
| [rpds-py](https://pypi.org/project/rpds-py/2026.6.3/) | 2026.6.3 | 传递 | MIT | wheel 许可文件 |
| [ruamel-yaml](https://pypi.org/project/ruamel-yaml/0.19.1/) | 0.19.1 | 传递 | MIT | wheel 许可文件 |
| [ruff](https://pypi.org/project/ruff/0.16.9/) | 0.16.9 | 开发 | MIT | wheel 许可文件 |
| [six](https://pypi.org/project/six/1.17.0/) | 1.17.0 | 传递 | MIT | wheel 许可文件 |
| [sniffio](https://pypi.org/project/sniffio/1.3.1/) | 1.3.1 | 传递 | Apache-2.0 OR MIT | wheel 许可文件 |
| [SQLAlchemy](https://pypi.org/project/SQLAlchemy/2.1.1/) | 2.1.1 | 直接 | MIT | wheel 许可文件 |
| [sqlite-vec](https://pypi.org/project/sqlite-vec/0.1.9/) | 0.1.9 | 传递 | MIT / Apache-2.0（上游双许可，见说明） | metadata + 上游许可 |
| [sse-starlette](https://pypi.org/project/sse-starlette/3.5.0/) | 3.5.0 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [starlette](https://pypi.org/project/starlette/1.7.0/) | 1.7.0 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [tenacity](https://pypi.org/project/tenacity/9.1.4/) | 9.1.4 | 传递 | Apache-2.0 | wheel 许可文件 |
| [truststore](https://pypi.org/project/truststore/0.10.4/) | 0.10.4 | 传递 | MIT | wheel 许可文件 |
| [typing_extensions](https://pypi.org/project/typing_extensions/4.16.0/) | 4.16.0 | 传递 | PSF-2.0 | wheel 许可文件 |
| [typing-inspection](https://pypi.org/project/typing-inspection/0.4.4/) | 0.4.4 | 传递 | MIT | wheel 许可文件 |
| [urllib3](https://pypi.org/project/urllib3/2.8.0/) | 2.8.0 | 传递 | MIT | wheel 许可文件 |
| [uuid_utils](https://pypi.org/project/uuid_utils/0.17.1/) | 0.17.1 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [uvicorn](https://pypi.org/project/uvicorn/0.54.0/) | 0.54.0 | 直接 | BSD-3-Clause | wheel 许可文件 |
| [watchfiles](https://pypi.org/project/watchfiles/1.3.0/) | 1.3.0 | 传递 | MIT | wheel 许可文件 |
| [websockets](https://pypi.org/project/websockets/16.1.1/) | 16.1.1 | 传递 | BSD-3-Clause | wheel 许可文件 |
| [wrapt](https://pypi.org/project/wrapt/2.5.0/) | 2.5.0 | 传递 | BSD-2-Clause | wheel 许可文件 |
| [xxhash](https://pypi.org/project/xxhash/4.0.1/) | 4.0.1 | 传递 | BSD-2-Clause | wheel 许可文件 |
| [zstandard](https://pypi.org/project/zstandard/0.25.0/) | 0.25.0 | 传递 | BSD-3-Clause | wheel 许可文件 |

### 特殊条款及重要传递依赖

- **certifi：MPL-2.0**。原 CA bundle 有 Mozilla 来源及许可说明。未修改其内容；如果分发带此组件的镜像/安装包，应保留许可、确保接收者能取得对应 MPL 源文件。MPL 是文件范围义务，不据此要求独立原创应用整体改用 MPL。[Mozilla 官方 FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)
- **orjson 3.12.0：`MPL-2.0 AND (Apache-2.0 OR MIT)`**。已核对 wheel 的三个许可文件及[精确版本 MPL 文件](https://github.com/ijl/orjson/blob/3.12.0/LICENSE-MPL-2.0)；不能把这里的 AND 丢掉。随二进制分发时需保留其全部适用条款及对应组件源码可得性。
- **Prance 25.4.8.0：metadata 为 MITNFA**。已检查 wheel `LICENSE.txt`；保留原版权与许可文本，不擅自改写成项目 MIT 声明。其解析器依赖 chardet 7.6.0 在本版本声明 0BSD，不能沿用旧版 chardet 的 LGPL 判断。
- **cryptography：Apache-2.0 OR BSD-3-Clause**；**greenlet：MIT AND PSF-2.0**；**typing_extensions：PSF-2.0**。选择适用许可或保留同时适用许可；原生组件与捆绑库需另外看分发内容。
- **sqlite-vec 0.1.9**（经 langgraph-checkpoint-sqlite 引入）：wheel metadata 声明 MIT/Apache，但未带 LICENSE 文件且 homepage 为占位值，已回查[上游 MIT](https://github.com/asg017/sqlite-vec/blob/v0.1.9/LICENSE-MIT)与[Apache 文件](https://github.com/asg017/sqlite-vec/blob/v0.1.9/LICENSE-APACHE)。仅引用 PyPI 元数据不足以完成镜像再分发材料；分发该原生扩展前应保留与实际源码版本对应的许可文本。
- **langsmith 0.14.1**（经 langchain-core 引入）：wheel metadata 为 MIT，缺少独立 LICENSE 文件，已核对[上游许可](https://github.com/langchain-ai/langsmith-sdk/blob/main/LICENSE)。上游旧 SDK 仓库已归档，后续维护须关注官方迁移信息；本轮不据此盲目升级或更换组件。
- **Langfuse Python SDK 4.16.0 为 MIT**；这不等于所有 Langfuse 服务端企业组件均适用该许可。本仓库没有把服务端企业代码作为自身源码捆绑。
- MIT/BSD/Apache 依赖通常要求分发时保留版权、许可及适用 NOTICE；Apache 修改文件还应标识修改。项目根许可证只覆盖被授权的原创部分，不替代这些第三方要求。

## 非 PyPI 运行组件与扫描限制

| 组件 | 可确认的来源 / 义务 | 本轮边界 |
| --- | --- | --- |
| setuptools（构建依赖） | `build-system.requires` 直接声明 `setuptools>=75`；[上游 MIT LICENSE](https://github.com/pypa/setuptools/blob/main/LICENSE) | 不在 101 包 lock 内，PEP 517 隔离构建动态解析版本；本轮未把未知实际版本当作已完成漏洞扫描。后续发布 wheel/固定构建供应链需记录实际版本与审计 |
| Python 3.12 / Debian bookworm base | [CPython LICENSE](https://github.com/python/cpython/blob/3.12/LICENSE)及 Debian 各软件包 copyright；包含独立的系统库许可 | `python:3.12-slim-bookworm` 是可变 tag；未取得本轮完整系统包 SBOM/CVE 结果。后续自托管或镜像分发需以实际 digest 和补丁状态复审 |
| Playwright 捆绑 Node 驱动 | wheel 内含 `driver/LICENSE`、`package/NOTICE`、`ThirdPartyNotices.txt` 等，已确认存在 | Python 包版本扫描不代表捆绑 Node/JS/原生库完整扫描；再分发须保留这些文本 |
| Chromium 浏览器 | 由 `playwright install chromium` 下载；最终验收副本实读 156.0.8078.4 / revision 1248；含独立 Chromium 及第三方条款 | 已做上文已知 Critical 公告/实际版本核对与定向升级；完整二进制许可证组件清单/CVE 扫描未完成，后续镜像分发仍须复核 |
| MySQL 8.4 | [官方许可说明](https://dev.mysql.com/doc/refman/8.4/en/introduction.html)提供 GPL 与商业授权路径；[官方镜像说明](https://github.com/docker-library/docs/blob/master/mysql/README.md#license)强调依赖也有各自许可 | Compose 仅引用独立镜像，不把 MySQL 改授本项目许可；若分发包含 MySQL 的成品，应满足适用源码/许可义务。本轮没有完整 MySQL/OS CVE SBOM |
| 可选 Chrome DevTools MCP | 固定 `chrome-devtools-mcp@1.10.1`；[精确版本 LICENSE](https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/chrome-devtools-mcp-v1.10.1/LICENSE)为 Apache-2.0；默认关闭 | 见下方可选工具复核；项目未保存 npm lock / tarball integrity，宿主 Node 与可选 peer 组件也未完整审计 |

Docker Scout 的尝试命令为 `docker scout cves --format sarif --output <外部临时文件> local://python:3.12-slim-bookworm`，结果为要求 Docker 登录、退出码 1；没有生成漏洞结论。未以忽略规则、空报告或“构建通过”代替镜像漏洞扫描。

### 可选 Node MCP 复核

`AUTOQA_MCP_ENABLED` 默认关闭，Production / Demo 的配置约束还会关闭该集成。默认参数固定 1.10.1 并设置 `--no-usage-statistics`。仅人工启用时才由 npx 启动，不能把 Node 工具等同于 Python 的 `mcp` SDK。

已核对上游 [CVE-2026-53765 / GHSA-3pvj-jv98-qhjq](https://github.com/ChromeDevTools/chrome-devtools-mcp/security/advisories/GHSA-3pvj-jv98-qhjq)（POSIX daemon PID 路径符号链接写入）与 [GHSA-8qf9-62x2-82pp](https://github.com/ChromeDevTools/chrome-devtools-mcp/security/advisories/GHSA-8qf9-62x2-82pp)（roots 路径符号链接绕过）：两者受影响到 1.0.1，修复于 1.1.0，因此默认固定的 1.10.1 不在这两项版本范围内。这是版本范围复核，不能推导全部 npm 传递包均无风险。

可选工具在外部临时目录按精确 1.10.1 执行 `npm install --package-lock-only --ignore-scripts --no-audit` 后，`npm audit --package-lock-only --ignore-scripts --json` 仅识别顶层 **1 个包 / 0 漏洞**。这不是全部通过：npm 发布包已经 bundle 其依赖，不把它们列为可供 npm audit 扫描的常规依赖。

已按 npm registry 的 `dist.integrity` 核验 tarball SHA-512，仅读取而不执行包。其 `build/src/third_party/THIRD_PARTY_NOTICES` 有 **60 条组件记录**：MIT 39、ISC 7、Apache-2.0 9、BSD-2-Clause 1、BSD-3-Clause 2、MPL-2.0 1（axe-core）、0BSD 1。接收/分发该工具须保留该完整 notices，不能只留顶层 Apache 文本。

事先评估了 [google/osv-scanner](https://github.com/google/osv-scanner)（持续维护、Apache-2.0、多生态；本轮未安装），随后直接按 [OSV 官方批量查询 API](https://google.github.io/osv.dev/post-v1-querybatch/)向公告服务提交 notices 内公开包名/版本，未上传源码、镜像或私有数据。**59 个语义版本组件返回 8 个公告命中；1 个 devtools-frontend Git commit 版本不按 npm semver 扫描，明确跳过**。未用 ignore 掩盖命中。

| 捆绑组件 / 版本 | 公告及修复版本 | 本项目路径的适用性 |
| --- | --- | --- |
| @modelcontextprotocol/server 2.0.0 | [CVE-2026-111768](https://github.com/modelcontextprotocol/typescript-sdk/security/advisories/GHSA-46jr-4wx8-v453)、[CVE-2026-111770](https://github.com/modelcontextprotocol/typescript-sdk/security/advisories/GHSA-jm63-cq47-j5jp)、[CVE-2026-111771](https://github.com/modelcontextprotocol/typescript-sdk/security/advisories/GHSA-rvq5-wwqv-78pq)；SDK >=2.3.0，部分项还需配置修复 | 官方前提为 HTTP 服务输入验证、多客户端共享实例或 HTTP bearer auth；AutoQA bridge 使用本地 stdio，不暴露该 HTTP/OAuth 服务路径 |
| @modelcontextprotocol/client 2.0.0 | [CVE-2026-111769](https://github.com/modelcontextprotocol/typescript-sdk/security/advisories/GHSA-6prh-2h8m-c8cw)修复于 >=2.3.0；[CVE-2026-104850](https://github.com/modelcontextprotocol/typescript-sdk/security/advisories/GHSA-6qxp-vccf-f47h)修复于 >=2.2.0 | 前提是 HTTP/SSE/OAuth 客户端；本项目只经 Python stdio 调本地 DevTools，不配置这些认证流 |
| lodash-es 4.17.21 | [CVE-2025-13465](https://github.com/lodash/lodash/security/advisories/GHSA-xxjr-mmjv-4gpg)修复于 4.17.23；[CVE-2026-2950](https://github.com/lodash/lodash/security/advisories/GHSA-f23m-r3pf-42rh)与 [CVE-2026-4800](https://github.com/lodash/lodash/security/advisories/GHSA-r5fr-rjxr-66jc)修复于 4.18.0 | 需不可信数据进入 unset/omit 路径或 template imports 键；未完成 bundle 内易受影响函数可达性证明，不能声明无影响 |

当前可选 MCP 默认关闭，本轮未新增启用、替换包或掩盖扫描项。**启用该可选工具前仍需上游修复 bundle、复验精确发行包及可达性**；自行给宿主 npm 添加 override 不能改掉工具已打包代码。上述残余风险不属于 Python 101 包“0 漏洞”结论的覆盖范围。

<details>
<summary>可选工具 60 条捆绑许可记录（来自精确发行包 notices）</summary>

| 组件 | 版本 / commit | 许可 |
| --- | --- | --- |
| core-js | 3.50.0 | MIT |
| zod | 4.6.2 | MIT |
| cliui | 9.0.1 | ISC |
| ansi-regex | 6.2.2 | MIT |
| strip-ansi | 7.1.2 | MIT |
| get-east-asian-width | 1.6.0 | MIT |
| emoji-regex | 10.6.0 | MIT |
| string-width | 7.2.0 | MIT |
| ansi-styles | 6.2.3 | MIT |
| wrap-ansi | 9.0.2 | MIT |
| escalade | 3.2.0 | MIT |
| yargs-parser | 22.0.0 | ISC |
| yargs | 18.1.0 | MIT |
| y18n | 5.0.8 | ISC |
| get-caller-file | 2.0.5 | ISC |
| semver | 7.8.5 | ISC |
| @modelcontextprotocol/server | 2.0.0 | MIT |
| @modelcontextprotocol/core | 2.0.0 | MIT |
| @modelcontextprotocol/client | 2.0.0 | MIT |
| cross-spawn | 7.0.6 | MIT |
| isexe | 2.0.0 | ISC |
| which | 2.0.2 | ISC |
| path-key | 3.1.1 | MIT |
| shebang-regex | 3.0.0 | MIT |
| shebang-command | 2.0.0 | MIT |
| pkce-challenge | 5.0.0 | MIT |
| uri-js | 4.4.1 | BSD-2-Clause |
| fast-deep-equal | 3.1.3 | MIT |
| ajv | 6.15.0 | MIT |
| json-schema-traverse | 0.4.1 | MIT |
| fast-json-stable-stringify | 2.1.0 | MIT |
| puppeteer-core | 25.11.0 | Apache-2.0 |
| @puppeteer/browsers | 3.2.2 | Apache-2.0 |
| ws | 8.21.3 | MIT |
| marked | 18.0.4 | MIT |
| devtools-frontend | 61277feec01840bfa7cffb21d90bed500f973c70 | Apache-2.0 |
| @formatjs/fast-memoize | 2.2.0 | MIT |
| @formatjs/icu-messageformat-parser | 2.6.2 | MIT |
| @formatjs/icu-skeleton-parser | 1.6.2 | MIT |
| @paulirish/trace_engine | 0.0.65 | BSD-3-Clause |
| @sentry/node | 10.58.0 | MIT |
| axe-core | 4.12.1 | MPL-2.0 |
| csp_evaluator | 1.1.8 | Apache-2.0 |
| debug | 4.3.4 | MIT |
| http-link-header | 1.1.1 | MIT |
| intl-messageformat | 10.5.3 | BSD-3-Clause |
| js-library-detector | 6.7.0 | MIT |
| legacy-javascript | 0.0.1 | Apache-2.0 |
| lighthouse-logger | 2.0.2 | Apache-2.0 |
| lighthouse-stack-packs | 1.12.3 | Apache-2.0 |
| lodash-es | 4.17.21 | MIT |
| lookup-closest-locale | 6.2.0 | MIT |
| marky | 1.2.2 | Apache-2.0 |
| ms | 2.1.2 | MIT |
| puppeteer-core | 25.3.0 | Apache-2.0 |
| robots-parser | 3.0.1 | MIT |
| third-party-web | 0.26.2 | MIT |
| tldts-core | 7.4.9 | MIT |
| tldts-icann | 7.4.9 | MIT |
| tslib | 2.6.2 | 0BSD |

</details>

## 原创代码及素材授权来源

- 当前源码、前端 HTML/CSS/JS、内置 Demo 页面随项目 Git 提交演进；作者显示为同一 Git 身份，但**提交署名不是完整的版权权属证明**。负责人须确认不存在雇主/客户保密代码、未获授权的复制片段及其他共同权利人限制。
- 跟踪的视觉素材为 **33 张 PNG 截图**，未发现跟踪的外部字体文件、图标库、库存照片、音视频或前端 CDN 资源依赖。样式使用系统字体、文本及 CSS。这里只能说明仓库里未识别到此类素材，不能凭文件类型证明全部原创。
- 截图来源有仓内证据：`docs/demo-guide.md` 记录 `docs/screenshots/` 来自隔离 Demo；`scripts/verify_studio.py` / `scripts/verify_public_demo.py` 调用真实 Playwright screenshot；`tests/test_provider_center_browser.py` 与 `tests/test_test_planner_browser.py` 分别生成模型中心及计划截图。历史阶段文档保留各自受控测试口径。
- 不将历史截图重新生成成“验证证据”。负责人仍须确认截图仅含可公开测试数据、产品素材权利可公开、没有第三方受限品牌/业务信息；敏感信息扫描与图像人工复核另见本轮安全报告。
- 未找到记录外部借用代码/素材授权的独立登记文件，因此**不能肯定全部源码和截图已完成权属确认**。若存在外部片段，应补充真实来源、适用版本/许可与修改说明，不能事后凭空补造授权。

## 许可证建议与 Public 条件

推荐负责人在确认原创部分权利后选用 **MIT**：文本简短、允许招聘方阅读与合法复用，保留版权与许可说明，适合以 GitHub 作为展示入口的个人作品。若负责人希望明确贡献者专利授权，可选 **Apache-2.0**，相应增加变更/NOTICE 管理要求。依据为 [GitHub MIT 说明](https://choosealicense.com/licenses/mit/)与[Apache-2.0 说明](https://choosealicense.com/licenses/apache-2.0/)。两者均不覆盖第三方组件的原许可或商标。

负责人需要确认：拥有授权所需权利；许可证选择；版权主体及年份；文档/截图是否与代码同许可、是否有例外；对二进制/镜像审查限制的处理。之后才可添加真实 LICENSE 与项目 metadata。若决定只公开展示而保留权利，应明确记录该决定，也不能把无 LICENSE 称为“已开源”；[GitHub 无许可说明](https://choosealicense.com/no-permission/)区分了可浏览/平台 fork 与一般复用授权。

本报告的依赖层面结果为：已核实的三个应用依赖安全缺口已定向修复，Python 包公告源复扫通过；宿主 Python TLS 运行时与可选 Node 工具仍有明确记录的剩余风险。原创权属与项目授权方式待负责人确认，系统/二进制完整分发审查未完成。最终 Public 放行还须结合独立安全、公开内容及 CI 验收结果，由负责人批准。

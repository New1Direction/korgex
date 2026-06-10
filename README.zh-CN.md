<p align="center">
  <img src="docs/images/banner.jpg" alt="korgex —— 可验证的编程智能体" width="100%">
</p>

<p align="center">
  <a href="https://pypi.org/project/korgex/"><img src="https://img.shields.io/pypi/v/korgex?color=3fb950&label=pypi" alt="PyPI 版本"></a>
  <a href="https://pypi.org/project/korgex/"><img src="https://img.shields.io/pypi/pyversions/korgex?color=2dd4bf" alt="Python 版本"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="许可证: MIT"></a>
</p>

<p align="center">
  <a href="README.md">English</a> · <b>简体中文</b> · <a href="README.zh-TW.md">繁體中文</a>
</p>

# korgex

**一个跑在终端里、并且全程“留底”的 AI 编程搭档。**

用大白话告诉 korgex 你想做什么——*“修好这个失败的测试”“加一个健康检查接口”*——它就会读你的代码、动手修改、跑测试,并把它做的每一步清清楚楚展示给你。它免费、开源,兼容你喜欢的任意 AI(Claude、ChatGPT、Gemini、Grok,或跑在你自己电脑上的私有模型),所以你永远不会被某一家公司绑死。

**它为什么不一样:** korgex 做的每一件事都会写进一份防篡改的记录,供你日后查验。哪怕有人只改了其中一个字符,korgex 都能证明。这是一个你可以*审计*、而不只是“但愿能信”的编程助手。

> 📖 **完整中文文档**:[简体中文文档](https://korgex-docs.pages.dev/zh-CN/docs) · 在中国大陆?见[在中国安装](https://korgex-docs.pages.dev/zh-CN/docs/install-china)

```bash
$ korgex "加一个 /healthz 接口,返回 200 并带上运行时长"
➤ Read(file_path=/app/routes.py)
➤ Edit(file_path=/app/routes.py, old_string=..., new_string=...)
➤ Bash(command=pytest tests/test_routes.py -q)
✓ 已添加 GET /healthz,返回 {"status": "ok", "uptime_seconds": ...}

$ korgex verify
  ✓ 账本完整 —— 7 条事件,哈希链 + 因果 DAG 校验通过
```

## 安装

```bash
pip install -U korgex          # 或安装为独立的全局 CLI:
uv tool install korgex@latest
```

需要 Python ≥ 3.10。**在中国大陆**用国内镜像更快:

```bash
pip install -U korgex -i https://pypi.tuna.tsinghua.edu.cn/simple
```

更多镜像(cargo / npm 等)见[在中国安装](https://korgex-docs.pages.dev/zh-CN/docs/install-china)。

## 快速开始

```bash
# 1. 接入一个厂商(交互式,保存到 ~/.korgex/config.json)
korgex setup
#    ……或直接导出一个密钥,下面任意一种都行:
export ANTHROPIC_API_KEY="sk-ant-..."     # Claude
export OPENAI_API_KEY="sk-proj-..."       # ChatGPT
export KORGEX_API_KEY="..." KORGEX_API_URL="http://your-gpu-box:8000/v1"   # 自建模型

# 2. 给它一个需求直接跑
korgex "修好 tests/test_auth.py 里失败的测试"

# 3. 或指定模型 / 模式
korgex --model claude-sonnet-4-6 "重构 src/handler.py"
korgex --mode plan "为这个 API 设计一个限流器"

# 4. 事后证明这次运行没被改过
korgex verify
```

不带提示词直接运行 `korgex` 即可进入交互式 REPL。

## REPL —— 住在里面

直接运行 `korgex` 进入流式、多轮的会话:它会连接你的 MCP 服务、读取你的项目规则,并为每个会话保留可回退(rewind)的记录。

- **斜杠命令**:`/loop`(无人值守地刷完一串任务)、`/diff`、`/rewind`、`/plan`、`/model`、`/verify`、`/cost`、`/resume`、`/skills` 等。
- **`@文件`**:把文件内容拉进本轮对话——`重构 @src/auth.py,改用 @src/db.py`。
- **`!命令`**:就地执行 shell——`!git status`、`!pytest -q`。
- **项目规则**:`korgex init` 会生成 `AGENTS.md`,korgex 每次会话都会自动读取它(以及上层目录的 `AGENTS.md` 和 `.korgex/rules/*.md`),按你的规范来做事。

## 可验证的认知(核心)

**用大白话说:** korgex 会把它做的每件事记成一本流水账——读过的每个文件、运行过的每条命令。每一条都和前一条紧紧相扣,就像链条上的扣环;只要有人事后改动、新增或删除哪怕一条,链条就会明显断裂。于是你得到的是关于 AI 到底做了什么的、诚实且可核验的证明——用于审计、合规、排查问题,或只是图个安心。据我们所知,目前还没有别的编程智能体做到这一点。

底层实现:每次运行都写入一本**防篡改的因果账本**(而不是一份不透明的日志)。每条事件既与前一条**哈希相扣**(`prev_hash`/`entry_hash`),又与触发它的原因**因果相连**(`triggered_by`)——所以整段会话都能被密码学地证明完整,任何改动、删除、重排或拼接都会被发现并精确定位到出问题的那一条。

```bash
korgex verify                 # 证明记录在案的运行没被改过(退出码 0/1,可用于 CI)
korgex trace                  # 因果轨迹——它做了什么 + 每一步是被什么触发的
korgex why src/auth.py        # 从一处文件改动,沿因果链回溯到对应的提示词
korgex cost                   # 这次会话的预估花费,按记录在案的 token 数算
export KORG_LEDGER_HMAC_KEY=… # 让链条从“防篡改”升级为“防伪造”
```

**交一份凭证给别人。** `korgex receipt` 能生成一个可随身携带的单文件,证明某次运行做了什么——它能**离线**校验,或者直接用 `--html` 打开,在浏览器里看着它自我重新校验。可证明的交付物,而不是一张截图。

```bash
korgex receipt --claim "上线 /healthz + 测试通过" --sign --html receipt.html
korgex receipt verify receipt.korgreceipt.json   # ✓ 有效 / ✗ 无效(可用于 CI 把关)
```

**在 CI 里把关。** 把 `verify-ledger` 这个 GitHub Action 放进任意仓库,一旦某个智能体的账本或凭证通不过校验,就让构建失败——对生成它的工具做到“零信任”。

## 能力(强力功能默认关闭、需手动开启)

除了核心的“文件 / shell / 搜索”循环,korgex 还内置了几套更深的系统。其中风险较高的都**默认关闭,一个环境变量即可开启**,而且每一个开启后仍然会记录到可核验的账本。

- **CodeAct —— 代码即动作**(`KORGEX_CODEACT_ENABLE=1`):一个持久、带“燃料”计量的 Python 内核,模型直接写代码来调用工具。开启后默认进 **OS 沙箱**(Linux/bubblewrap、macOS/Seatbelt)。
- **多智能体编排**(`KORGEX_PARALLEL_AGENTS` + `Orchestrate` 工具):并发跑一张子智能体的 DAG,账本原生、可核验。
- **可审计的网络抓包**(`KORGEX_NETCAPTURE_ENABLE=1`):在本地抓取你自己写的应用的 HTTP(S) 交互,密钥在记录前先被脱敏。
- **可验证的浏览器**:CDP 驱动的“快照→操作”自动化,全程记录,隐身模式可选。
- **远程签名**:用一台**你自己掌控**的签名服务给账本盖章,签名私钥可放在 agent 主机之外。
- **可验证的智能体总线**:多个 agent 通过 Ed25519 签名、防篡改的 korg 账本协作——“谁说了什么”是签名,而不是一句空口。
- **跨会话记忆 + 漂移检测**:记住的事实会锚定到来源的 sha256 基线,来源变化时给出精确的“过期”信号。
- **本地模型**(`korgex local`):按你机器的 CPU/内存/显卡推荐合适的本地模型,可一键把本地 **Ollama** 模型设为默认。

## 安全与沙箱

- **破坏性命令护栏**(默认开启):一道“白名单优先”的底线,拒绝明显具有破坏性的 shell 命令,拦截会记成一条防篡改的账本事件。
- **外发 / 外泄护栏**(默认以“标记”模式开启):检查经由外发工具离开本机的数据,识别密钥形态与大块编码数据;`flag` 仅警告并记录,`redact` 在外发前脱敏,`block` 直接拒绝。
- **沙箱执行**(`KORGEX_SANDBOX`)、**编辑确认**(关键文件改动会先给 diff 等你点头)。
- **强力功能一律默认关闭**:CodeAct、网络抓包、远程签名、浏览器隐身——都要你亲自打开才生效。

## 多模型路由

`--mode` 会按工作类型挑选合适的模型(`plan` / `execute` / `explore` / `review` / `debug` / `research`);显式 `--model` 始终优先。默认是 Sonnet 4.6。

## MCP 集成

korgex 内置原生的 MCP(模型上下文协议)客户端:`mcp.json` 里的任意 MCP 服务都会成为 agent 的工具。用 `korgex mcp` 管理(stdio 或远程 url+鉴权)。korgex 本身**也是一个 MCP 服务**(`korgex mcp-server`),把“可验证认知”的能力(`korg_verify` / `korg_audit` / `korg_import`)暴露给任意 MCP 宿主。

## 完整参考

完整的 CLI 参考、环境变量、架构图与开发指南,见[**完整文档**](https://korgex-docs.pages.dev/zh-CN/docs) 或[英文 README](README.md)。

## 相关项目

- **[korg](https://github.com/New1Direction/korg)** · **[korgchat](https://github.com/New1Direction/korgchat)** —— korg 账本周边更广的生态。
- **[Model Context Protocol](https://modelcontextprotocol.io/)** —— korgex 同时作为客户端与服务端实现的开放 MCP 标准。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。

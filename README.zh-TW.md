<p align="center">
  <img src="docs/images/banner.jpg" alt="korgex —— 可驗證的編程代理" width="100%">
</p>

<p align="center">
  <a href="https://pypi.org/project/korgex/"><img src="https://img.shields.io/pypi/v/korgex?color=3fb950&label=pypi" alt="PyPI 版本"></a>
  <a href="https://pypi.org/project/korgex/"><img src="https://img.shields.io/pypi/pyversions/korgex?color=2dd4bf" alt="Python 版本"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="授權: MIT"></a>
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <b>繁體中文</b>
</p>

# korgex

**一個跑在終端裡、而且全程「留底」的 AI 編程夥伴。**

用白話告訴 korgex 你想做什麼——*「修好這個失敗的測試」「加一個健康檢查端點」*——它就會讀你的程式碼、動手修改、跑測試,並把它做的每一步清清楚楚攤給你看。它免費、開源,相容你喜歡的任意 AI(Claude、ChatGPT、Gemini、Grok,或跑在你自己電腦上的私有模型),所以你永遠不會被某一家公司綁死。

**它為什麼不一樣:** korgex 做的每一件事都會寫進一份防竄改的紀錄,供你日後查驗。哪怕有人只改了其中一個字元,korgex 都能證明。這是一個你可以*稽核*、而不只是「但願可信」的編程助手。

> 📖 **完整中文文件**:[繁體中文文件](https://korgex-docs.pages.dev/zh-TW/docs) · 在中國大陸?見[在中國安裝](https://korgex-docs.pages.dev/zh-TW/docs/install-china)

```bash
$ korgex "加一個 /healthz 端點,回傳 200 並帶上執行時間"
➤ Read(file_path=/app/routes.py)
➤ Edit(file_path=/app/routes.py, old_string=..., new_string=...)
➤ Bash(command=pytest tests/test_routes.py -q)
✓ 已新增 GET /healthz,回傳 {"status": "ok", "uptime_seconds": ...}

$ korgex verify
  ✓ 帳本完整 —— 7 條事件,雜湊鏈 + 因果 DAG 校驗通過
```

## 安裝

```bash
pip install -U korgex          # 或安裝為獨立的全域 CLI:
uv tool install korgex@latest
```

需要 Python ≥ 3.10。**在中國大陸**用國內鏡像更快:

```bash
pip install -U korgex -i https://pypi.tuna.tsinghua.edu.cn/simple
```

更多鏡像(cargo / npm 等)見[在中國安裝](https://korgex-docs.pages.dev/zh-TW/docs/install-china)。

## 快速開始

```bash
# 1. 接上一個廠商(互動式,儲存到 ~/.korgex/config.json)
korgex setup
#    ……或直接匯出一個金鑰,下面任一種都行:
export ANTHROPIC_API_KEY="sk-ant-..."     # Claude
export OPENAI_API_KEY="sk-proj-..."       # ChatGPT
export KORGEX_API_KEY="..." KORGEX_API_URL="http://your-gpu-box:8000/v1"   # 自架模型

# 2. 給它一個需求直接跑
korgex "修好 tests/test_auth.py 裡失敗的測試"

# 3. 或指定模型 / 模式
korgex --model claude-sonnet-4-6 "重構 src/handler.py"
korgex --mode plan "為這個 API 設計一個速率限制器"

# 4. 事後證明這次執行沒被改過
korgex verify
```

不帶提示詞直接執行 `korgex` 即可進入互動式 REPL。

## REPL —— 住在裡面

直接執行 `korgex` 進入串流、多輪的工作階段:它會連接你的 MCP 伺服器、讀取你的專案規則,並為每個工作階段保留可回退(rewind)的紀錄。

- **斜線指令**:`/loop`(無人值守地刷完一串任務)、`/diff`、`/rewind`、`/plan`、`/model`、`/verify`、`/cost`、`/resume`、`/skills` 等。
- **`@檔案`**:把檔案內容拉進本輪對話——`重構 @src/auth.py,改用 @src/db.py`。
- **`!指令`**:就地執行 shell——`!git status`、`!pytest -q`。
- **專案規則**:`korgex init` 會產生 `AGENTS.md`,korgex 每次工作階段都會自動讀取它(以及上層目錄的 `AGENTS.md` 和 `.korgex/rules/*.md`),照你的規範做事。

## 可驗證的認知(核心)

**用白話說:** korgex 會把它做的每件事記成一本流水帳——讀過的每個檔案、執行過的每一條指令。每一條都和前一條緊緊相扣,就像鏈條上的扣環;只要有人事後改動、新增或刪除哪怕一條,鏈條就會明顯斷裂。於是你得到的是關於 AI 到底做了什麼的、誠實且可核驗的證明——用於稽核、合規、排查問題,或只是圖個安心。據我們所知,目前還沒有別的編程代理做到這一點。

底層實作:每次執行都寫入一本**防竄改的因果帳本**(而不是一份不透明的紀錄檔)。每條事件既與前一條**雜湊相扣**(`prev_hash`/`entry_hash`),又與觸發它的原因**因果相連**(`triggered_by`)——所以整段工作階段都能被密碼學地證明完整,任何改動、刪除、重排或拼接都會被發現並精確定位到出問題的那一條。

```bash
korgex verify                 # 證明記錄在案的執行沒被改過(離開碼 0/1,可用於 CI)
korgex trace                  # 因果軌跡——它做了什麼 + 每一步是被什麼觸發的
korgex why src/auth.py        # 從一處檔案改動,沿因果鏈回溯到對應的提示詞
korgex cost                   # 這次工作階段的預估花費,依記錄在案的 token 數算
export KORG_LEDGER_HMAC_KEY=… # 讓鏈條從「防竄改」升級為「防偽造」
```

**交一份憑證給別人。** `korgex receipt` 能產生一個可隨身攜帶的單一檔案,證明某次執行做了什麼——它能**離線**校驗,或者直接用 `--html` 打開,在瀏覽器裡看著它自我重新校驗。可證明的交付物,而不是一張截圖。

```bash
korgex receipt --claim "上線 /healthz + 測試通過" --sign --html receipt.html
korgex receipt verify receipt.korgreceipt.json   # ✓ 有效 / ✗ 無效(可用於 CI 把關)
```

**在 CI 裡把關。** 把 `verify-ledger` 這個 GitHub Action 放進任意儲存庫,一旦某個代理的帳本或憑證通不過校驗,就讓建置失敗——對產生它的工具做到「零信任」。

## 能力(強力功能預設關閉、需手動開啟)

除了核心的「檔案 / shell / 搜尋」循環,korgex 還內建了幾套更深的系統。其中風險較高的都**預設關閉,一個環境變數即可開啟**,而且每一個開啟後仍然會記錄到可核驗的帳本。

- **CodeAct —— 程式碼即動作**(`KORGEX_CODEACT_ENABLE=1`):一個持久、帶「燃料」計量的 Python 核心,模型直接寫程式碼來呼叫工具。開啟後預設進 **OS 沙箱**(Linux/bubblewrap、macOS/Seatbelt)。
- **多代理編排**(`KORGEX_PARALLEL_AGENTS` + `Orchestrate` 工具):並行跑一張子代理的 DAG,帳本原生、可核驗。
- **可稽核的網路擷取**(`KORGEX_NETCAPTURE_ENABLE=1`):在本機擷取你自己寫的應用的 HTTP(S) 交互,金鑰在記錄前先被遮蔽。
- **可驗證的瀏覽器**:CDP 驅動的「快照→操作」自動化,全程紀錄,隱身模式可選。
- **遠端簽章**:用一台**你自己掌控**的簽章服務替帳本蓋章,簽章私鑰可放在 agent 主機之外。
- **可驗證的代理匯流排**:多個 agent 透過 Ed25519 簽章、防竄改的 korg 帳本協作——「誰說了什麼」是簽章,而不是一句空口。
- **跨工作階段記憶 + 漂移偵測**:記住的事實會錨定到來源的 sha256 基線,來源變動時給出精確的「過期」訊號。
- **本機模型**(`korgex local`):依你機器的 CPU/記憶體/顯示卡推薦合適的本機模型,可一鍵把本機 **Ollama** 模型設為預設。

## 安全與沙箱

- **破壞性指令護欄**(預設開啟):一道「白名單優先」的底線,拒絕明顯具破壞性的 shell 指令,攔截會記成一條防竄改的帳本事件。
- **外送 / 外洩護欄**(預設以「標記」模式開啟):檢查經由外送工具離開本機的資料,識別金鑰形態與大量編碼資料;`flag` 僅警告並記錄,`redact` 在外送前遮蔽,`block` 直接拒絕。
- **沙箱執行**(`KORGEX_SANDBOX`)、**編輯確認**(關鍵檔案改動會先給 diff 等你點頭)。
- **強力功能一律預設關閉**:CodeAct、網路擷取、遠端簽章、瀏覽器隱身——都要你親自打開才生效。

## 多模型路由

`--mode` 會依工作類型挑選合適的模型(`plan` / `execute` / `explore` / `review` / `debug` / `research`);顯式 `--model` 始終優先。預設是 Sonnet 4.6。

## MCP 整合

korgex 內建原生的 MCP(模型上下文協定)用戶端:`mcp.json` 裡的任意 MCP 伺服器都會成為 agent 的工具。用 `korgex mcp` 管理(stdio 或遠端 url+驗證)。korgex 本身**也是一個 MCP 伺服器**(`korgex mcp-server`),把「可驗證認知」的能力(`korg_verify` / `korg_audit` / `korg_import`)暴露給任意 MCP 宿主。

## 完整參考

完整的 CLI 參考、環境變數、架構圖與開發指南,見[**完整文件**](https://korgex-docs.pages.dev/zh-TW/docs) 或[英文 README](README.md)。

## 相關專案

- **[korg](https://github.com/New1Direction/korg)** · **[korgchat](https://github.com/New1Direction/korgchat)** —— korg 帳本周邊更廣的生態。
- **[Model Context Protocol](https://modelcontextprotocol.io/)** —— korgex 同時作為用戶端與伺服端實作的開放 MCP 標準。

## 授權

MIT —— 見 [LICENSE](LICENSE)。

# EdgeAgent

可換模型的 Windows 本機編程 Agent。開啟專案後，Agent 可以讀取程式、提出修改、執行驗證，並在同一個介面審閱結果。SilentTalk 是使用共享推論服務的語音角色玩具。

## 使用

雙擊 `開啟 EdgeAgent.cmd`，或執行 `edge-agent`。首次啟動自動安裝依賴；`開啟 EdgeAgent.cmd --check` 只部署與檢查，不開啟介面。需要 Windows x64；有可用 NVIDIA 驅動時使用 CUDA，否則使用 CPU。

預設模型為 `Qwen3.5-4B-Q4_K_M.gguf`、16,384 token context、關閉 thinking。RTX 3050 Ti 4 GB 實測整機顯存約 3.1 GB，兩個小型 Python 修碼案例通過獨立驗證。2B 模型仍可下載切換，較小模型在本次測試出現重複無效編輯；上述結果不代表大型專案能力保證。

在「模型模組」中選擇已下載的 GGUF，或填入 Hugging Face 模型庫並下載。下載驗證 SHA256，完成後才能切換；模型切換失敗會嘗試恢復原模型。工作進行中或 SilentTalk 租借中會拒絕切換，避免中途更換推論模型。模型檔名與實際來源保持一致。

Agent 的寫檔、編輯及 PowerShell 執行由介面批准，可批准單次操作或本輪工作。Plan 模式只提供讀取工具。批准介面不是作業系統沙盒；執行的程式具有目前帳號的權限。`.env`、`settings.json`、`personas.json` 和 `character_state.json` 禁止透過檔案編輯工具修改。

相同操作連續重複而沒有進展，或本輪工具呼叫超過 64 次時，工作會停止並保留修改與上下文。失敗後排隊工作暫停，從更多操作選擇「繼續排隊」才能恢復；每個對話有自己的佇列。

對話、附件、工具結果、摘要及修改審閱資料只在記憶體。沒有 JSONL session、對話歷史或 shell 輸出暫存檔。關閉服務後無法復原對話；使用者要求修改的專案檔案會正常保存。只持久化模型、依賴及模型設定。

## 架構與責任

| 元件 | 唯一責任 |
| --- | --- |
| Pi coding-agent SDK 1.0.0 | Agent 執行迴圈、工具、摘要、重試與取消 |
| llama.cpp b11193 router | 模型子程序、載入與卸載、LoRA；最多一個模型實例 |
| `edge_agent/agent_bridge.mjs` | Pi 的本機 provider、RAM session、批准與輸出政策 |
| `edge_agent/chat.py` | 官方 RPC 與現有 UI 事件的轉接、RAM checkpoint |
| `edge_agent/service.py` | 單一 UI／控制端點、模型設定與租借協調 |
| `edge_agent/deploy.py` | 校驗資產、安裝固定版本 runtime；npm lockfile 固定相依版本 |
| SilentTalk `agent_lease.py` | 租借／歸還，不自行部署或啟動推論引擎 |

模型推論端點為 `127.0.0.1:18089`，UI／控制端點為 `127.0.0.1:18087`。SilentTalk 租借角色模型與相容 LoRA，使用租借回傳的端點；歸還後載入原 Agent 模型。LoRA 必須與其訓練基底相容，不能任意套用其他模型。

Agent 安裝不要求私人角色 LoRA。SilentTalk 的既有安裝器使用同一個 EdgeAgent 安裝器，額外部署角色基底／LoRA；登入私人資產 repository 只適用於這些資產。

可抽換模型仍須由目前 llama.cpp 版本支援其架構。下載模型不需要重建 Agent；更新 runtime 必須重新驗證協定與工具行為。

## 選型依據

採用 llama.cpp router 是為了共用一個模型管理入口並保留角色 LoRA。[Ollama 0.35.0 的建立流程](https://github.com/ollama/ollama/blob/v0.35.0/server/create.go)已拒絕 LoRA adapter，本機實測也回傳 HTTP 400。本次沒有相同模型、量化與 context 下的吞吐量對照，因此沒有宣稱 llama.cpp 比 Ollama 更快。

Pi 提供現成編程 Agent 的工具、取消、摘要與重試；本專案維護必要的 UI 與租借轉接。預設模型來源為 [Qwen3.5-4B GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF)，下載固定 commit 並驗證 SHA256。實测與限制見 [VALIDATION.md](VALIDATION.md)。

## 開發與驗證

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m ruff check edge_agent tests
.venv\Scripts\python.exe -m pytest -q
```

`tests/browser_check.mjs` 使用真實 Edge/CDP 驗證 UI。驗收不保存使用者對話。原始碼：[nobody945946/EdgeAgent](https://github.com/nobody945946/EdgeAgent)（私人 repository）。

上游：[Pi SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md)、[Pi RPC](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md)、[llama.cpp router](https://github.com/ggml-org/llama.cpp/blob/b11193/tools/server/README.md#using-multiple-models)。

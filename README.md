# EdgeAgent

可換模型的 Windows 本機編程 Agent。開啟專案後，Agent 可以讀取程式、提出修改、執行驗證，並在同一個介面審閱結果。[SilentTalk](https://github.com/nobody945946/SilentTalk) 是租借同一推論服務的 Discord 語音角色玩具；兩者各自有啟動入口與 Python 環境。

## 使用

已安裝 SilentTalk 的使用者不必另外安裝 Agent：SilentTalk 的 Windows 發行包會自動部署 EdgeAgent，預設安裝在 `%USERPROFILE%\EdgeAgent`。取得發行包的方式見 [SilentTalk 使用說明](https://github.com/nobody945946/SilentTalk/blob/copilot-story-mode/README.md)。目前 repository 與發行頁是私人的，下載來源或 ZIP 需有存取權限；取得包含來源的 Windows ZIP 後，部署 runtime 不要求 GitHub 登入。

雙擊 `開啟 EdgeAgent.cmd`，或在已完成部署後執行 `edge-agent`。首次啟動器會自動安裝 Python 3.12、獨立 venv、Git、Node、Pi SDK、llama.cpp 與預設 Agent 模型；不需預裝 Python、Git、winget 或 CUDA Toolkit。`開啟 EdgeAgent.cmd --check` 部署並檢查元件，不開啟介面。需要 Windows x64；有可用 NVIDIA 驅動時使用 CUDA，否則使用 CPU。下載中斷或元件缺損時重跑同一啟動器。

介面位於 `http://127.0.0.1:18087/`。選擇專案資料夾後輸入工作，查看 Agent 的工具操作與修改審閱；必要時批准修改與執行。模型、附件與對話都沿用同一介面。

預設模型為 `Qwen3.5-4B-Q4_K_M.gguf`、16,384 token context、關閉 thinking。RTX 3050 Ti 4 GB 實測整機顯存約 3.1 GB，兩個小型 Python 修碼案例通過獨立驗證。2B 模型仍可下載切換，較小模型在本次測試出現重複無效編輯；上述結果不代表大型專案能力保證。

## 下載與切換模型

1. 右上角「⋯」→「模型模組」。
2. 在「下載其他模型」填入 Hugging Face 的 `作者/模型庫`，點「列出檔案」。選擇 GGUF 後點「下載模型」，等待下載與 SHA256 驗證完成。
3. 在「已下載的模型」選擇模型，調整 context、GPU 層數與省 VRAM 選項，再點「套用並切換」。下載不會自動切換目前模型。
4. 等待載入完成後，在原對話繼續工作；不需要另一個 UI、啟動器或 Python 環境。

手動下載的模型放在安裝目錄的 `models`（預設 `%USERPROFILE%\EdgeAgent\models`），點「重新掃描」即可選擇。支援目前 llama.cpp 能載入的單檔 GGUF。Hub 下載清單會排除 LoRA、分片模型與多模態配件；手動安裝時仍需確認檔案是可獨立推論的文字模型，不要把 projector 配件當成基底模型。

切換會卸載舊模型再載入新模型；失敗時會嘗試恢復原配置。工作進行中或 SilentTalk 租借中會拒絕切換。模型檔名與實際來源保持一致；工具呼叫格式可辨識不代表該模型具備足夠的 Agent 能力。更大的 context 會增加 KV cache 與顯存需求。

## 自行安裝 LoRA

LoRA 是個人資產；EdgeAgent 只提供框架支援，不附帶、下載或預設啟用 adapter。

1. 準備與**訓練基底相同**的模型，以及 llama.cpp 能載入的 GGUF LoRA。
2. 將 adapter 放入 EdgeAgent 的 `models` 目錄。在「模型模組」點「重新掃描」，選擇基底，再從「LoRA（選用）」選 adapter。
3. 點「套用並切換」。停用時選「不使用」後再套用；換基底會清除舊 adapter 選擇。

選單只顯示架構相符的 adapter，但架構相符不是完整相容性保證。同架構不同基底仍可能載入失敗；失敗時會嘗試恢復原配置。E4B adapter 不能任意套到 Qwen 等其他基底。

SilentTalk 使用自己的 `copilot.llm_model`、`character_lora` 與 `character_lora_uses` 租借配置，不會直接採用 Agent 的 adapter 選擇。角色 adapter 也放在同一 `models` 目錄；設定方法見 [SilentTalk LoRA 說明](https://github.com/nobody945946/SilentTalk/blob/copilot-story-mode/README.md#自行安裝-lora選用)。

## 工作批准與保存範圍

Agent 的寫檔、編輯及 PowerShell 執行由介面批准，可批准單次操作或本輪工作。Plan 模式只提供讀取工具。批准介面不是作業系統沙盒；執行的程式具有目前帳號的權限。`.env`、`settings.json`、`personas.json` 和 `character_state.json` 禁止透過檔案編輯工具修改。

相同操作連續重複而沒有進展，或本輪工具呼叫超過 64 次時，工作會停止並保留修改與上下文。失敗後排隊工作暫停，從更多操作選擇「繼續排隊」才能恢復；每個對話有自己的佇列。

對話、附件、工具結果、摘要及修改審閱資料只在記憶體。沒有 JSONL session、對話歷史或 shell 輸出暫存檔。關閉服務後無法復原對話；使用者要求修改的專案檔案會正常保存。應用程式會保存模型、依賴、模型設定與啟動配置；獲准執行的專案工具仍可能自行產生檔案。

## 與 SilentTalk 共用服務

SilentTalk 透過 localhost 租借角色模型，歸還後恢復原 Agent 配置。最多載入一個完整模型；角色租借期間不能啟動編程工作或切換 Agent 模型。角色與 Agent 的 context 各自設定，預設分別為 32,768 與 16,384 token。

單獨安裝 EdgeAgent 時只準備 Agent 模型；SilentTalk 安裝時會額外部署公開 Gemma 4 E4B 角色基底，不部署個人 LoRA。模型可互換，但每個用途要選擇適合的模型與相容 adapter。

| 操作 | 指令 |
|---|---|
| 開啟 UI | `edge-agent` |
| 啟動服務，不開瀏覽器 | `edge-agent start` |
| 查詢服務狀態 | `edge-agent status` |
| 關閉服務、釋放顯存 | `edge-agent stop` |
| 查詢／開啟／關閉登入自啟動 | `edge-agent autostart status` / `on` / `off` |

退出 SilentTalk 是歸還租借，通常會重新載入 Agent；要釋放顯存需關閉 EdgeAgent 服務。首次部署是否就緒可重跑啟動器的 `--check`；SilentTalk 的 `--check` 還會實際載入 STT 並驗證角色租借與歸還。

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

可抽換模型仍須由目前 llama.cpp 版本支援其架構。下載模型不需要重建 Agent；更新 runtime 必須重新驗證協定與工具行為。

## 選型依據

採用 llama.cpp router 是為了共用一個模型管理入口並保留角色 LoRA。[Ollama 0.35.0 的建立流程](https://github.com/ollama/ollama/blob/v0.35.0/server/create.go)已拒絕 LoRA adapter，本機實測也回傳 HTTP 400。本次沒有相同模型、量化與 context 下的吞吐量對照，因此沒有宣稱 llama.cpp 比 Ollama 更快。

Pi 提供現成編程 Agent 的工具、取消、摘要與重試；本專案維護必要的 UI 與租借轉接。預設模型來源為 [Qwen3.5-4B GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF)，下載固定 commit 並驗證 SHA256。實測與限制見 [VALIDATION.md](VALIDATION.md)。

## 開發與驗證

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m ruff check edge_agent tests
.venv\Scripts\python.exe -m pytest -q
```

`tests/browser_check.mjs` 使用真實 Edge/CDP 驗證 UI。驗收不保存使用者對話。原始碼：[nobody945946/EdgeAgent](https://github.com/nobody945946/EdgeAgent)（私人 repository）。

上游：[Pi SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md)、[Pi RPC](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md)、[llama.cpp router](https://github.com/ggml-org/llama.cpp/blob/b11193/tools/server/README.md#using-multiple-models)。

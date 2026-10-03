# EdgeAgent

EdgeAgent 是可換模型的 Windows 本機編程 Agent。開啟專案後，Agent 可讀取程式、提出修改、執行驗證，並在同一個介面審閱結果。[SilentTalk](https://github.com/nobody945946/SilentTalk) 是租借同一推論服務的 Discord 語音角色玩具。

## 安裝與使用

已安裝 SilentTalk 的使用者不必另外安裝 Agent：玩家 ZIP 的啟動器會一起部署 EdgeAgent，預設位置為 `%USERPROFILE%\EdgeAgent`。取得發行包見 [SilentTalk 使用說明](https://github.com/nobody945946/SilentTalk/blob/copilot-story-mode/README.md)。目前 repository 與發行頁是私人的，取得來源／ZIP 需有存取權限；取得附來源的玩家 ZIP 後，部署不再要求 GitHub 登入。

雙擊 `開啟 EdgeAgent.cmd`。首次啟動器自動準備 Python 3.12、獨立 venv、Git、Node、Pi SDK、llama.cpp 與預設 Agent 模型，不需預裝 Python、Git、winget 或 CUDA Toolkit。需要 Windows x64；有可用 NVIDIA 驅動時選 CUDA，否則使用 CPU。下載中斷或元件缺損時重跑同一入口。`開啟 EdgeAgent.cmd --check` 部署與檢查元件，不開介面。

完成部署後，執行 `edge-agent` 或雙擊啟動器，介面位於 `http://127.0.0.1:18087/`。選專案資料夾、輸入工作，查看工具操作與修改審閱；附件、模型與工作佇列都在同一介面。

輸入框旁「＋」可加入圖片、音訊或文件，也支援貼上圖片與拖放檔案。圖片有縮圖，音訊可以在送出後播放；送出前可逐一移除。不支援圖片／音訊的模型會停用相應選項與麥克風，文件選項仍可使用。模型切換後，尚未送出的不相容附件會標示並阻止送出，供使用者移除或換回模型；含媒體的舊對話也不會在不支援的模型上悄悄略過附件。

麥克風按鈕錄製音訊，再按一次停止並加入附件；第一次使用由瀏覽器詢問麥克風權限。錄音最長 2 分鐘，轉成 16 kHz 單聲道 WAV，直接送進支援音訊的模型。圖片支援 PNG、JPEG、GIF、BMP、WebP（在記憶體轉 PNG），音訊支援 WAV、MP3、FLAC。每輪最多 8 個附件、合計 20 MB；文字／PDF／DOCX 文件合計仍限 2 MB。錄音、附件與對話只留在 RAM，不建立上傳或轉錄暫存檔。

附件大小上限不代表任何長度的媒體都能放進模型 context；長音訊、圖片與對話都會使用輸入空間。

預設模型為 `Qwen3.5-4B-Q4_K_M.gguf`、131,072 token（128K）context、thinking off，單次輸出上限預設 8,192 token。8 GB RAM／RTX 3050 Ti 4 GB 筆電已實際載入此容量，引擎回報 131,072；q8 KV cache、GPU 層數 99，載入後整機顯存為 3,872 MiB。一次小型修碼與驗證流程在 75.09 秒完成，獨立 pytest 通過，測試檔未修改。大型專案與填滿 100K 輸入的能力尚未建立；完整證據見 [VALIDATION.md](VALIDATION.md)。

GPU 層數預設為 `-1`（自動分配）；llama.cpp 依當時可用顯存決定放在 GPU／CPU 的部分，預留 512 MiB，context 保持選定容量，q8 KV cache 精度不變。`0` 使用 CPU，正整數為手動層數。上面的 99 層實測是在顯存充足時；另一輪切換後手動 99 層載入失敗，因此正式預設使用自動分配。自動分配的同一修碼案例在 186.67 秒完成，獨立驗證通過；CPU 分配增加、RAM 緊張時可能變慢。

Context 與單次輸出是兩個設定：context 放入輸入、媒體、歷史和輸出；單次輸出上限包含思考與回答。輸出額度至多為 context 的一半，Pi SDK 還會依目前輸入估算與 4,096 token 安全空間減少本次請求額度。先前將 Agent context 設成 4,096 的測試實際只剩 1 token 輸出，並非這台機器只能支援小 context。EdgeAgent 現在拒絕此配置；舊 4K 配置載入時只在 RAM 提升為 8K，保留其他設定。8K 是相容最低門檻，預設工作容量為 128K。

生成截斷時介面會顯示 context、單次輸出額度、本次請求上限及引擎回報的輸出 token 數，已完成的修改和 RAM 對話保留。Pi 既有的摘要與重試仍運作；本機 host 不另加無限制續寫。

## 下載與熱切換模型

1. 右上角「⋯」→「模型模組」。
2. 在「下載其他模型」填 Hugging Face 的 `作者/模型庫`，點「列出檔案」，選 GGUF 後點「下載模型」，等待下載與 SHA256 驗證。
3. 從「已下載的模型」選模型，設定 context、單次輸出上限、GPU 層數與省 VRAM，再點「套用並切換」。
4. 等載入完成，在原對話繼續工作。下載完成不會自動切換模型。

手動模型放在 Agent 安裝目錄的 `models`，點「重新掃描」即可選擇。支援目前 llama.cpp 可載入的單檔 GGUF。Hub 清單排除分片，mmproj 會標示為配件，下載後不會列入基底模型選單；adapter 也不能當獨立基底模型。

內建 Qwen3.5-4B 與 Gemma 4 E4B 在首次載入時，會經同一個可續傳、SHA256 驗證流程取得固定來源的公開多模態配件，分別支援圖片、圖片與音訊。其他模型在「圖片／音訊配件」選擇同一來源、相同基底的 mmproj 再套用；引擎會驗證能否載入，失敗恢復原配置。低 VRAM 模式將配件留在 CPU；介面依 llama.cpp 載入後回報的能力啟用選項，不依模型檔名猜測能力。角色租借沿用既有文字 API，不載入這個 Agent 配件。

切換會卸載舊模型再載入新模型，失敗時嘗試恢復原配置。Agent 正在工作或 SilentTalk 正在租借時拒絕切換。較大 context 會增加 KV cache／顯存需求；工具格式可辨識不代表模型有足夠編程能力。模型名稱保持實際來源，不另造模型專用 UI 或環境。

## 個人 LoRA（選用）

產品只提供框架支援，不附帶、自動下載、自動配對或預設啟用個人 adapter。

1. 準備訓練時使用的相同基底與 llama.cpp 可載入的 GGUF LoRA。
2. 把 adapter 放入 `models`，在「模型模組」重新掃描，選基底，再選「LoRA（選用）」。
3. 點「套用並切換」。停用時選「不使用」再套用；換基底會清除舊 adapter 選擇。

架構相符只是最低檢查，不保證不同基底相容；載入失敗會嘗試恢復原配置。E4B adapter 不能任意套用 Qwen 等其他基底。

SilentTalk 的角色配置與 Agent adapter 選擇分開，adapter 檔案則在同一 `models`。角色設定由 [SilentTalk 角色模型與 LoRA 說明](https://github.com/nobody945946/SilentTalk/blob/copilot-story-mode/README.md#silenttalk-的角色模型與-lora)維護。

## 批准、佇列與保存範圍

寫檔、編輯與 PowerShell 執行先由介面批准，可准許單次或本輪工作。Plan 模式只使用讀取工具。批准不是 OS 沙盒，執行程式仍有目前帳號的權限；受保護的 `.env`、`settings.json`、`personas.json`、`character_state.json` 禁止透過檔案編輯工具修改。

相同操作連續重複而無進展，或本輪工具呼叫超過 64 次時停止，保留修改與 RAM context。工作失敗後暫停該對話的佇列；更多操作中的「繼續排隊」明確恢復。每個對話有自己的佇列。

對話、附件、工具結果、摘要與審閱資料只在記憶體，沒有 session JSONL、對話歷史或 Shell 輸出暫存檔。關閉服務後無法復原對話；使用者要求的專案修改正常保存。應用程式保存模型、依賴、模型設定與啟動配置，獲准執行的工具可能自行產生檔案。

## 共用推論服務

SilentTalk 經控制 API 租借角色模型，使用回傳的推論端點；歸還後恢復原 Agent 配置。最多載入一個完整模型，租借時不能開始編程工作或切換 Agent。角色 context 預設 32,768，Agent 預設 131,072，分開設定。

單獨安裝 EdgeAgent 只準備 Agent 模型；SilentTalk 部署額外準備公開 Gemma 4 E4B，不取得個人 LoRA。角色有 LoRA 時使用兩個推論 slot，區分套用與未套用 cache；其他情況一個 slot，完整模型數仍為一。

| 操作 | 指令 |
|---|---|
| 開 UI | `edge-agent` |
| 只啟動服務 | `edge-agent start` |
| 查詢狀態 | `edge-agent status` |
| 關服務、釋放顯存 | `edge-agent stop` |
| 登入自啟動 | `edge-agent autostart status`／`on`／`off` |

退出 SilentTalk 通常會重新載入原 Agent；要釋放顯存需停止服務。SilentTalk 的 `--check` 會額外實測 STT、角色租借與歸還，不能與 Agent 啟動器的元件檢查混淆。

## 架構與選型

| 元件 | 責任 |
|---|---|
| Pi coding-agent SDK 1.0.0 | Agent 迴圈、工具、摘要、重試與取消 |
| llama.cpp b11193 router | 模型子程序、載入／卸載、LoRA；最多一個完整模型 |
| `edge_agent/agent_bridge.mjs` | 本機 provider、RAM session、批准與輸出政策 |
| `edge_agent/chat.py` | SDK RPC 與 UI 事件轉接、RAM checkpoint |
| `edge_agent/service.py` | UI／控制端點、模型設定、下載與租借協調 |
| `edge_agent/models.py` | GGUF 掃描與 Hub metadata |
| `edge_agent/deploy.py` | 校驗資產與固定 runtime；npm lockfile 固定版本 |
| SilentTalk `agent_lease.py` | 控制 API client、租借／歸還；不擁有推論引擎 |

控制／UI 位於 `127.0.0.1:18087`，推論位於 `127.0.0.1:18089`。Pi 與 router 沿用上游實作，本產品負責 UI、批准與租借轉接。新模型不需重建 Agent；更新 runtime 要重新驗證工具與協定。

採用 llama.cpp 是為了所需的 LoRA 與單一模型管理入口。[Ollama 0.35.0 建立流程](https://github.com/ollama/ollama/blob/v0.35.0/server/create.go)與本機實測均拒絕 LoRA，實測 HTTP 400。沒有同模型、量化與 context 的吞吐量對照，不能宣稱 llama.cpp 比 Ollama 快。

預設 [Qwen3.5-4B GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF) 使用固定來源 commit 與 SHA256。上游：[Pi SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md)、[Pi RPC](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md)、[llama.cpp router](https://github.com/ggml-org/llama.cpp/blob/b11193/tools/server/README.md#using-multiple-models)。

## 開發與驗證

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m ruff check edge_agent tests
.venv\Scripts\python.exe -m pytest -q
```

`tests/browser_check.mjs` 使用真實 Edge／CDP 驗證 UI，不保存使用者對話。修改應在既有 owner 重寫、同步呼叫端與測試，刪除被取代的路徑；不讓 UI 補償引擎缺陷，也不增加第二套部署或模型專用流程。

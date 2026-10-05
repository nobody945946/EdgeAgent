# EdgeAgent

EdgeAgent 是可換模型的 Windows 本機編程 Agent。開啟專案後，Agent 可讀取程式、提出修改、執行驗證，並在同一個介面審閱結果。[SilentTalk](https://github.com/nobody945946/SilentTalk) 是租借同一推論服務的 Discord 語音角色玩具。

## 安裝與使用

已安裝 SilentTalk 的使用者不必另外安裝 Agent：玩家 ZIP 的啟動器會一起部署 EdgeAgent，預設位置為 `%USERPROFILE%\EdgeAgent`。玩家 ZIP 在公開的 [SilentTalk 下載頁](https://github.com/nobody945946/SilentTalk-download/releases/latest)，下載和部署都不需要 GitHub 帳號。原始碼 repository 不公開。

雙擊 `開啟 EdgeAgent.cmd`。首次啟動器自動準備 Python 3.12、獨立 venv、Git、Node、Pi SDK、llama.cpp 與預設 Agent 模型，不需預裝 Python、Git、winget 或 CUDA Toolkit。需要 Windows x64；有可用 NVIDIA 驅動時選 CUDA，否則使用 CPU。下載中斷或元件缺損時重跑同一入口。`開啟 EdgeAgent.cmd --check` 部署與檢查元件，不開介面。

完成部署後，執行 `edge-agent` 或雙擊啟動器，介面位於 `http://127.0.0.1:18087/`。選專案資料夾、輸入工作，查看工具操作與修改審閱；附件、模型與工作佇列都在同一介面。

輸入框旁「＋」可加入圖片、音訊或文件，也支援貼上圖片與拖放檔案。圖片有縮圖，音訊可以在送出後播放；送出前可逐一移除。不支援圖片／音訊的模型會停用相應選項與麥克風，文件選項仍可使用。模型切換後，尚未送出的不相容附件會標示並阻止送出，供使用者移除或換回模型；含媒體的舊對話也不會在不支援的模型上悄悄略過附件。

麥克風按鈕錄製音訊，再按一次停止並加入附件；第一次使用由瀏覽器詢問麥克風權限。錄音最長 2 分鐘，轉成 16 kHz 單聲道 WAV，直接送進支援音訊的模型。圖片支援 PNG、JPEG、GIF、BMP、WebP（在記憶體轉 PNG），音訊支援 WAV、MP3、FLAC。每輪最多 8 個附件、合計 20 MB；文字／PDF／DOCX 文件合計仍限 2 MB。錄音、附件與對話只留在 RAM，不建立上傳或轉錄暫存檔。

附件大小上限不代表任何長度的媒體都能放進模型 context；長音訊、圖片與對話都會使用輸入空間。

預設模型為 `Qwen3.5-4B-Q4_K_M.gguf`、131,072 token（128K）context、thinking off，單次輸出上限預設 8,192 token。8 GB RAM／RTX 3050 Ti 4 GB 筆電已實際載入此容量，引擎回報 131,072；q8 KV cache、GPU 層數 99，載入後整機顯存為 3,872 MiB。一次小型修碼與驗證流程在 75.09 秒完成，獨立 pytest 通過，測試檔未修改。大型專案與填滿 100K 輸入的能力尚未建立；完整證據見 [VALIDATION.md](VALIDATION.md)。

GPU 層數預設為 `-1`（自動分配）；llama.cpp 依當時可用顯存決定放在 GPU／CPU 的部分，預留 512 MiB，context 保持選定容量，q8 KV cache 精度不變。`0` 使用 CPU，正整數為手動層數。上面的 99 層實測是在顯存充足時；另一輪切換後手動 99 層載入失敗，因此正式預設使用自動分配。自動分配的同一修碼案例在 186.67 秒完成，獨立驗證通過；CPU 分配增加、RAM 緊張時可能變慢。

Context 與單次輸出是兩個設定：context 放入輸入、媒體、歷史和輸出；單次輸出上限包含思考與回答。輸出額度至多為 context 的一半，Pi SDK 還會依目前輸入估算與 4,096 token 安全空間減少本次請求額度。先前將 Agent context 設成 4,096 的測試實際只剩 1 token 輸出，並非這台機器只能支援小 context。EdgeAgent 現在拒絕此配置；舊 4K 配置載入時只在 RAM 提升為 8K，保留其他設定。8K 是相容最低門檻，預設工作容量為 128K。

日常工作記憶目標另設為 32,768 token，模型模組可調整到 128K 或模型容量範圍內的其他值。它決定何時由 Pi 整理歷史，不會縮小引擎的 128K context，也不會減少已配置的 KV cache。實際工作目標至少為輸出額度的兩倍，至多為引擎 context；SDK 為輸出與安全空間提早摘要。摘要保留完整訊息邊界，單則大輸入仍可能暫時超過目標。

每次請求使用 SDK 的滾動摘要、近期對話、當前問題與相關歷史節錄。原始 session tree 是 RAM 歷史的唯一來源；摘要不會刪掉原始文字。中文詞組與關鍵字檢索每次最多加入四段、合計 6,000 字元，另有 `search_history` 工具供模型查詢早期細節。這不是語意向量檢索，可能漏掉只換了說法的資料；圖片、音訊與思考區塊不作文字歷史搜尋。歷史節錄是可能過期的參考資料，不能當成本輪指令或工具批准。新對話彼此隔離，關閉服務或刪除對話會清除該 RAM 歷史。

本機請求啟用 `cache_prompt`，llama.cpp 重用共同前綴的 KV，只計算變動部分；摘要或切換模型後仍需重算相應部分。Agent 的已保存 prompt-state 快取上限設為 256 MiB、recurrent context checkpoints 設為 2。這些是額外快取的限制，不是整個模型或 active KV 的記憶體上限；引擎原本的 8 GiB cache 預算也不代表預先占用 8 GiB。角色租借保留原設定，不共用 Agent 的工作記憶政策。

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

SilentTalk 租借時，Agent 自動共用角色傳來的基底與 LoRA，不必在兩個介面重複選擇；歸還後恢復 EdgeAgent 原本的模型與 adapter。模型模組會顯示租借期間實際使用的模型、LoRA，以及歸還後的選擇；租借不改寫 EdgeAgent 的持久設定。adapter 檔案在同一 `models`。角色設定由 [SilentTalk 角色模型與 LoRA 說明](https://github.com/nobody945946/SilentTalk/blob/copilot-story-mode/README.md#silenttalk-的角色模型與-lora)維護。

## 批准、佇列與保存範圍

在「⋯ → 權限」分別設定網頁與 Discord 的寫檔、指令、上網權限（允許／詢問／禁止）及可用資料夾。Discord 預設關閉；開啟後還需設定至少一個工作資料夾，SilentTalk 使用第一個。

指令另有「日常唯讀自動，其餘詢問」：Windows PowerShell 原生解析能確認的工作資料夾內列檔、指定檔案讀取／搜尋及計數直接執行，不逐次顯示批准卡。寫檔、刪除、系統變更、外部程式、腳本區塊及無法確認的語法仍需批准；含萬用字元的讀檔路徑也需批准，列檔可改用 `-Filter`。讀過網頁後仍重新詢問。此設定不會把使用者的「不要修改」自動變成工具禁寫模式，角色派工必須另行保留並執行該限制。

讀過網頁之後，本輪准許失效，允許的寫檔及指令會重新詢問。刪除及透過指令觸及受保護檔案的操作永遠只有單次批准，卡片說明原因。

權限設為詢問的寫檔、編輯與 PowerShell 執行先由介面批准，可准許單次或本輪工作。介面有兩種模式：「工作」直接動手；「求知儀式」在需要嚴謹的工作前先拷問需求：第一句會開啟內附的 grill-me skill（`edge_agent/skills/grill-me`，翻譯並改寫自 [mattpocock/skills](https://github.com/mattpocock/skills) 的 grill-me／grilling／to-spec，MIT；每輪問 3～5 題並逐類想過範圍、資料、行為、例外、驗收、限制。`bench/grill_eval.py` 盲評，E4B：三輪問到使用者真正決定的 66%（沒有清單 44%），四分之一到三分之一的問題是可以自己查的事；英文原版廢話 64% 而且全程英文；指示用中文或英文問到的一樣多），Agent 依設計樹一輪一輪提問並附建議答案，事實自己查、決定交給使用者；使用者確認共識後寫出規格。儀式中只能讀，不會動檔案；規格定案後切回「工作」叫它照規格做。Pi 不載入任何 skill；EdgeAgent 自己把內附的說明放在第一句前面（不附檔案路徑，小模型看到路徑會去讀、被工作區擋下後一直重試），system prompt 不變。MCP 伺服器由 EdgeAgent 設定管理（`mcp_servers`，`edge_agent/mcp.py`）：只接本機 stdio 程式，不讀 `mcp.json`、不寫 MCP 日誌；預設只給網頁介面，Discord 要另外加入。模型平常只看到 `tool_search`，需要時才載入工具（小模型工具太多會變笨）。每次呼叫都過權限：伺服器設 `allow` 時，它標為唯讀的工具直接執行，其餘都問；不可逆的操作和 `hardware` 伺服器（例如無人機）每一步都要單次批准，按「本輪准許」也不算；連到外界的工具結果和網頁一樣會讓之後的操作重新要求批准。每個伺服器可以用 `tools` 只開放部分工具（例如台股伺服器約 173 個工具，小模型只留十個左右）；`confirm: "turn"` 讓「本輪准許」涵蓋該伺服器標為不可逆的操作（瀏覽器的每個點擊都被標成不可逆時用），代價是本輪內網頁也能引導它操作，實體裝置伺服器不能這樣設。啟用前先用 `node scripts/mcp_probe.mjs -- <指令>` 看它的工具和標記。金鑰請用 `${變數}` 引用環境變數，不要直接寫進設定。對話紀錄：在網頁「更多操作 → 紀錄」選擇加密磁碟上已存在的資料夾，按「設為紀錄資料夾」，開啟記錄並儲存（`record_sessions` 與 `record_folder`）後，每輪結束時把整段工作對話寫進使用者加密磁碟（外接硬碟，或 SSD 上的加密容器）上的 `<資料夾>/edgeagent/<對話>.jsonl`（`edge_agent/records.py`）；Windows 的資料夾必須在系統磁碟以外的獨立磁碟代號；設定時會建立 `.agent-records` 標記檔（標記本身不會加密資料夾），介面會顯示記錄中／暫停／關閉（和 SilentTalk 的轉錄共用同一個加密磁碟），硬碟沒接上或沒解鎖就不寫，也不會改寫到別處。批准不是 OS 沙盒，執行程式仍有目前帳號的權限；受保護的 `.env`、`settings.json`、`personas.json`、`character_state.json` 禁止透過檔案編輯工具修改。

相同操作連續重複而無進展，或本輪工具呼叫超過 64 次時停止，保留修改與 RAM context。工作失敗後暫停該對話的佇列；更多操作中的「繼續排隊」明確恢復。每個對話有自己的佇列。

對話、附件、工具結果、摘要與審閱資料只在記憶體，沒有 session JSONL、對話歷史或 Shell 輸出暫存檔。關閉服務後無法復原對話；使用者要求的專案修改正常保存。應用程式保存模型、依賴、模型設定與啟動配置，獲准執行的工具可能自行產生檔案。

## 共用推論服務

SilentTalk 經控制 API 租借角色模型，使用回傳的推論端點；歸還後恢復原 Agent 配置。最多載入一個完整模型；租借期間 Agent 自動跟隨角色的基底與 LoRA，使用獨立 slot 做工作，不需要修改原本的 Agent 選擇。租借或工作中禁止手動切換模型。角色 context 預設 32,768，Agent 預設 131,072，分開設定；共用時取兩者較大值並限制在租借模型的 context 上限，模型需至少支援 8,192 token。

單獨安裝 EdgeAgent 只準備 Agent 模型；SilentTalk 部署額外準備公開 Gemma 4 E4B，不取得個人 LoRA。角色有 LoRA 時使用兩個推論 slot，區分套用與未套用 cache；其他情況角色使用一個 slot。相同基底與 LoRA 時另加 Agent 專用 slot，避免互相清除 cache。每個 slot 保留兩個用戶端要求的較大 context，不自動縮小使用者設定；記憶體不足時載入會失敗並回報。

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

`python -m tests.windows_check --inference-port 18090` 可使用暫存設定啟動真正服務、驗證清理，避開已被其他程式占用的推論埠。測試指示只透過環境變數傳入記憶體：`EDGE_AGENT_WEB_PROMPT` 要求搜尋及讀網頁，`EDGE_AGENT_SEARCH_PROMPT` 要求 grep/find；`EDGE_AGENT_ROLE_PROMPT` 配合 `EDGE_AGENT_READ_PROMPT` 測量 8K／16K／32K 共用 slot 的並行交談與讀檔。runner 確認工具及完成事件，只輸出事件和數字。`--browser` 會自己用暫存 profile 開一個隱藏的 Edge（CDP 9223，該埠被占用就拒跑），跑 `tests/browser_check.mjs`，結束時用自己的 PID 關掉它，profile 隨暫存目錄刪除。

## 授權

SilentTalk 與 EdgeAgent 以 [PolyForm Strict License 1.0.0](LICENSE) 授權，著作權屬於 nobody945946。以下是白話摘要，實際以英文條文為準：

- **可以**：個人、非商業的使用，例如自己玩、學習、研究、興趣專案；學校、公益團體、研究與政府機構也可以使用。
- **不可以**：重新散布或轉載（想分享請給[官方下載頁](https://github.com/nobody945946/SilentTalk-download/releases/latest)的連結）；修改後發布或做成衍生作品；用於商業目的。
- **免責**：在法律允許的範圍內，軟體照現狀提供，不做任何保證；使用造成的損失（例如 Agent 改動或刪除了檔案），作者不負責。
- **違反時**：收到書面通知後 32 天內改正並補救，可以繼續使用；否則授權終止。

這份授權只涵蓋 nobody945946 撰寫的程式。`edge_agent/skills/grill-me` 改寫自 [mattpocock/skills](https://github.com/mattpocock/skills)，依其 MIT 授權（同資料夾的 `LICENSE`）。安裝時下載的 Python 與 Node 套件、推論引擎、語音模型與語言模型，各自依原本的授權條款，不在這份授權範圍內。

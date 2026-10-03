# EdgeAgent 驗證與限制

驗證日期：2026-10-02。平台為 Windows x64、RTX 3050 Ti 4 GB 筆電；產品版本 2.0.1。以下是已完成的實測，不是對任意模型或大型專案能力的保證。

## 128K context 與輸出額度驗證（2026-10-03）

Agent 預設 context 改為 131,072，輸出上限獨立設定為 8,192，thinking off。此筆電實體 RAM 為 8,266,268,672 bytes，GPU 為 RTX 3050 Ti 4 GB。Qwen3.5-4B 與 E4B 都已實際載入 131,072；不是只修改介面數字。載入流程比對原生 `/props?model=local` 的 `n_ctx` 與要求值，不相符會拒絕成功狀態。

GPU 預設自動分配，使用 llama.cpp b11193 的 fit；保留指定 context、q8 KV 精度與 512 MiB 顯存餘裕。早期手動 99 層的 Qwen 載入後整機使用 3,872 MiB；其後同一配置在其他 GPU 使用量增加時載入失敗。沒有關閉使用者程式；改用既有引擎自動分配後，Qwen 與 E4B 128K 載入、切換及恢復均通過。

手動 99 層的 128K Qwen 小型修碼案例在 75.09 秒完成，依序讀取來源與測試、編輯、執行 PowerShell；獨立 pytest exit 0，測試檔 SHA256 未改。正式自動 GPU 配置的同一流程在 186.67 秒完成，四個工具操作、獨立 pytest exit 0、測試 SHA256 未改，無輸出截斷；當時可用實體 RAM 約 140 MiB。這是實際 Agent 工具流程；沒有把填滿 100K 輸入的品質或大型專案能力列為已驗證。

舊 bridge 將輸出上限固定為最多 4,096；Pi SDK 又扣除輸入估算與 4,096 安全空間。使用安裝的真實 SDK 擷取請求，確認舊 4K context 測試只剩 1 token 輸出；先前將不完整結果歸因於模型能力或 projector 並無充分證據。現在基底 Agent 拒絕 4K，舊配置只在 RAM 提升至 8K 並保留其他偏好；128K 才是預設工作容量。角色租借的 context／GPU 設定與文字 API 保持獨立。

單次輸出上限經同一設定 owner 傳給 SDK、摘要保留額度及 UI；SDK 可再依剩餘 context 降低本次請求額度。截斷事件含 context、設定上限、實際請求上限與引擎 usage，且綁定最後一則回答，不會被隨後的摘要请求覆蓋。SDK 既有重試保留，host 沒有新增無限制續寫。

本輪完整 pytest：87 passed，58.27 秒，exit 0。涵蓋 128K 啟動參數、引擎 context 不相符拒絕、角色租借邊界、舊設定 RAM 遷移、輸出額度重建 SDK、真實 SDK 的 4K／1-token 負向證據及截斷 usage。真實 Edge 多模態 17／17 項通過，8 種視窗尺寸通過，exit 0，未預期 runtime／console／network 錯誤 0；E4B 在 128K 同一輪成功辨識圖片與音訊，最後恢復 Qwen 128K 自動 GPU 配置。原生選單曾受模型輪詢重建干擾；三種模型選單已在既有 owner 改為只在目錄改變時更新選項。

最後選單回歸包含 RAM 模擬目錄，確認停用 LoRA 的選擇不被輪詢覆蓋；未載入、下載或修改個人 adapter。基線 UI 重驗通過 8 種視窗及觸控操作，exit 0，未預期錯誤 0，預期不存在資料夾 HTTP 400／錯誤 log 各 1。前兩次重驗分別遇到擴充套件例外與短暫本機連線拒絕，均保留為失敗；專用 Edge 停用擴充套件，服務就緒後重驗才列為通過。Ruff check／format、兩個 JavaScript syntax 檢查及 Git diff check 通過。

以下保留前一增量與歷史驗證數字。

## 圖片與音訊首次增量驗證（2026-10-03）

真實 Edge／CDP 多模態驗證 16／16 項通過、8 種視窗尺寸通過，exit 0；未預期的 runtime exception、console error 與 network error 為 0。E4B 在同一輪原生接收圖片與音訊，成功辨識兩種媒體的測試特徵。媒體、指令與回覆只留在 RAM；證據只記錄事件及通過數。

涵蓋圖片縮圖與移除、音訊選取與播放、麥克風錄音轉 WAV、錄音中與停止期間禁止切換對話、模型切換後停用不支援的選項、保留不相容草稿、文件選項可用及原模型配置恢復。Qwen3.5-4B 回報圖片可用、音訊不可用；E4B 回報兩者可用；Qwen3-0.6B 的圖片、音訊及麥克風選項停用。能力來自引擎載入後回報。

E4B 實測 context 為 16,384、GPU 層數 28、省 VRAM 模式，使用固定 revision／SHA256 的公開 ggml-org Q8 projector；Qwen 使用公開 F16 projector。4,096 context 的早期混合媒體案例觸發 SDK 摘要後達到輸出上限，未完成。曾以另一公開 F16 projector 測試同樣的小 context，亦未完成；沒有將更換 projector 宣稱為已證實的失敗原因。直接模型 API 的獨立媒體辨識成功；其後完整 SDK／UI 成功才列為通過。

首次增量 EdgeAgent pytest：81 passed，exit 0；Ruff check／format、兩個 JavaScript syntax 檢查及 Git diff check 均為 exit 0。真實 SDK 測試驗證媒體原始 bytes、音訊 provider 格式、RAM 歷史保留、同基底能力變更後重建 SDK，以及不支援歷史附件時拒絕送出。GGUF metadata 的 RAM cache 依檔案大小與修改時間失效。

本輪修改 EdgeAgent；未重跑或修改 SilentTalk runtime。以下保留 2026-10-02 的歷史驗證範圍。

## 編程能力與模型

預設 `Qwen3.5-4B-Q4_K_M.gguf` 使用 16,384 token context、關閉 thinking。測試時整機 GPU 記憶體使用量為 3,159 MiB／4,096 MiB。模型檔為 2,740,937,888 bytes，已驗證 SHA256：`00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4`。

| 案例 | 結果 | 能支持的結論 |
|---|---|---|
| 小型 Python 修碼案例一 | 36.47 秒；獨立 pytest exit 0；測試檔未修改 | 此案例可完成 |
| 小型 Python 修碼案例二 | 111.39 秒；獨立 pytest exit 0；測試檔未修改 | 此案例可完成 |
| 較小的 2B 模型 | 21.06 秒因無進展停止；測試檔未修改 | 停止機制有效；不算完成工作 |
| 大型專案 | 尚未建立能力證據 | 不宣稱可勝任大型專案 |

另以真實 UI 完成批准寫檔、工具輸出、修改審閱與排隊驗證；最後由獨立 pytest 確認結果。這些測試證明產品流程可運作，不能推論所有下載模型都有同等能力。

## 模型熱切換與個人 LoRA

實際切換過預設 4B、Qwen3-0.6B，以及角色基底與相容 LoRA，之後恢復原 Agent。切換期間原對話 context 保留在 RAM。

| 驗證 | 結果 |
|---|---|
| 選用 LoRA 的預設狀態 | 空白／不使用；部署不取得個人 adapter |
| 相容 adapter | 實際載入、移除、恢復原 Agent，exit 0 |
| 同架構但不相容的 adapter | 引擎拒絕載入；原 Agent 配置恢復並就緒 |
| 真實 Edge 的 LoRA 選單 | 8 項斷言通過：空白預設、架構篩選、載入、移除、換基底清除選擇、原配置恢復、Agent 就緒、小視窗控制項 |
| 角色租借 | 使用 LoRA、32,768 token context、共用推論端點 18089；取得租借 8.41 秒，歸還後恢復 Agent |

架構篩選只是最低門檻。adapter 仍需匹配訓練時使用的基底，不能以「下拉選單有出現」當成相容保證。

## 介面與工作生命週期

使用既有 `tests/browser_check.mjs` 在真實 Microsoft Edge／CDP 驗證模型目錄與下載入口、跨架構切換、Unicode 資料夾、Git worktree、終端機狀態與取消、附件、觸控側欄與修改審閱。

8 種視窗尺寸通過：1920×1080、1024×768、768×1024、390×844、320×568、844×390、390×320、320×280。最終完整執行 exit 0，未預期的 runtime exception、console error 與 network error 為 0。故意查詢不存在的資料夾會產生一次 HTTP 400 與一次錯誤 log；已依 request ID 和測試路徑辨識，並確認介面可以恢復，不把它當成未知錯誤略過。

一次早期瀏覽器啟動遭工具拒絕，一次後續執行被中斷；兩次都不計為通過。之後重新執行完整測試才取得上述成功結果。

工作失敗後，該對話的排隊工作會暫停；明確選擇恢復後只執行一次。取消測試確認在 abort 前先拒絕待批准操作，檔案未被修改。終端機是持續的 PowerShell 程序，不是 ConPTY 終端。

## 部署、資料與清理

Pi coding-agent SDK 1.0.0 使用固定 lockfile；llama.cpp b11193 router 設定最多一個完整模型實例。SDK 對話 session 與修改 checkpoint 都使用 RAM。檢查未產生 Pi state 目錄、JSONL session 或推論對話檔。

實際驗證過缺少引擎 DLL 的修復，以及 SDK 完成紀錄吻合但 import 損壞時的重新安裝；只有 import 成功才寫入 SDK 完成紀錄。SilentTalk 部署的角色資產只有公開基底，沒有取得個人 LoRA。兩個自行建立的部署測試目錄已刪除，正式公開模型、個人 adapter 與受保護設定未刪除。

Windows 發行包的首次部署已在新目錄完成，使用新的 SilentTalk／EdgeAgent venv、SDK 與引擎；沿用了既有 Python／Git 和公開下載快取。這是新目錄部署驗證，並非完全乾淨作業系統的安裝測試。部署結果見 [SilentTalk 發行頁](https://github.com/nobody945946/SilentTalk/releases/tag/windows-2026.10.02)。

## 自動檢查與已知限制

| 檢查 | 實際結果 |
|---|---|
| EdgeAgent pytest | 74 passed，exit 0 |
| EdgeAgent Ruff | exit 0 |
| SilentTalk 當輪租借相關測試 | 86 passed，exit 0 |
| SilentTalk 修改範圍 Ruff | exit 0 |
| LoRA UI JavaScript syntax | exit 0 |
| [EdgeAgent 後續 CI](https://github.com/nobody945946/EdgeAgent/actions/runs/37024149273) | success |

文件更新的 SilentTalk CI [37026837937](https://github.com/nobody945946/SilentTalk/actions/runs/37026837937) 有 223 passed、1 failed、1 skipped；失敗為既有的冷場回覆測試 `test_a_lull_line_is_dropped_if_someone_speaks_meanwhile`。這次只改文件，沒有改角色 runtime；此失敗未宣稱已修復，歷史通過不代表該輪通過。

SilentTalk 原有測試 fixture 曾產生 `SlowFakeLLM.state` 背景例外，pytest 仍為 exit 0；原有 Ruff 問題為 DTZ005 一項、BLE001 七項，已確認屬於基線。上述限制沒有以「測試全綠」掩蓋。

Ollama 0.35.0 的實際 LoRA 建立請求回傳 HTTP 400。本次採用 llama.cpp 是為了所需的 LoRA 與共享模型管理；沒有在相同模型、量化與 context 下比較吞吐量，不能宣稱它比 Ollama 快。

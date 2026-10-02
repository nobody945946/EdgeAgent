# EdgeAgent 驗證與限制

驗證日期：2026-10-02。平台為 Windows x64、RTX 3050 Ti 4 GB 筆電；產品版本 2.0.1。以下是已完成的實測，不是對任意模型或大型專案能力的保證。

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

SilentTalk 原有測試 fixture 曾產生 `SlowFakeLLM.state` 背景例外，pytest 仍為 exit 0；原有 Ruff 問題為 DTZ005 一項、BLE001 七項，已確認屬於基線。上述限制沒有以「測試全綠」掩蓋。

Ollama 0.35.0 的實際 LoRA 建立請求回傳 HTTP 400。本次採用 llama.cpp 是為了所需的 LoRA 與共享模型管理；沒有在相同模型、量化與 context 下比較吞吐量，不能宣稱它比 Ollama 快。
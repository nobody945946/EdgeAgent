# Windows AI 維護紀錄

唯一實作位於 EdgeAgent 的 `scripts/windows-ai-maintenance/`。本機 `.codex/windows-ai-maintenance/` 的入口只轉呼叫這裡。
系統原值及量測資料留在 `%USERPROFILE%\.codex\windows-ai-maintenance`，不加入 Git；從 repo 執行也使用同一份還原快照。
此工具獨立於 EdgeAgent 啟動流程，不會隨應用啟動自動修改 Windows。

## 2026-10-06 服務精簡：已永久套用

使用者授權未使用的服務預設關閉，保留 Discord 與正常電腦運作。已逐項核對實際服務、執行程序、依賴及裝置，再以服務管理員正常停止並設為 Disabled；不殺共用 svchost。

| 服務 | 套用前 | 現在 | 評估 |
|---|---|---|---|
| DiagTrack | Auto、Running | Disabled、Stopped | Windows 診斷與使用資料傳送；本次不需要 |
| ASUSSystemAnalysis | Manual、Stopped | Disabled、Stopped | 先前已停用的 ASUS 診斷 |
| ASUSSystemDiagnosis | Manual、Stopped | Disabled、Stopped | 先前已停用的 ASUS 診斷 |
| RetailDemo | Manual、Stopped | Disabled、Stopped | 商店展示模式；個人筆電不用 |

`deploy.cmd ApplyServices` 套用上述四項，`deploy.cmd PlanServices` 只讀，`deploy.cmd RestoreServices` 恢復本次接管前的啟動方式、執行狀態及 delayed automatic start。原值沿用既有 `original-state.json` 的 ServiceProfile；重複套用不改原值。開始前若有執行中的依賴服務即拒絕全部變更；中途失敗嘗試恢復並回報失敗。完整 `Restore` 仍恢復之前全部維護的基準，包含 Wi-Fi 延伸。

實際套用 exit 0，重新查服務四項均 Disabled／Stopped。Wi-Fi Up、GitHub HTTPS 200、WlanSvc／Audiosrv／WinDefend Running，Discord 六個程序仍存在。DiagTrack 停用前程序工作集 27.4 MiB、私有提交 43 MiB；其餘三項原本沒在跑，不能宣稱此次釋放大量 RAM。套用前後應用工作集持續變動，沒有隔離的 RAM 淨增量證據。

保留 Wi-Fi 核心、Bluetooth、音訊、Defender、Windows Update、電源、驅動及使用中的 Quest 裝置服務。Xbox、Fax 等已按需啟動且停止的項目沒有目前程序占用。已安裝 Maps 和連線裝置功能，不能僅因當下沒有視窗就斷言不用。開發中的 VS Code 承載目前維護工作，保留至工作結束；日常模型啟動時不需要它。

Microsoft 的[服務功能說明](https://learn.microsoft.com/en-us/windows/iot/iot-enterprise/optimize/services)列出 DiagTrack 的診斷用途與 RetailDemo 的展示用途；文件面向 IoT，這裡只用來核對功能，停用選擇依本機用途和依賴決定。

## 輕量化與視覺效果：2026-10-06 已永久套用

`deploy.cmd` 是 `maintain.ps1` 的一鍵入口；雙擊會要求 Windows UAC 提升權限。輔助 PowerShell 使用隱藏視窗，結束即退出，入口回傳真正的子程序 exit code。
使用同一個 Windows 帳號提升權限，不能輸入另一個管理員帳號來修改不同的 HKCU。
不需要重新開機。瀏覽器依其政策刷新時間生效；少數介面可能需要重新登入才完整更新。

| 指令 | 行為 |
|---|---|
| `deploy.cmd` 或 `deploy.cmd ApplyLight` | Edge／Chrome 背景政策、DiagTrack；16 項原生視覺控制全部關閉，透明關閉，Explorer 設定最佳效能、關工作列動畫／選取框透明／圖示陰影／縮圖 |
| `deploy.cmd PlanLight` | 只讀檢視目前設定與部署範圍，不提升權限 |
| `deploy.cmd RestoreLight` | 還原新增設定的原值、registry type、存在狀態、服務啟動方式及執行狀態；若有 Search 暫停工作階段，也一併恢復 |
| `deploy.cmd BeginAI` | 暫時將 Windows Search 改為 Manual 並停止；保存當次原設定 |
| `deploy.cmd EndAI` | 完整恢復該次 Search 原設定，包含 delayed automatic start；工作中斷後也可執行 |

原值存在既有 `original-state.json`，不另建立第二份維護流程。重複 ApplyLight 不覆寫最初原值；舊快照升級只補尚未接管的項目原值。部分部署失敗會嘗試恢復原設定並回報錯誤。
`RestoreLight` 恢復這次新增部署之前的設定；既有 `Restore` 則恢復全部維護項目，包含先前 WLAN 調整，因此會短暫重啟 Wi-Fi。

此版本保留 SysMain、Windows Update、Defender、pagefile、memory compression、DWM。使用者要求關閉視效，因此也關字型平滑；舊程式文字可能變粗糙，使用 DirectWrite 或自畫介面的 app 不一定遵守系統效果設定。
沒有安裝 Mem Reduct、移除系統套件或自動關閉開啟中的瀏覽器。
本機沒有 Copilot、Cortana、Widgets 的對應 Appx 套件，不需修改；Windows 11 Home 不在 Microsoft Widgets 管理政策的支援版本清單中。

驗證：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\verify.ps1`，60 checks passed。
真實 Win32 動畫 API 關閉／還原測試已恢復原值；服務操作使用隔離替身，registry 使用測試專用暫存 key，測完已刪除。
已測既有 DWORD／ExpandString 的原值與 type、原本不存在的值、重複套用／還原、注入失敗回復、Search 工作階段、延遲自動啟動與含空白路徑的一鍵入口參數。

2026-10-03 本機取樣：SysMain 工作集 6.8 MiB、WSearch 29.1 MiB、DiagTrack 29.4 MiB；wuauserv 已為 Manual／Stopped。
工作集包含共用頁面，不能把這些數字視為實測釋放 RAM；備用快取已包含在 Available RAM 中，不能再加一次。
21:38:59 部署 ApplyLight exit 0，重新查 16 項原生控制全部 0，EnableTransparency／TaskbarAnimations／ListviewAlphaSelect／ListviewShadow 全 0、IconsOnly=1、VisualFXSetting=2。Discord 六程序保留。Explorer 設定已保存，部分既有介面需重開後才讀新值；沒有中斷檔案操作來強制重啟 Explorer。

套用前後短時間取樣：Available RAM 1385→1274 MiB、DWM 59.1→59.3 MiB、Explorer 128.1→121.5 MiB、獨立 GPU used 都是 0/free3962 MiB。其他應用工作集同時變動，不是隔離 A/B，未證明新增 RAM／VRAM 淨增量，不宣稱此次解決模型載入。

控制採用 [SystemParametersInfoW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-systemparametersinfow)，包含全域 UI effects、動畫／淡出／陰影／漸層／滑過效果、字型平滑及拖曳時顯示視窗內容。後兩項依文件使用 uiParam，其餘布林 setter 使用 pvParam。Explorer 對應設定依 [Microsoft VDI 文件](https://learn.microsoft.com/en-us/windows-server/remote/remote-desktop-services/remote-desktop-services-vdi-optimize-configuration)核對。[DWM 從 Windows 8 起常駐](https://learn.microsoft.com/en-us/windows/win32/w8cookbook/desktop-window-manager-is-always-on)，不靠終止它來省資源。

參考：[Windows 快取與可用記憶體](https://learn.microsoft.com/en-us/windows-server/administration/performance-tuning/subsystem/cache-memory-management/)、[Search 索引負載退避](https://learn.microsoft.com/en-us/troubleshoot/windows-client/shell-experience/windows-search-performance-issues)、[更新與使用時間](https://learn.microsoft.com/en-us/windows/deployment/update/waas-restart)、[Widgets 支援版本](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-newsandinterests)、[Mem Reduct 原始碼](https://github.com/henrypp/memreduct/blob/master/src/main.c)。

2026-10-03，ASUS TUF Dash F15 FX516PE，Windows 11 Home，8 GB RAM。

本次保留 Windows、Defender、Windows Update、pagefile、memory compression、Wi-Fi 核心驅動、ASUS 風扇／電源／熱鍵服務與現有使用中的瀏覽器、編輯器、Discord、遠端連線。沒有移除軟體、driver package、DLL 或使用者資料。

## 已套用

| 項目 | 現在狀態 | 可還原的原值 |
|---|---|---|
| Discord、Steam、Edge 登入自啟動 | 移除各自 HKCU Run 值 | 保存存在狀態、資料與 registry type |
| Edge StartupBoostEnabled、BackgroundModeEnabled | 0 | 保存原值；原本不存在的值在還原時移除 |
| ASUSSystemAnalysis、ASUSSystemDiagnosis | Disabled、Stopped（2026-10-06 Services profile） | 完整基準原本 Auto、Running；本次 Services profile 前為 Manual、Stopped |
| 兩個 ASUS 診斷排程 | Disabled | 原本 Enabled |
| Intel AX201 的 IHV ExtensibilityDLL 登錄 | 移除該值，DLL 與 driver 原檔保留 | 保存原字串、registry type、網卡 identity |
| 閒置 Steam | 正常 shutdown，遊戲程序不存在 | 應用程式仍安裝；可正常重新開啟 |

WLANExt 原本 PID 4796，私有提交記憶體 6,865 MiB、working set 約 23 MiB。重啟 WLAN 後新 PID 6760 起初約 4 MiB，19 分鐘後約 30 MiB，顯示重啟只能暫時釋放累積。停用此網卡的 Intel 廠商延伸登錄後，WLANExt 未再啟動。當前 WPA2-Personal 連線、DNS、GitHub HTTPS 200 已驗證。這個變更會停用 Intel IHV 的廠商延伸功能；未測試特殊企業驗證或其他專有 Wi-Fi 功能。驅動更新可能重新註冊該延伸，後續檢查應確認實際狀態，不能只看本紀錄。

ASUS 診斷程序先前是 Windows 服務，單獨關閉程序會被服務復原重啟並重建排程。最終改從服務 owner 設成 Manual 並正常 Stop-Service，再停用其診斷排程；沒有增加背景監控、重複殺程序或定期修補的工作。

## 還原與驗證

唯一執行入口是 `maintain.ps1`，原值保存在 `original-state.json`，最後狀態保存在 `after-state.json`。腳本支援 Inspect、Apply、Restore、RestartWifi、DisableWifiExtension。Apply 不覆寫已保存的原值；新增的設定只有第一次接管時保存。Wi-Fi 修改會先核對網卡 identity 與 Personal 連線，連線測試失敗會復原該登錄並重启 WLAN。

Restore 會恢复原 registry 值與類型、排程啟用狀態、診斷服務啟動類型與執行狀態，以及 Wi-Fi 延伸登錄。實際 Restore 後逐項比對通過，再重新套用精簡狀態。恢復 Wi-Fi 設定會短暫重啟 WLAN；不需要刪除或重新安裝驅動。

執行此腳本修改服務、排程與 HKLM 需要系統管理員權限。需要還原時由維護 agent 執行 Restore，不必手動重建設定。

提交記憶體和實體 RAM 是不同量測。WLANExt 的 6.7 GiB 大部分當時不在 working set，不能宣稱釋放了 6.7 GiB 實體 RAM。可用 RAM 同時受使用中的 Edge、VS Code、Discord 與模型是否載入影響，數字記錄在狀態檔。

參考：[Microsoft IHV 延伸架構](https://learn.microsoft.com/en-us/windows-hardware/drivers/network/overview-of-ihv-extensibility)、[Intel 社群同版驅動的相近回報](https://community.intel.com/t5/Wireless/Bug-Report-AX211-WlanExt-exe-memory-leak-80-MB-h-regression/m-p/1758756?profile.language=en)。社群回報網卡是 AX211，本機是 AX201；判斷依本機累積及停用後的連線實測，沒有把不同機器的回報視為已確認根因。

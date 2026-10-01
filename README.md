# E4B Core

Source: https://github.com/nobody945946/E4BCore (private repository).

On Windows x64, open `開啟 E4B.cmd`: the independent installer prepares Python
3.12, its own virtual environment, pinned Python dependencies, verified llama.cpp
b11193 binaries, Gemma 4 E4B, and the character adapter. It uses CUDA 12.4 if an
NVIDIA driver is available and usable; otherwise it installs the CPU backend.
Git, winget and the CUDA Toolkit are not prerequisites. A GitHub sign-in with
access to this private repository is needed to obtain the character adapter.
`開啟 E4B.cmd --check` installs and validates binaries without opening the browser.
The installer does not enable Windows login startup or modify user configuration.
Large downloads resume after interruption and are checked against pinned SHA256.
Rerunning setup reuses verified assets. Allow at least 12 GB of free disk space.
For development, install the optional checks with
`.venv\Scripts\python.exe -m pip install -e ".[dev]"` after setup.
Model files, local configuration, caches, and runtime data are excluded from Git.
The original extracted core is preserved in SilentTalk commit `24b73e5`.

`scripts/bootstrap.ps1` owns Windows prerequisites and the independent package
environment; `e4b_core/deploy.py` owns inference binaries and model assets.
SilentTalk may invoke this installer, but its application imports no E4B code
and borrows only the localhost API. The source-only bootstrap distribution is
published separately from the `runtime-assets-v1` character adapter release.

This is the independent local E4B runtime. Its source, Python environment, and
models live under `C:\Users\A8fm0\E4BCore`; SilentTalk only leases the running
service over its localhost API.

Open `開啟 E4B.cmd` or run `e4b` from a terminal. The browser UI provides an
project/conversation sidebar, a workspace folder picker, and per-message thinking
effort. Conversations can be switched without losing their in-memory context.
Opening another folder starts a separate conversation. Use `儲存設定` to keep the
Agent and thinking defaults, and `關閉 E4B` to release the model and GPU memory.
E4B does not start automatically at Windows login.
File writes, edits, and shell commands require confirmation in the browser.
Approvals appear inline, including a proposed file diff. The `修改` panel shows
the cumulative file changes made by this conversation. Tool cards retain the
arguments and actual results in memory. `規劃` mode exposes only read-only tools
and also rejects unexpected mutating tool calls at execution time. `停止` cancels
generation and pending approvals; an already-running shell command may finish
before cancellation takes effect (shell timeout is capped at 60 seconds).

All seven agent tools execute in E4B Core. File operations use Python Unicode
paths rather than the llama.cpp Windows file-tool endpoint. Search honors Git
ignore rules when Git is available and skips common build/cache directories.
E4B's model and GPU capacity remain those of the local installation; this UI
does not imply parity with Codex cloud services or model capability.

The composer accepts UTF-8 text/code, selectable-text PDFs, and DOCX attachments
(up to eight files and 2 MB combined). Files are decoded in memory and are never
saved as uploads. Image understanding and scanned-PDF OCR need additional model
support and are not accepted by this text-document attachment path.

The `終端` panel runs a persistent PowerShell session for each conversation. Its
current directory and variables survive consecutive commands; output, command
history, and conversation drafts remain in memory. `停止` terminates the terminal
process tree, `清除` clears the displayed output, and `收起` hides the panel.
Terminal commands are explicitly entered by the user and execute independently
of the model's approval requests. Commands are capped at a five-minute runtime.

Messages sent during a model turn are queued and run in order after it finishes.
Stopping the model clears that queue. Conversation switching restores each draft,
pending attachments, work mode, and thinking setting.

Git projects show the current branch. `建立 Worktree` creates a real isolated
working tree on a new `e4b/…` branch under `.worktrees` and opens it as a new
conversation. It starts from committed HEAD; uncommitted changes in the original
workspace are not copied. This operation preserves the original workspace.

Development checks: `.venv\Scripts\python.exe -m ruff check e4b_core tests` and
`.venv\Scripts\python.exe -m pytest`. `node tests/browser_check.mjs` verifies the
real UI through a dedicated Edge CDP instance on port 9223. Optional test work is
supplied through `E4B_TEST_PROMPT` in memory, never stored by the runner.

For a terminal conversation, run `e4b chat`. Use `/reset` to start a fresh
conversation and `/exit` to leave. Thinking effort can be selected with
`e4b chat --thinking-effort off|low|medium|high`. Terminal chat keeps history in
memory for the current session and does not enable Agent tools.

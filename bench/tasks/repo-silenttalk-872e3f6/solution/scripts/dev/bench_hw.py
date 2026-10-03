"""How fast is character mode on *this* machine? One command, about 2 minutes.

    .venv\\Scripts\\python.exe scripts\\dev\\bench_hw.py        (Windows)
    .venv/bin/python scripts/dev/bench_hw.py                  (Linux)

Measures each stage between "someone stopped talking" and "the bot starts
speaking", with the same llama-server flags the app uses:

  - LLM: deciding whether to speak (1 token), old prompt format vs the
    append-only chat format, and reply generation speed
  - STT: Paraformer decode speed and the finalize step after a line ends
  - TTS: EdgeTTS time to first audio and to the whole line (needs internet)

Self-contained on purpose: it does not import silenttalk, so it runs on an
older install as long as .venv, llama-server and the models are there. All
text is a fixed synthetic chat; nothing is written to disk except a
temporary TTS clip that is deleted right away.

Paste the table it prints at the end back to whoever asked for it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
PORT = 18399
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Production character system prompt for the 凱哥 preset, rendered 2026-09-27.
SYSTEM = """你是 Discord 語音頻道裡的一個角色 Bot，由 AI 扮演下面人設裡的人，和大家一起用語音聊天。你說的話會直接用語音播出來。

規則：
- 只根據真實對話回應，不得杜撰別人的身分、經歷、情緒或事實。關於你自己，只用人設裡寫到的事，不要編新的經歷。
- 你就是這個人，從頭到尾用這個身分講話。有人說你是 AI、機器人或問你是不是真人時，當成朋友在開你玩笑，用這個角色會有的方式笑著帶過、吐槽或反問回去，不要承認自己是程式，也不要一本正經地解釋自己不是 AI（那樣反而更可疑）。
- 你只存在於這個語音頻道，只能用嘴巴聊天：不能打字、寫程式、寫文章、翻譯整段文字、上網查資料、設提醒，也不會記得下次聊天。被要求做這些事時，用這個角色會有的方式回應（推託、吐槽、給一句口頭建議、叫對方自己來或用別的工具），絕對不要答應或假裝在做你做不到的事。
- 你不是百科全書：超出你人設會懂的問題，就用這個人真實的程度回答，可以說不知道。
- 台灣繁體中文口語，只說一句話，長度很短，多半 4～12 字，最長不得超過 72 字。
- 像朋友聊天一樣自然接話：附和、追問、吐槽或開玩笑，選這個角色此刻最可能的反應。不要重複別人的話，不要說教，不要每句都叫對方名字。
- 不羞辱、不人身攻擊，不拿外貌、疾病、性別、種族、家庭、收入開玩笑。
- 這時候插話不自然（別人在私下聊、話題跟你無關、你剛說過類似的話）就輸出 SKIP。

【你的人設】口吻、用詞、句長、立場都照這裡：
名字：凱哥
朋友叫你：凱哥、阿凱
身分：24 歲，工程師，下班就上線打遊戲
背景：國中開始玩射擊遊戲，打過校隊，現在是朋友群裡最常開團的人。
你想要：想帶大家贏，也想被隊友當成可靠的指揮。
你害怕、想避開：怕被說是在甩鍋，怕氣氛變僵。
你在意：輸了先檢討自己，團隊合作大於個人數據。
你的雷點：有人不看小地圖亂衝、輸了只會怪別人。
你熟悉：射擊遊戲戰術、報點、PC 硬體。
說話方式：句子短、節奏快，常用「欸」「好啦」「穩」，偶爾一句台語。
口頭禪（偶爾自然帶到，不要每句都用）：穩啦、我的我的、笑死
幽默風格：自嘲和吐槽隊友，但點到為止
絕對不說：不罵人家人、不嘲笑別人技術差到要他退坑。
個性：偏外向健談、偏毒舌嘴砲、偏愛搞笑、偏衝動直接
你平常講話像這樣：
「欸我的，下波我先進」
「穩啦，這把能拿」
「笑死，我又被架」

只輸出一行：你要說的話，或 SKIP。不要加引號、名字或解釋。"""
REMINDER = "（記住：你是凱哥；句子短、節奏快；口頭禪和代表台詞少用，大約四五句才一次。這句要是這個人會說的話。）"
BOT = "凱哥"
CHAT = (
    ("阿傑", "欸今天要打幾場"),
    ("小雨", "我等等要先吃飯喔"),
    ("阿傑", "那先一場就好"),
    ("Ken", "我剛下班累死"),
    ("小雨", "凱哥你今天有要上嗎"),
    (BOT, "穩啦等我五分鐘"),
    ("Ken", "昨天那把真的有夠雷"),
    ("阿傑", "對啊那個打野一直送"),
    ("小雨", "我覺得是我們沒跟"),
    ("Ken", "欸有人要喝飲料嗎"),
    ("阿傑", "我要半糖去冰"),
    ("小雨", "凱哥昨天是不是又被架"),
    ("Ken", "笑死他每次都這樣"),
    ("阿傑", "好啦開了開了"),
    ("小雨", "誰要當指揮"),
    ("Ken", "凱哥來啊"),
)
# Lines before this index only build up history; each later line is timed.
TIMED_FROM = 8
RECENT_TURNS = 16  # config.toml [copilot] recent_turns
TTS_LINE = "欸我的，下波我先進，你們跟緊一點"
TTS_VOICE = "zh-TW-YunJheNeural"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model",
        default="gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf",
        help="file name in .runtime/models/llm/ or a path",
    )
    parser.add_argument("--gpu-layers", type=int, default=99, help="0 = CPU only")
    parser.add_argument(
        "--lora",
        help="adapter (file in .runtime/models/llm/ or a path): time the app's LoRA gate + reply",
    )
    parser.add_argument(
        "--lora-uses",
        default="called",
        help='with --lora: as copilot.character_lora_uses; "line" in it times the reply with the adapter too',
    )
    parser.add_argument("--skip-tts", action="store_true", help="no internet")
    parser.add_argument("--llama-server", help="path to llama-server(.exe)")
    args = parser.parse_args()
    # Redirected output on a zh-TW Windows is cp950, which has no Simplified
    # characters; the STT line would crash the whole report.
    sys.stdout.reconfigure(errors="replace")

    rows: list[tuple[str, str]] = []
    print_system(rows)
    gpu_before = gpu_memory_used()
    try:
        bench_llm(args, rows, gpu_before)
    except BenchError as exc:
        rows.append(("LLM", f"失敗：{exc}"))
    try:
        bench_stt(rows, None if args.skip_tts else synth_clip(rows))
    except BenchError as exc:
        rows.append(("STT", f"失敗：{exc}"))

    print("\n==== 貼這張表回來 ====")
    for name, value in rows:
        print(f"{name}：{value}")
    return 0


class BenchError(RuntimeError):
    pass


# ---------------------------------------------------------------- system


def print_system(rows: list[tuple[str, str]]) -> None:
    rows.append(("OS", f"{platform.system()} {platform.release()}"))
    rows.append(
        ("CPU", f"{platform.processor() or platform.machine()} x{os.cpu_count()}")
    )
    smi = nvidia_smi("name,memory.total,memory.used,power.limit,clocks.max.sm")
    rows.append(("GPU (nvidia-smi)", smi or "沒有 nvidia-smi"))


def nvidia_smi(fields: str) -> str:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    try:
        out = subprocess.run(
            [exe, f"--query-gpu={fields}", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""


def gpu_memory_used() -> int | None:
    value = nvidia_smi("memory.used").split(" ")[0]
    return int(value) if value.isdigit() else None


# ---------------------------------------------------------------- LLM


def find_llama_server(explicit: str | None) -> str:
    if explicit:
        if not Path(explicit).is_file():
            raise BenchError(f"--llama-server 指定的檔案不存在：{explicit}")
        return explicit
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
    if os.name == "nt":
        links = winget / "Links"
        os.environ["PATH"] = f"{links}{os.pathsep}{os.environ.get('PATH', '')}"
    exe = shutil.which("llama-server.exe") or shutil.which("llama-server")
    if exe:
        return exe
    # winget sometimes unpacks a portable package without creating the
    # Links shim; the exe is then only under Packages\ggml.llamacpp_*.
    if os.name == "nt":
        found = sorted((winget / "Packages").glob("ggml.llamacpp*/**/llama-server.exe"))
        if found:
            return str(found[-1])
    raise BenchError(
        "找不到 llama-server。Windows：winget install --id ggml.llamacpp -e，"
        "或用 --llama-server 指定路徑；Linux 開發機先 source .runtime/tools/env.sh"
    )


def bench_llm(
    args: argparse.Namespace, rows: list[tuple[str, str]], gpu_before: int | None
) -> None:
    model = Path(args.model)
    if not model.is_file():
        model = PROJECT_DIR / ".runtime" / "models" / "llm" / args.model
    if not model.is_file():
        raise BenchError(f"找不到模型 {model}")
    lora = None
    if args.lora:
        lora = Path(args.lora)
        if not lora.is_file():
            lora = PROJECT_DIR / ".runtime" / "models" / "llm" / args.lora
        if not lora.is_file():
            raise BenchError(f"找不到 LoRA {lora}")
    exe = find_llama_server(args.llama_server)
    rows.append(("llama-server", exe))
    listing = subprocess.run(
        [exe, "--list-devices"],
        capture_output=True,
        timeout=30,
        creationflags=NO_WINDOW,
    )
    devices = [
        line.strip()
        for line in (listing.stdout + listing.stderr)
        .decode("utf-8", "replace")
        .splitlines()
        if "MiB" in line and ":" in line
    ]
    rows.append(("llama.cpp 裝置", " | ".join(devices) or "沒有（只能用 CPU）"))

    # Same flags as silenttalk/copilot/llm.py LocalReplyModel.start (low_vram).
    threads = max(2, min(6, (os.cpu_count() or 4) // 2))
    cmd = [
        exe,
        "--model",
        str(model),
        "--host",
        "127.0.0.1",
        "--port",
        str(PORT),
        # With an adapter the app runs one slot per adapter scale (llm.py
        # _lora_field), each with the full context.
        "--ctx-size",
        "8192" if lora else "4096",
        "--threads",
        str(threads),
        "--n-gpu-layers",
        str(args.gpu_layers),
        "--parallel",
        "2" if lora else "1",
        "--jinja",
        "--flash-attn",
        "on",
        "--cache-type-k",
        "q8_0",
        "--cache-type-v",
        "q8_0",
    ] + (["--lora", str(lora)] if lora else [])
    started = time.perf_counter()
    server = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )
    try:
        wait_healthy(server)
        rows.append(("模型載入", f"{time.perf_counter() - started:.1f} s"))
        gpu_after = gpu_memory_used()
        if gpu_before is not None and gpu_after is not None:
            rows.append(
                (
                    "模型吃掉的 VRAM",
                    f"{gpu_after - gpu_before} MiB（載入前已用 {gpu_before} MiB）",
                )
            )
        post(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": "暖機"},
            ],
            1,
        )

        old_requests, new_requests = old_format_requests(), new_format_requests()
        if lora is not None:
            # What the app does per line: the gate with the adapter (slot 0),
            # then the reply, with the adapter on the same slot when "line"
            # is one of its uses, else without it (slot 1). Each slot keeps
            # its cache.
            on = {"lora": [{"id": 0, "scale": 1.0}], "id_slot": 0}
            off = {"lora": [{"id": 0, "scale": 0.0}], "id_slot": 1}
            line_on = "line" in args.lora_uses.split(",")
            reply = on if line_on else off
            post(new_requests[0], 1, extra=on)
            post(new_requests[0], 1, extra=off)
            gates, replies = [], []
            for messages in new_requests[1:]:
                gates.append(post(messages, 1, extra=on))
                replies.append(post(messages, 20, extra=reply))
            rows.append(("判斷一句（LoRA，slot 0）", summarize(gates)))
            rows.append(
                (
                    "接著回一句（LoRA，slot 0，最多 20 token）"
                    if line_on
                    else "接著回一句（原模型，slot 1，最多 20 token）",
                    summarize(replies),
                )
            )
            speed = post(new_requests[-1], 32, ignore_eos=True, extra=reply)
            rows.append(
                (
                    "生成速度（LoRA）" if line_on else "生成速度",
                    f"{speed['tps']:.1f} token/s",
                )
            )
            return

        # The first request of each format fills its cache; time the rest,
        # which is what a running chat looks like.
        post(old_requests[0], 1)
        old = [post(m, 1) for m in old_requests[1:]]
        post(new_requests[0], 1)
        new = [post(m, 1) for m in new_requests[1:]]
        rows.append(("判斷一句（舊格式）", summarize(old)))
        rows.append(("判斷一句（新格式）", summarize(new)))

        # Generation speed: force a fixed length so every machine does the same work.
        speed = post(new_format_requests()[-1], 32, ignore_eos=True)
        rows.append(("生成速度", f"{speed['tps']:.1f} token/s"))
        reply = post(new_format_requests()[-1], 60)
        rows.append(
            ("回一句話（新格式）", f"{reply['ms']:.0f} ms（{reply['n']} token）")
        )
    finally:
        server.terminate()
        try:
            server.wait(5)
        except subprocess.TimeoutExpired:
            server.kill()


def wait_healthy(server: subprocess.Popen) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise BenchError(
                f"llama-server 啟動失敗，exit code={server.returncode}"
                "（常見：port 被占用、VRAM 不足）"
            )
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1)
            return
        except OSError:
            time.sleep(0.5)
    raise BenchError("llama-server 載入逾時")


def post(
    messages: list[dict],
    max_tokens: int,
    *,
    ignore_eos: bool = False,
    extra: dict | None = None,
) -> dict:
    body = {
        **(extra or {}),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if ignore_eos:
        body["ignore_eos"] = True
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    timings = result.get("timings", {})
    return {
        "ms": (time.perf_counter() - started) * 1000,
        "prompt": timings.get("prompt_n", 0),
        "n": timings.get("predicted_n", 0),
        "tps": timings.get("predicted_per_second", 0.0),
    }


def summarize(results: list[dict]) -> str:
    ms = [r["ms"] for r in results]
    prompt = statistics.median(r["prompt"] for r in results)
    return (
        f"中位數 {statistics.median(ms):.0f} ms（{min(ms):.0f}–{max(ms):.0f}），"
        f"每次重算 {prompt:.0f} token"
    )


def old_format_requests() -> list[list[dict]]:
    """What silenttalk/copilot/llm.py sends today: the recent window rebuilt each line."""
    requests = []
    for index in range(TIMED_FROM, len(CHAT)):
        speaker, text = CHAT[index]
        if speaker == BOT:
            continue
        recent = CHAT[max(0, index - RECENT_TURNS) : index]
        context = "最近對話：\n" + "\n".join(
            f"{'你' if who == BOT else who}：{line}" for who, line in recent
        )
        user = (
            "/no_think\n下面是真實 Discord 語音對話（語音辨識結果，可能有錯字、沒有標點）。"
            f"「你」是你自己之前說過的話。\n\n{context}\n\n最後一句：\n{speaker}：{text}\n\n"
            f"{REMINDER}\n你要說："
        )
        requests.append(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
        )
    return requests


def new_format_requests() -> list[list[dict]]:
    """Append-only chat: others are user turns, the character's lines are assistant turns."""
    requests = []
    messages: list[dict] = [{"role": "system", "content": SYSTEM}]
    for index, (speaker, text) in enumerate(CHAT):
        if speaker == BOT:
            messages.append({"role": "assistant", "content": text})
            continue
        line = f"{speaker}：{text}"
        if messages[-1]["role"] == "user":
            messages[-1] = {
                "role": "user",
                "content": messages[-1]["content"] + "\n" + line,
            }
        else:
            messages.append({"role": "user", "content": line})
        if index >= TIMED_FROM:
            requests.append([dict(m) for m in messages])
    return requests


# ---------------------------------------------------------------- TTS / STT


def synth_clip(rows: list[tuple[str, str]]) -> bytes | None:
    """EdgeTTS timing, and the clip doubles as STT input."""
    try:
        import edge_tts
    except ImportError:
        rows.append(("TTS", "沒裝 edge_tts"))
        return None

    async def once() -> tuple[float, float, bytes]:
        started = time.perf_counter()
        first = None
        audio = bytearray()
        async for chunk in edge_tts.Communicate(TTS_LINE, TTS_VOICE).stream():
            if chunk["type"] == "audio":
                if first is None:
                    first = time.perf_counter() - started
                audio.extend(chunk["data"])
        return (
            (first or 0.0) * 1000,
            (time.perf_counter() - started) * 1000,
            bytes(audio),
        )

    runs = []
    for _ in range(3):
        try:
            runs.append(asyncio.run(once()))
        except Exception as exc:  # network errors come in many types
            rows.append(("TTS", f"失敗：{type(exc).__name__}（沒網路就加 --skip-tts）"))
            return None
    first = statistics.median(r[0] for r in runs)
    total = statistics.median(r[1] for r in runs)
    rows.append(("TTS 第一段聲音", f"{first:.0f} ms"))
    rows.append(("TTS 整句合成完", f"{total:.0f} ms（{len(TTS_LINE)} 字）"))
    return runs[-1][2]


def bench_stt(rows: list[tuple[str, str]], mp3: bytes | None) -> None:
    if mp3 is None:
        rows.append(("STT", "跳過（需要 TTS 產生的測試音檔）"))
        return
    try:
        import numpy as np
        import sherpa_onnx
    except ImportError as exc:
        raise BenchError(f"缺套件 {exc.name}") from exc
    model_dir = PROJECT_DIR / ".runtime" / "models" / "asr"
    if not (model_dir / "encoder.int8.onnx").is_file():
        raise BenchError(f"找不到 {model_dir}")
    ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not ffmpeg:
        raise BenchError("找不到 ffmpeg")
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
        handle.write(mp3)
        clip = Path(handle.name)
    try:
        pcm = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(clip),
                "-f",
                "f32le",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-",
            ],
            capture_output=True,
            timeout=30,
            creationflags=NO_WINDOW,
        ).stdout
    finally:
        clip.unlink(missing_ok=True)
    samples = np.frombuffer(pcm, dtype=np.float32)
    seconds = len(samples) / 48000
    # Same recognizer settings as silenttalk/copilot/stt.py.
    recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(
        tokens=str(model_dir / "tokens.txt"),
        encoder=str(model_dir / "encoder.int8.onnx"),
        decoder=str(model_dir / "decoder.int8.onnx"),
        num_threads=1,
        sample_rate=16000,
        feature_dim=80,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=0.8,
        rule3_min_utterance_length=20.0,
        decoding_method="greedy_search",
        provider="cpu",
    )
    stream = recognizer.create_stream()
    busy = 0.0
    step = 48000 // 50  # 20 ms Discord packets
    for start in range(0, len(samples), step):
        began = time.perf_counter()
        stream.accept_waveform(48000, samples[start : start + step])
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        busy += time.perf_counter() - began
    began = time.perf_counter()
    stream.accept_waveform(48000, np.zeros(int(0.66 * 48000), dtype=np.float32))
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    finalize = (time.perf_counter() - began) * 1000
    text = recognizer.get_result(stream).strip()
    rows.append(
        (
            "STT 解碼速度",
            f"{busy / seconds:.3f} × 即時（{seconds:.1f} s 音檔，越小越好）",
        )
    )
    rows.append(
        ("STT 句尾收尾", f"{finalize:.0f} ms（另外固定等 0.8 s 靜音才判定講完）")
    )
    rows.append(("STT 聽到", text or "（空白）"))


if __name__ == "__main__":
    sys.exit(main())

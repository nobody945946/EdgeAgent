"""Training data for the character-mode LoRA, written by a local open model.

One adapter for every persona a user may write (a wife, a kid, a gaming
buddy, a chuunibyou...), so the data covers hundreds of synthetic personas,
most of them with a relationship to the owner, who is in the chat.

Steps (each appends to .runtime/dev/chardata/*.jsonl and resumes):

    personas  the teacher designs character sheets
    scripts   the teacher writes whole voice chats with the character in them;
              who each line is aimed at is written down with the line, so
              "should the character speak now" is known by construction
    refine    at each line the character says, the teacher writes four more
              versions with the app's own request and picks the most natural
    render    both requests the app makes, as training rows: the 是/否 gate
              (every line) and the line itself (where it speaks); speech-
              recognition noise on the others' lines

    python scripts/dev/character_data.py personas --count 300
    python scripts/dev/character_data.py scripts --per-persona 3
    python scripts/dev/character_data.py refine
    python scripts/dev/character_data.py render

The teacher must be an open-weight model whose license allows training on
its outputs; never Claude, which may only evaluate (HANDOFF rule 8). Names,
personas and scenes of the M1 test set (scripts/dev/turn_eval_data.jsonl)
are excluded. Everything is synthetic; no real conversation is involved.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from silenttalk.copilot.llm import (  # noqa: E402
    _JOIN_AFTER_QUIET_LINES as JOIN_AFTER_QUIET_LINES,
)
from silenttalk.copilot.llm import LocalReplyModel, quiet_for  # noqa: E402
from silenttalk.copilot.persona import (  # noqa: E402
    PRESET_PERSONAS,
    REPLY_LENGTHS,
    TEXT_FIELDS,
    TRAIT_AXES,
    Persona,
    PersonaError,
    Relationship,
    new_persona_id,
    persona_from_dict,
    persona_to_dict,
)
from silenttalk.copilot.script import to_taiwan_traditional  # noqa: E402
from silenttalk.copilot.types import ConversationTurn  # noqa: E402

OUT_DIR = PROJECT_DIR / ".runtime" / "dev" / "chardata"
EVAL_DATA = Path(__file__).with_name("turn_eval_data.jsonl")
_WRITE_LOCK = threading.Lock()

# Who the character is to the owner. Most personas people write are about a
# relationship ("I want a wife / a kid / a brother"), so most data has one.
ROLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # (who the character is to the owner, how the character names the owner)
    ("老婆", ("我老公",)),
    ("老公", ("我老婆",)),
    ("女朋友", ("我男朋友", "我女朋友")),
    ("男朋友", ("我女朋友", "我男朋友")),
    ("曖昧中的對象（成年）", ("最近在曖昧的人",)),
    ("兒子（國小）", ("我爸", "我媽")),
    ("女兒（國中）", ("我爸", "我媽")),
    ("兒子（高中）", ("我爸", "我媽")),
    ("弟弟", ("我哥", "我姊")),
    ("妹妹", ("我哥", "我姊")),
    ("哥哥", ("我弟", "我妹")),
    ("姊姊", ("我弟", "我妹")),
    ("兄弟（死黨）", ("我兄弟，認識十幾年",)),
    ("閨蜜", ("我閨蜜",)),
    ("媽媽", ("我兒子", "我女兒")),
    ("爸爸", ("我兒子", "我女兒")),
    ("阿嬤", ("我孫子", "我孫女")),
    ("阿公", ("我孫子", "我孫女")),
    ("室友", ("我室友",)),
    ("同事", ("我同事，坐我隔壁",)),
    ("學長", ("我學弟", "我學妹")),
    ("學姊", ("我學弟", "我學妹")),
    ("師父", ("我徒弟",)),
    ("徒弟", ("我師父",)),
    ("青梅竹馬", ("從小一起長大的青梅竹馬",)),
    ("表弟", ("我表哥", "我表姊")),
    ("乾妹", ("我乾哥", "我乾姊")),
    ("寵物貓（擬人，會說話）", ("我的主人",)),
    ("管家", ("我服侍的主人",)),
    ("鄰居阿姨", ("隔壁的年輕人，我看著長大的",)),
    ("網友（常一起打遊戲）", ("常一起打遊戲的網友",)),
    ("隊長", ("我帶的隊員",)),
    ("老同學", ("我高中同學",)),
)
ROLE_SHARE = 0.8
# Personality spread, independent of the role.
ARCHETYPES = (
    "熱血",
    "佛系",
    "毒舌但心軟",
    "老派",
    "溫柔",
    "傲嬌",
    "天然呆",
    "中二",
    "社恐",
    "話很多",
    "冷靜理性",
    "愛抱怨但講義氣",
    "搞笑擔當",
    "完美主義",
    "健忘",
    "愛吃",
    "運動咖",
    "宅",
    "文青",
    "直腸子",
    "愛操心",
    "神經大條",
    "愛吹牛",
    "講台語很溜",
    "客家人",
    "原住民部落長大",
    "香港來台",
    "馬來西亞僑生",
    "理組",
    "藝術家脾氣",
)
NAMES = (
    "阿傑",
    "小雨",
    "Ken",
    "小美",
    "阿豪",
    "Leo",
    "小安",
    "阿翔",
    "Mia",
    "阿偉",
    "Kevin",
    "阿宏",
    "小白",
    "Jason",
    "小萱",
    "Amy",
    "阿志",
    "小婷",
    "阿倫",
    "Eric",
    "小瑜",
    "阿寬",
    "小潔",
    "Tony",
    "阿德",
    "小琪",
    "阿成",
    "Nina",
    "阿修",
    "小宇",
    "Joe",
    "阿哲",
    "小薇",
    "阿達",
    "小熊",
    "Ray",
    "阿賢",
    "小蘋",
    "阿龍",
    "Coco",
    "小羽",
    "阿勇",
    "Sam",
    "小晨",
    "阿凱文",
    "小彤",
    "Ivy",
    "阿峰",
    "小樂",
    "Andy",
)
TOPICS = (
    "打排位",
    "開黑前等人",
    "網路 lag",
    "被隊友雷",
    "打贏了在慶祝",
    "晚餐要吃什麼",
    "宵夜外送",
    "上班好累",
    "老闆很煩",
    "期末考",
    "面試",
    "找工作",
    "感情煩惱",
    "朋友生日",
    "寵物趣事",
    "颱風天",
    "追劇",
    "新番",
    "電影",
    "演唱會搶票",
    "組電腦",
    "手機壞了",
    "搬家",
    "租房子",
    "存錢",
    "減肥",
    "感冒",
    "失眠",
    "旅遊計畫",
    "通勤",
    "家人碎念",
    "要不要退坑",
    "遊戲改版",
    "週末要幹嘛",
    "小孩功課",
    "回老家",
    "煮飯",
    "買菜",
    "紀念日",
    "吵架和好",
    "打掃家裡",
    "天馬行空的假設問題",
    "講八卦",
    "運動比賽",
    "有人遲到",
    "有人要先下線",
)
MOMENTS = (
    "有人講到{name}，但不是在跟{name}說話（第三人稱）",
    "有人用「你」在跟另一個人說話，不是{name}",
    "{name}剛說完話，有人直接回應{name}說的內容，沒叫名字",
    "有人用暱稱或叫錯{name}的名字",
    "有人半開玩笑問{name}是不是 AI、機器人或真人",
    "有人叫{name}幫忙寫程式、查資料、寫作業或設鬧鐘（{name}做不到，用角色自己的方式推掉）",
    "其他人之間在講私事，跟{name}無關",
    "有人問在場所有人一個問題",
    "{owner}跟{name}撒嬌、拌嘴或關心{name}",
    "有人吐槽或虧{name}",
    "話題冷掉一下，又有人開了新話題",
    "{owner}跟別人聊自己的事，沒有要{name}回",
)
LINE_COUNTS = (18, 22, 24, 26, 28, 30, 32, 36)
# Speech-recognition confusions seen in this app's STT tests, plus common
# homophones; applied rarely, to the others' spoken lines only.
CONFUSIONS = (
    ("在", "再"),
    ("再", "在"),
    ("的", "得"),
    ("那", "哪"),
    ("做", "作"),
    ("已經", "以經"),
    ("打野", "達也"),
    ("補師", "不是"),
    ("好", "號"),
    ("吧", "八"),
    ("欸", "誒"),
    ("什麼", "甚麼"),
    ("是不是", "4不4"),
    ("幹嘛", "幹麻"),
    ("沒有", "沒又"),
)
PUNCTUATION = re.compile(r"[，。！？、,.!?~～…「」]")
SAMPLES = 4
# What personas.jsonl stores beside the persona itself.
_EXTRA_KEYS = ("role", "owner_relation", "archetype", "stt_variants")


def chat(
    url: str,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    n: int = 1,
) -> list[str]:
    """The teacher. Retries while the pipeline supervisor restarts it."""
    body = {
        "messages": messages,
        "temperature": temperature,
        "top_p": 0.9,
        "max_tokens": max_tokens,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    texts = []
    # One request per sample (not every llama-server build honours "n"); the
    # shared prompt is cached after the first.
    for _ in range(n):
        deadline = time.monotonic() + 20 * 60
        while True:
            try:
                with urllib.request.urlopen(request, timeout=900) as response:
                    texts.append(
                        str(
                            json.load(response)["choices"][0]["message"]["content"]
                            or ""
                        )
                    )
                break
            except (OSError, json.JSONDecodeError) as exc:
                if time.monotonic() >= deadline:
                    raise TeacherUnavailable(str(exc)) from exc
                time.sleep(3)
    return [re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip() for t in texts]


class TeacherUnavailable(RuntimeError):
    pass


def _json_block(text: str, opener: str) -> Any:
    closer = "}" if opener == "{" else "]"
    start, end = text.find(opener), text.rfind(closer)
    if start < 0 or end <= start:
        raise ValueError("no JSON")
    return json.loads(text[start : end + 1])


def _append(path: Path, record: dict[str, Any]) -> None:
    with _WRITE_LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # a line cut short when the process was killed mid-write
    return rows


def _pool(workers: int, work: Any, jobs: list[Any]) -> None:
    with ThreadPoolExecutor(workers) as pool:
        for n, _ in enumerate(pool.map(work, jobs), 1):
            if n % 20 == 0:
                print(f"  {n}/{len(jobs)}", flush=True)


def held_out() -> tuple[set[str], set[str]]:
    """Persona and human names the M1 test set uses; never in training data."""
    meta = json.loads(EVAL_DATA.read_text(encoding="utf-8").splitlines()[0])["meta"]
    personas = set(meta.get("persona_names_used", []))
    personas |= {p.name for p in PRESET_PERSONAS}
    personas |= {n for p in PRESET_PERSONAS for n in p.nickname.split("、") if n}
    return personas, set(meta.get("human_names_used", []))


# ---------------------------------------------------------------- personas

PERSONA_SYSTEM = "你是角色設計師，為台灣的 Discord 語音聊天設計具體、可信、有個性的虛構人物。只輸出 JSON。"


def persona_request(rng: random.Random) -> tuple[str, dict[str, Any]]:
    traits = {axis.key: rng.choice((1, 2, 3, 3, 4, 5)) for axis in TRAIT_AXES}
    role, owner_relation = "", ""
    if rng.random() < ROLE_SHARE:
        role, names = rng.choice(ROLES)
        owner_relation = rng.choice(names)
    skeleton = {
        "role": role,
        "owner_relation": owner_relation,
        "archetype": rng.choice(ARCHETYPES),
        "reply_length": rng.choice(tuple(REPLY_LENGTHS)),
        "traits": traits,
    }
    trait_text = "、".join(Persona(id="x", name="", traits=traits)._trait_phrases())
    fields = "\n".join(
        f'- "{key}"：{label}（最多 {limit} 字）'
        for key, (label, _p, limit) in TEXT_FIELDS.items()
    )
    # Users write anything from a few lines to every field full.
    size = rng.choice(("150", "250", "350", "500"))
    blank = rng.choice(("兩", "三", "四", "五"))
    who = (
        f"他是使用者（在語音頻道裡的真人）的「{role}」，使用者是他的「{owner_relation.removeprefix('我')}」。"
        "人設要從這個身分出發（關心的事、說話的樣子、怎麼稱呼使用者）。"
        if role
        else "他是使用者的普通朋友。"
    )
    user = f"""設計一個虛構人物，會在 Discord 語音頻道裡跟使用者和朋友們聊天。
{who}
個性基調：{skeleton["archetype"]}；{trait_text or "沒有特別極端"}

輸出一個 JSON 物件，欄位：
- "name"：名字或暱稱（2～5 字，不要用小明、阿凱、凱哥這類常見範例名）
- "stt_variants"：語音辨識可能把他的名字聽錯成的寫法，1～2 個（同音或近音字）
{fields}

要求：台灣繁體中文；像一般人在 App 裡隨手填的人設：大部分欄位一句話就好，整份約 {size} 字，{blank} 個欄位留空字串；具體、有生活細節；慾望、害怕、價值觀要能影響他怎麼回話；說話方式要可辨識（用詞、語助詞、句長），不要每句都用「欸」開頭；nickname 只寫 1～3 個稱呼，用「、」分隔；代表台詞 2～4 句、用 \\n 分隔、口語自然、不加引號；口頭禪 1～3 個、用「、」分隔。
親密關係（老婆、男女朋友、曖昧）只能是成年人；未成年角色只有家人或朋友的日常，不涉及戀愛。"""
    return user, skeleton


def make_personas(args: argparse.Namespace) -> None:
    path = OUT_DIR / "personas.jsonl"
    banned, _ = held_out()
    while (have := len(_read(path))) < args.count:
        rng = random.Random(1000 + have)
        jobs = [persona_request(rng) for _ in range(args.count - have)]

        def work(job: tuple[str, dict[str, Any]]) -> None:
            user, skeleton = job
            try:
                raw = _json_block(
                    chat(
                        args.teacher_url,
                        [
                            {"role": "system", "content": PERSONA_SYSTEM},
                            {"role": "user", "content": user},
                        ],
                        temperature=0.95,
                        max_tokens=1000,
                    )[0],
                    "{",
                )
                raw = {
                    k: to_taiwan_traditional(v) if isinstance(v, str) else v
                    for k, v in raw.items()
                }
                variants = [
                    to_taiwan_traditional(str(v)) for v in raw.pop("stt_variants", [])
                ][:2]
                for key, (_label, _p, limit) in TEXT_FIELDS.items():
                    raw[key] = str(raw.get(key, "")).strip()[:limit]
                raw["sample_lines"] = "\n".join(
                    line.strip().strip("「」『』\"'")
                    for line in raw["sample_lines"].replace("\\n", "\n").splitlines()
                )
                raw["nickname"] = "、".join(
                    n.strip()
                    for n in re.split(r"[、,，/]", raw["nickname"])
                    if 0 < len(n.strip()) <= 6
                )[:20]
                if str(raw.get("name", "")).strip() in banned:
                    raise ValueError("held-out name")
                raw.update(
                    id=new_persona_id(),
                    traits=skeleton["traits"],
                    reply_length=skeleton["reply_length"],
                )
                persona = persona_from_dict(raw)
            except (ValueError, PersonaError, KeyError, TypeError) as exc:
                print(f"persona skipped: {type(exc).__name__}", flush=True)
                return
            _append(
                path,
                persona_to_dict(persona)
                | {
                    "role": skeleton["role"],
                    "owner_relation": skeleton["owner_relation"],
                    "archetype": skeleton["archetype"],
                    "stt_variants": variants,
                },
            )

        _pool(args.workers, work, jobs)


# ---------------------------------------------------------------- scripts

SCRIPT_SYSTEM = (
    "你是寫實的對話編劇，寫台灣人在 Discord 語音頻道裡的真實聊天逐字稿。只輸出 JSON。"
)

ENGAGEMENT_BRIEF = {
    "called": "{name}只有在有人叫他、問他、要他做什麼，或直接回應他剛說的話時才開口；其他時候完全不說話（別人在聊、講到他、跟別人說話時都不插嘴）。",
    "active": "{name}是聊天的一份子：被叫到、被問到、有人回應他時一定會回；話題他有興趣時也會自然插一句，但大部分時間讓別人聊，大約每四五句才開口一次，別人在講私事時不插嘴。",
}


def script_request(
    persona: dict[str, Any], rng: random.Random, held_humans: set[str]
) -> dict[str, Any]:
    name = persona["name"]
    pool = [n for n in NAMES if n not in held_humans and n != name]
    humans = rng.sample(pool, rng.choice((2, 2, 3, 3, 4)))
    owner = humans[0]
    relations = []
    if persona.get("owner_relation"):
        relations.append((owner, persona["owner_relation"]))
    for other in humans[1:]:
        if rng.random() < 0.3:
            relations.append(
                (
                    other,
                    rng.choice(
                        (
                            "常一起打遊戲的朋友",
                            f"{owner}的同事",
                            f"{owner}的大學同學",
                            "剛認識的朋友",
                        )
                    ),
                )
            )
    engagement = "called" if rng.random() < 0.65 else "active"
    return {
        "persona_id": persona["id"],
        "humans": humans,
        "owner": owner,
        "relations": relations,
        "engagement": engagement,
        "honesty": "admit" if rng.random() < 0.1 else "in_character",
        "can_type": rng.random() < 0.3,
        "with_summary": rng.random() < 0.25,
        "topic": rng.choice(TOPICS),
        "moments": rng.sample(MOMENTS, 3),
        "length": rng.choice(LINE_COUNTS),
    }


def script_prompt(persona: Persona, spec: dict[str, Any]) -> str:
    name, owner = persona.name, spec["owner"]
    sheet = persona.render_system_block()
    relation_text = (
        "\n".join(f"- {who}：{rel}" for who, rel in spec["relations"])
        or "（沒有特別關係，都是朋友）"
    )
    moments = "\n".join(
        f"- {m.format(name=name, owner=owner)}" for m in spec["moments"]
    )
    honesty = (
        "被認真問是不是 AI 時，他會用自己的口吻承認自己是 AI 扮演的角色"
        if spec["honesty"] == "admit"
        else "被問是不是 AI 時，他當成朋友在開玩笑，用自己的方式帶過或反問，絕不承認是程式"
    )
    typing = (
        "有一兩句是別人在聊天室打字 @{name}（text 欄位前面加「[打字]」），{name}也用打字回。".format(
            name=name
        )
        if spec["can_type"]
        else ""
    )
    summary = (
        "這段對話是接在一段更長的聊天後面：先用一兩句寫出稍早聊過的重點（summary_before），逐字稿接著寫。"
        if spec["with_summary"]
        else "summary_before 留空字串。"
    )
    return f"""寫一段 Discord 語音頻道的語音辨識逐字稿，約 {spec["length"]} 句。
在場的人：{"、".join(spec["humans"])}，還有「{name}」。{owner} 是頻道主人。

「{name}」的人設：
{sheet}

{name}和在場的人的關係：
{relation_text}

主題：{spec["topic"]}
這段對話裡要自然出現：
{moments}

{ENGAGEMENT_BRIEF[spec["engagement"]].format(name=name)}
{name}說話要完全是人設裡這個人的口吻：口語、短、有個性，像真人朋友脫口而出，不像客服、老師或旁白；口頭禪全場最多用兩三次；不要每句都用「欸」開頭；只說人設裡寫到的關於自己的事，不編新經歷；{honesty}；他只能在頻道裡聊天，不會真的去寫程式、查資料或設提醒。
其他人講話像真實的語音聊天：短句、會打斷、會岔題、不會每句都叫名字，同一個人可以連講兩三句。{typing}
{summary}
不寫性內容；未成年角色不涉及戀愛；不人身攻擊。

輸出 JSON：{{"summary_before": "…", "lines": [{{"s": "說話的人", "t": "內容", "to": "這句是對誰說的"}}, …]}}
to 只能是：「{name}」、在場某個人的名字、或「大家」。{name}自己說的話 to 填他在回應的人。"""


def make_scripts(args: argparse.Namespace) -> None:
    personas = {p["id"]: p for p in _read(OUT_DIR / "personas.jsonl")}
    if not personas:
        raise SystemExit("run the personas step first")
    path = OUT_DIR / "scripts.jsonl"
    _, held_humans = held_out()
    done: dict[str, int] = {}
    for row in _read(path):
        done[row["spec"]["persona_id"]] = done.get(row["spec"]["persona_id"], 0) + 1
    rng = random.Random(2000 + sum(done.values()))
    jobs = []
    for pid, raw in personas.items():
        for _ in range(max(0, args.per_persona - done.get(pid, 0))):
            jobs.append(script_request(raw, rng, held_humans))
    drops: dict[str, int] = {}

    def work(spec: dict[str, Any]) -> None:
        raw = personas[spec["persona_id"]]
        persona = _persona(raw)
        try:
            data = _json_block(
                chat(
                    args.teacher_url,
                    [
                        {"role": "system", "content": SCRIPT_SYSTEM},
                        {"role": "user", "content": script_prompt(persona, spec)},
                    ],
                    temperature=0.9,
                    max_tokens=3000,
                )[0],
                "{",
            )
            lines, reason = check_script(data, persona.name, spec)
        except (ValueError, TypeError, AttributeError) as exc:
            lines, reason = [], type(exc).__name__
        if reason:
            with _WRITE_LOCK:
                drops[reason] = drops.get(reason, 0) + 1
            return
        _append(
            path,
            {
                "spec": spec,
                "summary_before": to_taiwan_traditional(
                    str(data.get("summary_before", ""))
                )[:200]
                if spec["with_summary"]
                else "",
                "lines": lines,
            },
        )

    _pool(args.workers, work, jobs)
    print(f"scripts done; dropped {drops}", flush=True)


def check_script(
    data: dict[str, Any], name: str, spec: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    """Normalize a teacher script; a reason string when it is unusable."""
    speakers = set(spec["humans"]) | {name}
    lines: list[dict[str, Any]] = []
    for raw in data.get("lines", []):
        speaker = str(raw.get("s", "")).strip()
        text = to_taiwan_traditional(str(raw.get("t", "")).strip())
        target = str(raw.get("to", "")).strip()
        if not text or speaker not in speakers:
            return [], "unknown speaker"
        typed = text.startswith("[打字]")
        text = text.removeprefix("[打字]").strip()
        if speaker == name:
            # The app strips these from what the model says; so does the data.
            text = text.strip("\"'「」『』*").strip()
        if lines and lines[-1]["s"] == name and speaker == name:
            return [], "character twice in a row"
        lines.append(
            {
                "s": speaker,
                "t": text,
                "to": target,
                "via": "text" if typed and spec["can_type"] else "voice",
            }
        )
    said = [line for line in lines if line["s"] == name]
    if len(lines) < 12:
        return [], "too short"
    if len(said) < (3 if spec["engagement"] == "called" else 4):
        return [], "character too quiet"
    if len(said) > len(lines) * 0.45:
        return [], "character too chatty"
    if sum(line["to"] == name for line in lines if line["s"] != name) < 3:
        return [], "too few lines for the character"
    return lines, ""


# ---------------------------------------------------------------- labels


def decision_points(script: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Every line from someone else, with whether the character speaks next.

    A point whose label contradicts the line's own addressee is dropped as
    ambiguous: in "called" mode the character answers exactly the lines
    aimed at it; in "active" mode it must answer those and may add others.
    """
    lines, engagement = script["lines"], script["spec"]["engagement"]
    points = []
    for index, line in enumerate(lines):
        if line["s"] == name:
            continue
        speaks = index + 1 < len(lines) and lines[index + 1]["s"] == name
        aimed = line["to"] == name
        if aimed != speaks and (engagement == "called" or aimed):
            continue
        points.append(
            {
                "index": index,
                "speaks": speaks,
                "aimed": aimed,
                "mention": aimed and line["via"] == "text",
            }
        )
    return points


# ---------------------------------------------------------------- refine

PICK_PROMPT = """下面是 Discord 語音聊天的最後幾句，以及角色「{name}」接話的 {k} 個候選。
角色人設：
{sheet}

對話：
{chat}

候選：
{options}

哪一句最像這個人在語音裡真的會脫口而出的話？標準：口語自然、不做作、不像客服或旁白，符合人設的口吻和立場，接得上前一句，長度合適，不要硬塞口頭禪。
只回答一個數字（1 到 {k}）。"""

_AI_CLAIM = re.compile(r"(我是|身為)(一個)?\s*(AI|人工智慧|機器人|語言模型|程式)", re.I)
_ASSISTANT = re.compile(
    r"(以下是|```|def |import |步驟一|第一步[，,：:]|好的，我來幫你)"
)


def conversation(
    script: dict[str, Any], persona: Persona, noisy: bool
) -> tuple[Persona, list[ConversationTurn]]:
    """The persona as the app would hold it (with relationships), and the chat
    as turns; `noisy` applies speech-recognition noise to spoken lines."""
    spec = script["spec"]
    persona = replace(
        persona, relationships=tuple(Relationship(w, r) for w, r in spec["relations"])
    )
    rng = random.Random(hash_seed(script))
    ids = {h: n + 1 for n, h in enumerate(spec["humans"])}
    turns = []
    for line in script["lines"]:
        if line["s"] == persona.name:
            turns.append(
                ConversationTurn(
                    0, persona.name, line["t"], is_self=True, via=line["via"]
                )
            )
            continue
        text = line["t"]
        if noisy and line["via"] == "voice":
            text = stt_noise(text, persona, script, rng)
        turns.append(ConversationTurn(ids[line["s"]], line["s"], text, via=line["via"]))
    return persona, turns


def hash_seed(script: dict[str, Any]) -> int:
    return sum(ord(c) for c in json.dumps(script["lines"][:3], ensure_ascii=False))


def stt_noise(
    text: str, persona: Persona, script: dict[str, Any], rng: random.Random
) -> str:
    if rng.random() < 0.85:
        text = PUNCTUATION.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()
    variants = script.get("stt_variants") or []
    if variants and rng.random() < 0.25:
        for name in [persona.name, *persona.nickname.split("、")]:
            if len(name) >= 2 and name in text:
                text = text.replace(name, rng.choice(variants), 1)
                break
    if rng.random() < 0.12:
        right, wrong = rng.choice(CONFUSIONS)
        text = text.replace(right, wrong, 1)
    return text


def reply_model(persona: Persona, spec: dict[str, Any]) -> LocalReplyModel:
    model = LocalReplyModel(
        Path("unused.gguf"), can_type=spec["can_type"], ai_honesty=spec["honesty"]
    )
    model.set_persona(persona)
    return model


def make_refine(args: argparse.Namespace) -> None:
    personas = {p["id"]: p for p in _read(OUT_DIR / "personas.jsonl")}
    path = OUT_DIR / "refined.jsonl"
    done = {(r["script"], r["index"]) for r in _read(path)}
    scripts = _read(OUT_DIR / "scripts.jsonl")
    jobs = []
    for number, script in enumerate(scripts):
        raw = personas[script["spec"]["persona_id"]]
        for point in decision_points(script, raw["name"]):
            if point["speaks"] and (number, point["index"]) not in done:
                jobs.append((number, point["index"], point["aimed"]))
    print(f"refine: {len(jobs)} lines to improve", flush=True)
    stats = {"kept_script": 0, "replaced": 0}

    def work(job: tuple[int, int, bool]) -> None:
        number, index, aimed = job
        raw = personas[scripts[number]["spec"]["persona_id"]]
        script = dict(scripts[number], stt_variants=raw.get("stt_variants", []))
        persona, turns = conversation(script, _persona(raw), noisy=True)
        scripted = script["lines"][index + 1]["t"]
        model = reply_model(persona, script["spec"])
        for turn in turns[: index + 1]:
            if turn.is_self:
                model._recent_character_lines.append(turn.text)
        # The request the app sends at this point: answering, or joining in.
        messages = model.character_messages(
            turns=turns[: index + 1],
            summary=script["summary_before"],
            addressed=aimed,
            joining=not aimed,
            engagement=script["spec"]["engagement"],
        )
        outs = chat(
            args.teacher_url, messages, temperature=0.9, max_tokens=90, n=SAMPLES
        )
        newest = turns[index].text
        candidates = [scripted] + [model._parse_character_line(o, newest) for o in outs]
        candidates = [
            c for c in dict.fromkeys(candidates) if usable(c, script["spec"], args)
        ]
        if not candidates:
            return
        # A small model copies whatever opener dominates its data.
        plain = [c for c in candidates if not c.startswith(("欸", "誒"))]
        options = plain or candidates
        best = (
            options[0]
            if len(options) == 1
            else pick(args, persona, turns[: index + 1], options)
        )
        with _WRITE_LOCK:
            stats["kept_script" if best == scripted else "replaced"] += 1
        _append(
            path,
            {"script": number, "index": index, "line": best, "candidates": candidates},
        )

    _pool(args.workers, work, jobs)
    print(f"refine done {stats}", flush=True)


def usable(line: str, spec: dict[str, Any], args: argparse.Namespace) -> bool:
    if not line or len(line) > args.max_chars or line.upper().startswith("SKIP"):
        return False
    if spec["honesty"] != "admit" and _AI_CLAIM.search(line):
        return False
    return not _ASSISTANT.search(line)


def pick(
    args: argparse.Namespace,
    persona: Persona,
    turns: list[ConversationTurn],
    options: list[str],
) -> str:
    chat_text = "\n".join(
        f"{t.speaker_name}（角色）：{t.text}"
        if t.is_self
        else f"{t.speaker_name}：{t.text}"
        for t in turns[-8:]
    )
    prompt = PICK_PROMPT.format(
        name=persona.name,
        sheet=persona.render_system_block(),
        chat=chat_text,
        k=len(options),
        options="\n".join(f"{n}. {o}" for n, o in enumerate(options, 1)),
    )
    answer = chat(
        args.teacher_url,
        [{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=8,
    )[0]
    match = re.search(r"\d+", answer)
    choice = int(match.group(0)) if match else 1
    return options[choice - 1] if 1 <= choice <= len(options) else options[0]


def _persona(raw: dict[str, Any]) -> Persona:
    return persona_from_dict({k: v for k, v in raw.items() if k not in _EXTRA_KEYS})


# ---------------------------------------------------------------- render


def make_render(args: argparse.Namespace) -> None:
    personas = {p["id"]: p for p in _read(OUT_DIR / "personas.jsonl")}
    scripts = _read(OUT_DIR / "scripts.jsonl")
    refined = {
        (r["script"], r["index"]): r["line"] for r in _read(OUT_DIR / "refined.jsonl")
    }
    rng = random.Random(4000)
    gate_yes, gate_no, replies = [], [], []
    for number, script in enumerate(scripts):
        raw = personas[script["spec"]["persona_id"]]
        noisy_script = dict(script, stt_variants=raw.get("stt_variants", []))
        persona, turns = conversation(noisy_script, _persona(raw), noisy=True)
        spec = script["spec"]
        model = reply_model(persona, spec)
        for point in decision_points(script, persona.name):
            index = point["index"]
            model._recent_character_lines.clear()
            for turn in turns[: index + 1]:
                if turn.is_self:
                    model._recent_character_lines.append(turn.text)
            history = turns[: index + 1]
            meta = {
                "script": number,
                "index": index,
                "engagement": spec["engagement"],
                "persona_id": persona.id,
                "role": raw.get("role", ""),
            }
            # The same questions the app asks (LocalReplyModel.character_turn):
            # "is this line for you" on every line, and in "active" mode,
            # after a quiet stretch, "would joining in now be natural".
            if not point["mention"]:
                asked = [("called", point["aimed"])]
                if (
                    spec["engagement"] == "active"
                    and not point["aimed"]
                    and quiet_for(history) >= JOIN_AFTER_QUIET_LINES
                ):
                    asked.append(("active", point["speaks"]))
                for question, answer in asked:
                    gate = model.gate_messages(
                        turns=history,
                        summary=script["summary_before"],
                        engagement=spec["engagement"],
                        question=question,
                    )
                    if gate is None:
                        continue
                    row = meta | {
                        "kind": f"gate_{question}",
                        "messages": gate
                        + [{"role": "assistant", "content": "是" if answer else "否"}],
                    }
                    (gate_yes if answer else gate_no).append(row)
            if point["speaks"]:
                line = refined.get((number, index))
                if not line:
                    continue
                messages = model.character_messages(
                    turns=history,
                    summary=script["summary_before"],
                    addressed=point["aimed"],
                    joining=not point["aimed"],
                    engagement=spec["engagement"],
                )
                replies.append(
                    meta
                    | {
                        "kind": "line",
                        "messages": messages + [{"role": "assistant", "content": line}],
                    }
                )
    # A small model copies whatever opener dominates its lines; the teacher
    # sometimes offers nothing but "欸…", so cap it here.
    rng.shuffle(replies)
    opener = [
        r for r in replies if r["messages"][-1]["content"].startswith(("欸", "誒"))
    ]
    others = [r for r in replies if r not in opener]
    replies = others + opener[: int(len(others) * 0.12)]
    # Real chats are mostly lines not meant for the character; keep that
    # lean without letting "否" swamp the gate.
    rng.shuffle(gate_no)
    gate_no = gate_no[: len(gate_yes) * 2]
    rows = gate_yes + gate_no + replies
    rows = [
        r
        for r in rows
        if sum(len(m["content"]) for m in r["messages"]) <= args.max_prompt_chars
    ]
    rng.shuffle(rows)
    out = OUT_DIR / "train.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    lines = [r["messages"][-1]["content"] for r in replies]
    print(
        f"render: {len(gate_yes)} gate 是, {len(gate_no)} gate 否, {len(replies)} lines "
        f"-> {len(rows)} rows in {out}",
        flush=True,
    )
    if lines:
        print(
            f"  lines starting with 欸: {sum(l.startswith('欸') for l in lines) / len(lines):.0%}, "
            f"mean length {sum(map(len, lines)) / len(lines):.1f}",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=("personas", "scripts", "refine", "render"))
    parser.add_argument("--teacher-url", default="http://127.0.0.1:18200")
    parser.add_argument(
        "--workers", type=int, default=4, help="match llama-server --parallel"
    )
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--per-persona", type=int, default=3)
    parser.add_argument("--max-chars", type=int, default=72)
    parser.add_argument("--max-prompt-chars", type=int, default=3400)
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    {
        "personas": make_personas,
        "scripts": make_scripts,
        "refine": make_refine,
        "render": make_render,
    }[args.step](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

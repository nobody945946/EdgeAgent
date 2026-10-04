"""Machine code to plain Traditional Chinese, exactly.

capstone disassembles the bytes and reports each instruction's operands and
the registers it really reads and writes (implicit ones included). The
Chinese for the common instructions of x86-64/x86 and ARM Thumb (the Cortex-M
chips in flight controllers) is fixed here, with the details people miss:
writing a 32-bit register clears the upper half of the 64-bit one (but 8/16-
bit writes do not), LEA computes an address without reading memory, JG/JL
compare signed and JA/JB unsigned, Thumb's PC reads as the instruction's
address + 4 (aligned down to 4 for loads), BL leaves the return address in LR
with bit 0 set. tests/test_lingua_asm.py runs each such claim in the unicorn
emulator. Instructions outside the table are listed with capstone's facts
(what they read and write), never a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

import capstone
from capstone import arm, x86

ARCHS = {
    "x86-64": (capstone.CS_ARCH_X86, capstone.CS_MODE_64),
    "x86-32": (capstone.CS_ARCH_X86, capstone.CS_MODE_32),
    "thumb": (capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB),
    "arm": (capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM),
    "arm64": (capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM),
}
ARCH_NAMES = {"x86-64": "x86-64（64 位元 Intel／AMD）", "x86-32": "x86（32 位元 Intel／AMD）",
              "thumb": "ARM Thumb（Cortex-M 微控制器，例如飛控晶片）", "arm": "ARM（32 位元，A32 指令）",
              "arm64": "ARM64（AArch64）"}

X86_CC = {
    "e": "相等（ZF=1）", "z": "結果為 0（ZF=1）", "ne": "不相等（ZF=0）", "nz": "結果不為 0（ZF=0）",
    "g": "大於（有號比較）", "nle": "大於（有號比較）", "ge": "大於或等於（有號比較）", "nl": "大於或等於（有號比較）",
    "l": "小於（有號比較）", "nge": "小於（有號比較）", "le": "小於或等於（有號比較）", "ng": "小於或等於（有號比較）",
    "a": "大於（無號比較）", "nbe": "大於（無號比較）", "ae": "大於或等於（無號比較，CF=0）", "nb": "大於或等於（無號比較，CF=0）",
    "nc": "沒有進位（CF=0）", "b": "小於（無號比較，CF=1）", "nae": "小於（無號比較，CF=1）", "c": "有進位（CF=1）",
    "be": "小於或等於（無號比較）", "na": "小於或等於（無號比較）", "s": "結果為負（SF=1）", "ns": "結果不為負（SF=0）",
    "o": "溢位（OF=1）", "no": "沒有溢位（OF=0）", "p": "同位（PF=1）", "np": "非同位（PF=0）",
}
ARM_CC = {
    "eq": "相等（Z=1）", "ne": "不相等（Z=0）", "cs": "無號大於或等於（C=1）", "hs": "無號大於或等於（C=1）",
    "cc": "無號小於（C=0）", "lo": "無號小於（C=0）", "mi": "結果為負（N=1）", "pl": "結果不為負（N=0）",
    "vs": "溢位（V=1）", "vc": "沒有溢位（V=0）", "hi": "無號大於", "ls": "無號小於或等於",
    "ge": "有號大於或等於", "lt": "有號小於", "gt": "有號大於", "le": "有號小於或等於", "al": "一定",
}
SIZES = {1: "1 個位元組（byte）", 2: "2 個位元組（word）", 4: "4 個位元組（dword）", 8: "8 個位元組（qword）",
         16: "16 個位元組"}


@dataclass
class Line:
    address: int
    raw: str
    asm: str
    text: str


@dataclass
class Translation:
    arch: str
    lines: list[Line]
    tail: str = ""

    def text(self) -> str:
        out = [f"機器碼（{ARCH_NAMES[self.arch]}，capstone 反組譯）", "逐條說明："]
        for line in self.lines:
            out.append(f"- 0x{line.address:X}  {line.raw:<20} {line.asm}")
            out.append(f"    {line.text}")
        if self.tail:
            out.append(self.tail)
        return "\n".join(out)


def hexnum(value: int) -> str:
    if -10 < value < 10:
        return str(value)
    sign = "-" if value < 0 else ""
    return f"{sign}0x{abs(value):X}（{value}）"


class X86:
    def __init__(self, insn, mode64: bool) -> None:
        self.i, self.mode64 = insn, mode64

    def op(self, k: int) -> str:
        o = self.i.operands[k]
        if o.type == x86.X86_OP_REG:
            return self.i.reg_name(o.reg)
        if o.type == x86.X86_OP_IMM:
            return hexnum(o.imm)
        return f"記憶體位址 {self.address(o)} 的 {SIZES.get(o.size, f'{o.size} 個位元組')}"

    def address(self, o) -> str:
        m = o.mem
        parts = []
        if m.base:
            parts.append(self.i.reg_name(m.base))
        if m.index:
            parts.append(f"{self.i.reg_name(m.index)}×{m.scale}" if m.scale != 1 else self.i.reg_name(m.index))
        text = " + ".join(parts)
        if m.disp or not parts:
            text += (" − " if m.disp < 0 else (" + " if parts else "")) + f"0x{abs(m.disp):X}"
        if m.base == x86.X86_REG_RIP:
            target = self.i.address + self.i.size + m.disp
            text += f"（= 0x{target:X}，相對於下一條指令）"
        segment = f"{self.i.reg_name(m.segment)}:" if m.segment else ""
        return f"[{segment}{text}]"

    def upper_note(self, k: int = 0) -> str:
        o = self.i.operands[k] if self.i.operands else None
        if not self.mode64 or o is None or o.type != x86.X86_OP_REG:
            return ""
        if o.size == 4:
            return f"（寫入 32 位元的 {self.i.reg_name(o.reg)} 時，對應的 64 位元暫存器上半部會被清成 0）"
        if o.size in (1, 2):
            return f"（只改 {self.i.reg_name(o.reg)} 這 {o.size * 8} 位元，暫存器其他位元保持不變）"
        return ""

    def text(self, previous) -> str | None:
        i, n = self.i, self.i.mnemonic
        ops = len(i.operands)
        if n == "mov" and ops == 2:
            return f"把 {self.op(1)} 複製到 {self.op(0)}{self.upper_note()}"
        if n in ("movzx", "movsx", "movsxd"):
            way = "高位補 0" if n == "movzx" else "依正負號擴充（負數高位補 1）"
            return f"把 {self.op(1)} 放進 {self.op(0)}，{way}{self.upper_note()}"
        if n == "lea":
            return f"{self.op(0)} = {self.address(i.operands[1])} 算出來的位址（只計算，不讀記憶體）{self.upper_note()}"
        if n == "xor" and ops == 2 and i.operands[0].type == i.operands[1].type == x86.X86_OP_REG \
                and i.operands[0].reg == i.operands[1].reg:
            return f"把 {self.op(0)} 設成 0（常見寫法）{self.upper_note()}"
        arith = {"add": "加上", "sub": "減掉", "and": "和", "or": "或", "xor": "互斥或", "imul": "乘以",
                 "adc": "加上（再加進位 CF）", "sbb": "減掉（再減借位 CF）"}
        if n in arith and ops == 2:
            word = {"and": "做位元 AND", "or": "做位元 OR", "xor": "做位元 XOR"}.get(n, arith[n])
            return f"{self.op(0)} = {self.op(0)} {word} {self.op(1)}，並更新旗標{self.upper_note()}"
        if n == "imul" and ops == 3:
            return f"{self.op(0)} = {self.op(1)} × {self.op(2)}（有號乘法）{self.upper_note()}"
        if n in ("inc", "dec"):
            return f"{self.op(0)} {'加' if n == 'inc' else '減'} 1，並更新旗標（CF 不變）{self.upper_note()}"
        if n == "neg":
            return f"{self.op(0)} 變成相反數（0 減它）{self.upper_note()}"
        if n == "not":
            return f"{self.op(0)} 的每個位元反過來（不影響旗標）{self.upper_note()}"
        if n in ("shl", "sal", "shr", "sar"):
            how = {"shl": "左移（低位補 0，等於乘以 2 的次方）", "sal": "左移（低位補 0）",
                   "shr": "右移，高位補 0（無號除以 2 的次方）",
                   "sar": "右移，高位補符號位元（有號除以 2 的次方，往負無限大取整）"}[n]
            amount = self.op(1) if ops == 2 else "1"
            return f"{self.op(0)} {how} {amount} 位{self.upper_note()}"
        if n == "cmp":
            return f"比較 {self.op(0)} 和 {self.op(1)}：計算 {self.op(0)} − {self.op(1)}，只更新旗標、不存結果"
        if n == "test":
            if ops == 2 and i.operands[0].type == i.operands[1].type == x86.X86_OP_REG \
                    and i.operands[0].reg == i.operands[1].reg:
                return f"檢查 {self.op(0)} 是不是 0、是不是負數（只更新旗標）"
            return f"計算 {self.op(0)} AND {self.op(1)}，只更新旗標、不存結果"
        if n.startswith("j") and n[1:] in X86_CC:
            return f"如果{self.condition(n[1:], previous)}，就跳到 {self.op(0)}；否則繼續下一條"
        if n == "jmp":
            return f"跳到 {self.op(0)}" if i.operands[0].type == x86.X86_OP_IMM else f"跳到 {self.op(0)} 裡存的位址"
        if n == "call":
            return f"呼叫位址 {self.op(0)} 的函式（先把返回位址推進堆疊）"
        if n == "ret":
            return "從函式返回：從堆疊取出返回位址並跳回去"
        if n == "push":
            size = 8 if self.mode64 else 4
            return f"把 {self.op(0)} 推進堆疊（堆疊指標減 {size}）"
        if n == "pop":
            size = 8 if self.mode64 else 4
            return f"從堆疊取出值放進 {self.op(0)}（堆疊指標加 {size}）"
        if n.startswith("cmov") and n[4:] in X86_CC:
            return f"如果{self.condition(n[4:], previous)}，就把 {self.op(1)} 複製到 {self.op(0)}{self.upper_note()}"
        if n.startswith("set") and n[3:] in X86_CC:
            return f"如果{self.condition(n[3:], previous)}，{self.op(0)} 設成 1，否則設成 0"
        simple = {"nop": "什麼都不做", "int3": "中斷點（除錯器會停在這裡）", "syscall": "呼叫作業系統（系統呼叫）",
                  "leave": "收回這個函式的堆疊空間（mov rsp, rbp；pop rbp）", "hlt": "停止 CPU，直到中斷",
                  "endbr64": "間接跳轉的保護標記（Intel CET），不影響運算", "endbr32": "間接跳轉的保護標記，不影響運算",
                  "cdq": "把 eax 依正負號擴充到 edx:eax（除法前的準備）", "cqo": "把 rax 依正負號擴充到 rdx:rax（除法前的準備）",
                  "cdqe": "把 eax 依正負號擴充成 rax", "ud2": "故意的無效指令（執行到會讓程式當掉）"}
        if n in simple:
            return simple[n]
        if n in ("div", "idiv"):
            kind = "無號" if n == "div" else "有號"
            return f"{kind}除法：被除數在 rdx:rax（或 edx:eax 等），除以 {self.op(0)}；商放 rax、餘數放 rdx（同尺寸的暫存器）"
        return None

    def condition(self, cc: str, previous) -> str:
        meaning = X86_CC[cc]
        if previous is not None and previous.mnemonic == "cmp" and len(previous.operands) == 2:
            a, b = X86(previous, self.mode64).op(0), X86(previous, self.mode64).op(1)
            symbol = {"e": "=", "z": "=", "ne": "≠", "nz": "≠", "g": ">", "nle": ">", "ge": "≥", "nl": "≥",
                      "l": "<", "nge": "<", "le": "≤", "ng": "≤", "a": ">", "nbe": ">", "ae": "≥", "nb": "≥",
                      "b": "<", "nae": "<", "be": "≤", "na": "≤"}.get(cc)
            if symbol:
                kind = "有號" if cc in ("g", "nle", "ge", "nl", "l", "nge", "le", "ng") else (
                    "無號" if cc in ("a", "nbe", "ae", "nb", "b", "nae", "be", "na") else "")
                return f" {a} {symbol} {b}" + (f"（{kind}比較）" if kind else "") + "（依上一條 cmp）"
        return f"旗標顯示「{meaning}」"


class Thumb:
    def __init__(self, insn) -> None:
        self.i = insn

    def op(self, k: int) -> str:
        o = self.i.operands[k]
        if o.type == arm.ARM_OP_REG:
            return self.i.reg_name(o.reg)
        if o.type == arm.ARM_OP_IMM:
            return hexnum(o.imm)
        return f"記憶體位址 {self.address(o)}"

    def address(self, o) -> str:
        m = o.mem
        base = self.i.reg_name(m.base)
        if m.base == arm.ARM_REG_PC:
            pc = (self.i.address + 4) & ~3 if self.i.mnemonic.startswith("ldr") else self.i.address + 4
            target = pc + m.disp
            return f"[pc + 0x{m.disp:X}] = 0x{target:X}（pc 在 Thumb 讀作這條指令位址 + 4，載入時再對齊到 4 的倍數）"
        if m.index:
            index = self.i.reg_name(m.index) + (f" 左移 {o.shift.value}" if o.shift.value else "")
            return f"[{base} + {index}]"
        return f"[{base} {'−' if m.disp < 0 else '+'} 0x{abs(m.disp):X}]" if m.disp else f"[{base}]"

    def flags(self) -> str:
        return "，並更新旗標 N、Z（加減法還有 C、V）" if self.i.update_flags else "（不更新旗標）"

    def text(self, previous) -> str | None:
        i = self.i
        n = i.mnemonic.split(".")[0]
        base, cc = n, ""
        for code in ARM_CC:
            if n.endswith(code) and n[:-len(code)] in ("b", "bx", "bl", "blx"):
                base, cc = n[:-len(code)], code
        ops = len(i.operands)
        if base in ("mov", "movs") and ops == 2:
            return f"{self.op(0)} = {self.op(1)}{self.flags()}"
        if base in ("movw",):
            return f"{self.op(0)} = {self.op(1)}（低 16 位元，高 16 位元清成 0）"
        if base in ("movt",):
            return f"{self.op(0)} 的高 16 位元設成 {self.op(1)}（低 16 位元不變）"
        loads = {"ldr": 4, "ldrb": 1, "ldrh": 2, "ldrsb": 1, "ldrsh": 2}
        if base in loads and ops == 2:
            ext = {"ldrb": "，高位補 0", "ldrh": "，高位補 0", "ldrsb": "，依正負號擴充", "ldrsh": "，依正負號擴充"}.get(base, "")
            return f"從 {self.op(1)} 讀 {SIZES[loads[base]]} 放進 {self.op(0)}{ext}"
        stores = {"str": 4, "strb": 1, "strh": 2}
        if base in stores and ops == 2:
            return f"把 {self.op(0)} 的低 {SIZES[stores[base]]} 寫到 {self.op(1)}"
        arith = {"add": "+", "adds": "+", "sub": "−", "subs": "−", "mul": "×", "muls": "×",
                 "and": "AND", "ands": "AND", "orr": "OR", "orrs": "OR", "eor": "XOR", "eors": "XOR",
                 "bic": "AND NOT", "bics": "AND NOT", "lsl": "左移", "lsls": "左移", "lsr": "邏輯右移（補 0）",
                 "lsrs": "邏輯右移（補 0）", "asr": "算術右移（補符號位元）", "asrs": "算術右移（補符號位元）"}
        if base in arith and ops in (2, 3):
            a, b = (self.op(0), self.op(1)) if ops == 2 else (self.op(1), self.op(2))
            return f"{self.op(0)} = {a} {arith[base]} {b}{self.flags()}"
        if base in ("cmp", "cmn", "tst"):
            what = {"cmp": "−", "cmn": "+", "tst": "AND"}[base]
            return f"計算 {self.op(0)} {what} {self.op(1)}，只更新旗標、不存結果"
        if base == "b":
            if not cc or cc == "al":
                return f"跳到 {self.op(0)}" + ("（跳到自己：無窮迴圈，常見於當機或等待）" if i.operands[0].imm == i.address else "")
            return f"如果{self.condition(cc, previous)}，就跳到 {self.op(0)}；否則繼續下一條"
        if base == "bl":
            return f"呼叫 {self.op(0)} 的函式：返回位址（下一條指令，最低位元設成 1 表示 Thumb）存進 lr"
        if base == "bx":
            if i.operands[0].reg == arm.ARM_REG_LR:
                return "從函式返回（跳回 lr 裡的位址）"
            return f"跳到 {self.op(0)} 裡的位址（最低位元決定 Thumb 或 ARM 模式）"
        if base == "blx":
            return f"呼叫 {self.op(0)} 裡位址的函式，返回位址存進 lr"
        if base in ("push", "pop"):
            regs = "、".join(self.op(k) for k in range(ops))
            if base == "push":
                return f"把 {regs} 推進堆疊（sp 減 {4 * ops}）"
            ret = "；取出到 pc 等於從函式返回" if any(o.reg == arm.ARM_REG_PC for o in i.operands) else ""
            return f"從堆疊依序取出放進 {regs}（sp 加 {4 * ops}）{ret}"
        if base in ("cbz", "cbnz"):
            word = "是 0" if base == "cbz" else "不是 0"
            return f"如果 {self.op(0)} {word}，就跳到 {self.op(1)}（不看旗標）"
        simple = {"nop": "什麼都不做", "bkpt": "中斷點（除錯器會停在這裡）", "svc": "呼叫系統服務（例如 RTOS 核心）",
                  "wfi": "睡眠，等中斷來", "cpsid": "關閉中斷", "cpsie": "開啟中斷", "dsb": "等前面的記憶體存取都完成",
                  "isb": "清空指令管線"}
        if base in simple:
            return simple[base]
        return None

    def condition(self, cc: str, previous) -> str:
        meaning = ARM_CC[cc]
        if previous is not None and previous.mnemonic == "cmp" and len(previous.operands) == 2:
            a, b = Thumb(previous).op(0), Thumb(previous).op(1)
            symbol = {"eq": "=", "ne": "≠", "gt": ">", "ge": "≥", "lt": "<", "le": "≤", "hi": ">", "hs": "≥",
                      "cs": "≥", "lo": "<", "cc": "<", "ls": "≤"}.get(cc)
            if symbol:
                kind = "有號" if cc in ("gt", "ge", "lt", "le") else ("無號" if cc in ("hi", "hs", "cs", "lo", "cc", "ls") else "")
                return f" {a} {symbol} {b}" + (f"（{kind}比較）" if kind else "") + "（依上一條 cmp）"
        return f"旗標顯示「{meaning}」"


def facts(insn) -> str:
    try:
        read, written = insn.regs_access()
    except capstone.CsError:
        return ""
    r = "、".join(insn.reg_name(x) for x in read) or "沒有"
    w = "、".join(insn.reg_name(x) for x in written) or "沒有"
    return f"讀取的暫存器：{r}；寫入的暫存器：{w}"


def translate(code: bytes | str, arch: str = "x86-64", address: int = 0) -> Translation:
    if arch not in ARCHS:
        raise ValueError(f"unknown arch {arch!r}")
    if isinstance(code, str):
        cleaned = "".join(code.replace("0x", "").split())
        code = bytes.fromhex(cleaned)
    md = capstone.Cs(*ARCHS[arch])
    md.detail = True
    lines: list[Line] = []
    previous = None
    used = 0
    for insn in md.disasm(code, address):
        if arch in ("x86-64", "x86-32"):
            text = X86(insn, arch == "x86-64").text(previous)
        elif arch == "thumb":
            text = Thumb(insn).text(previous)
        else:
            text = None
        if text is None:
            text = f"{insn.mnemonic}：沒有中文說明（不做猜測）；{facts(insn)}"
        lines.append(Line(insn.address, insn.bytes.hex(" "), f"{insn.mnemonic} {insn.op_str}".strip(), text))
        previous = insn
        used += insn.size
    tail = ""
    if used < len(code):
        tail = (f"⚠ 從位址 0x{address + used:X} 開始的 {len(code) - used} 個位元組無法解成 {ARCH_NAMES[arch]} 的指令"
                "（可能是資料、別的架構，或從指令中間開始切）")
    return Translation(arch, lines, tail)

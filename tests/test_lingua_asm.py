"""The machine-code translator: each behaviour its Chinese states, executed in
the unicorn emulator (x86-64 and ARM Thumb / Cortex-M). unicorn is a check-only
dependency (pip install unicorn), not shipped with the product.
"""

from __future__ import annotations

import pytest

pytest.importorskip("capstone")
unicorn = pytest.importorskip("unicorn")

from unicorn import arm_const as A
from unicorn import x86_const as X

from edge_agent.lingua import asm

CODE, STACK = 0x1000, 0x100000
ONES = 0xFFFF_FFFF_FFFF_FFFF


def x86(code_hex: str, **regs: int) -> unicorn.Uc:
    code = bytes.fromhex(code_hex.replace(" ", ""))
    emu = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_64)
    emu.mem_map(CODE, 0x1000)
    emu.mem_map(STACK - 0x1000, 0x2000)
    emu.mem_write(CODE, code)
    emu.reg_write(X.UC_X86_REG_RSP, STACK)
    for name, value in regs.items():
        emu.reg_write(getattr(X, f"UC_X86_REG_{name.upper()}"), value)
    emu.emu_start(CODE, CODE + len(code))
    return emu


def reg(emu: unicorn.Uc, name: str) -> int:
    return emu.reg_read(getattr(X, f"UC_X86_REG_{name.upper()}"))


def test_x86_claims() -> None:
    # writing a 32-bit register clears the upper half; 16/8-bit writes do not
    assert reg(x86("b8 01 00 00 00", rax=ONES), "rax") == 1                       # mov eax, 1
    assert reg(x86("66 b8 01 00", rax=ONES), "rax") == 0xFFFF_FFFF_FFFF_0001      # mov ax, 1
    assert reg(x86("b0 01", rax=ONES), "rax") == 0xFFFF_FFFF_FFFF_FF01            # mov al, 1
    assert reg(x86("31 c0", rax=ONES), "rax") == 0                                # xor eax, eax
    # lea computes the address without touching memory (rbx points nowhere)
    assert reg(x86("48 8d 44 8b 08", rbx=0xDEAD_0000, rcx=2), "rax") == 0xDEAD_0000 + 2 * 4 + 8
    rip = x86("48 8d 05 10 00 00 00")                                             # lea rax, [rip + 0x10]
    assert reg(rip, "rax") == CODE + 7 + 0x10
    # jg/setg are signed, ja/seta unsigned: -1 vs 1
    emu = x86("83 f8 01 0f 9f c3 0f 97 c1", rax=0xFFFF_FFFF)                      # cmp eax,1; setg bl; seta cl
    assert reg(emu, "bl") == 0 and reg(emu, "cl") == 1
    # sar keeps the sign, shr fills with 0
    assert reg(x86("d1 f8", rax=0xFFFF_FFF8), "eax") == 0xFFFF_FFFC              # sar eax, 1
    assert reg(x86("d1 e8", rax=0xFFFF_FFF8), "eax") == 0x7FFF_FFFC              # shr eax, 1
    # movzx fills with 0, movsx with the sign
    assert reg(x86("0f b6 c3", rbx=0x80), "eax") == 0x80                          # movzx eax, bl
    assert reg(x86("0f be c3", rbx=0x80), "eax") == 0xFFFF_FF80                   # movsx eax, bl
    # push/pop move rsp by 8; call pushes the return address
    assert reg(x86("50"), "rsp") == STACK - 8                                      # push rax
    assert reg(x86("50 5b"), "rsp") == STACK                                       # push rax; pop rbx
    emu = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_64)
    emu.mem_map(CODE, 0x1000)
    emu.mem_map(STACK - 0x1000, 0x2000)
    emu.mem_write(CODE, bytes.fromhex("e8 00 00 00 00"))                           # call next
    emu.reg_write(X.UC_X86_REG_RSP, STACK)
    emu.emu_start(CODE, CODE + 5)
    assert reg(emu, "rsp") == STACK - 8
    assert int.from_bytes(emu.mem_read(STACK - 8, 8), "little") == CODE + 5
    # inc leaves CF alone
    emu = x86("f9 ff c0 0f 92 c3", rax=0)                                         # stc; inc eax; setc bl
    assert reg(emu, "bl") == 1
    # test r, r finds zero; cdq spreads the sign; div leaves quotient and remainder
    assert reg(x86("85 c0 0f 94 c3", rax=0), "bl") == 1                           # test eax,eax; sete bl
    assert reg(x86("99", rax=0xFFFF_FFFF), "edx") == 0xFFFF_FFFF                  # cdq
    emu = x86("f7 f1", rax=17, rdx=0, rcx=5)                                      # div ecx
    assert reg(emu, "eax") == 3 and reg(emu, "edx") == 2


FLASH, RAM = 0x0800_0000, 0x2000_0000


def thumb(code_hex: str, **regs: int) -> unicorn.Uc:
    code = bytes.fromhex(code_hex.replace(" ", ""))
    emu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
    emu.mem_map(FLASH, 0x1000)
    emu.mem_map(RAM, 0x1000)
    emu.mem_write(FLASH, code)
    emu.reg_write(A.UC_ARM_REG_SP, RAM + 0x800)
    for name, value in regs.items():
        emu.reg_write(getattr(A, f"UC_ARM_REG_{name.upper()}"), value)
    emu.emu_start(FLASH | 1, FLASH + len(code))
    return emu


def areg(emu: unicorn.Uc, name: str) -> int:
    return emu.reg_read(getattr(A, f"UC_ARM_REG_{name.upper()}"))


def test_thumb_claims() -> None:
    # pc-relative loads read from (address + 4) aligned down to 4
    code = bytearray(bytes.fromhex("00bf 0849") + bytes(0x40))                    # nop; ldr r1,[pc,#0x20]
    code[0x24:0x28] = (0x11223344).to_bytes(4, "little")
    emu = thumb(code[:4].hex(), r1=0)
    emu.mem_write(FLASH, bytes(code))
    emu.emu_start(FLASH | 1, FLASH + 4)
    assert areg(emu, "r1") == 0x11223344
    assert "0x8000024" in asm.translate("00bf 0849", "thumb", FLASH).text()
    # bl leaves next | 1 in lr
    emu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
    emu.mem_map(FLASH, 0x1000)
    emu.mem_write(FLASH, bytes.fromhex("00f0 00f8 00bf"))                          # bl next; nop
    emu.emu_start(FLASH | 1, FLASH + 4)
    assert areg(emu, "lr") == (FLASH + 4) | 1
    # push {r4, lr} moves sp by 8
    assert areg(thumb("10b5"), "sp") == RAM + 0x800 - 8
    # bgt is signed, bhi unsigned: cmp r1,#1 with r1 = -1, then movs r2 on each path
    signed = thumb("0129 01dc 0022 00e0 0122", r1=0xFFFF_FFFF)                     # cmp; bgt +2; movs r2,#0; b +0; movs r2,#1
    assert areg(signed, "r2") == 0
    unsigned = thumb("0129 01d8 0022 00e0 0122", r1=0xFFFF_FFFF)                   # same with bhi
    assert areg(unsigned, "r2") == 1
    # asrs keeps the sign, lsrs fills with 0
    assert areg(thumb("4010", r0=0xFFFF_FFF8), "r0") == 0xFFFF_FFFC              # asrs r0, r0, #1
    assert areg(thumb("4008", r0=0xFFFF_FFF8), "r0") == 0x7FFF_FFFC              # lsrs r0, r0, #1


def test_wording() -> None:
    text = asm.translate("b8 01 00 00 00 66 b8 01 00 48 8d 44 8b 08 39 c8 7f 00 77 00", "x86-64").text()
    assert "上半部會被清成 0" in text and "其他位元保持不變" in text and "不讀記憶體" in text
    assert "（有號比較）" in text and "（無號比較）" in text
    thumb_text = asm.translate("0120 10b5 fee7 0129 01dc", "thumb", FLASH).text()
    assert "更新旗標" in thumb_text and "sp 減 8" in thumb_text and "無窮迴圈" in thumb_text and "有號比較" in thumb_text
    assert "沒有中文說明（不做猜測）" in asm.translate("0f a2", "x86-64").text()                # cpuid
    assert "無法解成" in asm.translate("ff ff", "x86-64").text()

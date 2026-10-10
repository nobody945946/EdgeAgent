"""The laptop the product runs on, as the Agent's sandbox shows it.

The bench runs the Agent in a Linux container, but the product runs on the
owner's Windows laptop. When the Agent reads the machine (Get-CimInstance,
nvidia-smi, systeminfo...), the container's answers are false there
(2026-10-08: answers about an RTX 3090 and Docker, so such conversations
never became rows, and a chore like "is C: an NVMe drive?" could never be
shown). The commands Windows has and the container's PowerShell lacks or
answers as Linux answer with this laptop instead (bench/windows: modules
PowerShell loads on first use, and nvidia-smi, systeminfo and hostname on
PATH; bench/pshome: Get-Process, Get-PSDrive and Get-ComputerInfo as
PowerShell's own cmdlets; all reading the facts agent_runner.py writes to
FACTS).

What the laptop is known to have is fixed: the GPU and 8 GB of RAM, which the
Agent's runtime section states (HARDWARE). What the owner never said (CPU,
maker, disk, how full things are, temperatures) changes from task to task, so
a model learns to read the machine, not to remember one.
"""

from __future__ import annotations

import hashlib
import random

GPU = "NVIDIA GeForce RTX 3050 Ti Laptop GPU"
GPU_MIB = 4096
RAM_GB = 8
TOTAL_KB = 8_225_676  # MemTotal: 8 GB less what the firmware and the integrated GPU keep
GPU_LAYERS = 41
# The runtime section's sentence (edge_agent.hardware.describe on the laptop).
HARDWARE = f"Windows、{GPU}（4 GB 顯示記憶體）、{RAM_GB} GB RAM；模型有 {GPU_LAYERS} 層放在顯示卡，其餘在 CPU"
# Where agent_runner.py writes a task's laptop in the sandbox, and the module
# and scripts read it (LAPTOP_FACTS overrides it for the tests).
FACTS = "/tmp/.laptop/machine.json"

# (name, cores, threads, base MHz, integrated graphics, vendor, identifier)
CPUS = [
    ("11th Gen Intel(R) Core(TM) i7-11800H @ 2.30GHz", 8, 16, 2304, "Intel(R) UHD Graphics", "GenuineIntel",
     "Intel64 Family 6 Model 141 Stepping 1"),
    ("11th Gen Intel(R) Core(TM) i5-11400H @ 2.70GHz", 6, 12, 2688, "Intel(R) UHD Graphics", "GenuineIntel",
     "Intel64 Family 6 Model 141 Stepping 1"),
    ("12th Gen Intel(R) Core(TM) i5-12500H", 12, 16, 2500, "Intel(R) Iris(R) Xe Graphics", "GenuineIntel",
     "Intel64 Family 6 Model 154 Stepping 3"),
    ("12th Gen Intel(R) Core(TM) i7-12700H", 14, 20, 2300, "Intel(R) Iris(R) Xe Graphics", "GenuineIntel",
     "Intel64 Family 6 Model 154 Stepping 3"),
    ("AMD Ryzen 7 5800H with Radeon Graphics", 8, 16, 3201, "AMD Radeon(TM) Graphics", "AuthenticAMD",
     "AMD64 Family 25 Model 80 Stepping 0"),
    ("AMD Ryzen 5 5600H with Radeon Graphics", 6, 12, 3301, "AMD Radeon(TM) Graphics", "AuthenticAMD",
     "AMD64 Family 25 Model 80 Stepping 0"),
]
MAKERS = [("ASUSTeK COMPUTER INC.", "ASUS TUF Gaming F15 FX506HE_FX506HE"), ("Acer", "Nitro AN515-57"),
          ("LENOVO", "82JW"), ("HP", "Victus by HP Laptop 16-d0xxx"),
          ("Micro-Star International Co., Ltd.", "GF63 Thin 11UD")]
DISKS = ["SAMSUNG MZVL2512HCJQ-00B00", "WDC PC SN530 SDBPNPZ-512G-1014", "Micron 2450 NVMe 512GB",
         "KBG40ZNS512G NVMe KIOXIA 512GB", "INTEL SSDPEKNW512G8"]
EDITIONS = ["Microsoft Windows 11 家用版", "Microsoft Windows 11 專業版"]
DRIVERS = ["566.36", "572.83", "576.88", "581.15"]
USERS = ["user", "owner", "chen", "lin", "wang"]
# Processes a laptop running SilentTalk and EdgeAgent shows: (name, MB low, MB high).
PROCESSES = [("System", 1, 5), ("Registry", 20, 80), ("smss.exe", 1, 2), ("csrss.exe", 4, 7),
             ("wininit.exe", 5, 8), ("services.exe", 8, 14), ("lsass.exe", 15, 30), ("svchost.exe", 8, 60),
             ("svchost.exe", 8, 60), ("svchost.exe", 8, 60), ("svchost.exe", 8, 60), ("dwm.exe", 60, 160),
             ("explorer.exe", 90, 220), ("SearchHost.exe", 60, 200), ("RuntimeBroker.exe", 10, 40),
             ("MsMpEng.exe", 120, 300), ("Discord.exe", 120, 300), ("Discord.exe", 60, 180),
             ("msedge.exe", 80, 260), ("msedge.exe", 40, 160), ("msedge.exe", 30, 120),
             ("python.exe", 150, 420), ("python.exe", 90, 260), ("node.exe", 60, 180),
             ("llama-server.exe", 1300, 2300), ("pwsh.exe", 60, 110)]


def machine(task: str) -> dict:
    """The laptop for one task: the same task always gets the same one."""
    rng = random.Random(int(hashlib.sha256(task.encode("utf-8")).hexdigest()[:16], 16))
    name, cores, threads, mhz, integrated, vendor, identifier = rng.choice(CPUS)
    maker, model = rng.choice(MAKERS)
    total_kb = TOTAL_KB
    free_mib = rng.randint(350, 1500)
    disk_bytes = 512_110_190_592
    c_size = 510_833_381_376
    uptime_hours = rng.randint(2, 200)
    pid = iter(rng.sample(range(500, 30000), len(PROCESSES)))
    return {
        "computer": "LAPTOP-" + "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(7)),
        "user": rng.choice(USERS),
        "os": {"caption": rng.choice(EDITIONS), "version": "10.0.26100", "build": "26100",
               "architecture": "64 位元", "uptime_hours": uptime_hours},
        "cpu": {"name": name, "cores": cores, "threads": threads, "mhz": mhz, "vendor": vendor,
                "identifier": f"{identifier}, {vendor}", "load": rng.randint(3, 45)},
        "gpu": {"name": GPU, "mib": GPU_MIB, "driver": rng.choice(DRIVERS), "temperature": rng.randint(45, 72),
                "utilization": rng.randint(0, 60), "used_mib": rng.randint(3300, 3900),
                "power_w": round(rng.uniform(8, 40), 1), "integrated": integrated},
        "memory": {"total_kb": total_kb, "free_kb": free_mib * 1024, "dimm_mb": RAM_GB * 1024},
        "disk": {"model": rng.choice(DISKS), "bytes": disk_bytes, "c_bytes": c_size,
                 "c_free": rng.randint(25_000_000, 180_000_000) * 1000 + rng.randint(0, 999)},
        "system": {"maker": maker, "model": model, "bios": f"{rng.randint(300, 330)}"},
        "battery": {"charge": rng.randint(40, 100)},
        "processes": [{"name": process, "pid": 4 if process == "System" else next(pid),
                       "mb": rng.randint(low, high)} for process, low, high in PROCESSES],
    }

"""edge_agent.hardware: the machine stated in the Agent's runtime section."""

from __future__ import annotations

from edge_agent import hardware


def test_the_laptop_is_described_as_it_is(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "gpu", lambda: ("NVIDIA GeForce RTX 3050 Ti Laptop GPU", 4096))
    monkeypatch.setattr(hardware, "ram_mib", lambda: 7900)
    assert hardware.describe(41) == "NVIDIA GeForce RTX 3050 Ti Laptop GPU（4 GB 顯示記憶體）、8 GB RAM；模型有 41 層放在顯示卡，其餘在 CPU"
    assert hardware.describe(99).endswith("模型全部放在顯示卡")
    assert hardware.describe(-1).endswith("自動分配到顯示卡和 CPU")


def test_without_an_nvidia_card_the_model_runs_on_the_cpu(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "gpu", lambda: None)
    monkeypatch.setattr(hardware, "ram_mib", lambda: None)
    assert hardware.describe(41) == "沒有 NVIDIA 顯示卡；模型全部在 CPU 上跑"

from __future__ import annotations

from dataclasses import dataclass

from silenttalk.config import DiscordConfig
from silenttalk.discord_voice import DiscordVoiceClient


@dataclass
class FakeUser:
    id: int
    name: str
    bot: bool = False

    @property
    def display_name(self) -> str:
        return self.name


@dataclass
class FakeVoiceData:
    pcm: bytes


def test_voice_receive_keeps_owner_and_other_people_but_ignores_bots() -> None:
    heard: list[tuple[int, str, bytes]] = []
    client = DiscordVoiceClient(
        DiscordConfig("unused", 123),
        lambda *_: None,
        pcm_callback=lambda user_id, name, pcm: heard.append((user_id, name, pcm)),
    )

    client._on_voice_pcm(FakeUser(123, "Owner"), FakeVoiceData(b"owner"))  # type: ignore[arg-type]
    client._on_voice_pcm(FakeUser(456, "OtherBot", True), FakeVoiceData(b"bot"))  # type: ignore[arg-type]
    client._on_voice_pcm(FakeUser(789, "Alice"), FakeVoiceData(b"human"))  # type: ignore[arg-type]

    assert heard == [
        (123, "Owner", b"owner"),
        (789, "Alice", b"human"),
    ]


@dataclass
class FakeConnection:
    dave_protocol_version: int
    can_encrypt: bool


@dataclass
class FakeVoice:
    _connection: FakeConnection


def test_voice_receive_drops_audio_until_dave_session_is_ready() -> None:
    heard: list[bytes] = []
    client = DiscordVoiceClient(
        DiscordConfig("unused", 123),
        lambda *_: None,
        pcm_callback=lambda _user_id, _name, pcm: heard.append(pcm),
    )
    alice = FakeUser(789, "Alice")
    pending = FakeVoice(FakeConnection(dave_protocol_version=1, can_encrypt=False))
    ready = FakeVoice(FakeConnection(dave_protocol_version=1, can_encrypt=True))

    client._on_voice_pcm(alice, FakeVoiceData(b"ciphertext-noise"), pending)  # type: ignore[arg-type]
    client._on_voice_pcm(alice, FakeVoiceData(b"speech"), ready)  # type: ignore[arg-type]

    assert heard == [b"speech"]

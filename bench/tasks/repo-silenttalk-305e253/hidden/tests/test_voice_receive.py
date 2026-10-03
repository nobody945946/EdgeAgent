"""Receive-path checks that don't need Discord: which frames reach STT, what
the receive stats say, and that one bad Opus frame doesn't stop listening."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest

from silenttalk import discord_voice, voice_recv_patch
from silenttalk.config import DiscordConfig
from silenttalk.copilot.stt_offline import SAMPLE_RATE, audio_stats
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
    dave_session: object | None


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
    pending = FakeVoice(FakeConnection(dave_protocol_version=1, dave_session=None))
    ready = FakeVoice(FakeConnection(dave_protocol_version=1, dave_session=object()))

    client._on_voice_pcm(alice, FakeVoiceData(b"ciphertext-noise"), pending)  # type: ignore[arg-type]
    client._on_voice_pcm(alice, FakeVoiceData(b"speech"), ready)  # type: ignore[arg-type]

    assert heard == [b"speech"]


def _voice(version: int, session: object | None) -> SimpleNamespace:
    return SimpleNamespace(
        _connection=SimpleNamespace(dave_protocol_version=version, dave_session=session)
    )


def test_frames_need_a_dave_session_only_when_dave_is_on() -> None:
    assert discord_voice._dave_receive_ready(_voice(0, None))
    assert not discord_voice._dave_receive_ready(_voice(1, None))
    assert discord_voice._dave_receive_ready(_voice(1, object()))


class _Session:
    ready = True
    epoch = 7

    def get_decryption_stats(self, user_id: int) -> SimpleNamespace:
        if user_id == 3:
            raise RuntimeError("no decryptor")
        return SimpleNamespace(attempts=100, successes=90, failures=10, passthroughs=0)


def test_receive_stats_show_decrypt_counters_even_for_silent_members(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stats = discord_voice.ReceiveStats(started=0.0)
    for _ in range(1500):
        stats.frame(1, discord_voice.FRAME_BYTES)
    stats.frame(1, 100)
    stats.dropped = 4
    with caplog.at_level(logging.INFO, logger="silenttalk.discord_voice"):
        stats.log(_voice(1, _Session()), 30.0, members=[1, 2, 3])
    [line] = [r.getMessage() for r in caplog.records]
    assert "speakers=1 fps=[50.0]" in line
    assert "odd_frames=1 dropped_no_session=4" in line
    # Member 2 sent nothing through but DAVE still counts its attempts;
    # member 3 has no decryptor at all.
    assert "ready=1 epoch=7" in line
    assert "=[100/90/10/0 100/90/10/0 -]" in line


def test_a_frame_that_fails_to_decode_becomes_a_lost_frame() -> None:
    voice_recv_patch.apply()
    from discord.ext.voice_recv import opus

    decoded: list[object] = []

    class _Decoder:
        def decode(self, data: object, fec: bool) -> bytes:
            decoded.append(data)
            if data is not None:
                raise ValueError("corrupted stream")
            return b"\0" * 8

    decoder = SimpleNamespace(_decoder=_Decoder())
    before = voice_recv_patch.decode_errors
    packet = SimpleNamespace(decrypted_data=b"xx")
    _, pcm = opus.PacketDecoder._decode_packet(decoder, packet)
    assert pcm == b"\0" * 8
    assert decoded == [b"xx", None]
    assert voice_recv_patch.decode_errors == before + 1


def test_audio_stats_tell_voice_from_noise() -> None:
    t = np.arange(SAMPLE_RATE) / SAMPLE_RATE
    voice = sum(np.sin(2 * np.pi * 150 * k * t) / k for k in range(1, 20)) * 0.05
    noise = np.random.default_rng(0).normal(0, 0.05, len(t))
    voice_level, voice_flat = audio_stats(voice.astype(np.float32))
    noise_level, noise_flat = audio_stats(noise.astype(np.float32))
    assert -30 < voice_level < -15 and -30 < noise_level < -20
    assert voice_flat < 0.1 < 0.4 < noise_flat
    assert audio_stats(np.zeros(0, dtype=np.float32)) == (-120.0, 1.0)


def test_the_local_microphone_arrives_as_discord_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from silenttalk.copilot import stt_check

    streams: list[SimpleNamespace] = []

    def input_stream(**kwargs: object) -> SimpleNamespace:
        streams.append(SimpleNamespace(**kwargs))
        return streams[-1]

    monkeypatch.setitem(
        sys.modules, "sounddevice", SimpleNamespace(InputStream=input_stream)
    )
    frames: list[bytes] = []
    assert stt_check._open_microphone(frames.append) is streams[0]
    assert (streams[0].samplerate, streams[0].channels) == (48_000, 1)

    mono = np.arange(1440, dtype="<i2").reshape(-1, 1)  # 30 ms
    streams[0].callback(mono, 1440, None, None)

    [frame] = frames  # 20 ms out, 10 ms kept for the next one
    assert len(frame) == discord_voice.FRAME_BYTES
    assert np.frombuffer(frame, "<i2")[:6].tolist() == [0, 0, 1, 1, 2, 2]

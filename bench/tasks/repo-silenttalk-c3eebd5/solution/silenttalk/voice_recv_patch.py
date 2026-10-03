"""What voice_recv (upstream + the DAVE receive fix, PR #62) still misses.

- RTP padding (RFC 3550) is left on the payload. DAVE's trailer sits at the
  very end of the frame, so a padded packet fails to decrypt and is dropped;
  discord.js strips it first, and so does this.
- Silence frames (F8 FF FE) are never DAVE-encrypted; they are passed on as
  they are instead of failing decryption (which also kept the DAVE failure
  counters from meaning anything).
- discord.py only encrypts, so nothing notices when its MLS state can no
  longer decrypt anyone. As discord.js does: after 36 frames in a row fail
  for a speaker with no transition pending, the last transition is reported
  invalid and the session re-initialised (at most every 30 s).
- Frames from a speaker whose SSRC isn't mapped to a user yet can't be
  decrypted (the key is per sender) and are dropped; they are counted
  apart, since a mapping that never arrives means hearing nobody.
- A frame that fails to decode raises out of the router thread, which stops
  listening and makes the app rebuild the voice connection; it is decoded
  as a lost frame instead (Opus packet loss concealment).

Counters (for the voice_receive_stats log line) are module globals, among
them RTP packets expected (from sequence numbers) and received: Discord
sends nothing while nobody speaks but never skips a number, so a gap is a
packet lost on the way.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from discord.ext.voice_recv import opus, reader

_LOG = logging.getLogger(__name__)
SILENCE = b"\xf8\xff\xfe"
# discord.js DEFAULT_DECRYPTION_FAILURE_TOLERANCE: a few frames slip past when
# a session starts, 5-24 after a re-initialisation.
FAILURE_TOLERANCE = 36
RECOVERY_COOLDOWN_SECONDS = 30.0

decode_errors = 0
padded = 0
dave_dropped = 0
unknown_ssrc = 0
recoveries = 0
rtp_expected = 0
rtp_received = 0
_last_sequence: dict[int, int] = {}
_applied = False


def apply() -> None:
    global _applied
    if _applied:
        return
    _patch_decode()
    _patch_dave_decrypt()
    _patch_transition_ready()
    _applied = True


def _patch_decode() -> None:
    original = getattr(opus.PacketDecoder, "_decode_packet", None)
    if original is None:
        _LOG.warning("voice_recv_patch_skipped part=decode")
        return

    def _decode_packet(self: Any, packet: Any) -> tuple[Any, bytes]:
        global decode_errors
        try:
            return original(self, packet)
        except Exception:  # discord.opus.OpusError, or a malformed payload
            decode_errors += 1
            return packet, self._decoder.decode(None, fec=False)

    opus.PacketDecoder._decode_packet = _decode_packet


def _patch_dave_decrypt() -> None:
    original = getattr(reader.AudioReader, "_dave_decrypt", None)
    if original is None:
        # A voice_recv without PR #62 would hand DAVE ciphertext to Opus.
        _LOG.warning("voice_recv_patch_skipped part=dave_decrypt")
        return

    def _dave_decrypt(self: Any, packet: Any) -> bool:
        global padded, dave_dropped, unknown_ssrc
        _count_sequence(packet)
        data = packet.decrypted_data
        if getattr(packet, "padding", False) and data:
            count = data[-1]
            if 0 < count <= len(data):
                packet.decrypted_data = data = data[:-count]
                padded += 1
        if data == SILENCE:
            return True
        session = getattr(self.voice_client._connection, "dave_session", None)
        if (
            session is not None
            and self.voice_client._get_id_from_ssrc(packet.ssrc) is None
        ):
            unknown_ssrc += 1
            return False
        ok = bool(original(self, packet))
        if not ok:
            dave_dropped += 1
        _track_failures(self, packet, ok)
        return ok

    reader.AudioReader._dave_decrypt = _dave_decrypt


def _count_sequence(packet: Any) -> None:
    global rtp_expected, rtp_received
    rtp_received += 1
    last = _last_sequence.get(packet.ssrc)
    step = 1 if last is None else (packet.sequence - last) & 0xFFFF
    if 0 < step < 1000:  # in order, maybe after a gap
        rtp_expected += step
        _last_sequence[packet.ssrc] = packet.sequence
    elif step >= 1000 and 0xFFFF - step >= 1000:  # a new stream (e.g. rejoined)
        rtp_expected += 1
        _last_sequence[packet.ssrc] = packet.sequence
    # else: a late or duplicate packet, already counted as expected


def _patch_transition_ready() -> None:
    from discord.gateway import DiscordVoiceWebSocket

    original = getattr(DiscordVoiceWebSocket, "send_transition_ready", None)
    if original is None:
        _LOG.warning("voice_recv_patch_skipped part=transition_ready")
        return

    async def send_transition_ready(self: Any, transition_id: int) -> None:
        # The transition to report invalid if decryption breaks later.
        self._connection._silenttalk_last_transition = transition_id
        await original(self, transition_id)

    DiscordVoiceWebSocket.send_transition_ready = send_transition_ready


def _track_failures(audio_reader: Any, packet: Any, ok: bool) -> None:
    voice_client = audio_reader.voice_client
    connection = getattr(voice_client, "_connection", None)
    if getattr(connection, "dave_session", None) is None:
        return
    failures: dict[int, int] = audio_reader.__dict__.setdefault(
        "_silenttalk_failures", {}
    )
    ssrc = packet.ssrc
    if ok:
        failures[ssrc] = 0
        return
    failures[ssrc] = failures.get(ssrc, 0) + 1
    if failures[ssrc] <= FAILURE_TOLERANCE:
        return
    transition = getattr(connection, "_silenttalk_last_transition", None)
    last = getattr(connection, "_silenttalk_recovered_at", 0.0)
    now = time.monotonic()
    if (
        not transition
        or getattr(connection, "dave_pending_transitions", None)
        or now - last < RECOVERY_COOLDOWN_SECONDS
    ):
        return
    loop = getattr(voice_client, "loop", None)
    recover = getattr(connection, "_recover_from_invalid_commit", None)
    if loop is None or recover is None or loop.is_closed():
        return
    global recoveries
    recoveries += 1
    connection._silenttalk_recovered_at = now
    failures.clear()
    _LOG.warning(
        "dave_decrypt_recovery transition_id=%d consecutive_failures=%d",
        transition,
        FAILURE_TOLERANCE + 1,
    )
    asyncio.run_coroutine_threadsafe(recover(transition), loop)

"""Ringtones: built-in rings made of tones, or the user's own WAV file.

No Qt or PJSIP here. Built-in rings are played by PJSIP's tone generator,
so they need no sound files. A ring is a list of steps, each one
(frequency 1, frequency 2, milliseconds on, milliseconds off); a second
frequency of 0 means a single pure tone. The whole list loops.

Own sounds: PJSIP's WAV player plays only uncompressed (PCM) WAV, and
only mono into our audio path. prepare_wav() checks the user's file and
saves a mono 16-bit copy in the app's data folder, so the ring keeps
working if the original is moved or deleted. Python's standard library
reads 16-bit WAV itself; anything else (MP3, OGG, FLAC, M4A, other WAVs)
goes through the ffmpeg program once, if it's installed. ffmpeg is
optional: no Python dependency, and WAV works without it.
"""

import array
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from app.paths import DATA_DIR

CUSTOM = "custom"   # the ringtone setting when the user picked their own file
DEFAULT_RINGTONE = "Soft chord"
RINGTONE_DIR = DATA_DIR / "ringtones"
# Only the start of a long sound (a theme song, say) is kept: callers go to
# voicemail long before 2 minutes, and it keeps the copy small (~10 MB).
MAX_SECONDS = 120
FADE_MS = 500               # fade out where a long sound is cut, so the loop doesn't click
FFMPEG_TIMEOUT_S = 60
SOUND_FILE_PATTERNS = "*.wav *.mp3 *.ogg *.oga *.opus *.flac *.m4a *.aac"


def _repeat(steps, times, pause_ms):
    """`steps` `times` over, with a longer pause after the last one."""
    out = list(steps) * times
    f1, f2, on, _ = out[-1]
    out[-1] = (f1, f2, on, pause_ms)
    return tuple(out)


PRESETS = {
    # The original Glitch ring: a soft two-tone chord (A + C#), two bursts.
    "Soft chord": ((440, 554, 400, 200), (440, 554, 400, 2000)),
    # The North American telephone ring: 2 s on, 4 s off.
    "Classic phone": ((440, 480, 2000, 4000),),
    # The British-style "ring ring".
    "Double ring": ((400, 450, 400, 200), (400, 450, 400, 2000)),
    # Three rising notes.
    "Rising chirp": ((660, 0, 110, 30), (880, 0, 110, 30), (1100, 0, 160, 1800)),
    # A fast warble, like an old desk phone's electronic ringer.
    "Trill": _repeat(((1000, 0, 45, 15), (1250, 0, 45, 15)), 8, 1600),
}


class RingtoneError(Exception):
    """The chosen file can't be used; the message is meant for the user."""


NEEDS_FFMPEG = ("To use MP3 and other sound formats, install ffmpeg:\n"
                "  Ubuntu/Debian:  sudo apt install ffmpeg\n"
                "  Fedora:  sudo dnf install ffmpeg-free\n\n"
                "Or choose a 16-bit WAV file, which works without it.")
UNREADABLE = "ffmpeg couldn't read that file; it may not be a sound file."


class _NeedsConverting(Exception):
    """Not a WAV the standard library can read; ffmpeg might."""


def is_valid(ringtone: str) -> bool:
    return ringtone in PRESETS or ringtone == CUSTOM


def prepare_wav(source: Path, folder: Path = RINGTONE_DIR) -> Path:
    """Check `source` and save a mono 16-bit WAV copy in `folder`, named
    like the original. Returns the copy's path. Raises RingtoneError."""
    try:
        rate, samples = _read_wav(source)
    except _NeedsConverting:
        with tempfile.TemporaryDirectory() as tmp:
            rate, samples = _read_wav(_convert(source, Path(tmp) / "converted.wav"))
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (source.stem + ".wav")
    tmp = target.with_name(target.name + ".tmp")
    if sys.byteorder == "big":
        samples.byteswap()   # WAV is little-endian
    with wave.open(str(tmp), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())
    tmp.replace(target)
    return target


def _read_wav(source: Path) -> tuple[int, array.array]:
    """(sample rate, mono 16-bit samples) of a 16-bit PCM WAV."""
    try:
        with wave.open(str(source), "rb") as wav:
            channels, width = wav.getnchannels(), wav.getsampwidth()
            rate, frames = wav.getframerate(), wav.getnframes()
            if width != 2 or channels not in (1, 2) or not 8000 <= rate <= 48000:
                raise _NeedsConverting
            cut = frames > rate * MAX_SECONDS
            data = wav.readframes(min(frames, rate * MAX_SECONDS))
    except (wave.Error, EOFError):
        raise _NeedsConverting from None
    except OSError as e:
        raise RingtoneError(f"Couldn't read {source.name}: {e.strerror or e}") from None
    samples = array.array("h", data[:len(data) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if channels == 2:
        samples = array.array("h", ((left + right) // 2 for left, right
                                    in zip(samples[0::2], samples[1::2])))
    if not samples:
        raise RingtoneError("That sound file is empty.")
    if cut:
        _fade_out(samples, rate * FADE_MS // 1000)
    return rate, samples


def _fade_out(samples: array.array, length: int):
    length = min(length, len(samples))
    start = len(samples) - length
    for i in range(length):
        samples[start + i] = samples[start + i] * (length - i) // length


def _convert(source: Path, target: Path) -> Path:
    """Decode any sound file to mono 16-bit WAV with ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RingtoneError(NEEDS_FFMPEG)
    # A second past the limit, so _read_wav knows it was cut and fades it out.
    command = [ffmpeg, "-nostdin", "-v", "error", "-i", str(source.resolve()),
               "-t", str(MAX_SECONDS + 1), "-ac", "1", "-ar", "48000",
               "-c:a", "pcm_s16le", "-f", "wav", str(target)]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RingtoneError(UNREADABLE) from None
    if result.returncode != 0 or not target.is_file():
        raise RingtoneError(UNREADABLE)
    return target

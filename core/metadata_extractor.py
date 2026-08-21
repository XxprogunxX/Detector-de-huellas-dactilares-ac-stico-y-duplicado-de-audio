"""
Audio metadata extraction using Mutagen with FFprobe fallback.
"""

import os
import sys
import json
import subprocess
from typing import Dict, Any, Tuple
import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.wave import WAVE
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4


LOSSLESS_FORMATS = {"flac", "wav", "alac", "aiff", "ape", "wv", "dsd", "dff", "dsf"}


def extract_metadata(filepath: str) -> Dict[str, Any]:
    """
    Extracts comprehensive audio technical specs and tags from file.
    
    Returns:
        Dict with: format, bitrate, samplerate, channels, bit_depth,
                   is_lossless, title, artist, album, duration.
    """
    ext = os.path.splitext(filepath)[1].lower().lstrip(".")
    is_lossless = ext in LOSSLESS_FORMATS
    
    result = {
        "format": ext.upper() if ext else "UNKNOWN",
        "bitrate": 0,
        "samplerate": 44100,
        "channels": 2,
        "bit_depth": 16,
        "is_lossless": is_lossless,
        "duration": 0.0,
        "title": "",
        "artist": "",
        "album": "",
    }

    # Attempt Mutagen first
    try:
        audio = mutagen.File(filepath)
        if audio is not None:
            if hasattr(audio, "info") and audio.info is not None:
                info = audio.info
                result["duration"] = getattr(info, "length", 0.0)
                
                # Bitrate in kbps
                raw_bitrate = getattr(info, "bitrate", 0)
                if raw_bitrate:
                    result["bitrate"] = int(raw_bitrate // 1000 if raw_bitrate > 1000 else raw_bitrate)
                
                result["samplerate"] = getattr(info, "sample_rate", 44100)
                result["channels"] = getattr(info, "channels", 2)
                result["bit_depth"] = getattr(info, "bits_per_sample", 16)
                
                # Format name refinement
                codec_name = type(audio).__name__.upper()
                if "FLAC" in codec_name:
                    result["format"] = "FLAC"
                    result["is_lossless"] = True
                elif "MP3" in codec_name:
                    result["format"] = "MP3"
                    result["is_lossless"] = False
                elif "WAVE" in codec_name or "WAV" in codec_name:
                    result["format"] = "WAV"
                    result["is_lossless"] = True
                elif "MP4" in codec_name:
                    result["format"] = "M4A"
                elif "OGG" in codec_name:
                    result["format"] = "OGG"

            # Extract tags safely
            tags = audio.tags
            if tags:
                result["title"] = _get_tag_value(tags, ["title", "tit2", "\xa9nam", "TITLE"])
                result["artist"] = _get_tag_value(tags, ["artist", "tpe1", "\xa9ART", "ARTIST", "albumartist"])
                result["album"] = _get_tag_value(tags, ["album", "talb", "\xa9alb", "ALBUM"])
                
            # If bitrate is missing for lossless, calculate approximate uncompressed bitrate
            if result["is_lossless"] and result["bitrate"] == 0 and result["duration"] > 0:
                filesize = os.path.getsize(filepath)
                result["bitrate"] = int((filesize * 8) / (result["duration"] * 1000))
                
            return result
    except Exception:
        pass

    # Fallback to ffprobe
    ffprobe_meta = _extract_with_ffprobe(filepath)
    if ffprobe_meta:
        result.update(ffprobe_meta)
        
    return result


def _get_tag_value(tags: Any, keys: list) -> str:
    """Helper to extract first matching tag value as string."""
    for key in keys:
        if key in tags:
            val = tags[key]
            if isinstance(val, (list, tuple)) and len(val) > 0:
                return str(val[0]).strip()
            return str(val).strip()
        # Case-insensitive search
        for k, v in tags.items():
            if str(k).lower() == key.lower():
                if isinstance(v, (list, tuple)) and len(v) > 0:
                    return str(v[0]).strip()
                return str(v).strip()
    return ""


def _extract_with_ffprobe(filepath: str) -> Dict[str, Any]:
    """Uses ffprobe to extract stream information when mutagen fails."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", filepath
    ]
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo)
        if proc.returncode != 0:
            return {}
        data = json.loads(proc.stdout)
        
        streams = data.get("streams", [])
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        format_info = data.get("format", {})
        
        res = {}
        if audio_stream:
            codec = audio_stream.get("codec_name", "").upper()
            res["format"] = codec
            res["samplerate"] = int(audio_stream.get("sample_rate", 44100))
            res["channels"] = int(audio_stream.get("channels", 2))
            res["bit_depth"] = int(audio_stream.get("bits_per_raw_sample", 16) or 16)
            res["is_lossless"] = codec.lower() in LOSSLESS_FORMATS
            
            raw_br = audio_stream.get("bit_rate") or format_info.get("bit_rate")
            if raw_br:
                res["bitrate"] = int(int(raw_br) // 1000)
                
            res["duration"] = float(audio_stream.get("duration") or format_info.get("duration", 0.0))
            
        tags = format_info.get("tags", {})
        if tags:
            res["title"] = str(tags.get("title", tags.get("TITLE", ""))).strip()
            res["artist"] = str(tags.get("artist", tags.get("ARTIST", ""))).strip()
            res["album"] = str(tags.get("album", tags.get("ALBUM", ""))).strip()
            
        return res
    except Exception:
        return {}

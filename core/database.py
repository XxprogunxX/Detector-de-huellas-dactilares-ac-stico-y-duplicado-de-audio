"""
SQLite Database caching engine for Audio Tracks, Fingerprints and Metadata.
"""

import os
import sqlite3
from contextlib import contextmanager
from typing import Optional, List, Dict, Any
from core.models import AudioTrack
from core.fingerprint import compress_fingerprint, decompress_fingerprint


DEFAULT_DB_NAME = "music_fingerprints.db"


def get_default_db_path() -> str:
    """Returns safe database location in user's AppData or project directory."""
    if os.name == "nt":
        app_data = os.environ.get("APPDATA")
        if app_data:
            data_dir = os.path.join(app_data, "AudioDuplicateDetector")
            os.makedirs(data_dir, exist_ok=True)
            return os.path.join(data_dir, DEFAULT_DB_NAME)
    
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(app_dir, DEFAULT_DB_NAME)


class Database:
    def __init__(self, db_path: Optional[str] = None):
        if not db_path:
            self.db_path = get_default_db_path()
        else:
            self.db_path = db_path

        self.init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL;")  # Fast concurrent writes
        conn.execute("PRAGMA synchronous = NORMAL;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self):
        """Initializes tables and high-speed indexes."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filepath TEXT UNIQUE NOT NULL,
                    filesize INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    sha256 TEXT,
                    audio_hash TEXT,
                    duration REAL,
                    format TEXT,
                    bitrate INTEGER,
                    samplerate INTEGER,
                    channels INTEGER,
                    bit_depth INTEGER,
                    is_lossless INTEGER,
                    spectral_cutoff REAL,
                    fake_lossless_confidence REAL,
                    quality_score REAL,
                    quality_details TEXT,
                    fingerprint BLOB,
                    title TEXT,
                    artist TEXT,
                    album TEXT,
                    last_scanned TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache ON tracks(filepath, filesize, mtime);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sha256 ON tracks(sha256);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audio_hash ON tracks(audio_hash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_duration ON tracks(duration);")
            
            # Migrate old column if exists
            try:
                conn.execute("ALTER TABLE tracks RENAME COLUMN is_fake_lossless TO fake_lossless_confidence;")
            except sqlite3.OperationalError:
                pass

    def get_all_cached_lookup(self) -> Dict[str, AudioTrack]:
        """Returns a fast lookup dict of all cached AudioTracks indexed by filepath."""
        lookup = {}
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, filepath, filesize, mtime, sha256, audio_hash, duration, format, "
                "bitrate, samplerate, channels, bit_depth, is_lossless, spectral_cutoff, "
                "fake_lossless_confidence, quality_score, quality_details, fingerprint, title, artist, album "
                "FROM tracks"
            )
            for row in cursor.fetchall():
                t = self._row_to_track(row)
                lookup[t.filepath] = t
        return lookup

    def get_lightweight_cache_lookup(self) -> Dict[str, tuple]:
        """Returns a fast lookup dict of (filesize, mtime) indexed by filepath, avoiding full load."""
        lookup = {}
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT filepath, filesize, mtime FROM tracks")
            for row in cursor.fetchall():
                lookup[row[0]] = (row[1], row[2])
        return lookup

    def get_track_by_cache(self, filepath: str, filesize: int, mtime: float) -> Optional[AudioTrack]:
        """Returns cached AudioTrack if file size and modified time match exactly."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, filepath, filesize, mtime, sha256, audio_hash, duration, format, "
                "bitrate, samplerate, channels, bit_depth, is_lossless, spectral_cutoff, "
                "fake_lossless_confidence, quality_score, quality_details, fingerprint, title, artist, album "
                "FROM tracks WHERE filepath = ? AND filesize = ? AND ABS(mtime - ?) < 0.001",
                (filepath, filesize, mtime)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_track(row)

    def upsert_track(self, track: AudioTrack):
        """Inserts or updates track record in cache."""
        fp_blob = compress_fingerprint(track.fingerprint_raw)
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO tracks (
                    filepath, filesize, mtime, sha256, audio_hash, duration, format,
                    bitrate, samplerate, channels, bit_depth, is_lossless, spectral_cutoff,
                    fake_lossless_confidence, quality_score, quality_details, fingerprint, title, artist, album, last_scanned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(filepath) DO UPDATE SET
                    filesize = excluded.filesize,
                    mtime = excluded.mtime,
                    sha256 = excluded.sha256,
                    audio_hash = excluded.audio_hash,
                    duration = excluded.duration,
                    format = excluded.format,
                    bitrate = excluded.bitrate,
                    samplerate = excluded.samplerate,
                    channels = excluded.channels,
                    bit_depth = excluded.bit_depth,
                    is_lossless = excluded.is_lossless,
                    spectral_cutoff = excluded.spectral_cutoff,
                    fake_lossless_confidence = excluded.fake_lossless_confidence,
                    quality_score = excluded.quality_score,
                    quality_details = excluded.quality_details,
                    fingerprint = excluded.fingerprint,
                    title = excluded.title,
                    artist = excluded.artist,
                    album = excluded.album,
                    last_scanned = CURRENT_TIMESTAMP;
            """, (
                track.filepath, track.filesize, track.mtime, track.sha256, track.audio_hash,
                track.duration, track.format, track.bitrate, track.samplerate, track.channels,
                track.bit_depth, 1 if track.is_lossless else 0, track.spectral_cutoff,
                track.fake_lossless_confidence, track.quality_score, track.quality_details,
                fp_blob, track.title, track.artist, track.album
            ))

    def upsert_tracks_batch(self, tracks: List[AudioTrack]):
        """Batch insert for high-performance bulk indexing."""
        if not tracks:
            return
        data = []
        for t in tracks:
            fp_blob = compress_fingerprint(t.fingerprint_raw)
            data.append((
                t.filepath, t.filesize, t.mtime, t.sha256, t.audio_hash,
                t.duration, t.format, t.bitrate, t.samplerate, t.channels,
                t.bit_depth, 1 if t.is_lossless else 0, t.spectral_cutoff,
                t.fake_lossless_confidence, t.quality_score, t.quality_details,
                fp_blob, t.title, t.artist, t.album
            ))
        with self._get_connection() as conn:
            conn.executemany("""
                INSERT INTO tracks (
                    filepath, filesize, mtime, sha256, audio_hash, duration, format,
                    bitrate, samplerate, channels, bit_depth, is_lossless, spectral_cutoff,
                    fake_lossless_confidence, quality_score, quality_details, fingerprint, title, artist, album, last_scanned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(filepath) DO UPDATE SET
                    filesize = excluded.filesize,
                    mtime = excluded.mtime,
                    sha256 = excluded.sha256,
                    audio_hash = excluded.audio_hash,
                    duration = excluded.duration,
                    format = excluded.format,
                    bitrate = excluded.bitrate,
                    samplerate = excluded.samplerate,
                    channels = excluded.channels,
                    bit_depth = excluded.bit_depth,
                    is_lossless = excluded.is_lossless,
                    spectral_cutoff = excluded.spectral_cutoff,
                    fake_lossless_confidence = excluded.fake_lossless_confidence,
                    quality_score = excluded.quality_score,
                    quality_details = excluded.quality_details,
                    fingerprint = excluded.fingerprint,
                    title = excluded.title,
                    artist = excluded.artist,
                    album = excluded.album,
                    last_scanned = CURRENT_TIMESTAMP;
            """, data)

    def delete_track(self, filepath: str):
        """Removes track entry from database."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM tracks WHERE filepath = ?", (filepath,))

    def get_tracks_for_files(self, filepaths: List[str]) -> List[AudioTrack]:
        """Fetches tracks for given filepaths list."""
        if not filepaths:
            return []
        res = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for i in range(0, len(filepaths), 500):
                chunk = filepaths[i:i + 500]
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"SELECT id, filepath, filesize, mtime, sha256, audio_hash, duration, format, "
                    f"bitrate, samplerate, channels, bit_depth, is_lossless, spectral_cutoff, "
                    f"fake_lossless_confidence, quality_score, quality_details, fingerprint, title, artist, album "
                    f"FROM tracks WHERE filepath IN ({placeholders})",
                    chunk
                )
                for row in cursor.fetchall():
                    res.append(self._row_to_track(row))
        return res

    def _row_to_track(self, row: tuple) -> AudioTrack:
        (
            tid, filepath, filesize, mtime, sha256, audio_hash, duration, fmt,
            bitrate, samplerate, channels, bit_depth, is_lossless, spectral_cutoff,
            fake_lossless_confidence, quality_score, quality_details, fp_blob, title, artist, album
        ) = row
        
        raw_fp = decompress_fingerprint(fp_blob) if fp_blob else []
        return AudioTrack(
            id=tid,
            filepath=filepath,
            filesize=filesize,
            mtime=mtime,
            sha256=sha256 or "",
            audio_hash=audio_hash or "",
            duration=duration or 0.0,
            format=fmt or "",
            bitrate=bitrate or 0,
            samplerate=samplerate or 44100,
            channels=channels or 2,
            bit_depth=bit_depth or 16,
            is_lossless=bool(is_lossless),
            spectral_cutoff=spectral_cutoff or 0.0,
            fake_lossless_confidence=float(fake_lossless_confidence or 0.0),
            quality_score=quality_score or 0.0,
            quality_details=quality_details or "",
            fingerprint_raw=raw_fp,
            title=title or "",
            artist=artist or "",
            album=album or ""
        )

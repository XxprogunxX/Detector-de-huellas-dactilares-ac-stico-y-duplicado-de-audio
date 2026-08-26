"""
High-Performance Audio Scanner Engine with Multiprocessing, Incremental Caching and Pause/Resume.
"""

import os
import time
import itertools
import threading
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from typing import List, Optional, Callable, Dict, Any
from core.models import AudioTrack, DuplicateGroup, DuplicateType, ScanStats
from core.fingerprint import (
    compute_file_sha256,
    compute_audio_pcm_hash,
    extract_fingerprint
)
from core.metadata_extractor import extract_metadata
from core.quality_analyzer import estimate_spectral_cutoff, evaluate_track_quality
from core.database import Database
from core.clustering import cluster_duplicates


SUPPORTED_EXTENSIONS = {
    ".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg",
    ".opus", ".wma", ".alac", ".aiff", ".ape", ".wv"
}


def _process_audio_worker(filepath: str) -> Optional[Dict[str, Any]]:
    """
    Worker function executed in parallel processes to analyze a single audio file.
    """
    try:
        if not os.path.isfile(filepath):
            return None

        stat = os.stat(filepath)
        filesize = stat.st_size
        mtime = stat.st_mtime

        # 1. Binary SHA-256 Hash
        sha256 = compute_file_sha256(filepath)

        # 2. Metadata (bitrate, sample rate, channels, tags)
        meta = extract_metadata(filepath)

        # 3. Acoustic Fingerprint (Chromaprint / fpcalc with 120s window)
        duration, raw_fp = extract_fingerprint(filepath, max_length_seconds=120)
        if duration <= 0.0:
            duration = meta.get("duration", 0.0)

        # 4. Selective Spectral Cutoff (Only run FFT on Lossless to catch fake upscales)
        is_lossless = meta.get("is_lossless", False)
        if is_lossless:
            spectral_cutoff, fake_lossless_confidence = estimate_spectral_cutoff(filepath)
        else:
            br = meta.get("bitrate", 128)
            spectral_cutoff = 16000.0 if br <= 128 else (19000.0 if br <= 256 else 20500.0)
            fake_lossless_confidence = 0.0

        # Build raw dict for transfer back to main process
        track_data = {
            "filepath": filepath,
            "filesize": filesize,
            "mtime": mtime,
            "sha256": sha256,
            "audio_hash": compute_audio_pcm_hash(filepath),
            "duration": duration,
            "format": meta.get("format", ""),
            "bitrate": meta.get("bitrate", 0),
            "samplerate": meta.get("samplerate", 44100),
            "channels": meta.get("channels", 2),
            "bit_depth": meta.get("bit_depth", 16),
            "is_lossless": is_lossless,
            "spectral_cutoff": spectral_cutoff,
            "fake_lossless_confidence": fake_lossless_confidence,
            "fingerprint_raw": raw_fp,
            "title": meta.get("title", ""),
            "artist": meta.get("artist", ""),
            "album": meta.get("album", ""),
        }
        return track_data
    except Exception as e:
        return None


class AudioScanner:
    def __init__(self, db: Optional[Database] = None, max_workers: Optional[int] = None):
        self.db = db or Database()
        cpu_cores = os.cpu_count() or 4
        # Leave 1-2 cores free so OS/GUI stays smooth and prevents laptop overheating
        self.max_workers = max_workers or max(1, min(6, cpu_cores - 1 if cpu_cores > 2 else cpu_cores))
        self.stats = ScanStats()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Unpaused initially

    def pause(self):
        """Pauses the ongoing scan."""
        self._pause_event.clear()
        self.stats.is_paused = True

    def resume(self):
        """Resumes the paused scan."""
        self._pause_event.set()
        self.stats.is_paused = False

    def stop(self):
        """Stops/cancels the scan."""
        self._stop_event.set()
        self._pause_event.set()
        self.stats.is_running = False

    def is_cancelled(self) -> bool:
        return self._stop_event.is_set()

    def scan_directory(
        self,
        folder_path: str,
        progress_callback: Optional[Callable[[ScanStats], None]] = None
    ) -> List[DuplicateGroup]:
        """
        Scans all supported audio files in folder_path recursively,
        extracts fingerprints, saves to SQLite cache, and clusters duplicates.
        """
        self._stop_event.clear()
        self._pause_event.set()
        self.stats = ScanStats(is_running=True, phase="Descubriendo archivos...")
        start_time = time.time()

        if progress_callback:
            progress_callback(self.stats)

        # 1. Discover all audio files
        discovered_files: List[str] = []
        for root, _, files in os.walk(folder_path):
            if self._stop_event.is_set():
                break
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    discovered_files.append(os.path.join(root, f))

        self.stats.total_files_found = len(discovered_files)
        if progress_callback:
            progress_callback(self.stats)

        if not discovered_files or self._stop_event.is_set():
            self.stats.is_running = False
            return []

        # 2. Check Database Cache (Bulk in-memory check for instant speed on 40k+ files)
        self.stats.phase = "Verificando caché..."
        tracks_to_process: List[str] = []
        all_tracks: List[AudioTrack] = []
        
        cached_map = self.db.get_lightweight_cache_lookup()
        total_discovered = len(discovered_files)

        cached_paths_to_load: List[str] = []

        for idx, fpath in enumerate(discovered_files, start=1):
            if self._stop_event.is_set():
                break
            try:
                stat = os.stat(fpath)
                cached = cached_map.get(fpath)
                if cached and cached[0] == stat.st_size and abs(cached[1] - stat.st_mtime) < 0.001:
                    cached_paths_to_load.append(fpath)
                    self.stats.files_from_cache += 1
                    self.stats.files_scanned += 1
                else:
                    tracks_to_process.append(fpath)
            except Exception:
                tracks_to_process.append(fpath)

            if progress_callback and (idx % 250 == 0 or idx == total_discovered):
                self.stats.elapsed_seconds = time.time() - start_time
                progress_callback(self.stats)

        if progress_callback:
            progress_callback(self.stats)

        # 3. Parallel Processing of New / Modified Files
        if tracks_to_process and not self._stop_event.is_set():
            self.stats.phase = "Analizando huellas acústicas..."
            batch_save: List[AudioTrack] = []
            
            # Stream tasks to avoid holding 40k futures in memory simultaneously
            with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
                max_in_flight = self.max_workers * 4
                file_iter = iter(tracks_to_process)
                futures = {}

                # Prime worker queue
                for path in list(itertools.islice(file_iter, max_in_flight)):
                    f = executor.submit(_process_audio_worker, path)
                    futures[f] = path

                while futures:
                    # Check pause state
                    self._pause_event.wait()

                    if self._stop_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

                    # Wait for next completed future
                    done, _ = wait(futures.keys(), return_when=FIRST_COMPLETED)
                    for future in done:
                        path = futures.pop(future)
                        self.stats.current_file = os.path.basename(path)

                        try:
                            res = future.result()
                            if res:
                                track = AudioTrack(
                                    filepath=res["filepath"],
                                    filesize=res["filesize"],
                                    mtime=res["mtime"],
                                    sha256=res["sha256"],
                                    audio_hash=res["audio_hash"],
                                    duration=res["duration"],
                                    format=res["format"],
                                    bitrate=res["bitrate"],
                                    samplerate=res["samplerate"],
                                    channels=res["channels"],
                                    bit_depth=res["bit_depth"],
                                    is_lossless=res["is_lossless"],
                                    spectral_cutoff=res["spectral_cutoff"],
                                    fake_lossless_confidence=res["fake_lossless_confidence"],
                                    fingerprint_raw=res["fingerprint_raw"],
                                    title=res["title"],
                                    artist=res["artist"],
                                    album=res["album"]
                                )
                                evaluate_track_quality(track)
                                all_tracks.append(track)
                                batch_save.append(track)
                            else:
                                self.stats.files_failed += 1
                        except Exception:
                            self.stats.files_failed += 1

                        self.stats.files_scanned += 1
                        self.stats.elapsed_seconds = time.time() - start_time

                        # Feed next file to worker pool
                        try:
                            next_path = next(file_iter)
                            new_f = executor.submit(_process_audio_worker, next_path)
                            futures[new_f] = next_path
                        except StopIteration:
                            pass

                        # Periodic batch save to database
                        if len(batch_save) >= 200:
                            self.db.upsert_tracks_batch(batch_save)
                            batch_save.clear()

                        if progress_callback and (self.stats.files_scanned % 5 == 0 or self.stats.files_scanned == self.stats.total_files_found):
                            progress_callback(self.stats)

            # Final save remaining batch
            if batch_save:
                self.db.upsert_tracks_batch(batch_save)
                batch_save.clear()

        # 4. Clustering Phase
        if self._stop_event.is_set():
            self.stats.is_running = False
            return []

        self.stats.phase = "Cargando caché para agrupar..."
        if progress_callback:
            progress_callback(self.stats)
            
        # Lazy load all valid cached tracks
        all_tracks.extend(self.db.get_tracks_for_files(cached_paths_to_load))

        self.stats.phase = "Agrupando duplicados..."
        if progress_callback:
            progress_callback(self.stats)

        def clustering_progress(pct, curr_comp, tot_comp, msg):
            self.stats.phase = msg
            self.stats.progress_ratio = pct
            self.stats.comparison_current = curr_comp
            self.stats.comparison_total = tot_comp
            self.stats.elapsed_seconds = time.time() - start_time
            if progress_callback:
                progress_callback(self.stats)

        groups = cluster_duplicates(
            all_tracks,
            progress_callback=clustering_progress,
            is_cancelled=self.is_cancelled
        )

        # 5. Summarize Statistics
        self.stats.progress_ratio = 1.0
        self.stats.total_groups_count = len(groups)
        self.stats.exact_duplicates_count = sum(1 for g in groups if g.primary_type in (DuplicateType.EXACT_HASH, DuplicateType.EXACT_AUDIO))
        self.stats.acoustic_duplicates_count = sum(1 for g in groups if g.primary_type == DuplicateType.ACOUSTIC_DUPLICATE)
        self.stats.possible_duplicates_count = sum(1 for g in groups if g.primary_type == DuplicateType.POSSIBLE_DUPLICATE)
        self.stats.potential_space_saving = sum(g.space_saving_bytes for g in groups)
        self.stats.elapsed_seconds = time.time() - start_time
        self.stats.is_running = False
        self.stats.phase = "Completado"

        if progress_callback:
            progress_callback(self.stats)

        return groups

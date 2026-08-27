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


# CPU usage ceiling: if system CPU exceeds this, the scanner throttles
_CPU_THROTTLE_THRESHOLD = 85.0   # percent
_CPU_THROTTLE_SLEEP     = 0.35   # seconds to sleep when throttling
_CPU_THROTTLE_INTERVAL  = 8      # check CPU every N completed files



SUPPORTED_EXTENSIONS = {
    ".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg",
    ".opus", ".wma", ".alac", ".aiff", ".ape", ".wv"
}


def _worker_process_init():
    """
    Runs once in each spawned worker process (ProcessPoolExecutor initializer).
    Drops the worker process to BELOW_NORMAL CPU priority so audio scanning
    never starves the GUI or the OS on any computer.
    """
    try:
        import psutil
        p = psutil.Process()
        if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
            # Windows
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            # Linux / macOS: nice value 10 = noticeably lower priority
            p.nice(10)
    except Exception:
        pass  # Non-fatal: runs at normal priority if psutil unavailable


def _process_audio_worker(filepath: str) -> Optional[Dict[str, Any]]:
    """
    Worker function executed in parallel processes to analyze a single audio file.
    Order of operations is optimized for early-exit:
      1. Fingerprint first (fpcalc, 60s window) — fastest way to detect corrupt/unsupported files
      2. SHA256 + PCM hash only if fingerprint succeeded
      3. Metadata and spectral cutoff last
    """
    try:
        if not os.path.isfile(filepath):
            return None

        stat = os.stat(filepath)
        filesize = stat.st_size
        mtime = stat.st_mtime

        # 1. Acoustic Fingerprint first — if fpcalc fails, the file is unusable
        #    60s is sufficient for Chromaprint (was 120s, halving CPU time for this step).
        try:
            duration, raw_fp = extract_fingerprint(filepath, max_length_seconds=60)
        except Exception:
            return None  # Can't fingerprint → skip everything else

        # 2. Metadata (bitrate, sample rate, channels, tags)
        meta = extract_metadata(filepath)
        if duration <= 0.0:
            duration = meta.get("duration", 0.0)

        # 3. Binary SHA-256 Hash
        sha256 = compute_file_sha256(filepath)

        # 4. PCM Audio Hash (first 30s only — see fingerprint.py)
        audio_hash = compute_audio_pcm_hash(filepath)

        # 5. Selective Spectral Cutoff (Only run FFT on Lossless to catch fake upscales)
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
            "audio_hash": audio_hash,
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
    except Exception:
        return None


class AudioScanner:
    def __init__(self, db: Optional[Database] = None, max_workers: Optional[int] = None):
        self.db = db or Database()
        cpu_cores = os.cpu_count() or 4

        if max_workers is not None:
            self.max_workers = max_workers
        else:
            # Adaptive worker count: leave cores free for OS/GUI AND respect available RAM.
            # Each worker peak RAM: ~300-400 MB (ffmpeg decode + numpy FFT buffers).
            # This prevents memory thrashing on PCs with 4-8 GB RAM.
            try:
                import psutil
                available_ram_gb = psutil.virtual_memory().available / (1024 ** 3)
                # Allow 1 worker per 0.5 GB of available RAM, capped at cpu_cores-1
                ram_based_limit = max(1, int(available_ram_gb / 0.5))
                cpu_based_limit = max(1, cpu_cores - 1 if cpu_cores > 2 else cpu_cores)
                self.max_workers = min(ram_based_limit, cpu_based_limit, 6)
            except ImportError:
                # psutil not installed: fall back to conservative CPU-only heuristic
                self.max_workers = max(1, min(4, cpu_cores - 1 if cpu_cores > 2 else cpu_cores))

        self.stats = ScanStats()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Unpaused initially
        self._cpu_throttle_enabled = True  # Can be disabled via settings

    def _maybe_throttle_cpu(self):
        """
        If system CPU usage is above the threshold, sleep briefly to give
        the OS and GUI a chance to breathe. Uses psutil; no-op if not installed.
        """
        if not self._cpu_throttle_enabled:
            return
        try:
            import psutil
            cpu_pct = psutil.cpu_percent(interval=None)
            if cpu_pct >= _CPU_THROTTLE_THRESHOLD:
                time.sleep(_CPU_THROTTLE_SLEEP)
        except ImportError:
            pass


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
        last_walk_update = time.time()
        for root, _, files in os.walk(folder_path):
            if self._stop_event.is_set():
                break
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    discovered_files.append(os.path.join(root, f))

            now = time.time()
            if progress_callback and (now - last_walk_update > 0.15 or len(discovered_files) % 500 == 0):
                self.stats.total_files_found = len(discovered_files)
                self.stats.current_file = root
                self.stats.elapsed_seconds = now - start_time
                self.stats.phase = f"Descubriendo archivos ({len(discovered_files):,} encontrados)..."
                progress_callback(self.stats)
                last_walk_update = now

        self.stats.total_files_found = len(discovered_files)
        self.stats.phase = f"Descubrimiento completado: {len(discovered_files):,} archivos encontrados"
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
            # initializer: each worker process lowers its own CPU priority to
            # BELOW_NORMAL so the GUI and OS always stay responsive.
            with ProcessPoolExecutor(max_workers=self.max_workers, initializer=_worker_process_init) as executor:
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

                        # Adaptive CPU throttle: every N files, check if the system
                        # is overloaded and sleep briefly to keep GUI responsive.
                        if self.stats.files_scanned % _CPU_THROTTLE_INTERVAL == 0:
                            self._maybe_throttle_cpu()

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

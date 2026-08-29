"""
Audio Duplicate Dataset Generator - V5
======================================
Cambios respecto a V4 (documentados):

NUEVA CATEGORIA: LOW_CONFIDENCE_REVIEW (20 casos)
  - Karaoke (Inversión de fase / vocal removal simple)
  - Sample/Loop (Extrae 5s y repite 4 veces)
  - Pitch & Speed (Remix agresivo)
  - Reverberación extrema (Concierto / Live simulado)
"""

import os
import sys
import csv
import random
import shutil
import argparse
import subprocess
import stat
import time
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.models import DuplicateType


def make_writable(path):
    try:
        if path.exists():
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass


def safe_copy(src, dst, retries=5, delay=0.4):
    if dst.exists():
        make_writable(dst)
        try:
            dst.unlink(missing_ok=True)
        except Exception:
            pass
    for attempt in range(retries):
        try:
            make_writable(dst)
            shutil.copyfile(src, dst)
            return
        except PermissionError:
            if attempt == retries - 1:
                try:
                    make_writable(dst)
                    with open(src, 'rb') as f_in, open(dst, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    return
                except Exception as e:
                    print(f"  Error de permisos al copiar a {dst}: {e}")
                    raise
            time.sleep(delay)


def run_ffmpeg(args):
    """
    CAMBIO 4: Devuelve bool en lugar de lanzar excepcion.
    Un fallo individual no aborta toda la generacion del dataset.
    """
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "quiet", "-nostdin"] + args,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


def get_duration_ffprobe(path):
    """
    CAMBIO 3: ffprobe para duracion real, mas robusto que extract_metadata().
    Retorna 0.0 si ffprobe falla.
    """
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path)
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def find_source_files(source_path, audio_exts, max_files=100):
    """Busca archivos de audio recursivamente. Sin cambios respecto a V1."""
    files = []
    print(f"Buscando hasta {max_files} archivos de audio en {source_path}...")
    try:
        for root, _, filenames in os.walk(source_path):
            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext in audio_exts:
                    file_path = Path(root) / filename
                    if file_path.stat().st_size > 0:
                        files.append(file_path)
                        if len(files) % 25 == 0:
                            print(f"  Encontrados {len(files)}/{max_files} archivos...")
                        if len(files) >= max_files:
                            return files
    except Exception as e:
        print(f"Advertencia durante el escaneo: {e}")
    return files


def generate_dataset(source_dir, out_dir, max_source_files=100):
    """
    Genera el dataset de benchmark V2 compatible con las reglas de comparator.py.
    Punto de entrada mantenido con el mismo nombre para compatibilidad CLI.
    """
    source_path = Path(source_dir)
    out_path = Path(out_dir)

    if not source_path.exists() or not source_path.is_dir():
        print(f"Error: El directorio origen '{source_dir}' no existe.")
        return

    out_path.mkdir(parents=True, exist_ok=True)

    audio_exts = {'.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg'}
    source_files = find_source_files(source_path, audio_exts, max_files=max_source_files)

    if len(source_files) < 10:
        print(f"Error: Se encontraron solo {len(source_files)} archivos de audio en {source_dir}.")
        print("Por favor, provee al menos 10 canciones distintas.")
        return

    dirs = defaultdict(list)
    for f in source_files:
        dirs[f.parent].append(f)

    manifest_path = out_path / "manifest.csv"
    pairs = []
    error_count = 0

    print(f"\nGenerando dataset V2 en '{out_dir}' usando {len(source_files)} canciones originales...")
    print("=" * 65)

    # 1. EXACT_HASH (20) - SIN CAMBIOS
    print("\n[1/5] EXACT_HASH (20) - shutil.copy2 byte-for-byte...")
    for i in range(20):
        src = random.choice(source_files)
        name_a = f"EH_{i:02d}_A{src.suffix}"
        name_b = f"EH_{i:02d}_B{src.suffix}"
        path_a = out_path / name_a
        path_b = out_path / name_b
        try:
            safe_copy(src, path_a)
            safe_copy(src, path_b)
            pairs.append((name_a, name_b, DuplicateType.EXACT_HASH.value))
        except Exception as e:
            print(f"  ERROR EH_{i:02d}: {e}")
            error_count += 1

    # 2. EXACT_AUDIO (20) - V4 Adversarial: WAV -> ALAC (m4a)
    print("\n[2/5] EXACT_AUDIO (20) - V4: fuente->WAV (Track A) + WAV->ALAC (Track B)...")
    for i in range(20):
        src = random.choice(source_files)
        name_a = f"EA_{i:02d}_A.wav"
        name_b = f"EA_{i:02d}_B.m4a"
        path_a = out_path / name_a
        path_b = out_path / name_b
        try:
            ok_a = run_ffmpeg([
                "-i", str(src),
                "-c:a", "pcm_s16le", "-ac", "2", "-ar", "44100",
                str(path_a)
            ])
            if not ok_a or not path_a.exists() or path_a.stat().st_size == 0:
                raise RuntimeError(f"Fallo al decodificar {src.name} a WAV")

            ok_b = run_ffmpeg(["-i", str(path_a), "-c:a", "alac", str(path_b)])
            if not ok_b or not path_b.exists() or path_b.stat().st_size == 0:
                raise RuntimeError(f"Fallo al convertir {name_a} a ALAC")

            pairs.append((name_a, name_b, DuplicateType.EXACT_AUDIO.value))
        except Exception as e:
            print(f"  ERROR EA_{i:02d}: {e}")
            error_count += 1

    # 3. ACOUSTIC_DUPLICATE (40) - V4 Adversarial: Low bitrate + Mono
    print("\n[3/5] ACOUSTIC_DUPLICATE (40) - V4: transcodificacion 32k/48k/64k + Mono...")
    for i in range(40):
        src = random.choice(source_files)
        name_a = f"AD_{i:02d}_A{src.suffix}"
        name_b = f"AD_{i:02d}_B.mp3"
        path_a = out_path / name_a
        path_b = out_path / name_b
        bitrate = random.choice(["32k", "48k", "64k"])
        try:
            safe_copy(src, path_a)
            ok = run_ffmpeg([
                "-i", str(src),
                "-c:a", "libmp3lame", "-b:a", bitrate, "-ac", "1",
                str(path_b)
            ])
            if not ok or not path_b.exists() or path_b.stat().st_size == 0:
                raise RuntimeError(f"Fallo ffmpeg {bitrate}")
            pairs.append((name_a, name_b, DuplicateType.ACOUSTIC_DUPLICATE.value))
        except Exception as e:
            print(f"  ERROR AD_{i:02d}: {e}")
            error_count += 1

    # 4. POSSIBLE_DUPLICATE (20) - V4 Adversarial: 4 Subtipos
    print("\n[4/5] POSSIBLE_DUPLICATE (20) - V4: 4 Subtipos Adversariales...")
    for i in range(20):
        src = random.choice(source_files)
        name_a = f"PD_{i:02d}_A{src.suffix}"
        name_b = f"PD_{i:02d}_B.mp3"
        path_a = out_path / name_a
        path_b = out_path / name_b
        try:
            safe_copy(src, path_a)
            dur_a = get_duration_ffprobe(path_a)
            
            if i < 5:
                # Subtipo A: Truncado al final (35s)
                target_duration = max(10, int(dur_a) - 35)
                ok = run_ffmpeg(["-i", str(src), "-t", str(target_duration), "-c:a", "libmp3lame", "-b:a", "128k", str(path_b)])
                tipo = "Truncado Fin 35s"
            elif i < 10:
                # Subtipo B: Truncado al inicio (20s) (Prueba offset penalization)
                ok = run_ffmpeg(["-i", str(src), "-ss", "20", "-c:a", "libmp3lame", "-b:a", "128k", str(path_b)])
                tipo = "Truncado Inicio 20s"
            elif i < 15:
                # Subtipo C: EQ Extremo
                ok = run_ffmpeg(["-i", str(src), "-af", "lowpass=f=1000,highpass=f=100,volume=0.5", "-c:a", "libmp3lame", "-b:a", "128k", str(path_b)])
                tipo = "EQ Extremo"
            else:
                # Subtipo D: Tempo ligero (1.02x) - Para probar robustez a pequeñísimas variaciones
                ok = run_ffmpeg(["-i", str(src), "-af", "atempo=1.02", "-c:a", "libmp3lame", "-b:a", "128k", str(path_b)])
                tipo = "Tempo 1.02x"

            if not ok or not path_b.exists() or path_b.stat().st_size == 0:
                raise RuntimeError(f"Fallo ffmpeg en {tipo}")

            dur_b = get_duration_ffprobe(path_b)
            diff = abs(dur_b - dur_a)
            status = "OK" if diff <= 90 else f"WARN diff={diff:.0f}s >90s"
            print(f"  PD_{i:02d} ({tipo}): A={dur_a:.0f}s B={dur_b:.0f}s diff={diff:.0f}s [{status}]")

            pairs.append((name_a, name_b, DuplicateType.POSSIBLE_DUPLICATE.value))
        except Exception as e:
            print(f"  ERROR PD_{i:02d}: {e}")
            error_count += 1

    # 5. LOW_CONFIDENCE_REVIEW (20) - V5 Adversarial: Hard Negatives (Karaoke, Remix, Sample)
    print("\n[5/6] LOW_CONFIDENCE_REVIEW (20) - V5: Karaoke, Loops, Remixes...")
    for i in range(20):
        src = random.choice(source_files)
        name_a = f"LC_{i:02d}_A{src.suffix}"
        name_b = f"LC_{i:02d}_B.mp3"
        path_a = out_path / name_a
        path_b = out_path / name_b
        try:
            safe_copy(src, path_a)
            if i < 5:
                # Subtipo Karaoke (Fase invertida)
                ok = run_ffmpeg(["-i", str(src), "-af", "pan=stereo|c0=c0-c1|c1=c0-c1", "-c:a", "libmp3lame", "-b:a", "128k", str(path_b)])
            elif i < 10:
                # Subtipo Remix agresivo (Pitch + Speed)
                ok = run_ffmpeg(["-i", str(src), "-af", "asetrate=44100*1.15,aresample=44100", "-c:a", "libmp3lame", "-b:a", "128k", str(path_b)])
            elif i < 15:
                # Subtipo Sample/Loop (10 segundos loopeados)
                ok = run_ffmpeg(["-i", str(src), "-ss", "30", "-t", "10", "-filter_complex", "aloop=loop=3:size=441000", "-c:a", "libmp3lame", "-b:a", "128k", str(path_b)])
            else:
                # Subtipo Live / Reverb extremo
                ok = run_ffmpeg(["-i", str(src), "-af", "aecho=0.8:0.9:1000:0.3", "-c:a", "libmp3lame", "-b:a", "128k", str(path_b)])

            if not ok or not path_b.exists() or path_b.stat().st_size == 0:
                raise RuntimeError("Fallo ffmpeg en LOW_CONFIDENCE_REVIEW")

            pairs.append((name_a, name_b, DuplicateType.LOW_CONFIDENCE_REVIEW.value))
        except Exception as e:
            print(f"  ERROR LC_{i:02d}: {e}")
            error_count += 1

    # 6. NO_MATCH (50) - hard negatives 70%
    print("\n[6/6] NO_MATCH (50) - 70% hard negatives (mismo artista/directorio)...")
    nm_count = 0
    nm_attempts = 0
    valid_dirs = [files for files in dirs.values() if len(files) >= 2]

    while nm_count < 50 and nm_attempts < 500:
        nm_attempts += 1
        try:
            if valid_dirs and random.random() < 0.70:
                chosen = random.choice(valid_dirs)
                src_a, src_b = random.sample(chosen, 2)
            else:
                src_a, src_b = random.sample(source_files, 2)
            
            # Evitar seleccionar archivos identicos por accidente
            if src_a.stat().st_size == src_b.stat().st_size:
                continue

            i = nm_count
            name_a = f"NM_{i:02d}_A{src_a.suffix}"
            name_b = f"NM_{i:02d}_B{src_b.suffix}"
            path_a = out_path / name_a
            path_b = out_path / name_b
            safe_copy(src_a, path_a)
            safe_copy(src_b, path_b)
            pairs.append((name_a, name_b, DuplicateType.NO_MATCH.value))
            nm_count += 1
        except Exception as e:
            print(f"  ERROR NM intento {nm_attempts}: {e}")

    if nm_count < 50:
        print(f"  ADVERTENCIA: Solo se generaron {nm_count}/50 pares NO_MATCH.")

    print(f"\nEscribiendo manifiesto en {manifest_path}...")
    with open(manifest_path, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["track_a_path", "track_b_path", "expected_category"])
        for p in pairs:
            writer.writerow(p)

    from collections import Counter
    dist = Counter(p[2] for p in pairs)

    print("\n" + "=" * 65)
    print("Dataset V2 generado.")
    print(f"Total de pares: {len(pairs)}  |  Errores de generacion: {error_count}")
    print("\nDistribucion:")
    for cls in ["EXACT_HASH", "EXACT_AUDIO", "ACOUSTIC_DUPLICATE", "POSSIBLE_DUPLICATE", "LOW_CONFIDENCE_REVIEW", "NO_MATCH"]:
        print(f"  {cls}: {dist.get(cls, 0)}")
    print(f"\nCarpeta de salida: {out_path.absolute()}")
    print("Ahora puedes ejecutar la evaluacion usando este dataset.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Genera un dataset de audio V5 compatible con las reglas de comparator.py."
    )
    parser.add_argument("source_dir", help="Directorio con canciones originales")
    parser.add_argument("--out-dir", default="benchmark_dataset_v5",
                        help="Directorio de salida (default: benchmark_dataset_v5)")
    parser.add_argument("--max-source-files", type=int, default=100,
                        help="Limite de canciones a escanear del origen (default: 100)")
    args = parser.parse_args()
    generate_dataset(args.source_dir, args.out_dir, max_source_files=args.max_source_files)

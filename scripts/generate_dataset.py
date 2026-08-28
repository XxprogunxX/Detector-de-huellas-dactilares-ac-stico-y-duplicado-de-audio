import os
import sys
import csv
import random
import shutil
import argparse
import subprocess
from pathlib import Path

# Agregar directorio padre al path para importar DuplicateType
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.models import DuplicateType

import time
import stat

def make_writable(path: Path):
    """Asegura que el archivo tenga permisos de escritura si existe."""
    try:
        if path.exists():
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass

def safe_copy(src: Path, dst: Path, retries: int = 5, delay: float = 0.4):
    """Copia un archivo manejando posibles bloqueos temporales de Windows/OneDrive."""
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
                # Último intento: lectura y escritura por chunks
                try:
                    make_writable(dst)
                    with open(src, 'rb') as f_in, open(dst, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    return
                except Exception as e:
                    print(f"Error de permisos al copiar a {dst}: {e}")
                    raise
            time.sleep(delay)

def run_ffmpeg(args):
    """Ejecuta ffmpeg silenciosamente."""
    subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-nostdin"] + args, check=True)

def find_source_files(source_path: Path, audio_exts: set, max_files: int = 100) -> list[Path]:
    """Busca archivos de audio recursivamente y se detiene al alcanzar el límite."""
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

def generate_dataset(source_dir: str, out_dir: str, max_source_files: int = 100):
    source_path = Path(source_dir)
    out_path = Path(out_dir)
    
    if not source_path.exists() or not source_path.is_dir():
        print(f"Error: El directorio origen '{source_dir}' no existe.")
        return

    out_path.mkdir(parents=True, exist_ok=True)
    
    # Obtener archivos de audio soportados (limitado a extensiones comunes)
    audio_exts = {'.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg'}
    source_files = find_source_files(source_path, audio_exts, max_files=max_source_files)
    
    if len(source_files) < 10:
        print(f"Error: Se encontraron solo {len(source_files)} archivos de audio en {source_dir}.")
        print("Por favor, provee al menos 10 canciones distintas para asegurar una buena variabilidad de NO_MATCH.")
        return

    manifest_path = out_path / "manifest.csv"
    pairs = []
    
    print(f"Generando dataset en {out_dir} usando {len(source_files)} canciones originales...")
    
    # Asegurar distribución mínima
    # EXACT_HASH (20), EXACT_AUDIO (20), ACOUSTIC (40), POSSIBLE (20), NO_MATCH (50) = 150 pares
    
    # 1. Generar EXACT_HASH (copia bit a bit)
    print("Generando casos EXACT_HASH (20)...")
    for i in range(20):
        src = random.choice(source_files)
        # track A
        name_a = f"EH_{i}_A{src.suffix}"
        path_a = out_path / name_a
        safe_copy(src, path_a)
        
        # track B (misma copia)
        name_b = f"EH_{i}_B{src.suffix}"
        path_b = out_path / name_b
        safe_copy(src, path_b)
        
        pairs.append((name_a, name_b, DuplicateType.EXACT_HASH.value))

    # 2. Generar EXACT_AUDIO (mismo audio, cambio de contenedor/metadatos)
    print("Generando casos EXACT_AUDIO (20)...")
    for i in range(20):
        src = random.choice(source_files)
        # track A (original)
        name_a = f"EA_{i}_A{src.suffix}"
        path_a = out_path / name_a
        safe_copy(src, path_a)
        
        # track B (convertir a WAV lossless para cambiar el hash pero no la acústica)
        name_b = f"EA_{i}_B.wav"
        path_b = out_path / name_b
        run_ffmpeg(["-i", str(src), "-c:a", "pcm_s16le", str(path_b)])
        
        pairs.append((name_a, name_b, DuplicateType.EXACT_AUDIO.value))

    # 3. Generar ACOUSTIC_DUPLICATE (transcodificación a baja calidad)
    print("Generando casos ACOUSTIC_DUPLICATE (40)...")
    for i in range(40):
        src = random.choice(source_files)
        # track A (original)
        name_a = f"AD_{i}_A{src.suffix}"
        path_a = out_path / name_a
        safe_copy(src, path_a)
        
        # track B (MP3 a 64kbps o 96kbps)
        name_b = f"AD_{i}_B.mp3"
        path_b = out_path / name_b
        bitrate = random.choice(["64k", "96k", "128k"])
        run_ffmpeg(["-i", str(src), "-c:a", "libmp3lame", "-b:a", bitrate, str(path_b)])
        
        pairs.append((name_a, name_b, DuplicateType.ACOUSTIC_DUPLICATE.value))
        
    # 4. Generar POSSIBLE_DUPLICATE (corte de audio)
    print("Generando casos POSSIBLE_DUPLICATE (20)...")
    for i in range(20):
        src = random.choice(source_files)
        # track A (original)
        name_a = f"PD_{i}_A{src.suffix}"
        path_a = out_path / name_a
        safe_copy(src, path_a)
        
        # track B (truncar a 30 segundos)
        name_b = f"PD_{i}_B.mp3"
        path_b = out_path / name_b
        run_ffmpeg(["-i", str(src), "-c:a", "libmp3lame", "-b:a", "128k", "-t", "30", str(path_b)])
        
        pairs.append((name_a, name_b, DuplicateType.POSSIBLE_DUPLICATE.value))

    # 5. Generar NO_MATCH (archivos distintos)
    print("Generando casos NO_MATCH (50)...")
    for i in range(50):
        src_a, src_b = random.sample(source_files, 2)
        # track A
        name_a = f"NM_{i}_A{src_a.suffix}"
        path_a = out_path / name_a
        safe_copy(src_a, path_a)
        
        # track B
        name_b = f"NM_{i}_B{src_b.suffix}"
        path_b = out_path / name_b
        safe_copy(src_b, path_b)
        
        pairs.append((name_a, name_b, DuplicateType.NO_MATCH.value))

    # Guardar el CSV
    print(f"Escribiendo manifiesto en {manifest_path}...")
    with open(manifest_path, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["track_a_path", "track_b_path", "expected_category"])
        for p in pairs:
            writer.writerow(p)

    print("\n¡Dataset generado con éxito!")
    print(f"Total de pares generados: {len(pairs)}")
    print(f"Carpeta de salida: {out_path.absolute()}")
    print("Ahora puedes ejecutar la evaluación usando este dataset.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Genera un dataset de audio sintético para el motor de duplicados.")
    parser.add_argument("source_dir", help="Directorio con tus canciones originales (WAV, MP3, FLAC, etc.)")
    parser.add_argument("--out-dir", default="dataset_validacion", help="Directorio donde se creará el dataset (default: dataset_validacion)")
    parser.add_argument("--max-source-files", type=int, default=100, help="Límite máximo de canciones a escanear del origen (default: 100)")
    
    args = parser.parse_args()
    generate_dataset(args.source_dir, args.out_dir, max_source_files=args.max_source_files)

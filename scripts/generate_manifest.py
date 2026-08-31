"""
generate_manifest.py

Escanea una carpeta de música y genera un esqueleto de manifest.csv
para el framework de validación (Fase 3/4).

Qué hace:
1. Encuentra todos los archivos de audio (recursivo).
2. Genera un inventario completo (inventory.csv) con todos los archivos
   encontrados, agrupados por carpeta — útil como referencia para armar
   a mano los pares de ACOUSTIC_DUPLICATE / POSSIBLE_DUPLICATE / EXACT_*
   (esos requieren que tú sepas cuáles archivos son versiones del mismo
   audio, el script no puede adivinar eso).
3. Genera automáticamente sugerencias de pares NO_MATCH "hard negative":
   archivos que comparten carpeta (probablemente mismo artista/álbum)
   pero con nombres de archivo distintos. Estas SIEMPRE deben revisarse
   a mano antes de correr la evaluación real — el script marca cada
   fila como "AUTO-SUGGESTED: verificar" precisamente para eso.

Uso:
    python scripts/generate_manifest.py --folder "C:\\ruta\\a\\tu\\musica" --output manifest_template.csv

Parámetros opcionales:
    --extensions   Extensiones de audio a incluir (default: mp3,flac,wav,m4a,ogg)
    --max-pairs-per-folder   Máximo de pares NO_MATCH sugeridos por carpeta (default: 5)
    --seed         Semilla para el muestreo aleatorio de pares (default: 42, reproducible)
    --max-files    Límite opcional de archivos de audio a escanear (0 = sin límite)
"""

import argparse
import csv
import os
import random
from collections import defaultdict
from itertools import combinations
from pathlib import Path


DEFAULT_EXTENSIONS = {"mp3", "flac", "wav", "m4a", "ogg"}


def find_audio_files(base_folder: Path, extensions: set, max_files: int = 0) -> list[Path]:
    """Encuentra recursivamente todos los archivos de audio bajo base_folder."""
    audio_files = []
    print(f"Escaneando archivos de audio en {base_folder}...")
    for root, _dirs, files in os.walk(base_folder):
        for fname in files:
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            if ext in extensions:
                audio_files.append(Path(root) / fname)
                if len(audio_files) % 500 == 0:
                    print(f"  Encontrados {len(audio_files)} archivos...")
                if max_files > 0 and len(audio_files) >= max_files:
                    print(f"Límite alcanzado: {max_files} archivos.")
                    return sorted(audio_files)
    print(f"Total de archivos encontrados: {len(audio_files)}")
    return sorted(audio_files)


def write_inventory(audio_files: list[Path], base_folder: Path, output_path: Path) -> None:
    """Escribe un inventario completo de archivos encontrados, agrupados por carpeta."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["relative_path", "parent_folder", "filename", "extension"])
        for fpath in audio_files:
            rel = fpath.relative_to(base_folder)
            writer.writerow([
                str(rel),
                str(rel.parent) if rel.parent != Path(".") else "(raíz)",
                fpath.name,
                fpath.suffix.lstrip("."),
            ])
    print(f"Inventario completo escrito en: {output_path} ({len(audio_files)} archivos)")


def suggest_no_match_pairs(
    audio_files: list[Path],
    base_folder: Path,
    max_pairs_per_folder: int,
    seed: int,
) -> list[tuple[str, str]]:
    """
    Agrupa archivos por carpeta padre y sugiere pares NO_MATCH (hard
    negatives) dentro de cada carpeta con al menos 2 archivos.

    Estos son SUGERENCIAS, no verdad confirmada — el script no sabe si
    dos archivos en la misma carpeta son en realidad la misma canción
    en dos formatos (lo cual sería ACOUSTIC_DUPLICATE, no NO_MATCH).
    Revisa cada fila antes de correr la evaluación real.
    """
    rng = random.Random(seed)
    by_folder = defaultdict(list)
    for fpath in audio_files:
        rel = fpath.relative_to(base_folder)
        by_folder[rel.parent].append(fpath)

    suggested_pairs = []
    for folder, files in by_folder.items():
        if len(files) < 2:
            continue
        all_pairs = list(combinations(files, 2))
        rng.shuffle(all_pairs)
        chosen = all_pairs[:max_pairs_per_folder]
        for a, b in chosen:
            rel_a = a.relative_to(base_folder)
            rel_b = b.relative_to(base_folder)
            suggested_pairs.append((str(rel_a), str(rel_b)))

    return suggested_pairs


def write_manifest_template(
    suggested_pairs: list[tuple[str, str]],
    output_path: Path,
) -> None:
    """Escribe el manifest.csv con las sugerencias automáticas y filas vacías
    para que completes a mano las demás categorías."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["track_a_path", "track_b_path", "expected_category", "note"])

        for a, b in suggested_pairs:
            writer.writerow([a, b, "NO_MATCH", "AUTO-SUGGESTED: verificar que NO sean la misma canción"])

        # Filas vacías de ejemplo para las categorías que el script no puede
        # adivinar por sí solo — requieren que tú sepas qué archivos son
        # versiones del mismo audio.
        writer.writerow(["", "", "EXACT_HASH", "TODO: completa con una copia bit-a-bit idéntica"])
        writer.writerow(["", "", "EXACT_AUDIO", "TODO: mismo audio, distinta metadata/contenedor"])
        writer.writerow(["", "", "ACOUSTIC_DUPLICATE", "TODO: mismo audio, distinto bitrate/formato"])
        writer.writerow(["", "", "POSSIBLE_DUPLICATE", "TODO: remaster / radio edit / versión extendida"])
        writer.writerow(["", "", "UNCERTAIN", "TODO: archivo corrupto o sin huella extraíble"])

    print(f"Manifest template escrito en: {output_path}")
    print(f"  - {len(suggested_pairs)} pares NO_MATCH auto-sugeridos (revísalos antes de correr la evaluación)")
    print("  - 5 filas TODO para completar a mano (EXACT_HASH, EXACT_AUDIO, ACOUSTIC_DUPLICATE, POSSIBLE_DUPLICATE, UNCERTAIN)")


def main():
    parser = argparse.ArgumentParser(description="Genera un manifest.csv esqueleto para el framework de validación.")
    parser.add_argument("--folder", required=True, help="Carpeta base con tu música real.")
    parser.add_argument("--output", default="manifest_template.csv", help="Ruta o nombre de salida del manifest.")
    parser.add_argument("--extensions", default=",".join(DEFAULT_EXTENSIONS), help="Extensiones separadas por coma.")
    parser.add_argument("--max-pairs-per-folder", type=int, default=5, help="Máximo de pares NO_MATCH sugeridos por carpeta.")
    parser.add_argument("--seed", type=int, default=42, help="Semilla para el muestreo aleatorio (reproducible).")
    parser.add_argument("--max-files", type=int, default=0, help="Límite máximo de archivos a escanear (0 para todos).")
    args = parser.parse_args()

    base_folder = Path(args.folder).resolve()
    if not base_folder.is_dir():
        raise SystemExit(f"Error: la carpeta no existe: {base_folder}")

    extensions = {e.strip().lower().lstrip(".") for e in args.extensions.split(",")}

    audio_files = find_audio_files(base_folder, extensions, max_files=args.max_files)
    if not audio_files:
        raise SystemExit(f"No se encontraron archivos de audio con extensiones {extensions} en {base_folder}")

    # Determinar ruta de salida de inventory y manifest
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = base_folder / output_path

    inventory_path = output_path.parent / "inventory.csv"
    write_inventory(audio_files, base_folder, inventory_path)

    suggested_pairs = suggest_no_match_pairs(
        audio_files, base_folder, args.max_pairs_per_folder, args.seed
    )

    write_manifest_template(suggested_pairs, output_path)

    print()
    print("Siguiente paso:")
    print(f"  1. Revisa {output_path.name} — confirma que los pares NO_MATCH sugeridos")
    print("     realmente son canciones distintas (no versiones del mismo audio).")
    print(f"  2. Usa {inventory_path.name} como referencia para completar a mano las")
    print("     filas TODO de EXACT_HASH, EXACT_AUDIO, ACOUSTIC_DUPLICATE, POSSIBLE_DUPLICATE.")
    print("  3. Cuando esté listo, pasa la ruta a evaluation_runner.py.")


if __name__ == "__main__":
    main()

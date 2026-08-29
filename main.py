"""
Audio Duplicate & Acoustic Fingerprinting Detector.
Main entrypoint supporting both Modern Desktop GUI and Headless CLI modes.
"""

import os
import sys
import argparse
import multiprocessing
from typing import Optional


def main():
    # Crucial for Windows PyInstaller executables using multiprocessing / ProcessPoolExecutor
    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser(
        description="Analizador de bibliotecas de música y detector de duplicados acústicos."
    )
    parser.add_argument(
        "--folder", "-f",
        type=str,
        help="Ruta de la carpeta de música a analizar."
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Ejecutar en modo consola / headless sin abrir la interfaz gráfica."
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Ruta personalizada para la base de datos de huellas SQLite."
    )
    parser.add_argument(
        "--auto-move",
        type=str,
        default=None,
        help="Mover automáticamente los duplicados inferiores a la carpeta especificada."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostrar qué sucedería sin mover o eliminar ningún archivo."
    )
    parser.add_argument(
        "--export-csv",
        type=str,
        default=None,
        help="Ruta para exportar los resultados en formato CSV."
    )

    args = parser.parse_args()

    if args.cli:
        run_cli_mode(args)
    else:
        from gui.app import run_gui
        run_gui(initial_folder=args.folder)


def run_cli_mode(args):
    """Headless CLI execution for scripts or automated servers."""
    if not args.folder or not os.path.isdir(args.folder):
        print("❌ Error: Debes especificar una carpeta válida con --folder <ruta>")
        sys.exit(1)

    from core.scanner import AudioScanner
    from core.database import Database
    from core.file_manager import auto_apply_recommendations, move_marked_duplicates
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print(f"[bold cyan]🎵 Iniciando análisis acústico en:[/] {args.folder}")

    db = Database(db_path=args.db)
    scanner = AudioScanner(db=db)

    def cli_progress(stats):
        pct = (stats.files_scanned / max(1, stats.total_files_found)) * 100
        print(f"\r[{stats.phase}] {stats.files_scanned}/{stats.total_files_found} ({pct:.1f}%) | Duplicados: {stats.exact_duplicates_count + stats.acoustic_duplicates_count + stats.possible_duplicates_count}", end="", flush=True)

    groups = scanner.scan_directory(args.folder, progress_callback=cli_progress)
    print()

    console.print(f"\n[bold green]✅ Escaneo finalizado en {scanner.stats.elapsed_seconds:.1f}s[/]")
    console.print(f"Total archivos analizados: {scanner.stats.files_scanned}")
    console.print(f"Duplicados exactos: [bold blue]{scanner.stats.exact_duplicates_count}[/]")
    console.print(f"Duplicados acústicos: [bold green]{scanner.stats.acoustic_duplicates_count}[/]")
    console.print(f"Posibles duplicados: [bold yellow]{scanner.stats.possible_duplicates_count}[/]")
    savings_mb = scanner.stats.potential_space_saving / (1024 * 1024)
    console.print(f"Espacio recuperable: [bold magenta]{savings_mb:.2f} MB[/]\n")

    # Display Groups in Table
    for group in groups:
        table = Table(title=f"Grupo {group.group_id} ({group.primary_type.value}) - Similitud: {group.average_similarity:.1f}%")
        table.add_column("Acción", style="bold")
        table.add_column("Archivo", style="white")
        table.add_column("Formato", style="cyan")
        table.add_column("Bitrate", style="green")
        table.add_column("Duración", style="yellow")
        table.add_column("Tamaño", style="magenta")
        table.add_column("Calidad", style="white")

        for track in group.tracks:
            is_best = (track.filepath == group.best_track_path)
            if getattr(group, "requires_manual_review", False):
                action_tag = "[yellow]REVISAR[/]"
            else:
                action_tag = "[green]CONSERVAR[/]" if is_best else "[red]ELIMINAR[/]"
            table.add_row(
                action_tag,
                track.filename,
                track.format,
                f"{track.bitrate}k",
                track.formatted_duration,
                track.formatted_size,
                f"{track.quality_score}/100"
            )
        console.print(table)
        console.print(f"[bold yellow]Recomendación:[/] {group.best_track_reason}\n")

    if args.export_csv:
        import csv
        with open(args.export_csv, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Grupo ID", "Tipo Duplicado", "Acción Recomendada", "Ruta Archivo", "Formato", "Bitrate", "Duración", "Tamaño bytes", "Puntaje Calidad", "Razón"])
            for group in groups:
                for track in group.tracks:
                    is_best = (track.filepath == group.best_track_path)
                    if getattr(group, "requires_manual_review", False):
                        action = "REVISAR"
                    else:
                        action = "CONSERVAR" if is_best else "ELIMINAR"
                    writer.writerow([
                        group.group_id, group.primary_type.value, action, track.filepath,
                        track.format, track.bitrate, track.duration, track.filesize,
                        track.quality_score, group.best_track_reason if is_best else ""
                    ])
        console.print(f"[bold green]✅ Resultados exportados a:[/] {args.export_csv}\n")

    if args.auto_move:
        if args.dry_run:
            console.print(f"[bold yellow]DRY-RUN:[/] Se moverían los duplicados inferiores a: {args.auto_move}")
        else:
            auto_apply_recommendations(groups)
            console.print(f"[bold cyan]Moviendo duplicados a:[/] {args.auto_move}")
            success, failed, logs = move_marked_duplicates(groups, args.auto_move, db=db)
            console.print(f"[bold green]Movidos con éxito: {success} archivos. Errores: {failed}[/]")


if __name__ == "__main__":
    main()

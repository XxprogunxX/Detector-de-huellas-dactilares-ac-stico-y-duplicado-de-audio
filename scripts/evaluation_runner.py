import os
import sys
import csv
import json
import argparse
from collections import defaultdict
from typing import Dict, Any, List

# Add parent directory to path so we can import core modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.scanner import _process_audio_worker
from core.models import AudioTrack, DuplicateType
from core.comparator import compare_tracks

def calculate_metrics(confusion_matrix: Dict[str, Dict[str, int]], misclassifications: List[Dict[str, Any]], evaluated_cases: int) -> Dict[str, Any]:
    categories = [e.value for e in DuplicateType]
    
    metrics = {
        "global": {
            "macro_precision": 0.0,
            "macro_recall": 0.0,
            "macro_f1": 0.0
        },
        "per_class": {},
        "critical_errors_count": 0
    }
    
    precisions = []
    recalls = []
    f1s = []
    
    for c in categories:
        tp = confusion_matrix[c].get(c, 0)
        
        # FP: sum of column c, minus TP
        fp = sum(confusion_matrix[row].get(c, 0) for row in categories) - tp
        
        # FN: sum of row c, minus TP
        fn = sum(confusion_matrix[c].get(col, 0) for col in categories) - tp
        
        # TN: total evaluated - (TP + FP + FN)
        tn = evaluated_cases - (tp + fp + fn)
        
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        fnr = fn / (tp + fn) if (tp + fn) > 0 else 0.0
        
        # DECISIÓN DE DISEÑO: Opción B (Estándar con exclusión por falta de soporte)
        # Si una categoría tiene TP=0, FP=0, FN=0 significa que no existía en el dataset (expected=0)
        # ni el modelo la predijo por error (predicted=0). 
        # Excluirla del Macro-Average evita castigar el score global por falta de cobertura en el dataset de prueba.
        # Si el modelo la predice por error (FP > 0) o se esperaba pero no la predijo (FN > 0), SÍ se incluye.
        if (tp + fp + fn) > 0:
            precisions.append(precision)
            recalls.append(recall)
            f1s.append(f1)
        
        metrics["per_class"][c] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "fpr": round(fpr, 4),
            "fnr": round(fnr, 4),
            "tp": tp, "fp": fp, "tn": tn, "fn": fn
        }
    
    # Promedio Macro solo sobre clases con actividad (esperadas o predichas)
    active_classes = len(precisions)
    metrics["global"]["macro_precision"] = round(sum(precisions) / active_classes, 4) if active_classes > 0 else 0.0
    metrics["global"]["macro_recall"] = round(sum(recalls) / active_classes, 4) if active_classes > 0 else 0.0
    metrics["global"]["macro_f1"] = round(sum(f1s) / active_classes, 4) if active_classes > 0 else 0.0
    metrics["global"]["active_classes_in_average"] = active_classes
    
    # Classify severity of misclassifications
    for m in misclassifications:
        exp = m["expected"]
        pred = m["predicted"]
        
        if exp == DuplicateType.NO_MATCH.value and pred in [DuplicateType.EXACT_HASH.value, DuplicateType.EXACT_AUDIO.value, DuplicateType.ACOUSTIC_DUPLICATE.value, DuplicateType.POSSIBLE_DUPLICATE.value]:
            m["severity"] = "CRITICAL"
            metrics["critical_errors_count"] += 1
        elif exp in [DuplicateType.EXACT_HASH.value, DuplicateType.EXACT_AUDIO.value, DuplicateType.ACOUSTIC_DUPLICATE.value] and pred in [DuplicateType.POSSIBLE_DUPLICATE.value, DuplicateType.NO_MATCH.value, DuplicateType.UNCERTAIN.value]:
            m["severity"] = "MODERATE"
        elif exp == DuplicateType.POSSIBLE_DUPLICATE.value and pred in [DuplicateType.EXACT_HASH.value, DuplicateType.EXACT_AUDIO.value, DuplicateType.ACOUSTIC_DUPLICATE.value]:
            m["severity"] = "MODERATE"
        else:
            m["severity"] = "LOW"
            
    return metrics

def run_evaluation(manifest_path: str, dataset_dir: str, output_json: str, limit: int = 0):
    if not os.path.exists(manifest_path):
        print(f"Error: Manifest file '{manifest_path}' not found.")
        return

    summary = {
        "total_cases": 0,
        "evaluated_cases": 0,
        "error_cases": 0,
        "exact_matches": 0,
        "accuracy": 0.0
    }
    
    # Categories: EXACT_HASH, EXACT_AUDIO, ACOUSTIC_DUPLICATE, POSSIBLE_DUPLICATE, NO_MATCH, UNCERTAIN
    categories = [e.value for e in DuplicateType]
    
    confusion_matrix = {
        expected: {predicted: 0 for predicted in categories}
        for expected in categories
    }
    
    all_scores = []
    misclassifications = []
    errors = []

    # Cache de pistas ya procesadas para no recalcular huellas acústicas múltiples veces
    track_cache: Dict[str, AudioTrack] = {}

    with open(manifest_path, 'r', encoding='utf-8') as f:
        rows = [r for r in csv.DictReader(f) if r.get("track_a_path", "").strip() and r.get("track_b_path", "").strip()]

    if limit > 0 and len(rows) > limit:
        print(f"Aplicando límite: se evaluarán los primeros {limit} pares de {len(rows)} disponibles.")
        rows = rows[:limit]

    total_pairs = len(rows)
    print(f"\nIniciando evaluación de {total_pairs} pares de prueba...")
    sys.stdout.flush()

    for idx, row in enumerate(rows, 1):
        track_a_rel = row.get("track_a_path", "").strip()
        track_b_rel = row.get("track_b_path", "").strip()
        expected_category = row.get("expected_category", "").strip()

        summary["total_cases"] += 1
        
        if expected_category not in categories:
            errors.append({
                "row": row,
                "error": f"Invalid expected_category: {expected_category}"
            })
            summary["error_cases"] += 1
            continue

        track_a_full = os.path.join(dataset_dir, track_a_rel)
        track_b_full = os.path.join(dataset_dir, track_b_rel)

        if not os.path.exists(track_a_full) or not os.path.exists(track_b_full):
            errors.append({
                "row": row,
                "error": "One or both audio files do not exist."
            })
            summary["error_cases"] += 1
            continue
            
        try:
            # Mostrar progreso periódico en consola
            if idx % 5 == 0 or idx == 1 or idx == total_pairs:
                print(f"  [{idx}/{total_pairs}] ({idx/total_pairs*100:.1f}%) Procesando...", end='\r')
                sys.stdout.flush()

            # Obtener o procesar Pista A con caché
            if track_a_full in track_cache:
                t1 = track_cache[track_a_full]
            else:
                data_a = _process_audio_worker(track_a_full)
                if not data_a:
                    raise ValueError(f"No se pudieron extraer características de: {track_a_rel}")
                t1 = AudioTrack.from_dict(data_a)
                track_cache[track_a_full] = t1

            # Obtener o procesar Pista B con caché
            if track_b_full in track_cache:
                t2 = track_cache[track_b_full]
            else:
                data_b = _process_audio_worker(track_b_full)
                if not data_b:
                    raise ValueError(f"No se pudieron extraer características de: {track_b_rel}")
                t2 = AudioTrack.from_dict(data_b)
                track_cache[track_b_full] = t2
                
            # Ejecutar el motor de evidencias
            report = compare_tracks(t1, t2)
            predicted_category = report.classification.value
            
            # Actualizar matriz de confusión
            confusion_matrix[expected_category][predicted_category] += 1
            summary["evaluated_cases"] += 1
            
            all_scores.append({
                "track_a": track_a_rel,
                "track_b": track_b_rel,
                "expected": expected_category,
                "predicted": predicted_category,
                "confidence": report.confidence
            })

            if expected_category == predicted_category:
                summary["exact_matches"] += 1
            else:
                misclassifications.append({
                    "track_a": track_a_rel,
                    "track_b": track_b_rel,
                    "expected": expected_category,
                    "predicted": predicted_category,
                    "confidence": report.confidence,
                    "reasons": report.reasons
                })

        except Exception as e:
            errors.append({
                "row": row,
                "error": str(e)
            })
            summary["error_cases"] += 1

    if summary["evaluated_cases"] > 0:
        summary["accuracy"] = summary["exact_matches"] / summary["evaluated_cases"]

    evaluated_cases = summary["total_cases"] - summary["error_cases"]
    metrics = calculate_metrics(confusion_matrix, misclassifications, evaluated_cases)
    
    final_report = {
        "summary": summary,
        "metrics": metrics,
        "confusion_matrix": confusion_matrix,
        "misclassifications": misclassifications,
        "errors": errors
    }

    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(final_report, f, indent=4, ensure_ascii=False)
        
    # Guardar CSV crudo para análisis empírico
    csv_path = output_json.replace(".json", "_scores.csv")
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["track_a", "track_b", "expected", "predicted", "confidence"])
        for m in all_scores:
            writer.writerow([m["track_a"], m["track_b"], m["expected"], m["predicted"], f"{m['confidence']:.2f}"])

    print(f"\nEvaluation complete. Report saved to {output_json}")
    print(f"Scores salvados en: {csv_path}")
    print(f"Accuracy: {summary['accuracy'] * 100:.2f}% ({summary['exact_matches']}/{summary['evaluated_cases']})")
    if summary["error_cases"] > 0:
        print(f"Warning: {summary['error_cases']} cases failed to evaluate.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate the Evidence Engine against a labeled dataset.")
    parser.add_argument("--manifest", type=str, required=True, help="Path to manifest.csv")
    parser.add_argument("--dataset-dir", "--base-dir", dest="dataset_dir", type=str, required=True, help="Base directory for the audio files")
    parser.add_argument("--output", type=str, default="validation_report.json", help="Path to save the output JSON report")
    parser.add_argument("--limit", "--max-cases", dest="limit", type=int, default=0, help="Límite máximo de pares a evaluar (0 = todos)")
    args = parser.parse_args()
    
    run_evaluation(args.manifest, args.dataset_dir, args.output, limit=args.limit)

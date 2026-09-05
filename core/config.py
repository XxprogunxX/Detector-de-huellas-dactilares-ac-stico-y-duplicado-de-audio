"""
Centralized Detection Configuration and Persistence Engine (Phase B / AC-006).

Provides a single source of truth for duplicate detection thresholds,
duration firewalls, worker concurrency, and spectral analysis flags.
Guarantees immutability during scans and atomic disk persistence.
"""

import os
import json
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectionConfig:
    """
    Immutable configuration parameters for audio duplicate detection.

    Hierarchy & Validation:
      100.0 >= acoustic_threshold > possible_threshold > review_threshold >= 0.0
      max_auto_duration_diff >= 0.0
      min_duration >= 0.0
      max_workers is None or int >= 1
    """
    acoustic_threshold: float = 95.0
    possible_threshold: float = 80.0
    review_threshold: float = 40.0

    max_auto_duration_diff: float = 2.0
    min_duration: float = 5.0

    spectral_analysis: bool = True
    max_workers: Optional[int] = None

    def __post_init__(self):
        # Numeric type and range checks
        for name, val in [
            ("acoustic_threshold", self.acoustic_threshold),
            ("possible_threshold", self.possible_threshold),
            ("review_threshold", self.review_threshold),
            ("max_auto_duration_diff", self.max_auto_duration_diff),
            ("min_duration", self.min_duration),
        ]:
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(
                    f"{name} debe ser un valor numérico (int o float), recibido: {type(val).__name__}"
                )

        if not (100.0 >= self.acoustic_threshold):
            raise ValueError(
                f"acoustic_threshold ({self.acoustic_threshold}) debe ser <= 100.0"
            )
        if not (self.acoustic_threshold > self.possible_threshold):
            raise ValueError(
                f"acoustic_threshold ({self.acoustic_threshold}) debe ser mayor que "
                f"possible_threshold ({self.possible_threshold})"
            )
        if not (self.possible_threshold > self.review_threshold):
            raise ValueError(
                f"possible_threshold ({self.possible_threshold}) debe ser mayor que "
                f"review_threshold ({self.review_threshold})"
            )
        if not (self.review_threshold >= 0.0):
            raise ValueError(
                f"review_threshold ({self.review_threshold}) debe ser >= 0.0"
            )

        if not (self.max_auto_duration_diff >= 0.0):
            raise ValueError(
                f"max_auto_duration_diff ({self.max_auto_duration_diff}) debe ser >= 0.0"
            )
        if not (self.min_duration >= 0.0):
            raise ValueError(
                f"min_duration ({self.min_duration}) debe ser >= 0.0"
            )

        if not isinstance(self.spectral_analysis, bool):
            raise ValueError(
                f"spectral_analysis debe ser un booleano (True/False), recibido: {type(self.spectral_analysis).__name__}"
            )

        if self.max_workers is not None:
            if isinstance(self.max_workers, bool) or not isinstance(self.max_workers, int) or self.max_workers < 1:
                raise ValueError(
                    f"max_workers debe ser None o un entero >= 1, recibido: {self.max_workers}"
                )

    def to_dict(self) -> Dict[str, Any]:
        """Converts configuration to a serializable dictionary."""
        return {
            "acoustic_threshold": float(self.acoustic_threshold),
            "possible_threshold": float(self.possible_threshold),
            "review_threshold": float(self.review_threshold),
            "max_auto_duration_diff": float(self.max_auto_duration_diff),
            "min_duration": float(self.min_duration),
            "spectral_analysis": bool(self.spectral_analysis),
            "max_workers": self.max_workers,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DetectionConfig":
        """
        Builds DetectionConfig from dictionary, safely filtering unknown keys
        and validating loaded values.
        """
        valid_fields = {
            "acoustic_threshold",
            "possible_threshold",
            "review_threshold",
            "max_auto_duration_diff",
            "min_duration",
            "spectral_analysis",
            "max_workers",
        }
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


def get_default_config_path() -> str:
    """Returns canonical OS path for detection settings JSON."""
    app_data = os.getenv("APPDATA")
    if app_data:
        base_dir = os.path.join(app_data, "AudioDuplicateDetector")
    else:
        base_dir = os.path.join(os.path.expanduser("~"), ".audioduplicatedetector")
    return os.path.join(base_dir, "detection_settings.json")


def save_detection_config(config: DetectionConfig, path: Optional[str] = None) -> bool:
    """
    Persists DetectionConfig to disk using an atomic write pattern
    (.tmp -> flush -> fsync -> os.replace).
    """
    if not isinstance(config, DetectionConfig):
        raise TypeError(
            f"config debe ser una instancia de DetectionConfig, recibido: {type(config).__name__}"
        )

    target_path = path or get_default_config_path()
    parent_dir = os.path.dirname(target_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    temp_path = f"{target_path}.tmp"

    try:
        data = config.to_dict()
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, target_path)
        return True
    except Exception as e:
        logger.error(f"Error guardando DetectionConfig en {target_path}: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


def load_detection_config(path: Optional[str] = None) -> DetectionConfig:
    """
    Loads DetectionConfig from disk.
    If the file is missing, corrupt, or contains invalid thresholds,
    falls back safely to default DetectionConfig() and logs a warning.
    Unknown keys from future versions are safely ignored.
    """
    target_path = path or get_default_config_path()
    if not os.path.isfile(target_path):
        return DetectionConfig()

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            logger.warning(
                f"Formato JSON inválido en {target_path} (no es un objeto). Usando configuración por defecto."
            )
            return DetectionConfig()

        config = DetectionConfig.from_dict(data)
        return config
    except json.JSONDecodeError as e:
        logger.warning(
            f"Archivo de configuración corrupto ({target_path}): {e}. Usando configuración por defecto."
        )
        return DetectionConfig()
    except (ValueError, TypeError) as e:
        logger.warning(
            f"Valores de configuración inválidos en {target_path}: {e}. Usando configuración por defecto."
        )
        return DetectionConfig()
    except Exception as e:
        logger.warning(
            f"Error inesperado leyendo {target_path}: {e}. Usando configuración por defecto."
        )
        return DetectionConfig()

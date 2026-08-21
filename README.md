# 🎵 Audio Duplicate & Acoustic Fingerprinting Detector

Una aplicación de escritorio moderna y de alto rendimiento en Python para analizar grandes bibliotecas de música (decenas de miles de canciones) y detectar duplicados exactos, duplicados por audio y posibles versiones (remasters, radio edits, directos), utilizando **huellas acústicas locales (Chromaprint / fpcalc)**, análisis espectral FFT y hash de audio PCM sin depender de servicios externos.

---

## 🌟 Características Principales

1. **Detección de Duplicados en 3 Niveles**:
   - **Duplicados Exactos (100%)**: Detección instantánea de copias idénticas en disco (SHA-256) y de flujo de audio PCM decodificado (mismo audio exacto con diferentes etiquetas ID3 o contenedores).
   - **Duplicados Acústicos ($\ge 95\%$)**: Identifica la misma pista/grabación original incluso con diferente formato (`MP3`, `FLAC`, `WAV`, `M4A`, `OGG`, `AAC`), diferente tasa de bits (320k vs 128k), cambios de volumen o compresión.
   - **Posibles Duplicados ($80\% - 94\%$)**: Identifica remasterizaciones, radio edits, versiones extendidas o directos que comparten la misma estructura armónica.
   - **Cero Falsos Positivos**: Canciones diferentes del mismo artista jamás se agrupan juntas.

2. **Detección de Falsos Lossless (Transcodes / Fake FLAC)**:
   - Análisis FFT de corte de frecuencia (*spectral rolloff*): detecta si un archivo `.flac` o `.wav` fue convertido artificialmente desde un MP3 de 128 kbps (corte en ~16 kHz) o 320 kbps (corte en ~20 kHz).

3. **Recomendación Inteligente del "Mejor Archivo"**:
   - Calcula una puntuación de calidad (0 a 100) evaluando:
     * Fidelidad real del formato (lossless auténtico vs lossy vs falso FLAC).
     * Tasa de bits real y ancho de banda espectral.
     * Frecuencia de muestreo (44.1k, 48k, 96k Hi-Res) y profundidad de bits (16-bit vs 24-bit).
     * Integridad y duración completa de la pista.

4. **Rendimiento para Bibliotecas Grandes**:
   - **Caché persistente SQLite (`music_fingerprints.db`)**: el re-escaneo de archivos inalterados toma 0.01 ms.
   - **Procesamiento paralelo multi-núcleo**: decodificación y extracción de huellas simultánea en todos los núcleos de CPU.
   - **Control de escaneo**: Soporte para Pausar, Reanudar y Cancelar en cualquier momento.

5. **Interfaz Gráfica Moderna (CustomTkinter)**:
   - Tema oscuro elegante con tarjetas interactivas y visualización de espectro/metadata lado a lado.
   - **Reproductor de audio integrado** (`pygame`) para escuchar y comparar pistas antes de decidir.
   - Pestañas de filtrado (Todos, Exactos, Acústicos, Posibles), búsqueda instantánea y ordenación dinámica.
   - **Gestión segura de archivos**:
     * Botón para abrir la ubicación en el Explorador de Windows.
     * Marcar individualmente o con auto-recomendación `[CONSERVAR]` / `[ELIMINAR]`.
     * Opción para **Mover duplicados** a una carpeta de respaldo seleccionada.
     * Opción para **Eliminación permanente segura** con confirmación explícita (bloqueo estricto que impide borrar archivos marcados para conservar).

---

## 🛠️ Instalación y Requisitos

### Requisitos del Sistema
- **Sistema Operativo**: Windows 10/11 (o Linux / macOS).
- **Python**: 3.10 o superior.
- **FFmpeg**: Instalado y disponible en el sistema.
- **fpcalc**: Binario autónomo incluido en `bin/fpcalc.exe`.

### Instalación de Dependencias
```bash
pip install -r requirements.txt
```

---

## 🚀 Uso de la Aplicación

### Modo Interfaz Gráfica (Recomendado)
```bash
python main.py
```
O especificando una carpeta de inicio:
```bash
python main.py --folder "D:\MiMusica"
```

### Modo Consola / Headless (CLI)
Para escaneos rápidos o automatizaciones en servidores:
```bash
python main.py --cli --folder "D:\MiMusica"
```
Para escanear y mover duplicados automáticamente a una carpeta de respaldo:
```bash
python main.py --cli --folder "D:\MiMusica" --auto-move "D:\Duplicados_Backup"
```

---

## 🧪 Ejecución de Pruebas Automatizadas

El proyecto cuenta con una suite completa de pruebas unitarias e integración que genera audio sintético para validar todos los escenarios requeridos:

```bash
python -m unittest discover tests
```

Los tests cubren:
- Copias idénticas en disco.
- Mismo audio con diferentes tags ID3.
- Mismo audio en MP3 y FLAC.
- Mismo audio a diferentes bitrates (320k vs 128k).
- Detección de Falsos Lossless (FLAC inflado desde 128k MP3).
- Remasterizaciones y variaciones de ganancia/ecualización.
- Radio edits y pistas truncadas.
- Verificación estricta de cero falsos positivos (canciones distintas).
- Resistencia y velocidad de la caché SQLite.

---

## 📂 Estructura del Código

```
├── bin/
│   └── fpcalc.exe              # Binario Chromaprint de alta velocidad
├── core/
│   ├── models.py               # Modelos de datos (AudioTrack, DuplicateGroup, ScanStats)
│   ├── fingerprint.py          # Extracción Chromaprint, hashes y serialización
│   ├── metadata_extractor.py   # Extracción con Mutagen y FFprobe
│   ├── quality_analyzer.py     # Análisis FFT de cortes espectrales y calidad
│   ├── comparator.py           # Alineamiento temporal y distancia de Hamming
│   ├── clustering.py           # Agrupamiento disjunto (Union-Find) y ranking
│   ├── database.py             # Motor de base de datos SQLite WAL
│   ├── scanner.py              # Orquestador recursivo multiproceso
│   └── file_manager.py         # Operaciones seguras (mover, marcar, eliminar)
├── gui/
│   ├── app.py                  # Ventana principal moderna CustomTkinter
│   ├── styles.py               # Sistema de diseño, paleta dark y fuentes
│   └── components/
│       ├── duplicate_card.py   # Tarjeta interactiva de grupo
│       ├── audio_player.py     # Reproductor embebido con Pygame
│       ├── scan_progress.py    # Barra de progreso y estadísticas en vivo
│       └── filter_bar.py       # Pestañas de filtro y acciones por lote
├── tests/
│   ├── test_comparator.py      # Pruebas de comparación acústica
│   ├── test_quality.py         # Pruebas de detección de falsos FLAC
│   ├── test_clustering.py      # Pruebas de agrupación
│   └── test_end_to_end.py      # Suite integral con síntesis de audio
├── main.py                     # Punto de entrada CLI y GUI
├── requirements.txt            # Dependencias Python
└── README.md                   # Documentación técnica
```

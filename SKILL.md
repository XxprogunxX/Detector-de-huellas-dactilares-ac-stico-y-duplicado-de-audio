---
name: audio-duplicate-detector
description: Guía de arquitectura, seguridad, algoritmos, desarrollo y optimización universal de rendimiento para el analizador de duplicados de audio y huellas acústicas.
---

# Skill: Audio Duplicate & Acoustic Fingerprinting Detector

## 1. Propósito

Esta skill define cómo un agente de IA debe comprender, modificar, probar, optimizar y extender este repositorio. El proyecto es una aplicación de escritorio en Python para analizar bibliotecas grandes de música (decenas o cientos de miles de canciones) y detectar:

- duplicados exactos por hash de archivo (SHA-256);
- duplicados exactos del audio PCM aunque cambien etiquetas ID3 o contenedor;
- duplicados acústicos mediante Chromaprint/fpcalc ($\ge 95\%$ de similitud);
- posibles versiones relacionadas mediante comparación acústica/espectral ($80\% - 94.9\%$);
- falsos lossless/transcodes (*Fake FLAC*) mediante análisis FFT;
- el archivo de mejor calidad dentro de un grupo de duplicados (scoring 0–100);
- operaciones seguras para mover o eliminar archivos (protección contra borrado accidental).

**Prioridad rectora:** Preservar la seguridad absoluta de los archivos del usuario, la precisión de detección (cero falsos positivos) y garantizar un **rendimiento altamente optimizado y fluido en cualquier tipo de computadora** (desde equipos de gama baja / *low-end* con recursos limitados hasta estaciones de trabajo multi-núcleo).

---

## 2. Arquitectura Actual del Proyecto

El sistema está modularizado con estricta separación de responsabilidades:

```text
main.py
├── core/
│   ├── models.py               # Modelos de datos (AudioTrack, DuplicateGroup, EvidenceReport, ScanStats)
│   ├── fingerprint.py          # Hashes SHA-256/PCM, huellas Chromaprint y serialización
│   ├── metadata_extractor.py   # Extracción de metadatos con Mutagen
│   ├── quality_analyzer.py     # Análisis espectral FFT, corte de frecuencia y scoring
│   ├── comparator.py           # Alineamiento temporal, ventana de offset (600 frames) y Hamming
│   ├── clustering.py           # Prefiltro LSH, Union-Find, mitigación has_weak_link y ranking
│   ├── database.py             # Motor de persistencia SQLite WAL de alta velocidad
│   ├── scanner.py              # Orquestador recursivo de escaneo multiproceso y por fases
│   └── file_manager.py         # Operaciones seguras en disco (mover, marcar, eliminar)
├── gui/
│   ├── app.py                  # Ventana principal moderna en PyQt6 y orquestador GUI
│   ├── styles.py               # Paleta de colores, estilos globales QSS y tipografía
│   └── components/
│       ├── ab_comparison.py    # Comparador auditivo A/B side-by-side en tiempo real
│       ├── audio_player.py     # Motor de reproducción de audio con Pygame
│       ├── bottom_player.py    # Barra de reproducción persistente en la parte inferior
│       ├── delete_modal.py     # Modal de confirmación y seguridad para eliminación/backup
│       ├── duplicate_card.py   # Tarjeta interactiva de grupo de duplicados
│       ├── filter_bar.py       # Pestañas de filtrado, búsqueda y ordenación
│       ├── library_view.py     # Vista de gestión completa de biblioteca indexada
│       ├── quality_view.py     # Vista de auditoría espectral y salud de calidad
│       ├── scan_progress.py    # Barra de progreso y estadísticas en tiempo real
│       ├── scanner_view.py     # Vista interactiva de configuración y escaneo
│       ├── settings_view.py    # Vista de configuración de motor y mantenimiento BD
│       ├── sidebar.py          # Barra lateral de navegación con iconos QtAwesome
│       └── stats_bar.py        # Barra superior con estadísticas globales
├── scripts/
│   ├── generate_dataset.py     # Generador de dataset sintético y adversarial de audio
│   └── evaluation_runner.py    # Ejecutor de benchmarks automatizados y métricas
└── tests/
    ├── test_clustering.py      # Pruebas de agrupamiento Union-Find, transitividad y LSH
    ├── test_comparator.py      # Pruebas de comparación acústica, Hamming y offset
    ├── test_database.py        # Pruebas de base de datos SQLite y caché
    ├── test_end_to_end.py      # Suite integral end-to-end con síntesis de audio
    ├── test_file_manager.py    # Pruebas de seguridad de operaciones en disco y permisos
    ├── test_framework.py       # Pruebas del framework de evaluación adversarial
    ├── test_performance.py     # Pruebas de rendimiento y estrés
    └── test_quality.py         # Pruebas de detección de falsos lossless y FFT
```

---

## 3. Principio de Optimización Universal para Cualquier Computadora ⚡

La aplicación **debe funcionar de manera rápida, ligera, estable y sin congelarse en cualquier ordenador**, ya sea:
- Una laptop antigua con 2 o 4 núcleos de CPU, 4 GB de RAM y disco duro mecánico (HDD).
- Una PC moderna de alto rendimiento con 16+ núcleos de CPU, 64 GB de RAM y SSD NVMe.

Para lograrlo, todo agente que modifique el código debe aplicar obligatoriamente los siguientes estándares:

### A. Gestión Eficiente de CPU y Multiprocesamiento Adaptativo
- **Detección y ajuste dinámico de hilos (`psutil` / `os.cpu_count()`):** Nunca saturar el 100% de los núcleos de forma indiscriminada si eso congela el sistema operativo del usuario. El valor por defecto debe ser adaptativo (`max(1, cpu_count - 1)`).
- **Procesamiento por lotes (*Batching / Chunking*):** Enviar trabajos al `ProcessPoolExecutor` o cola de multiprocessing en fragmentos razonables (ej. 25 a 100 archivos por lote) para minimizar la sobrecarga de serialización IPC (*Inter-Process Communication*).
- **Timeouts explícitos en subprocesos:** Toda llamada a `fpcalc.exe` o `ffmpeg` debe tener un `timeout` (ej. 15-30s) para evitar procesos zombies o bloqueos infinitos ante archivos dañados.

### B. Consumo Mínimo de Memoria RAM (Streaming y Zero Leaks)
- **Lectura por bloques (*Streaming I/O*):** Nunca cargar archivos de audio completos (un WAV de 500 MB o FLAC Hi-Res) en memoria para calcular hashes. Utilizar lectura en chunks de $64\text{ KB}$ a $1\text{ MB}$.
- **Análisis FFT acotado:** Para el análisis espectral en `quality_analyzer.py`, analizar únicamente segmentos representativos (ej. 10 a 30 segundos centrales) en lugar de decodificar y transformar horas enteras de audio.
- **Liberación de memoria y recolección de basura:** Limpiar estructuras intermedias y evitar retener listas masivas de buffers PCM sin necesidad.

### C. Optimización de I/O en Disco (Soporte para HDDs lentos y SSDs rápidos)
- **Modo SQLite WAL y transacciones por lote:**
  - Base de datos configurada con `PRAGMA journal_mode=WAL;`, `PRAGMA synchronous=NORMAL;` y `PRAGMA cache_size=-64000;`.
  - Inserciones y actualizaciones agrupadas en una única transacción (`BEGIN TRANSACTION ... COMMIT`) en lugar de hacer un `commit()` por cada canción.
- **Evitar tormentas de I/O aleatorio:** Ordenar el procesamiento y las consultas para minimizar el movimiento de cabezales en discos mecánicos HDD.

### D. Interfaz Gráfica Ligera y Reactiva (PyQt6)
- **Cero bloqueos en el hilo principal:** Toda tarea de escaneo, cálculo pesado, FFT o acceso a disco debe ejecutarse en hilos secundarios (`QThread` / `ScannerWorker`).
- **Paginación y renderizado perezoso (*Lazy Loading / Virtualization*):**
  - Nunca instanciar 10,000 tarjetas de duplicados (`DuplicateGroupCard`) simultáneamente en la GUI; esto consumiría cientos de MBs de RAM y congelaría Qt.
  - Implementar paginación (ej. lotes de 20 a 50 grupos por página) o renderizado bajo demanda al hacer scroll.
- **Throttling de eventos de progreso:** Emitir señales de actualización de progreso con intervalos controlados (ej. cada 100-250 ms o cada $N$ archivos) para no saturar la cola de eventos de PyQt6.

---

## 4. Flujo Conceptual del Sistema

```text
Biblioteca de Música
   ↓
[Fase 1] Descubrimiento rápido de archivos (os.scandir / mtime / size)
   ↓
[Fase 2] Consulta de Caché SQLite (Re-escaneo instantáneo en 0.01 ms si mtime coincide)
   ↓
[Fase 3] Extracción paralela de Metadata, Hashes y Chromaprint (fpcalc)
   ↓
[Fase 4] Detección de Duplicados Exactos (SHA-256 y Audio PCM Hash)
   ↓
[Fase 5] Comparación Acústica de Candidatos (Hamming Distance + Window Alignment)
   ↓
[Fase 6] Agrupamiento Disjunto (Union-Find)
   ↓
[Fase 7] Auditoría Espectral FFT & Scoring de Calidad (0 a 100)
   ↓
[Fase 8] Recomendación Automática [CONSERVAR] / [ELIMINAR]
   ↓
Presentación en GUI (PyQt6) o CLI con Ahorro de Espacio Calculado
   ↓
Acción Segura del Usuario (Mover a Respaldo / Eliminar con Confirmación)
```

---

## 5. Reglas de Detección y Precisión

1. **Duplicado Exacto (100%)**:
   - `EXACT_HASH`: Mismo SHA-256 bit-a-bit en disco.
   - `EXACT_AUDIO`: Mismo hash de flujo PCM decodificado (mismo audio, diferentes tags ID3 o contenedor). Cortocircuito exacto, sin rango porcentual.
2. **Duplicado Acústico ($\ge 95\%$)**:
   - Misma grabación original analizada mediante huellas Chromaprint locales.
   - Tolera variaciones de formato, bitrate (320k vs 128k), compresión y ganancia de volumen.
3. **Posible Duplicado / Versión ($80\% - 94.9\%$)**:
   - Remasters, radio edits, versiones extendidas, pistas en vivo.
   - Se muestran como candidatos para revisión del usuario, nunca como equivalencias exactas.
4. **Revisión Manual de Baja Confianza ($40\% - 79.9\%$)**:
   - Atrapa intencionalmente modificaciones severas (Tempo alterado, EQ extremo, aislamientos vocales simples) que destruyen parcialmente la huella acústica.
   - Estos grupos **siempre** nacen en estado `UNSET` para forzar revisión humana.

### Regla Crítica: Cero Falsos Positivos y Aislamiento Seguro
- **La precisión es sagrada:** Jamás agrupar canciones distintas simplemente porque son del mismo artista, álbum, año, duración similar o género.
- El agrupamiento de Union-Find (`core/clustering.py`) debe mantener componentes conexas estrictas y validar que cada unión cumpla con los umbrales acústicos establecidos.
- Todo grupo que requiera revisión (`LOW_CONFIDENCE_REVIEW`, `POSSIBLE_DUPLICATE`) debe inicializarse con el flag de modelo `requires_manual_review = True` para desactivar permanentemente las políticas masivas de limpieza sobre ellos.

---

## 6. Módulos Críticos y Políticas de Edición

### `core/fingerprint.py`
- Extracción local con `fpcalc.exe`.
- Manejo robusto de errores de decodificación y timeouts.
- Conservar la serialización binaria compacta en SQLite para mantener la base de datos pequeña.

### `core/comparator.py`
- Cálculo ultra-optimizado de distancia de Hamming entre sub-huellas acústicas mediante operaciones a nivel de bit (`popcount` / bitwise).
- Alineamiento temporal con ventana deslizante (*sliding window*) de **600 frames** de tolerancia (`max_offset_frames=600`) para detectar audios desfasados o con silencios introductorios severos sin comprometer artificialmente el score de similitud.
- Implementa un cortafuegos temprano por duración: diferencias >90 segundos devuelven 0% de similitud instantánea para salvaguardar ciclos de CPU.

### `core/clustering.py`
- **Prefiltro LSH de alto rendimiento:** Genera buckets exclusivamente por token de hash (`val`), sin restringir por buckets rígidos de duración para capturar correctamente recortes y ediciones como *Radio Edits*.
- **Umbral estricto `min_hits = 3` y escalabilidad:** Requiere al menos 3 coincidencias de tokens entre pares para suprimir falsos positivos. Soporta mega-clústeres mediante `max_bucket_size = 500` con deduplicación de pares candidatos (`candidate_pairs_set`) para evitar cuellos de botella $O(N^2)$.
- **Mitigación de Transitividad Insegura (`has_weak_link`):** Si cualquier par conexo dentro de un componente conexo cae en `POSSIBLE_DUPLICATE` o `LOW_CONFIDENCE_REVIEW`, el grupo completo activa `requires_manual_review = True`, degrada su clasificación y blinda a todas las pistas integrantes contra borrados automáticos.

### `core/quality_analyzer.py`
- Análisis FFT eficiente calculando el *spectral rolloff* en el percentil 99% de energía.
- Detección de Falsos Lossless (corte $<16\text{ kHz}$ o $<20\text{ kHz}$ en archivos `.flac` o `.wav`).
- Fórmula de scoring transparente y equilibrada (fidelidad, bitrate, sample rate, bit depth, duración).

### `core/database.py`
- Persistencia SQLite con esquema indexado (`filepath`, `sha256`, `audio_hash`, `mtime`).
- Soporte para migraciones seguras y métodos de optimización (`VACUUM`, `clear_cache`).

### `core/file_manager.py` (Zona de Máximo Riesgo ⚠️)
- **Aislamiento por `requires_manual_review`:** Todo agrupamiento automático (`auto_apply_recommendations`) DEBE verificar el estado de este flag en el modelo y ejecutar un `continue` preventivo si es True.
- **Nunca eliminar archivos sin confirmación explícita.**
- **Prohibido borrar pistas marcadas como `CONSERVAR` / `KEEP`.**
- **Impedir eliminar todas las copias de un grupo:** siempre debe preservarse al menos una copia intacta.
- Priorizar el traslado a carpetas de respaldo antes que la eliminación permanente.
- Validar rutas contra ataques de *path traversal* o cadenas vacías.

### `gui/components/bottom_player.py` & `gui/components/audio_player.py`
- **Barra de Preescucha de Alto Contraste:** Estilo deck moderno con controles circulares de transporte ($\pm 10\text{s}$, botón central circular en cian brillante `#00E5FF`).
- **Waveform Interactivo con Scrubbing Libre:** Visualizador de barras estilo ecualizador donde el progreso transcurrido brilla en cian y el restante se atenúa en pizarra oscuro. Permite **hacer clic o arrastrar con el ratón** en cualquier punto de la barra para saltar (*seek/scrubbing*) instantáneamente a ese segundo exacto de la canción.
- **Ficha Técnica en Vivo:** Extracción automática de formato, bitrate, frecuencia de muestreo y canales (`FLAC | 24-bit | 96 kHz | Stereo` o `MP3 | 320 kbps | 44.1 kHz | Stereo`).
- **Sincronización Realtime:** `AudioPlayer` emite señales de posición a 100 ms para actualizar simultáneamente el reloj actual (`01:42`), duración total (`06:23`) y las barras del waveform sin interrupciones.

### `gui/app.py` & Persistencia de Sesión
- **Persistencia Segura de Sesión (`last_session.json`):** Almacena la última carpeta activa y los grupos de duplicados detectados. Al arrancar la aplicación, `set_active_folder` se ejecuta con `save_session=False` para impedir sobreescrituras accidentales con listas vacías.
- **Reconstrucción Instantánea desde Caché SQLite (Zero Re-scan):** Si el archivo de sesión no existiera pero la base de datos `music_fingerprints.db` contiene pistas indexadas para la carpeta seleccionada, la aplicación carga los tracks y huellas de SQLite y ejecuta `cluster_duplicates` directamente en memoria en $<1\text{ segundo}$, restaurando todos los grupos de duplicados sin necesidad de volver a escanear ni re-analizar los miles de archivos del disco.

---

## 7. GUI y Componentes Visuales (PyQt6)

- La interfaz sigue un diseño dark moderno y modularizado.
- No colocar lógica de negocio pesada en las clases visuales de `gui/components/`.
- Mantener la separación de responsabilidades:
  - `LibraryView`: Exploración, filtrado y búsqueda.
  - `ScannerView`: Configuración y monitoreo de escaneo en vivo.
  - `DuplicatesView` & `DuplicateGroupCard`: Gestión de grupos y comparación.
  - `QualityView`: Auditoría de calidad y salud espectral.
  - `SettingsView`: Preferencias de motor y base de datos.
  - `ABComparisonDialog`: Comparador auditivo A/B instantáneo.
  - `BottomPlayerBar`: Reproductor global interactivo con waveform scrubbing.
  - `DeleteModal`: Diálogo de seguridad con protección de borrado.

---

## 8. CLI y Automatización

El punto de entrada `main.py` debe mantener total compatibilidad con:
- `python main.py` (GUI interactiva).
- `python main.py --folder <ruta>` (GUI con carpeta precargada).
- `python main.py --cli --folder <ruta>` (Modo consola con tablas Rich).
- `python main.py --cli --folder <ruta> --export-csv <archivo.csv>` (Exportación de datos).
- `python main.py --cli --folder <ruta> --auto-move <carpeta>` (Mover duplicados inferiores).
- `python main.py --cli --folder <ruta> --auto-move <carpeta> --dry-run` (Simulación segura).

---

## 9. Pruebas Obligatorias y Validación

Ejecutar la suite completa tras cualquier cambio sustancial:

```bash
python -m unittest discover tests
```

### Directrices de pruebas:
- **Audio sintético en memoria:** En pruebas unitarias (`test_comparator.py`, `test_quality.py`, `test_clustering.py`), generar tonos sinusoidales puros y barridos de frecuencia en memoria con `numpy` para que los tests se ejecuten en pocos milisegundos sin depender de archivos de disco pesados.
- **Pruebas de no-regresión:** Asegurar que ninguna optimización rompa los casos límite (copias exactas, tags alterados, bitrates variados, falsos lossless y canciones distintas).

---

## 10. Checklist de Calidad y Rendimiento para el Agente

Antes de dar por completada cualquier tarea, verificar:
- [ ] ¿El cambio está optimizado para funcionar con fluidez en computadoras de bajos recursos?
- [ ] ¿Se evita el consumo excesivo de memoria RAM mediante streaming o procesamiento en lotes?
- [ ] ¿Las operaciones intensivas de CPU y disco están fuera del hilo principal de la GUI?
- [ ] ¿Se preserva la regla de **cero falsos positivos**?
- [ ] ¿Se garantiza la seguridad de los archivos del usuario en `file_manager.py`?
- [ ] ¿Se mantiene el rendimiento de la caché SQLite WAL?
- [ ] ¿Pasan todas las pruebas de `tests/`?
- [ ] ¿La documentación (`README.md` / `SKILL.md`) refleja fielmente el comportamiento del código?

---

## 11. Principio Rector

> **Seguridad de Archivos > Precisión de Detección > Optimización Universal de Rendimiento > Estabilidad > Nuevas Funcionalidades.**

# 🎵 Audio Duplicate & Acoustic Fingerprinting Detector

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PyQt6-green?logo=qt&logoColor=white)](https://www.riverbankcomputing.com/software/pyqt/)
[![Acoustic Engine](https://img.shields.io/badge/Engine-Chromaprint%20%2F%20fpcalc-orange)](https://acoustid.org/chromaprint)
[![Database](https://img.shields.io/badge/Storage-SQLite%20WAL-blueviolet?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](#)
[![License](https://img.shields.io/badge/License-MIT-brightgreen.svg)](LICENSE)

Una aplicación de escritorio moderna, robusta y de alto rendimiento en Python diseñada para analizar grandes colecciones de música (decenas o cientos de miles de canciones) y detectar duplicados exactos, copias acústicas y variantes musicales (remasters, radio edits, directos), utilizando **huellas acústicas locales (Chromaprint / fpcalc)**, **análisis espectral FFT** para detectar *Fake Lossless* y **hashes de audio PCM**, todo de forma 100% local y sin depender de servicios externos.

---

## 📑 Tabla de Contenidos

1. [Características Principales](#-características-principales)
2. [Arquitectura e Interfaz Gráfica (PyQt6)](#-arquitectura-e-interfaz-gráfica)
3. [Algoritmo de Detección y Calidad](#-algoritmo-de-detección-y-calidad)
4. [Instalación y Requisitos](#-instalación-y-requisitos)
5. [Guía de Uso](#-guía-de-uso)
   - [Modo Interfaz Gráfica (GUI)](#modo-interfaz-gráfica-gui)
   - [Modo Consola / Headless (CLI)](#modo-consola--headless-cli)
6. [Compilación a Ejecutable Autónomo (.exe)](#-compilación-a-ejecutable-autónomo-exe)
7. [Suite de Pruebas Automatizadas](#-suite-de-pruebas-automatizadas)
8. [Estructura del Proyecto](#-estructura-del-proyecto)
9. [Licencia](#-licencia)

---

## 🌟 Características Principales

### 1. 🎧 Escucha antes de Decidir (Reproducción & Comparador A/B en Vivo)
- **Reproductor Integrado Persistente**: Barra de reproducción inferior con motor de audio Pygame para preescuchar cualquier pista de tu biblioteca de inmediato.
- **Comparador A/B Side-by-Side con Playhead Sincronizado**: Conmuta al milisegundo exacto entre dos pistas manteniendo la misma posición temporal de reproducción, permitiendo identificar al instante diferencias auditivas sutiles de compresión, ecualización, dinamismo o cortes.
- **Control Total en Manos del Usuario**: El sistema analiza, clasifica con rigor matemático y recomienda la mejor opción, pero **la decisión final de conservar, mover o eliminar siempre permanece bajo el control del usuario**. Nunca se realiza un borrado sin confirmación explícita.

### 2. 🔍 Detección de Duplicados en 4 Niveles Jerárquicos
- **Duplicados Exactos (100%)**:
  - *Bit-a-bit en disco (`EXACT_HASH`)*: Coincidencia instantánea SHA-256 de archivos físicamente idénticos.
  - *Flujo de audio PCM decodificado (`EXACT_AUDIO`)*: Cortocircuito exacto por hash de audio crudo decodificado, identificando canciones idénticas con distintos formatos (`.flac` vs `.wav` vs `.mp3`) o metadatos ID3 modificados.
- **Duplicados Acústicos ($\ge 95\%$, `ACOUSTIC_DUPLICATE`)**:
  - Huellas acústicas Chromaprint (`fpcalc`) con comparación Hamming bitwise y ventana de alineamiento temporal dinámico de hasta **600 frames**.
  - Identifica la misma grabación original sin importar variaciones de formato (`MP3`, `FLAC`, `WAV`, `M4A`, `OGG`, `AAC`), tasa de bits (320 kbps vs 128 kbps), normalización de volumen o compresión.
- **Posibles Duplicados / Versiones ($80\% - 94.9\%$, `POSSIBLE_DUPLICATE`)**:
  - Detecta remasterizaciones, radio edits, versiones extendidas o grabaciones en vivo que comparten la misma base armónica.
- **Revisión Manual de Baja Confianza ($40\% - 79.9\%$, `LOW_CONFIDENCE_REVIEW`)**:
  - Aísla intencionalmente modificaciones acústicas severas (alteraciones de tempo, inversión de fase, ecualizaciones extremas).
  - Los grupos en esta franja nacen **siempre protegidos** contra auto-eliminación (`requires_manual_review = True`), forzando la intervención humana.

### 3. 🛡️ Seguridad Blindada y Prevención contra Pérdida de Datos
- **Mitigación de Transitividad Insegura (`has_weak_link`)**: Si dentro de un clúster una pista intermedia vincula débilmente dos audios distintos, el grupo completo se degrada y exige confirmación manual obligatoria.
- **Protección de Copia Única**: El sistema bloquea activamente cualquier intento de eliminar todas las copias de un grupo; siempre se preserva al menos una pista intacta.
- **Inmunidad para Pistas [CONSERVAR]**: Jamás se permite el borrado de archivos marcados para mantenerse.
- **Aislamiento en Limpieza Automática**: El motor `file_manager.py` y el CLI ignoran preventivamente cualquier grupo sin resolución humana explícita.
- **Carpetas de Respaldo y Simulación (*Dry-Run*)**: Opción de mover archivos duplicados a un directorio de backup seguro antes de cualquier borrado definitivo, con capacidad de simular acciones en consola sin tocar el disco.

### 4. 🔬 Auditoría Espectral y Detección de Falsos Lossless (*Fake FLAC*)
- **Análisis FFT de Corte Espectral (*Spectral Rolloff*)**:
  - Detecta automáticamente si un archivo `.flac` o `.wav` fue convertido artificialmente (*upscaled / transcoded*) a partir de un archivo lossy de baja calidad (corte en $\sim 16\text{ kHz}$ para 128 kbps o $\sim 20\text{ kHz}$ para 320 kbps).
- **Visualizador Espectral Gráfico Interactivo**:
  - Gráfico de barras de frecuencia (20 Hz a 22 kHz) que ilustra visualmente el corte real del espectro para cada pista.

### 5. 🏆 Puntuación Técnica de Calidad Inteligente (0 a 100)
Calcula automáticamente qué archivo es el mejor dentro de cada grupo de duplicados evaluando:
- Fidelidad real del formato (Lossless auténtico vs Lossy vs Fake Lossless).
- Tasa de bits real (*bitrate*) y ancho de banda espectral útil.
- Frecuencia de muestreo (44.1 kHz, 48 kHz, 96 kHz / 192 kHz Hi-Res) y profundidad de bits (16-bit vs 24-bit).
- Integridad temporal y duración completa de la pista.
- Asigna recomendaciones automáticas `[CONSERVAR]` / `[ELIMINAR]` con justificación técnica detallada y cálculo de ahorro de espacio en disco.

### 6. ⚡ Rendimiento Extremo y Optimización Universal
- **Prefiltro LSH de Alto Rendimiento**: Indexación por tokens de hash acústico sin dependencia de buckets de duración rígidos, con selectividad estricta (`min_hits = 3`) y escalabilidad comprobada en mega-clústeres (`max_bucket_size = 500`).
- **Caché persistente SQLite con modo WAL (Write-Ahead Logging)**: Re-escaneo instantáneo de archivos inalterados en menos de $0.01\text{ ms}$.
- **Procesamiento paralelo adaptativo**: Decodificación y extracción de huellas acústicas simultánea aprovechando todos los núcleos de CPU disponibles sin congelar el sistema operativo.
- **Diseñado para cualquier equipo**: Optimizado tanto para laptops con discos mecánicos HDD y CPUs modestas como para estaciones de trabajo con SSD NVMe.
- **Control total del escáner**: Capacidad de Pausar, Reanudar y Cancelar el análisis en cualquier momento.

### 7. 📚 Explorador y Gestión Integral de Biblioteca
- Explorador completo de todas las canciones indexadas en la base de datos local.
- Búsqueda instantánea en tiempo real por título, artista, álbum o ruta de archivo.
- Filtros avanzados por calidad, formato y estado de duplicados.
- Exportación de auditoría completa y recomendaciones a formato CSV.

### 8. 🌐 Soporte Multiformato Universal y Modo Dual (GUI / CLI)
- **Formatos compatibles**: `.mp3`, `.flac`, `.wav`, `.m4a`, `.ogg`, `.aac`, `.wma`, `.opus`.
- **Modo Interfaz Gráfica (GUI PyQt6)**: Entorno visual moderno con tema oscuro profesional y feedback en tiempo real.
- **Modo Consola (CLI / Headless)**: Ideal para automatización en servidores, scripts programados y escaneos desatendidos.

---

## 🖥️ Arquitectura e Interfaz Gráfica

La interfaz gráfica está construida sobre **PyQt6** con un sistema de diseño oscuro profesional inspirado en herramientas de audio modernas:

```
┌─────────────────┬────────────────────────────────────────────────────────┐
│  AUDIO CLEANER  │  [Top Stats Bar: Pistas, Duplicados, Ahorro Potencial] │
├─────────────────┼────────────────────────────────────────────────────────┤
│ 📚 Biblioteca   │                                                        │
│ ⚡ Escaneo      │                     VISTA ACTIVA                       │
│ 👥 Duplicados   │   (Biblioteca / Escáner / Duplicados / Calidad / Config)│
│ 🔬 Calidad      │                                                        │
│ ⚙️ Ajustes      │                                                        │
├─────────────────┴────────────────────────────────────────────────────────┤
│ 🎵 [Bottom Player Bar]: Carátula | Info Pista | Barra Progreso | Volumen  │
└──────────────────────────────────────────────────────────────────────────┘
```

### Módulos Principales de la GUI:
1. **📚 Biblioteca (`LibraryView`)**:
   - Explorador de todas las canciones indexadas en la base de datos local.
   - Búsqueda en tiempo real por título, artista, álbum o ruta.
   - Filtros por calidad y formato, ordenación múltiple y exportación de datos a CSV.
2. **⚡ Escaneo (`ScannerView`)**:
   - Selector de directorios con soporte para arrastrar y soltar (*drag-and-drop*).
   - Selector de extensiones de audio y configuración de hilos de trabajo.
   - Indicadores en vivo de velocidad (archivos/seg), fase actual, tiempo transcurrido, uso de CPU y RAM.
3. **👥 Duplicados (`DuplicatesView`)**:
   - Tarjetas interactivas por grupo de duplicados con porcentaje de similitud y badge de tipo.
   - Tabla comparativa técnica completa por pista (Formato, Bitrate, Duración, Tamaño, Calidad).
   - Acciones individuales y por lote: abrir en el Explorador de Windows, reproducir, cambiar acción `CONSERVAR`/`ELIMINAR` y lanzar comparador A/B.
4. **🔬 Calidad (`QualityView`)**:
   - Auditoría integral de la salud acústica de la biblioteca.
   - Métricas de Lossless reales vs Falsos Lossless detectados.
   - Visualización de cortes de frecuencia y distribución de tasas de bits.
5. **⚙️ Configuración (`SettingsView`)**:
   - Ajuste de umbrales de similitud acústica ($\ge 95\%$, etc.) y distancia Hamming.
   - Configuración de hilos de CPU y tamaño de bloques FFT.
   - Mantenimiento de base de datos SQLite (Optimización `VACUUM`, limpieza de huellas y reinicio seguro).
   - Definición de carpeta de respaldo para movimientos seguros de duplicados.
6. **🛡️ Modal de Eliminación Segura (`DeleteModal`)**:
   - Protección estricta: Impide eliminar todas las copias de un grupo o borrar pistas marcadas como `CONSERVAR`.
   - Soporte para mover a carpeta de respaldo o eliminación permanente controlada.

---

## 🧮 Algoritmo de Detección y Calidad

### Proceso de Análisis por Fases:
```mermaid
flowchart TD
    A[Inicio: Directorio de Música] --> B[Fase 1: Extracción Rápida de Metadatos & SHA-256]
    B --> C{¿En Caché SQLite?}
    C -- Sí --> D[Recuperar Huella & Calidad Instantáneamente]
    C -- No --> E[Fase 2: Extracción Chromaprint fpcalc & Análisis FFT]
    E --> F[Guardar en SQLite WAL]
    D --> G[Fase 3: Cortocircuito Duplicados Exactos SHA-256 / Audio PCM Hash]
    F --> G
    G --> H[Fase 4: Prefiltro LSH & Comparación Acústica Hamming / Offset]
    H --> I[Fase 5: Agrupamiento Union-Find con Mitigación de Transitividad]
    I --> J[Fase 6: Ranking de Calidad & Asignación de Acciones Seguras]
    J --> K[Resultados Listos para GUI / CLI]
```

### Fórmula de Puntuación de Calidad:
$$\text{Score} = w_{\text{fidelity}} + w_{\text{bitrate}} + w_{\text{samplerate}} + w_{\text{depth}} + w_{\text{integrity}}$$
- **Fidelidad**: FLAC/WAV auténtico = $+40\text{ pts}$, MP3/AAC 320k = $+25\text{ pts}$, Falso FLAC = penalización proporcional al corte real.
- **Bitrate**: Hasta $+30\text{ pts}$ en función del ancho de banda y bitrate efectivo.
- **Frecuencia de Muestreo**: $44.1\text{ kHz}$ ($+10\text{ pts}$), $48\text{ kHz}$ ($+12\text{ pts}$), $96\text{ kHz}+ \text{ Hi-Res}$ ($+15\text{ pts}$).
- **Profundidad de Bits**: 24-bit ($+10\text{ pts}$), 16-bit ($+5\text{ pts}$).

---

## 🛠️ Instalación y Requisitos

### Requisitos del Sistema
- **Sistema Operativo**: Windows 10 / 11, Linux o macOS.
- **Python**: 3.10 o superior.
- **FFmpeg**: Instalado y disponible en el `PATH` del sistema.
- **fpcalc**: Binario autónomo de Chromaprint (incluido en `bin/fpcalc.exe` para Windows).

### Instalación de Dependencias
Clona el repositorio e instala las dependencias requeridas en tu entorno virtual:

```bash
git clone https://github.com/XxprogunxX/Detector-de-huellas-dactilares-ac-stico-y-duplicado-de-audio.git
cd Detector-de-huellas-dactilares-ac-stico-y-duplicado-de-audio

python -m venv venv
# En Windows:
venv\Scripts\activate
# En Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

---

## 🚀 Guía de Uso

### Modo Interfaz Gráfica (GUI)
Para iniciar la interfaz gráfica completa:
```bash
python main.py
```
O iniciando directamente el escaneo de una carpeta específica:
```bash
python main.py --folder "D:\MiMusica"
```

---

### Modo Consola / Headless (CLI)
Ideal para servidores, scripts programados o escaneos automáticos desatendidos:

#### 1. Escaneo básico con reporte enriquecido en consola:
```bash
python main.py --cli --folder "D:\MiMusica"
```

#### 2. Escanear y exportar reporte detallado a CSV:
```bash
python main.py --cli --folder "D:\MiMusica" --export-csv "reporte_duplicados.csv"
```

#### 3. Simulación de movimiento (*Dry-Run*):
```bash
python main.py --cli --folder "D:\MiMusica" --auto-move "D:\Backup_Duplicados" --dry-run
```

#### 4. Escanear y mover duplicados inferiores automáticamente a una carpeta de respaldo:
```bash
python main.py --cli --folder "D:\MiMusica" --auto-move "D:\Backup_Duplicados"
```

#### Parámetros disponibles en CLI:
| Parámetro | Abreviatura | Descripción |
| :--- | :--- | :--- |
| `--folder` | `-f` | Ruta de la carpeta de música a analizar (Obligatorio en CLI). |
| `--cli` | | Ejecuta en modo headless / consola sin interfaz gráfica. |
| `--db` | | Ruta personalizada para el archivo de base de datos SQLite. |
| `--export-csv` | | Exporta los grupos de duplicados y recomendaciones a un archivo CSV. |
| `--auto-move` | | Mueve automáticamente los archivos duplicados inferiores a la carpeta indicada. |
| `--dry-run` | | Muestra las acciones planificadas sin realizar modificaciones en disco. |

---

## 📦 Compilación a Ejecutable Autónomo (.exe)

El proyecto incluye un script de compilación y un archivo de configuración de **PyInstaller** optimizado (`build_installer.spec` y `build_exe.bat`) que empaqueta todas las dependencias, binarios de `fpcalc.exe` e iconos en un único archivo ejecutable:

### Pasos para compilar:
1. En Windows, ejecuta el script por lotes:
   ```cmd
   build_exe.bat
   ```
2. O manualmente mediante PyInstaller:
   ```bash
   pip install pyinstaller
   pyinstaller --clean build_installer.spec
   ```
3. El ejecutable standalone se generará en:
   ```
   dist/AudioDuplicateDetector.exe
   ```
Este `.exe` es completamente autónomo y puede distribuirse en cualquier PC con Windows sin necesidad de tener Python instalado.

---

## 🧪 Suite de Pruebas Automatizadas

El proyecto cuenta con una suite exhaustiva de 32 pruebas unitarias e integración que valida de extremo a extremo la integridad matemática y funcional del sistema:

```bash
python -m unittest discover tests
```

### Escenarios cubiertos por las pruebas:
- ✔️ Copias idénticas bit-a-bit en disco (`EXACT_HASH`).
- ✔️ Mismo audio con diferentes etiquetas ID3, metadatos y contenedores (`EXACT_AUDIO`).
- ✔️ Mismo audio convertido entre distintos formatos (`MP3`, `FLAC`, `WAV`, `OGG`, `M4A`).
- ✔️ Mismo audio a diferentes tasas de bits ($320\text{ kbps}$ vs $128\text{ kbps}$) y canales (Stereo a Mono).
- ✔️ Detección de Falsos Lossless (*Fake FLAC* inflado desde MP3 de $128\text{ kbps}$).
- ✔️ Remasterizaciones y variaciones de ganancia/ecualización.
- ✔️ Tolerancia a desalineación temporal y cortes de inicio (hasta 600 frames).
- ✔️ Pistas con diferencias extremas de duración (Radio Edits y versiones extendidas).
- ✔️ Mitigación de transitividad insegura (`has_weak_link`) y protección de borrado en clústeres heterogéneos.
- ✔️ Escalabilidad del prefiltro LSH en mega-clústeres de 60+ duplicados idénticos.
- ✔️ Operaciones atómicas de gestión de archivos (`FileManager`) e integridad ante errores de permisos.
- ✔️ Verificación estricta de **cero falsos positivos** entre canciones distintas del mismo artista.
- ✔️ Rendimiento, persistencia y atomicidad de la base de datos SQLite WAL.

---

## 📂 Estructura del Proyecto

```
Detector-de-huellas-dactilares-ac-stico-y-duplicado-de-audio/
├── bin/
│   └── fpcalc.exe              # Binario Chromaprint de alta velocidad
├── core/
│   ├── __init__.py
│   ├── models.py               # Modelos de datos (AudioTrack, DuplicateGroup, EvidenceReport)
│   ├── fingerprint.py          # Extracción Chromaprint, hashes PCM y serialización
│   ├── metadata_extractor.py   # Extracción de metadatos con Mutagen
│   ├── quality_analyzer.py     # Análisis espectral FFT, Fake Lossless y scoring 0-100
│   ├── comparator.py           # Alineamiento temporal, Hamming y ventana de offset
│   ├── clustering.py           # Prefiltro LSH, Union-Find y mitigación has_weak_link
│   ├── database.py             # Motor de persistencia SQLite con modo WAL
│   ├── scanner.py              # Orquestador recursivo de escaneo multiproceso
│   └── file_manager.py         # Operaciones seguras en disco y protección contra borrado
├── gui/
│   ├── __init__.py
│   ├── app.py                  # Ventana principal moderna en PyQt6 y orquestador GUI
│   ├── styles.py               # Paleta de colores, estilos QSS y tipografía
│   └── components/
│       ├── ab_comparison.py    # Comparador auditivo A/B side-by-side en tiempo real
│       ├── audio_player.py     # Motor de reproducción de audio con Pygame
│       ├── bottom_player.py    # Barra de reproducción persistente en la parte inferior
│       ├── delete_modal.py     # Modal de confirmación y seguridad para eliminación/backup
│       ├── duplicate_card.py   # Tarjeta interactiva de visualización de grupo de duplicados
│       ├── filter_bar.py       # Pestañas de filtrado, búsqueda y ordenación
│       ├── library_view.py     # Vista de gestión completa de biblioteca indexada
│       ├── quality_view.py     # Vista de auditoría espectral y salud de calidad de audio
│       ├── scan_progress.py    # Barra de progreso y estadísticas en tiempo real
│       ├── scanner_view.py     # Vista interactiva de configuración y ejecución de escaneo
│       ├── settings_view.py    # Vista de configuración de motor y mantenimiento de BD
│       ├── sidebar.py          # Barra lateral de navegación con iconos QtAwesome
│       └── stats_bar.py        # Barra superior con estadísticas globales
├── scripts/
│   ├── generate_dataset.py     # Generador de dataset de audio sintético y adversarial
│   └── evaluation_runner.py    # Ejecutor de benchmarks automatizados y métricas
├── tests/
│   ├── __init__.py
│   ├── test_clustering.py      # Pruebas de agrupamiento, transitividad y escalabilidad LSH
│   ├── test_comparator.py      # Pruebas de comparación acústica, Hamming y offset
│   ├── test_database.py        # Pruebas de base de datos SQLite y caché
│   ├── test_end_to_end.py      # Suite integral end-to-end con síntesis de audio
│   ├── test_file_manager.py    # Pruebas de seguridad de operaciones en disco y permisos
│   ├── test_framework.py       # Pruebas del marco de evaluación adversarial
│   ├── test_performance.py     # Pruebas de rendimiento y estrés
│   └── test_quality.py         # Pruebas de detección de falsos lossless y FFT
├── app_icon.ico                # Icono de la aplicación en formato ICO
├── app_icon.png                # Icono de la aplicación en formato PNG
├── build_exe.bat               # Script automatizado para compilar el .exe con PyInstaller
├── build_installer.spec        # Especificación técnica de empaquetado de PyInstaller
├── main.py                     # Punto de entrada principal (CLI / GUI)
├── requirements.txt            # Dependencias del proyecto
├── walkthrough.md              # Documento técnico consolidado de auditorías y benchmarks
└── README.md                   # Documentación técnica completa
```

---

## 📄 Licencia

Este proyecto está bajo la Licencia MIT. Para más información, consulta el archivo `LICENSE`.

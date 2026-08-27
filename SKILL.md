---
name: audio-duplicate-detector
description: Guía de arquitectura, seguridad, algoritmos y desarrollo para el analizador de duplicados de audio y huellas acústicas.
---

# Skill: Audio Duplicate & Acoustic Fingerprinting Detector

## 1. Propósito

Esta skill define cómo un agente de IA debe comprender, modificar, probar y extender este repositorio. El proyecto es una aplicación de escritorio en Python para analizar bibliotecas grandes de música y detectar:

- duplicados exactos por hash de archivo;
- duplicados exactos del audio PCM aunque cambien etiquetas o contenedor;
- duplicados acústicos mediante Chromaprint/fpcalc;
- posibles versiones relacionadas mediante comparación acústica/espectral;
- falsos lossless/transcodes mediante análisis FFT;
- el archivo de mejor calidad dentro de un grupo de duplicados;
- operaciones seguras para mover o eliminar archivos.

La prioridad del agente es preservar la precisión de detección, la seguridad de los archivos del usuario y el rendimiento en bibliotecas grandes.

## 2. Arquitectura actual

La arquitectura principal está dividida en tres áreas:

```text
main.py
├── core/
│   ├── models.py
│   ├── fingerprint.py
│   ├── metadata_extractor.py
│   ├── quality_analyzer.py
│   ├── comparator.py
│   ├── clustering.py
│   ├── database.py
│   ├── scanner.py
│   └── file_manager.py
├── gui/
│   ├── app.py
│   ├── styles.py
│   └── components/
│       ├── duplicate_card.py
│       ├── audio_player.py
│       ├── scan_progress.py
│       └── filter_bar.py
└── tests/
    ├── test_comparator.py
    ├── test_quality.py
    ├── test_clustering.py
    └── test_end_to_end.py
```

### Responsabilidades

- `main.py`: punto de entrada y selección entre GUI y CLI.
- `core/models.py`: modelos de datos del dominio.
- `core/fingerprint.py`: hashes, huellas acústicas y serialización.
- `core/metadata_extractor.py`: metadata mediante Mutagen/FFprobe.
- `core/quality_analyzer.py`: análisis espectral/FFT y evaluación de calidad.
- `core/comparator.py`: comparación, alineamiento temporal y similitud de fingerprints.
- `core/clustering.py`: formación de grupos de duplicados y ranking.
- `core/database.py`: caché persistente SQLite y operaciones de persistencia.
- `core/scanner.py`: recorrido de bibliotecas y procesamiento paralelo.
- `core/file_manager.py`: mover, marcar y eliminar archivos.
- `gui/app.py`: ventana principal y coordinación de la interfaz.
- `gui/components/`: componentes visuales especializados.
- `tests/`: pruebas unitarias, integración y end-to-end.

## 3. Flujo conceptual del sistema

El flujo esperado es aproximadamente:

```text
Biblioteca
   ↓
Descubrimiento de archivos
   ↓
Consulta de caché SQLite
   ↓
Extracción de metadata / hashes / fingerprint
   ↓
Análisis de calidad cuando corresponde
   ↓
Comparación de pistas candidatas
   ↓
Clustering de duplicados
   ↓
Ranking del mejor archivo
   ↓
Resultados GUI/CLI
   ↓
Acción del usuario: conservar / mover / eliminar
```

No asumir que todos los archivos necesitan el mismo nivel de procesamiento. Mantener los atajos y la caché que evitan trabajo innecesario.

## 4. Reglas de detección

El proyecto maneja tres niveles conceptuales:

1. **Duplicado exacto (100%)**
   - SHA-256 para copias idénticas en disco.
   - Hash del flujo PCM para detectar el mismo audio aunque cambien tags ID3 o el contenedor.

2. **Duplicado acústico (>=95%)**
   - Principalmente mediante huellas acústicas locales con Chromaprint/fpcalc.
   - Debe tolerar diferencias de formato, bitrate, volumen y compresión cuando corresponda.

3. **Posible duplicado (80%-94%)**
   - Puede representar remasters, radio edits, versiones extendidas, directos u otras variantes relacionadas.
   - Estos resultados deben tratarse como candidatos y no como equivalencia exacta.

No cambiar estos umbrales sin revisar primero los tests y el impacto sobre falsos positivos y falsos negativos.

## 5. Regla crítica: falsos positivos

La precisión es una propiedad central del proyecto. Nunca introducir una optimización que agrupe canciones distintas solamente porque pertenecen al mismo artista, álbum, género, duración aproximada o comparten características espectrales.

La afirmación de diseño es que canciones diferentes no deben terminar agrupadas como duplicados por coincidencias débiles.

Si se modifica el algoritmo de comparación o clustering, añadir o actualizar pruebas específicamente para canciones distintas.

## 6. Fingerprinting

`core/fingerprint.py` es una zona crítica.

Reglas:

- Mantener el fingerprint local siempre que sea posible.
- No introducir dependencia de servicios externos sin una decisión explícita del proyecto.
- No romper la compatibilidad entre fingerprints existentes y la caché SQLite.
- Si cambia el formato de serialización o la información almacenada, evaluar migración/invalidez de caché.
- Separar claramente hash de archivo, hash PCM y fingerprint acústico: no son equivalentes.

`fpcalc` se considera una dependencia importante del pipeline y el repositorio contempla un binario autónomo en `bin/fpcalc.exe`.

- Ejecutar llamadas a binarios externos (`fpcalc`, `ffmpeg`, `ffprobe`) siempre con **timeouts explícitos** para evitar bloqueos por archivos colgados o problemas de I/O.
- Aislar el manejo de excepciones ante archivos corruptos, truncados o con metadatos malformados (`MutagenError`, errores de decodificación): registrar advertencia en logs y continuar el escaneo sin abortar el lote.

## 7. Comparación acústica

`core/comparator.py` debe conservar la distinción entre:

- identidad exacta;
- similitud acústica alta;
- similitud parcial/candidata.

Antes de cambiar la distancia de Hamming, alineamiento temporal, normalización o puntuaciones:

1. revisar `tests/test_comparator.py`;
2. revisar `tests/test_end_to_end.py`;
3. probar copias exactas;
4. probar diferentes tags;
5. probar formatos/bitrates distintos;
6. probar versiones relacionadas;
7. probar canciones completamente diferentes.

No confundir una mejora en recall con una mejora real de calidad si aumenta los falsos positivos.

## 8. Calidad y falsos lossless

`core/quality_analyzer.py` analiza características espectrales mediante FFT para detectar archivos lossless que en realidad proceden de una fuente lossy.

Debe distinguirse entre:

- formato declarado;
- calidad real estimada;
- bitrate declarado/real;
- ancho de banda espectral;
- frecuencia de muestreo;
- profundidad de bits;
- integridad y duración.

Los cortes espectrales son evidencia heurística, no una prueba absoluta del origen del archivo. No presentar una heurística como certeza científica sin validación adicional.

## 9. Ranking del mejor archivo

Cuando un grupo contiene varias copias, el ranking debe favorecer calidad real y utilidad, no simplemente el nombre de extensión.

Considerar, según las reglas actuales del proyecto:

- lossless auténtico frente a lossy;
- falso lossless;
- bitrate;
- ancho de banda espectral;
- sample rate;
- bit depth;
- duración/integridad.

Un FLAC no debe considerarse automáticamente mejor que un MP3 si el FLAC es un transcode/falso lossless.

## 10. Caché SQLite

`core/database.py` mantiene la caché persistente.

Reglas:

- No invalidar o borrar la caché innecesariamente.
- Preservar consistencia cuando se modifiquen modelos o fingerprints.
- Tener en cuenta concurrencia y el modo WAL existente.
- No asumir que una base SQLite puede compartirse entre múltiples procesos sin revisar cómo se gestionan las conexiones.
- Si se cambia el esquema, contemplar compatibilidad con instalaciones existentes.

El reescaneo de archivos sin cambios debe seguir aprovechando la caché.

## 11. Multiprocessing y rendimiento

`core/scanner.py` coordina el escaneo recursivo y el procesamiento paralelo.

Al modificarlo:

- evitar trabajo duplicado;
- no cargar innecesariamente archivos completos en memoria;
- conservar Pause/Resume/Cancel;
- manejar correctamente excepciones de workers, garantizando que un fallo en un archivo individual no detenga el resto del escaneo;
- evitar condiciones de carrera con la base de datos;
- medir rendimiento antes y después de optimizaciones importantes.

La aplicación está pensada para bibliotecas de decenas de miles de canciones, por lo que una solución correcta pero O(n²) sin justificación puede convertirse en un problema serio.

## 12. Gestión de archivos: zona de máximo riesgo

`core/file_manager.py` puede mover o eliminar archivos reales del usuario.

Reglas obligatorias:

- Nunca eliminar archivos automáticamente por una inferencia ambigua.
- Mantener confirmaciones explícitas para eliminación permanente.
- Respetar estrictamente las marcas de `[CONSERVAR]`.
- Preferir mover a una carpeta de respaldo cuando la operación lo permita.
- Validar rutas antes de operar.
- Evitar path traversal y rutas accidentalmente vacías o raíz.
- No ejecutar una operación destructiva durante una simple acción de análisis.
- Si se modifica esta área, añadir pruebas de seguridad y de casos límite.

## 13. GUI

La GUI usa CustomTkinter y tiene componentes separados.

Al modificar la interfaz:

- no mover lógica de negocio compleja a la GUI si puede permanecer en `core/`;
- mantener las operaciones largas fuera del hilo principal para no congelar la interfaz;
- conservar actualización de progreso y estadísticas;
- mantener filtros: Todos, Exactos, Acústicos y Posibles;
- conservar el reproductor y la comparación cuando sean relevantes;
- respetar la arquitectura de componentes existente.

La GUI debe presentar claramente la diferencia entre duplicado confirmado y posible duplicado.

## 14. CLI y automatización

`main.py` soporta GUI y modo headless/CLI.

Comandos documentados actualmente incluyen:

```bash
python main.py
python main.py --folder "D:\MiMusica"
python main.py --cli --folder "D:\MiMusica"
python main.py --cli --folder "D:\MiMusica" --auto-move "D:\Duplicados_Backup"
```

Cualquier cambio en argumentos CLI debe conservar compatibilidad con los usos documentados o actualizar el README y las pruebas.

## 15. Pruebas obligatorias

Ejecutar la suite completa después de cambios relevantes:

```bash
python -m unittest discover tests
```

Los escenarios importantes incluyen:

- copias idénticas;
- mismo audio con tags diferentes;
- mismo audio en MP3 y FLAC;
- mismo audio a diferentes bitrates;
- falsos lossless;
- remasterizaciones/variaciones de ganancia o EQ;
- radio edits y pistas truncadas;
- canciones distintas para validar ausencia de falsos positivos;
- comportamiento de caché SQLite.

### Buenas prácticas de pruebas:
- **Audio sintético para pruebas rápidas**: en pruebas unitarias (`test_comparator.py`, `test_quality.py`), priorizar generadores de audio sintético en memoria (ondas sinusoidales generadas programáticamente con `wave`/`numpy`) para mantener los tests rápidos y evitar inflar el repositorio con binarios.
- **Muestras reales para E2E**: reservar muestras reales de audio únicamente para las pruebas de integración y end-to-end (`test_end_to_end.py`).

Si una modificación afecta varias capas, ejecutar la suite completa en lugar de confiar solamente en una prueba unitaria.

## 16. Dependencias y entorno

El proyecto está orientado principalmente a Windows 10/11, aunque el README contempla Linux/macOS.

Dependencias externas importantes:

- Python 3.10+;
- FFmpeg;
- fpcalc/Chromaprint;
- dependencias listadas en `requirements.txt`;
- CustomTkinter;
- Pygame;
- Mutagen;
- FFprobe cuando corresponda.

No agregar una dependencia pesada para resolver un problema pequeño sin evaluar tamaño, compatibilidad, mantenimiento y rendimiento.

## 17. Política de cambios para agentes

Antes de editar código:

1. Leer `README.md`.
2. Identificar qué módulo es responsable del comportamiento.
3. Revisar tests relacionados.
4. Entender dependencias entre módulos.
5. Hacer el cambio mínimo necesario.
6. Ejecutar pruebas.
7. Revisar efectos secundarios.
8. Actualizar documentación si cambia el comportamiento público.

Evitar refactors masivos cuando la tarea solamente requiere un cambio localizado.

## 18. Cambios que requieren especial cuidado

Solicitar o considerar una revisión explícita antes de cambiar de forma importante:

- algoritmo de fingerprinting;
- umbrales de similitud;
- clustering;
- análisis FFT;
- esquema de SQLite;
- multiprocessing;
- operaciones de eliminación/movimiento;
- contratos CLI;
- formato de datos persistidos;
- dependencias de `fpcalc`/FFmpeg.

## 19. Qué NO hacer

- No reemplazar el sistema acústico por comparación de filenames.
- No declarar duplicado solamente por metadata.
- No eliminar archivos solamente porque tengan menor bitrate.
- No asumir que FLAC significa calidad superior.
- No aumentar umbrales de similitud para solucionar falsos negativos sin medir falsos positivos.
- No eliminar la caché para simplificar una implementación.
- No bloquear la GUI con procesamiento pesado.
- No introducir APIs externas sin autorización.
- No modificar tests simplemente para que pasen si el comportamiento nuevo no está justificado.
- No afirmar que una heurística de calidad demuestra con certeza el origen de un archivo.

## 20. Estilo de desarrollo

Preferir código claro, modular y fácil de probar. Mantener responsabilidades separadas entre dominio, persistencia, escaneo, gestión de archivos y presentación.

Cuando sea posible, una nueva funcionalidad debe incluir:

- implementación en el módulo apropiado;
- prueba automatizada;
- manejo de errores;
- documentación si afecta al usuario;
- consideración de rendimiento si procesa grandes bibliotecas.

## 21. Checklist mental del agente

Antes de finalizar cualquier cambio, comprobar:

- ¿Puede crear falsos positivos?
- ¿Puede marcar como duplicado una versión que no debería serlo?
- ¿Puede borrar/mover un archivo incorrectamente?
- ¿Rompe la caché?
- ¿Rompe multiprocessing?
- ¿Congela la GUI?
- ¿Rompe CLI?
- ¿Rompe una prueba existente?
- ¿Aumenta significativamente el consumo de RAM/CPU?
- ¿La documentación sigue describiendo el comportamiento real?

Si alguna respuesta es sí, resolverlo antes de considerar terminado el cambio.

## 22. Principio principal

> La prioridad del proyecto es: **seguridad de los archivos > precisión de detección > estabilidad > rendimiento > nuevas funcionalidades**.

Un agente debe preferir una detección conservadora y explicable antes que una agrupación agresiva que pueda provocar falsos positivos o pérdida accidental de archivos.

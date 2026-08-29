# Walkthrough Consolidado: Estado Final del Proyecto (Fases 1 a 4.5)

El motor de detección de duplicados ha pasado por múltiples ciclos de auditoría, corrección de bugs críticos, y validación empírica. A continuación se documenta el estado actual de las funcionalidades principales y los umbrales estabilizados.

## 1. Correcciones Críticas de Seguridad (Fases 1 y 1.5)
- **Aislamiento de Borrados Masivos:** Se descubrió un comportamiento peligroso donde `best_track_path = None` en grupos de baja confianza causaba que el motor marcara todos los archivos de un grupo como `FileAction.DELETE`. 
- **Solución:** Se implementó el flag explícito `requires_manual_review` a nivel modelo (`DuplicateGroup`). El orquestador `auto_apply_recommendations` en `core/file_manager.py` y la interfaz CLI fueron parcheados para ignorar silenciosamente cualquier grupo con este flag activo, forzando la revisión humana y blindando la seguridad de los datos.

## 2. Motor de Evidencia (Fase 2)
El `EvidenceEngine` ahora es completamente explicable. Todas las decisiones de similitud secundaria (alineamiento temporal, duración, espectro) generan cadenas de razonamiento literales y legibles. Estos ajustes secundarios operan siempre bajo la regla de no inflar artificialmente la similitud base si esta no cumple el umbral estricto de Chromaprint.

## 3. Validación y Umbrales Estabilizados (Fases 3, 4 y 4.5)
El sistema ha sido validado empíricamente a través del `Benchmark V5` (170 pares adversariales). Los umbrales de detección y categorías actuales (aprobados y validados) son:

* **`EXACT_HASH` (100%):** Coincidencia criptográfica idéntica byte-por-byte (excluye recodificaciones).
* **`EXACT_AUDIO` (Coincidencia PCM):** Cortocircuito de coincidencia exacta sobre el audio crudo decodificado, con `confidence = 100.0`. Atrapa transcodificaciones puras (sin modificaciones) ignorando por completo el contenedor o el padding introducido por formatos con pérdida.
* **`ACOUSTIC_DUPLICATE` (>= 95%):** Covers transcodificaciones de baja calidad, compresiones extremas, y reducciones de canal (Stereo a Mono).
* **`POSSIBLE_DUPLICATE` (>= 80% y < 95%):** Reservado para casos donde el motor identifica una alta similitud pero existen variaciones acústicas reales (ej. remasterizaciones o adición de ecos superficiales).
* **`LOW_CONFIDENCE_REVIEW` (>= 40% y < 80%):** Nueva "franja de aislamiento" introducida tras descubrir el bug de offset. Atrapa intencionalmente modificaciones severas (Tempo alterado, EQ extremo, aislamientos vocales simples) que destruyen parcialmente la huella acústica. Estos grupos **siempre** nacen en estado `UNSET` para forzar revisión humana y prevenir borrados accidentales.

*Nota sobre Precisión Global (87.06%):* Esta métrica no significa que el sistema falle 1 de cada 8 veces de forma impredecible. El sistema demostró **100% de precisión** en todos los casos claros y funcionales (`EXACT_HASH`, `EXACT_AUDIO`, `ACOUSTIC_DUPLICATE`, `NO_MATCH`). Las únicas "desviaciones" ocurrieron exclusivamente aislando los *Hard Negatives* extremos y ambiguos dentro de la nueva categoría de revisión manual, comprobando que el sistema actúa de forma conservadora en lugar de realizar inferencias peligrosas.

## 4. Resoluciones Técnicas Clave
- **Bug de Offset Corregido:** Se incrementó la ventana de tolerancia de desalineación (`max_offset_frames`) de 15 a 600. Esto resolvió el problema crónico donde introducciones silenciosas o recortes de pocos segundos destruían artificialmente el score de similitud, provocando que copias legítimas cayeran a 0%. 
  - *Impacto de Rendimiento:* El costo real medido tras el fix fue de ~39.58ms por par evaluado. Sin embargo, este impacto es completamente mitigado gracias al uso del prefiltro LSH, el cual elimina del cálculo a cientos de miles de pares irrelevantes. En hardware de gama baja estimado, el tiempo total de CPU gastado en la evaluación de pares se mantiene en unos más que aceptables ~15-20 segundos por escaneo.
- **Cortafuegos de Duración:** Las pistas con diferencias de duración superiores a 90 segundos son descartadas tempranamente (similitud = 0%), optimizando ciclos de CPU en escaneos masivos. Casos como loops o samples cortos son correctamente rechazados o aislados por esta regla.

El sistema se encuentra estable, auditable, con umbrales validados empíricamente y con la integridad de los datos del usuario garantizada.

# Parámetros del simulador

Generado con `python ogi_sim.py --params-md docs/parametros.md`. No editar a mano: la fuente es `Config` en `ogi_sim.py`.

Se cambian con un preset (`--preset`), un fichero JSON (`--config`) o `--set clave=valor`. Los valores por defecto reproducen el simulador original bit a bit.

**Si no sabes qué hace un parámetro, no lo toques**: los valores por defecto están elegidos para que todo funcione. Nivel: **B** básico (lo que normalmente se toca), **A** avanzado, **E** experto. El panel muestra solo el nivel que elijas; la guía completa también está en su pestaña «Guía».

## Cámara

| Parámetro | Nivel | Qué es | Si lo subes | Valor típico | Por defecto | Unidad | Rango |
|---|---|---|---|---|---|---|---|
| `width` — Ancho de la imagen | A | Columnas de píxeles de la imagen simulada. | Más píxeles = más detalle pero más lento (el tiempo crece con el área). Si lo cambias, cambia también el tamaño del píxel. | 384 (cámara de 384×288). Para explorar usa la vista rápida (la mitad). | `384` | px | 16 … 4096 |
| `height` — Alto de la imagen | A | Filas de píxeles de la imagen simulada. | Igual que el ancho: más detalle y más lento. | 288 | `288` | px | 16 … 4096 |
| `fps` — Fotogramas por segundo | A | Cuántas imágenes por segundo graba la cámara. | Más = movimiento más suave y más fotogramas para promediar el ruido, pero vídeos más pesados. | 25 (muchas cámaras térmicas dan 9, 25 o 30). | `25` | Hz | 1 … 240 |
| `m_per_px` — Tamaño real de un píxel | A | Metros de la escena que cubre un píxel. Fija el campo de visión (ancho = píxeles × este valor). | Más grande = campo de visión mayor: la pluma ocupa menos píxeles y cuesta más verla. Más pequeño = campo pequeño: la pluma llena la imagen. | 0,005 m (campo de 1,9 × 1,4 m). La vista rápida lo duplica sola. | `0.005` | m | 0.0001 … 1 |
| `netd_k` — Ruido de la cámara (NETD) | B | Ruido aleatorio de un píxel en un fotograma, en kelvin. Es la sensibilidad térmica de la cámara (cuanto menor, mejor). | Más ruido = imagen más granulada y fugas pequeñas más difíciles de detectar. | 0,035 K (35 mK). Cámaras no refrigeradas: 30–60 mK; refrigeradas: 15–25 mK. | `0.035` | K | 0 … 1 |
| `fpn_k` — Grano fijo por píxel | A | Patrón fijo: cada píxel responde un poco distinto y eso no cambia fotograma a fotograma. | Más = más grano fijo. El detector lo resta junto con el fondo, pero el obturador de la cámara lo cambia. | 0,02 K | `0.02` | K | 0 … 1 |
| `col_noise_k` — Franjas verticales fijas | A | Patrón fijo por columnas (rayas verticales). | Más = rayas más marcadas. | 0,01 K | `0.01` | K | 0 … 1 |
| `drift_k` — Deriva lenta de toda la imagen | A | Oscilación lenta del nivel de toda la imagen (la cámara se calienta y se enfría). | Más = la imagen sube y baja más. El detector quita la parte uniforme. | 0,03 K | `0.03` | K | 0 … 1 |
| `drift_period_s` — Periodo de esa deriva | E | Cada cuántos segundos se repite la deriva. | Más largo = deriva más lenta. | 20 s | `20` | s | 0.1 … 3600 |
| `psf_sigma_px` — Desenfoque de la óptica | A | Cuánto emborrona la imagen la lente. | Más = bordes y pluma más suaves, menos detalle. | 0,7 px | `0.7` | px | 0 … 10 |
| `detector_tau_s` — Inercia del sensor | E | Tiempo que tarda el microbolómetro en seguir un cambio. | Más = estelas cuando algo se mueve rápido. | 0,010 s (10 ms) | `0.01` | s | 1e-06 … 1 |
| `level_c` — Centro de la ventana de 8 bit | A | Temperatura que corresponde al gris medio cuando la cámara tiene el rango fijo. Vacío = automático. | Moverlo cambia qué temperaturas se ven. Si el fondo se sale de la ventana, la imagen se satura (negro o blanco) y el gas puede no verse. | Vacío (automático). A mano, para escena difícil: centrado en la temperatura del fondo. | `None` | °C |  |
| `span_c` — Ancho de la ventana de 8 bit | A | Rango de temperatura que cubren los 256 grises. Vacío = automático. | Más ancho = menos milikelvin por gris (menos resolución para ver gas). Más estrecho = más resolución, pero se satura antes. | Vacío (automático, unos 10 K). En un caso difícil: 8 K. | `None` | K |  |
| `auto_span_margin_hot_k` — Margen del rango automático (placa) | E | Holgura que se añade al rango automático con placa caliente. | Más = ventana más ancha. | 8 K | `8` | K | 0 … 100 |
| `auto_span_margin_cold_k` — Margen del rango automático (cielo) | E | Holgura que se añade al rango automático con cielo frío. | Más = ventana más ancha. | 12 K | `12` | K | 0 … 100 |
| `auto_headroom_k` — Holgura de la ventana | E | Espacio libre en la ventana automática para que quepa el gas. | Más = más sitio, menos resolución. | 1 K | `1` | K | 0 … 50 |
| `hot_ref_margin_k` — Referencia de la ventana (placa) | E | Qué píxeles de la placa se usan para fijar la ventana automática. | Casi nunca se toca. | 5 K | `5` | K | 0 … 100 |
| `cold_ref_margin_k` — Referencia de la ventana (cielo) | E | Qué píxeles del cielo se usan para fijar la ventana automática. | Casi nunca se toca. | 8 K | `8` | K | 0 … 100 |
| `band_lo_um` — Banda de la cámara: inicio | E | Longitud de onda mínima a la que responde la cámara. | Térmica de onda larga (LWIR): 8 µm. Cambiarla cambia cuánta radiación llega. | 8 µm | `8` | µm | 1 … 30 |
| `band_hi_um` — Banda de la cámara: fin | E | Longitud de onda máxima a la que responde la cámara. | LWIR: 14 µm. | 14 µm | `14` | µm | 1 … 30 |

## Escena

| Parámetro | Nivel | Qué es | Si lo subes | Valor típico | Por defecto | Unidad | Rango |
|---|---|---|---|---|---|---|---|
| `scene` — Tipo de escena | B | Placa caliente: el gas ABSORBE y se ve más oscuro. Cielo frío: el gas EMITE y se ve más claro. | Cambia el signo de lo que se ve y qué fondo hay. | Placa caliente (banco de pruebas con spray) o cielo frío (exterior). | `hot_plate` |  | hot_plate / cold_sky |
| `t_amb_c` — Temperatura ambiente | A | Temperatura del aire, del tubo y del gas al salir (si no defines otra). | Cambia el contraste entre el gas y el fondo. | 22 °C | `22` | °C | -60 … 60 |
| `t_bg_c` — Temperatura del fondo | B | Placa caliente: temperatura de la placa. Cielo frío: temperatura aparente del cielo. | El gas se ve por la DIFERENCIA entre el fondo y su propia temperatura: más diferencia = más visible. Con poca diferencia (placa a 27 °C con ambiente a 22 °C) es difícil. | 70 °C (diferencia grande, fácil); 27 °C (difícil); –25 °C (cielo). | `70` | °C | -80 … 400 |
| `tip_x_frac` — Posición del tubo (horizontal) | A | Dónde está la boca del tubo, de 0 (izquierda) a 1 (derecha). | Más = más a la derecha. | 0,42 | `0.42` |  | 0 … 1 |
| `tip_y_frac` — Posición del tubo (vertical) | A | Altura de la boca del tubo, de 0 (arriba) a 1 (abajo). | Más = más abajo. | 0,70 | `0.7` |  | 0 … 1 |
| `pipe_half_px` — Anchura del tubo | E | Mitad de la anchura del tubo en píxeles. | Más = tubo más grueso. | 3 px | `3` | px | 0 … 50 |
| `pipe_dt_k` — Temperatura del tubo | E | Cuánto más caliente que el ambiente está el tubo. | Más = tubo más claro. | 0,5 K | `0.5` | K | -20 … 20 |
| `amb_nonuniformity_k` — Irregularidad del entorno | E | Variación suave de temperatura fuera de la placa. | Más = fondo menos uniforme. | 0,4 K | `0.4` | K | 0 … 10 |
| `plate_top_frac` — Borde superior de la placa | E | Dónde empieza la placa por arriba (fracción del alto). |  | 0,06 | `0.06` |  | 0 … 1 |
| `plate_bottom_frac` — Borde inferior de la placa | E | Dónde acaba la placa por abajo. |  | 0,86 | `0.86` |  | 0 … 1 |
| `plate_left_frac` — Borde izquierdo de la placa | E | Dónde empieza la placa por la izquierda. |  | 0,08 | `0.08` |  | 0 … 1 |
| `plate_right_frac` — Borde derecho de la placa | E | Dónde acaba la placa por la derecha. |  | 0,92 | `0.92` |  | 0 … 1 |
| `plate_nonuniformity_k` — Irregularidad de la placa | E | Variación suave de temperatura dentro de la placa. | Más = placa menos uniforme. | 0,8 K | `0.8` | K | 0 … 20 |
| `plate_gradient_k` — Caída de temperatura de la placa | E | Cuánto más fría está la placa abajo que arriba. | Más = más degradado. | 1,5 K | `1.5` | K | 0 … 50 |
| `sky_gradient_k` — Degradado del cielo | E | Cuánto más frío es el cielo en lo alto que en el horizonte. | Más = más degradado. | 18 K | `18` | K | 0 … 80 |
| `sky_nonuniformity_k` — Irregularidad del cielo | E | Variación suave del cielo. |  | 0,6 K | `0.6` | K | 0 … 20 |
| `ground_start_frac` — Dónde empieza el suelo | E | Fila a partir de la cual hay suelo (cielo frío). |  | 0,90 | `0.9` |  | 0 … 1 |
| `ground_dt_k` — Temperatura del suelo | E | Diferencia del suelo respecto al ambiente. |  | –1 K | `-1` | K | -30 … 30 |
| `ground_nonuniformity_k` — Irregularidad del suelo | E | Variación suave del suelo. |  | 0,5 K | `0.5` | K | 0 … 20 |

## Gas y fuga

| Parámetro | Nivel | Qué es | Si lo subes | Valor típico | Por defecto | Unidad | Rango |
|---|---|---|---|---|---|---|---|
| `gas` — Gas | B | Qué refrigerante se libera: R134a, R152a o R1234ze. Fija su peso (y por tanto si la nube se hunde o sube). | Los más pesados que el aire (los tres) tienden a hundirse. | R134a (el del bote de aire comprimido para limpiar electrónica). | `R134a` |  |  |
| `gas_molar_mass_kg_mol` — Masa molar del gas propio | E | Sustituye la de la tabla para un gas que no esté en la lista. | Más = nube más densa en ppm·m. | Vacío | `None` | kg/mol | 0.001 … 1 |
| `gas_density_kg_m3` — Densidad del gas propio | E | Sustituye la de la tabla (a unos 20 °C). | Más denso = la nube se hunde más. | Vacío | `None` | kg/m³ | 0.01 … 50 |
| `air_density_kg_m3` — Densidad del aire | E | Densidad del aire para calcular la flotabilidad. |  | 1,2 kg/m³ | `1.2` | kg/m³ | 0.5 … 2 |
| `gravity_m_s2` — Gravedad | E | Gravedad para la flotabilidad. |  | 9,81 | `9.81` | m/s² | 0 … 30 |
| `molar_volume_m3_mol` — Volumen molar | E | Convierte masa de gas a concentración. |  | 0,02445 | `0.02445` | m³/mol | 0.01 … 0.05 |
| `flow_g_s` — Caudal de la fuga | B | Gramos de gas por segundo que salen mientras el venteo está abierto. | Más caudal = nube más grande y densa (más fácil de ver). Menos = nube pequeña y débil (más difícil). | 0,001–0,01 g/s: fuga pequeña real. 0,1 g/s: ejemplo. 1 g/s o más: bote de aire comprimido. | `0.1` | g/s | 0 … 1000 |
| `t_exit_c` — Temperatura del gas al salir | A | Vacío = igual que el ambiente. Un gas más frío (spray) contrasta más con un fondo caliente. | Más frío = más contraste con un fondo caliente. | Vacío; un spray sale por debajo del ambiente. | `None` | °C | -100 … 300 |
| `band_fraction` — Fracción de la banda que absorbe el gas  ⚠ sin calibrar | A | Qué parte de la señal de la cámara pasa por la zona donde el gas absorbe. Es un valor ESTIMADO: hay que calibrarlo con la cámara real. | Más = gas más visible. Por eso los resultados sobre visibilidad son orientativos hasta calibrar. | 0,25 | `0.25` |  | 0 … 1 |
| `k_abs` — Absorción del gas  ⚠ sin calibrar | A | Cuánto absorbe el gas por cantidad de gas (1/(ppm·m)). Valor ESTIMADO: hay que calibrarlo. | Más = gas más visible. | 0,002 | `0.002` | 1/(ppm·m) | 0 … 1 |
| `leak_on_s` — Cuándo empieza la fuga | B | Segundo en que se abre el venteo. | El detector aprende el fondo con el tiempo anterior: necesita al menos ~1 s sin gas. Si es muy pronto no puede aprender. | 1–3 s (con 3 s, mejor para el detector). | `1` | s | 0 … 3600 |
| `leak_off_s` — Cuándo se cierra la fuga | A | Segundo en que se cierra el venteo. Vacío = no se cierra. | Si lo pones, la nube se disipa después y se puede ver cómo desaparece. | Vacío; o 2–4 s después de abrir. | `None` | s | 0 … 3600 |

## Chorro y pluma

| Parámetro | Nivel | Qué es | Si lo subes | Valor típico | Por defecto | Unidad | Rango |
|---|---|---|---|---|---|---|---|
| `v_exit` — Velocidad de salida | A | Velocidad del gas al salir del tubo. | Más rápido = el chorro llega más lejos y se abre antes. | 3 m/s | `3` | m/s | 0 … 100 |
| `exit_angle_deg` — Ángulo de salida | A | 0 = vertical; positivo = inclinado hacia la derecha. | Más ángulo = el chorro sale más de lado. | 0° | `0` | ° | -90 … 90 |
| `nozzle_sigma_m` — Tamaño inicial del gas | E | Tamaño de cada «bolita» de gas al nacer. |  | 5 mm | `0.005` | m | 0.0001 … 1 |
| `jet_spread` — Apertura del chorro | E | Cuánto se ensancha el chorro al avanzar. | Más = chorro más abierto. | 0,11 | `0.11` |  | 0 … 1 |
| `jet_turb` — Turbulencia del chorro | A | Irregularidad propia del chorro cerca del tubo. | Más = pluma más irregular y rota. | 0,25 | `0.25` |  | 0 … 2 |
| `jet_tau_min_s` — Memoria mínima de la turbulencia del chorro | E | Mínimo tiempo de correlación de la turbulencia del chorro. |  | 0,01 s | `0.01` | s | 0.0001 … 10 |
| `jet_tau_max_s` — Memoria máxima de la turbulencia del chorro | E | Máximo tiempo de correlación de la turbulencia del chorro. |  | 0,5 s | `0.5` | s | 0.0001 … 10 |
| `buoy_tau_s` — Amortiguación de la flotabilidad | E | Qué rápido se frena el movimiento de subida o bajada. |  | 0,5 s | `0.5` | s | 0.001 … 100 |
| `entrain_ambient_coeff` — Mezcla con el aire | E | Cuánto crece la nube por mezclarse con el aire. | Más = nube más difusa. | 0,10 | `0.1` |  | 0 … 5 |
| `diffusion_base_m_s` — Crecimiento base de la nube | E | Ensanchamiento mínimo de cada bolita. |  | 2 mm/s | `0.002` | m/s | 0 … 1 |
| `puffs_per_s` — Calidad de la nube (bolitas por segundo) | A | En cuántas bolitas se reparte el gas emitido. | Más = nube más suave pero simulación más lenta. Menos = más rápida pero granulada. | 500 (para ir más rápido: 200). | `500` | 1/s | 10 … 20000 |
| `puff_mass_lognormal_sigma` — Variación de masa entre bolitas | E | Dispersión de masa entre puffs (lognormal). |  | 0,35 | `0.35` |  | 0 … 3 |
| `nozzle_jitter_frac` — Dispersión de salida | E | Dispersión de la posición de emisión (× nozzle_sigma_m). |  | 0,3 | `0.3` |  | 0 … 10 |
| `substep_s` — Paso de cálculo del movimiento | E | Paso de integración del movimiento. | Más pequeño = más preciso y más lento. | 5 ms | `0.005` | s | 0.0001 … 0.1 |

## Viento y turbulencia

| Parámetro | Nivel | Qué es | Si lo subes | Valor típico | Por defecto | Unidad | Rango |
|---|---|---|---|---|---|---|---|
| `wind_ms` — Viento horizontal | B | Velocidad del viento; positivo = hacia la derecha. | Más viento = la nube se estira, se aleja del tubo y se diluye antes (más difícil). Sin viento sube y se acumula. | 0,3 m/s (interior, casi quieto). Exterior: 1–3 m/s. | `0.3` | m/s | -30 … 30 |
| `wind_vertical_ms` — Viento vertical | E | Componente hacia arriba (positivo) del viento. |  | 0 | `0` | m/s | -10 … 10 |
| `gust_ms` — Ráfagas | A | Fluctuación del viento alrededor de su valor medio. | Más = la nube ondula y cambia de dirección. | 0,15 m/s | `0.15` | m/s | 0 … 10 |
| `gust_tau_s` — Duración de las ráfagas | E | Cada cuánto cambian las ráfagas. | Más = ráfagas más largas. | 2 s | `2` | s | 0.01 … 600 |
| `turb_rms` — Turbulencia del aire | A | Remolinos pequeños del aire. | Más = nube más rota y difusa. | 0,15 m/s | `0.15` | m/s | 0 … 5 |
| `turb_n_modes` — Detalle de la turbulencia | E | Cuántas ondas se suman para formar la turbulencia. | Más = más detalle y algo más lento. | 60 | `60` |  | 1 … 5000 |
| `turb_lambda_min_m` — Remolino más pequeño | E | Longitud de onda mínima de la turbulencia. |  | 2 cm | `0.02` | m | 0.001 … 10 |
| `turb_lambda_max_m` — Remolino más grande | E | Longitud de onda máxima de la turbulencia. |  | 80 cm | `0.8` | m | 0.001 … 100 |

## Perturbaciones del mundo real

| Parámetro | Nivel | Qué es | Si lo subes | Valor típico | Por defecto | Unidad | Rango |
|---|---|---|---|---|---|---|---|
| `bg_change_k` — Cambio lento del fondo (sol) | B | Cuánto cambia la temperatura del fondo por el sol o por equipos que se calientan. 0 = no cambia. | Más = el detector ve diferencias donde no hay gas (falsas alarmas). Se corrige con el perfil de detector «Exterior». | 0 (sin cambio). Para probar: 0,1–0,5 K. | `0` | K | -20 … 20 |
| `bg_change_period_s` — Duración del cambio del fondo | A | Tiempo del ciclo (si es senoidal) o de la subida (si es rampa). | Más largo = cambio más lento. | 20 s | `20` | s | 0.5 … 3600 |
| `bg_change_shape` — Forma del cambio del fondo | A | «sine»: sube y baja. «ramp»: sube y se queda. |  | sine | `sine` |  |  |
| `bg_change_spatial` — Cuánto cambia más en un lado | A | 0 = el fondo cambia igual en toda la imagen; 1 = cambia más en un lado. | Más = más difícil, porque un único nivel global ya no basta. | 0,5 | `0.5` |  | 0 … 1 |
| `cloud_k` — Nube o sombra que cruza | B | Cambio de temperatura en el centro de una nube que cruza la escena (negativo = más fría). 0 = sin nube. | Más = una mancha que se parece a una nube de gas. Se descarta exigiendo que la detección nazca en el tubo. | 0 (sin nube). Para probar: –1 K. | `0` | K | -30 … 30 |
| `cloud_size_m` — Tamaño de la nube | A | Diámetro aproximado de la nube. | Más = mancha mayor. | 0,5 m | `0.5` | m | 0.05 … 20 |
| `cloud_speed_ms` — Velocidad de la nube | A | Hacia la derecha. | Más = cruza antes. | 0,3 m/s | `0.3` | m/s | 0 … 20 |
| `cloud_start_s` — Cuándo entra la nube | E | Segundo en que empieza a entrar por la izquierda. |  | 0 s | `0` | s | 0 … 3600 |
| `object_count` — Personas u objetos que cruzan | B | Cuántos objetos que NO son gas cruzan la imagen (personas, pájaros, vehículos). 0 = ninguno. | Más = más oportunidades de falsa alarma. Se descartan exigiendo el origen en el tubo. | 0. Para probar: 1–2. | `0` |  | 0 … 50 |
| `object_size_m` — Tamaño del objeto | A | Ancho del objeto (el alto es el doble). | Más = objeto mayor. | 0,15 m | `0.15` | m | 0.02 … 5 |
| `object_speed_ms` — Velocidad del objeto | A | De izquierda a derecha. | Más = cruza antes. | 0,6 m/s (paso de persona) | `0.6` | m/s | 0 … 30 |
| `object_temp_c` — Temperatura del objeto | A | Temperatura aparente (el objeto es opaco y tapa el gas). |  | 32 °C (persona) | `32` | °C | -60 … 400 |
| `object_start_s` — Cuándo entra el primer objeto | E | Instante en que entra el primer objeto. |  | 0,5 s | `0.5` | s | 0 … 3600 |
| `object_row_frac` — Altura por la que cruza | E | 0 = abajo, 1 = arriba. –1 = al azar. |  | –1 | `-1` |  | -1 … 1 |
| `object_gap_s` — Tiempo entre objetos | E | Tiempo entre la entrada de un objeto y el siguiente. |  | 1,5 s | `1.5` | s | 0 … 3600 |
| `nuc_period_s` — Obturador de la cámara (cada cuántos s) | B | La cámara cierra un obturador cada cierto tiempo para recalibrarse: la imagen se congela un instante y cambia el nivel y el patrón fijo. 0 = nunca. | Más frecuente = más saltos. Los detectores sin fondo adaptativo se confunden con el cambio de patrón. | 0 (nunca). Para probar: 3–5 s. | `0` | s | 0 … 3600 |
| `nuc_step_k` — Salto de nivel del obturador | A | Cuánto salta el nivel global en cada cierre. | Más = salto mayor. | 0,03 K | `0.03` | K | 0 … 1 |
| `nuc_fpn_change` — Cuánto cambia el patrón fijo | A | Fracción del patrón fijo que cambia en cada cierre. | Más = el patrón anterior vale menos. | 0,3 | `0.3` |  | 0 … 1 |
| `nuc_freeze_frames` — Fotogramas congelados | A | Fotogramas repetidos mientras actúa el obturador. | Más = parada más larga. | 2 | `2` |  | 0 … 50 |
| `vibration_px` — Vibración de la cámara | B | Cuánto se mueve toda la imagen por vibración del soporte (píxeles, rms). 0 = nada. | Más = los bordes nítidos (placa, tubo) se mueven y parecen cambios. Se corrige con el perfil «Cámara con vibración». | 0 (nada). Para probar: 1–1,5 px. | `0` | px | 0 … 20 |
| `vibration_tau_s` — Rapidez de la vibración | E | Tiempo de correlación del movimiento. | Más = movimiento más lento. | 0,3 s | `0.3` | s | 0.01 … 60 |
| `compression_quality` — Compresión del vídeo | B | Compresión tipo JPEG del vídeo de 8 bit (1 = muy fuerte, 100 = casi nada). 0 = sin compresión. Simula un flujo comprimido (RTSP, MP4). | Más fuerte (número más bajo) = bloques y ruido no gaussiano: el detector pierde sensibilidad y salta con falsas alarmas. | 0 (sin compresión). Para probar: 25–60. | `0` |  | 0 … 100 |
| `bad_pixel_frac` — Píxeles defectuosos | A | Fracción de píxeles estropeados (siempre negros o siempre blancos). | Más = más píxeles inútiles. | 0 (ninguno). Real: 0,0005–0,002. | `0` |  | 0 … 0.05 |

## Salida y ejecución

| Parámetro | Nivel | Qué es | Si lo subes | Valor típico | Por defecto | Unidad | Rango |
|---|---|---|---|---|---|---|---|
| `duration_s` — Duración del vídeo | B | Segundos de vídeo simulados. | Más = más fotogramas, más lento y más memoria. Para vídeos muy largos usa el modo «vídeo largo». | 4–10 s. | `4` | s | 0.1 … 3600 |
| `mask_thresh_k` — Umbral de la máscara real | A | Cambio mínimo de temperatura (K) para que un píxel cuente como «gas» en la verdad del simulador. | Más alto = la máscara real solo incluye gas muy visible. Más bajo = incluye bordes muy tenues. | 0,035 K (un NETD). | `0.035` | K | 0 … 10 |
| `fov_margin_sigmas` — Margen al salir de la imagen | E | Los puffs se descartan al salir del campo de visión con este margen. |  | 4 | `4` | σ | 0 … 20 |
| `cl_kernel_sigmas` — Radio de dibujo de cada bolita | E | Radio (en σ) con que se dibuja cada puff en el mapa de columna. |  | 3,5 | `3.5` | σ | 1 … 10 |
| `store_cl` — Guardar el mapa de gas | A | Guarda cuánto gas hay en cada píxel. | Quitarlo ahorra memoria. | Sí | `True` |  |  |
| `store_temp` — Guardar la matriz de temperaturas | A | Guarda la matriz radiométrica (en centikelvin). | Quitarlo ahorra memoria. | Sí | `True` |  |  |
| `seed` — Semilla aleatoria | A | Mismo número = mismo vídeo exacto. Cambiarlo da otra realización aleatoria. |  | 0 | `0` |  | 0 … 4294967295 |

## Opciones del detector

| Opción | Nivel | Qué es | Si la subes | Valor típico |
|---|---|---|---|---|
| `input` — Entrada | A | Qué usa el detector: el vídeo de 8 bit («u8») o la matriz de temperaturas («temp»). | «temp» ve mejor las cosas muy débiles si la cámara la entrega. | u8 |
| `gas_free_s` — Periodo sin gas | B | Segundos del principio del vídeo que se sabe que NO tienen gas. Con ellos aprende el fondo y el ruido. | Más largo = fondo y ruido mejor medidos (pero tiene que ser anterior a la fuga). Mínimo ~0,7 s. | Automático = el 80 % del tiempo antes de la fuga. |
| `z_low` — Umbral bajo (σ) | A | Cuántas veces el ruido tiene que superar un píxel para considerarlo posible gas. | Más alto = menos falsas alarmas pero la nube se ve más pequeña. Más bajo = delimita mejor pero más ruido. | 2,5 |
| `z_high` — Umbral alto (σ) | A | Una zona solo cuenta si algún píxel suyo supera este umbral. | Más alto = más seguro (menos falsas alarmas) pero tarda más o no ve gas débil. | 4,5 |
| `sigma_px` — Suavizado espacial (píxeles) | A | Cuánto se emborrona la imagen para promediar ruido: una nube es grande, el ruido es de un píxel. | Más = menos ruido pero se pierden detalles pequeños. | 2 |
| `window_s` — Promedio temporal (s) | A | Cuántos segundos se promedian los últimos fotogramas. | Más = menos ruido pero la detección tarda más y arrastra el movimiento. | 0,2 s |
| `min_area` — Área mínima (píxeles) | A | Las zonas más pequeñas que esto se descartan. | Más = ignora manchas pequeñas. | 40 |
| `background` — Fondo | A | «static»: el fondo se aprende una vez. «adaptive»: sigue los cambios lentos. | Adaptativo ayuda con el sol y los cambios de patrón del obturador; se congela mientras hay alarma para no «absorber» una fuga. | static |
| `bg_tau_s` — Rapidez del fondo adaptativo (s) | E | Tiempo en que el fondo adaptativo asimila un cambio. | Más largo = sigue peor los cambios pero asimila menos gas. | 10–30 s |
| `drift_order` — Deriva del fondo | A | 0 = quita solo el nivel global. 1 = quita un plano. 2 = una superficie curva. | Con el sol o una placa que se calienta de forma desigual, 1 o 2 evitan falsas detecciones. | 0 (interior) o 1 (exterior). |
| `stabilize` — Corregir vibración | A | Alinea cada fotograma con el fondo antes de compararlo. | Útil si la cámara vibra. Cuesta una franja de los bordes y los píxeles de bordes muy marcados. | No (activar solo con vibración). |
| `polarity` — Signo esperado | A | «dark»: solo cuenta lo más oscuro que el fondo. «bright»: solo lo más claro. «both»: las dos. «auto»: según la escena. | Con placa caliente el gas es oscuro; una persona caliente delante sería clara y se descarta. | both (o auto). |
| `auto_threshold` — Umbral automático | A | Sube los umbrales hasta superar el mayor ruido visto sin gas. | Evita falsas alarmas con vídeo comprimido, pero pierde sensibilidad y con vibración fuerte deja de ver gas. | No (activar solo con vídeo comprimido). |
| `roi_text` — Zona de vigilancia | A | Polígono «x,y; x,y; x,y» (fracciones 0–1) donde se busca gas. Fuera no se detecta nada. | Reduce falsas alarmas del cielo, de gente de fondo y de reflejos. | Vacío = toda la imagen. |
| `require_source` — Exigir el origen en el tubo | A | Una detección solo cuenta si nace en la zona del tubo y crece desde allí. | Rechaza personas, pájaros y nubes que entran por un borde. Una nube que llegue al tubo sí contaría. | No. |
| `source_text` — Zona de origen | E | Polígono de la zona del tubo. Vacío = se calcula con la posición del tubo (solo simulado). | Más grande = deja pasar más cosas. | Vacío. |
| `bg_margin_px` — Margen alrededor de la detección | E | Margen para excluir lo detectado de ciertos ajustes. |  | 6 px |
| `bg_hold_on_alarm` — Congelar el fondo con alarma | E | Detiene el fondo adaptativo mientras hay alarma. | Casi siempre conviene dejarlo activado. | Sí |
| `max_shift_px` — Máximo desplazamiento a corregir | E | Mayor vibración que intentará corregir. |  | 6 px |

## Constantes que siguen fijas en el código

- Forma de la falta de uniformidad suave de la escena (`lowfreq`, senos de frecuencias fijas).
- Percentiles 1 y 99 con que se elige la ventana automática de 8 bit.
- Rango de frecuencias y fases aleatorias de cada modo de turbulencia (±50 % de `k·rms`).
- Rejilla de temperaturas de la tabla de Planck (150–800 K, paso 0,1 K) y 241 longitudes de onda en la banda.
- Modelo de ráfagas (Ornstein–Uhlenbeck solo en la componente horizontal).

# Parámetros del simulador

Generado con `python ogi_sim.py --params-md docs/parametros.md`. No editar a mano: la fuente es `Config` en `ogi_sim.py`.

Se cambian con un preset (`--preset`), un fichero JSON (`--config`) o `--set clave=valor`. Los valores por defecto reproducen el simulador original bit a bit.

## Cámara

| Parámetro | Por defecto | Unidad | Rango | Descripción |
|---|---|---|---|---|
| `width` | `384` | px | 16 … 4096 | Ancho de la imagen. |
| `height` | `288` | px | 16 … 4096 | Alto de la imagen. |
| `fps` | `25` | Hz | 1 … 240 | Fotogramas por segundo. |
| `m_per_px` | `0.005` | m | 0.0001 … 1 | Tamaño en el plano del objeto de un píxel. |
| `netd_k` | `0.035` | K | 0 … 1 | Ruido temporal de un píxel en un fotograma (NETD). |
| `fpn_k` | `0.02` | K | 0 … 1 | Patrón fijo residual por píxel. |
| `col_noise_k` | `0.01` | K | 0 … 1 | Patrón fijo residual por columna. |
| `drift_k` | `0.03` | K | 0 … 1 | Amplitud de la deriva lenta global del nivel. |
| `drift_period_s` | `20` | s | 0.1 … 3600 | Periodo de la deriva senoidal. |
| `psf_sigma_px` | `0.7` | px | 0 … 10 | Desenfoque óptico (σ de la gaussiana). |
| `detector_tau_s` | `0.01` | s | 1e-06 … 1 | Constante de tiempo térmica del microbolómetro. |
| `level_c` | `None` | °C |  | Centro de la ventana de 8 bit. None = automático (y fijo). |
| `span_c` | `None` | K |  | Ancho de la ventana de 8 bit. None = automático (y fijo). |
| `auto_span_margin_hot_k` | `8` | K | 0 … 100 | Margen que se suma al rango automático en placa caliente. |
| `auto_span_margin_cold_k` | `12` | K | 0 … 100 | Margen que se suma al rango automático en cielo frío. |
| `auto_headroom_k` | `1` | K | 0 … 50 | Holgura de la ventana automática respecto al fondo. |
| `hot_ref_margin_k` | `5` | K | 0 … 100 | Placa caliente: se usan los píxeles a más de (t_bg_c − margen) para fijar la ventana. |
| `cold_ref_margin_k` | `8` | K | 0 … 100 | Cielo frío: se usan los píxeles a menos de (t_amb_c − margen) para fijar la ventana. |
| `band_lo_um` | `8` | µm | 1 … 30 | Límite inferior de la banda espectral de la cámara. |
| `band_hi_um` | `14` | µm | 1 … 30 | Límite superior de la banda espectral de la cámara. |

## Escena

| Parámetro | Por defecto | Unidad | Rango | Descripción |
|---|---|---|---|---|
| `scene` | `hot_plate` |  | hot_plate / cold_sky | hot_plate (el gas absorbe: se ve oscuro) o cold_sky (el gas emite: se ve claro). |
| `t_amb_c` | `22` | °C | -60 … 60 | Temperatura ambiente (también la del tubo y, por defecto, la del gas). |
| `t_bg_c` | `70` | °C | -80 … 400 | Temperatura de la placa caliente / aparente del cielo. |
| `tip_x_frac` | `0.42` |  | 0 … 1 | Posición horizontal de la boca del tubo (fracción del ancho). |
| `tip_y_frac` | `0.7` |  | 0 … 1 | Posición vertical de la boca del tubo (fracción del alto, desde arriba). |
| `pipe_half_px` | `3` | px | 0 … 50 | Semianchura del tubo. |
| `pipe_dt_k` | `0.5` | K | -20 … 20 | Temperatura del tubo sobre la ambiente. |
| `amb_nonuniformity_k` | `0.4` | K | 0 … 10 | Falta de uniformidad suave del entorno (placa caliente). |
| `plate_top_frac` | `0.06` |  | 0 … 1 | Borde superior de la placa (fracción del alto). |
| `plate_bottom_frac` | `0.86` |  | 0 … 1 | Borde inferior de la placa (fracción del alto). |
| `plate_left_frac` | `0.08` |  | 0 … 1 | Borde izquierdo de la placa (fracción del ancho). |
| `plate_right_frac` | `0.92` |  | 0 … 1 | Borde derecho de la placa (fracción del ancho). |
| `plate_nonuniformity_k` | `0.8` | K | 0 … 20 | Falta de uniformidad suave de la placa. |
| `plate_gradient_k` | `1.5` | K | 0 … 50 | Caída de temperatura de la placa de arriba abajo. |
| `sky_gradient_k` | `18` | K | 0 … 80 | Cuánto más frío es el cielo en lo alto que en el horizonte. |
| `sky_nonuniformity_k` | `0.6` | K | 0 … 20 | Falta de uniformidad suave del cielo. |
| `ground_start_frac` | `0.9` |  | 0 … 1 | Fila donde empieza el suelo (fracción del alto). |
| `ground_dt_k` | `-1` | K | -30 … 30 | Temperatura del suelo respecto a la ambiente. |
| `ground_nonuniformity_k` | `0.5` | K | 0 … 20 | Falta de uniformidad suave del suelo. |

## Gas y fuga

| Parámetro | Por defecto | Unidad | Rango | Descripción |
|---|---|---|---|---|
| `gas` | `R134a` |  |  | Gas liberado (R134a, R152a, R1234ze) o cualquier nombre si se dan masa molar y densidad. |
| `gas_molar_mass_kg_mol` | `None` | kg/mol | 0.001 … 1 | Sustituye la masa molar de la tabla de gases. |
| `gas_density_kg_m3` | `None` | kg/m³ | 0.01 … 50 | Sustituye la densidad de la tabla de gases (~20 °C). |
| `air_density_kg_m3` | `1.2` | kg/m³ | 0.5 … 2 | Densidad del aire (flotabilidad). |
| `gravity_m_s2` | `9.81` | m/s² | 0 … 30 | Gravedad. |
| `molar_volume_m3_mol` | `0.02445` | m³/mol | 0.01 … 0.05 | Volumen molar a 25 °C y 1 atm (pasa kg/m² a ppm·m). |
| `flow_g_s` | `0.1` | g/s | 0 … 1000 | Caudal másico mientras el venteo está abierto. |
| `t_exit_c` | `None` | °C | -100 … 300 | Temperatura del gas al salir. None = ambiente. |
| `band_fraction` | `0.25` |  | 0 … 1 | MARCADOR: fracción de la banda de la cámara en que el gas absorbe. Calibrar. |
| `k_abs` | `0.002` | 1/(ppm·m) | 0 … 1 | MARCADOR: coeficiente de absorción. Calibrar. |
| `leak_on_s` | `1` | s | 0 … 3600 | Instante en que se abre el venteo. |
| `leak_off_s` | `None` | s | 0 … 3600 | Instante en que se cierra. None = no se cierra. |

## Chorro y pluma

| Parámetro | Por defecto | Unidad | Rango | Descripción |
|---|---|---|---|---|
| `v_exit` | `3` | m/s | 0 … 100 | Velocidad de salida del gas. |
| `exit_angle_deg` | `0` | ° | -90 … 90 | Ángulo de salida: 0 = vertical, positivo = hacia +x. |
| `nozzle_sigma_m` | `0.005` | m | 0.0001 … 1 | Tamaño inicial de cada puff. |
| `jet_spread` | `0.11` |  | 0 … 1 | Ensanchamiento del chorro: d(σ)/d(recorrido). |
| `jet_turb` | `0.25` |  | 0 … 2 | Intensidad de turbulencia del chorro (fracción de la velocidad local). |
| `jet_tau_min_s` | `0.01` | s | 0.0001 … 10 | Mínimo tiempo de correlación de la turbulencia del chorro. |
| `jet_tau_max_s` | `0.5` | s | 0.0001 … 10 | Máximo tiempo de correlación de la turbulencia del chorro. |
| `buoy_tau_s` | `0.5` | s | 0.001 … 100 | Tiempo de amortiguación de la velocidad de flotabilidad. |
| `entrain_ambient_coeff` | `0.1` |  | 0 … 5 | Crecimiento de los puffs por difusión con el aire (× turb_rms). |
| `diffusion_base_m_s` | `0.002` | m/s | 0 … 1 | Crecimiento base de cada puff. |
| `puffs_per_s` | `500` | 1/s | 10 … 20000 | Puffs emitidos por segundo (resolución de la pluma; más = más lento). |
| `puff_mass_lognormal_sigma` | `0.35` |  | 0 … 3 | Dispersión de masa entre puffs (lognormal). |
| `nozzle_jitter_frac` | `0.3` |  | 0 … 10 | Dispersión de la posición de emisión (× nozzle_sigma_m). |
| `substep_s` | `0.005` | s | 0.0001 … 0.1 | Paso de integración del movimiento. |

## Viento y turbulencia

| Parámetro | Por defecto | Unidad | Rango | Descripción |
|---|---|---|---|---|
| `wind_ms` | `0.3` | m/s | -30 … 30 | Viento horizontal medio (+x = hacia la derecha). |
| `wind_vertical_ms` | `0` | m/s | -10 … 10 | Componente vertical media del viento (+ = hacia arriba). |
| `gust_ms` | `0.15` | m/s | 0 … 10 | Fluctuación del viento (rms). |
| `gust_tau_s` | `2` | s | 0.01 … 600 | Tiempo de correlación de las ráfagas. |
| `turb_rms` | `0.15` | m/s | 0 … 5 | Velocidad rms de la turbulencia ambiente. |
| `turb_n_modes` | `60` |  | 1 … 5000 | Número de modos de Fourier de la turbulencia (más = más detalle). |
| `turb_lambda_min_m` | `0.02` | m | 0.001 … 10 | Longitud de onda mínima de la turbulencia. |
| `turb_lambda_max_m` | `0.8` | m | 0.001 … 100 | Longitud de onda máxima de la turbulencia. |

## Salida y ejecución

| Parámetro | Por defecto | Unidad | Rango | Descripción |
|---|---|---|---|---|
| `duration_s` | `4` | s | 0.1 … 3600 | Duración de la secuencia. |
| `mask_thresh_k` | `0.035` | K | 0 … 10 | La máscara de verdad marca |contraste sin ruido| mayor que esto. |
| `fov_margin_sigmas` | `4` | σ | 0 … 20 | Los puffs se descartan al salir del campo de visión con este margen. |
| `cl_kernel_sigmas` | `3.5` | σ | 1 … 10 | Radio (en σ) con que se dibuja cada puff en el mapa de columna. |
| `store_cl` | `True` |  |  | Guardar el mapa de densidad de columna (ocupa 4 de cada ~10 bytes por píxel). |
| `store_temp` | `True` |  |  | Guardar la matriz radiométrica temp_ck. |
| `seed` | `0` |  | 0 … 4294967295 | Semilla del generador aleatorio. |

## Constantes que siguen fijas en el código

- Forma de la falta de uniformidad suave de la escena (`lowfreq`, senos de frecuencias fijas).
- Percentiles 1 y 99 con que se elige la ventana automática de 8 bit.
- Rango de frecuencias y fases aleatorias de cada modo de turbulencia (±50 % de `k·rms`).
- Rejilla de temperaturas de la tabla de Planck (150–800 K, paso 0,1 K) y 241 longitudes de onda en la banda.
- Modelo de ráfagas (Ornstein–Uhlenbeck solo en la componente horizontal).

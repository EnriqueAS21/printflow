# Plan v2: Producción por líneas con rutas de pasos (PrintFlow)

## Contexto
Hoy Producción opera por pedido. Se quiere operar por línea (DetallePedido), y cada línea recorre una **ruta de pasos** (procesos/estaciones). Prioridad: no empeorar (y si se puede mejorar) el rendimiento. Solo análisis: no se tocó ningún archivo del proyecto.

> **Premisa corregida:** no hay SSE. El tablero hace *polling* cada 3 s a `/dashboard/cambios` (`static/js/tablero_sync.js:6,577`) guiado por ids de bitácora (`version_tablero`, `rutas/notificaciones.py:41`). Confirmado: polling con delta.

**Decisiones confirmadas:** ~~Producción ve todas las líneas con filtro "Mías"~~ (reemplazado: Producción solo ve lo asignado a ella) · `DetalleAsignacion.id_usuario` nullable · desmarcan Producción y Admin · editar un Terminado lo regresa a En proceso y notifica · ticket de mostrador fuera (`rutas/caja.py:202` solo crea `MovimientoCaja`) · sin aviso por línea · backfill: Terminados/Entregados quedan completados · Terminados en Producción = hoy + "ver anteriores" · hora local · **(resueltas)** editar un Terminado lo regresa a En proceso solo si la edición agrega/cambia líneas o deja pasos pendientes · precio y responsable no reinician avance, cambiar la **cantidad** reabre los pasos de esa línea · producto sin ruta ni proceso principal cae a reglas y luego a "Sin clasificar" · Terminado con pasos opcionales pendientes los marca `no_aplica` · ~~cualquier usuario de Producción marca cualquier paso~~ (reemplazado: Producción solo marca pasos de sus líneas) · categoría de líneas libres se calcula al leer con reglas vigentes · `/produccion` será la página de inicio del rol Produccion (desde el Paso 6).

**Decisiones de la auditoría (2026-11-21):**
- **Visibilidad por responsable:** Producción solo ve y marca lo asignado a ella; lo que no tiene responsable solo lo ven Recepción y Admin. Aplica a tablero, etiquetas y vista `/produccion`. Se **quita el filtro "Mías"** del Paso 6 (ya no hace falta: Producción solo ve lo suyo) y en el **Paso 4** Producción solo marca sus líneas. Recepción/Admin ven un contador **"N sin asignar"** (bloque 4 de la auditoría).
- **Etiquetas:** cualquier rol con permiso manda a la cola, pero Producción solo de sus pedidos; solo Admin y Recepción administran la cola (reanudar/reintentar/descartar). La etiqueta lleva datos de pago y es igual para todos los roles (no se quita el saldo a Producción).
- **Pedidos Entregados:** Producción no los abre; Admin y Recepción solo lectura.
- **Sesión:** se queda en 30 días. **M21 (Facturación ve fondo/efectivo esperado) queda pendiente; no tocar.**
- **Orden de los bloques de corrección:** 0 Rápidos → 1 Filtros del tablero → 2 Sesión vencida → 3 CSRF → 4 Visibilidad por responsable + etiquetas y cola → 5 Permisos del tablero → 6 Procesos y reglas → 7 Móvil.

**Despliegues (en este orden):**
1. **[0, A, B]**
2. **[1, 2a, 2b, 2c]**
3. **[3, 4, 5]** — `pg_dump` de la base **antes** de desplegar (toca el guardado de pedidos, backfill y regla de estatus)
4. **[6, 7]**
5. **[8]**

---

## 1. Cómo funciona hoy (sin cambios respecto a v1)

| Paso | Dónde | Qué hace |
|---|---|---|
| Captura | `static/js/formulario_pedido.js`, `templates/pedidos/_formulario_pedido.html` | Cada fila: `detalle_id[]`, `producto_id[]`, `presentacion_id[]`, `configuracion[]`, `responsable_producto[]` |
| Lectura | `rutas/pedidos.py` `_leer_detalles` (157), `_leer_responsables` (314) | Valida; responsables = únicos de `responsable_producto[]` (rol Produccion) |
| Guardado | `registrar_pedido` (708) → `_crear_pedido` (569) / `_actualizar_pedido` (531) → `Pedido.reemplazar_detalles` (`modelos/pedido.py:269`) | `detalles.clear()` y **recrea todo** (id_detalle nuevo); asignación solo si hay responsable; conserva `completado` solo si la línea quedó idéntica |
| Responsables | `pedido_responsables` (M2M) + `Pedido.id_usuario_produccion` (legado) + `DetalleAsignacion.id_usuario` | Tres fuentes de verdad |
| Marcar línea | `terminar_producto` (`rutas/pedidos.py:71`) | POST+redirect, `with_for_update`, solo desde ficha; auto-Terminado solo si >1 responsable |
| Terminado | `cambiar_estado` (`rutas/dashboard.py:354`) | Bloquea si >1 responsable y líneas incompletas; no toca `completado`. Con 1 responsable `completado` nunca se marca |
| Tablero | `dashboard.index/cambios/entregados/buscar` | Tarjeta = HTML Jinja en JSON `{id, columna, orden, html}` |
| Vista previa | `vista_previa_nota_pedido` (657) | Llama `reemplazar_detalles` sobre Pedido transitorio con rollback → no debe escribir pasos |

---

## 2. Código real vs. diseño v2

| Diseño | Real | Veredicto |
|---|---|---|
| `procesos` | No existe | Falta |
| Ruta por producto / por regla | `Producto.categoria` (String 60) existe; no hay ruta, proceso principal ni reglas | Falta (`producto_pasos`, `reglas_clasificacion`, `regla_pasos`, `Producto.id_proceso_principal`) |
| `linea_pasos` por línea | No existe | Falta; FK `ON DELETE CASCADE` a `detalle_pedido` |
| Edición por `id_detalle` | `reemplazar_detalles` borra y recrea; el formulario **ya envía** `detalle_id[]` | Reescribir como upsert (el dato ya llega) |
| `completado` derivado | Existe (`modelos/pedido.py:449`), editable a mano | Pasa a ser resumen mantenido por un solo servicio |
| `id_usuario` nullable | NOT NULL (`:443`); la plantilla usa `d.asignacion.usuario.nombre` (`_info_produccion.html:131,147`) y `terminar_producto` (`pedidos.py:92`) | Cambiar y proteger esos accesos |
| `fecha_completado`, `id_usuario_completo` | No existen | Falta |
| Estatus por progreso | Solo Terminado y solo con >1 responsable | Reescribir en `servicios/progreso.py` |
| "Mías" | Producción hoy ve por pedido asignado (`solo_sus_pedidos`, `permisos.py:71`; `_filtrar_por_rol`, `dashboard.py:56`) | Vista nueva: solo las líneas con `DetalleAsignacion.id_usuario` = el usuario (sin filtro "Mías"; las sin responsable solo Recepción/Admin). El tablero actual de Producción no cambia |
| Permisos | Producción: `dashboard.ver/terminar` | Agregar `produccion.ver`, `produccion.marcar` (Producción; Admin por `*`; Recepción no marca) |
| Sin dinero a Producción | Plantilla (`ver_comercial = puede('caja.ver')`, `_tarjeta_pedido.html:13`) | Vista nueva con serializador de **lista blanca**; el enlace WhatsApp sin `?text` con saldo (`_info_produccion.html:48`) |
| Fechas en Producción | No existía | Paso A (tablero) y Paso 6 (agrupación por Fecha) |
| Tests | Ninguno cubre `cambiar_estado`, `terminar_producto` ni `reemplazar_detalles`; patrón `unittest` + SQLite en memoria (`tests/test_caja.py`) | Paso 0. Evitar SQL solo-Postgres en servicios (`DISTINCT ON`); `COUNT FILTER` sí funciona en SQLite ≥3.30 |
| Migraciones | `scripts/migrar_*.py` idempotentes; `MIGRACIONES.txt` solo lista nombres; `create_all()` no agrega columnas | Un script por paso + línea en `MIGRACIONES.txt` |

**Ajuste de diseño propuesto (rendimiento):** guardar en `DetalleAsignacion` el resumen derivado `id_proceso_actual` (proceso del primer paso pendiente; NULL si completa). Así el tablero muestra "paso actual" y la vista Producción agrupa por proceso **sin cargar `linea_pasos`** salvo al expandir. Lo mantiene solo `servicios/progreso.py`.

**Agrupación por producto:** categoría del catálogo (`Producto.categoria`); en filas libres, la categoría de la regla se **calcula al leer** con las reglas en caché (sin columna en la línea). Efecto: si se editan reglas, el agrupado cambia en líneas libres existentes (el ruteo guardado en `linea_pasos` no cambia).

---

## 3. Rendimiento

### Medición (no ejecutada: modo análisis)
Sin tocar código: `SQL_ECHO=1` (`config.py:9`) y `PRINTFLOW_ENTORNO=desarrollo` (`pfContar`, `base.html:489`). Protocolo: arrancar con ambos, cargar `/dashboard`, contar `SELECT` del log; en DevTools→Network contar peticiones en 60 s. Cifras = **estimación por lectura de código**; confirmar en el Paso 0.

### Línea base estimada (hoy)

| Acción | SQL | HTTP |
|---|---|---|
| Cargar `/dashboard` | ~25 (Usuario 1, perfil 1, `version_tablero` 1, 3 listas × ~5, total entregados 1, `_folio_sugerido` 1, `_contexto_tarjeta` 2) | 1 + estáticos cacheados |
| Poll `/cambios` vacío | 2 (Usuario + bitácora) | 1 cada 3 s por pestaña |
| Poll con 1 cambio | ~12 | 1 |
| Pestaña en reposo | — | `/cambios` 20/min + `/notificaciones/resumen` 2/min + cola de etiquetas 2/min |
| Marcar línea (ficha) | ~8 + redirect ≈ 10 | 2 |
| Botón Terminado | ~6 | 1 + poll forzado |

### Estimado tras v2

| Vista / acción | Antes | Después | Cómo |
|---|---|---|---|
| Tablero Recepción/Admin | ~25 | ~25 (≈23 tras Paso B) | Progreso e `id_proceso_actual` salen de asignaciones ya cargadas; **0 consultas nuevas por tarjeta**; usuario que completó = `joinedload` en la misma consulta de asignaciones |
| Columnas por fecha de Producción (Paso A) | ~25 | **11** (medido) | La columna se calcula en servidor con las fechas ya cargadas; no se carga Entregado |
| Vista Producción (carga) | — | **4** (3 tras Paso B): Usuario, perfil, líneas pendientes (JOIN pedido+asignación+producto, **sin dinero**), selectinload de pasos; + Terminados de hoy (1) | Procesos/reglas/rutas de producto en caché (0) |
| **Marcar un paso** | ~10 y 2 HTTP (hoy, marcar línea) | **≈5 SQL, 1 HTTP** si el paso no cierra la línea: `UPDATE linea_pasos … WHERE estado='pendiente' RETURNING` (1) + pasos restantes de la línea (1) + `UPDATE detalle_asignaciones` (`id_proceso_actual`) (1) + bitácora (1) + Usuario (1; 0 tras Paso B). **≈7** si cierra la línea (+ recuento de progreso `COUNT FILTER` (1) + `completado/fecha`). **≈10** si cierra el pedido (+ `UPDATE pedidos SET estatus='Terminado' WHERE estatus='En proceso' RETURNING` + usuarios por rol (1) + `INSERT` notificaciones) |
| Marcar subgrupo (N líneas) | N × ~10 | **≈8, 1 HTTP**, independiente de N: `UPDATE … WHERE id IN (…) RETURNING` + resumen por línea agrupado + `UPDATE` asignaciones en lote + recuento por pedido (`GROUP BY`) + transición de pedidos en lote + `INSERT` masivo de bitácora/notificaciones |
| Respuesta de marcar | redirect + HTML | JSON mínimo: `{id_paso, id_detalle, estado, id_proceso_actual, completa, progreso, estatus_pedido}`; sin HTML |
| Poll vacío | 2 | 2 (1 tras Paso B) | Mismo mecanismo |
| Poll con cambio de paso | ~12 | ~8 | Delta por línea; tarjeta completa solo si cambia de columna |

### Índices
Existentes: `pedidos(estatus, fecha_entrega)`, `pedidos(estatus, fecha_entregado)`, `detalle_pedido.id_pedido`, `detalle_pedido.id_producto`, `detalle_asignaciones.id_usuario`, `pagos.id_pedido`, `pedido_responsables(id_usuario)`, `bitacora(entidad, accion, id_bitacora)`, `productos.categoria`.
**Nuevos:** `linea_pasos(id_detalle, orden)` único · parcial `linea_pasos(id_detalle) WHERE estado='pendiente'` · `detalle_asignaciones(id_proceso_actual) WHERE completado = false` · `detalle_asignaciones(fecha_completado)` (Terminados de hoy / tiempos) · `producto_pasos(id_producto, orden)` · `regla_pasos(id_regla, orden)`.

### Waitress
`threads=16` (`servidor.py`), pool 16+4 (`app.py:70-75`). Hoy no hay conexiones largas: cada poll dura ~10–30 ms; ~0.4 req/s por pestaña → **decenas de pestañas** sin riesgo; el límite real es renderizar tarjetas. Con SSE real **cada conexión ocuparía un hilo**: 16 pestañas congelarían login, caja y etiquetas. Se mantiene polling con delta.

### Cuellos actuales (impacto ↓ / esfuerzo)

| # | Cuello | Impacto | Esfuerzo |
|---|---|---|---|
| 1 | `/dashboard` carga y renderiza **todas** las En proceso y Terminadas (`dashboard.py:213-220`); WhatsApp y apartados recompuestos por tarjeta (`_info_produccion.html:43-50`) | Alto | Medio |
| 2 | `db.session.get(Usuario)` en cada request (`permisos.py:132`, también polls) y `PerfilUsuario` en cada render (`permisos.py:151`) | Medio-Alto (×24 req/min×pestañas) | Bajo |
| 3 | `cambios` con actividad repite `_contexto_tarjeta` (2), `_total_entregados`, `_por_cobrar` | Medio | Bajo |
| 4 | `_por_cobrar_terminados` carga todos los pagos de Terminados | Medio | Bajo-Medio (SUM en SQL) |
| 5 | `buscar` usa `ILIKE '%x%'` sin índice trigram | Medio con historial grande | Medio |
| 6 | `/notificaciones/resumen` agrega `version_tablero` por pestaña cada 30 s | Bajo-Medio | Bajo |
| 7 | `_leer_detalles` valida presentación con 1 consulta por línea (`pedidos.py:221`) | Bajo | Bajo |
| 8 | `reemplazar_detalles` borra/recrea todo | Bajo en rendimiento, **alto en riesgo** | Medio (se resuelve en Paso 3) |

---

## 4. Plan por pasos y prompts

> Un chat por paso; solo los archivos listados; no correr pruebas hasta el final del paso. Cada migración: script idempotente en `scripts/` + línea en `MIGRACIONES.txt`.

### Paso 0 — Línea base y tests de caracterización *(sin cambios de código)*
- **Archivos:** nuevo `tests/test_estado_lineas.py`.
- **BD:** ninguna. **Riesgo:** ninguno. **Prueba:** `pytest tests/test_estado_lineas.py`; medir consultas con `SQL_ECHO=1` y anotarlas.
- **Prompt:** "Crea `tests/test_estado_lineas.py` (patrón de `tests/test_caja.py`) que fije el comportamiento actual de `cambiar_estado`, `terminar_producto` y `reemplazar_detalles` (conserva `completado` si la línea no cambió, lo reinicia si cambió). No modifiques código de la app. Corre solo ese archivo al final."

### Paso A — Columnas por fecha en el tablero de Producción *(independiente de líneas)*
- **Objetivo:** solo para el rol Produccion, el tablero pasa a **Vencidos · Para hoy · Próximos · Terminados hoy**. Vencidos/Para hoy/Próximos = pedidos En proceso según el **día** de `fecha_entrega` contra hoy (hora local); sin fecha → Próximos. Terminados hoy = Terminado con `fecha_terminado` de hoy. Producción no ve Entregado (ni su total, ni la ruta `/entregados`, ni resultados Entregado en la búsqueda). Administrador y Recepción no cambian.
- **Cómo:** la columna se decide en el servidor (`_columna_produccion` en `rutas/dashboard.py`), donde se asigna `columna` a cada tarjeta; `/dashboard/cambios` mueve las tarjetas con el mecanismo existente (solo se agregan las claves de columna a `ORDEN_PREDETERMINADO` en `tablero_sync.js`). Si la página sigue abierta al cambiar de día, recarga completa (compara con la fecha local al cargar, para no recargar en bucle por desfase de zona).
- **Archivos:** `rutas/dashboard.py`, `templates/dashboard.html` (se quita el bloque "Para hoy"), `static/js/tablero_sync.js`.
- **BD:** ninguna. **Riesgo:** bajo. **Medición:** `/dashboard` de Producción 11 consultas (≤ 22: no carga Entregado ni folio sugerido); Admin 23, igual que antes. Sin consultas nuevas por tarjeta.
- **Prompt:** "Solo para el rol Produccion, cambia las columnas del tablero a Vencidos · Para hoy · Próximos · Terminados hoy decididas en el servidor; recarga completa al cambiar el día; sin consultas nuevas por tarjeta."

### Paso B — Cuello #2: Usuario y Perfil por request
- **Objetivo:** quitar 2 consultas por render y 1 por poll. `g.usuario` solo se usa por `id_usuario`, `rol`, `nombre`, `activo` (y `es_responsable`); los tests ya usan `SimpleNamespace`, por lo que un objeto ligero es viable.
- **Archivos:** `servicios/permisos.py` (`_exigir_sesion`, `_contexto`), `rutas/usuarios.py` (invalidar al editar/desactivar/cambiar foto), `rutas/autenticacion.py` si guarda sesión.
- **Cambio:** caché en memoria por proceso `{uid: datos}` con TTL corto (30–60 s) + invalidación explícita; `foto_archivo` incluida. Usuario desactivado o con rol cambiado debe surtir efecto en ≤ TTL (o al instante por invalidación).
- **Riesgo:** medio (seguridad/permisos). **Prueba:** tests existentes; desactivar un usuario y verificar bloqueo; contar consultas antes/después.
- **Prompt:** "Reduce las consultas por request en `servicios/permisos.py`: cachea (TTL 60 s + invalidación en `rutas/usuarios.py`) los datos de usuario y foto de perfil en un objeto ligero con `id_usuario, nombre, rol, activo, foto`. Un usuario desactivado debe perder acceso de inmediato al invalidarse. Mide consultas con SQL_ECHO=1 antes y después."

### Paso 1 — Esquema de rutas (aditivo) *(reescrito)*
- **Objetivo:** tablas `procesos` (id, nombre, orden, activo, `es_sistema`), `producto_pasos` (id_producto, orden, id_proceso, opcional), `reglas_clasificacion` (id, palabra_clave, prioridad, categoria, id_proceso_principal, activo), `regla_pasos` (mismo esquema que `producto_pasos`), `linea_pasos` (id, id_detalle FK CASCADE, orden, id_proceso, **opcional**, estado `pendiente|hecho|no_aplica` con CHECK, fecha, id_usuario nullable). Columnas: `Producto.id_proceso_principal`; `DetalleAsignacion.fecha_completado`, `id_usuario_completo`, `id_proceso_actual`; `id_usuario` → nullable. Sembrar el proceso de sistema "Sin clasificar".
- **Archivos:** nuevo `modelos/produccion.py`, `modelos/producto.py`, `modelos/pedido.py` (columnas y relación `pasos` con `cascade="all, delete-orphan"`), `app.py` (import del modelo), nuevo `scripts/migrar_rutas_produccion.py`, `MIGRACIONES.txt`.
- **BD:** `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `ALTER COLUMN id_usuario DROP NOT NULL` (idempotente en PostgreSQL), índices de la sección 3. Crear `DetalleAsignacion` vacía para toda línea sin fila (el backfill de pasos va en el Paso 3).
- **Riesgo:** bajo-medio: al volverse nullable, proteger `d.asignacion.usuario.nombre` en `_info_produccion.html:131,147` y `pedidos.py:92`. **Prueba:** correr la migración 2 veces; arrancar la app; tests del Paso 0.
- **Prompt:** "Agrega modelos, columnas, índices y `scripts/migrar_rutas_produccion.py` idempotente según el Paso 1 del plan (procesos, producto_pasos, reglas_clasificacion, regla_pasos, linea_pasos, columnas nuevas de DetalleAsignacion, `id_usuario` nullable, proceso de sistema 'Sin clasificar'). Protege los accesos a `asignacion.usuario` que asumen no-nulo. Sin cambiar lógica de negocio. Corre la migración dos veces y `tests/test_estado_lineas.py`."

### Paso 2 — Servicio de rutas, clasificación y caché *(reescrito)*
- **Objetivo:** `servicios/rutas_produccion.py` con función **pura** `ruta_para(id_producto, descripcion)` → lista `(orden, id_proceso, opcional)`:
  1. producto del catálogo con ruta → esa ruta; con solo proceso principal → 1 paso;
  2. si no (o fila libre) → reglas por palabra clave (`servicios/textos.normalizar_busqueda`, por prioridad): su ruta, o su proceso principal;
  3. nada → 1 paso "Sin clasificar".
  Caché en memoria de procesos, rutas de producto y reglas, con `invalidar()` al guardar. CRUD de procesos y reglas (Admin, `productos.administrar`) y editor de ruta en el formulario de producto.
- **Sin carga inicial:** el Administrador captura procesos, rutas y reglas desde la interfaz; la única fila previa es "Sin clasificar" (la crea la migración del Paso 1). Todo requiere `productos.administrar` y **invalida la caché al guardar**.
- **Se divide en 3 sub-pasos (un chat cada uno):**

**2a — Servicio + caché + CRUD de procesos (Configuración)**
- **Archivos:** nuevo `servicios/rutas_produccion.py`; `rutas/configuracion.py` (o nuevo `rutas/procesos.py` bajo Configuración), `templates/configuracion/procesos.html`, entrada en el menú de Configuración.
- **Cambio:** alta/edición/orden/activar-desactivar de procesos (nunca borrar uno en uso; "Sin clasificar" no editable ni desactivable).
- **Prompt:** "Crea `servicios/rutas_produccion.py` con `ruta_para()` pura y caché con `invalidar()` (Paso 2 del plan) y un CRUD de procesos en Configuración con permiso `productos.administrar`: nombre, orden, activo; 'Sin clasificar' protegido; invalida la caché al guardar. No toques el guardado de pedidos. Tests unitarios de `ruta_para()`."

**2b — Ruta de producción en el producto**
- **Archivos:** `rutas/productos.py` (guardar/leer ruta, `copiar-ruta`, `aplicar-a-categoria`, filtro `?ruta=sin`), `templates/productos/form.html`, `static/productos/form.js`, `templates/productos/lista.html`.
- **Cambio:**
  - Sección **"Ruta de producción"** en el formulario: lista ordenable (arrastrar o subir/bajar) de procesos, casilla **opcional** por paso, y selector de **proceso principal** (usado si la ruta queda vacía).
  - **"Copiar ruta de otro producto"** (buscador de producto → rellena la lista; no guarda hasta pulsar Guardar).
  - **"Aplicar ruta a toda una categoría"**: copia la ruta del producto actual a todos los productos de su `categoria` (confirmación con conteo; una transacción; con dos modos: **"solo productos sin ruta" (por defecto)** o **"sobrescribir todas"**; la confirmación muestra el conteo de cada caso, p. ej. "12 sin ruta · 30 en total").
  - Filtro **"Sin ruta"** en la lista de productos (sin `producto_pasos` ni proceso principal); consulta con `NOT EXISTS` + índice `producto_pasos(id_producto, orden)`, sin N+1.
  - Validaciones: procesos activos, sin repetir el mismo proceso consecutivamente en duplicado inútil, máximo razonable de pasos (p. ej. 8).
- **Riesgo:** medio-bajo (el formulario de producto es JS y ya es largo). **Prueba:** guardar/leer ruta, copiar, aplicar a categoría (cuenta correcta), filtro "Sin ruta", caché invalidada.
- **Prompt:** "En el formulario de producto agrega la sección 'Ruta de producción' (lista ordenable, casilla opcional por paso, proceso principal) con 'Copiar ruta de otro producto'; agrega la acción 'Aplicar ruta a toda una categoría' (una transacción; modo 'solo sin ruta' por defecto u 'sobrescribir todas'; la confirmación muestra el conteo de cada caso) y el filtro 'Sin ruta' en `productos/lista.html` (consulta con NOT EXISTS). Permiso `productos.administrar`; invalida la caché al guardar. Sin tocar pedidos."

**2c — CRUD de reglas con su ruta**
- **Archivos:** `rutas/configuracion.py` (o `rutas/reglas.py`), `templates/configuracion/reglas.html` (reutiliza el editor de ruta de 2b como componente JS compartido).
- **Cambio:** alta/edición/prioridad/activar de reglas: palabra clave, categoría (para agrupar), proceso principal y ruta ordenable con casilla opcional. Vista de prueba: escribir una descripción y ver qué regla y ruta le tocaría (llama a `ruta_para()`, sin escribir).
- **Prompt:** "Crea el CRUD de reglas de clasificación en Configuración (palabra clave, prioridad, categoría, proceso principal, ruta ordenable con opcional), reutilizando el editor de ruta del formulario de producto, con una caja de prueba que muestre qué ruta daría una descripción. Permiso `productos.administrar`; invalida la caché al guardar."

- **BD (2a–2c):** ninguna nueva. **Riesgo global:** bajo-medio (caché por proceso: válida con un solo proceso Waitress; con varios, usar versión en BD). **Prueba global:** tests unitarios del clasificador sin BD; desactivar un proceso no rompe líneas que lo usan.
- **Nota de adopción:** hasta que el Administrador capture rutas, todas las líneas nuevas caen en "Sin clasificar" (1 paso). El filtro "Sin ruta" y "Aplicar a categoría" son la forma rápida de poblar el catálogo; conviene hacerlo antes de usar la vista de Producción (Paso 6).

### Paso 3 — Guardado por `id_detalle` + `linea_pasos` + backfill *(reescrito; riesgo alto)*
- **Objetivo:** reescribir `Pedido.reemplazar_detalles` como **upsert por `id_detalle`** (actualiza las existentes, crea nuevas, elimina las quitadas por `delete-orphan`).
  - Ruta: línea nueva → `ruta_para()` → copiar a `linea_pasos`. Línea existente → recalcular **solo si cambió `id_producto`, `id_presentacion` o `descripcion`**; los pasos ya `hecho` con el mismo `id_proceso` se conservan.
  - Cambiar **precio o responsable** no reinicia avances. Cambiar la **cantidad** **reabre todos los pasos de esa línea** (pasan a `pendiente`; se limpian fecha/usuario) y recalcula su resumen (hoy cualquier cambio reinicia).
  - Siempre existe `DetalleAsignacion` (responsable opcional); recalcular su resumen.
  - Pedido **Terminado** editado → En proceso (`fecha_terminado = NULL`) + notificar a los responsables de las líneas (si no hay, a Producción) **solo si** la edición agrega/cambia líneas, reabre pasos por cantidad o deja pasos pendientes; si solo cambia notas, fecha, cliente, precio o responsable, sigue Terminado. Entregado/Cancelado siguen sin editarse.
  - **Acción Admin "Reclasificar líneas pendientes"** (botón en Configuración, `productos.administrar`, con confirmación y conteo previo): recalcula `linea_pasos` con el `ruta_para()` vigente **solo en líneas sin ningún paso `hecho`** de pedidos En proceso; las demás no se tocan. Una transacción, en lote (carga reglas/rutas de la caché una vez, sin N+1), registra bitácora con el conteo.
  - El Pedido transitorio de `vista_previa_nota_pedido` no debe tocar BD ni clasificar.
  - **Backfill** (script idempotente): líneas de pedidos Terminado/Entregado → 1 paso "Sin clasificar" `hecho`, `completado=True`, `fecha_completado = fecha_terminado/fecha_entregado`; En proceso → `ruta_para()` (los `completado` ya marcados → pasos `hecho`); Cancelado sin pasos.
- **Archivos:** `modelos/pedido.py`, `rutas/pedidos.py` (`_actualizar_pedido`, `_crear_pedido`, `_leer_detalles` mínimo), `servicios/rutas_produccion.py` (`reclasificar_pendientes()`), `rutas/configuracion.py` + `templates/configuracion/procesos.html` (botón), `scripts/backfill_pasos.py` (o dentro de la migración), `MIGRACIONES.txt`.
- **BD:** solo datos (backfill). **Riesgo:** **alto**: toca el guardado de todos los pedidos. **Prueba:** tests del Paso 0 + nuevos (editar precio o responsable conserva avance; editar cantidad reabre los pasos de esa línea; cambiar producto recalcula ruta; editar Terminado → En proceso; vista previa sin escrituras); guardar/editar a mano un pedido; backfill dos veces.
- **Prompt:** "Reescribe `Pedido.reemplazar_detalles` como upsert por `id_detalle` según el Paso 3 del plan: recalcular `linea_pasos` solo si cambió producto/presentación/descripción; conservar pasos hechos con mismo proceso; siempre crear `DetalleAsignacion`; editar un Terminado → En proceso y notificar solo si la edición agrega/cambia líneas o deja pasos pendientes; cambiar la cantidad reabre los pasos de esa línea (precio y responsable no); sin escrituras en la vista previa. Agrega el script de backfill idempotente y la acción Admin 'Reclasificar líneas pendientes' (solo líneas sin pasos hechos, con `ruta_para()` vigente) con botón en Configuración. Actualiza los tests y corre solo `tests/test_estado_lineas.py`."

### Paso 4 — Servicio de progreso y endpoints AJAX *(reescrito; riesgo alto)*
- **Objetivo:** `servicios/progreso.py` como **único** lugar que cambia pasos, resumen de línea y estatus:
  - `marcar_paso`, `marcar_pasos` (lote), `desmarcar_paso`, `no_aplica` (**solo** si `opcional`; si no, error).
  - Línea completa = todos sus pasos `hecho`/`no_aplica` → `DetalleAsignacion.completado=True`, `fecha_completado`, `id_usuario_completo` (quien cerró el último paso), `id_proceso_actual=NULL`; si no, `id_proceso_actual` = proceso del primer paso pendiente.
  - Pedido: 100 % → Terminado (`fecha_terminado`, notifica una vez a Recepción/Admin); <100 % estando Terminado → En proceso; **nunca** toca Entregado/Cancelado. Transición con `UPDATE … WHERE estatus='En proceso' RETURNING` (idempotente, sin bloqueos largos).
  - Botón "Terminado" (`cambiar_estado`): `completar_todas` → pasos obligatorios `hecho`, opcionales pendientes `no_aplica`.
  - `terminar_producto` (ficha) delega: completa todos los pasos de esa línea.
  - Bitácora: `PASO_HECHO`, `PASO_REABIERTO`, y `PRODUCTO_TERMINADO` al cerrar la línea; agregar a `ACCIONES_TABLERO`.
  - Permisos `produccion.ver`, `produccion.marcar` (Producción; Admin por `*`).
- **Archivos:** nuevo `servicios/progreso.py`, `rutas/pedidos.py` (`terminar_producto`), `rutas/dashboard.py` (`cambiar_estado`), `rutas/notificaciones.py`, `servicios/permisos.py`.
- **BD:** ninguna. **Riesgo:** alto (cambia la regla de Terminado: deja de depender de ">1 responsable"). **Prueba:** tests del Paso 0 actualizados + marcar/desmarcar/no_aplica, 100 %, regreso a En proceso, doble clic simultáneo, notificación única.
- **Prompt:** "Crea `servicios/progreso.py` (marcar/desmarcar/no_aplica/completar_todas, resumen derivado de línea y estatus de pedido) según el Paso 4. Haz que `terminar_producto` y `cambiar_estado` lo usen. Respuestas AJAX mínimas. Agrega permisos `produccion.*` y acciones de bitácora. Actualiza tests."

### Paso 5 — Progreso en Recepción/Admin *(reescrito)*
- **Objetivo:** barra "1 de 2 · 50%", etiqueta "Parcial" (0 < hechas < total, En proceso) y, en "ver más", por línea: ✓/Pendiente, **paso actual** (nombre de proceso desde caché), quién y a qué hora. Reemplazar usos de `tiene_asignaciones_por_producto` (`_tarjeta_pedido.html:223`, `_info_produccion.html:102,129,143`, `ficha.html:28`, `dashboard.py:379`, `pedidos.py:94`).
- **Archivos:** `modelos/pedido.py` (`con_relaciones_tarjeta`: `joinedload` de `usuario_completo` en la misma consulta; propiedad `progreso` sobre relaciones ya cargadas), `templates/pedidos/_tarjeta_pedido.html`, `_info_produccion.html`, `ficha.html`, `static/css/tarjeta_pedido.css`.
- **BD:** ninguna. **Riesgo:** bajo-medio. **Prueba:** consultas de `/dashboard` ≤ línea base; tarjetas con 0, 1 y N líneas; `tablero_sync.js` detecta el cambio solo (`sinEstadoDeInterfaz` compara el HTML).
- **Prompt:** "Agrega progreso, etiqueta Parcial y detalle por línea con paso actual en las plantillas de tarjeta/ficha usando solo relaciones ya cargadas por `con_relaciones_tarjeta` (sin consultas nuevas por tarjeta). Elimina los usos de `tiene_asignaciones_por_producto`."

### Paso 6 — Vista de Producción *(reescrito)*
- **Objetivo:** `/produccion` (`produccion.ver`):
  - Dos listas: **Pendientes** (líneas `completado=false` de pedidos En proceso) y **Terminados** (hoy + "ver anteriores" con cursor por `fecha_completado, id`).
  - Acordeón agrupable por Proceso (= `id_proceso_actual`), Producto (categoría del catálogo o de la regla, calculada al leer), Pedido, Fecha. **Fecha es la agrupación por defecto y usa la misma disposición que el tablero de Producción (Paso A): Vencidos · Para hoy · Próximos** (más Terminados hoy en la lista de Terminados), con etiquetas HOY/VENCIDO.
  - **La línea aparece en el proceso de su primer paso pendiente**; al marcar el paso se mueve al siguiente proceso (la respuesta trae el nuevo `id_proceso_actual`). "No aplica" solo en pasos opcionales. ~~Filtro "Mías"~~ (retirado: Producción solo ve sus líneas asignadas).
  - Por línea: folio, descripción, cliente, teléfono → WhatsApp (sin texto de saldo), entrega, notas del pedido. **Sin dinero** (serializador con lista blanca).
  - Acciones: marcar paso, "marcar subgrupo" (todos los pasos actuales del grupo), desmarcar (`produccion.marcar`).
  - **Página de inicio del rol Produccion:** `/produccion` reemplaza al tablero como destino tras el login y en `GET /` (hoy `index()` en `app.py` redirige a `dashboard.index`; el login redirige según `rutas/autenticacion.py`). Solo para el rol Produccion; los demás roles no cambian. El tablero sigue accesible por menú.
- **Archivos:** nuevo `rutas/produccion.py`, `templates/produccion/index.html`, `static/js/produccion.js`, `app.py` (blueprint y redirección de `/` por rol), `rutas/autenticacion.py` (destino tras login), `base.html` (navegación).
- **BD:** ninguna. **Riesgo:** medio (módulo nuevo y aislado). **Prueba:** consultas según sección 3; test de que respuestas/HTML para Producción no contienen `total`, `saldo`, `pago`, `factura`; mover línea de proceso al marcar; paginación de Terminados.
- **Prompt:** "Crea el blueprint `produccion` según el Paso 6: una consulta de líneas pendientes (joinedload pedido/asignación/producto, sin dinero) + selectinload de pasos, agrupación en cliente con catálogos en caché, sin filtro 'Mías' (Producción solo ve sus líneas asignadas), endpoints AJAX para marcar paso y subgrupo vía `servicios/progreso.py`, Terminados de hoy con cursor para anteriores. Serializador con lista blanca; ningún dato de pago. Haz de `/produccion` la página de inicio del rol Produccion (login y `/`)."

### Paso 7 — Delta por polling *(sin cambios de enfoque)*
- **Objetivo:** extender `/dashboard/cambios` y crear `/produccion/cambios` con delta `{id_detalle, id_proceso_actual, completa, progreso, estatus}`; el JS actualiza la línea y recarga la tarjeta solo si cambia de columna. Sin SSE.
- **Archivos:** `rutas/dashboard.py`, `rutas/produccion.py`, `static/js/tablero_sync.js`, `static/js/produccion.js`. **Riesgo:** medio (respetar `protegida()`, scroll y foco). **Prueba:** dos navegadores.
- **Prompt:** "Extiende `/dashboard/cambios` con `lineas` (delta) para `PASO_HECHO`/`PASO_REABIERTO`/`PRODUCTO_TERMINADO`, y en `tablero_sync.js` aplica el delta sin reemplazar la tarjeta salvo cambio de columna. Crea `/produccion/cambios` equivalente."

### Paso 8 — Resto de rendimiento
- Cuellos #1, #3, #4, #6, #7 de la sección 3. **Riesgo:** medio. **Prueba:** comparar con la línea base del Paso 0.
- **Prompt:** "Reduce consultas según los cuellos #3, #4, #6 y #7 del plan sin cambiar comportamiento visible; limita Terminadas del tablero a recientes con paginación. Mide antes/después con SQL_ECHO=1."

---

## 5. Verificación end-to-end (al terminar)
- `pytest` completo; migraciones y backfill ejecutados dos veces sin error.
- Con `SQL_ECHO=1`: comparar `/dashboard`, `/produccion`, marcar paso, marcar subgrupo y poll de `/cambios` contra la tabla de la sección 3.
- Flujo manual: pedido con 3 líneas (catálogo con ruta de 2 pasos, libre con regla, sin clasificar) → marcar paso 1 (la línea cambia de proceso) → "no aplica" en paso opcional → cerrar líneas (Parcial → 100 % → Terminado, notificación única) → desmarcar (vuelve a En proceso) → botón Terminado completa todo → editar Terminado → En proceso + notificación → Entregar intacto.
- Rol Producción: ninguna respuesta contiene montos, saldo ni pagos.
- Dos pestañas: el cambio llega por delta sin recargar; las tarjetas expandidas no se reemplazan.

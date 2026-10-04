# AGENTS.md — Guía para asistentes de IA

Guía de contexto y reglas para trabajar sobre **Control Horario** ubicado en
`/home/dmlan/Documentos/Default Project/horarios-app/` (raíz del proyecto).

## Qué es el proyecto

Aplicación web self-hosted, en español, para registrar la **jornada laboral**:
entradas/salidas diarias con franja de **mañana/tarde**, **descuento de
interrupción de comida**, **% de teletrabajo**, **objetivo semanal reducido por
días marcados como no trabajados** (festivo/vacaciones/asuntos propios restan
la jornada completa; los días sin registrar NO reducen el objetivo) con
**objetivo de jornada de tarde según días laborables disponibles** según días
efectivamente trabajados, marcajes especiales (festivo, vacaciones, asuntos
propios) y gestión de copias de seguridad de la base de datos desde Ajustes.

### Stack

- Python 3.12, FastAPI, SQLAlchemy 2.x, SQLite (por defecto `sqlite:///./horarios.db`), Jinja2, bcrypt.
- Frontend: HTML/CSS/JS vanilla (sin frameworks). `templates/*.html` renderizadas por FastAPI.
- Dependencias fijadas en `requirements.txt`: fastapi==0.115.0, uvicorn==0.30.6, sqlalchemy==2.0.35, python-multipart==0.0.12, jinja2==3.1.4, bcrypt==4.2.0, psycopg2-binary==2.9.10 (driver Postgres, también se instala en local con SQLite sin problema).
- Tests (solo dev, en venv, fuera de requirements): pytest 9.1.1 + httpx 0.28.1.

## Estructura de archivos

- `main.py` — todo el backend: rutas, modelos Pydantic, lógica de negocio, plantillas.
- `models.py` — tablas `User`, `Entry`, `Setting`, `UserSetting`, `AuthSession` (`sessions`), `Invite` (`invites`: `code`, `created_by`, `used`, `used_by`, `created_at`).
- `database.py` — `DATABASE_URL` (env, default `sqlite:///./horarios.db`), `engine`, `SessionLocal`, `Base`, `get_db()`.
- `templates/` — `base.html`, `login.html`, `dashboard.html`, `calendar.html`, `history.html`, `reports.html`, `settings.html`.
- `static/css/style.css` — todo el CSS (cache-busting en `base.html`).
- `static/manifest.json` — PWA: `name` y `short_name` = `APP_NAME` ("Control de horario AGE"), `start_url: /dashboard`, iconos `/static/icon-192.png` y `/static/icon-512.png`. En iOS el nombre de la app instalada sale de `<meta name="apple-mobile-web-app-title">` en `base.html` (mismo valor). Cambiar el nombre de la PWA obliga a subir `CACHE_NAME` en `static/js/sw.js` (`horarios-v3`), porque el SW cachea todo `/static/` (cache-first) y las apps ya instaladas no verían el manifest antiguo.
- `static/js/app.js` registra el service worker; `static/js/sw.js` solo cachea `/static/` con `CACHE_NAME = 'horarios-v3'`.
- `static/favicon.svg` (favicon principal, cronómetro azul), `static/favicon.png` (32x32, fallback rasterizado), `static/apple-touch-icon.png` (180x180). Todos referenciados desde el `<head>` de `base.html`. Los PNG se generaron con un rasterizador propio en Python puro (sin Pillow); si hay que regenerarlos/rediseñarlos, reproducir el script inline desde el historial (SVG mano + encoder PNG zlib/struct con supersampling x4).
- `test_app.py` — suite de 82 tests (pytest + TestClient).
- `test_report.txt` — informe de resultados (regenerar tras cada cambio en tests).
- `ControlHorario.bat` (Windows) y `ControlHorario.sh` (macOS/Linux) — scripts todo-en-uno para que USUARIOS NO TECNICOS instalen y arranquen la app con doble clic: detectan Python (3.9+), crean `.venv`, `pip install -r requirements.txt`, buscan puerto libre 8000–8010 y abren el navegador en el puerto elegido. No usar `.bat` en dev Linux.
- `README.md` — guía guiada para no técnicos (instalación, PIN, móvil en LAN, problemas comunes, backups, despliegue Render, sección desarrolladores). Mantener al día con la app.
- `render.yaml` — despliegue opcional en Render.
- `horarios.db` — BD real activa. `horario.db` — obsoleto/no usar.

## Modelo de datos

`Entry` (tabla `entries`): `id`, `date` (Date, index), `time_in` (Time), `time_out` (Time, nullable = jornada abierta), `entry_type` (string 20, default `presencial`), `notes`, `created_at`, `updated_at`.

Tipos de marcaje: `presencial`, `teletrabajo`, `festivo`, `vacaciones`, `ap` (asuntos propios), `otro`.
- `SPECIAL_TYPES = {"festivo","vacaciones","ap","otro"}`
- `NON_WORK_TYPES = {"festivo","vacaciones","ap"}` → en `/api/week`: `worked_days` = días con registro real (`entry_type` NO en `NON_WORK_TYPES`, p.ej. presencial/teletrabajo/otro); `special_days` = días marcados como NO trabajados (festivo/vacaciones/ap); `non_worked_days = special_days` (SÓLO marcados, NO días vacíos).

`Setting` (tabla `settings`, clave-valor): `weekly_hours` (por defecto 35), `telework_pct`, `default_telework_days` (p.ej. "0,2"), `lunch_start`, `lunch_end`, y objetivos de tarde `obj_tarde_5d`…`obj_tarde_1d`.

## Multi-usuario (sesiones, cuentas e invitaciones)

La app usa **autenticación propia** (no Supabase/terceros): cada request autenticado lleva la cookie `auth_token` con un **token aleatorio** (`secrets.token_hex(16)`) almacenado en la tabla `sessions` (`AuthSession`).

- **Usuarios**: `users.id`, `users.username` (único, sin acentos/tildes en el nombre), `users.pin_hash` (bcrypt), `users.is_owner`. El **dueño** es el único con `is_owner=True`; se garantiza al arrancar (`init_db`): si no existe, el primer usuario (o uno nuevo `admin` con `PIN_DEFAULT`) pasa a ser dueño. Si una BD antigua solo tenía un `User` anónimo, se convierte en dueño con username `admin`.
- **Login**: `POST /api/login` `{username, pin}` → crea sesión y setea cookie 30 días. `GET /api/logout` borra la sesión y redirige a `/login`. `POST /api/change-pin` cambia el PIN **del usuario logueado**. `GET /api/me` → `{username, is_owner}`.
- **Registro por invitación**: `POST /api/register` `{code, username, pin}` — requiere código de invitación válido y sin usar (`invites`). Crea usuario normal y hace auto-login. Pagina `/register?code=...` (creada por el dueño en Ajustes → Cuentas).
- **API de administración (solo dueño, 403 en otro caso)**: `GET/POST /api/users` (listar/crear), `POST /api/users/{id}/reset-pin`, `DELETE /api/users/{id}` (borra también sus `entries`, `user_settings`, `sessions` e `invites` creadas; el dueño nunca se borra). `GET/POST /api/invites` (listar/crear → devuelve `code` + `url`; el listado incluye `used`, `used_by` y `created_at`), `DELETE /api/invites/{code}`.
- **Aislamiento por usuario**: `_entry_query(db, user)` = `or_(Entry.user_id == user.id, Entry.user_id.is_(None))`. Los **festivos nacionales** se siembran con `user_id=None` y son **compartidos** y visibles por todos; el resto de registros se crean con `user_id=usuario` (en `manual_entry`, `entry_type == "festivo"` → `user_id=None`). `_validate_day_mark(..., user_id=)` solo choca contra entradas visibles del usuario. `_entry_query` + filtros por id hacen que PUT/DELETE den 404 si el registro no es tuyo.
- **Ajustes por usuario**: `get_setting(db, key, default, user_id)` lee primero `user_settings` (tabla `UserSetting`) y si no hay fila cae a `settings` (del dueño); `get_schedule(user_id)` y `get_telework_pct(user_id)` van por igual. `POST /api/settings`: el dueño escribe en `settings`, los demás en `user_settings`. `/api/week`, `/api/day`, `/api/today`, `/api/entries`, `/api/report/monthly`, `/api/export/csv` usan `user.id` para schedule/telework y entradas.
- **Guardar/Restaurar y Clear**: `clear` y el apartado "Base de Datos" de Ajustes son **solo del dueño** (403 en otro usuario). El JSON portable (`horarios-json-1`) incluye **todas** las tablas (también `users`, `sessions`, `invites`, `user_settings`), por lo que un restore completo trae también cuentas y sesiones (los tokens guardados siguen valiendo; `auth_token` se valida contra `sessions`).

## Horario y lógica de negocio

`SCHEDULE_DEFAULTS` (main.py):
- `morning_start 07:00`, `morning_end 14:30`, `afternoon_start 14:31`, `afternoon_end 19:00`, `lunch_start 16:00`, `lunch_end 16:30`.
- Objetivo de jornada de tarde por días trabajados: `obj_tarde_5d 03:00`, `_4d 02:30`, `_3d 01:45`, `_2d 01:00`, `_1d 00:00`. `get_schedule()` los devuelve como objetos `time` (`key="obj_tarde_{i}d"` con `range(1,6)`).

Reglas clave:
- La jornada validada solo dentro del tramo `morning_start`–`afternoon_end` (`validate_work_window`).
- **Comida** (`lunch_deducted_minutes`): **opcional** — `lunch_start`/`lunch_end` (`LUNCH_KEYS`) admiten valor vacío; `get_schedule` los devuelve como `None` (`tm_optional`) cuando están vacíos o no parsean, y entonces no se descuenta nada (ni en `/api/day` ni en `/api/week`). Si hay valores: si `time_out < lunch_end` no se descuenta nada; si no, `minutos(lunch_end) − minutos(max(time_in, lunch_start))`, clamp a >= 0. Si solo se rellena uno de los dos, tampoco se descuenta (basta que falte uno). `POST /api/settings` guarda `""` para los campos de comida vacíos (en `settings`/`user_settings`) y sigue devolviendo 400 "Formato de hora incorrecto (use HH:MM)" si se mandan vacíos los horarios de mañana/tarde u objectives, o una hora invalida. Los default (`16:00`/`16:30`) solo se aplican si el usuario nunca ha guardado ese ajuste; en Ajustes el campo se rellena con `s.lunch_start || ''` (sin fallback) para que lo vaciado se vea vacio.
- Cortes de franja en `morning_end` (14:30) en ambos subtipos: `entry_split` (NET, semanal/today/entries) y `entry_split_gross` (GROSS, `/api/day`). `entry_net` = mañana + tarde (sin comida). Ejemplo 07:00–19:00: net 11.5h, split m7.5 / a4.0 / l0.5, gross m7.5 / a4.5 / l0.5.
- `afternoon_objective_hours(days_worked, sch)`: 0→0, 1→0, 2→1.0, 3→1.75, 4→2.5, 5→3.0 (minutos desde `obj_tarde_{n}d`), fuera de rango→0.
- Cálculo semanal (`/api/week`): `days_worked` (respuesta) = días con registro NO en `NON_WORK_TYPES`; `non_worked_days = special_days` (SOLO días marcados festivo/vacaciones/ap); cada día marcado como no trabajado resta **la jornada completa**: `day_discount = non_worked_days × (weekly_hours / 5)`; `adjusted_target = max(0, weekly_hours − day_discount)`; `remaining = max(0, adjusted_target − total_week)`. Los días sin registrar NO reducen el objetivo. El objetivo de jornada de tarde (`afternoon_target`/`afternoon_objective`, NUEVA clave `afternoon_available_days`) se prorratea con `obj_tarde_{afternoon_available_days}d` donde `afternoon_available_days = 5 − non_worked_days` (días laborables; NO depende de lo trabajado). `afternoon_reduction` = `day_discount`. Los objetivos presencial/teletrabajo se derivan de `adjusted_target` y `telework_pct`. **Totales en minutos exactos**: todos los acumuladores semanales/diarios se agregan en minutos enteros por día (`round(valor*60)`, por componente mañana/tarde/comida y por día) — `fmt_minutes` para las tarjetas y `total_week`/`total_morning`/… = minutos/60 — de modo que **el total de semana coincide exactamente con la suma de las tarjetas de cada día** (evita desfases de redondeo float, p.ej. 38h00 vs 37h57). `fmt_hours(h)` = `fmt_minutes(round(h*60))` (redondea al minuto, no trunca; no usar `int()` sobre el fracción porque p.ej. 1103/60 → 22.999 daría 22 en vez de 23). Ejemplos verificados con BD real: semana 2026-09-21 (Lunes y Martes con horas, restante vacío) → days_worked 2, laborables 5, objetivo tarde 3h, target 35h, quedan 16h37; semana del 12/10/2026 (festivo) → laborables 4, objetivo tarde 2h30, target 28h, reducción 7h. En el dashboard, la fila "Total semana" del detalle muestra la línea **"Interrupcion comida descontada: -X"** cuando `lunch_deducted_week > 0` (elemento `weekDetailLunchRow`; el antiguo `weekLunchRow` de la cabecera se eliminó por decisión del usuario).

Festivos nacionales (España, solo lunes-viernes): `national_holidays_for_year()` (9 fijos + Viernes Santo vía `easter_sunday`). `seed_national_holidays()` siembra en `init_db()` para `año_actual−1 … +3`.

## Autenticación

- Multi-usuario con login por **nombre + PIN** (ver sección "Multi-usuario"). PIN por defecto del dueño `1234` (`PIN_DEFAULT`). Login: `POST /api/login` con `{"username": "admin", "pin": "1234"}`; setea cookie `auth_token=<token de sesion>` (30 días, guardado en `sessions`). Logout: `GET /api/logout`. Cambio de PIN: `POST /api/change-pin`.
- `check_auth(request, db)` → usuario de BD o 401 "No autenticado" si no hay cookie válida.
- Los endpoints de horas exigen auth; `/api/login`, `/api/register`, `/login`, `/register` y `/healthz` son públicos.

## Endpoints principales

Páginas: `/` (redirect a /dashboard), `/login`, `/dashboard`, `/calendar`, `/history`, `/reports`, `/settings`. `/healthz` — endpoint público de salud (`{"ok":true}`, sin auth) usado por Render (`healthCheckPath`).

API:
- `POST /api/login` — `{username, pin}` → sesión (cookie `auth_token` 30 días).
- `POST /api/register` — `{code, username, pin}` → crear cuenta con código de invitación y entrar.
- `GET /api/logout`, `GET /api/me`.
- `GET/POST /api/users` (dueño), `POST /api/users/{id}/reset-pin`, `DELETE /api/users/{id}`, `GET/POST /api/invites`, `DELETE /api/invites/{code}` (dueño).
- `POST /api/entries/manual` — crear jornada (valida ventana, día especial, duplicados). Body `{date, time_in, time_out?, entry_type?, notes?}`.
- `PUT /api/entries/{id}` y `DELETE /api/entries/{id}` — editar/borrar.
- `GET /api/week?week_start=YYYY-MM-DD` — resumen semanal (5 días laborables). Claves respuesta importantes: `days[]`, `days_worked`, `non_worked_days`, `afternoon_available_days`, `target`/`target_formatted` (ajustado), `target_base`, `afternoon_reduction(_formatted)`, `afternoon_target(_formatted)`, `remaining`, `total_week`, `morning_hours`, `afternoon_hours`, `lunch_deducted_week`, `presencial/teletrabajo_hours(_formatted/_pct/_target)`, `afternoon_objective_hours(_formatted)`.
- `GET /api/day?date_str=` — resumen del día (desglose GROSS): `morning`, `afternoon`, `total_net`, `total_gross`, `lunch_deducted`, `entry_type`, `shift`, `is_today`, etc.
- `GET /api/today` — igual pero del día actual (NET).
- `GET /api/entries?month=&year=` — lista de `{id, date, time_in, time_out, total_hours, entry_type, shift, notes}` de un mes.
- `GET /api/report/monthly?month=&year=` — informe mensual.
- `GET /api/export/csv?month=&year=` — CSV.
- `GET/POST /api/settings` — leer/guardar ajustes. `SettingsUpdate` acepta (todos opcionales): `weekly_hours`, `telework_pct`, `default_telework_days`, `morning_start`, `morning_end`, `afternoon_start`, `afternoon_end`, `lunch_start`, `lunch_end`, `obj_tarde_5d`…`obj_tarde_1d`.
- `POST /api/change-pin`.
- `POST /api/login`, `POST /api/register`, `GET /api/logout`, `GET /api/me`.
- `GET/POST /api/users`, `POST /api/users/{id}/reset-pin`, `DELETE /api/users/{id}`, `GET/POST /api/invites`, `DELETE /api/invites/{code}` — solo dueño.

## Sección "Base de Datos" (Ajustes)

Tres operaciones (todas exigen auth):

1. `POST /api/database/clear` — **Eliminar base de datos**. Borra SOLO `Entry.entry_type != "festivo"`. **Los festivos nunca se borran desde esta opción** (regla explícita del usuario); no hay re-siembra. No toca `settings` ni `users`. Respuesta `{ok, deleted, festivos_preservados}`.
2. `GET /api/database/backup` — **Guardar**: dos formatos según el motor de BD:
   - **SQLite** (`engine.dialect.name == "sqlite"`): snapshot binario vía `sqlite3 .backup()` → gzip → `backup_horarios_YYYYMMDD_HHMMSS.db.gz` (comportamiento original, retrocompatible).
   - **Cualquier otro motor (p.ej. Postgres/Supabase/Neon)**: `_backup_json()` — snapshot **portable JSON** (`{"format":"horarios-json-1", "tables": {"<tabla>": {"columns":[...], "rows":[[...]]}}, ...}`) con **reflexión SQLAlchemy** (`Table(t, MetaData(), autoload_with=engine)`), fechas en ISO, gzip → `backup_horarios_YYYYMMDD_HHMMSS.horarios.gz`.
3. `POST /api/database/restore` — **Restaurar**: multipart `file`; descomprime gzip (magic `\x1f\x8b`); si es binario SQLite (magic `SQLite format 3\0`) solo restaura cuando el motor es SQLite (`.db.gz` antiguo): valida `PRAGMA integrity_check`, `os.replace` + `init_db()`. Si es JSON (`horarios-json-1`): restaura en **cualquier motor** (SQLite o Postgres): borra tablas en orden topológico inverso de FKs (`_topo_table_order`) y reinserta filas con IDs explícitos (`_value_for_column` convierte ISO→date/time/datetime); en Postgres además rescata las secuencias con `pg_get_serial_sequence`/`setval`. Límite 100 MB, 400/500 si inválido. `_sqlite_path()` responde 501 si no hay archivo SQLite.
4. **Backups rapidos gestionados** (nuevo, solo dueño): `POST /api/database/backups/quick` guarda en `BACKUP_DIR` (env `HORARIO_BACKUP_DIR`, por defecto `backups/` junto a la app) un snapshot JSON portable gzip `backup_YYYYMMDD_HHMMSS.horarios.gz`. `GET /api/database/backups` lista (name, size, created), `POST /api/database/backups/{name}/restore` restaura desde el archivo (misma lógica JSON que el upload), `GET /api/database/backups/{name}/download` descarga el archivo y `DELETE /api/database/backups/{name}` lo borra. Nombres validados con `BACKUP_NAME_RE` + `_safe_backup_path` (anti path traversal). Aviso: en Render el disco es efímero; los backups de la lista se pierden al recrear el servicio si no se descargan antes.

Frontend (`settings.html`): sección con 3 botones + input file oculto (`restoreFile`). Modal de borrado con checkbox de confirmación **visible** (clase `.clear-check`, NO `.day-check` porque su CSS hace `input{display:none}`) + botón rojo que se muestra gris cuando está deshabilitado (`.btn:disabled`). Modal de guardado con "Guardar en el equipo" y "Enviar por email" (usa `window.location` + `mailto:`; no hay SMTP). El borrado/restauración recargan o muestran toast (`showToast`, definido en settings.html).

## Reglas y convenciones

- **Mantener AGENTS.md al día**: cada vez que se modifique algo relevante del proyecto (nuevos endpoints, cambios de lógica/negocio, modelos, tests, comandos, estructura de archivos, dependencias, despliegue), actualizar este documento en la misma tanda de cambios. Verificar al terminar que sigue siendo fiel al código real.
- **Mantener al día también `README.md` y los scripts de instalación** (`ControlHorario.bat`/`.sh`) si cambian dependencias, puerto, requisitos o comportamiento de arranque: son la puerta de entrada para usuarios no técnicos.
- **Texto de UI en español SIN acentos** ("Manana", "Tarde", "Interrupcion comida", "No autenticado"). Mantenerlo.
- **No añadir comentarios en el código** salvo que se pidan.
- **Cache-busting CSS**: al tocar `static/css/style.css`, subir `?v=` en `templates/base.html` (actual `20260911s`). El usuario debe recargar con Ctrl+F5.
- **Nombre y versión de la app**: `main.py` define `APP_NAME = "Control de horario AGE"` y `APP_VERSION = "1.0"`, expuestos a todas las plantillas con `templates.env.globals.update(...)`. La versión se muestra en Ajustes al final, a la derecha y en pequeño (`<div class="app-version">{{ APP_NAME }} v.{{ APP_VERSION }}</div>`, clase `.app-version` en `style.css`). Al subir la versión hay que tocar `APP_VERSION` (y el manifest si cambia el nombre).
- **Menu de usuario = menu de navegación (un solo menu)**: ya NO existen `.nav-links`, `.nav-toggle` (3 rayitas) ni la función `toggleNav()`; las 5 plantillas solo llevan `<nav class="navbar">` con el `.nav-brand`. `base.html` inyecta al final de `.navbar` un chip (`.nav-user`, con `margin-left:auto`) con el nombre de usuario (`.nav-user-name`) y un desplegable (`.nav-user-menu`) que contiene **todas las secciones** (`sections` = Inicio, Calendario, Historial, Informes, Ajustes; cada una `<a class="menu-link">`, con `active` si coincide con `window.location.pathname`) y al final **"Cerrar sesion"** (`<a class="menu-logout">` → `/api/logout`, separada con `border-top`). Se construye tras consultar `/api/me` (si no hay sesión no se inyecta nada). No duplicar el chip en las plantillas (settings.html no lleva `#navUser`); `loadMe()` en settings.html solo se usa para saber si el usuario es dueño (muestra/oculta "Cuentas" y "Base de Datos"). Si se añade una sección nueva, añadirla a `sections` en `base.html`.
- **Listado de invitaciones (Ajustes)**: `invites.used_by` (VARCHAR(80), nullable) guarda **el nombre del usuario que se registro con ese codigo**; se rellena en `POST /api/register` (`invite.used_by = username`) y se anade a las BD antiguas con `_add_column_if_missing("invites", "used_by VARCHAR(80)")` en `init_db()` (las invitaciones usadas ANTES de esta columna se ven con `used_by = null`, la UI muestra "-"). En `settings.html` el listado esta **oculto por defecto**: boton de texto pequeno `.link-toggle` (`#inviteToggle`, "Mostrar invitaciones" <-> "Ocultar invitaciones", funcion `toggleInviteList()`) que alterna `#inviteList` (`display:none` inicial). `loadInvites()` lo rellena con una **tabla `.invite-table`** de columnas **"Codigo invitacion" | "Quien lo ha usado" | "usado (S/N)"** + celda de accion: las invitaciones **usadas** muestran solo codigo + quien las uso (**sin la celda "usado (S/N)"**) y las **sin usar** muestran codigo, "-", "N" y el boton "Borrar". Ya no existen el `<ul>`/`.invite-list li` ni la clase `.used-tag`. Todo texto que venga de la BD se pasa por el helper `esc()` definido en `settings.html`.
- **Título de la barra como enlace**: el `.nav-brand` ("Control Horario") es un `<a href="/dashboard">` en las 5 plantillas con barra (`dashboard`, `calendar`, `history`, `reports`, `settings`) y `.nav-brand` lleva `text-decoration:none` para no verse subrayado. Es el único elemento de la barra ademas del chip de usuario. Si se añade una página con navbar, replicarlo como enlace.
- **Fichas superiores del dashboard (estilo compacto pero coherente)**: se mantienen las **3 cajas separadas** ("Hoy", "Esta semana", "Quedan") en grid `.summary-cards` (borde fino, radio 8px, sin sombra, acento lateral en la central/`remaining-card`, padding 10px 12px). Contenido **centrado horizontal y verticalmente** (`text-align:center` + `display:flex; flex-direction:column; justify-content:center` en `.summary-card`). Reparto: "Esta semana" = total + Manana/Tarde (`weekBreakdownWrap`, gap 8px, centrado); "Quedan" = `targetInfo`, `weekDaysCount`, `weekAfternoonObj` (con su razón `(X dias laborables)` en **línea aparte** vía `.summary-cards .afternoon-obj-reason{display:block;margin-left:0}`) y `weekAfternoonDiscount`. La línea de descuento por días no trabajados en la ficha derecha muestra SOLO la etiqueta **"Jornada reducida por dias no trabajados" en rojo** (`.discount-label`, `var(--danger)`) + razón `(X dias no trabajados)`; **no muestra horas/minutos** (se eliminó `weekAfternoonDiscountVal`). La línea de comida descontada en la CABECERA se ha **eliminado** (`weekLunchRow` ya no existe); solo queda como fila `weekDetailLunchRow` bajo "Total semana" del detalle. En móvil (<640px) una columna.
- JS de cada página está embebido en su propia plantilla (block `scripts`); `app.js` solo registra el SW.
- **Backups**: antes de operar destructivamente sobre `horarios.db` en pruebas, sacar copia (p.ej. `POST /api/database/backup`). Nunca probar `clear` contra la BD real si contiene datos del usuario.
- No usar `pkill`/`kill $(pgrep ...)` en comandos bash del agente (auto-matchea el propio PID y da error). Usar `fuser -k 8767/tcp`.
- Respetar la semántica de `NON_WORK_TYPES` al tocar semanas: `days_worked` = días con registro NO en `NON_WORK_TYPES` (días vacíos no cuentan); `non_worked_days`/reducción = SOLO días marcados festivo/vacaciones/ap.
- `entry_type` puede faltar en BDs antiguas; `init_db()` la añade usando `inspect(engine)` (SQLAlchemy, compatible SQLite/Postgres; nunca usar `PRAGMA` fuera de SQLite, rompería Postgres).
- **Migraciones `ALTER TABLE`**: `_add_column_if_missing(table, column_def)` devuelve `True` si ha añadido la columna, `False` si ya existía o si falló, y **nunca propaga la excepción** (todo envuelto en `try/except` + `print`): una migración fallida NO debe tumbar el arranque en Render (si falla, el deploy termina en *Application startup failed*). Los valores por defecto deben ser **válidos en SQLite y Postgres**: para booleanos usar `DEFAULT FALSE` (NUNCA `DEFAULT 0`; Postgres lanza `DatatypeMismatch: column "is_owner" is of type boolean but default expression is of type integer`). Columnas migradas: `entries.user_id`, `entries.entry_type`, `users.username`, `users.is_owner`, `invites.used_by`.

## Comandos de trabajo

En la raíz del proyecto:

```bash
# Servidor (puerto 8767, dev)
./venv/bin/python3 -m uvicorn main:app --host 127.0.0.1 --port 8767 &

# Alternativa: python main.py (puerto 8000 por defecto o el de env PORT)
# APP_RELOAD=1 habilita recarga; __main__ usa uvicorn.run("main:app", host 0.0.0.0, port=PORT)

# Reinicio limpio
fuser -k 8767/tcp

# End users: scripts ControlHorario.bat / .sh -> crean .venv, instalan, puerto libre 8000-8010, abren navegador

# Tests (suite completa, 82)
./venv/bin/python3 -m pytest test_app.py -v

# Verificar endpoints con curl (cookie de sesión en /tmp/c.txt)
curl -s -c /tmp/c.txt -X POST http://127.0.0.1:8767/api/login -H "Content-Type: application/json" -d '{"username":"admin","pin":"1234"}'
curl -s -b /tmp/c.txt http://127.0.0.1:8767/api/week
```

Tras modificar tests, **regenerar `test_report.txt`** con la salida completa de `pytest -v`. Si se toca `README.md`, scripts de instalación o `AGENTS.md` en sí, mantenerlos coherentes (reglas de la sección siguiente).

## Tests (test_app.py)

- BD de test aislada: `DATABASE_URL=sqlite:////tmp/horario_test.db` (variable seteada antes de importar `main`); `init_db()` manual; se borra el archivo al inicio si existe. `init_db` crea al dueño `admin` con PIN `1234`.
- `client` (TestClient que re-autentica en cada llamada) y `anon` (sin cookie, para pruebas de 401).
- `auth_headers(username="admin", pin="1234")` → hace **login real** (POST `/api/login`) y devuelve `{"Cookie": "auth_token=<token>"}`.
- Fixture autouse `clean_db` borra tablas `entries`, `settings`, `user_settings`, `sessions` e `invites` al final de cada test (no borra `users`; el dueño persiste). También vacía `BACKUP_DIR` (aislado en `/tmp/horario_backups_test` vía env `HORARIO_BACKUP_DIR` en el propio test).
- Helpers: `_make(date, t_in, t_out, etype)`, `_sch()`, `_seed_week()` (semana 2027-06-07: Lunes festivo + 3 jornadas, Viernes vacío), `_seed_week_festivo_4trabajados()` (Lunes festivo + M-J a 5h) para el cálculo semanal con reducción, `_create_user(username, pin="2222", via_invite=False)`.
- Verificaciones numéricas clave: 5 días→35h; semana festivo+4 trabajados→target 28h/reducción 7h/obj tarde 2h30 (4 laborables); semana con festivo y 3 trabajados (V vacío)→target 28h/obj tarde 2h30; semana normal vacía→0 días trabajados pero target 35h y obj tarde 3h; semana a medias (2 días)→"2 dias trabajados"/target 35h/obj tarde 3h; weekly_hours 40 + festivo→32h/8h; dos días marcados→21h/14h; `07:00–19:00`→net 11.5h; consistencia de redondeo: `total_week_formatted` == suma de los `total_formatted` de los días (incluso con entradas con segundos, `test_week_total_coincide_con_suma_de_tarjetas_de_dia`).
- Categorías cubiertas: login/auth, multi-usuario (crear cuenta dueño, reset PIN, invitaciones, registro válido/inválido, migración de columnas `test_migracion_columna_es_idempotente_y_no_rompe`, aislamiento de ajustes por usuario, aislamiento de registros entre usuarios, borrar usuario, no borrar dueño, 403 a no-dueños), páginas (incluye `test_nav_brand_links_to_dashboard`, `test_menu_usuario_contiene_secciones_y_logout`, `test_invites_list_includes_used_by`, `test_settings_lista_invitaciones_oculta_por_defecto`, `test_settings_shows_app_version` y `test_pwa_name_is_control_de_horario_age`), settings, entradas CRUD + validaciones, unidades de horario (comida, comida vacía sin descuento `test_lunch_vacio_no_descuenta`/`test_guardar_comida_vacia_sin_descuento`/`test_comida_solo_un_campo_vacio_no_descuenta`/`test_horario_invalido_sigue_dando_error`, net/split/gross, objetivo, turnos), semana prorrateada, day/today, listado mensual, informe, CSV, PUT ventana, y Base de Datos (clear preserva festivos+settings, backup gzip, restore roundtrip, archivos inválidos, auth, JSON portable, backups rapidos: crear/listar, restore roundtrip, borrar, 403/auth y path traversal, descarga).

## Despliegue (Render)

- `render.yaml` (Blueprint): crea **dos** recursos — `databases: control-horario-db` (Postgres, `plan: free`) y web service `plan: free` con `startCommand uvicorn main:app --host 0.0.0.0 --port $PORT`, `healthCheckPath: /healthz` y `DATABASE_URL` vía `fromDatabase`. `APP_PIN` queda `sync: false` (se define en el dashboard). Flujo usuario: GitHub repo público → Render → New → Blueprint → pegar/URL del repo → Apply. El README lo explica paso a paso para no técnicos.
- El disco de Render es **efímero**: en producción hay que usar Postgres (no SQLite). `database.py` lee `DATABASE_URL` (mapea `postgres://`→`postgresql://`; `check_same_thread` solo para SQLite).
- Free tier (verificado 2026): web gratis se duerme a los 15 min sin tráfico (~1 min de cold start); Postgres gratis = 1 GB y **caduca a los 30 días** (14 días de gracia). Para datos permanentes gratis: Neon/Supabase (URL externa en `DATABASE_URL`). 750 horas/mes gratuitas; servicios dormidos no consumen horas.
- Guardar/Restaurar funciona en **SQLite y Postgres** (formato JSON portable `.horarios.gz` en Postgres; `.db.gz` binario SQLite en local es retrocompatible y también se aceptan JSON en SQLite). Con Postgres (free: Supabase/Neon) los backups externos (botón "Guardar") siguen disponibles; los auto-backups son los del propio proveedor de BD. `init_db()`/`entry_type` compatible SQLite+Postgres.
- Cualquier cambio en arranque/BD (p.ej. nueva tabla) debe seguir siendo compatible con SQLite y Postgres; no usar PRAGMA/sqlite3 fuera de la sección Base de Datos.

## Pautas de trabajo recomendadas

1. Entender el contexto antes de tocar (`/api/week`, `entry_split*`, objetivos) para no romper el prorrateo.
2. Implementar backend + frontend, añadir tests para cada nueva funcionalidad y correr el suite completo.
3. Regenerar `test_report.txt` y subir `?v=` del CSS si aplica.
4. Verificar en el servidor real (curl + web) sin destruir datos reales (usar `/tmp` para copias).
# Control Horario

Aplicacion web **local y gratuita** para controlar tus horas de trabajo: fichajes de entrada/salida, registro de manana y tarde, teletrabajo, objetivos semanales, calendario, historial e informes. Funciona en **Windows, macOS y Linux**, y puedes abrirla tambien desde el **movil** en la misma red WiFi.

No necesitas conocimientos tecnicos: hay un script que instala y arranca todo solo.

---

## Instalacion (paso a paso)

### 1. Requisito unico: instalar Python

La aplicacion necesita Python 3.9 o superior (recomendado 3.11 o 3.12).

- **Windows**: abre https://www.python.org/downloads/ y pulsa el boton amarillo de descarga. Al instalarlo, **marca la casilla "Add Python to PATH"** (importante) y pulsa "Install Now".
- **macOS**: abre https://www.python.org/downloads/ y descarga el instalador.
- **Linux** (si no lo tienes): en la terminal ejecuta
  `sudo apt install python3 python3-venv` (Debian/Ubuntu) o el equivalente de tu distribucion.

Si ya tienes Python instalado, salta este paso.

### 2. Instalar y arrancar la aplicacion

Descomprime la carpeta de la aplicacion (o copiala) donde quieras, por ejemplo en tu Escritorio o Documentos. Despues:

- **Windows**: haz **doble clic** en el archivo **`ControlHorario.bat`**.
- **macOS y Linux**: haz **doble clic** en el archivo **`ControlHorario.sh`** (si no se abre, en macOS haz clic derecho -> Abrir; en Linux abre una terminal dentro de la carpeta y ejecuta `./ControlHorario.sh`).

El script hace todo solo:
1. Crea un entorno privado de la aplicacion (no toca nada mas del sistema).
2. Instala las dependencias automaticamente (solo la primera vez).
3. Arranca el servidor y abre tu navegador en la aplicacion.

### 3. Usar la aplicacion

- PIN por defecto: **1234**.
- Una vez dentro, puedes cambiar el PIN desde **Ajustes -> Cambiar PIN**.
- No cierres la ventana negra (Windows) / terminal mientras uses la aplicacion; cerrarla detiene la app. Para detenerla tambien puedes pulsar `Ctrl+C`.

---

## Abrir la app desde el movil

Con el servidor en marcha, el movil (conectado al mismo WiFi) puede usarla:

1. Averigua la IP de tu PC: en Windows `ipconfig`, en macOS/Linux `ip addr` o `ifconfig`. Suela ser algo como `192.168.1.10`.
2. En el movil abre el navegador y entra en `http://IP_DE_TU_PC:8000` (por ejemplo `http://192.168.1.10:8000`).
3. Puedes crear un acceso directo desde el navegador para que parezca una aplicacion.

> **Windows**: si el movil no conecta, acepta el aviso de **Firewall** que aparece la primera vez (o permite Python en redes privadas de Windows Defender Firewall).

---

## Si algo falla (problemas comunes)

| Problema | Solucion |
| --- | --- |
| "No se encontro Python instalado" | Sigue el paso 1 y vuelve a abrir el script. |
| El puerto 8000 esta ocupado | El script busca un puerto libre automaticamente (8000-8010) y abre el navegador en el correcto. |
| La instalacion tarda mucho | Es normal la primera vez. Las siguientes son casi instantaneas. |
| El navegador no se abre solo | Entra manualmente en `http://127.0.0.1:8000` (o el puerto que indique el script). |
| Quiero empezar de cero / liberar espacio | Borra la carpeta `.venv` (el entorno) y la base de datos `horarios.db`; la app se reinstala sola al abrir el script. |
| Quiero conservar mis datos al mover la app | Copia/destruye junto a la app el archivo `horarios.db`. Tambien puedes hacer copias de seguridad desde **Ajustes -> Guardar base de datos**. |

---

## Datos y copias de seguridad

- Todos los datos se guardan en el archivo **`horarios.db`** (junto a la aplicacion), sin depender de internet.
- En **Ajustes -> Base de Datos** puedes **Guardar** una copia de seguridad (archivo `.db.gz`), **Restaurarla** sobre la app o **Eliminar** los registros conservando los festivos y ajustes.
- Al actualizar la aplicacion por una version nueva, tus datos se conservan (se usan el mismo `horarios.db` y los mismos ajustes).

---

## Uso rapido

1. Abrir la app en el navegador.
2. Introducir el PIN (por defecto: 1234).
3. Fichar entrada/salida con los botones de la pantalla principal.
4. Ver historial, calendario e informes en el menu.
5. Exportar datos a CSV desde el historial.

---

## Usar la app desde el movil sin tener el PC encendido (en la nube)

Esto se llama **desplegar**: la aplicacion se instala en un ordenador de internet (un "servidor") y tu movil entra con una direccion web, como si fuera otra pagina. Pasos para alguien que no ha hecho esto nunca:

### Paso 1: subir el codigo a GitHub (gratis, 10 minutos)

GitHub guarda la copia de la aplicacion en internet.

1. Crea cuenta en https://github.com (puedes usar tu correo de Gmail).
2. Crea un repositorio: boton verde **New** (o `+` → New repository), nombre `control-horario`, marca **Public** y pulsa **Create repository**.
3. En la pagina del repositorio nuevo pulsa **Add file → Upload files**.
4. Sube el contenido de la carpeta `control-horario` del zip `control_horario.zip` (los archivos sueltos: `main.py`, `database.py`, `models.py`, `requirements.txt`, `render.yaml`, `ControlHorario.bat`, `ControlHorario.sh`, `README.md`, mas las carpetas `templates` y `static` completas).
5. Pulsa **Commit changes**.

### Paso 2: crear la aplicacion en Render (gratis)

Render alojara la aplicacion. El archivo `render.yaml` ya esta preparado para que haga casi todo solo.

1. Crea cuenta en https://render.com (puedes registrarte con tu cuenta de GitHub).
2. En el panel pulsa **New → Blueprint**.
3. Conecta tu cuenta de GitHub si te lo pide y selecciona tu repositorio `control-horario`.
4. Render verificara `render.yaml` y te propondra crear dos cosas: la **aplicacion web** y una base de datos **Postgres** (donde se guardan tus horas). Pulsa **Apply** y espera 2-4 minutos.

### Paso 3: entrar desde el movil

1. Cuando termine, veras la URL de la aplicacion (algo tipo `https://control-horario-xxxx.onrender.com`).
2. Entra desde el navegador del movil y anhadela a la pantalla de inicio (menu del navegador → Añadir a la pantalla de inicio).
3. PIN por defecto: 1234 (cambiable en Ajustes). Para elegir el PIN desde el principio: en Render → Environment → añade `APP_PIN` con tu valor y reinicia.

### Limitaciones de lo gratis (importante)

- **La app se duerme tras 15 minutos sin visitas.** Al entrar tarda ~1 minuto en despertar; despues va normal. Para evitar esa espera hay que pagar (Hobby+).
- **La base de datos Postgres gratis caduca a los 30 dias.** Render te avisara antes; despues tienes 14 dias de gracia para hacer upgrade o copiar tus datos. Si quieres datos gratis que no caduquen, usa **Neon** (https://neon.tech) o **Supabase** (https://supabase.com): crea el proyecto, copia la URL de conexion y en Render → Environment cambia `DATABASE_URL` a ese valor y reinicia.
- Los datos viven en Postgres (el disco del servidor es temporal): en la nube no sirven `horarios.db` ni los botones Guardar/Restaurar de Ajustes. Usa los exports CSV o cambia `DATABASE_URL` para mover datos.

### Despliegue manual (alternativa avanzada)

Si prefieres configurarlo a mano sin `render.yaml`: crea un **Web Service** en Render desde el repositorio, Build Command `pip install -r requirements.txt`, Start Command `uvicorn main:app --host 0.0.0.0 --port $PORT`, env `PYTHON_VERSION=3.11` y `APP_PIN`, y conecta una base de datos Postgres con su `DATABASE_URL`.

---

## Desarrolladores

Arranque en desarrollo:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Con `python main.py` tambien funciona (puerto 8000 por defecto, o el de la variable `PORT`).

Tests:

```bash
.venv/bin/python -m pytest test_app.py -v
```

Consulta `AGENTS.md` para el contexto completo del proyecto.
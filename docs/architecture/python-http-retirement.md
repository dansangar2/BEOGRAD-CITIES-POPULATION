# Selección y operación de backends HTTP

Java y Python son backends mantenidos. Java tiene prioridad y es el valor
predeterminado del selector cuando falta configuración. En la revisión documental
del 2026-11-19, [backend.toml](../../backend.toml) selecciona `python`.
`BEOGRAD_BACKEND` prevalece sobre el fichero. Los valores inválidos detienen
el arranque sin activar otro backend.

## Arranque y propietarios HTTP

Desde la raíz:

```powershell
.\levantar_app.bat
.\levantar_app.bat 8080
# Desde otra terminal:
.\levantar_app.bat stop
```

En modo `java`, Django escucha en el puerto público (8000 por defecto) y Java
en el siguiente. `web/java_routing.py` reenvía exclusivamente los cinco nombres
de `JAVA_ROUTE_NAMES`: resumen de países, población del panel, resumen derivado,
datos derivados del panel y exportación CSV de configuración. Java ejecuta esos
casos de uso nativamente, sin llamar a Python. Páginas, detalles, editores,
tareas, `/admin/` e `/i18n/` conservan sus propietarios Django. Si Java falla,
una ruta migrada devuelve error, sin fallback a Python.

En modo `python`, Django sirve todas sus rutas y no se construye ni arranca Java.
Cerrar la ventana termina el árbol propio. Volver a ejecutar el lanzador detiene
la instancia registrada y arranca de nuevo; en modo Java recompila si falta un
artefacto verificado o cambia la huella de fuentes. La parada funciona aunque
haya cambiado la selección. Solo escucha en loopback; es operación local.

## Requisitos y datos

El supervisor requiere Python 3.13+ y solo usa biblioteca estándar. Ambos modos
necesitan las dependencias Django de [requirements.txt](../../requirements.txt)
en `BEOGRAD_PYTHON` o el intérprete del lanzador. Java requiere además JDK 25.
No se instalan dependencias automáticamente.

Ambos reciben `database` y `media_root` de `backend-java/config/local.json`,
o `BEOGRAD_DATABASE` y `BEOGRAD_MEDIA_ROOT`. La base debe existir con ruta
absoluta. El arranque no crea tablas ni ejecuta migraciones o importaciones.
La UI usa `ciudades_del_mundo.local_settings`, sin autoreloader, con estáticos
y media locales. La clave Django procede de `BEOGRAD_DJANGO_SECRET_KEY` o
`.web_django_secret`; el lanzador crea ese archivo local cuando falta en ambos
modos. La JVM no recibe esa clave.

`BEOGRAD_JAVA_ENABLED` es un detalle interno del entorno Django configurado por
el supervisor en modo Java. La selección de usuario se hace con `backend.toml`
o `BEOGRAD_BACKEND`; no exige exportar aquel indicador manualmente.

## Construcción, propiedad y reversión

```powershell
.\levantar_app.bat prepare
.\levantar_app.bat build
.\levantar_app.bat rollback
```

Estas acciones son Java, independientes de la selección. `prepare` verifica JDK,
Wrapper y base existente, y crea la configuración local si falta; no instala
Django ni importa SQL. `build` ejecuta Maven `clean verify`, verifica el JAR y
lo activa con checksum y huella de fuentes, conservando el artefacto anterior.
No ejecuta las suites Django o frontend. La primera construcción puede necesitar
Maven Central. `rollback` restaura el JAR previo con la instancia detenida;
no revierte datos. La reversión se mantiene hasta un nuevo cambio de fuentes.

Se conservan Job Object, bloqueos de propietario/runtime/base, logs acotados y
comprobación de disponibilidad. El lanzador no mata procesos ajenos que ocupen
un puerto. La disponibilidad pública consulta SQL desde Django; la de la JVM
comprueba sus dependencias por separado. Ninguna hace scraping o reparaciones.

## Código y mantenimiento

El backend [Python](../../backend-python/README.md) tiene el mismo estatus de
mantenimiento que Java. Su entrada `backend-python/manage.py` ejecuta los
propietarios activos de [web](../../ciudades_del_mundo/web/), compartidos con la
UI Django. Se conserva una sola implementación Python. Toda mejora HTTP debe implementarse y verificarse
en ambos backends con prioridad Java, conservando contratos compartidos.
La migración Java continúa incompleta. El [inventario](java-api-inventory.md)
se genera desde los propietarios activos y la allowlist del código; es evidencia
estática y no acredita equivalencia en ejecución.

## Verificación offline

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
py -B -m unittest tools.test_backend_selection -v
py -B -m unittest tools.test_java_windows -v
py -B manage.py check --settings=ciudades_del_mundo.tests.isolated_settings
```

Las pruebas Windows usan datos y runtime temporales. Maven usa contratos SQL/HTTP
sintéticos congelados sin importar Django. Las pruebas Django usan settings
aislados y su runner bloquea conexiones externas. Consulta
[el alcance de verificación](operations.md#offline-verification-scope).
Estos comandos son instrucciones reproducibles, no resultados de esta revisión.


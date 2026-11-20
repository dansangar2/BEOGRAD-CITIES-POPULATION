# API Java: migración incremental y operación Windows

**Estado actual (revisión 2026-11-19):** se mantienen Java y Python. En modo
Java, Django escucha en el puerto público y reenvía las cinco rutas migradas
a la JVM del puerto siguiente; el resto permanece en Django. En modo Python
Django sirve sus rutas sin JVM. La referencia operativa es
[selección de backends](python-http-retirement.md).

La migración completa **no está terminada**. Hay cinco rutas implementadas en
Java y caracterizadas contra Django. El resto conserva sus controladores
Python. El [inventario de 107 rutas](java-api-inventory.md) separa páginas,
respuestas JSON, parciales, descargas y rutas mixtas; el
[JSON de evidencia](java-api-inventory.json) registra las entradas, expresiones
de respuesta/error, dependencias, llamadas, consumidores y pruebas encontradas.
Es un inventario estático, no una especificación completa de efectos transitivos.

## Responsabilidades y decisiones comprobadas

| Componente | Responsabilidad actual |
| --- | --- |
| Spring Boot / Java | Aplicación y HTTP nativos para las cinco rutas de abajo; comprobaciones de disponibilidad y logs con identificador de petición |
| JDBC SQLite | Lecturas parametrizadas del esquema Django existente; sin Hibernate, generación de esquema ni migraciones |
| Django | Origen público, HTML, admin, i18n, sesiones, CSRF, estáticos/media y todos los endpoints pendientes |
| Gestor Python existente | Única implementación de cola, ejecución, cancelación, recuperación y progreso de tareas |
| Supervisor Windows | JVM y Django sin autoreloader; puertos internos, propiedad de procesos, disponibilidad, parada y artefactos |

Las rutas Java son `/api/countries/`, `/dashboard/population/`, `/api/derived/`,
`/dashboard/derived/` y `/configs/<slug>/export-data-csv/`. Su lógica de
aplicación no llama a la API Python. El middleware Django contiene una lista
explícita de nombres de ruta y las envía a la JVM, después de la validación
CSRF. El navegador mantiene el mismo origen y las mismas URLs. Las rutas
pendientes siguen ejecutando su implementación original; no son rutas Java.

JDBC permite conservar IDs de texto, relaciones, tablas intermedias y
semántica SQL sin adaptar el esquema a un ORM. La prueba inicial verifica
relaciones, restricciones, rollback y rechazo de escrituras en conexiones de
lectura. La base exige una ruta absoluta existente; se desactiva `CREATE` en
el driver. No se consulta la base real durante las pruebas. El resumen de
países consulta raíces, códigos y traducciones por lotes; no serializa todas
las subdivisiones. No se han añadido índices, cachés ni paralelismo SQL.

Versiones fijadas: JDK 25, Spring Boot 3.5.7, Maven 3.9.11, Wrapper 3.3.4,
SQLite JDBC 3.50.3.0 y TOMLJ 1.1.1. El JDK utilizado aquí es JBR 25.0.4, con
`java` y `javac`. Se consultaron los
[requisitos oficiales de Spring Boot 3.5](https://docs.spring.io/spring-boot/3.5/system-requirements.html)
y la [verificación de checksums del Wrapper](https://maven.apache.org/tools/wrapper/).
No se descarga ni instala globalmente un JDK. Si falta, la preparación explica
el requisito y la ubicación del proveedor. Maven y su Wrapper proceden de Maven
Central; las propiedades fijan SHA-256 de ambos binarios. La preparación y
construcción verifican además el JAR del Wrapper antes de ejecutarlo.

El archivo `backend-java/requirements-local.lock` conserva el lock histórico
con hashes. La preparación vigente no crea un venv ni instala Django. Instala
las dependencias de `requirements.txt` con el intérprete elegido para la UI.
El catálogo Java se genera explícitamente desde los `.mo`
distribuidos y los diccionarios de etiquetas/identificadores en código. Esto
no importa semillas geográficas ni configura la base al arrancar.

## Preparación, construcción y ejecución

Desde PowerShell, en una instalación nueva, indica un JDK 25 completo:

```powershell
$env:BEOGRAD_JAVA_HOME = 'C:\ruta con espacios\jdk-25'
.\levantar_app.bat prepare
.\levantar_app.bat build
$env:BEOGRAD_BACKEND = 'java'
.\levantar_app.bat
```

La preparación crea `backend-java/config/local.json`, ignorado por Git, con
rutas absolutas. Revisa `java_home`, `database` y `media_root`; existe una
[plantilla sin secretos](../../backend-java/config/local.example.json).
`BEOGRAD_JAVA_HOME`, `BEOGRAD_DATABASE`, `BEOGRAD_MEDIA_ROOT` y
`BEOGRAD_STARTUP_TIMEOUT` permiten sobrescribir esos valores. `JAVA_HOME` o
`javac` en PATH también sirven para localizar el JDK. Se comprueban ambos
ejecutables y la versión. La primera preparación/construcción necesita acceso
a Maven Central. El arranque reconstruye Java si cambia la huella de fuentes;
puede usar la caché Maven cuando dispone de todas las dependencias.

El despliegue usa el perfil `ciudades_del_mundo.local_settings`, con DEBUG
desactivado. `settings.py` lee la clave de `BEOGRAD_DJANGO_SECRET_KEY` o
`.web_django_secret`, ignorado por Git. En este espacio se externalizó la clave
existente conservando su valor para mantener sesiones y CSRF. Una instalación
nueva genera una clave local. `BEOGRAD_DJANGO_SECRET_KEY_FALLBACKS` admite una
lista JSON externa de claves anteriores para una rotación deliberada. No se
ha copiado la clave a Java ni a archivos versionados.

Para otro puerto, detén primero la instancia y usa:

```powershell
.\levantar_app.bat 8080
```

Desde otra terminal:

```powershell
.\levantar_app.bat stop
.\levantar_app.bat rollback
.\levantar_app.bat 8080
```

La selección se hace con `backend.toml` o `BEOGRAD_BACKEND`, no exportando
el indicador interno `BEOGRAD_JAVA_ENABLED`. El lanzador usa su propia ubicación,
acepta rutas con espacios y propaga errores. La construcción ejecuta Maven
`clean verify` antes de activar `.runtime/current.jar`; conserva `previous.jar`,
checksums y huella de fuentes. Las pruebas Django y frontend se ejecutan aparte.
La reversión comprueba el artefacto previo y exige
que la instancia esté detenida. **Restaurar el JAR no deshace escrituras SQL.**

El supervisor toma bloqueos de sistema operativo para su runtime y la base
seleccionada. Cada hijo espera a estar asignado a un Windows Job Object antes
de crear descendientes. La pérdida del supervisor cierra el job y termina su
árbol de procesos. No se usa `start` ni se matan procesos por ocupar un puerto.
La JVM escucha en loopback, en el puerto siguiente al público, que debe estar
libre. Se ejecuta con
`java -jar`; Django mantiene el puerto público, con `--noreload --insecure`
para servir estáticos locales con DEBUG desactivado. La parada solicita el
cierre de Spring con un token efímero interno y termina los procesos propios
restantes. Los logs de proceso rotan a 2 MiB con dos copias.

`/internal/live` en Java comprueba proceso vivo. `/internal/ready` comprueba
las tablas imprescindibles sin escrituras; `/internal/ready/` público comprueba
SQL desde Django. El supervisor espera disponibilidad de la JVM por separado,
aplica un plazo y vigila
ambos procesos. Estas comprobaciones no hacen scraping, IA ni reparaciones.

## Contratos y mejoras verificables

`tools/characterize_java_api.py` crea un esquema sintético a partir de modelos,
sin migraciones ni señales de importación. Captura respuestas Django con
datos vacíos/poblados, nueve idiomas, siete métodos HTTP y errores de ausencia
de datos. `EquivalenceTest` compara HTTP real contra la JVM. La matriz incluye
1320 respuestas, IDs Unicode/con barras en el CSV, el límite de 64 bits y
sumas superiores a él, además de valores superiores a 2^53,
TOML inválido, país obtenido del contenido SQL, orden, null, BOM, CRLF, escala
decimal, fechas, cabeceras de descarga y errores HTML. MIME se compara según
su semántica: Tomcat normaliza espacios entre parámetros. Se conserva también
el orden de claves para las huellas de la caché del navegador, sin depender
del orden aleatorio de `Map.of` entre JVM. Esta cobertura no sustituye la caracterización pendiente
de los otros endpoints ni prueba todos los estados posibles de los cinco.
Se prueba también `Accept` HTML, XML, comodín, inválido y ausente: las respuestas
JSON existentes no se convierten en errores 406 de Spring.

El bloqueo Windows Java usa exactamente el byte `[0,1)` del protocolo Python.
`WindowsLockTest` ejecuta Python como otro proceso: comprueba exclusión en
ambos sentidos y readquisición. No afirma compatibilidad con `flock` en POSIX.
La API actual es de solo lectura; esta prueba no habilita escritores Java.

La corrección de SQLite ocupado está separada de equivalencia. El middleware
ahora maneja excepciones de vista mediante `process_exception`, usa códigos
SQLite base/extendidos y la negociación común de `web/http.py`. Tanto
`XMLHttpRequest` como `Accept: application/json` reciben JSON 503; el navegador
conserva la página HTML. Java clasifica códigos 5/6 del driver, con espera
acotada y sin reintentos mutantes. `BusyFailureTest` mantiene un bloqueo SQLite
real y comprueba respuesta JSON y recuperación. Los IDs de petición se añaden
sin registrar cuerpos, cookies ni cabeceras sensibles.

Comandos reproducibles, sin base real ni servicios externos:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\levantar_app.bat build
py -B -m unittest tools.test_java_windows -v
py -B tools/build_java_catalog.py --check
# Generador histórico: véase el inventario antes de sobrescribir su evidencia.
```

El harness Windows usa una copia de código en una ruta con espacios y una base
sintética. Ejecuta realmente los `.bat`, arranque normal/alternativo, requisito
ausente, puerto ocupado, construcción fallida, disponibilidad fallida, caída
de dependencia, parada y restauración/rearranque del artefacto. La suite
general `test_web_interface` se ejecutó por separado y no forma parte de la
puerta de construcción específica: sus resultados y el diagnóstico se deben
consultar junto al informe de la sesión, sin presentarla como aprobada.

## Trabajo pendiente y condiciones de cierre

Faltan el detalle de países/áreas, países nuevos y sus árboles, assets JSON,
selectores y mutaciones de configuración/grupos/subdivisiones, tareas y demás
descargas/operaciones mixtas inventariadas. Tampoco están caracterizados todos
sus errores, permisos, formatos TOML/XLSX ni límites numéricos.

Las tareas siguen en Python, con sus handles en el proceso Django. Java no
recupera tareas al arrancar ni al consultar sus endpoints. Los bloqueos del
supervisor impiden dos stacks de este lanzador sobre la misma base, pero no
coordinan un `runserver` antiguo iniciado por separado. La recuperación actual
del gestor Python sigue siendo un riesgo para esa coexistencia. Hace falta
resolver propiedad/ejecución abandonada y probar caída/recuperación de tareas
antes de migrar esos endpoints o permitir múltiples servidores Django sobre
la misma base. No se promete ejecución exactamente una vez.

La aceptación final requiere completar el inventario dinámico, portar los
endpoints pendientes con aplicación nativa Java, resolver las regresiones
generales y demostrar convivencia y tareas. Ninguna de esas etapas se declara
terminada por tener el JAR y este primer bloque funcionando.

## Verificación histórica (2026-10-28)

Los resultados siguientes pertenecen a aquella implementación. La construcción
vigente solo ejecuta Maven; esta revisión documental no repite aquellas suites.

La construcción específica ejecutó 56 pruebas Python, seis pruebas Java y los
harnesses frontend de 22 y dos pruebas. Incluye equivalencia HTTP, SQLite
ocupado real y exclusión Java/Python en Windows. La suite web general se
ejecutó aparte: 170 pruebas, con 13 fallos y un error. Se repitió restaurando
en memoria el comportamiento previo de ajustes y middleware: los mismos
14 identificadores fallidos se reprodujeron. Eso no equivale a una suite
general aprobada. Afectan al ciclo de configuración, selectores, grupos,
subdivisiones/países derivados y una aserción de codificación sobre
`ensure_visual_assets.py`. Esos módulos no se han cambiado para ocultar las
discrepancias con sus pruebas.

El harness Windows ampliado pasó sus 11 escenarios. Incluye construcción Maven
real en una ruta con espacios, rechazo de otra instancia sobre la misma base
y caída del supervisor con limpieza de hijos, además de parada y reversión.
El lock de dependencias Python se validó además mediante resolución offline
de wheels con `pip --require-hashes --dry-run --ignore-installed`.

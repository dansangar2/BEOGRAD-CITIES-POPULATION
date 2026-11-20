# Estado acumulado de implementación

Revisión documental: 2026-11-19. Este documento reúne los cambios presentes en
el árbol de trabajo, incluidos archivos nuevos aún sin seguimiento. Describe
capacidades y límites observables en sus propietarios; no acredita despliegue,
sincronización de semillas con SQL ni reparación de la base local. Los resultados
de pruebas fechados en otros documentos pertenecen a sus sesiones originales.

## Backends y operación Windows

Se mantienen Java y Python. Java tiene prioridad y es el valor por defecto del
selector; el fichero local revisado selecciona Python. La variable de entorno
prevalece sobre el fichero. En modo Java, Django sirve la UI y reenvía cinco
rutas de lectura a la JVM en el puerto siguiente. Las pendientes siguen en
Django. Python sirve sus rutas sin JVM. Un error Java no activa otro backend.

El lanzador reinicia procesos propios y reconstruye Java si cambian sus entradas.
Mantiene bloqueos, Job Object, comprobación de disponibilidad y checksums;
la reversión afecta al JAR. El arranque exige base existente y no migra/importa.
Maven verifica contratos sintéticos sin Django; las suites Python y frontend
se ejecutan aparte. [Operación vigente](architecture/python-http-retirement.md)
y [alcance Java](architecture/java-api.md).

## Capas y contratos HTTP

La antigua implementación extensa de vistas se distribuye entre handlers,
formularios y consultas. `web/views.py` conserva exports HTTP explícitos;
rutas, consumidores y patches apuntan al propietario. Las consultas reciben
datos ordinarios y las mutaciones viven en servicios/adaptadores. Métricas,
parámetros y renombrado de referencias tienen reglas independientes de Django.
Las pruebas de arquitectura protegen imports y funciones duplicadas.

El tratamiento SQLite ocupado conserva respuestas 503 HTML/JSON; el seguimiento
de tareas dispone de cursores de logs acotados y protección frente a carreras.
Los procesos de tareas siguen ligados al gestor Python; no se garantiza
ejecución exactamente una vez ni recuperación con varios servidores.
[Refactor y límites](refactorizacion.md), [contratos web](architecture/web.md).

## Scraping y configuración SQL

CityPopulation V2 valida bloques y enlaza globalmente en memoria antes de
persistir. Se amplían controles genéricos de tablas/niveles, raíces, identidad
de origen, exclusiones exactas y padres. Compartir QID no colapsa partes de
localidad ni repeticiones territoriales. Los roles mantienen niveles uniformes
en todas las ramas; se conservan capas repetidas sin inventar localidades.

Se normalizan hectáreas a km2 antes de enlaces, densidad y sumas, respetando
el ámbito territorial. Los checkpoints incompatibles deben invalidarse.
Las ciudades configuradas pueden rematerializarse sobre SQL sin repetir scraping;
ese paso no vuelve a parsear HTML ni aplica retrospectivamente toda regla de página.

Hay cambios en objetivos territoriales y semillas de numerosos países de Europa,
América, Asia, África y Oceanía, además de bundles de Italia y ciudades de Bahamas.
Su presencia en disco no prueba importación ni reconstrucción de datos. Los
detalles y ejemplos de Austria/Viena y Cuba/La Habana están en
[scraping](architecture/scraping.md); el
[procedimiento completo](architecture/scraping-workflow.md) exige comprobar
ramas, hijos semánticos, recursos nacionales y datos confirmados mediante SQL/API.

Las semillas de scraping modificadas detectadas en esta revisión son las de
Afganistán, Albania, Samoa Americana, Angola, Anguila, Argentina, Armenia,
Australia, Austria, Azerbaiyán, Bahamas, Baréin, Bangladés, Bielorrusia, Benín,
Bután, Bolivia, Bosnia, Brasil, Islas Vírgenes Británicas, Brunéi, Canadá,
Croacia, Cuba, Chequia, El Salvador, Alemania, Honduras, Hungría, México,
Polonia, Rumanía, Serbia, Eslovaquia, Eslovenia, Ucrania y Estados Unidos.
Es un inventario de archivos modificados, no una lista de países validados.

## Reconciliación territorial y motores IA

La estrategia tradicional sigue por defecto. La alternativa CLI `--strategy ai`
usa referencia revisada, evidencia congelada y fotografía SQL separadas.
El motor predeterminado es simbólico local, sin modelo remoto ni credenciales;
`--ai-engine provider` selecciona explícitamente el proveedor externo.
No genera hechos oficiales ni resuelve silenciosamente identidades ambiguas.

Propuesta, validación, comparación y aplicación son pasos separados. Aplicar
requiere el hash revisado y comprobar que no cambiaron las entradas; usa
bloqueo y transacción. Se conservan relaciones y metadatos protegidos.
Las tablas territoriales se incorporan mediante la migración `0030`; esta
revisión no la aplica a la base local. La integración del flujo territorial
con botones/trabajadores HTTP y su implementación Java siguen pendientes.

El transporte JSON, validación de respuestas y enriquecimiento SQL se separan
entre puertos, aplicación e infraestructura. La IA de textos/assets y la
reconciliación son flujos distintos. Las pruebas de aceptación territorial usan
procesos y SQLite desechables; el modo proveedor simula el transporte.
[Entradas, comandos y límites](architecture/territorial-reconciliation.md).

## Jerarquías derivadas y exportaciones

La revisión documental del 2026-11-20 incorpora el editor de entidades
guardadas, la selección de fuentes y capitales, las mutaciones de ramas y
las restas automáticas de grupos. Los contratos del editor se describen en
[entidades guardadas](architecture/derived.md#entidades-guardadas).

Países/variantes, subdivisiones y grupos tienen configuración SQL y puentes
TOML separados. Importar semillas no construye filas derivadas.
Se conservan recetas heredadas y rutas de compatibilidad todavía referenciadas.
Hay nuevas semillas del Imperio austriaco, variantes provinciales y ajustes
en variantes españolas; no se declara su construcción sobre la base existente.

Las selecciones parciales de distritos de una ciudad unificada mantienen solo
la parte elegida. Los selectores cargan por país/nivel/página y conservan
operaciones de suma/resta aunque no se abra una rama. Capitales, traducciones,
representantes y relaciones de origen mantienen sus contratos.
[Jerarquías](architecture/derived.md) y [CSV/XLSX](architecture/exports.md).

## Frontend, recursos e idiomas

JS/CSS tienen fuentes ordenadas, manifiesto y bundles reproducibles.
El navegador de países muestra resumen cacheado antes de revalidar SQL,
carga ramas al pulsar y consulta assets/traducciones por lotes. Los efectos
de tema mantienen animación basada en composición y respetan movimiento reducido.
Los recursos visuales conservan placeholders estables, preview y ficha legible;
su descripción usa el idioma activo. Se mantienen nueve catálogos gettext.
[Contratos y carga](architecture/web.md) y
[mediciones históricas](architecture/operations.md#country-browser-performance-checks).

## Documentación y skills

[AGENTS](../AGENTS.md) es el índice de reglas e invariantes, [README](../README.md)
contiene comandos de usuario y los documentos temáticos desarrollan cada área.
Las skills de proyecto versionadas reúnen decisiones reutilizables:
[código](../.agents/skills/beograd-code-generation/SKILL.md),
[scraping](../.agents/skills/beograd-scraping-configs/SKILL.md) y
[derivados](../.agents/skills/beograd-derived-data/SKILL.md).

El [registro inicial de configs](DOCUMENTACION_CAMBIOS_CONFIGS.md) conserva
historia. El inventario Java generado inspecciona los propietarios Python activos
y la allowlist Java. Python tiene el mismo estatus de mantenimiento que Java;
`backend-python/manage.py` comparte el código Django de `ciudades_del_mundo`.
El inventario es evidencia estática, no una comprobación de equivalencia HTTP.

## Verificación y pendientes

Para esta entrega se revisan enlaces locales, anclas y comandos contra sus
propietarios, formato de las skills y espacios del diff documental.
No se ejecutan scraping real, proveedor pagado, migraciones ni reparaciones SQL.
No se atribuye una suite completa aprobada: los
[resultados históricos](architecture/refactor-validation.md) conservan fallos
y límites; [operaciones](architecture/operations.md#offline-verification-scope)
define las comprobaciones reproducibles por componente.

Comprobaciones ejecutadas el 2026-11-19: 17 documentos revisados, 130 enlaces
locales/anclas resueltos, 24 nombres de comandos de gestión comprobados contra
propietarios del proyecto o comandos Django incorporados, y tres frontmatters
simples de skills verificados (campos, nombre, descripción y placeholders).
El validador estándar `quick_validate.py` se intentó para las tres skills y no
pudo arrancar por ausencia de `PyYAML`. La comprobación alternativa usa biblioteca
estándar y no equivale a una validación YAML general. No se instalaron dependencias.

Siguen pendientes la migración de las rutas Java restantes, integración HTTP
territorial, validación con referencias nacionales independientes y resolver
las regresiones generales históricas mediante nuevas ejecuciones.
El consumo exacto de tokens y su presupuesto no están disponibles en esta sesión.


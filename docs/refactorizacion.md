# Arquitectura y verificación del refactor

Las URLs, nombres de rutas, respuestas HTTP y formatos CSV/XLSX se conservan.
SQL continúa siendo la fuente de verdad; las semillas TOML solo se consultan
en las operaciones explícitas de importación, exportación y bootstrap.

## Responsabilidades

El [estado acumulado](estado-actual.md) sitúa este refactor dentro de los cambios
de scraping, derivados, IA y backends. Java y Python siguen mantenidos; Django
conserva la UI y rutas pendientes en modo Java. La allowlist activa pertenece a
`web/java_routing.py`; consulta [operación](architecture/python-http-retirement.md).

| Ubicación | Responsabilidad |
| --- | --- |
| `web/handlers/` | Requests, métodos/códigos HTTP, respuestas y composición |
| `web/http.py` | Parseo HTTP, paginación y conversión de errores de datos a 404 |
| `web/queries/` | Selección SQL y payloads de datos, sin requests ni escrituras |
| `web/forms/` | Transformaciones de formularios y contenido TOML |
| `web/views.py` | Exports explícitos de los handlers públicos anteriores |
| `infrastructure/django/area_selectors.py` | Filtros ORM por país, nivel, población y estado de merge |
| `services/derived_config_mutations.py` | Renombrado, clonación y borrado SQL de ramas seleccionadas |
| `services/scraping_config_tasks.py` | Bootstrap explícito de configuraciones antes de tareas |
| `application/group_references.py` | Reglas de renombrado/deduplicación de referencias |
| `application/query_parameters.py` | Normalización independiente de HTTP/ORM |
| `application/browser_metrics.py`, `application/export_metrics.py` | Métricas, rollups y reglas de exportación |
| `ports/query_errors.py` | Error de datos independiente de Django HTTP |
| `infrastructure/scraping/discovery_http.py` | Transporte de la generación explícita de configuraciones |
| `infrastructure/wikidata_countries.py` | Catálogo externo Wikidata con las 232 claves geográficas originales |
| `static/ciudades_del_mundo/src/` | Fuentes ordenadas de los bundles JS/CSS |
| `tools/` | Ensamblado reproducible y verificación de copia limpia |

Las consultas reciben datos ordinarios. Los handlers convierten los parámetros
multivalor con `query_params(request, list_fields=...)`, conservando filtros y
paginación. Los getters de entidades ausentes lanzan `DataNotFoundError`;
el adaptador HTTP conserva el mensaje y lo transforma en `Http404`.

Las escrituras de las jerarquías derivadas están fuera de las consultas. Los
handlers resuelven el ámbito SQL y componen los adaptadores de mutación dentro
de las transacciones existentes. El recorrido de descendientes se comparte entre
los dos borrados, conserva sus diferencias al seleccionar raíces y evita repetir
ancestros. Las pruebas verifican raíces, hermanos, países ajenos e idempotencia.

Las reglas de referencias operan sobre los datos de configuración. Reciben el
normalizador existente para conservar sus errores traducidos. Los adaptadores de
mutación reciben los selectores/serializadores que necesitan; no importan web.
La preparación de exportaciones mantiene la actualización previa de ciudades
más pobladas en el handler; la construcción del workbook consulta datos.

## Compatibilidad e imports

`web/urls.py` importa los módulos propietarios de los callbacks. La fachada
`web/views.py` conserva únicamente imports públicos HTTP explícitos, incluidos
los endpoints `_legacy` todavía activos. No reexporta modelos, imports auxiliares
ni helpers privados; tampoco usa una subclase de módulo ni propaga asignaciones.

Los consumidores internos y las pruebas importan sus propietarios. Un patch
se aplica donde se busca la dependencia, no donde se definió originalmente:

```python
patch("ciudades_del_mundo.web.handlers.configs.sync_scraping_configs_from_toml")
patch("ciudades_del_mundo.services.scraping_config_tasks.sync_scraping_configs_from_toml")
```

El primero sirve para la importación explícita del editor y el segundo para
el bootstrap de una tarea. Se prueban aislamiento y restauración de los patches.
Las dos pruebas anteriores de propagación entre módulos se sustituyeron por
esas garantías, conforme al nuevo contrato; no se relajaron las expectativas
de las rutas, configuraciones SQL, jerarquías ni exportaciones.

Los nombres nuevos usan `snake_case`, tipos `PascalCase` y constantes
`UPPER_SNAKE_CASE`. Los nombres persistidos no se modifican por estética.
No se retiraron recetas o rutas `_legacy` todavía referenciadas.

## Frontend

El manifiesto conserva 22 fuentes JavaScript y 11 CSS. Se ensamblan en el IIFE y
la cascada originales: mismos scripts, traducciones, estado e inicialización
Select2 → editor → acciones posteriores. No son módulos ES independientes.

Las pruebas ejecutan el bundle real en Node con dobles de DOM, reloj y HTTP.
Comprueban el evento `DOMContentLoaded`, debounce y cambios inmediatos de autosave,
hidratar sin guardar, Beacon y fallback keepalive, errores de guardado, solicitudes
acotadas de selectores y trabajo en vuelo compartido, búsqueda normalizada,
navegación con teclado/clic central, offsets de logs y cancelación de tareas.
El acceso a funciones privadas se inyecta solo en el harness de pruebas.

El script del editor de grupos vive en `templates/ciudades_del_mundo/group_form.html`,
fuera del bundle. `tests/frontend/group_selection.test.cjs` lo ejecuta con dobles
para comprobar las operaciones de suma/resta al hidratar, cargar hijos y cancelar.
Se ejecuta aparte; la verificacion en copia limpia solo incluye el harness del bundle.

Una regresión reprodujo la pérdida de cambios cuando la respuesta de un guardado
anterior llegaba después de una edición nueva. El contador de revisiones evita
marcar esa edición como guardada y permite enviarla al cerrar la página.
Las versiones de ambos estáticos se mantienen iguales en
`templates/ciudades_del_mundo/base.html` y se actualizan con cada cambio de
comportamiento del bundle.

## Dependencias y comandos

Python 3.13+; dependencias directas verificadas en `requirements.txt`.
`lxml` es opcional y `openpyxl` no es necesario para el escritor XLSX incluido.
Node 20+ permite ejecutar las pruebas del frontend sin npm. `tools/__init__.py`
hace explícito el paquete de herramientas.

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONDONTWRITEBYTECODE='1'
.venv\Scripts\python.exe -B -m tools.build_web_assets
.venv\Scripts\python.exe -B -m tools.build_web_assets --check
.venv\Scripts\python.exe -B manage.py check --settings=ciudades_del_mundo.tests.isolated_settings
.venv\Scripts\python.exe -B manage.py test ciudades_del_mundo.tests --settings=ciudades_del_mundo.tests.isolated_settings --noinput
node --test tests/frontend/behavior.test.cjs
node --test tests/frontend/group_selection.test.cjs
.venv\Scripts\python.exe -B -m tools.verify_clean_copy
```

`verify_clean_copy` copia solo fuentes, herramientas, pruebas y catálogos a un
directorio temporal. Excluye DB, semillas, fixtures locales y diagnósticos.
Ejecuta `pip check`, importación de las herramientas, bundles, `check`, regresiones
SQL/arquitectura/IA/exportación y el frontend; comprueba que no se creó DB local.
Puede seleccionarse otro intérprete con `--python <ruta>`. Se ejecutó con un
venv nuevo instalado desde `requirements.txt`, sin compartir site-packages.

Las pruebas Django usan SQLite en memoria y bloquean conexiones externas.
Los proveedores IA/HTTP se simulan. No se modificó `db.sqlite3`, ni se ejecutó
scraping real o enriquecimiento IA pagado. Los bloqueos SQLite, transacciones y
recuperación de tareas se conservan; el arranque y la cancelación se sincronizan.

## Resultados y límites

Los resultados siguientes corresponden a la fase del refactor registrada en
[el registro de validacion](architecture/refactor-validation.md). No son una
ejecucion de esta revision documental ni el estado garantizado del checkout actual.
Consulta el [alcance de cada comando](architecture/operations.md#offline-verification-scope)
antes de comparar una seleccion de regresiones con la suite completa.

| Comprobación ejecutada | Resultado |
| --- | --- |
| Instalación en venv nuevo y `pip check` | Pasa |
| Copia limpia: Django `check` y bundles | Pasan |
| Copia limpia: regresiones Django | 64 pruebas, todas pasan |
| Frontend con dobles | 12 pruebas, todas pasan |
| Suite completa aislada | 421 pruebas; 30 fallos de aserción y 7 errores previos; ninguna omitida |
| Línea base al comenzar esta fase | 389 pruebas; los mismos identificadores de fallo/error |

La suite completa **no está verde**: siguen pendientes los casos descritos en
[el registro de validación](architecture/refactor-validation.md). No se alteraron
sus expectativas para ocultar esos problemas. La comprobación de arquitectura
recorre imports relativos/recursivos y protege el núcleo, las consultas sin HTTP
ni mutaciones y los adaptadores nuevos sin dependencia inversa de web.

Las pruebas con dobles no sustituyen una revisión visual en navegadores y
dispositivos, ni comprueban la disponibilidad actual de CityPopulation o de una
IA externa. Los payloads históricos conservan relaciones entre funcionalidades;
no se afirma que toda la aplicación existente sea un dominio puro.

## Correcciones puntuales de HTTP y tareas

Se eliminaron las seis redefiniciones idénticas de helpers de `web/http.py`.
Arquitectura detecta funciones repetidas por ámbito, también dentro de bloques
de control, y permite el mismo nombre en clases o funciones distintas.
El catálogo Wikidata se extrajo sin normalizar claves ni cambiar su fallback;
una huella de regresión protege las 232 correspondencias originales.

Los deltas del log se limitan a 64 KiB y devuelven el offset de los bytes
consumidos, aunque el archivo crezca durante la lectura. UTF-8 incompleto queda
pendiente para la siguiente petición. El fallback SQL conserva offsets de
caracteres. `output_has_more` permite vaciar bloques pendientes tras finalizar;
el bundle y ambas versiones estáticas se actualizaron de forma reproducible.

La comprobación final, `Popen` y el registro del proceso comparten el bloqueo
de cancelación. Un proceso cancelado conserva su plaza hasta `wait()`, evitando
solapar sustituciones con la misma clave. Las pruebas simulan procesos y usan
eventos para comprobar la exclusión durante el arranque, sin scraping ni IA.

Se ejecutaron pruebas focalizadas de tareas/arquitectura y la validación
desde una copia limpia con un venv nuevo instalado desde `requirements.txt`.
Los comandos anteriores siguen siendo válidos; para el bloque focalizado:

```powershell
py -B manage.py test ciudades_del_mundo.tests.test_task_lifecycle ciudades_del_mundo.tests.test_architecture_boundaries --settings=ciudades_del_mundo.tests.isolated_settings --noinput
```

Límite del cursor existente: detecta truncado si el offset supera el tamaño;
una reescritura que ya haya recuperado ese tamaño no se distingue mediante un
offset sin identidad de archivo. La concurrencia sigue siendo local al gestor.

# Procedimiento general para configurar y reparar scrapings

Esta guia se aplica a cualquier pais. Complementa los detalles del
[parser y enlazador](scraping.md), las [operaciones](operations.md) y las
[reglas de persistencia](data.md). Las instrucciones generales viven en
`AGENTS.md`; el objetivo de cada pais, en
`ciudades_del_mundo/autoconfig_specs/<slug>.txt`; la configuracion operativa,
en SQL (`ScrapingConfig.content`).

## 1. Definir la jerarquia antes de editar

Asignar un nivel a cada **funcion territorial**, valido para todas las ramas
del pais. Los niveles concretos dependen del objetivo del pais; no copiar los
de otro pais ni deducirlos del orden de las paginas de CityPopulation.

- Comparar una rama ordinaria completa con capitales, ciudades-estado,
  ciudades estatutarias y otras excepciones presentes en la fuente.
- Una entidad puede cumplir varias funciones. Mantener entonces una cadena
  explicita con IDs distintos en niveles consecutivos, aunque las filas
  compartan nombre, QID, poblacion y superficie. Esa repeticion no justifica
  una fusion o eliminacion; tampoco se suman sus poblaciones entre niveles.
- Conservar las funciones intermedias exigidas por el objetivo. La ausencia
  de una seccion en la pagina no autoriza a subir ciudades o localidades de
  nivel. No inventar asentamientos para completar una capa final inexistente.
- Clasificar por funcion, no solo por la palabra del tipo original. Un
  distrito regional y un distrito municipal pueden ocupar niveles distintos;
  conservar la identidad y el tipo de la fuente y definir su encaje.
- Si el usuario ha especificado la jerarquia, recogerla en el objetivo. Si
  falta una decision que cambie la interpretacion territorial, aclarar ese
  punto mientras se avanza con el diagnostico independiente.

El objetivo debe indicar niveles, cadenas ordinarias y especiales, repeticiones,
capas finales ausentes y la ubicacion de subdivisiones adicionales. Los conteos
esperados se anotan cuando esten confirmados; no se inventan para validar.

Ejemplo de alineacion, no plantilla universal:

| Rama de Austria | L1 | L2 | L3 | L4 |
| --- | --- | --- | --- | --- |
| Ordinaria | Vorarlberg | Bregenz distrito | Bregenz ciudad/comuna | Bregenz localidad |
| Capital | Wien estado | Wien distrito/ciudad capital | Wien ciudad | 23 distritos municipales |

Los distritos municipales de Viena conservan su tipo; no se convierten en
localidades ficticias. La cadena tiene conteos de hijos directos `1 > 1 > 23`.
La regla general es alinear las funciones y preservar las subdivisiones reales.

## 2. Diagnosticar la tarea y su configuracion efectiva

Consultar primero la tarea mas reciente del pais y sus registros:

- `.web_task_logs/<slug>/<task_id>.log`: etapa alcanzada y excepcion concreta.
- `.web_scrape_block_errors/<slug>/`: resumen de problemas de bloque o enlace.
- `.web_scrape_pages/<slug>/<task_id>/index.jsonl`: rutas al HTML y a las
  entidades de cada pagina que realmente intervino en esa ejecucion.

Leer colas, cabeceras y paginas pertinentes. No volcar registros enormes,
datasets completos ni todo el historial. Distinguir el `slug` de configuracion
del `country_code` que identifica sus filas SQL.

Un codigo general como `SCR-DATA-001` describe la fase fallida, no la causa.
Buscar la excepcion especifica, por ejemplo `SCR-LINK-001`, y sus entidades
afectadas. Cuando el indicador corresponde a `WebTask.returncode`, `1` es el
codigo de salida del proceso, no el numero de entidades invalidas.

Preparar o consultar el expediente:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
py manage.py prepare_ai_autoconfig france --max-log-chars 6000 --max-page-index-lines 40
```

Sustituir `france` por el slug correspondiente. El comando genera diagnosticos
locales y ejecuta la retencion de registros antiguos; no lanza un scraping ni
modifica la configuracion SQL. El expediente y las instantaneas son material de
diagnostico que caduca, no sustitutos del objetivo versionado ni entradas de
respaldo para el scraper en produccion.

## 3. Comprobar TOML, secciones y niveles

Trabajar sobre el contenido SQL actual. Los TOML locales solo se importan o
exportan expresamente; no sustituir SQL por una semilla antigua para reparar
un fallo. Conservar opciones ajenas a la correccion y actualizar hash y
metadatos mediante los servicios de configuracion existentes.
Comprobar `toml_source` en el expediente: si el diagnostico solo encontro una
semilla, eso no demuestra que exista una configuracion operativa en SQL.

Las claves globales deben aparecer antes de la primera tabla:

```toml
scrape_schema_version = 2
name = "Pais de ejemplo"
country_code = "example"

[visual_assets.flag]
# Las propiedades que siguen pertenecen a esta tabla hasta la siguiente tabla.
```

Lo mismo se aplica al `wikidata_id` nacional y a `LEGAL_SUBDIVISION` cuando se
usen. Escribir `scrape_schema_version = 2` despues de `[visual_assets.flag]`
lo guarda dentro de esa tabla: el TOML puede ser valido, pero el cargador no
encuentra la clave global y usa el valor por defecto del esquema antiguo.
Comprobar la estructura con `tomllib` y el plan del cargador; `is_valid` no
demuestra que la jerarquia resultante tenga los niveles deseados.

`include_root = false` prevalece sobre `include.infosection = true`. Al excluir
secciones, cambia la distribucion de niveles: `force_highest_level` es el
nivel base del bloque, no una garantia de que siempre represente un estado.
Comprobar que la primera seccion persistida ocupa el nivel de su funcion. Por
ejemplo, si una pagina omite la raiz y empieza por comunas de L3, usar una base
coherente con L3; no conservar L2 solo porque otra pagina empiece por distritos.

Mantener la cabecera cuando aporta el padre de las tablas. Si solo aporta
contexto, verificar que ese contexto permite recuperar los padres correctos
antes de excluirla. Una pagina con varias zonas administrativas puede carecer
de una raiz unica: no exigir una infoseccion que no existe.

Expresar las excepciones en configuracion con `repeat`, `root_code`,
`root_parent_code`, `root_entity_type`, niveles forzados y las extensiones
`parent_overrides`/`synthetic_entities` cuando correspondan. Usar IDs estables
y distintos para funciones distintas. No anadir ramas del parser por nombre
de pais cuando los mecanismos genericos ya representan el objetivo.

Si distintas filas de poblacion comparten nombre o QID, comprobar sus codigos y
tipos antes de fusionarlas. `preserve_source_codes` conserva la identidad por
codigo/nivel en el bloque; los tipos `(part)` mantienen separadas las partes de
una localidad. Usar `wikidata_overrides` o `code_overrides` para ajustes de
identidad por pagina y `code_pattern`/`parent_code_template` para reglas de padres.
`exclude_entity_codes` es una clave global de coincidencia exacta, anterior a las
tablas: no elimina descendientes ni repara sus padres. Revisar el conjunto final
completo, no solo la ausencia de la fila excluida. Ver
[controles de identidad](scraping.md#source-identity-controls).

La entrega de una correccion de pais debe incluir el ajuste en
`ciudades_del_mundo/subdivisions/<slug>.toml`, para que `Importar TOML` pueda
reproducirlo. Partir del contenido SQL vigente para conservar los demas ajustes.
Si se edito primero SQL, exportar explicitamente solo ese pais; si se edito
primero el archivo, importarlo explicitamente a SQL. Revisar el diff y comprobar
que ambos contenidos coinciden. Los comandos estan en
[README: sincronizacion TOML/SQL](../../README.md#sincronizar-toml-semilla-por-pais-con-sql).
Esta sincronizacion sigue siendo un puente explicito: el runtime lee SQL.

Una URL puede aportar varias funciones en bloques diferentes. En el prefetch
actual, el parseo se reutiliza por URL dentro de cada bloque: separar en
`[[pages]]` distintos los usos con diferentes controles de raiz o niveles.
No asumir que reutilizar HTML permite reutilizar entidades ya parseadas con
otra configuracion.

## 4. Verificar los recursos visuales por identidad

Confirmar el QID del pais y de las entidades afectadas con una fuente
identificable. Una coincidencia de nombre, una busqueda automatica o un QID
guardado anteriormente no demuestran que sea la entidad geografica correcta.
Si CityPopulation no proporciona el QID nacional, declarar el correcto como
clave global y revisar los valores existentes que puedan prevalecer en el
flujo ejecutado. Los caminos manual y por lotes no tienen necesariamente el
mismo orden de resolucion.

Verificar conjuntamente:

- `AdminArea.data_wd` de la raiz y QID nacional configurado.
- Assets `entity_type="country"` para la clave del pais y
  `entity_type="admin_area"` para su fila raiz: ambas fichas deben recibir
  la bandera y el escudo correspondientes.
- Assets de las funciones repetidas con el mismo QID: pueden compartir la
  identidad visual y las traducciones, conservando sus IDs de entidad.
- `wikidata_id`, `commons_filename` o `remote_url`, estado y `image_url` que
  entrega la API. No dar por aplicado un override solo por verlo en el TOML.

Corregir asociaciones antiguas equivocadas, no perpetuarlas como fallback.
Usar los mecanismos SQL y servicios existentes para declarar imagenes y
compartirlas; no hardcodear banderas en vistas. No confundir iconos de idioma
de CityPopulation con banderas nacionales. Respetar la seleccion de imagenes
actuales, los limites de consultas y el uso normal de URLs sin descargas.
La ausencia de un recurso visual se informa como aviso segun el flujo; no
debilita ni sustituye las validaciones de datos y jerarquia.

## 5. Validar la solucion completa sin scraping real incidental

La validacion sintactica y el listado de paginas no comprueban las relaciones
que producira el HTML:

```powershell
py manage.py validate_subdivision_configs france
py manage.py scrape_subdivisions france --list-pages
```

El primer comando guarda el resultado de validacion en SQL y actualiza el
progreso; el segundo muestra el plan sin descargar paginas. Para comprobar
parser/enlazador, usar HTML guardado o fixtures pertinentes en una verificacion
offline con red bloqueada. Si se cambia codigo, ejecutar las pruebas relevantes
con `ciudades_del_mundo.tests.isolated_settings`, que usa SQLite en memoria.

Una prueba de dos paginas no basta para confirmar la jerarquia completa de un
pais con otros bloques afectados. Verificar el plan completo disponible o
explicar su cobertura. Reproducir el orden real: parseo y validacion de bloques,
normalizacion global, fusiones, extensiones de configuracion, validacion final
y persistencia simulada. Incluir los efectos del prefetch y de las claves de
cache cuando se reutiliza una URL.

Ademas de cero huerfanos, comprobar que no hay autoenlaces o ciclos, que cada
padre existe y ocupa un nivel superior, que las funciones mantienen su nivel,
que las capas obligatorias estan presentes y que los conteos de hijos directos
coinciden con el objetivo. Revisar ramas ordinarias y especiales. Un estado
vacio junto a distritos colgados directamente del pais es un resultado
incorrecto aunque todos esos distritos tengan un padre tecnicamente valido.

Comparar IDs, altas/bajas, poblaciones, tipos, traducciones y relaciones antes
y despues. No eliminar una fila por llamarse igual que su padre. Una repeticion
puede ser necesaria para la alineacion; su eliminacion exige comprobar antes
que no representa otra funcion y preservar las referencias afectadas.
No hacer scraping real ni llamadas de IA de pago para validar sin autorizacion.

## 6. Aplicar y comprobar lo que vera el usuario

Cuando el encargo incluye corregir datos ya importados, guardar tanto la
receta SQL para futuros scrapings como la reparacion autorizada de las filas
actuales. Editar un `.txt`, generar un expediente o guardar el TOML no
reconstruye por si solo los `AdminArea` existentes. Tampoco lo hace una
simulacion correcta en memoria.

Para reparaciones locales, usar el bloqueo SQLite y transacciones existentes,
limitar el alcance al pais y las entidades afectadas, conservar una copia de
los valores que se modifican y comprobar que la configuracion no ha cambiado
desde la verificacion. Mantener IDs, poblaciones, tipos, traducciones y
relaciones ajenas al arreglo. Crear nuevas filas de funcion con codigos
deterministas; no sumar su poblacion repetida ni cambiar otros paises.
Una peticion de documentacion no autoriza esta mutacion de datos.

Un script local de reparacion puede aplicar esas mutaciones una sola vez y
guardar diagnosticos bajo `.web_ai_autoconfig/<slug>/`. Documentar por separado
su alcance, la copia anterior y las comprobaciones realizadas. Estos artefactos
caducan con los diagnosticos y pueden depender del estado previo y del contexto
de ejecucion; no convertirlos en comandos mantenidos ni en dependencias del
parser, del arranque o de `Importar TOML`. La regla reutilizable debe quedar
expresada en el TOML versionado y sincronizada con SQL. Importar configuracion
no ejecuta esos scripts ni materializa automaticamente la reparacion de datos.
Ver el [caso de Cuba](scraping.md#cuba-havana-city).

Tras confirmar la transaccion, leer desde una conexion nueva y comprobar
tambien la respuesta de `api_admin_area_detail` (`api/admin-areas/<area_id>/`):
`children`, `area.subdivision_count`, niveles, grupos de hijos y assets. La
interfaz aplica selectores y reglas de presentacion; un conteo SQL aislado no
demuestra que entregue el mismo resultado. Conservar los contratos de la API.

Solo dar una reparacion por aplicada cuando las filas persistidas y el payload
que consume la interfaz reflejen lo solicitado. Indicar si se cambiaron solo
instrucciones, solo configuracion, datos existentes o ambos. Reportar las
comprobaciones ejecutadas y sus limites: un payload con URL no demuestra por
si solo que el navegador haya descargado y mostrado la imagen.

## Documentacion permanente y puntos de entrada

- `AGENTS.md`: reglas breves y enlaces; no historiales por pais.
- Esta guia y `scraping.md`: procedimiento general y comportamiento tecnico.
- `autoconfig_specs/<slug>.txt`: objetivo concreto, incluidas excepciones.
- `README.md`: comandos y uso. `prepare_ai_autoconfig` incluye estas reglas en
  los nuevos expedientes para futuros agentes.
- `services/scraping_configs.py`: guardado SQL y metadatos.
- `services/scraping_config_extensions.py`: extensiones desde SQL.
- `application/scrape_admin_areas.py` y `application/citypopulation_linking.py`:
  validaciones, orden de ejecucion y enlaces.
- `services/visual_assets.py` y `scrape_subdivisions_with_assets.py`:
  recursos visuales, identidades compartidas y reflejo nacional en la raiz.
- `web/queries/browser.py` y `web/handlers/dashboard.py`: payload y API usados
  por la interfaz para comprobar la jerarquia visible.

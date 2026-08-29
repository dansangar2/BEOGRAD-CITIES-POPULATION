# BEOGRAD-CITIES-POPULATION

Proyecto Django para:

- obtener divisiones administrativas y poblacion desde `citypopulation.de`
- persistir el resultado en `AdminArea`
- construir subdivisiones derivadas o historicas en `NuevoAdminArea`
- exportar esas jerarquias a CSV y Excel

## Ejecutar en local

Desde la raiz del proyecto:

```powershell
py manage.py runserver 127.0.0.1:8000
```

Abre `http://127.0.0.1:8000/` para el panel principal o
`http://127.0.0.1:8000/countries/` para el navegador de paises. Usa
`127.0.0.1` para que Django escuche solo en tu propia maquina; no uses
`0.0.0.0` salvo que quieras exponerlo en tu red local.

### Primer arranque: cargar las configuraciones SQL iniciales

Si partes de una base nueva o la tabla `ScrapingConfig` esta vacia, primero
aplica migraciones y carga las configuraciones iniciales desde los TOML
semilla de `ciudades_del_mundo/subdivisions/*.toml`:

```powershell
py manage.py migrate
py manage.py sync_scraping_configs --force
```

Para cargar o reinstanciar solo un pais concreto desde su TOML semilla:

```powershell
py manage.py sync_scraping_configs spain --force
```

Despues ya puedes validar o popular desde `/configs/` o con los comandos CLI
`validate_subdivision_configs`, `scrape_subdivisions` y
`scrape_subdivisions_with_assets`.

La interfaz web tambien hace este arranque bajo demanda: si pulsas `Popular`
para un pais cuya fila `ScrapingConfig` aun no existe, importa primero el TOML
semilla correspondiente y despues lanza el flujo normal de validacion y
populacion. Si `ScrapingConfig` esta completamente vacia, `Popular todo` y
`Popular no populados` intentan importar los TOML semilla antes de seleccionar
las configuraciones a procesar.

## Estado actual

El sistema de obtencion activo para `/configs/<pais>/` vuelve a ser
CityPopulation con `scrape_schema_version = 2`. La configuracion activa vive en
la tabla SQL `ScrapingConfig`; su campo `content` mantiene formato TOML para que
siga siendo editable y versionable como texto dentro de SQL. El runtime lee
siempre la configuracion desde BBDD, no desde ficheros TOML.

Ya no existe la capa de obtencion Wikimedia V3 ni la ruta
`/configs/old/<pais>/`. Las filas `old-*` creadas durante la prueba V3 se
restauraron sobre su slug principal y se eliminaron. Wikimedia/Commons se sigue
usando solo como apoyo para assets visuales y reparaciones puntuales de padres
del flujo CityPopulation.

Los TOML de `ciudades_del_mundo/subdivisions/*.toml` se usan solo como semilla
explicita para crear o refrescar filas en `/configs/` mediante
`sync_scraping_configs`. Una vez instanciadas las filas SQL, puedes retirar esa
carpeta local y el scraping seguira funcionando porque no hay fallback runtime a
ficheros.

Los TOML de agrupaciones derivadas se separan bajo
`ciudades_del_mundo/subdivision_groups/`: `groups/<pais>.toml` contiene un
bundle compacto importable con una asignacion por cada `SubdivisionGroup`, y
`subdivisions/<pais>.toml` contiene el bundle por pais de
subdivisiones/diccionarios que se importa en la seccion `/subdivisions/` como
base declarativa `DerivedSubdivision`.

Los TOML de `ciudades_del_mundo/cities_merge/<pais>.toml` son un puente
separado para importar/exportar solo bloques `[[cities]]` de unificacion de
ciudades. Importarlos reemplaza esos bloques en la fila SQL `ScrapingConfig`,
pero no toca `[[pages]]`, assets ni el resto de la configuracion.
Cuando el editor `/configs/<slug>/` guarda una configuracion con `[[cities]]`
pendientes y el pais ya tiene datos `AdminArea`, el sistema materializa esas
ciudades en BBDD sin repetir el scraping. Para aplicar ese paso de forma
explicita sobre una config ya guardada:

```powershell
py manage.py materialize_city_merges <slug>
```

Cada configuracion CityPopulation describe:

- que formato CityPopulation usar por bloque (`cities`, `admin` o `citiesadmin`)
- que rutas scrapear bajo la base fija `https://www.citypopulation.de/en/`
- que secciones de cada pagina se persisten y cuantas veces se repiten
- desde que nivel arranca cada bloque o que nivel se fuerza
- como enlazar bloques consecutivos, padres forzados y paginas que suman al padre
- reglas opcionales de normalizacion de ciudades, merges y extensiones runtime
- `LEGAL_SUBDIVISION` como nivel legal/configurado para otros flujos; la ciudad
  mas poblada scrapeada se calcula por el nivel descendiente mas alto disponible

Comandos utiles:

```powershell
py manage.py validate_subdivision_configs spain
py manage.py scrape_subdivisions spain
py manage.py scrape_subdivisions_with_assets spain
py manage.py materialize_city_merges spain
py manage.py prepare_ai_autoconfig spain
```

`scrape_subdivisions_with_assets` ejecuta primero el scraping CityPopulation y
despues reutiliza el flujo existente de assets visuales por QID.

### Autoconfiguracion por IA

Para que un agente pueda corregir una configuracion a partir del objetivo
territorial esperado, crea un fichero
`ciudades_del_mundo/autoconfig_specs/<slug>.txt`. El texto debe describir la
jerarquia persistida esperada por niveles, por ejemplo
`Region > Departamento > Distrito > Comuna > Localidad`.

Antes de pedir la correccion, genera el expediente offline:

```powershell
py manage.py prepare_ai_autoconfig france
```

Si no pasas pais, el comando toma los paises con logs recientes:

```powershell
py manage.py prepare_ai_autoconfig --limit 5
```

El expediente se guarda en `.web_ai_autoconfig/<slug>/` e incluye el `.txt`
territorial, el TOML activo de `ScrapingConfig.content`, el ultimo log de
validacion, el ultimo log de tarea y el indice de paginas scrapeadas mas
reciente. No scrapea red ni modifica configuraciones. Los scrapes normales
guardan snapshots de pagina en `.web_scrape_pages/<slug>/<task>/` con
`index.jsonl`, HTML y entidades parseadas para que el agente pueda revisar la
pagina exacta que produjo el error.

## Arquitectura

El proyecto esta organizado por capas hexagonales. Para que una IA pueda leerlo
sin mezclar responsabilidades, empieza por `AGENTS.md`, luego entra solo en la
capa del cambio:

Nota para agentes: cuando una peticion cambie una regla duradera de diseno,
API, formularios, controladores o generacion de codigo, actualiza tambien la
SKILL local correspondiente si esta disponible, ademas de `AGENTS.md` y la
documentacion del repo.

- `ciudades_del_mundo/domain`
  Modelos puros y logica de dominio: configuracion de scraping, DTOs, jerarquia y ciudad mas poblada. No importa Django ni adaptadores.
- `ciudades_del_mundo/ports`
  Protocolos que definen las fronteras del nucleo, como repositorios, scrapers y `HtmlFetcher`.
- `ciudades_del_mundo/application`
  Casos de uso: ejecutar scraping, aplicar ciudades configuradas y exportar `NuevoAdminArea`. Solo depende de `domain`, `ports` y modulos de la propia capa.
- `ciudades_del_mundo/infrastructure`
  Implementaciones concretas: repositorios Django, scrapers HTML, cliente HTTP y escritor XLSX.
- `ciudades_del_mundo/services`
  Servicios operativos que todavia trabajan con modelos Django para agregacion, capitales, assets, traducciones dinamicas y reparto de representantes.
- `ciudades_del_mundo/management/commands`
  Raices de composicion CLI: conectan casos de uso con repositorios, clientes HTTP y otros adaptadores.
- `ciudades_del_mundo/web`
  Interfaz operativa para inspeccionar datos, editar configuraciones, lanzar
  tareas locales y borrar datos.
- `locale`
  Catalogos gettext de la interfaz web en espanol, ingles, frances, aleman,
  ruso, italiano, serbio cirilico, serbio latino y arabe estandar.

Regla de dependencias: `domain` y `ports` no importan adaptadores; `application`
no importa `models`, `services`, `web`, `management` ni `infrastructure`. Las
raices de composicion inyectan implementaciones concretas. Por ejemplo,
`ScrapeAdminAreas` hace prefetch concurrente a traves del puerto `HtmlFetcher`,
y el comando `scrape_subdivisions` le pasa `CityPopulationHtmlFetcher`
desde `infrastructure`.

## Modelos principales

- `AdminArea`
  Entidad scrapeada directamente desde CityPopulation.
- `NuevoAdminArea`
  Entidad derivada a partir de varias `AdminArea`, util para subdivisiones ficticias, historicas o politicas.
- `DerivedCountry` y `DerivedCountryConfig`
  Base SQL para agrupar paises derivados y sus configuraciones TOML.
- `SubdivisionGroup`
  Grupo TOML reutilizable de subdivisiones fuente para futuras configs de paises derivados.

## Flujo de trabajo

1. Definir o ajustar una configuracion en `/configs/` o en una fila `ScrapingConfig`.
2. Validar la configuracion.
3. Ejecutar el scraping.
4. Opcionalmente asignar capitales.
5. Opcionalmente construir subdivisiones derivadas o historicas.
6. Exportar a CSV o Excel.

## Formato CityPopulation V2

Las configuraciones activas viven en `/configs/<pais>/` y usan el formato por
bloques.

El `slug` SQL define el prefijo comun de las rutas. Ejemplo: el slug `spain`
produce rutas bajo `spain/...`.

```toml
LEGAL_SUBDIVISION = 3
scrape_schema_version = 2

[representation]
level = 2
total = 350
min = 2
system = "dhondt"

[[pages]]
source = "admin"
path = ["admin"]
include = { infosection = true, major_subdivision = true, minor_subdivision = true }

[[pages]]
source = "cities"
path = ["andalucia", "aragon", "asturias"]
include = { infosection = false, major_subdivision = true, cities = true }

[[pages]]
source = "cities"
path = ["ruta-en-revision"]
enabled = false
include = { infosection = false, major_subdivision = true, cities = true }

[[pages]]
source = "cities"
path = ["ceuta", "melilla"]
parent_level = 2
include = { infosection = true, major_subdivision = true, cities = false }
repeat = { infosection = 2 }

[[pages]]
source = "citiesadmin"
path = ["cities/mayotte"]
sum_to_root = true
include = { infosection = true, major_subdivision = true, minor_subdivision = true, cities = true }
repeat = { infosection = 2 }

[[cities]]
city = "Example City"
id = "123"
level = 3
type = "City"
district_types = ["Arrondissement"]
from = { 2 = ["Example Parent"] }
communes = []
```

### Reglas del formato

- `pages` agrupa paginas por bloque. Si `path` contiene varias rutas, se crea
  una pagina por ruta con la misma configuracion y el mismo nivel de bloque.
  Internamente cada pagina expandida conserva `block_index` y `path_index` para
  validar el bloque completo antes de avanzar al siguiente.
- `path` siempre es un array, aunque solo haya una ruta. Las rutas relativas se
  prefijan con el `slug` SQL y siempre usan la base
  `https://www.citypopulation.de/en/`.
- `source` acepta `cities`, `admin` o `citiesadmin`. Es el formato HTML de la
  pagina, no una ruta. `cities` modela
  `infosection -> major_subdivision -> cities`; `admin` modela
  `infosection -> major_subdivision -> minor_subdivision`; `citiesadmin` modela
  `infosection -> major_subdivision -> minor_subdivision -> cities`. En paginas
  `citiesadmin` con `table#tl` agrupado, los hijos pueden aparecer antes del
  `tbody.adm` padre; el parser los enlaza por codigo/abreviatura y por totales
  de poblacion/area sin reglas por pais.
- En paginas `cities`, la seccion de ciudades persistida sale de `table#ts`
  (`Contents`). Las tablas auxiliares tipo "Major Cities" o "Major
  Agglomerations" se usan solo si CityPopulation las publica como el `table#ts`
  configurado.
- `include = { ... }` activa o desactiva secciones. Las secciones desactivadas
  no se guardan, pero pueden seguir dando contexto de padre dentro de la misma
  pagina cuando CityPopulation las necesita para enlazar la tabla persistida.
- `repeat = { infosection = 2 }` duplica explicitamente una misma entidad en
  niveles consecutivos. Se usa para raices administrativas repetidas como
  Ceuta/Melilla o departamentos/regiones de ultramar.
- `force_highest_level` fuerza el nivel base del bloque y evita depender del
  orden automatico de bloques; los bloques forzados no se bajan al nivel de una
  ancla repetida anterior.
- `parent_level` indica que el bloque debe buscar un padre de ese nivel. Cuando
  CityPopulation no trae `data-wd`, el enlazador puede usar codigo interno,
  nombre normalizado y prefijos de codigo para casos como localidades italianas
  (`01811010004` bajo `018110`). Se aplica antes de colapsar duplicados, por lo
  que casos como Paris pueden conservar el padre `751` y mover `75056` debajo.
  Tambien puede usar el ambito de la URL para ubicar el padre configurado y
  permite que un padre tenga hijos directos en mas de un nivel inferior cuando
  la pagina de CityPopulation salta una capa intermedia.
- `sum_to_root = true` se muestra en la UI como "Sumar al padre": las metricas
  de la rama se agregan al padre ya enlazado, o al pais si no hay padre forzado.
- `area_km2` permite indicar un tamano personalizado para la entidad raiz scrapeada en esa pagina.
- `area_overrides` permite indicar tamanos personalizados por `id`, `code` o `name` de entidad scrapeada.
- En `[[cities]]`, `keep_communes = false` agrega las comunas o distritos
  usados para calcular la ciudad pero no los conserva como filas hijas. El `id`
  de una ciudad configurada no debe reutilizar el codigo de la subdivision padre
  cuando esa subdivision debe seguir siendo seleccionable en el editor. Si el
  tipo del padre necesita conservarse para labels o reparaciones de BBDD, define
  `parent_type = "Prefecture"` (u otro tipo visible) en el bloque.
  El editor Manual de `/configs/<slug>/` puede construir esos bloques desde la
  subseccion `Ciudades`: la tabla lista las ciudades unificadas configuradas y
  el boton `Añadir` abre una ventana modal de nuevo/editar. Esa ventana usa el
  selector buscable nativo, el mismo estilo usado en `/groups/groups/...`, con
  las entidades del nivel inmediatamente superior a `LEGAL_SUBDIVISION`. La
  busqueda no distingue mayusculas ni acentos, y al elegir una subdivisión mayor
  carga bajo demanda sus hijos legales como badges. La X cierra sin aplicar
  cambios; solo `Guardar` actualiza el TOML/autoguardado. Cada bloque exige
  `city`/nombre de ciudad no vacio; los badges movidos a la derecha se guardan
  en `communes = [...]`. Al popular, la ciudad nueva se guarda en `AdminArea`
  con `city_merge_status = 1` y las entidades usadas para crearla quedan con
  `city_merge_status = 2`.
- `LEGAL_SUBDIVISION` es el unico nombre aceptado para el nivel legal.
- `enabled = false` en un bloque `[[pages]]` conserva esa configuracion en
  SQL/TOML pero la excluye del plan de scraping. En `/configs/<slug>/` se maneja
  con un boton de icono para desactivar o reactivar; los bloques desactivados se
  muestran oscurecidos.
- Durante el scraping, cada bloque se instancia en memoria y se valida antes de
  pasar al siguiente bloque o guardar en SQL. Para `cities`, `admin` y
  `citiesadmin` se exige que las secciones configuradas aparezcan en las
  entidades parseadas, que todo nivel inferior al nivel base del bloque tenga
  padre dentro del bloque o en bloques ya validados, y que el calculo de ciudad
  mas poblada sea posible para las ramas con hijos. Si falla, el comando lanza
  `SCR-BLOCK-001` y escribe `.web_scrape_block_errors/<slug>/<slug>_block_<n>_*.txt`
  con URL, HTML scrapeado completo y problemas detectados.
- Si todos los bloques pasan, el paso siguiente enlaza en memoria los bloques
  entre si mediante `normalize_citypopulation_entities` y las extensiones
  runtime. Ninguna fila se guarda aun. Antes de validar el enlace final, el
  pipeline intenta reparar filas aun sin padre con Wikimedia: consulta `P131` y
  `parentLabel` para los `data_wd` scrapeados y solo acepta padres que ya
  existan en el mismo scrape por QID o por nombre normalizado unico. Si
  `parent_level` esta configurado se usa como preferencia, pero no bloquea un
  padre valido de otro nivel cuando Wikidata/CityPopulation no exponen el padre
  ideal. Como ultimo respaldo generico, una fila puede enlazarse a un padre
  unico ya scrapeado cuyo nombre aparezca en el ambito de la URL, por ejemplo
  `/overijssel/_/...` bajo `Overijssel`. Para configuraciones v2, la foto final
  debe quedar sin entidades de nivel mayor que 0 sin padre valido; si queda
  alguna, el proceso lanza `SCR-LINK-001`, escribe
  `.web_scrape_block_errors/<slug>/<slug>_link_*.txt` con todas las paginas scrapeadas
  y se detiene antes de `save_many`.
- El enlazador une bloques por `data-wd`, codigo CityPopulation, nombre
  normalizado en el mismo nivel y codigo persistido. Si CityPopulation reutiliza
  un codigo para otra entidad, se desambigua con un codigo derivado del padre
  antes de persistir para no mezclar ramas. Si despues una fila queda sin padre
  pero su codigo desambiguado conserva el ambito del padre, como `17_174`, ese
  ambito se usa como tercera via generica de enlace (`17_174` bajo `17`).
  Cuando ya existe una raiz de pais, una fila ordinaria de nivel 1 sin padre se
  adjunta a esa raiz antes de validar el enlace final; esto evita que bloques
  sin `infosection` dejen provincias o regiones sueltas.
- La resolucion de `parent_level` / `Forced parent level` usa indices por nivel
  para codigo, prefijo, QID, nombre y ambito de URL. No debe recorrer todos los
  candidatos del nivel objetivo por cada fila: Portugal genera mas de 21k filas
  y muchas localidades con padre forzado a L2.
- La deduplicacion por nombre no es global para anclas de bloque que ya tienen
  padre o ambito de URL. Entidades homonimas de paginas distintas, como
  `Saint-Pierre` o `Grande-Terre` en departamentos ultramarinos franceses, deben
  conservar sus hijos dentro de su bloque antes de enlazar con otros bloques.
- La reparacion por prefijo de codigo no es global: el candidato de prefijo debe
  pertenecer a la misma rama territorial de la URL cuando ambas filas tienen
  ruta. Esto evita colisiones como comunas de Essonne `91521...` enlazadas por
  error bajo un codigo corto de Réunion `9152`.
- La misma reparacion puede mover un hijo desde un padre superior alfanumerico a
  un padre numerico mas especifico aunque el codigo del nuevo padre sea mas
  corto; esto cubre administraciones uniprovinciales como `MAD -> 28 -> 28079`
  sin reglas por pais.
- Si el hijo tiene una rama territorial en la URL y el candidato de prefijo es
  un ancla de bloque sin rama, el candidato se descarta. Esto evita que anchors
  mal resueltos como `#i6527` o `#i8125` capturen comunas de otros departamentos
  por simple prefijo numerico.
- Los segmentos territoriales multi-palabra de la URL se conservan tambien como
  una unidad normalizada. Asi, un padre ya asignado como `Castelo Branco` puede
  proteger a un hijo bajo `/castelo_branco/...` frente a un falso prefijo
  numerico como `1690502 -> 16`.
- El parser registra alias visibles de nombres cooficiales, parentesis,
  corchetes y separadores `/`, de modo que un `radm` como `Jávea` puede enlazar
  con una fila `Xàbia (Jávea)` sin reglas por pais.
- Si una fila de `table#ts` incluye una pista textual del tipo
  `Nombre (in: Parroquia)`, el parser la guarda como anotacion
  `CityPopulation parent hint` y el enlazador intenta usarla para bajar la fila
  al padre intermedio del nivel anterior cuando ese padre existe de forma unica
  en el mismo ambito. Portugal usa esto para localidades con parroquia visible.
- Para paises con paginas CityPopulation divididas entre varias familias se
  pueden declarar extensiones runtime en el TOML SQL:
  `[[synthetic_entities]]`, `[[parent_overrides]]` y
  `[[root_metric_sources]]`. Sirven para crear contenedores logicos, mover
  padres/niveles y sumar metricas de paginas adicionales sin meter parches en
  vistas ni modelos.

- Algunas paginas de CityPopulation vinculan filas hijas a un padre que no es el
  nivel inmediatamente anterior. En esos casos la config puede importar esas
  filas un nivel mas profundo manteniendo el `parent_code` real de la pagina.
  Ejemplos: localidades de Italia por provincia, localidades de Portugal por
  municipio cuando CityPopulation no publica la parroquia, y urban places de
  Marruecos por provincia/prefectura. La vista
  `/countries/` abre primero el padre y despues el hijo cuando detecta ese salto
  de nivel.

Francia usa estas extensiones para insertar `Metropolitan France` y
`Overseas France` como nivel 1, colgar regiones metropolitanas en nivel 2,
colgar departamentos metropolitanos y ultramarinos en nivel 3, y sumar al pais
las metricas de los departamentos de ultramar. Tras modificar esos seeds,
sincroniza y valida la config SQL antes de popular:

```powershell
py manage.py sync_scraping_configs france --force
py manage.py validate_subdivision_configs france
```

## Comandos utiles

### Validar configuraciones

```powershell
py manage.py validate_subdivision_configs
py manage.py validate_subdivision_configs spain morocco
```

La validacion desde la web o CLI actualiza `ScrapingConfig.is_valid` y
`validation_error`; si una configuracion estaba en `Fallo`, una validacion
correcta borra el error anterior y la deja en `Validado`.

### Limpiar datos scrapeados de una configuracion

```powershell
py manage.py clear_config_data spain
```

`clear_config_data` recibe el `CountryCode` real usado en `AdminArea.country_code`
y borra esas filas. Tambien acepta el `slug` de la configuracion SQL por
compatibilidad, pero lo resuelve antes al `country_code` guardado en
`ScrapingConfig`. La accion web `Limpiar` se lanza desde el `slug` de la fila,
pero ejecuta el comando con ese `CountryCode` real. La accion se permite cuando
no hay operacion activa para esa configuracion (validar, popular o limpiar) y
el `CountryCode` tiene al menos una fila `AdminArea`; no depende de que el
estado visual sea `Populado`. Deja las configuraciones SQL afectadas pendientes
de validar. No borra filas `ScrapingConfig` ni
`NuevoAdminArea`; las relaciones derivadas que apunten a esos `AdminArea` se
limpian segun las reglas del modelo. El borrado se hace por SQL en lotes
pequenos, primero limpiando FKs/M2M dependientes y luego eliminando los
`AdminArea`, para que SQLite no falle con `too many SQL variables` y para que
sea mas rapido que el collector completo de Django. Durante la tarea escribe
progreso por lote (`Limpiando: lote=... borradas=.../...`), visible en consola
y en el log de la tarea web. En la web, `Limpiar` se ejecuta dentro de una
transaccion: si se pulsa `Parar` antes de terminar, el borrado pendiente se
revierte y la fila recupera sus datos y su estado anterior.

### Sincronizar TOML semilla por pais con SQL

```powershell
py manage.py sync_scraping_configs
py manage.py sync_scraping_configs spain --force
py manage.py sync_scraping_configs --force
py manage.py sync_scraping_configs --to-toml --output-dir .tmp-config-export
```

Desde la web, cada editor `/configs/<slug>/` tiene el boton `Exportar TOML`.
Ese boton llama a `/configs/<slug>/export-toml/` y escribe el contenido SQL
actual en `ciudades_del_mundo/subdivisions/<slug>.toml`. Es una exportacion de
puente para revisar o versionar semillas; no cambia la regla runtime: validar y
scrapear leen la fila SQL `ScrapingConfig.content`.

La pantalla `/configs/` tambien tiene `Importar TOML`. Esa accion lanza una
tarea en segundo plano equivalente a:

```powershell
py manage.py sync_scraping_configs --force
```

Sobrescribe las filas SQL `ScrapingConfig` con los TOML semilla presentes en
`ciudades_del_mundo/subdivisions/*.toml`; no popula datos `AdminArea`.
En `/configs/<slug>/` tambien existe `Importar TOML`, que importa solo
`ciudades_del_mundo/subdivisions/<slug>.toml` a la fila SQL de ese pais sin
scrapear ni limpiar datos.

Para reinstanciar solo la configuración inicial de España desde `subdivisions/spain.toml`:

```powershell
py manage.py migrate
py manage.py sync_scraping_configs spain --force
py manage.py validate_subdivision_configs spain
py manage.py scrape_subdivisions --list-pages spain
py manage.py scrape_subdivisions_with_assets spain
```

La configuracion SQL de España separa las localidades por profundidad: provincias con
`lowest_level = 3`, comunidades uniprovinciales con `lowest_level = 2`, y
Ceuta/Melilla con `lowest_level = 2`. Esto evita que las localidades de Ceuta
y Melilla se importen un nivel demasiado profundo o dependan de una raíz
sintética `spain_spain`.

Para reinstanciar las configs tocadas en la configuracion de Francia, Marruecos
y Sahara Occidental desde sus TOML de `subdivisions/`:

```powershell
py manage.py sync_scraping_configs france morocco westernsahara --force
py manage.py validate_subdivision_configs france morocco westernsahara
```

Para reinstanciar Italia, Marruecos, Portugal y Tunez tras los cambios de
niveles/padres en localidades, urban places y division municipal:

```powershell
py manage.py sync_scraping_configs italy morocco portugal tunisia --force
py manage.py validate_subdivision_configs italy morocco portugal tunisia
```

Portugal se configura como `Pais > Distrito/Region autonoma > Municipio >
Parroquia > Ciudad/localidad`: `/portugal/admin/` empieza en L1, cada
`/<distrito>/admin/` empieza en L2, y las paginas `/<distrito>/` persisten
`table#ts` en L4 mientras usan `table#tl` solo como contexto municipal L2.
Cuando el HTML de una localidad trae `(... in: Parroquia)`, esa pista enlaza la
localidad a la parroquia L3. Si la fila solo publica municipio, la localidad se
conserva bajo el municipio porque CityPopulation no da una parroquia segura.

El runtime usa SQL como unica fuente operativa. `sync_scraping_configs` importa
o exporta TOML por pais desde `ciudades_del_mundo/subdivisions/*.toml`. El
repositorio de scraping no hace fallback a TOML: usa `sync_scraping_configs`
solo para crear o refrescar explicitamente las filas SQL de `/configs/`.

La carpeta local `ciudades_del_mundo/subdivisions/` es opcional despues de
sincronizar. Si se borra, no afecta al scraping ni al arranque mientras las
filas SQL de `/configs/` ya existan. Para reconstruir o refrescar `/configs/`,
restaura antes esos TOML y ejecuta:

```powershell
py manage.py sync_scraping_configs --force
```

Algunas paginas compuestas de CityPopulation usan una primera tabla solo como
contexto del padre. En esos casos la config semilla puede usar
`include_tables = ["ts"]` y `table_levels = { ts = 4 }` para persistir solo la
segunda tabla en el nivel correcto, manteniendo el padre que expone la web.
Si una pagina `cities` no tiene filas `tbody` en `table#tl` pero si un unico
total en `table#tl > tfoot`, el parser puede usar ese total como
`major_subdivision` del bloque cuando la infosection esta desactivada; este
patron cubre paginas de centros urbanos por provincia como Netherlands.
Para el esquema v2 nuevo, usa preferentemente `include = { ... }` y
`force_highest_level`; `include_tables` y `table_levels` quedan como
compatibilidad de configs antiguas y casos muy concretos.

La carpeta `ciudades_del_mundo/html/` contiene muestras offline de formatos
CityPopulation para Belgium, Italy, Spain y France. Sirven para probar parsers y
enlazado sin red; no son fuente runtime ni sustituyen a `ScrapingConfig`.

### Ver las URLs que se van a scrapear

```powershell
py manage.py scrape_subdivisions --list-pages spain
```

### Ejecutar scraping

```powershell
py manage.py scrape_subdivisions spain
py manage.py scrape_subdivisions spain morocco portugal
py manage.py scrape_subdivisions spain france --country-workers=2 --page-workers=4
py manage.py scrape_subdivisions --seed-assets-from-pages spain
py manage.py scrape_subdivisions_with_assets spain --page-workers=4
py manage.py scrape_subdivisions_with_assets spain --resume --page-workers=4
py manage.py scrape_subdivisions_with_assets spain --ai-enrich --page-workers=4
py manage.py scrape_subdivisions_with_assets spain --page-workers=1  # modo secuencial exacto
```

El scraping descarga paginas CityPopulation independientes en paralelo dentro
del bloque actual con `--page-workers` (por defecto `4`, configurable con
`CIUDADES_SCRAPE_PAGE_WORKERS`). El parseo, los eventos `FOUND`, la siembra de
assets y la escritura SQL se mantienen en el orden de la configuracion, por lo
que no cambia la funcionalidad ni los datos extraidos; solo se solapa la espera
de red. Si una misma URL aparece repetida, se reutiliza el HTML y el parseo de
esa pagina para no procesarla dos veces. Usa `--page-workers=1` si
quieres reproducir el comportamiento secuencial antiguo.

Antes de enlazar entre bloques o persistir, cada bloque v2 se valida en memoria.
Un fallo genera `SCR-BLOCK-001` y un `.txt` bajo
`.web_scrape_block_errors/<pais>/` con las URLs implicadas, el HTML crudo y la
lista de errores. Ese archivo esta pensado para pegarlo entero en una
investigacion posterior sin repetir el scrapeo. Despues del enlace global entre
bloques hay una segunda validacion: si cualquier entidad final de nivel mayor
que 0 queda sin `parent_code` o apunta a un codigo inexistente, se genera
`SCR-LINK-001` y otro `.txt` en la misma carpeta del pais, tambien antes de
escribir en BBDD.

`--country-workers` permite scrapear varias configuraciones a la vez en
`scrape_subdivisions`; por defecto vale `1` y tambien se puede configurar con
`CIUDADES_SCRAPE_COUNTRY_WORKERS`. Las descargas y parseo de paises se solapan,
pero las fases de escritura SQLite se serializan usando
`.web_sqlite_write.lock` para evitar bloqueos y escrituras cruzadas entre tareas
web o procesos CLI. El bloqueo real es un lock del sistema operativo sobre el
archivo; si un proceso muere y el archivo queda en disco, no bloquea futuros
scrapeos. No se combina con `--seed-assets-from-pages`; la siembra de
Wikimedia/Commons queda en modo secuencial. Los comandos envoltorio
`repopulate_configs` y `validate_and_scrape_configs` tambien aceptan
`--country-workers`; desde la web, `Popular todo` y `Popular no populados` usan
`--country-workers=1` para no solapar paises dentro de un mismo subproceso web.

El flujo normal de popular no vacia antes el pais. La persistencia compara la
foto scrapeada completa contra SQL: crea filas nuevas, actualiza solo las filas
modificadas, conserva intactas las que no cambian y borra al final las filas
que ya no aparecen mediante `delete_missing`. `--clear-first` queda como opcion
explicita de mantenimiento en `scrape_subdivisions_with_assets`; la accion web
`Re-popular` ya usa el guardado incremental y no limpia antes de popular.
La consola usa cinco pasos para diagnostico: `paso 1/5` scrapea bloques,
`paso 2/5` vincula bloques y asigna la ciudad mas poblada, `paso 3/5` consulta
recursos Wikimedia/Wikidata, `paso 4/5` asigna recursos visuales y `paso 5/5`
guarda o confirma BBDD. Tras el ultimo `FOUND ... entities`, esos pasos
distinguen espera de red, trabajo de enlace, fallback de Commons y escritura
local. La ciudad mas poblada no se valida en el bloque aislado del paso 1:
se calcula despues de enlazar los bloques, usando el nivel descendiente mas alto
disponible en cada rama.
En `scrape_schema_version = 2`, las paginas raiz con `source = "cities"` pueden
usar `path = [""]` y empiezan en nivel 0 aunque la URL no termine en `/cities`;
si una pagina con forma `cities` vive bajo `/admin` y persiste `infosection`,
tambien se trata como raiz de nivel 0. Durante el enlazado, cualquier
`parent_code` igual al propio `code` se repara con el padre por prefijo mas
especifico antes de validar. Si una pagina declara `include_root = false`, esa
opcion tiene prioridad aunque `include.infosection` sea verdadero, y la
validacion de bloque no exige la infosection como seccion persistida.
`source` solo selecciona el formato/parser HTML; no fuerza ni presupone que la
ruta sea `/cities/` o `/admin/`. Las rutas anidadas que acaban en `/admin` no
arrancan como raiz por defecto y deben declarar su nivel si la cadena de bloques
no lo determina. El generador web usa la misma regla al decidir si escribe
`force_highest_level`; elegir `admin` o `cities` en el formulario no rellena ni
presupone la ruta.
Si el equipo se ralentiza durante un scraping, revisa primero procesos Python
activos y los logs de `.web_task_logs/<pais>/`: una tarea web o CLI viva puede
seguir consumiendo CPU aunque el navegador parezca quieto. Los logs locales de
`.web_task_logs/`, `.web_scrape_block_errors/`, `.web_task_progress/` y
`.web_scrape_resume/` caducan automaticamente a los 90 dias. Para diagnostico o
equipos justos, baja temporalmente a `--page-workers=1` y `--country-workers=1`.

La persistencia del scrapeo tambien usa operaciones masivas: guardado por nivel,
busqueda de filas existentes en lotes compatibles con SQLite, creacion masiva de
filas nuevas, `bulk_update` solo para filas modificadas y agrupado por los
campos que realmente cambiaron, borrado rapido de filas
ausentes con la misma limpieza segura de relaciones que `clear_config_data`, y
limpieza de `VisualAsset` solo para esas areas eliminadas. La ciudad mas poblada
se calcula en memoria durante el paso 2 con un indice bottom-up de descendientes
por nivel y se persiste dentro de `save_many`, despues de crear todos los
niveles para que la FK pueda apuntar a hijos recien insertados. No debe recorrer
el subarbol completo por cada fila en paises grandes.
Si `save_many` falla por una escritura transitoria, el caso de uso reintenta el
guardado hasta 10 veces seguidas antes de abortar. El comando escribe
`bloque de persistencia guardado completamente` cuando el bloque SQL principal
queda persistido.

`--resume` reutiliza checkpoints locales de paginas completadas para una
recuperacion manual por CLI y solo descarga las paginas pendientes. Los
checkpoints viven en `.web_scrape_resume/`, estan ignorados por Git, se invalidan cuando cambia el
contenido SQL de la configuracion y se eliminan al terminar correctamente.

El fallback generico de Commons para assets solo se ejecuta automaticamente
cuando un QID ya devolvio algun recurso en Wikidata y falta otro tipo solicitado
por la configuracion. Para forzar busqueda por nombre incluso en QIDs sin
ningun recurso Wikidata, usa `visual_assets.commons_fallback_for_empty_qids =
true` en la configuracion TOML SQL; puede ser lento en paises con muchos
registros de primer nivel.

### Enriquecer textos dinamicos con IA

```powershell
$env:CIUDADES_AI_API_KEY = "..."
$env:CIUDADES_AI_MODEL = "..."
py manage.py enrich_ai_texts spain --infer-entity-types --translate-names --translate-entity-types --describe-assets
py manage.py enrich_ai_texts spain --translate-area-names --limit 50
```

`CIUDADES_AI_BASE_URL` es opcional y por defecto apunta a un endpoint
compatible con `/v1/chat/completions`; `CIUDADES_AI_TIMEOUT` controla el timeout
en segundos. El enriquecimiento tambien puede activarse al scrapear con
`--ai-enrich`. En ese modo el scraper primero aplica inferencias revisadas ya
guardadas y, si encuentra tipos incompletos como `Prov`, pide a la IA una forma
canonica en singular ingles (`Province`, `Municipality`, etc.) usando pais,
nivel y ejemplos de entidades. El texto original queda en
`AdminArea.raw_entity_type` y la forma canonica en `AdminArea.entity_type`.

Importante: antes de scrapear con este esquema, ejecuta `py manage.py migrate`. Si el scrapeo muestra `table ciudades_del_mundo_adminarea has no column named raw_entity_type`, la base local esta en una version anterior a `0020_dynamic_ai_texts`; aplica migraciones y vuelve a lanzar el comando.

Si `makemigrations` propone una migracion del tipo `0022_remove_visualassettranslation_asset_and_more.py` que elimina `VisualAsset` o `VisualAssetTranslation`, no la apliques: es una migracion destructiva generada porque faltaban las clases Django de esos modelos en `models.py`, aunque las tablas ya existen por `0019_visual_assets`. El arreglo correcto es restaurar esas clases, borrar la migracion 0022 generada localmente y comprobar de nuevo con `py manage.py makemigrations --check --dry-run`.

Las traducciones dinamicas se guardan en `DynamicTranslation` y tienen prioridad
en la UI cuando estan activas y no requieren revision. Gettext queda para textos
estaticos de interfaz. Las descripciones de banderas, escudos y sellos se
guardan en `VisualAssetTranslation.description` y `blazon`; el proveedor recibe
la URL de imagen cuando existe, pero el resultado sigue marcado con metadatos de
origen/modelo para poder revisarlo. Los módulos externos a
`services/visual_assets.py` deben escribir esas filas mediante el helper público
`upsert_visual_asset_translation`, no importando el helper privado
`_upsert_translation`.

### Reparar banderas y escudos

```powershell
py manage.py ensure_visual_assets spain
py manage.py ensure_visual_assets --all
py manage.py ensure_visual_assets --country-subdivisions spain --levels 1,2
py manage.py ensure_visual_assets --admin-area spain_cat
py manage.py ensure_visual_assets spain --no-citypopulation-fetch
```

El comando manual usa Wikidata/Commons como fuente principal para banderas y
escudos. Para paises raiz, primero respeta el `wikidata_id` configurado en
SQL/TOML y los `[visual_assets.flag]`/`[visual_assets.coat]` declarados como
`wikidata_id`, `commons_filename` o `remote_url`; despues hace busqueda
Wikidata si falta el QID. Tambien admite `[[visual_assets.admin_areas]]` para
assets curados de ciudades/subdivisiones cuando Wikidata no expone P41/P94/P158.
Si un QID concreto no expone P41/P94/P158, el servicio puede buscar en Commons
por nombre y tipo (`flag`, `coat`, `seal`) para completar huecos como escudos
de territorios cuyo item no tiene la propiedad de imagen. En
`scrape_subdivisions_with_assets`, ese fallback por busqueda se limita a los
niveles pedidos con `--subdivision-asset-levels` o, si no se indica nada, al
nivel 1 para no disparar miles de consultas. `ensure_visual_assets --admin-area`
puede usarlo para una entidad concreta de cualquier nivel.
No se deben anadir fallbacks hardcodeados en vistas: casos como Sahara
Occidental, donde la bandera y el escudo viven en QID distintos, se resuelven
en el contenido SQL de la configuracion o en el TOML semilla del pais bajo
`subdivisions/` antes de sincronizar. CityPopulation queda
solo como respaldo para imagenes explicitamente etiquetadas o con nombre de
archivo claro, porque sus paginas incluyen iconos de idioma que no son la
bandera del pais. Los ficheros
no se descargan por defecto: se guarda `commons_filename`, `remote_url`, QID y
traducciones/descripciones en SQL, y la UI usa directamente URLs de Wikimedia
Commons (`Special:FilePath`). Para refrescar el caso de Sahara Occidental tras
sincronizar la configuracion semilla:

```powershell
py manage.py sync_scraping_configs westernsahara --force
py manage.py ensure_visual_assets westernsahara --no-citypopulation-fetch
py manage.py ensure_visual_assets --country-subdivisions westernsahara
```

La ficha selecciona la traduccion del idioma activo
y solo muestra texto heraldico/vexilologico curado. `media/visual_assets/` queda solo como fallback
legacy o para reparaciones locales explicitas.
`scrape_subdivisions_with_assets` reutiliza el HTML ya descargado por el
scraping para registrar assets de CityPopulation del pais y de subdivisiones
`AdminArea` de todos los niveles scrapeados por defecto. Si falta bandera,
escudo o sello, los resuelve en ese momento contra Wikidata/Commons en servidor,
guardando URL remota y traducciones, sin descargar los ficheros ni hacer que el
navegador busque nada. Primero usa los QID `data-wd` que vengan en la
pagina de CityPopulation; despues crea un indice SPARQL por QID de pais con
`P17`, `P31`, `P131`, `P41`, `P94` y `P158`, y vincula los resultados a cada
`AdminArea.id` persistiendo el QID en `ciudades_del_mundo_visual_asset`.
Usa `--skip-subdivision-assets` o `--subdivision-asset-levels` para ajustar ese
coste. Si algunos recursos quedan sin asignar, se reportan como aviso
`SCR-ASSET-W001` y el proceso continua. La seleccion de imagenes Wikidata
prioriza claims vigentes y recientes para no asignar banderas historicas
antiguas cuando existe una bandera moderna. Las descargas locales solo existen como opt-in (`--download` o
`--download-assets`) y no deben usarse en el flujo normal porque aumentan mucho
los 429 de Wikimedia.
Para una cobertura completa de un pais, los assets esperados abarcan todas las
entidades `AdminArea` que vengan de CityPopulation: pais, comunidades o regiones,
provincias, municipios, localidades y cualquier otro nivel scrapeado en la
configuracion. Si una pagina de localidades no asocia candidatos de
CityPopulation, el proceso debe continuar con Wikidata/Commons por QID, indice o
busqueda por entidad persistida. Los errores HTTP 429 se deben tratar como
reintentables, reutilizando cache y pausas; en SVG, si Commons limita el original,
el descargador puede caer a una miniatura PNG. No son ausencia definitiva de
bandera o escudo.
Si `/countries/` no muestra un asset nacional, revisa primero que la fila SQL
tenga `commons_filename`, `remote_url` o un `local_path` usable. La tarjeta y el
panel de pais no buscan banderas fuera de la BBDD: no hidratan desde Wikidata, no
usan seeds TOML y no escanean carpetas locales si no hay fila SQL. Cuando falta
el asset registrado, muestran el placeholder de bandera o de escudo/sello.
`ensure_visual_assets` queda para busqueda explicita en Wikidata/Commons y
`--repair-local-only` para reparacion legacy de ficheros locales.

### Asignar capitales

```powershell
py manage.py assign_admin_capitals
```

### Construir subdivisiones derivadas

```powershell
py manage.py build_new_subdivisions --country-id spanish_federal_republic
py manage.py build_new_subdivisions --country-id nuevo_imperio_romano
py manage.py build_derived_subdivisions spain --force
```

Las recetas de `new_subdivisions/*.py` pueden ajustar la poblacion usada para
representacion y marcar provincias especiales:

```python
POPULATION_INDEXES = {
    2: {"Madrid": 0.5},  # Madrid L2 y descendientes computan al 50%
}

PROVINCE_STATUSES = {
    2: {
        "Costa Rica": {"status": "dependencia", "depends_on": "Nicaragua"},
        "Fernando Poo": "territorio",
    }
}
```

Tambien se puede declarar en una entrada concreta con `population_index`,
`province_status` y `depends_on`.

Las recetas pueden declarar `ROOT_NAME` o `COUNTRY_NAME` para fijar el nombre
visible del nodo raiz. `nuevo_imperio_romano.py` usa `ROOT_NAME = "Nuevo Imperio
Romano"` y define las prefecturas de Hispania y Macaronesia.

La nueva base declarativa vive en SQL y TOML:

- `/new-countries/` conserva dos pasos: primero muestra tarjetas de pais fuente
  poblado con el mismo formato visual que `/countries/`; al seleccionar una se
  abre un recuadro con los paises nuevos de ese origen. Esa lista interna debe
  ocupar todo el ancho del recuadro, igual que la lista inicial de paises.
  Dentro de ese recuadro, cada tarjeta representa un unico contenedor
  `DerivedCountry`, no una config suelta. Muestra codigo, poblacion y terreno
  desde el root `NuevoAdminArea` si
  existe; si todavia no esta construido, usa la suma de las configs de primer
  nivel. Clicar una tarjeta carga el detalle compartido mediante
  `/api/new-country-containers/<pais_nuevo>/`: en escritorio, los datos del
  pais quedan a la izquierda y la grafica con la tabla de subdivisiones a la
  derecha, en la misma fila visual. Las cajas de `Nivel 1` deben usar el mismo
  tamano/proporcion que las cajas abiertas para `Nivel 2` y niveles inferiores.
  En paises no materializados, el
  arbol navegable incluye configs hijas y subdivisiones fuente/asignadas; las
  filas y registros con hijos exponen URL de detalle hasta que la entidad no
  tenga hijos. La unica diferencia visual permitida frente a `/countries/` son
  los enlaces compactos de editar configuracion y exportar Excel. No debe
  volver a mostrar `Crear`, `Ver`, acciones de build por config ni una tarjeta
  por cada `DerivedCountryConfig`.
  Los cambios futuros en graficas, tablas, selector de niveles y paneles de
  porcentajes deben hacerse en el renderer/contrato compartido por `/countries/`
  y `/new-countries/`, salvo peticion explicita en contra.
- En `/countries/` y en el navegador interno de `/new-countries/`, el selector
  de nivel de subentidad actualiza a la vez la tabla, las graficas y las
  tarjetas/resumen. Solo se ofrecen niveles cuyo resultado sea de 1000 filas o
  menos: por ejemplo, en Espana se puede cambiar de CCAA a provincias para ver
  las 52 provincias, y al abrir una provincia se muestran sus municipios.
- La ficha del contenedor usa la ruta canonica
  `/new-countries/<pais_fuente>/<pais_nuevo>/`; la ruta antigua
  `/new-countries/<pais_nuevo>/` redirige a ella. En esa ficha, los campos
  `Nombre` y `Descripcion` se editan directamente; el codigo queda fijo.
  Crear y editar estos datos base comparten el mismo parcial de campos: codigo
  y nombre comparten la primera fila, y descripcion ocupa todo el ancho.
- Dentro de cada pais, `Nueva entidad` crea una fila `DerivedCountryConfig` en
  la ruta canonica
  `/new-countries/<pais_fuente>/<pais_nuevo>/configs/<division>/`. El codigo
  derivado ya no se edita: se toma del contenedor `DerivedCountry.slug`. La
  seleccion visual genera un bloque `[[entities]]` y bloques
  `[[entities.include]]` / `[[entities.subtract]]` por nivel y pais.
- Crear un pais nuevo desde una tarjeta fuente usa
  `/new-countries/<pais_fuente>/new/`; el pais fuente queda oculto y fijado por
  esa URL. Los campos visibles de codigo se muestran como `Codigo`, se
  normalizan en el navegador a mayusculas sin espacios ni acentos, y se guardan
  con la normalizacion interna compatible con las rutas. En
  `/new-countries/<pais_fuente>/<pais_nuevo>/configs/new/`, los campos base
  estan en un recuadro propio y la seleccion de entidades fuente en otro,
  siguiendo el formato del editor `/groups/subdivisions/<pais>/<codigo>/`. El
  campo `Nivel` no se muestra en el formulario de `Nueva entidad`; se calcula
  automaticamente desde la jerarquia y solo se escribe en el TOML generado. El
  interruptor `Usar subdivisiones` cambia el selector: apagado muestra
  entidades ya creadas del mismo pais nuevo, cargadas desde
  `/new-countries/<pais_fuente>/<pais_nuevo>/configs/<codigo-o-new>/created-entities/`,
  para convertirlas en hijas de la entidad que se guarda; el nivel se deriva
  automaticamente desde `parent_config_slug` y se reescribe en cascada en las
  entidades reparentadas. Al guardar se calculan y persisten `area_km2`,
  `pop_latest` y `density` en el primer `[[entities]]`; esa tabla muestra la
  fila `NuevoAdminArea` materializada o, si todavia no existe, esos valores
  guardados en TOML sin volver a sumar selecciones al listar. Encendido muestra
  `DerivedSubdivision` creadas en
  `/subdivisions/` de cualquier pais fuente para usarlas como hijos directos de
  esa division. Ese listado se carga por el endpoint JSON
  `/new-countries/<pais_fuente>/<pais_nuevo>/configs/new/created-subdivisions/`
  y se presenta como tabla separada, buscable, filtrable por pais y paginada;
  el filtro de pais usa Select2 con busqueda normalizada sin distinguir acentos
  ni mayusculas/minusculas y se alimenta solo de paises con
  `DerivedSubdivision` creadas. La barra de filtros ocupa una fila completa del
  mismo ancho que la tabla: Buscar usa el espacio sobrante, Pais es ancho,
  Filas queda compacto y la paginacion conserva el hueco derecho visible aunque
  solo haya una pagina. No hay tabla de subdivisiones reales; sus columnas visibles son seleccion,
  nombre, pais, tipo sin sufijos de nivel, terreno, poblacion y densidad
  calculados desde la fila materializada o, si todavia no existe, desde la vista
  previa del TOML. La seleccion visual conserva el pais de cada subdivision
  creada y el TOML se guarda con un bloque `[[entities.include]]` por
  `country_code` cuando se mezclan paises, incluyendo el `level` asignado a
  esas subdivisiones dentro del arbol del pais nuevo. La columna visible
  `Nombre` de esas filas omite sufijos entre parentesis aunque el texto
  original siga disponible para buscar. `Capitales` usa el mismo selector
  buscable con badges que `/subdivisions/`: guarda IDs en `capitals = [...]`,
  busca por nombres mostrados/alias localizados y, cuando hay subdivisiones
  creadas seleccionadas, limita las opciones a sus municipios/fuentes aunque
  pertenezcan a otros paises. Con `Usar
  subdivisiones` activo y sin subdivisiones marcadas, el selector no ofrece
  capitales.
- `/new-countries/<pais_fuente>/<pais_nuevo>/configs/<codigo>/` usa el mismo
  formulario visual que la creacion. Al guardar, re-renderiza el TOML desde los
  campos visuales; si cambia `Codigo`, tambien actualiza las referencias
  `parent_config_slug` de las configs hijas del mismo pais nuevo. El `Codigo`
  visible acepta letras/numeros ASCII en mayusculas, `_` y `-`; la ruta usa el
  slug tecnico equivalente con `_`.
- Las configuraciones de `new-countries` no tienen ruta propia `view/`: la URL
  de config es siempre la de edicion. En la ficha
  `/new-countries/<pais_fuente>/<pais_nuevo>/`, la tabla de entidades se
  organiza como arbol plegable por niveles (`NV 1` con sus hijos `NV 2+`):
  clicar la fila abre la edicion de la configuracion, no hay botones `Crear`,
  `Ver`, `Excel` ni `Configuracion`, y la columna `Acciones` ofrece `Clonar`
  y `Borrar` mediante POST. `Clonar` duplica la configuracion y cambia el
  codigo local visible a `COD-COPIA`, usando un slug tecnico como `cod_copia`.
  `Borrar` no elimina configs hijas: las hijas directas se reasignan al padre
  de la config borrada y sus niveles y codigos jerarquicos, junto con los
  descendientes, se recalculan en cascada.
  Las subdivisiones fuente o creadas que estan
  asignadas a una entidad salen como filas hijas de solo lectura con codigo
  limpio y color diferenciado; no muestran el valor tecnico
  `derived-subdivision::<pais>::<codigo>` ni padre; si forman parte de la
  jerarquia muestran su `NV` asignado. La tabla debe
  encajar en el ancho del panel sin scroll lateral; sus columnas visibles son
  nivel para configs, codigo jerarquico o codigo asignado, nombre, tipo,
  `Terreno`, poblacion y densidad, con `Acciones` suficientemente ancha para
  que `Clonar` y `Borrar` quepan en una sola fila. Las metricas salen de la
  fila `NuevoAdminArea` materializada o de los valores guardados en TOML y se
  muestran sin separador de miles, con coma decimal. Las tarjetas de
  `/new-countries/` ya no muestran `Crear`, `Ver` ni acciones por config; solo
  controles compactos para exportar Excel del contenedor completo y abrir su
  configuracion.
- La ficha `/new-countries/<pais_fuente>/<pais_nuevo>/` tambien ofrece
  `Exportar Excel` en la cabecera del contenedor. Si el pais nuevo ya esta
  materializado, descarga todas sus filas `NuevoAdminArea`, no solo las configs
  visibles. La hoja `Jerarquia` es una tabla ancha por rutas del arbol:
  las primeras columnas son `Nombre`, `Poblacion`, `%`, `Ranking poblacion`,
  `Terreno`, `%`, `Ranking terreno`, `Densidad`, `Capital`, `Poblacion capital`,
  `% capital`, `Ranking capital`, `Ciudad mas poblada`, `Poblacion ciudad mas
  poblada`, `% ciudad mas poblada`, `Ranking ciudad mas poblada` y `Num
  municipios`; las siguientes repiten esos datos para `NV1`, luego para `NV2`
  y asi sucesivamente. Cada fila representa una ruta jerarquica; cuando el pais
  o un padre aparece en varias rutas consecutivas, sus celdas del bloque se
  combinan verticalmente para no repetir el texto. Todos los hijos de un
  elemento de nivel superior salen agrupados antes de pasar al siguiente
  hermano. `Num municipios` se calcula desde `municipios_originales` acumulados,
  incluyendo los municipios de hijos cuando el padre no los tiene directos, y la
  ciudad mas poblada se resuelve desde esos municipios cuando no hay fila
  materializada. El XLSX contiene solo esa hoja de datos, centra el contenido y
  ajusta anchos de columna; no anade una hoja `Resumen` ni usa tabla
  estructurada cuando hay celdas combinadas. Si todavia no hay build, cae al
  resumen de configs como modo de reserva.
  Cada descarga guarda una linea JSONL
  de auditoria en
  `.web_export_logs/new-countries/<pais_fuente>/<pais_nuevo>.jsonl` con conteo
  de filas, niveles y subdivisiones legales, y una copia del XLSX en
  `excels/new-countries/<pais_fuente>/`.
- `Crear` encola `py manage.py build_new_subdivisions --country-id <codigo>`.
  Si no existe receta legacy para ese codigo, el comando resuelve la fila SQL
  `DerivedCountryConfig` y materializa el arbol con
  `ciudades_del_mundo.services.derived_country_builder`. Los codigos de las
  entidades creadas se derivan de la jerarquia de configs: una entidad de primer
  nivel usa su codigo local (`ARA`) y sus hijas reciben el prefijo de ese padre
  (`ARA-VAL`). El editor conserva siempre el codigo local (`VAL`), no el prefijo
  calculado. Si al crear o editar un padre se selecciona como hijo una config que
  ya usa el mismo codigo local, el sistema renombra solo su identificador tecnico
  (`ara` -> `ara_ara`) para que el arbol resultante sea `ARA` > `ARA-ARA`, sin
  obligar al usuario a cambiar el codigo local del hijo.
- `Excel` encola `py manage.py export_nuevoadmin_excel --country-id <codigo>`
  cuando el arbol ya existe. Ambas tareas se lanzan con la ventana emergente de
  tareas y no sacan al usuario de la pagina.
- `/groups/` lista `SubdivisionGroup`, grupos TOML reutilizables que podran
  referenciarse desde configuraciones de nuevos paises.

Las recetas Python antiguas siguen en `ciudades_del_mundo/new_subdivisions/` y
`ciudades_del_mundo/historical_divisions/` para compatibilidad con
`build_new_subdivisions`. Los TOML activos de la nueva base declarativa estan en
`ciudades_del_mundo/new_country_configs/*.toml` y
`ciudades_del_mundo/subdivision_groups/groups/<pais>.toml`; los bundles de
subdivisiones legacy estan separados en
`ciudades_del_mundo/subdivision_groups/subdivisions/<pais>.toml`.
`/new-countries/`, `/groups/` y `/subdivisions/` tienen `Importar TOML`, que
encola una tarea por fichero semilla y actualiza solo filas SQL de
configuracion; si una semilla falla, las demas tareas siguen en la cola. Las
acciones de importacion de estos listados usan la misma ventana emergente de
tarea que el scraping, por lo que no redirigen a `/tasks/` ni sacan al usuario
de la pagina actual. Las copias `*_old/` son backups locales ignorados por Git.

### Exportar

```powershell
py manage.py export_nuevoadmin_csv --country-id spanish_federal_republic
py manage.py export_nuevoadmin_excel --country-id spanish_federal_republic
```

Los Excel se guardan por defecto en la subcarpeta `excels/`. Puedes cambiarla con `--output-dir`.
El Excel principal usa una hoja `Jerarquia` con una tabla ancha por rutas del
arbol. El primer bloque de columnas describe el pais con `Nombre`, `Poblacion`,
`%`, `Ranking poblacion`, `Terreno`, `%`, `Ranking terreno`, `Densidad`,
`Capital`, `Poblacion capital`, `% capital`, `Ranking capital`, `Ciudad mas
poblada`, `Poblacion ciudad mas poblada`, `% ciudad mas poblada`, `Ranking
ciudad mas poblada` y `Num municipios`; los bloques siguientes repiten esas
columnas para `NV1`, `NV2` y niveles posteriores. Cada fila baja por una rama
del arbol, por lo que todos los hijos de un elemento de nivel superior aparecen
agrupados antes de pasar al siguiente hermano; los bloques repetidos de pais o
padre se combinan verticalmente en Excel. `Num municipios` se calcula desde
`municipios_originales` acumulados, incluyendo los hijos del padre. Las celdas
se centran y las columnas se ensanchan automaticamente. No se exportan columnas
tecnicas, hoja `Resumen`, tablas estructuradas con merges ni tablas auxiliares
a la derecha.


### Reglas de preservacion UI

No modifiques tamaños de recuadro, proporciones, anchuras de tablas, espaciados,
layouts o comportamientos ya existentes salvo que la peticion indique de forma
concreta ese cambio. Las correcciones deben ser quirurgicas y conservar lo que ya
funcionaba.

## Interfaz web local

Arranca el servidor con:

```powershell
py manage.py runserver 127.0.0.1:8000
```

La raiz `/` abre el panel operativo. Sus datos cargan por la API local con
indicador de carga: `/api/countries/` alimenta las roscas de poblacion y terreno
por pais y usa solo filas `AdminArea` de nivel 0; `/api/derived/` alimenta el
resumen de paises derivados. Las rutas antiguas `/dashboard/population/` y
`/dashboard/derived/` siguen existiendo por compatibilidad.
Si un `country_code` trae varias filas `AdminArea.level=0`, la rosca las
agrupa como un unico pais para no mostrar subdivisiones como paises. El
buscador compartido filtra solo la tabla combinada, no las graficas. La tabla
combina poblacion y terreno en una sola vista. El mismo pais comparte siempre
color en ambas roscas; los colores se asignan al conjunto formado por los 10
paises mas poblados y los 10 mas extensos. Los paises sin color se agregan en
`Otros paises` dentro de cada rosca y siguen apareciendo en la tabla combinada,
que tiene un unico marcador de color, busqueda y ordenacion por columnas. Al hacer clic en un pais
de la rosca se carga `/api/countries/<country_code>/`, con datos generales,
tabla filtrable/ordenable por nivel y graficas de primer orden de poblacion y
terreno. La tabla de datos esta paginada en el navegador, permite elegir filas
por pagina, indica la columna activa de ordenacion con flechas y al cambiar de
nivel actualiza solo la tabla. Tambien incluye porcentaje de poblacion y terreno
respecto al pais. En la portada, la unica tabla que conserva scroll lateral es
`Tabla de datos`; las demas tablas compactas deben encajar en escritorio con
columnas numericas estrechas y sin salto de linea, dejando que solo nombre y
tipo puedan partir linea antes de recortar texto. `Datos generales del pais`,
`Tabla de datos` y `Subdivisiones de primer orden` ocupan el mismo ancho en
escritorio. En tablas compactas se mantiene el label completo y la flecha de ordenacion
debe aparecer siempre a la derecha del texto, sin saltar debajo del label. Las secciones de roscas
muestran las graficas arriba y las listas/leyendas filtrables debajo. Las tarjetas de
porcentaje por subdivision de primer orden muestran minipizzas con el reparto
interno de sus subdivisiones directas del siguiente nivel cuando ese nivel no
alcanza 150 filas en el pais; no se filtran por nombre de tipo de entidad. Los
datos generales usan bandera y escudo persistidos en SQL, mostrando primero la
URL remota de Wikimedia/Commons y dejando `media/visual_assets/` solo como
fallback legacy. Cualquier bandera, escudo, sello o imagen de identidad visual
debe abrir primero el recuadro emergente de previsualizacion; no debe navegar
directamente a la ficha. Desde ese recuadro, el boton `Ficha` abre en ventana
nueva la ficha interna legible
`/identity/<tipo>/entity/<entity_type>/<entity_key>/`, por ejemplo
`/identity/flag/entity/country/spain/`. Las rutas antiguas con
`visual_assets/...` siguen resolviendo el asset guardado cuando existe.
En `/countries/`, al seleccionar un pais se muestran dos recuadros: la ficha
basica y las subdivisiones directas de primer nivel. La bandera y el
escudo/sello del pais salen solo del payload SQL de `/api/countries/`; si falta
la fila `ciudades_del_mundo_visual_asset`, el navegador muestra el placeholder
de bandera o escudo sin buscar en Wikidata, Wikimedia, TOML ni carpetas locales.
Los placeholders mantienen un recuadro cuadrado como una imagen real y se
dibujan por CSS con colores del tema activo, no con emoji. Al abrir una
subdivision, el navegador agrega otra ficha con su bandera/escudo y sus hijos
directos; se puede seguir bajando con `/api/admin-areas/<area_id>/` hasta llegar
a entidades sin hijos.
La tabla general del pais puede cambiar de nivel con un selector situado en la
zona de graficas/tabla, justo encima del titulo `Tabla de datos`, solo cuando
hay mas de una opcion util. Si no se pide ningun nivel, se usa NV1. Las opciones
del selector son los nombres de tipo del nivel y solo se ofrecen niveles con mas
de 0 y hasta 500 registros en el pais. Si un nivel completo supera 500 registros
pero contiene tipos navegables con 500 o menos filas, el selector ofrece esos
tipos dentro del mismo nivel y deja de ofrecer niveles mas profundos; por
ejemplo, Espana ofrece comunidades autonomas/ciudades autonomas y provincias,
mientras Francia ofrece regiones, departamentos y distritos.
Las traducciones de nombres administrativos de Espana que no son simples cadenas
de interfaz viven en `ciudades_del_mundo/web/spain_translations.py` para mantener
juntas las equivalencias de CCAA, provincias, ciudades y tipos de entidad por
idioma (`Lleida` -> `Lérida` en espanol, `Province` -> `Provincia`, `Provinz`,
etc.). Los tipos de entidad se traducen solo con contexto de Espana para no
afectar nombres de areas ficticias. Si se agregan entradas gettext de interfaz,
compila despues con `py manage.py compile_local_messages`.
El renderizado frontend esta centralizado en `window.CiudadesCharts` dentro de
`ciudades_del_mundo/static/ciudades_del_mundo/app.js`; los contenedores
reutilizables usan el atributo `data-chart-widget`. La base de API para datos
web esta en `/api/countries/`, `/api/countries/<country_code>/`,
`/api/admin-areas/<area_id>/`, `/api/new-country-containers/`,
`/api/new-country-containers/<country_slug>/` y `/api/derived/`.
Secciones principales:

- `/configs/`: lista filas SQL `ScrapingConfig` con tablas dinamicas cargadas
  desde `/configs/table/` y `/configs/tasks/table/`; las filas se descargan una
  vez y la paginacion cambia de pagina en cliente. El ciclo visible de una
  configuracion es: `Por Validar`, `Validando`, `Validado`, `Populando`,
  `Populado`, `Limpiando` y `Fallo`. `Popular` esta disponible desde
  `Por Validar`, `Fallo` y `Validado`; desde `Por Validar`/`Fallo` lanza
  `validate_and_scrape_configs`, por lo que primero se ve `Validando` y despues
  `Populando`. Desde `Validado` lanza directamente
  `scrape_subdivisions_with_assets --page-workers=4`. El poblado normal es
  incremental; no ejecuta `Limpiar` antes de scrapear. `Populado` solo muestra
  `Limpiar`. `Limpiar` se muestra si existen `AdminArea` previos y la fila no
  esta `Validando`, `Populando` ni `Limpiando`. Los estados activos solo
  muestran `Parar`; no dejan visible `Validar` o `Popular` como botones
  deshabilitados. Al cancelar no existe estado `Parado`/`Parando`, la fila
  vuelve al estado anterior inferible. Si se cancela `Populando`, vuelve a
  `Validado`; si se cancela `Limpiando`, la limpieza se revierte porque se
  ejecuta dentro de una transaccion y la fila recupera sus datos. Durante una
  tarea de poblado, la etiqueta `Populando` mantiene ancho estable con tres
  puntos animados por JS, sin keyframes CSS que se reinicien al refrescar la
  fila, y la fila se refresca con una cadencia baja para que el estado pase a
  `Populado` al terminar. Las filas navegables de tablas soportan clic normal,
  teclado, Ctrl/Cmd-clic y clic con la rueda para abrir el contenido en otra
  pestana. El contenido clicable que carga una ficha en la misma pagina
  (graficas, tarjetas, filas de estadisticas y miniaturas visuales) abre esa
  misma ficha en otra pestana con clic de rueda. Los botones de accion no
  capturan clic con rueda salvo que sean enlaces reales a otra pagina. Las
  tareas lanzadas desde la web usan
  `scrape_subdivisions_with_assets --page-workers=4` cuando ya estan validadas
  para solapar la descarga de HTML de CityPopulation sin cambiar el orden de
  parseo/escritura. El boton `Validar` de una configuracion queda desactivado
  mientras esa validacion sigue activa. En edicion, el editor guarda
  `ScrapingConfig.content` automaticamente tras cambios en la vista Manual o
  Archivo y valida sintaxis/esquema antes de escribir. La edicion no muestra un
  boton visible de guardado ni texto permanente de autoguardado; `Guardar` solo
  aparece al crear una config nueva. Si el formulario envia `pages_json` y
  campos visibles, los campos visibles ganan para procedimiento, activacion,
  niveles forzados, padre forzado, inclusion, repeticion y suma al padre. Los
  cambios discretos como checks, selects, activar/desactivar bloques o agregar
  filas disparan guardado inmediato, y si queda un guardado pendiente al
  refrescar se intenta enviar con `sendBeacon`/`keepalive`. Cuando se cambia
  comportamiento de `app.js` o `app.css` para esta pantalla, sube tambien el
  cache-buster de `base.html` para que el navegador no mantenga el editor viejo.
  En `/configs/<slug>/`, la antigua subseccion visible de `Escudos y banderas`
  se sustituye por `Ciudades`: una tabla de ciudades unificadas configuradas y
  un modal de nuevo/editar `[[cities]]` basado en el selector buscable nativo de
  `/groups/groups/...` + badges. En la tabla, `Ciudad unificada` y `Subdivisión
  mayor` ocupan 15% cada una, `Acciones` reserva ancho para `Editar`/`Quitar` en
  una fila y `Elementos` muestra la lista de entidades seleccionadas, no un
  contador. El selector normaliza acentos y mayusculas para que busquedas como
  `tan` o `tán` encuentren nombres como `Tanger-Assilah`; el nombre de la ciudad
  unificada es obligatorio antes de guardar. Cerrar con la X descarta la copia
  temporal y solo el boton `Guardar` aplica cambios al TOML. Las filas de tabla
  no abren el editor al hacer click: hay que usar `Editar`. En el modal, la
  zona `Disponibles` filtra badges por nombre normalizado y por tipo de
  subdivision como enum. Los botones `Importar TOML` y `Exportar TOML` de esta
  subseccion leen/escriben solo `ciudades_del_mundo/cities_merge/<slug>.toml`.
  Esa subseccion esta oculta hasta que el pais tenga datos populados y el
  navegador vuelve a pedir
  `/configs/<slug>/editor-data/` al terminar `Popular` o `Limpiar`, para mostrar
  u ocultar los datos actualizados.
  Cada bloque `[[pages]]` puede desactivarse sin borrarse. El boton `Exportar
  TOML` escribe la version SQL actual en
  `ciudades_del_mundo/subdivisions/<slug>.toml` sin convertir ese fichero en
  fuente runtime.
- `/new-countries/`: base SQL para paises derivados. Lista primero tarjetas por
  pais fuente poblado y, al seleccionar una, muestra en un segundo recuadro sus
  contenedores `DerivedCountry` unicos; no muestra un boton general `Crear` ni
  tarjetas por config. Permite importar las semillas de
  `new_country_configs/*.toml` con una tarea por fichero, y la ficha del
  contenedor permite crear una `Nueva entidad` desde un arbol SQL de
  `AdminArea`. En ese arbol, marcar una subdivision sin una superior marcada
  guarda una suma; marcarla dentro de una superior marcada guarda una resta. La
  seleccion se persiste en
  `DerivedCountryConfig.content` como `include_ids`, `subtract_ids` y
  `[[selection.items]]`. La ficha del contenedor vive en
  `/new-countries/<pais_fuente>/<pais_nuevo>/` y permite editar directamente
  nombre y descripcion con el mismo parcial usado al crear el pais nuevo. Sus
  configs viven en
  `/new-countries/<pais_fuente>/<pais_nuevo>/configs/<division>/`; las rutas
  antiguas sin `<pais_fuente>` redirigen. El recuadro interno comparte el
  renderer de `/countries/`: la lista interna ocupa todo el ancho del recuadro
  y, al abrir un contenedor, el detalle en escritorio coloca datos del pais a la
  izquierda y grafica/tabla de subdivisiones a la derecha en una misma fila; la
  caja de `Nivel 1` mantiene el mismo tamano que las cajas de niveles
  navegados (`Nivel 2+`).
  Usa detalle en `/api/new-country-containers/<pais_nuevo>/`, drill-down de
  configs no materializadas en
  `/api/new-country-containers/<pais_nuevo>/config-nodes/<tree_id>/`, filas de
  subdivisiones fuente asignadas con hijos hacia `/api/admin-areas/<area_id>/`,
  y `DerivedSubdivision` seleccionadas que se despliegan hasta las unidades
  legales efectivas de cada pais fuente, proyectando `municipios_originales`
  materializados antiguos cuando sea necesario y usando el TOML como fallback.
  Mantiene enlaces compactos a la exportacion Excel completa del contenedor y a
  su configuracion. El selector de nivel compartido con `/countries/` cambia a
  la vez tabla, graficas y resumen de subentidades, lista todos los niveles
  disponibles, incluidas comunas/municipios hoja, y pagina la tabla a 20 filas
  por defecto con opciones de 50 y 100 como maximo.
  En el detalle del pais
  nuevo, `Borrar` elimina solo la config SQL seleccionada: sus hijas directas
  se reasignan al padre de la config borrada y sus niveles y codigos visibles
  se recalculan en cascada, por lo que borrar una entidad NV2 convierte sus
  hijas NV3 en nuevas NV2 con el prefijo de codigo actualizado. Con `Usar
  subdivisiones`, cada bloque `[[entities.include]]` guarda el `level` asignado
  a esas subdivisiones dentro del pais nuevo, normalmente el nivel de la
  entidad padre + 1, y la tabla hija muestra nombres limpios sin sufijos entre
  parentesis.
- `/groups/`: lista y edita `SubdivisionGroup`, grupos TOML reutilizables para
  futuras configuraciones de nuevos paises. La lista de paises usa el mismo
  formato de tarjetas que `/countries/`; al seleccionar una tarjeta se abre el
  panel de ese pais en la misma pagina, sin anadir `?country` a la URL. El
  panel seleccionado muestra dos cajas con reparto 40/60: `Agrupaciones` y
  `Nuevas divisiones`. Las acciones importar/exportar/nuevo de `Agrupaciones`
  son botones compactos de icono SVG con `title` y `aria-label` para mantenerse
  en una sola fila sin deformar la caja. Las tablas del panel agrupan buscador,
  selector de filas y selector de pagina en una misma barra cuando hay ancho
  suficiente; el selector de pagina se repite tambien al pie de cada tabla.
  La caja de agrupaciones no muestra los metadatos de pais fuente dentro del
  panel; su tabla tiene solo `Grupo` y `Grupos`, con buscador local y
  paginacion cliente de 25, 50 o 100 filas, ajustada para no necesitar scroll
  horizontal. Las filas de grupo son clicables y abren `/groups/groups/<pais>/<grupo>/`; `Exportar TOML` va en
  la botonera del panel, junto a `Importar TOML`, y escribe el bundle
  `subdivision_groups/groups/<pais>.toml`; `Nuevo grupo` abre
  `/groups/groups/<pais>/new/`. La caja derecha muestra `Importar TOML`,
  `Exportar TOML` y `Nueva subdivisión` para la seccion `/subdivisions/`; usa
  el mismo formato de tabla local: buscador, selector de filas, paginacion
  cliente y filtro por nivel. Sus filas
  salen de `NuevoAdminArea`, mapeadas al pais fuente seleccionado por
  `DerivedCountry`/`DerivedCountryConfig` cuando exista esa relacion, y tambien
  incluyen definiciones SQL `DerivedSubdivision` guardadas que aun no se han
  materializado. Esas filas pendientes abren el editor y calculan una vista
  previa de `Terreno` y `Poblacion` desde los `AdminArea` fuente seleccionados;
  si ya existe una fila `NuevoAdminArea` materializada, la tabla usa primero sus
  metricas guardadas. Sus columnas son `Nombre de la entidad`,
  `Tipo`, `Terreno`, `Población` y `Acciones`. `Clonar`
  duplica la definicion SQL con `COD-COPIA` como codigo visible y un slug
  tecnico `cod_copia`; `Borrar` es una `X` con confirmacion del navegador y
  borra por POST la definicion
  SQL `DerivedSubdivision` y, si existian, las filas `NuevoAdminArea`
  materializadas directamente desde ella y sus descendientes. Ya no muestra el
  resumen por nivel ni la columna `Sumar nivel`. Cuando una fila se puede
  resolver a su configuracion SQL, abre `/groups/subdivisions/<pais>/<codigo>/`.
  Si esa definicion SQL es mas reciente que la fila `NuevoAdminArea`
  materializada, la tabla pinta la previsualizacion de `Terreno` y `Poblacion`
  calculada desde el TOML actualizado hasta que `Popular` reconstruya los datos
  persistidos.
  El editor crea una agrupacion por pais: el encabezado muestra el codigo
  interno o `NUEVO GRUPO`, no muestra metadatos del pais ni campo `Nombre`, y el
  primer recuadro contiene el codigo interno obligatorio en mayusculas, un
  select de pais con buscador y `Añadir`. Empieza con un bloque vacio del pais
  origen que no se puede quitar; cada pais agregado crea un bloque hermano y el
  select oculta paises ya usados dentro del grupo. El boton `Guardar` aparece
  arriba y abajo del formulario. Los bloques de pais se colocan en una columna
  para dejar espacio horizontal a sus tablas. Dentro de cada bloque de pais hay una cascada de selects
  buscables desde el nivel 1 hasta `N-1`, donde `N` es el nivel maximo efectivo
  del pais; niveles residuales muy pequenos y niveles compuestos casi por
  completo por localidades o sedes no cuentan como maximo efectivo. En Espana,
  el editor ofrece provincias para seleccionar municipios, no municipios para
  seleccionar localidades. El nivel maximo no se ofrece como select porque se elige
  desde los badges del nivel anterior. El primer select carga las subdivisiones
  superiores y cada select inferior se filtra por el padre elegido en el nivel anterior.
  Cada nivel tiene su propio boton `Añadir`, las subdivisiones ya anadidas
  desaparecen de su select, y si se anade una entidad superior se eliminan del
  bloque las secciones inferiores que dependan de ella. Las opciones mientras se
  gestiona la seleccion muestran el tipo entre parentesis, como `Abla
  (Municipio)` o `Andalucia (Comunidad Autonoma)`; una vez seleccionadas, las
  filas/badges visibles muestran solo el nombre/codigo limpio, sin parentesis ni
  nivel visible. Cada pais nuevo se agrega debajo de los bloques de pais
  existentes. El input de busqueda vive dentro del desplegable, no junto al
  select, y el filtrado no distingue mayusculas/minusculas ni acentos.
  Cada subdivision agregada abre un modal de nuevo/editar, y el bloque de pais
  muestra una tabla con entidad, elementos seleccionados y acciones. La X
  cierra sin aplicar cambios; solo `Guardar` confirma la seleccion en la tabla y
  en el JSON oculto. Dentro del modal hay dos listas de badges para hijos
  disponibles e hijos incluidos en el grupo; cada lista usa un unico recuadro
  visible bajo su label. Un click en un badge lo mueve de un lado al otro. Las
  filas de tabla se editan o eliminan con sus botones, y los bloques de pais
  tambien se pueden eliminar salvo el pais origen. Guardar o exportar un grupo escribe TOML compacto:
  `source_country_code`, la asignacion compatible
  `CODIGO_INTERNO = ["Municipio", ...]`, `[[country_groups]]` y, si hace falta,
  `[[country_groups.sections]]`. No se guardan metadatos como `kind`, `slug`,
  `entry_slug`, `name`, `source_bundle` o `source_python`. Los TOML de grupos
  importables viven en `subdivision_groups/groups/<pais>.toml`, con una
  asignacion `CODIGO_INTERNO = [...]` por grupo; los bundles legacy con
  diccionarios como `restar` o `childs` viven en
  `subdivision_groups/subdivisions/<pais>.toml` y no se importan como grupos.
  El codigo interno no puede repetirse dentro de un mismo pais; el backend lo
  rechaza y el frontend deshabilita `Guardar`/`Importar TOML` con un aviso si
  detecta la colision. Ese aviso reserva su linea bajo el input del codigo
  interno y cambia solo visibilidad/opacidad para no deformar el recuadro. El
  mismo codigo puede existir en otro pais porque el slug SQL se guarda como
  `<pais>_<codigo>`.
  Al abrir un grupo en `/groups/groups/<pais>/<grupo>/`, el formulario lee la
  fila SQL `SubdivisionGroup` e hidrata en el contexto inicial las listas planas
  antiguas de nombres o codigos; la plantilla no debe lanzar un POST inicial a
  `/groups/source-data/` solo para resolver badges seleccionados.
  `Importar TOML`
  en la cabecera importa todas las semillas; `Importar TOML` dentro del panel de
  un pais encola solo la semilla `subdivision_groups/groups/<pais>.toml` cuyo
  `source_country_code` coincide con ese pais. `Exportar TOML` se muestra junto
  a ese `Importar TOML` del panel y publica a `/groups/<pais>/export-toml/`.
  En el editor de un grupo concreto, `Exportar TOML` escribe ese mismo bundle de
  pais desde SQL e `Importar TOML` refresca todos los grupos contenidos en el
  bundle y recarga la pagina editada tras importarlo por AJAX. Tras guardar o
  importar, `Volver` conserva pais/tabla/fila y, si vuelve por historial del
  navegador, recarga `/groups/` antes de posicionarse para mostrar los datos
  actualizados. Los paneles de pais de `/groups/` cargan sus tablas solo al
  pulsar la tarjeta del pais, mediante `/groups/countries/<pais>/data/`; el
  HTML inicial no debe precargar filas de grupos ni de nuevas divisiones. La
  tabla `Nuevas divisiones` de ese endpoint lista definiciones SQL
  `DerivedSubdivision` del pais fuente, nunca municipios base `AdminArea` ni
  filas materializadas `NuevoAdminArea` expandibles. Su filtro de nivel se
  calcula desde esas definiciones.
  El `name` SQL del grupo se guarda igual que el codigo interno en mayusculas
  (`ALICANTE_A_MURCIA`), sin convertirlo a titulo. Si se cambia el codigo
  interno desde el editor, se renombra la fila `SubdivisionGroup` existente y se
  actualizan las referencias `groups = [...]` y `capital_groups.group` de las
  `DerivedSubdivision` del mismo pais, junto con las referencias del mismo pais
  dentro de otros `SubdivisionGroup.content` SQL, incluidos bundles legacy
  `[legacy].python_source`; asi no queda el grupo antiguo como opcion
  seleccionable.
- `/subdivisions/`: base SQL/TOML para crear administraciones ficticias o
  historicas que despues se construiran como `NuevoAdminArea`. Reutiliza la
  navegacion por tarjetas de pais de `/groups/`: la caja izquierda lista las
  subdivisiones creadas y la derecha muestra los grupos reutilizables
  disponibles para incluir o restar. Esas tablas tambien se cargan al pulsar el
  pais, mediante `/subdivisions/countries/<pais>/data/`; la pagina inicial solo
  renderiza tarjetas y carcasas vacias. `Importar TOML` lee el bundle de pais de
  `subdivision_groups/subdivisions/<pais>.toml`, `Exportar TOML` escribe ese
  mismo bundle desde SQL y cada entrada crea o refresca una fila
  `DerivedSubdivision`. Cada fila guarda TOML con
  `kind = "derived_subdivision"`, metadatos raiz como `flag_url` y `coat_url`
  para URLs manuales de bandera y escudo, bloques `[[include]]` para sumar
  subdivisiones, grupos o referencias `derived_subdivisions = [...]` a otras
  `DerivedSubdivision` ya creadas del mismo pais fuente, y bloques
  `[[subtract]]` para excluir niveles inferiores, grupos como
  `ALBACETE_A_CUENCA` o esas mismas referencias derivadas. El boolean
  `use_selected_entities_as_children` sigue en el TOML para que el builder
  pueda decidir si genera hijos desde unidades municipales/comunales fuente o
  desde los badges incluidos, pero el formulario principal ya no muestra ese
  checkbox; conserva su valor como input oculto hasta que se edite desde otra
  ubicacion. `Bandera` y `Escudo` se reparten el ancho disponible de esa fila.
  El boton `Popular` encola
  `py manage.py build_derived_subdivisions <pais> --force --continue-on-error`,
  que reconstruye las filas `NuevoAdminArea` de ese `country_code` desde SQL,
  calcula terreno, poblacion y densidad, y asigna capitales simples o
  compuestas por grupo con `[[capital_groups]]`. El flag
  `--continue-on-error` construye las recetas resolubles y registra como
  omitidas las definiciones obsoletas que no puedan resolverse, en vez de
  abortar todo el pais. Para un arreglo puntual, usa uno o varios
  `--slug <slug-o-internal-name>`; combinado con `--force`, el comando reemplaza
  solo esas filas materializadas y no borra todas las nuevas divisiones del pais.
  Al resolver `[[subtract]]` por nombres o grupos, si
  hay homonimos en el pais fuente, el builder prefiere la coincidencia que ya
  pertenece al territorio municipal incluido; asi una resta como `FENOLLEDA`
  bajo `Pyrénées-Orientales` no descuenta municipios del mismo nombre en otro
  departamento. En fuentes con niveles mixtos como Francia, CityPopulation puede
  dejar algunas comunas como hojas terminales en un nivel inferior al municipal
  nominal; el builder las trata como unidades atomicas cuando no tienen hijos,
  tanto al resolver nombres como al expandir macros. La semilla francesa de
  `Berry` debe incluir `Cher` e `Indre` ademas de `CREUSE_A_BERRY`, porque ese
  grupo solo no contiene Bourges como capital. Si una definicion importada trae
  metadatos raiz y un `[[children]]` espejo con el mismo nombre/nivel para
  contener los `[[children.include]]` / `[[children.subtract]]`, el builder
  pliega ese hijo en la fila padre para no crear duplicados visuales como
  `Artois` > `Artois`; si hay otros hijos reales, se conservan y los totales se
  recalculan desde las unidades fuente unicas. El editor
  `/groups/subdivisions/<pais>/<codigo>/` usa
  el mismo formato visual que el editor de grupos: encabezado compacto, guardar
  arriba y abajo, primera caja de campos base y dos paneles inferiores para
  seleccionar fuentes de BBDD que se van a `Sumar` o `Restar`. El pais fuente es
  siempre el segmento `<pais>` de la URL, no un campo visible del formulario. El
  bloque principal ya no muestra el campo numerico `Nivel`; por compatibilidad
  el formulario mantiene su valor como input oculto hasta que el nivel se edite
  desde su ubicacion definitiva. Tampoco muestra `Seccion padre` ni `Codigo
  propio`: guarda `parent_code` como el codigo raiz del pais seleccionado y
  calcula el codigo jerarquico completo desde
  `Codigo interno` (`ESP` + `CASTILLA_VIEJA` > `ESP-CASTILLA_VIEJA`), sin
  mostrar un campo visible de `Codigo calculado`. La caja base tambien permite
  editar las URLs manuales de bandera y escudo, que se guardan como `flag_url` y
  `coat_url` en el TOML raiz. Los codigos raiz de subdivisiones derivadas se
  normalizan en `ciudades_del_mundo.services.derived_codes`, por lo que Espana
  usa `ESP` aunque la raiz scrapeada de `AdminArea` tenga `code = "spain"`. El
  GET del editor carga solo la carcasa con una rueda; los datos reales se hidratan desde
  `/groups/subdivisions/<pais>/<codigo>/data/`, que ya no precarga todos los
  `SubdivisionGroup` ni devuelve `group_countries`, salvo los grupos y
  subdivisiones derivadas ya referenciados en el TOML actual para pintar sus
  badges. Al hidratar grupos ya guardados en `[[subtract]]`, los nombres planos del grupo se resuelven
  dentro del ambito municipal ya incluido para ese pais fuente, de modo que los
  badges muestran los mismos miembros y padre que luego restara el builder.
  Guardar en este editor
  usa AJAX cuando el navegador lo permite y no recarga la pagina; si el registro
  se crea o cambia de codigo, la URL se actualiza en sitio. Al volver desde el
  editor despues de guardar o importar, `/groups/` recarga una copia obsoleta
  del historial antes de reabrir el panel del pais, calcula la pagina correcta
  de la tabla cliente y desplaza la vista hasta la fila editada. Las filas ya
  construidas de `/groups/` se enlazan a su definicion SQL por codigo actual y
  por `internal_name` con prefijo raiz, para que entidades con el mismo nombre
  visible como `CATALUNHA_A` y `CATALUNHA_B` sigan siendo clicables por separado
  aunque el codigo haya cambiado. La parte inferior
  reutiliza `/groups/source-data/` para consultar los mismos niveles y secciones
  que `/groups/groups/`; cuando el editor pide `include_groups=1`, ese endpoint
  anade opciones `SubdivisionGroup` del mismo pais y ambito como badges
  `source_kind = "group"`; con `include_derived_subdivisions=1` tambien anade
  `DerivedSubdivision` de niveles inferiores como `source_kind =
  "derived_subdivision"`, filtradas por nivel actual, subdivision actual, pais
  de esa subdivision actual y ambito municipal del padre seleccionado. La
  exclusion de la entidad actual solo se aplica dentro de ese pais actual, para
  no ocultar una entidad homonima de otro pais fuente. Igual que el editor de grupos, incluye una fila raiz
  del pais antes de `Nivel 1`. Ese modal permite anadir el pais como NV0 y sus
  administraciones directas de nivel 1 como badges directos. Cuando se guarda un
  pais o territorio completo desde esa fila raiz, se conserva como bloque
  `level = 0` con `ids = [...]` del `AdminArea` raiz y se rehidrata como badge
  seleccionado al editar. El modal carga los hijos de la seccion elegida y puede guardar tanto entidades directas de
  `AdminArea` como grupos reutilizables. El TOML sigue guardandose en
  `DerivedSubdivision.content`, pero queda oculto en el formulario; cuando se
  modifica la seleccion visual inferior, el POST envia `include_ids_json` y
  `subtract_ids_json` con entidades directas de BBDD, grupos o subdivisiones
  derivadas, y la vista los convierte en bloques `[[include]]` / `[[subtract]]`
  con `ids = [...]`, `groups = [...]` o `derived_subdivisions = [...]` segun
  corresponda. En esa parte
  inferior hay un unico panel de fuentes: el selector de paises vive en una caja
  de control separada y su unico boton `Anadir` agrega el bloque del pais. No se
  renderizan dos paneles `Sumar` / `Restar`. Dentro del bloque, los botones de
  jerarquia `Anadir` abren el modal de transferencia con `Disponibles` y
  `Grupo`, como el editor `/groups/groups/`, y los badges seleccionados pueden
  ser entidades `AdminArea`, grupos reutilizables o subdivisiones derivadas de
  nivel inferior. Los badges de grupo usan un color distinto, los badges de
  subdivisiones derivadas usan un tercer color y ambos muestran un tooltip con
  sus miembros resueltos desde SQL. El modal
  tiene un filtro unico que normaliza mayusculas y acentos, por lo que `Le`,
  `le` o `Lé` filtran igual, y checks combinables para mostrar solo entidades
  normales, grupos reutilizables y/o subdivisiones creadas. Mover badges dentro
  del modal no debe reenfocar el buscador ni desplazar la pagina. En
  `/groups/source-data/`, las opciones disponibles de grupos y subdivisiones
  derivadas se calculan con caches por peticion y se envian en formato compacto:
  conservan `member_ids` completos para que `Excluir` funcione, pero no mandan
  `member_names` completos para evitar payloads enormes. El POST AJAX de
  guardado devuelve un payload ligero y no recalcula `include_items` ni
  `subtract_items`; la hidratacion completa queda en `/data/`.
  Al pulsar `Anadir` sobre una fila de jerarquia, los grupos disponibles se
  limitan al mismo nivel que los hijos directos de esa fila; al abrir
  `Excluir`, los grupos disponibles se resuelven para todos los niveles
  descendientes bajo los incluidos. En el modal, los grupos no se mezclan con
  entidades normales: se separan por tipo y nivel, como `Nivel 3` y
  `Grupos - Nivel 3`; esas secciones se separan con titulo y linea gruesa
  neutra dentro de la caja `Disponibles`/`Grupo`, no con cajas anidadas. Al
  cargar hijos directos, el endpoint mantiene solo la capa inmediata mas alta si
  la BBDD tiene hijos mezclados de varios niveles bajo el mismo padre, pero no
  oculta ciudades unificadas directas (`city_merge_status = 1`) aunque esten en
  un nivel mas profundo. La tabla
  agrupa las fuentes seleccionadas por pais, nivel y padre, sin separar por
  tipo, de forma que las entidades `AdminArea`, los grupos reutilizables y las
  subdivisiones derivadas del mismo padre aparecen como badges en una sola fila;
  los badges especiales mantienen su color distinto. La tabla se ordena como
  `Incluidos`, `Excluidos`, `Nivel`, `Acciones`; `Incluidos` y `Excluidos` son
  columnas de badges que reparten el ancho util al 50/50, `Nivel` queda
  estrecho y `Acciones` solo ocupa lo necesario para los botones. `Editar` abre
  un selector del mismo pais fuente, nivel y padre que la fila editada. Por
  ejemplo, una CCAA muestra todas las CCAA de Espana y una provincia bajo
  Castilla y Leon muestra las demas provincias de Castilla y Leon. Ese selector
  de edicion consulta `/groups/source-data/` con
  `source_mode=items` para traer todas las filas del nivel, no solo secciones
  con hijos. `Excluir` abre un selector de descendientes de todos los incluidos
  en esa fila mediante `source_mode=descendants`, por lo que permite restar
  niveles inferiores completos, no solo hijos directos. Si un grupo pertenece al
  ambito de una entidad incluida, aparece tambien como candidato de `Excluir` y
  se guarda como `[[subtract]] groups = [...]`; si no pertenece a ningun
  incluido, se mantiene como fuente de `Incluir`. Ese selector sigue siendo un
  unico modal, pero separa disponibles y seleccionados en bloques visuales por
  tipo y nivel. `Excluidos` muestra solo las entidades ya marcadas como restadas, no
  todos los candidatos. Asi una subdivision puede sumar Castilla y Leon,
  Cantabria y La Rioja, y marcar las provincias leonesas como restas dentro de
  Castilla y Leon.
  En el editor de una subdivision concreta, `Importar TOML` importa por AJAX el
  bundle `subdivision_groups/subdivisions/<pais>.toml`, refresca sus filas SQL y
  recarga la pagina editada.
  En `/groups/groups/`, los municipios ya seleccionados se hidratan desde SQL
  en el contexto inicial, sin una peticion POST inicial de hidratacion desde la
  plantilla; antes del selector de `Nivel 1`, el bloque jerarquico muestra una
  fila raiz con el pais y el resumen del primer nivel, y su boton abre el mismo
  modal para seleccionar el pais como NV0 o administraciones de nivel 1. La
  lista completa de hijos se pide a BBDD solo al abrir el modal de una seccion, para evitar una peticion
  por cada seccion
  seleccionada durante la carga.
  Los bloques que referencian paises
  externos sin datos `AdminArea` se omiten; las referencias al pais principal
  siguen fallando si faltan datos para evitar builds incompletos. El campo
  `Capitales` ocupa la fila completa bajo los campos basicos. Dentro de
  `Capitales`, el Select2 buscable usa aproximadamente el 30% y la barra de
  badges el 70%; esa barra tiene el alto de un input, muestra
  badges legibles y eliminables con una `x` sin circulo, y se desplaza en
  horizontal si no caben. Permite guardar varias capitales o ninguna, envia
  `capitals` como lista mediante inputs ocultos y el TOML queda en
  `capitals = [...]`. `/data/` carga solo la seleccion inicial por ID y el
  formulario no consulta capitales al cargar, al editar TOML ni al modificar
  fuentes; solo el AJAX del Select2 consulta
  `/groups/subdivisions/<pais>/<codigo>/capital-options/` cuando el usuario
  escribe al menos dos letras. El endpoint filtra por prefijo normalizado,
  como `Za`, y comprueba tambien nombres mostrados/alias localizados antes de
  descartar una candidata; si el prefijo crudo no basta, revisa las candidatas
  del ambito seleccionado sin abrir la busqueda al pais completo. Despues
  valida que cada candidata pertenezca a las regiones/municipios incluidos y
  quede fuera de las restas. El select muestra solo el nombre. Tanto sus opciones como los hijos
  mostrados en los badges de grupos/subdivisiones usan municipios base y
  ciudades unificadas (`city_merge_status` 0 y 1), excluyendo las entidades
  fuente consumidas por una ciudad unificada (`city_merge_status` 2) para no
  duplicar terreno, poblacion ni `municipios_originales`.
- `/countries/`: navegador de paises en tarjetas de 10 columnas, con slot
  cuadrado fijo para bandera registrada en SQL o placeholder local cuando no hay
  asset registrado, terreno y poblacion desde `/api/countries/`;
  al hacer clic carga la ficha basica del pais y sus subdivisiones directas desde
  `/api/countries/<country_code>/`, y cada subdivision se abre recursivamente con
  `/api/admin-areas/<area_id>/`. Si una subdivision tiene hijos directos en varios
  niveles porque CityPopulation enlaza una tabla inferior a un padre superior,
  la ficha muestra un recuadro por nivel hijo para no mezclar, por ejemplo,
  comunas L3 y urban places L4. El selector de nivel puede saltar a cualquier
  nivel importado, tambien comunas o municipios sin hijos; la tabla se sirve
  paginada desde la API con 20 filas por defecto y tamanos de 50 o 100.
- `/stats/`: redireccion de compatibilidad hacia `/countries/`.
- `/delete/`: borrado confirmado de datos `AdminArea` por pais fuente o
  `NuevoAdminArea` por pais derivado.
- `/tasks/`: historial de tareas lanzadas desde la web, cargado dinamicamente
  desde `/tasks/table/` con paginacion/ordenacion local y refresco periodico.
  Las filas son clicables y abren el detalle de la tarea.
- `/map/<origen>/<id>/`: ficha de mapa e identidad visual para un `AdminArea`
  (`origen=admin`) o `NuevoAdminArea` (`origen=derived`).
- `/identity/<tipo>/entity/<entity_type>/<entity_key>/`: ficha interna legible
  para bandera, escudo o sello persistido; muestra la imagen de Wikimedia y una
  sola descripcion en el idioma activo de la aplicacion. La descripcion debe ser
  heraldica/vexilologica o explicativa del simbolo; no se muestra una tabla de
  "Descripciones multiidioma" ni descripciones genericas de entidad.
- `/identity/<tipo>/<archivo>/`: ruta legacy para nombres Commons o rutas locales
  `visual_assets/...`; intenta resolver la fila SQL para mostrar la misma ficha.

La interfaz tiene selector de idioma en la barra superior. Los idiomas
disponibles son:

- Espanol (`es`)
- Ingles (`en`)
- Frances (`fr`)
- Aleman (`de`)
- Ruso (`ru`)
- Italiano (`it`)
- Serbio cirilico (`sr`)
- Serbio latino (`sr-latn`)
- Arabe estandar (`ar`)

Los nombres visibles de paises y subdivisiones pasan por los catalogos gettext.
Para mostrar `Brazil` como `Brasil` en espanol, la traduccion debe existir en
`locale/es/LC_MESSAGES/django.po` y despues compilarse con
`py manage.py compile_local_messages`.

La barra superior tambien incluye un selector de estilo. Los estilos se agrupan
en `Basico` (`Claro`, `Dark`, `Dracula`), `Complejos` (`Retro`, `Retro Azul`,
`Retro` en violeta, verde, azul celeste y rojo, `8bits`, `Arcoiris`, `Papel`) y
`Especiales` (`Espana`). Los
estilos especiales por pais deben usar imagenes de fondo de ciudades o
monumentos representativos y recuadros basados en los colores de su bandera; si
se agregan manana estilos como `Francia` o `Marruecos`, deben seguir esa misma
regla. `Espana` usa fondos de monumentos/ciudades, recuadros amarillos con
bordes rojos y botones rojos, sin convertir los recuadros en una bandera literal.
La eleccion se guarda en `localStorage` como `ciudades_del_mundo_theme` y se
aplica en cliente con `html[data-theme]`. El check `Efectos complejos` guarda
`ciudades_del_mundo_theme_effects` y activa animaciones opcionales en estilos
complejos o especiales, como movimiento de colores en `Arcoiris`, barridos en
`Retro` o animacion por pasos en `8bits`.

Las tareas web se gestionan en `ciudades_del_mundo/web/tasks.py`. Cada accion
lanza un subproceso `manage.py`, guarda estado/salida reciente en la tabla
`WebTask`, escribe el log completo en `.web_task_logs/<pais-o-clave>/*.log` y
usa `.web_task_progress/*.json` para progreso por tarea en la raiz del proyecto.
Las acciones que crean una tarea desde la interfaz se lanzan por AJAX con la
misma ventana emergente que usa el scraping: importaciones TOML de listados,
construccion de subdivisiones derivadas y exportaciones desde recetas o
`/derived/<id>/` se encolan sin abandonar la pagina actual.
Las tareas de scraping pueden dejar checkpoints tecnicos en
`.web_scrape_resume/` mientras no terminan correctamente, pero la UI de
configuraciones ya no muestra una accion `Reanudar`: al parar una tarea la fila
vuelve al estado anterior inferible. `/tasks/<id>/` carga
el log completo al abrirse y, mientras la
tarea sigue activa, solo solicita el nuevo fragmento por offset para no
ralentizar la pagina. Esos ficheros locales estan ignorados por git y caducan a
los 90 dias.
Las tareas web entran en una cola backend real. Por defecto solo hay 1
subproceso `manage.py` activo a la vez para no saturar SQLite ni el equipo; se
puede subir hasta 3 con `CIUDADES_WEB_MAX_RUNNING_TASKS=2` o `3`. Las demas
tareas quedan en `queued` y arrancan cuando termina una activa. Si se lanza otra
tarea con la misma clave operativa, o si se guarda una configuracion/receta
mientras su tarea equivalente sigue activa, la tarea anterior se cancela y se
reemplaza.
Al reiniciar el servidor se conserva el historial reciente; cualquier tarea
activa se marca como parada. Las tareas pueden continuar si se cierra el
navegador, pero no si se apaga el PC o el proceso Django que las lanzo.

Las API y graficas del navegador de paises usan solo filas `AdminArea` visibles:
se excluyen las filas con `city_merge_status = 3` para que no aparezcan en
tablas, roscas ni tarjetas. En `AdminArea.city_merge_status`, `0` es una fila
normal, `1` es una ciudad unificada creada desde `[[cities]]` o merges, y `2`
es una entidad fuente usada para construir esa ciudad.
Las expansiones de grupos y subdivisiones derivadas usan por defecto filas `0`
y `1`; solo una preferencia explicita `prefer_city_merge_status = "source"`
incluye fuentes `2` en lugar de ciudades unificadas.

Las traducciones viven en `locale/<idioma>/LC_MESSAGES/django.po` y se cargan
desde los `.mo` compilados. Como Windows puede no tener GNU gettext instalado,
el proyecto incluye un compilador local:

```powershell
py manage.py compile_local_messages
py manage.py compile_local_messages es en fr de ru it sr sr_Latn ar
```

Cuando cambies texto visible de la web, marca el texto con `{% trans %}` /
`{% blocktrans %}` en plantillas o `gettext` en Python, actualiza los `.po` y
vuelve a ejecutar `compile_local_messages`.

Los listados grandes de `/areas/` y `/derived/<id>/` cargan sus tablas de forma
asincrona desde endpoints parciales (`/areas/table/` y
`/derived/<id>/table/`). Los filtros avanzados usan Select2 cuando los assets de
CDN estan disponibles; si no cargan, los selects nativos siguen funcionando.

La ficha de mapa usa Leaflet con teselas de OpenStreetMap y geocodificacion
client-side por nombre mediante Nominatim, ya que la base de datos no guarda
geometria ni coordenadas. La bandera y escudo usan primero assets persistidos o
locales. Las fichas de pais no hacen busqueda Wikidata/Wikimedia en tiempo de
render; las imagenes faltantes se deben resolver previamente con scraping o
`ensure_visual_assets`. La pagina muestra ademas las capitales y la ciudad mayor
registradas en la base local cuando existen.

Para reducir errores `database is locked` durante tareas de poblacion, SQLite
se abre con timeout de 30s y PRAGMAs `busy_timeout`, `journal_mode=WAL` y
`synchronous=NORMAL`. Si aun asi una vista encuentra la base bloqueada, el
middleware web devuelve una respuesta 503 controlada en vez de romper la
aplicacion.

## Estructura del repositorio

```text
ciudades_del_mundo/
  application/
  domain/
  historical_divisions/
  infrastructure/
  management/commands/
  new_country_configs/
  new_subdivisions/
  ports/
  services/
  subdivision_groups/
  subdivisions/
  templates/
  web/
manage.py
db.sqlite3
```

## Paquetes de configuracion

- `subdivisions`
  TOML semilla por pais para instanciar o refrescar `/configs/` con
  `sync_scraping_configs`. No es fuente operativa del scraping runtime: despues
  de sincronizar, la tabla SQL `ScrapingConfig` es suficiente para validar y
  popular.
- `historical_divisions`
  Recetas Python legacy para `build_new_subdivisions`; no es carpeta activa de
  semillas TOML.
- `new_subdivisions`
  Recetas Python legacy para `build_new_subdivisions`; no es carpeta activa de
  semillas TOML.
- `new_country_configs`
  TOML semilla/importacion para el nuevo modelo `DerivedCountryConfig` y la
  seccion `/new-countries/`.
- `subdivision_groups`
  `groups/<pais>.toml` contiene TOML compacto de semilla/importacion para el
  modelo `SubdivisionGroup` y la seccion `/groups/`, con una asignacion por
  grupo; `subdivisions/<pais>.toml` conserva bundles legacy de subdivisiones
  que no se importan como grupos.

## Desarrollo local

Requisitos minimos:

- Python 3.13+ o compatible con `tomllib`
- Dependencias de Django, BeautifulSoup, requests y lxml

Arranque tipico:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
py manage.py migrate
py manage.py runserver 127.0.0.1:8000
```

Si no existe `requirements.txt`, instala al menos:

```powershell
pip install django requests beautifulsoup4 lxml openpyxl
```

## Tests rapidos

La suite principal es offline: no usa red, no toca `db.sqlite3` y usa una base
de datos de test aislada cuando necesita modelos SQL. Sirve para validar cambios
de scraping, contenido TOML almacenado en SQL y logica de dominio antes de
ejecutar operaciones caras.

```powershell
py manage.py test ciudades_del_mundo.tests --verbosity 2
```

La suite incluye `test_architecture_boundaries.py`, que falla si `domain`,
`ports` o `application` empiezan a importar capas externas y rompe la direccion
hexagonal.

Tambien puede ejecutarse con `unittest` directo:

```powershell
py -m unittest discover ciudades_del_mundo\tests -v
```

Para comprobar las configuraciones reales almacenadas en SQL:

```powershell
py manage.py validate_subdivision_configs
```

### Tests de contratos de scraping

`ciudades_del_mundo/tests/test_scraping_country_data.py` contiene contratos de
scraping autocontenidos; no lee `country_data/`, no usa `db.sqlite3` y no
requiere variables de entorno. El caso de Espana declara 19 CCAA/ciudades
autonomas, 52 provincias/ciudades autonomas, 8131 municipios, 29509 localidades
con poblacion >= 20, recuentos por provincia y 20 rutas de jerarquia con los
nombres scrapeados por CityPopulation cuando hay cooficialidad.

La suite se ejecuta con:

```powershell
py manage.py test ciudades_del_mundo.tests.test_scraping_country_data --verbosity 2
```

## Notas operativas

- Las migraciones se consideran codigo generado y no forman parte de la logica de scraping.
- Los ficheros `subdivisions/*.py.txt` son material antiguo de referencia y no participan en el flujo actual.
- El dashboard web es util para inspeccion manual, no como API publica.

## Siguientes mejoras razonables

- ampliar el editor visual de `/new-countries/` para crear arboles anidados de
  entidades intermedias y definitivas sin editar TOML a mano
- limpiar codificacion legacy en algunos datos historicos
- anadir tests para configuraciones SQL y scrapers HTML

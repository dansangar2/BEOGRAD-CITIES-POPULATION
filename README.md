# BEOGRAD-CITIES-POPULATION

Proyecto Django para:

- scrapear divisiones administrativas y poblacion desde `citypopulation.de`
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
`validate_subdivision_configs` y `scrape_subdivisions_with_assets`.

## Estado actual

El sistema de scraping ya no depende de modulos Python por pais. La
configuracion activa vive en la tabla SQL `ScrapingConfig`; su campo `content`
mantiene el mismo formato TOML para que siga siendo editable y versionable como
texto dentro de SQL. El scraping runtime lee siempre la configuracion desde BBDD, no desde ficheros
TOML. Los TOML de `ciudades_del_mundo/subdivisions/*.toml` se usan solo como
semilla explicita para crear o refrescar filas en `/configs/` mediante
`sync_scraping_configs`. Una vez instanciadas las filas SQL, puedes retirar esa
carpeta local y el scraping seguira funcionando porque no hay fallback runtime a
ficheros.

Cada configuracion SQL describe:

- que formato CityPopulation usar por bloque (`cities`, `admin` o `citiesadmin`)
- que rutas scrapear bajo la base fija `https://www.citypopulation.de/en/`
- que secciones de cada pagina se persisten y cuantas veces se repiten
- desde que nivel arranca cada bloque o que nivel se fuerza
- como enlazar bloques consecutivos, padres forzados y paginas que suman al padre
- reglas opcionales de normalizacion de ciudades, merges y extensiones runtime
- `LEGAL_SUBDIVISION` para calcular la ciudad mas poblada por rama

## Arquitectura

El proyecto esta organizado por capas hexagonales. Para que una IA pueda leerlo
sin mezclar responsabilidades, empieza por `AGENTS.md`, luego entra solo en la
capa del cambio:

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

## Formato del contenido de configuracion

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
- `sum_to_root = true` se muestra en la UI como "Sumar al padre": las metricas
  de la rama se agregan al padre ya enlazado, o al pais si no hay padre forzado.
- `area_km2` permite indicar un tamano personalizado para la entidad raiz scrapeada en esa pagina.
- `area_overrides` permite indicar tamanos personalizados por `id`, `code` o `name` de entidad scrapeada.
- En `[[cities]]`, `keep_communes = false` agrega las comunas o distritos usados para calcular la ciudad pero no los conserva como filas hijas.
- `LEGAL_SUBDIVISION` es el unico nombre aceptado para el nivel legal.
- El enlazador une bloques por `data-wd`, codigo CityPopulation, nombre
  normalizado en el mismo nivel y codigo persistido. Si CityPopulation reutiliza
  un codigo para otra entidad, se desambigua con un codigo derivado del padre
  antes de persistir para no mezclar ramas. Si despues una fila queda sin padre
  pero su codigo desambiguado conserva el ambito del padre, como `17_174`, ese
  ambito se usa como tercera via generica de enlace (`17_174` bajo `17`).
- La deduplicacion por nombre no es global para anclas de bloque que ya tienen
  padre o ambito de URL. Entidades homonimas de paginas distintas, como
  `Saint-Pierre` o `Grande-Terre` en departamentos ultramarinos franceses, deben
  conservar sus hijos dentro de su bloque antes de enlazar con otros bloques.
- La reparacion por prefijo de codigo no es global: el candidato de prefijo debe
  pertenecer a la misma rama territorial de la URL cuando ambas filas tienen
  ruta. Esto evita colisiones como comunas de Essonne `91521...` enlazadas por
  error bajo un codigo corto de Réunion `9152`.
- Si el hijo tiene una rama territorial en la URL y el candidato de prefijo es
  un ancla de bloque sin rama, el candidato se descarta. Esto evita que anchors
  mal resueltos como `#i6527` o `#i8125` capturen comunas de otros departamentos
  por simple prefijo numerico.
- El parser registra alias visibles de nombres cooficiales, parentesis,
  corchetes y separadores `/`, de modo que un `radm` como `Jávea` puede enlazar
  con una fila `Xàbia (Jávea)` sin reglas por pais.
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
  municipio y urban places de Marruecos por provincia/prefectura. La vista
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

El scraping descarga paginas CityPopulation independientes en paralelo con
`--page-workers` (por defecto `4`, configurable con
`CIUDADES_SCRAPE_PAGE_WORKERS`). El parseo, los eventos `FOUND`, la siembra de
assets y la escritura SQL se mantienen en el orden de la configuracion, por lo
que no cambia la funcionalidad ni los datos extraidos; solo se solapa la espera
de red. Si una misma URL aparece repetida, se reutiliza el HTML y el parseo de
esa pagina para no procesarla dos veces. Usa `--page-workers=1` si
quieres reproducir el comportamiento secuencial antiguo.

`--country-workers` permite scrapear varias configuraciones a la vez en
`scrape_subdivisions`; por defecto vale `1` y tambien se puede configurar con
`CIUDADES_SCRAPE_COUNTRY_WORKERS`. Las descargas y parseo de paises se solapan,
pero la transaccion final de SQLite se serializa usando
`.web_sqlite_write.lock` para evitar bloqueos y escrituras cruzadas entre tareas
web o procesos CLI. El bloqueo real es un lock del sistema operativo sobre el
archivo; si un proceso muere y el archivo queda en disco, no bloquea futuros
scrapeos. No se combina con `--seed-assets-from-pages`; la siembra de
Wikimedia/Commons queda en modo secuencial. Los comandos envoltorio
`repopulate_configs` y `validate_and_scrape_configs` tambien aceptan
`--country-workers`; desde la web, `Popular todo` y `Popular no populados` usan
`--country-workers=2`.

El flujo normal de popular no vacia antes el pais. La persistencia compara la
foto scrapeada completa contra SQL: crea filas nuevas, actualiza solo las filas
modificadas, conserva intactas las que no cambian y borra al final las filas
que ya no aparecen mediante `delete_missing`. `--clear-first` queda como opcion
explicita de mantenimiento en `scrape_subdivisions_with_assets`; la accion web
`Re-popular` ya usa el guardado incremental y no limpia antes de popular.

La persistencia del scrapeo tambien usa operaciones masivas: guardado por nivel,
busqueda de filas existentes en lotes compatibles con SQLite, creacion masiva de
filas nuevas, `bulk_update` solo para filas modificadas, borrado rapido de filas
ausentes con la misma limpieza segura de relaciones que `clear_config_data`,
limpieza de `VisualAsset` solo para esas areas eliminadas, y `bulk_update` para
ciudad mas poblada y representantes.

`--resume` reutiliza checkpoints locales de paginas completadas para una
recuperacion manual por CLI y solo descarga las paginas pendientes. Los
checkpoints viven en `.web_scrape_resume/`, estan ignorados por Git, se invalidan cuando cambia el
contenido SQL de la configuracion y se eliminan al terminar correctamente.

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
coste. Las descargas locales solo existen como opt-in (`--download` o
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

- `/new-countries/` lista contenedores `DerivedCountry`.
- Dentro de cada pais, `Nueva configuracion` crea una fila
  `DerivedCountryConfig` con contenido TOML editable.
- `Vista` abre una ficha base de la configuracion y enlaza a
  `/derived/<root_id>/` cuando ya existen filas `NuevoAdminArea` para su
  `derived_country_code`.
- `/groups/` lista `SubdivisionGroup`, grupos TOML reutilizables que podran
  referenciarse desde configuraciones de nuevos paises.

Las recetas Python antiguas siguen en `ciudades_del_mundo/new_subdivisions/` y
`ciudades_del_mundo/historical_divisions/` para compatibilidad con
`build_new_subdivisions`. Se generaron semillas TOML equivalentes en
`ciudades_del_mundo/new_country_configs/*.toml` y
`ciudades_del_mundo/subdivision_groups/*.toml`; cada TOML guarda metadatos,
selecciones iniciales y el Python legacy embebido para no perder logica que aun
no tenga traduccion declarativa. Las copias `*_old/` son backups locales
ignorados por Git.

### Exportar

```powershell
py manage.py export_nuevoadmin_csv --country-id spanish_federal_republic
py manage.py export_nuevoadmin_excel --country-id spanish_federal_republic
```

Los Excel se guardan por defecto en la subcarpeta `excels/`. Puedes cambiarla con `--output-dir`.
Cada bloque de nivel incluye `Lx_ranking_poblacion_pais`, que ordena las areas
por poblacion dentro de todo el pais para ese mismo nivel. La hoja Excel genera
una sola tabla principal de rutas raiz-hoja, sin tablas auxiliares a la derecha.


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
hay mas de una opcion util. El primer nivel bajo la raiz siempre se muestra por
defecto; los niveles intermedios con hijos se mantienen aunque algun padre sea
grande, y se ocultan los niveles hoja masivos o niveles muy grandes con pocos
padres visibles para evitar tablas inmanejables.
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
`/api/admin-areas/<area_id>/` y `/api/derived/`.
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
  mientras esa validacion sigue activa. El editor guarda `ScrapingConfig.content`
  y valida sintaxis/esquema antes de escribir. El boton `Exportar TOML` escribe
  la version SQL actual en `ciudades_del_mundo/subdivisions/<slug>.toml` sin
  convertir ese fichero en fuente runtime.
- `/new-countries/`: base SQL para paises derivados. Lista `DerivedCountry`,
  muestra sus configuraciones TOML `DerivedCountryConfig`, permite crear/editar
  esas configuraciones y abre una vista base que enlaza a `/derived/<id>/`
  cuando ya hay datos construidos.
- `/groups/`: lista y edita `SubdivisionGroup`, grupos TOML reutilizables para
  futuras configuraciones de nuevos paises.
- `/countries/`: navegador de paises en tarjetas de 10 columnas, con slot
  cuadrado fijo para bandera registrada en SQL o placeholder local cuando no hay
  asset registrado, terreno y poblacion desde `/api/countries/`;
  al hacer clic carga la ficha basica del pais y sus subdivisiones directas desde
  `/api/countries/<country_code>/`, y cada subdivision se abre recursivamente con
  `/api/admin-areas/<area_id>/`. Si una subdivision tiene hijos directos en varios
  niveles porque CityPopulation enlaza una tabla inferior a un padre superior,
  la ficha muestra un recuadro por nivel hijo para no mezclar, por ejemplo,
  comunas L3 y urban places L4.
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
`WebTask`, escribe el log completo en `.web_task_logs/*.log` y usa
`.web_task_progress/*.json` para progreso por tarea en la raiz del proyecto.
Las tareas de scraping pueden dejar checkpoints tecnicos en
`.web_scrape_resume/` mientras no terminan correctamente, pero la UI de
configuraciones ya no muestra una accion `Reanudar`: al parar una tarea la fila
vuelve al estado anterior inferible. `/tasks/<id>/` carga
el log completo al abrirse y, mientras la
tarea sigue activa, solo solicita el nuevo fragmento por offset para no
ralentizar la pagina. Esos ficheros locales estan ignorados por git.
Las tareas web empiezan inmediatamente en el backend: no existe cola real de
subprocesos. La unica cola permitida es visual, en las cajas/toasts del
navegador, para no mostrar mas de tres avisos a la vez. Si se lanza otra tarea
con la misma clave operativa, o si se guarda una configuracion/receta mientras
su tarea equivalente sigue activa, la tarea anterior se cancela y se reemplaza.
Al reiniciar el servidor se conserva el historial reciente; cualquier tarea
activa se marca como parada. Las tareas pueden continuar si se cierra el
navegador, pero no si se apaga el PC o el proceso Django que las lanzo.

Las API y graficas del navegador de paises usan solo filas `AdminArea` visibles:
se excluyen las filas con `city_merge_status = 3` para que no aparezcan en
tablas, roscas ni tarjetas.

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
  Recetas Python legacy para subdivisiones historicas. Siguen siendo buildables
  por compatibilidad.
- `new_subdivisions`
  Recetas Python legacy para nuevas subdivisiones derivadas. Siguen siendo
  buildables por compatibilidad.
- `new_country_configs`
  TOML semilla generado desde `new_subdivisions/*.py` para el nuevo modelo
  `DerivedCountryConfig`.
- `subdivision_groups`
  TOML semilla generado desde `historical_divisions/*.py` para el nuevo modelo
  `SubdivisionGroup`.

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

## Notas operativas

- Las migraciones se consideran codigo generado y no forman parte de la logica de scraping.
- Los ficheros `subdivisions/*.py.txt` son material antiguo de referencia y no participan en el flujo actual.
- El dashboard web es util para inspeccion manual, no como API publica.

## Siguientes mejoras razonables

- conectar el constructor de `NuevoAdminArea` al TOML de `DerivedCountryConfig`
  y `SubdivisionGroup`
- limpiar codificacion legacy en algunos datos historicos
- anadir tests para configuraciones SQL y scrapers HTML

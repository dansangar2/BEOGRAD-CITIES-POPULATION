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

## Estado actual

El sistema de scraping ya no depende de modulos Python por pais. La
configuracion activa vive en la tabla SQL `ScrapingConfig`; su campo `content`
mantiene el mismo formato TOML para que siga siendo editable y versionable como
texto dentro de SQL. Los ficheros locales de
`ciudades_del_mundo/subdivisions/*.toml` son solo seeds temporales para
bootstrap/import-export, estan ignorados por Git y deben desaparecer cuando se
retire el puente TOML/SQL.

Cada configuracion SQL describe:

- que scrapers usar (`admin`, `auto`, `table`, `double`, `cities`, `infosection`)
- que rutas scrapear
- desde que nivel arrancar cada parser
- reglas opcionales de normalizacion de ciudades
- `LEGAL_SUBDIVISION` para calcular la ciudad mas poblada por rama

## Arquitectura

El proyecto esta organizado por capas:

- `ciudades_del_mundo/domain`
  Modelos puros y logica de dominio: configuracion de scraping, DTOs, jerarquia y ciudad mas poblada.
- `ciudades_del_mundo/application`
  Casos de uso: ejecutar scraping, aplicar ciudades configuradas y exportar `NuevoAdminArea`.
- `ciudades_del_mundo/infrastructure`
  Implementaciones concretas: repositorios Django, scrapers HTML, cliente HTTP y escritor XLSX.
- `ciudades_del_mundo/services`
  Servicios de agregacion, capitales y reparto de representantes.
- `ciudades_del_mundo/management/commands`
  Comandos de operacion para scrapear, validar, construir subdivisiones y exportar.
- `ciudades_del_mundo/web`
  Interfaz operativa para inspeccionar datos, editar configuraciones, lanzar
  tareas locales y borrar datos.
- `locale`
  Catalogos gettext de la interfaz web en espanol, ingles, frances, aleman,
  ruso, italiano, serbio cirilico, serbio latino y arabe estandar.

## Modelos principales

- `AdminArea`
  Entidad scrapeada directamente desde CityPopulation.
- `NuevoAdminArea`
  Entidad derivada a partir de varias `AdminArea`, util para subdivisiones ficticias, historicas o politicas.

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

[representation]
level = 2
total = 350
min = 2
system = "dhondt"

[[pages]]
source = "admin"
path = ["admin"]
lowest_level = 0
area_km2 = 505990

[[pages]]
source = "table"
path = ["andalucia", "aragon", "asturias"]
lowest_level = 1
area_overrides = { "51" = 19.00, "52" = 13.40 }

[[pages]]
source = "double"
path = ["localities/acoruna", "localities/alava"]
lowest_level = 3

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

- `pages` agrupa paginas por parser y nivel.
- `path` siempre es un array, aunque solo haya una ruta.
- `source` selecciona el scraper; `auto` detecta la estructura HTML real de la pagina CityPopulation y delega en `admin`, `table`, `double` o `infosection`.
- `area_km2` permite indicar un tamano personalizado para la entidad raiz scrapeada en esa pagina.
- `area_overrides` permite indicar tamanos personalizados por `id`, `code` o `name` de entidad scrapeada.
- En `[[cities]]`, `keep_communes = false` agrega las comunas o distritos usados para calcular la ciudad pero no los conserva como filas hijas.
- `LEGAL_SUBDIVISION` es el unico nombre aceptado para el nivel legal.
- `admin` e `infosection` son tipos de scraper, no atajos especiales de ruta.

## Comandos utiles

### Validar configuraciones

```powershell
py manage.py validate_subdivision_configs
py manage.py validate_subdivision_configs spain morocco
```

### Sincronizar configuraciones TOML/SQL

```powershell
py manage.py sync_scraping_configs
py manage.py sync_scraping_configs spain --force
py manage.py sync_scraping_configs --force
py manage.py sync_scraping_configs --to-toml --output-dir .tmp-config-export
```

Para reinstanciar solo la configuración inicial de España desde el TOML semilla:

```powershell
py manage.py migrate
py manage.py sync_scraping_configs spain --force
py manage.py validate_subdivision_configs spain
py manage.py scrape_subdivisions --list-pages spain
py manage.py scrape_subdivisions_with_assets spain
```

`spain.toml` separa las localidades por profundidad: provincias con
`lowest_level = 3`, comunidades uniprovinciales con `lowest_level = 2`, y
Ceuta/Melilla con `lowest_level = 2`. Esto evita que las localidades de Ceuta
y Melilla se importen un nivel demasiado profundo o dependan de una raíz
sintética `spain_spain`.

El runtime usa SQL como unica fuente operativa. Los TOML de `subdivisions/`
quedan como semillas temporales locales para importar/exportar filas
`ScrapingConfig`, pero no se versionan. El repositorio de scraping no hace
fallback a esos ficheros: usa `sync_scraping_configs` o el bootstrap web para
importarlos cuando existan en local. Este puente debe retirarse antes de
publicar si el proyecto deja de necesitar seeds TOML.

### Ver las URLs que se van a scrapear

```powershell
py manage.py scrape_subdivisions --list-pages spain
```

### Ejecutar scraping

```powershell
py manage.py scrape_subdivisions spain
py manage.py scrape_subdivisions spain morocco portugal
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
de red. Usa `--page-workers=1` si quieres reproducir el comportamiento
secuencial antiguo.

`--resume` reutiliza checkpoints locales de paginas completadas por una tarea
web parada y solo descarga las paginas pendientes. Los checkpoints viven en
`.web_scrape_resume/`, estan ignorados por Git, se invalidan cuando cambia el
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

Las traducciones dinamicas se guardan en `DynamicTranslation` y tienen prioridad
en la UI cuando estan activas y no requieren revision. Gettext queda para textos
estaticos de interfaz. Las descripciones de banderas, escudos y sellos se
guardan en `VisualAssetTranslation.description` y `blazon`; el proveedor recibe
la URL de imagen cuando existe, pero el resultado sigue marcado con metadatos de
origen/modelo para poder revisarlo.

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
TOML/SQL y los `[visual_assets.flag]`/`[visual_assets.coat]` declarados como
`commons_filename` o `remote_url`; despues hace busqueda Wikidata si falta el
QID. Tambien guarda sellos cuando Wikidata los expone. CityPopulation queda
solo como respaldo para imagenes explicitamente etiquetadas o con nombre de
archivo claro, porque sus paginas incluyen iconos de idioma que no son la
bandera del pais. Los ficheros
no se descargan por defecto: se guarda `commons_filename`, `remote_url`, QID y
traducciones/descripciones en SQL, y la UI usa directamente URLs de Wikimedia
Commons (`Special:FilePath`). La ficha selecciona la traduccion del idioma activo
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
  vez y la paginacion cambia de pagina en cliente. Permite validar y lanzar
  scraping. Una configuracion `Populada` puede volver a lanzarse con `Popular`
  sin revalidar, y las acciones masivas de poblado incluyen configuraciones
  `Validadas` y `Populadas`. `Popular no populados` lanza solo las filas
  elegibles que todavia no estan `Populadas`. Las filas con validacion o
  poblado activo muestran `Parar` junto a `Validar`/`Popular`; al cancelar la
  tarea la fila pasa a `Parado`. Si el servidor local se reinicia o el ordenador
  se apaga con una tarea en cola/ejecucion, esa tarea se recupera como `Parado`
  en vez de `Fallo`; una fila parada durante el poblado muestra `Reanudar`, que
  lanza `scrape_subdivisions_with_assets --resume` para continuar desde las
  paginas completadas antes de la parada. Durante una tarea de poblado, la
  etiqueta `Populando` mantiene ancho estable con tres puntos animados por JS,
  sin keyframes CSS que se reinicien al refrescar la fila, y la fila se refresca
  con una cadencia baja para que el estado pase a `Populado` y el boton
  `Popular` se reactive al terminar. Las tareas lanzadas desde la web usan
  `scrape_subdivisions_with_assets --page-workers=4` para solapar la descarga
  de HTML de CityPopulation sin cambiar el orden de parseo/escritura. El
  boton `Validar` de una configuracion queda desactivado mientras esa validacion
  sigue activa. El editor guarda `ScrapingConfig.content` y valida
  sintaxis/esquema antes de escribir.
- `/recipes/`: lista recetas de `new_subdivisions` e `historical_divisions`.
  Permite crear recetas nuevas con un formulario JSON, editar recetas nuevas en
  Python y lanzar `build_new_subdivisions`, CSV o Excel.
- `/derived/`: muestra paises `NuevoAdminArea` creados y una tabla comparativa
  por pais con porcentajes respecto al pais y al padre.
- `/countries/`: navegador de paises en tarjetas de 10 columnas, con slot
  cuadrado fijo para bandera registrada en SQL o placeholder local cuando no hay
  asset registrado, terreno y poblacion desde `/api/countries/`;
  al hacer clic carga la ficha basica del pais y sus subdivisiones directas desde
  `/api/countries/<country_code>/`, y cada subdivision se abre recursivamente con
  `/api/admin-areas/<area_id>/`.
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
lanza un subproceso `manage.py`, guarda estado/salida reciente en
`.web_tasks.json`, escribe el log completo en `.web_task_logs/*.log` y usa
`.web_task_progress/*.json` para progreso por tarea en la raiz del proyecto.
Las tareas de scraping guardan checkpoints de reanudacion en
`.web_scrape_resume/` mientras no terminan correctamente. `/tasks/<id>/` carga
el log completo al abrirse y, mientras la
tarea sigue activa, solo solicita el nuevo fragmento por offset para no
ralentizar la pagina. Esos ficheros locales estan ignorados por git. Como maximo
se ejecuta 1 subproceso a la vez; el resto queda en estado `queued` y se
despacha por orden cuando termina o se cancela una tarea en ejecucion. Si se
lanza otra tarea con la misma clave operativa, o si se guarda una
configuracion/receta mientras su tarea equivalente sigue activa, la tarea
anterior se cancela y se reemplaza. Al reiniciar el servidor se conserva el
historial reciente; cualquier tarea activa o en cola se marca como interrumpida.
Las tareas pueden continuar si se cierra el navegador, pero no si se apaga el PC
o el proceso Django que las lanzo.

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
  new_subdivisions/
  ports/
  services/
  subdivisions/
  templates/
  web/
manage.py
db.sqlite3
```

## Paquetes de configuracion

- `subdivisions`
  Seeds TOML temporales locales para crear/exportar filas `ScrapingConfig`; no
  son la fuente operativa del runtime y no se versionan.
- `historical_divisions`
  Recetas Python para subdivisiones historicas.
- `new_subdivisions`
  Recetas Python para nuevas subdivisiones derivadas.

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

- mover recetas historicas y nuevas subdivisiones a un formato declarativo unificado
- limpiar codificacion legacy en algunos datos historicos
- anadir tests para configuraciones SQL y scrapers HTML

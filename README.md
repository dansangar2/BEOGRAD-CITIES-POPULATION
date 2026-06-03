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

El sistema de scraping ya no depende de modulos Python por pais. La configuracion activa vive en:

- `ciudades_del_mundo/subdivisions/<pais>.toml`

Cada fichero TOML describe:

- que scrapers usar (`admin`, `table`, `double`, `cities`, `infosection`)
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

1. Definir o ajustar una configuracion en `subdivisions/<pais>.toml`.
2. Validar la configuracion.
3. Ejecutar el scraping.
4. Opcionalmente asignar capitales.
5. Opcionalmente construir subdivisiones derivadas o historicas.
6. Exportar a CSV o Excel.

## Formato de configuracion TOML

El nombre del fichero define el prefijo comun de las rutas. Ejemplo: `spain.toml` produce rutas bajo `spain/...`.

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
- `source` selecciona el scraper.
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

### Ver las URLs que se van a scrapear

```powershell
py manage.py scrape_subdivisions --list-pages spain
```

### Ejecutar scraping

```powershell
py manage.py scrape_subdivisions spain
py manage.py scrape_subdivisions spain morocco portugal
```

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
respecto al pais. Las secciones de roscas muestran las graficas arriba y las
listas/leyendas filtrables debajo. Las tarjetas de
porcentaje por subdivision de primer orden muestran minipizzas con el reparto
interno de sus subdivisiones directas del siguiente nivel cuando ese nivel no
alcanza 150 filas en el pais; no se filtran por nombre de tipo de entidad. Los
datos generales esperan a resolver nombre oficial, idioma oficial, capital,
bandera y escudo desde Wikidata/Wikimedia cuando hay identificador disponible;
bandera y escudo abren una vista previa local y desde ahi la ficha de Commons o
la imagen completa.
En `/countries/`, al seleccionar un pais se muestran dos recuadros: la ficha
basica y las subdivisiones directas de primer nivel. Al abrir una subdivision,
el navegador agrega otra ficha con su bandera/escudo y sus hijos directos; se
puede seguir bajando con `/api/admin-areas/<area_id>/` hasta llegar a entidades
sin hijos.
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

- `/configs/`: lista `subdivisions/*.toml` con tablas dinamicas cargadas desde
  `/configs/table/` y `/configs/tasks/table/`; las filas se descargan una vez y
  la paginacion cambia de pagina en cliente. Permite validar y lanzar scraping.
  El boton `Validar` de una configuracion queda desactivado mientras esa
  validacion sigue activa. El editor guarda TOML y valida sintaxis/esquema antes
  de escribir.
- `/recipes/`: lista recetas de `new_subdivisions` e `historical_divisions`.
  Permite crear recetas nuevas con un formulario JSON, editar recetas nuevas en
  Python y lanzar `build_new_subdivisions`, CSV o Excel.
- `/derived/`: muestra paises `NuevoAdminArea` creados y una tabla comparativa
  por pais con porcentajes respecto al pais y al padre.
- `/countries/`: navegador de paises en tarjetas de 10 columnas, con bandera,
  terreno y poblacion desde `/api/countries/`; al hacer clic carga la ficha
  basica del pais y sus subdivisiones directas desde
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
- `/identity/<tipo>/<archivo>/`: ficha interna placeholder para bandera o
  escudo resuelto desde Wikimedia; queda preparada para detallar heráldica,
  colores oficiales, fecha de adopcion y fuente normativa.

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
`.web_tasks.json` y escribe el log completo en `.web_task_logs/*.log` en la raiz
del proyecto. Esos ficheros locales estan ignorados por git. Como maximo se
ejecutan 3 subprocesses a la vez; el resto queda en estado `queued` y se
despacha por orden cuando termina o se cancela una tarea en ejecucion. Si se
lanza otra tarea con la misma clave operativa, o si se guarda una
configuracion/receta mientras su tarea equivalente sigue activa, la tarea
anterior se cancela y se reemplaza. Al reiniciar el servidor se conserva el
historial reciente; cualquier tarea activa o en cola se marca como interrumpida.

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
geometria ni coordenadas. La bandera, escudo y mapa localizador se intentan
resolver en el navegador desde Wikidata/Wikimedia Commons; tambien se consultan
etiquetas traducidas, pais, region superior y capitales de Wikidata. La pagina
muestra ademas las capitales y la ciudad mayor registradas en la base local
cuando existen.

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
  Configuracion activa de scraping en TOML.
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

La suite principal es offline: no usa red, no toca `db.sqlite3` y evita crear
base de datos de test. Sirve para validar cambios de scraping, TOML y logica de
dominio antes de ejecutar operaciones caras.

```powershell
py manage.py test ciudades_del_mundo.tests --verbosity 2
```

Tambien puede ejecutarse con `unittest` directo:

```powershell
py -m unittest discover ciudades_del_mundo\tests -v
```

Para comprobar solo las configuraciones reales de `subdivisions/*.toml`:

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
- anadir tests para configuraciones TOML y scrapers HTML

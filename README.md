# BEOGRAD-CITIES-POPULATION

Proyecto Django para:

- scrapear divisiones administrativas y poblacion desde `citypopulation.de`
- persistir el resultado en `AdminArea`
- construir subdivisiones derivadas o historicas en `NuevoAdminArea`
- exportar esas jerarquias a CSV y Excel

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
  Dashboard y listado simple para inspeccionar el estado de la base de datos.

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

[[pages]]
source = "table"
path = ["andalucia", "aragon", "asturias"]
lowest_level = 1

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
```

### Exportar

```powershell
py manage.py export_nuevoadmin_csv --country-id spanish_federal_republic
py manage.py export_nuevoadmin_excel --country-id spanish_federal_republic
```

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
py manage.py runserver
```

Si no existe `requirements.txt`, instala al menos:

```powershell
pip install django requests beautifulsoup4 lxml openpyxl
```

## Notas operativas

- Las migraciones se consideran codigo generado y no forman parte de la logica de scraping.
- Los ficheros `subdivisions/*.py.txt` son material antiguo de referencia y no participan en el flujo actual.
- El dashboard web es util para inspeccion manual, no como API publica.

## Siguientes mejoras razonables

- mover recetas historicas y nuevas subdivisiones a un formato declarativo unificado
- limpiar codificacion legacy en algunos datos historicos
- anadir tests para configuraciones TOML y scrapers HTML

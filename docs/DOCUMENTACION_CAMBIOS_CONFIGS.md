# Documentación de cambios - /configs/ y scraping modular

Fecha: 2026-06-07  
Proyecto: BEOGRAD-CITIES-POPULATION

## Regla principal indicada por Daniel

No se debe cambiar nada que no se haya pedido explícitamente.

## Regla de documentación acumulativa

Cada vez que se pida un cambio, se debe entregar este documento actualizado con lo hablado y cambiado. Esta documentación queda como registro vivo: debe añadirse lo tratado en conversaciones anteriores, la petición actual y las decisiones técnicas nuevas que deban mantenerse para conversaciones futuras.

## Contexto previo documentado de /configs/

En `/configs/`, el ciclo de populación debe mantenerse así:

| Estado visible | Condición | Acciones posibles |
| --- | --- | --- |
| Por validar | No tiene datos, se ha limpiado o se ha modificado la configuración | Validar, Popular, Limpiar solo si tiene datos |
| Validando... | Proceso de validación | Parar; al parar vuelve a Por validar |
| Validado | Solo si está validado | Popular, Limpiar solo si tiene datos |
| Populando... | Proceso de populación | Parar; al parar vuelve a Validado |
| Populado | Cuando ya se han scrapeado los datos | Limpiar |
| Limpiando... | Si se están limpiando los datos | Parar; al parar vuelve al estado anterior |
| Fallo | Si hay algún error | Validar, Popular validando primero, Limpiar solo si hay datos |

Reglas visuales y de comportamiento:

- Los badges deben tener colores distintos.
- Los badges no deben tener iconos.
- Debe poder filtrarse por cada estado del ciclo.
- Al terminar una tarea debe mostrarse el estado final actualizado.
- Validando, Populando y Limpiando deben tener animación constante de puntos "...".
- La animación de puntos no debe cambiar el tamaño del badge.

## Contexto previo documentado de limpieza

- Se detectó un error de SQLite durante la limpieza de datos: `sqlite3.OperationalError: too many SQL variables`.
- La causa fue un borrado masivo con demasiadas variables SQL en SQLite.
- Se introdujo borrado seguro por lotes para `clear_config_data`.
- Después apareció el error: `manage.py clear_config_data: error: unrecognized arguments: --config-slug spain`.
- Se ajustó `clear_config_data` para aceptar `--config-slug` y también permitir `country_code` posicional.
- La limpieza debe marcar las configuraciones afectadas como no validadas y limpiar el error de validación.

## Contexto previo documentado de análisis de código

Se revisó el proyecto como una aplicación local Django para scrapear CityPopulation, persistir `AdminArea`, construir jerarquías derivadas en `NuevoAdminArea` y exportar CSV/Excel.

Conclusiones duraderas:

- Mantener la arquitectura por capas: `domain`, `ports`, `application`, `infrastructure`, `services`, `web` y `management`.
- No introducir lógica específica de país dentro del dominio ni dentro de scrapers genéricos si puede resolverse con configuración.
- La aplicación debe tratarse como herramienta local salvo que se añadan autenticación, permisos y hardening de settings.
- Las operaciones destructivas y la edición de recetas Python son riesgosas si la app se expone fuera de localhost.
- Las vistas y servicios grandes deben refactorizarse solo cuando el cambio lo pida explícitamente o sea necesario para no duplicar lógica.

## Contexto previo documentado de scraping modular

Se añadió un mecanismo de hints por página para no romper los tipos de scraping existentes:

- `include_root`
- `root_level`
- `root_code`
- `root_name`
- `root_parent_code`
- `root_entity_type`
- `table_levels`
- `include_tables`

También se añadieron extensiones runtime de configuración:

- `[[synthetic_entities]]`
- `[[parent_overrides]]`
- `[[root_metric_sources]]`

Reglas técnicas:

- Las tablas o cuerpos usados solo como contexto deben poder participar en la resolución de padres sin persistirse.
- El padre debe resolverse por el nivel superior más cercano disponible, no asumiendo siempre `level - 1`.
- Si una página no tiene `infosection`, la población y superficie de la raíz pueden calcularse sumando los hijos directos de mayor rango.
- Los scrapers existentes `admin`, `table`, `double`, `cities` e `infosection` deben seguir funcionando.
- La modularidad debe venir de configuración y helpers reutilizables, no de ramas `if country == ...`.

## Cambios añadidos en la entrega actual

### 1. Scraper `admin` con tablas de contexto

`include_tables` ahora también funciona sobre cuerpos jerárquicos `admin1`, `admin2`, etc. Esto permite que `/tunisia/mun/admin/` use las governorates como contexto de padres y persista solo los municipios.

Ejemplo conceptual:

```toml
include_tables = ["admin2"]
table_levels = { admin1 = 1, admin2 = 3 }
```

### 2. Configuraciones TOML actualizadas

Se actualizaron las semillas TOML de:

- `tunisia.toml`
- `andorra.toml`
- `monaco.toml`
- `malta.toml`
- `belgium.toml`
- `netherlands.toml`
- `caribbeannetherlands.toml`
- `aruba.toml`
- `curacao.toml`
- `sintmaarten.toml`
- `westernsahara.toml`
- `frenchpolynesia.toml`
- `saintbarthelemy.toml`
- `saintmartin.toml`
- `stpierremiquelon.toml`

### 3. Niveles configurados en la entrega actual

| Configuración | Niveles |
| --- | --- |
| Túnez | 0 País, 1 Governatura, 2 Delegación, 3 Municipio |
| Andorra | 0 País, 1 Parroquia, 2 Lugar |
| Mónaco | 0 País, 1 Quarter, 2 Quarter/Sector |
| Malta | 0 País, 1 Distrito, 2 Localidad |
| Bélgica | 0 País, 1 Región, 2 Provincia, 3 Distrito, 4 Municipio, 5 Municipio/Submunicipio |
| Países Bajos | 0 País, 1 Provincia, 2 Municipio, 3 Centro urbano |
| Caribe Neerlandés | 0 Territorio, 1 Municipio especial, 2 Lugar |
| Aruba | 0 País, 1 Región, 2 Ciudad/área urbana, 3 Zona |
| Curazao | 0 País, 1 Ciudad/lugar, 2 Geozone, 3 Vecindad |
| San Martín - Países Bajos | 0 País, 1 Lugar |
| Sáhara Occidental | 0 Territorio, 1 Provincia/RASD, 2 Ciudad/Pueblo |
| Polinesia Francesa | 0 Colectividad, 1 Circunscripción, 2 Lugar |
| San Bartolomé | 0 Colectividad, 1 Área estadística, 2 Capital |
| San Martín - Francia | 0 Colectividad, 1 Lugar |
| Saint-Pierre and Miquelon | 0 Colectividad, 1 Comuna |

### 4. Decisiones especiales por país/territorio

- Bélgica declara sintéticamente `Flemish Region` y `Walloon Region`, y mantiene `Région de Bruxelles-Capitale` como región existente de CityPopulation. Las provincias se reparentan a Flandes o Valonia por configuración.
- Bélgica usa `/belgium/{provincia}/` para distritos y municipios, y `/belgium/places/{provincia}/` para submunicipios, manteniendo los municipios como contexto cuando procede.
- Túnez separa delegaciones y municipios: `/tunisia/admin/` aporta governaturas/delegaciones y `/tunisia/mun/admin/` aporta municipios bajo governatura.
- Países Bajos usa `/netherlands/admin/` para provincias/municipios y `/netherlands/{provincia}/` para centros urbanos. Algunos centros sin municipio explícito en la URL pueden quedar sin padre automático si CityPopulation no da `radm`.
- Aruba mantiene regiones y zonas desde `/aruba/admin/`, y añade ciudades/áreas urbanas desde `/aruba/cities/`.
- Curazao mantiene geozones/vecindades desde `/curacao/admin/`, y añade ciudades/lugares desde `/curacao/cities/`.
- San Martín neerlandés, San Martín francés y Saint-Pierre and Miquelon usan la tabla principal como contexto y persisten los lugares/comunas en nivel 1.

### 5. Documentación interna del repo

`AGENTS.md` se actualizó para documentar:

- uso de `include_tables` también con `admin1`, `admin2`, etc.;
- uso de `table_levels` para saltos semánticos de nivel;
- uso de hints de raíz;
- uso de entidades sintéticas y overrides como datos, no como ramas de código.

## Validación ejecutada

Se ejecutó:

```bash
python -m compileall -q ciudades_del_mundo
PYTHONPATH=. python -m unittest ciudades_del_mundo.tests.test_scraping_admin -v
```

Resultado: OK.

No se pudo ejecutar la suite completa Django ni `manage.py check` porque el entorno no tiene instalado `django`.

## Archivos principales modificados

- `AGENTS.md`
- `ciudades_del_mundo/infrastructure/scraping/admin.py`
- `ciudades_del_mundo/tests/test_scraping_admin.py`
- `ciudades_del_mundo/subdivisions/tunisia.toml`
- `ciudades_del_mundo/subdivisions/andorra.toml`
- `ciudades_del_mundo/subdivisions/monaco.toml`
- `ciudades_del_mundo/subdivisions/malta.toml`
- `ciudades_del_mundo/subdivisions/belgium.toml`
- `ciudades_del_mundo/subdivisions/netherlands.toml`
- `ciudades_del_mundo/subdivisions/caribbeannetherlands.toml`
- `ciudades_del_mundo/subdivisions/aruba.toml`
- `ciudades_del_mundo/subdivisions/curacao.toml`
- `ciudades_del_mundo/subdivisions/sintmaarten.toml`
- `ciudades_del_mundo/subdivisions/westernsahara.toml`
- `ciudades_del_mundo/subdivisions/frenchpolynesia.toml`
- `ciudades_del_mundo/subdivisions/saintbarthelemy.toml`
- `ciudades_del_mundo/subdivisions/saintmartin.toml`
- `ciudades_del_mundo/subdivisions/stpierremiquelon.toml`
- `docs/DOCUMENTACION_CAMBIOS_CONFIGS.md`

## Plantilla para próximas entregas

Cuando Daniel pida nuevos cambios, añadir aquí:

1. Fecha y resumen de la petición.
2. Páginas HTML o rutas implicadas.
3. Niveles administrativos esperados.
4. Cambios de código hechos.
5. Cambios TOML hechos.
6. Riesgos o limitaciones.
7. Validaciones ejecutadas.
8. Archivos entregados.

---

## Entrega 1 - Documentación retroactiva de la primera tanda de scraping

Fecha documentada: 2026-06-07.

Esta sección se añade retroactivamente porque Daniel pidió dejar anotada también la primera tanda de scraping modular.

### Objetivo técnico de la primera tanda

- Crear una base refactorizable, modular y compatible con los tipos existentes de scraping.
- Evitar ramas específicas por país en los scrapers.
- Permitir que cada página declare desde TOML su nivel raíz, niveles de tablas, tablas persistidas y tablas usadas solo como contexto.
- Resolver padres por contexto real de la página, no por una resta fija `level - 1`.
- Permitir agrupaciones administrativas sintéticas para casos complejos, especialmente Francia.
- Calcular población y superficie de raíces sintéticas desde hijos cuando una página no tenga `infosection` suficiente.

### Hints y extensiones introducidas en la primera tanda

- `include_root`
- `root_level`
- `root_code`
- `root_name`
- `root_parent_code`
- `root_entity_type`
- `table_levels`
- `include_tables`
- `[[synthetic_entities]]`
- `[[parent_overrides]]`
- `[[root_metric_sources]]`

### Configuraciones de la primera tanda

| Configuración | Niveles acordados |
| --- | --- |
| Gibraltar | 0 País; 1 Zona estadística |
| Francia | 0 País; 1 Metrópolis/Ultramar; 2 Región/Departamento de ultramar; 3 Departamento; 4 Distrito; 5 Comuna |
| España | 0 País; 1 CCAA/Ciudad Autónoma; 2 Provincia/Ciudad Autónoma; 3 Municipio; 4 Localidad |
| Portugal | 0 País; 1 Distrito/Región Autónoma; 2 Municipio; 3 Parroquia; 4 Localidad |
| Marruecos | 0 País; 1 Región; 2 Provincia/Prefectura; 3 Comuna; 4 Zona urbana |
| Argelia | 0 País; 1 Provincia; 2 Comuna; 3 Localidad |
| Italia | 0 País; 1 Región; 2 Provincia; 3 Comuna; 4 Localidad |

### Rutas HTML de la primera tanda

- `/gibraltar/admin/`
- `/france/reg/admin/`
- `/france/admin/`
- `/france/{departamento}/`, ejemplo `ain`
- `/france/cities/{departamentoultramar}/`, ejemplo `guyane`, excepto Mayotte
- `/france/cities/mayotte/`
- `/spain/admin/`
- `/spain/{ccaa}/`, ejemplo `andalucia`
- `/spain/localities/{provincia}/`, ejemplo `almeria`
- `/spain/{ciudadautonoma}/`, ejemplo `ceuta`
- `/portugal/admin/`
- `/portugal/{regaut_o_distrito}/admin/`, ejemplo `acores`
- `/portugal/{regaut_o_distrito}/`, ejemplo `acores`
- `/morocco/admin/`
- `/morocco/{region}/`, ejemplo `benimellalkhenifra`
- `/morocco/{region}/admin/`, ejemplo `benimellalkhenifra`
- `/italy/admin/`
- `/italy/{region}/`, ejemplo `abruzzi`
- `/italy/localities/{region}/`, ejemplo `abruzzi`
- `/algeria/admin/`
- `/algeria/{prov}/`, sin fichero de ejemplo en la entrega inicial

### Decisiones especiales de la primera tanda

- Francia crea agrupaciones sintéticas `METRO` y `OVERSEAS` para separar Francia metropolitana y ultramar.
- Francia metropolitana se alimenta de `/france/reg/admin/`; ultramar se alimenta con páginas `cities/{departamentoultramar}`.
- En Francia, los departamentos ultramarinos quedan bajo `OVERSEAS`, y comunas/distritos se conectan respetando el salto semántico de niveles.
- España usa páginas autonómicas para provincias/municipios y páginas `localities/{provincia}` para localidades.
- Ceuta/Melilla pueden actuar simultáneamente como nivel 1/2 según la estructura indicada por CityPopulation y el nivel configurado.
- Portugal combina `/admin/`, páginas regionales/distritales `/admin/` y páginas de localidades por región/distrito.
- Marruecos separa provincias/comunas y zonas urbanas, dejando tablas previas como contexto cuando haga falta.
- Italia usa `/italy/localities/{region}/` para localidades bajo comunas ya existentes.
- Argelia quedó parcialmente inferida para `/algeria/{prov}/` al no haber fichero de ejemplo.

### Validación de la primera tanda

Se ejecutó:

```bash
python -m compileall -q ciudades_del_mundo
PYTHONPATH=. python -m unittest ciudades_del_mundo.tests.test_scraping_admin -v
```

Resultado: OK en el entorno disponible. No se pudo ejecutar la suite Django completa porque el entorno no tenía instalado `django`.

---

## Entrega 3 - Documentación incorporada al Markdown interno

Fecha: 2026-06-07.

Esta sección sincroniza el Markdown interno con el DOCX round3.

### Configuraciones añadidas o revisadas en round3

| País / territorio | Niveles |
| --- | --- |
| Wallis y Futuna | 1 Distritos; 2 Pueblo |
| Liechtenstein | 1 Distritos; 2 Comuna |
| Luxemburgo | 1 Canton; 2 Comuna; 3 Localidad |
| San Marino | 1 Municipios |
| República Checa | 1 Región; 2 Distrito; 3 Localidad/Pueblo/Villa |
| Eslovaquia | 1 Región; 2 Distrito; 3 Municipio/Ciudad |
| Austria | 1 Estado; 2 Distrito; 3 Comuna; 4 Localidad |
| Hungría | 1 Condado; 2 Distrito; 3 Localidad |
| Eslovenia | 1 Región; 2 Municipio; 3 Localidad |
| Croacia | 1 Condado; 2 Municipio; 3 Localidad |
| Serbia | 1 Distrito; 2 Municipio; 3 Localidad |
| Vaticano | 1 Ciudadano/No ciudadano |

### Decisiones técnicas de round3

- Se reforzó el lookup genérico de padres en `table`/`double` con aliases de nombre completo, nombre sin corchetes/paréntesis y forma compacta.
- Se añadió resolución por prefijo único para celdas `radm` abreviadas.
- República Checa excluye Praga en páginas regionales porque no sigue el patrón `/czechrep/{region}/`.
- Hungría mantiene Budapest excluido en páginas por condado.
- Vaticano se trata como raíz país nivel 0 y dos filas nivel 1: `Citizens` y `Non-Citizens`.

---

## Entrega 4 - Anotaciones de población compartida y columna condicional

Fecha: 2026-06-07.

### Petición documentada

Daniel pidió:

- actualizar este documento con la primera tanda de scraping;
- cuando se asigna padre desde una columna de padre, si esa columna está vacía, usar como padre el objeto de la tabla de contexto con el mismo nombre;
- si la columna de padre contiene varios valores divididos por `/`, aplicar la misma lógica: preferir el que coincida con el nombre del objeto;
- si ninguno coincide, intentar el primer valor de la lista;
- cuando se use esta regla, añadir el campo `Anotaciones` con el texto `Comparte población con otras divisiones`;
- agregar una columna `Anotaciones` en las tablas de `/countries/` solo si alguno de los objetos visibles tiene anotación.

### Cambios técnicos aplicados

- Se añadió `annotations` a `ScrapedAdminArea`.
- Se añadió `AdminArea.annotations` como `TextField` con nombre visible `Anotaciones`.
- Se añadió la migración `0022_adminarea_annotations.py`.
- El repositorio Django persiste `annotations` en `bulk_create(... update_conflicts=True ...)`.
- El scraper `double/table` ahora resuelve celdas `radm` vacías por padre del mismo nombre cuando existe.
- El scraper `double/table` ahora resuelve celdas `radm` con `/` priorizando el nombre coincidente y, si no hay coincidencia, el primer segmento.
- Las filas resueltas por esas reglas reciben la anotación `Comparte población con otras divisiones`.
- El JSON de detalle de `/countries/` incluye `annotations` por fila.
- El frontend de la tabla de datos de `/countries/` añade la columna `Anotaciones` solo si al menos una fila cargada tiene anotación.
- La búsqueda de esa tabla también contempla el texto de anotaciones.

### Validación ejecutada

- `python -m compileall -q ciudades_del_mundo`.
- `PYTHONPATH=. python -m unittest ciudades_del_mundo.tests.test_scraping_admin -v`: 12 tests OK.
- `node --check ciudades_del_mundo/static/ciudades_del_mundo/app.js`.
- Revisión visual del DOCX actualizado tras renderizarlo a PNG.


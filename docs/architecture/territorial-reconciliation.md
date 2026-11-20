# Reconciliación territorial asistida por IA

## Estado e integración HTTP pendiente

El módulo Python retenido implementa importación SQL, descubrimiento controlado,
propuesta, revisión por CLI, comparación y aplicación transaccional. No modifica
`ScrapingConfig`, ni activa el modo IA por defecto.

El [contrato HTTP activo](python-http-retirement.md) mantiene Java y Python;
en modo Java, Django conserva UI, tareas y rutas pendientes. La reconciliación
territorial todavía no está conectada a botones o trabajadores HTTP, ni tiene
implementación nativa Java. El flujo solicitado desde el botón **no está
terminado**. La CLI descrita aquí es operativa como entrada explícita separada.

## Estrategias y motor local

`traditional` sigue siendo la estrategia predeterminada de `scrape_subdivisions`.
La alternativa `ai` usa por defecto `LocalTerritorialEngine`: un sistema simbólico
local que cruza códigos, nombres/alias revisados, QID cuando existe, ascendencia
y excepciones explícitas. No es un LLM, no entrena ni genera hechos oficiales.
Una identidad o exclusión ambigua queda sin resolver y bloquea la validación.
Con evidencia SQL congelada no requiere credenciales ni consultas de red.

`--ai-engine provider` selecciona explícitamente el proveedor JSON externo.
Comparte el contrato de propuesta y la validación determinista; los límites de
transporte y credenciales no se aplican al motor local. `--discover` autoriza
por separado descubrimiento HTTP, incluso usando motor local.

```powershell
# Proponer; requiere referencia revisada y evidencia ya importadas en SQL:
py manage.py scrape_subdivisions spain --strategy ai --evidence-id 1
# Revisar sin aplicar:
py manage.py scrape_subdivisions spain --strategy ai --territorial-run <run_id>
# Aplicar exactamente el plan revisado:
py manage.py scrape_subdivisions spain --strategy ai --territorial-run <run_id> --plan-hash <hash>
```

Sustituye `1`, `<run_id>` y `<hash>` por los identificadores reales. La estrategia
exige un país explícito y no acepta opciones tradicionales de assets/enriquecimiento.
`reconcile_territory` también acepta `--ai-engine local|provider` y mantiene su
interfaz separada `--show` / `--apply --plan-hash`.

Los propietarios nuevos son `application/ai/local_territorial.py` (inferencia),
`application/ai/territorial.py` (propuestas/herramientas limitadas) y
`services/scraping_strategies.py` (composición de estrategias). Las métricas
persistidas distinguen `engine`, `engine_calls` y `provider_calls`; el motor
local informa cero llamadas al proveedor y no tiene consumo de tokens remoto.

## Tres entradas independientes

1. `TerritorialReference`: referencia revisada, procedencia, versión, ámbito,
   roles, niveles, entidades, relaciones y excepciones. La carga exige identificar
   a una persona revisora. Una versión existente no puede sustituirse por otro
   contenido mediante el importador.
2. `TerritorialEvidence`: páginas HTML congeladas y observaciones del parser
   existente. Su versión es el hash del documento. Se reconstruyen las
   observaciones desde el HTML para detectar cambios del parser o adulteración.
3. Datos `AdminArea` vigentes y configuraciones SQL: fotografía de regresión y
   control de concurrencia. No se envían como referencia territorial al modelo.

La inspección de la SQLite del proyecto en modo de solo lectura no encontró un
catálogo territorial independiente: había resultados, configuraciones y jerarquías
derivadas. Estos últimos no se convierten implícitamente en referencia oficial.
La migración `0030` crea las tres tablas nuevas, incluida `TerritorialRun`.
No importa semillas ni referencias de España y no se ha aplicado a la base local
durante el desarrollo.

## Datos que requieren aportación y validación

Se necesita una referencia independiente y revisada para cada ámbito que vaya a
aplicarse. No se suministra un catálogo oficial de España. La referencia requiere
las entidades cubiertas, con identificadores locales estables, códigos fuente
verificados, nombres o alias, roles y padres. Una organización descrita solo en
prosa o una lista de roles no permite comprobar cobertura e identidad completa y
no es suficiente para aplicar un plan.

Las excepciones y repeticiones deben tener procedencia propia. Un QID es opcional;
si existe en la referencia, debe coincidir con la evidencia. No se completa un QID,
una localidad o una población con conocimiento del modelo. El ámbito debe declarar
si es parcial; superar una prueba parcial no demuestra cobertura nacional.

Ejemplo **esquemático**, que debe sustituirse por datos revisados del ámbito real:

```json
{
  "schema_version": 1,
  "country": "country_slug",
  "version": "reference-release-1",
  "provenance": "Publicación, fecha, identificador o URL de procedencia verificada",
  "scope": "Ámbito exacto y limitaciones de cobertura",
  "entry_path": "country_slug/",
  "roles": [{"id": "country", "level": 0}],
  "entities": [{
    "id": "reference_root",
    "code": "country_slug",
    "names": ["Nombre verificado"],
    "role": "country",
    "parent": null
  }],
  "exceptions": []
}
```

Los niveles deben ser consecutivos desde L0 y tienen el mismo significado en todas
las ramas. `entities[].parent` usa el ID de referencia, no el código de página.
`repeat_of` permite repetir evidencia para una función territorial consecutiva,
solo cuando apunta al padre y una excepción documentada incluye esa entidad:

```json
{
  "id": "sourced_exception",
  "entities": ["reference_repeated_entity"],
  "reason": "Motivo territorial verificado de la repetición",
  "provenance": "Fuente y versión verificadas"
}
```

## Comandos y efectos

Desde la raíz, con las dependencias Python instaladas:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
py manage.py migrate
py manage.py import_territorial_reference referencia.json --reviewed-by "Persona revisora"
py manage.py import_territorial_evidence country_slug paginas.json
```

`migrate` modifica la base configurada y debe ejecutarse de forma explícita.
Los importadores escriben exclusivamente sus entradas versionadas en SQL. El
archivo de páginas es un objeto `{URL_permitida: HTML_congelado}`. No se importan
observaciones ya redactadas por un modelo. Los archivos son puentes explícitos de
importación; el flujo operativo no los consulta como fallback.

Para generar una propuesta con una versión SQL de evidencia, el importador informa
el valor numérico de `evidence` que se debe usar:

```powershell
py manage.py reconcile_territory country_slug --new --evidence-id 123
```

Este comando usa el motor local por defecto, sin credenciales. Para consultar
al proveedor añade `--ai-engine provider` y configura `CIUDADES_AI_API_KEY` y
`CIUDADES_AI_MODEL`. Ningún motor aplica áreas ni modifica el TOML tradicional
al proponer. Informa un ID de ejecución y el hash del plan validado.
Para descubrir páginas nuevas en red, en
lugar de reutilizar evidencia congelada, la petición debe ser explícita:

```powershell
py manage.py reconcile_territory country_slug --new --discover
```

La revisión y aplicación se separan de la generación de propuestas:

```powershell
py manage.py reconcile_territory ID_EJECUCION --show
py manage.py reconcile_territory ID_EJECUCION --apply --plan-hash HASH_REVISADO
```

`--show` muestra estructura esperada, observada, correspondencias, diferencias por
entidad/campo, pendientes, diagnóstico y métricas, sin llamadas al proveedor.
`--apply` exige el hash revisado, revalida todas las entradas y aplica datos SQL.
Volver a aplicar una ejecución aplicada devuelve su resultado previo sin repetir
las modificaciones. Una ejecución inválida o cancelada requiere una propuesta
nueva; puede reutilizar la evidencia SQL conservada. El scraping tradicional se
sigue seleccionando mediante sus comandos existentes y su configuración SQL.

## Descubrimiento, seguridad y límites

No se necesita una receta TOML para obtener el plan. La aplicación empieza en
`https://www.citypopulation.de/en/{entry_path}` y sigue enlaces de páginas del
mismo país. Reutiliza `CityPopulationSectionParser` en modo de observación de
secciones. Los niveles observados son locales a cada página: la propuesta debe
asignarlos a roles nacionales, sin tomar la disposición de una página como la
jerarquía oficial.

Solo se permiten HTTPS, el host exacto, la ruta del país y páginas terminadas en
`/` o `.html`. También se comprueban redirecciones. No se permiten consultas,
credenciales en URL, traversal ni otros países. Un enlace no alcanzable no se
inventa: se aportan páginas congeladas explícitas o el plan falla por cobertura.

El modelo dispone únicamente de consultas `reference` y `evidence` por IDs
existentes, hasta 100 IDs por consulta. No recibe capacidad de ejecutar SQL, código
ni URLs. Los textos scrapeados se presentan como datos no confiables y no se
ejecutan instrucciones contenidas en ellos.

Límites actuales: descubrimiento de hasta 100 páginas por defecto (máximo 150),
20 segundos por petición (máximo 30), 240 segundos por descubrimiento (máximo
300), 4 MB por página y 30 MB totales. Si quedan páginas al alcanzar un límite,
se rechaza el descubrimiento incompleto. Las importaciones congeladas admiten
hasta 150 páginas con los mismos límites de bytes.

La propuesta admite seis llamadas en total, incluida una repetición tras error de
proveedor, 500.000 caracteres de entrada acumulada por llamada, 300 segundos de
presupuesto y un timeout HTTP de hasta 45 segundos. El proveedor recibe un máximo
de 16.000 tokens de salida por llamada; el cuerpo HTTP tiene un máximo de 2 MB.
Una respuesta agotada/incompleta falla el mismo validador. No hay procesamiento
por lotes automático para referencias nacionales que superen estos límites: se
debe preparar un ámbito revisado menor. No se debe declarar ese resultado parcial
como aceptación de un país completo.

`TerritorialRun.metrics` guarda modelo, duración, límites y `usage` real cuando el
proveedor lo expone. La ausencia de información se representa como no disponible,
sin inferir cero tokens ni estimar costes. No se registra la clave del proveedor.

## Validación, persistencia y arquitectura

El plan v1 contiene país y versiones de referencia/evidencia, nodos con IDs de
evidencia y referencia, rol, nivel, padre, motivo verificable, discrepancias y
pendientes. No acepta nombres nuevos, métricas, códigos ejecutables o URLs como
campos de decisión. El primer ID de evidencia de cada nodo identifica la
observación usada para sus métricas; las otras observaciones permanecen auditables
en SQL. La comparación señala cualquier diferencia con los valores vigentes.

Se valida esquema estricto, identidades, alias/QID, códigos fuente, existencia de
citas, cobertura completa de referencia y evidencia, niveles consecutivos,
padres, ausencia de ciclos, coherencia de padres observados, repeticiones
documentadas y estabilidad frente al enlazador existente. Los contextos de página
sin identificador real se reconocen expresamente; el slug de país usado como
fallback por el parser no se trata como una identidad mundial compartida.
Una confianza declarada por el modelo no tiene efecto en estas comprobaciones.

Para observaciones no territoriales puede añadirse `exclusions` a la referencia:
cada regla requiere `id`, `source_codes`, `names`, `reason` y `provenance`.
El plan debe citarlas expresamente en `excluded`, con `evidence_id`, `reference_id`
y `reason`. Se comprueban código y nombre exactos contra la regla revisada; el
modelo no puede excluir libremente una fila ni omitir una entidad esperada. Las
exclusiones no borran áreas vigentes y las observaciones originales permanecen
en la evidencia SQL.

Antes de aplicar se comprueban los valores SQL reales de referencia, evidencia,
configuraciones y áreas vigentes, incluyendo enlaces de capitales, además de las
últimas versiones publicadas y el hash del plan revisado. Las consultas al
proveedor ocurren fuera de transacciones. La aplicación usa el bloqueo SQLite
existente y una transacción que incluye `save_many`, cálculos derivados y el estado
de ejecución. No llama a `reset_country` ni `delete_missing`; los IDs, traducciones,
assets, capitales, representantes y marcas de fusión se conservan. Cambiar una
relación de una entidad participante en una fusión se rechaza. Las ramas sin
evidencia no se borran y participan en el cálculo determinista de descendientes
más poblados. Una cancelación cooperativa durante la aplicación revierte la
transacción; no se integra aún con un trabajador HTTP activo.

Propietarios:

- `domain/territorial_reconciliation.py`: tipos, referencias y validación pura.
- `ports/territorial_reconciliation.py`: límites de referencia, evidencia,
  proveedor y persistencia.
- `application/ai/territorial.py`: propuesta y herramientas controladas.
- `application/ai/local_territorial.py`: inferencia simbólica local predeterminada.
- `application/territorial_reconciliation.py`: compilación y comparación por
  entidad, reutilizando reglas de dominio y conservando compatibilidad de imports.
- `infrastructure/scraping/territorial_evidence.py`: HTTP limitado, congelación y
  adaptación del parser existente.
- `infrastructure/django/territorial_reconciliation.py`: importaciones SQL,
  fotografías, enlazador y aplicación con el repositorio existente.
- `services/territorial_reconciliation.py`: selección de motor local o proveedor
  JSON compartido, estado, diagnóstico, métricas y cancelación cooperativa.
- `services/scraping_strategies.py`: estrategias tradicional/IA para la CLI.
- `management/commands/*territorial*` y `reconcile_territory.py`: entrada CLI.

## Comparación offline y España

La fixture `tests/fixtures/territorial_spain.json` contiene ocho páginas **sintéticas**,
23 entidades esperadas, la receta tradicional usada, la referencia declarada y los
hashes de los propietarios tradicionales capturados. Sus códigos y poblaciones
son datos de prueba, no estadísticas oficiales. Se declaran independientemente
las ramas y relaciones en `tests/territorial_support.py`; el proveedor simulado
no consulta la salida tradicional para construir la propuesta.

Ambas estrategias reciben exactamente las mismas páginas congeladas y versión de
referencia. Se comparan todas las identidades, niveles, padres, tipos y métricas,
incluyendo fechas, QID y descendiente más poblado. Las comprobaciones cubren
Andalucía con dos provincias de prueba, Madrid con comunidad y provincia separadas,
las repeticiones explícitas de Ceuta/Melilla, municipios con varias localidades,
QID compartidos entre niveles y dos partes de localidad con QID coincidente.
Se comprueban las ramas completas de esa fixture, no los totales de España.

```powershell
py manage.py test ciudades_del_mundo.tests.test_territorial_reconciliation ciudades_del_mundo.tests.test_ai_provider ciudades_del_mundo.tests.test_architecture_boundaries --settings=ciudades_del_mundo.tests.isolated_settings
```

El runner impide conexiones externas y usa SQLite en memoria. Incluye casos de
fallo, concurrencia, rollback y regresión. Esta prueba con proveedor simulado no
demuestra calidad del modelo real ni equivalencia sobre páginas reales de España.
Para recapturar deliberadamente el resultado tradicional de la fixture, revisando
después el diff, existe `py -B tools/capture_territorial_spain.py --write`.

## Aceptación entre procesos

```powershell
py -B tools/verify_territorial_scraping.py
py -B tools/verify_territorial_scraping.py --engine provider
```

El primer comando prueba el motor local de producción; el segundo simula el
transporte del proveedor, sin llamadas pagadas. Ambos usan procesos CLI separados,
SQLite temporal y sockets bloqueados. Cubren propuesta, revisión, hash aprobado,
aplicación, repetición, entradas obsoletas y cancelación. No usan la base local.
`test_local_territorial_ai` y `test_scraping_strategies` amplían las pruebas
unitarias. Estos comandos no se han ejecutado durante esta revisión documental.

## Prueba optativa con proveedor real

Fuera de la suite offline y exclusivamente con una base desechable en memoria:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:CIUDADES_TERRITORIAL_REAL_TEST='1'
# Configurar CIUDADES_AI_API_KEY y CIUDADES_AI_MODEL en el entorno.
py manage.py probe_territorial_provider --allow-paid --settings=ciudades_del_mundo.tests.isolated_settings
```

Exige ambas activaciones y credenciales. Rechaza cualquier base que no sea SQLite
`:memory:`. Crea sus tablas de prueba, carga la misma referencia y páginas
sintéticas, consulta al proveedor real, valida y compara con el mismo comparador.
Emite diferencias por entidad/campo, diagnóstico y consumo informado. No aplica
una propuesta en la base vigente ni hace scraping real. Una prueba real exitosa
solo se refiere a esta fixture y a esa ejecución/modelo, no al país completo.

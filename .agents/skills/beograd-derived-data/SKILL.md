---
name: beograd-derived-data
description: Modificar países, subdivisiones o grupos derivados Beograd, sus recetas TOML, selecciones de origen, capitales y exportaciones CSV/XLSX.
---

# Datos derivados Beograd

Lee [AGENTS.md](../../../AGENTS.md) y
[jerarquías derivadas](../../../docs/architecture/derived.md).
`DerivedCountryConfig`, `DerivedSubdivision` y `SubdivisionGroup` en SQL,
incluido `content`, son la fuente de verdad. Elige el propietario según el flujo:

- `new_country_configs/`: semillas de países y variantes.
- `subdivision_groups/groups/`: bundles compactos de grupos por país.
- `subdivision_groups/subdivisions/`: bundles de subdivisiones/diccionarios.
- Recetas Python heredadas: conserva compatibilidad del constructor existente.

Importar con `sync_derived_configs` no construye `NuevoAdminArea`.
Construir puede borrar/recrear filas no raíz: consulta
[efectos de comandos](../../../docs/architecture/operations.md#command-effects)
y respeta el alcance autorizado sobre datos existentes.

Preserva IDs/códigos, niveles municipales heredados, referencias de grupos,
operaciones de suma/resta, capitales, representantes y relaciones de origen.
Seleccionar distritos de una ciudad unificada representa solo esos distritos;
usa `services/composite_city_sources.py` y evita expandir a la ciudad completa.
Verifica solapamientos, sumas y repetición de construcciones con datos aislados.

Para selectores pide país y nivel/página/búsqueda. La carga inicial vacía
no debe borrar selecciones guardadas ni convertir resta en suma.
El harness `tests/frontend/group_selection.test.cjs` se ejecuta aparte.

Mantén el formato exportado y las métricas puras en `application/export_metrics.py`;
la escritura XLSX pertenece a infraestructura. Consulta
[exportaciones](../../../docs/architecture/exports.md).
Documentar semillas no acredita importación SQL ni reconstrucción de datos.


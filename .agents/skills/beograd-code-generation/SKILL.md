---
name: beograd-code-generation
description: Modificar código de BEOGRAD-CITIES-POPULATION preservando capas, contratos HTTP Java/Python, SQL y frontend. Usar para implementación o refactorización en este repositorio.
---

# Código Beograd

Lee [AGENTS.md](../../../AGENTS.md) y revisa `git status --short` antes de editar.
Consulta [el estado acumulado](../../../docs/estado-actual.md) para separar
implementación, cobertura pendiente y resultados históricos.

- `domain` y `ports` no dependen de Django ni adaptadores; `application`
  depende del núcleo. Inyecta implementaciones desde CLI, handlers o servicios.
- Requests/respuestas pertenecen a handlers; consultas a `web/queries`;
  mutaciones SQL a servicios/adaptadores. Patches apuntan al propietario.
  `web/views.py` conserva exports HTTP explícitos.
- Para HTTP, prioriza Java y verifica ambos backends. En modo Java, Django
  sirve UI y reenvía solo `JAVA_ROUTE_NAMES`; una caída Java sigue visible.
  Las rutas pendientes son de Django. Maven usa contratos congelados sin Django.
- Conserva IDs, semántica SQL, transacciones, locks y ciclo de tareas.
- Edita JS/CSS en `static/ciudades_del_mundo/src/`, ensambla con
  `py -B tools/build_web_assets.py` y sube ambas versiones de `base.html`
  cuando cambia comportamiento distribuido. Traduce textos con gettext.

Selecciona checks según [operaciones](../../../docs/architecture/operations.md#offline-verification-scope):
settings aislados y bytecode desactivado para Python, harness frontend aplicable
y contratos Maven para Java. La construcción Java no ejecuta la suite Django.
No atribuyas a la sesión resultados históricos o una suite completa por una selección.
Para documentación, comprueba enlaces y comandos; actualízala cuando se solicite.

Detalles: [refactor](../../../docs/refactorizacion.md),
[backends](../../../docs/architecture/python-http-retirement.md),
[web](../../../docs/architecture/web.md).


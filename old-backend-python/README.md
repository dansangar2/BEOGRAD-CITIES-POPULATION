# Copia histórica del HTTP Python

Los propietarios activos se encuentran en `ciudades_del_mundo/web/`.
Esta carpeta conserva una fotografía de la etapa de retirada; no se importa
en ejecución ni es una dependencia del lanzador o de Maven.

Actualmente se mantienen Java y Python. El selector `backend.toml` y
`BEOGRAD_BACKEND` eligen entre Django solo y Django con las rutas migradas en
Java. Las rutas pendientes siguen en Django; el fallo de una ruta Java no
activa fallback. Consulta [operación vigente](../docs/architecture/python-http-retirement.md).

La carpeta `characterization/` conserva pruebas antiguas de caracterización;
Maven usa contratos sintéticos congelados sin ejecutarlas. El generador
`tools/inventory_java_api.py` todavía inspecciona esta copia histórica:
su salida describe esa fotografía, no la cobertura activa.


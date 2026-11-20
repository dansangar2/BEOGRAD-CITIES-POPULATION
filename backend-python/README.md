# Backend Python

Backend HTTP Django mantenido como alternativa a `backend-java`, con el mismo
estatus de mantenimiento. El código activo reside en
[`ciudades_del_mundo/web`](../ciudades_del_mundo/web/), compartiendo modelos,
servicios y comandos con el resto del proyecto. Se mantiene una sola
implementación Python de cada ruta.

Selecciona `backend = "python"` en [`backend.toml`](../backend.toml) y ejecuta
`levantar_app.bat` desde la raíz. El supervisor usa `backend-python/manage.py`
y conserva la configuración local, la propiedad de procesos y la parada.
Python sirve todas las rutas sin construir ni arrancar Java.

Para desarrollo, desde la raíz, `py -B backend-python/manage.py runserver`
selecciona Python explícitamente. Acepta los mismos comandos y settings que
`manage.py`; no sustituye el perfil local del supervisor.

Toda mejora HTTP debe mantenerse y verificarse en ambos backends, conservando
sus contratos. Java conserva la prioridad y el valor predeterminado del selector;
su cobertura nativa sigue incompleta. Consulta la
[operación de ambos backends](../docs/architecture/python-http-retirement.md).

`characterization/` conserva pruebas Java de referencia de una etapa anterior;
no forma parte del arranque Python ni de la suite Maven vigente.

---
name: beograd-scraping-configs
description: Configurar, diagnosticar o reparar scraping CityPopulation y reconciliación territorial de un país Beograd con objetivos por niveles y configuración SQL.
---

# Configuraciones de scraping Beograd

Lee [AGENTS.md](../../../AGENTS.md) y el
[procedimiento general](../../../docs/architecture/scraping-workflow.md).
Para errores, empieza por los logs más recientes del país y por
`py manage.py prepare_ai_autoconfig <slug>`; genera un expediente y puede
limpiar logs antiguos. No inicia scraping ni sustituye la configuración SQL.

`ScrapingConfig.content` es la fuente activa. Los TOML son puentes de importación:
entrega reparaciones específicas en el TOML del país y sincroniza SQL explícitamente
dentro del alcance autorizado. Conserva claves globales antes de las tablas.

- Define primero el objetivo territorial. Los roles ocupan niveles uniformes.
  Conserva repeticiones consecutivas con IDs distintos; evita inventar localidades.
- Usa controles genéricos de páginas, tablas, identidad, exclusiones y padres;
  evita ramas del parser por país. QID compartido no identifica una única fila.
- Normaliza hectáreas a km2 una vez y conserva el ámbito tierra/agua.
  Invalida checkpoints si cambian las métricas del parser.
- Verifica ramas completas, hijos semánticos, recursos nacionales y raíces.
  Una reparación de datos requiere SQL confirmado y detalle HTTP;
  un TOML corregido o un preview no acreditan datos reparados.

La estrategia `traditional` sigue por defecto. `--strategy ai` usa motor
simbólico local; `--ai-engine provider` elige el proveedor remoto.
Requiere referencia revisada y evidencia versionada; aplicar exige hash revisado.
Consulta [reconciliación](../../../docs/architecture/territorial-reconciliation.md).
La integración HTTP territorial sigue pendiente. El motor local no es un LLM
ni una fuente de hechos oficiales. No autoriza scraping real o llamadas pagadas.

Antes de ejecutar distingue diagnóstico, red y escritura en la
[tabla de efectos](../../../docs/architecture/operations.md#command-effects).
Prueba parsers con HTML congelado y settings aislados. Las pruebas sintéticas
no demuestran cobertura nacional ni sincronización de la base local.


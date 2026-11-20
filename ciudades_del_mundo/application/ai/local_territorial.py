"""Local symbolic territorial expert system; no model, credentials or network.

Uses reviewed identities, aliases, graph ancestry and explicit exceptions.
Unknown facts remain unresolved. It does not learn or generate official data.
"""
from ciudades_del_mundo.application.territorial_reconciliation import evidence_rows
from ciudades_del_mundo.domain.territorial_reconciliation import normalized_name, parse_reference


class LocalTerritorialEngine:
    model = "local-territorial-symbolic-v1"
    usage_history = None

    def complete_json(self, *, system_prompt: str, payload: dict, image_url: str = "") -> dict:
        reference = parse_reference(payload["reference"])
        rows = evidence_rows(payload["evidence"])
        refs = {e.id: e for e in reference.entities}
        by_code = {e.code: e for e in reference.entities}
        levels = {r.id: r.level for r in reference.roles}
        names = {e.id: {normalized_name(n) for n in e.names} for e in reference.entities}
        by_name = {}
        for entity in reference.entities:
            for name in names[entity.id]:
                by_name.setdefault(name, []).append(entity)
        ancestors = {}
        for entity in reference.entities:
            chain = set()
            parent = entity.parent
            while parent:
                chain.add(parent)
                parent = refs[parent].parent
            ancestors[entity.id] = chain
        assigned = {entity.id: [] for entity in reference.entities}
        unresolved, excluded = [], []
        contexts = []
        row_owner = {}

        def matches(source, target):
            return normalized_name(source.name) in names[target.id] and (not target.qid or target.qid == source.data_wd)

        for row in rows:
            source = row.entity
            target = by_code.get(source.code)
            if target and matches(source, target):
                assigned[target.id].append(row)
                row_owner[row.id] = target.id
                continue
            context = source.code == reference.country and source.level == 0 and "infosection" in row.block
            if context:
                contexts.append(row)
                continue
            exclusions = [rule for rule in reference.exclusions if source.code in rule["source_codes"]
                and normalized_name(source.name) in {normalized_name(n) for n in rule["names"]}]
            if len(exclusions) == 1:
                excluded.append({"evidence_id": row.id, "reference_id": exclusions[0]["id"],
                    "reason": f"Exact source code {source.code} and name match reviewed exclusion {exclusions[0]['id']}."})
            else:
                unresolved.append({"id": row.id, "reason": f"No unique reviewed identity/exclusion for source code {source.code}, name {source.name!r}, QID {source.data_wd!r}."})

        # A country-slug fallback is a page context, not a shared source ID.
        # Its observed children constrain which reviewed ancestor it represents.
        children = {}
        for row in rows:
            if row.id in row_owner:
                children.setdefault((row.page, row.entity.parent_code), set()).add(row_owner[row.id])
        for row in contexts:
            candidates = [e for e in by_name.get(normalized_name(row.entity.name), [])
                if not e.repeat_of and matches(row.entity, e)]
            observed_children = children.get((row.page, row.entity.code), set())
            if observed_children:
                candidates = [e for e in candidates if all(e.id in ancestors[child] for child in observed_children)]
                if candidates:
                    deepest = max(levels[e.role] for e in candidates)
                    candidates = [e for e in candidates if levels[e.role] == deepest]
            if len(candidates) == 1:
                assigned[candidates[0].id].append(row)
            else:
                unresolved.append({"id": row.id, "reason": "Page context has no unique reviewed ancestor: "
                    + ", ".join(e.id for e in candidates)})

        nodes = []
        for entity in sorted(reference.entities, key=lambda e: (levels[e.role], e.id)):
            observations = assigned[entity.id]
            if not observations and entity.repeat_of:
                observations = [r for r in assigned[entity.repeat_of] if matches(r.entity, entity)]
                assigned[entity.id] = observations
            if not observations:
                unresolved.append({"id": entity.id, "reason": f"No observed source row for reviewed {entity.role} code {entity.code}; no entity will be invented."})
                continue

            def observation_rank(row):
                source = row.entity
                return (str(source.pop_latest_date or ""), source.last_census_year or 0,
                    source.code == entity.code, sum(getattr(source, f) is not None for f in (
                        "pop_latest", "area_km2", "density")))

            # Stable tie-breaking is independent of input page/row ordering.
            observations = sorted(observations, key=lambda r: (r.entity.url or "", r.id))
            observations.sort(key=observation_rank, reverse=True)
            citations = [entity.id, entity.role]
            citations.extend(rule["id"] for rule in reference.exceptions if entity.id in rule["entities"])
            nodes.append({"reference_id": entity.id, "evidence_ids": [r.id for r in observations],
                "role": entity.role, "level": levels[entity.role], "parent": entity.parent,
                "reference_ids": citations,
                "reason": f"Reviewed identity {entity.code}, verified alias/QID and reference ancestry establish role {entity.role} at L{levels[entity.role]}; "
                    f"metrics use observation {observations[0].id}."
                    + (f" Consecutive repetition is explicitly sourced from {entity.repeat_of}." if entity.repeat_of else "")})
        return {"schema_version": 1, "country": reference.country, "reference_version": reference.version,
            "evidence_version": payload["evidence_version"], "nodes": nodes, "excluded": excluded,
            "unresolved": unresolved, "discrepancies": []}

SAHARA_OCCIDENTAL = [
    {
        "name": "Sahara",
        "code": "SAH",
        "forced_area_km2": 266000,
        "capitals": ["Laâyoune"],
        "year_start": 1900,
        "year_end": 1978,
        "entity_type": "Provincia",
        "spec": {
            0: {
                "westernsahara": [
                    "Aousserd",
                    "Boujdour",
                    "Es Semara",
                    "Laâyoune",
                    "Oued Ed-Dahab",
                    "R.A.S.D.",
                    "Tarfaya",
                ]
            }
        }
    },
]

SAGUIA_EL_HAMRA = [
    {
        "name": "Saguía el Hamra",
        "code": "SEH",
        "forced_area_km2": 82000,
        "capitals": ["Laâyoune"],
        "year_start": 1900,
        "year_end": 1978,
        "entity_type": "Territorio",
        "spec": {1: {"westernsahara": ["Laâyoune", "Es Semara", "Tarfaya"]}, 2: {"westernsahara": ["Boujdour"]}}
    },
]

RIO_DE_ORO = [
    {
        "name": "Río de Oro",
        "code": "RDO",
        "forced_area_km2": 184000,
        "capitals": ["Dakhla"],
        "year_start": 1900,
        "year_end": 1978,
        "entity_type": "Territorio",
        "spec": {1: {"westernsahara": ["Boujdour", "Oued Ed-Dahab", "Aousserd"]}, "restar": {2: {"westernsahara": ["Boujdour"]}}}
    },
]

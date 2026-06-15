ZONA_TANGER = ["Tánger", "Laaouama", "Hjar Ennhal", "Gueznaia"]
FAHS_ANJRA_A_TANGER = ["Al Bahraoyine"]
TANGER_ASSILAH_A_YEBALA = ["Sebt Azzinate", "Dar Chaoui", "Al Manzla"]
TETOUAN_A_LOCUS = ["Bni Leit"]
ALHUCEMAS_A_KERT = ["Imzouren", "Bni Bouayach", "Nekkour"]

CABO_JUBY = {
    "name": "Cabo Juby",
    "code": "CJU",
    "entity_type": "Provincia",
    "capitals": ["Tarfaya"],
    "forced_area_km2": 32875,
    "spec": {
        2: {"morocco": ["Tan-Tan", "Assa-Zag"], "westernsahara": ["Tarfaya"]}
    },
}
IFNI = {
    "name": "Ifni",
    "code": "CJU",
    "entity_type": "Provincia",
    "capitals": ["Sidi Ifni"],
    "forced_area_km2": 1502,
    "spec": {
        3: {"morocco": ["Sidi Ifni", "Tioughza", "Tnine Amellou", "Mesti"]}
    },
}

YEBALA = {
    "name": "Yebala",
    "code": "EMA-YEB",
    "entity_type": "Región",
    "capitals": ["Tétouan"],
    "spec": {
        2: {"morocco": ["Fahs - Anjra", "M'Diq - Fnideq", "Tétouan"]},
        3: {"morocco": TANGER_ASSILAH_A_YEBALA},
        "restar": {3: {"morocco": FAHS_ANJRA_A_TANGER + TETOUAN_A_LOCUS}}
    }
}

LOCUS = {
    "name": "Locus",
    "code": "EMA-LOC",
    "entity_type": "Región",
    "capitals": ["Larache"],
    "spec": {
        2: {"morocco": ["Tanger - Assilah", "Larache"]},
        3: {"morocco": TETOUAN_A_LOCUS},
        "restar": {3: {"morocco": ZONA_TANGER + TANGER_ASSILAH_A_YEBALA}}
    }
}

CHAUEN = {
    "name": "Chauen",
    "code": "EMA-CHA",
    "entity_type": "Región",
    "capitals": ["Chefchaouen"],
    "spec": {
        2:{"morocco": "Chefchaouen"}
    }
}

RIF = {
    "name": "Rif",
    "code": "EMA-RIF",
    "entity_type": "Región",
    "capitals": ["Al Hoceïma"],
    "spec": {
        2:{"morocco": "Al Hoceïma"},
        "restar": {3: {"morocco": ALHUCEMAS_A_KERT}}
    }
}

KERT = {
    "name": "Kert",
    "code": "EMA-KER",
    "entity_type": "Región",
    "capitals": ["Nador"],
    "spec": {
        2: {"morocco": ["Driouch", "Nador"]},
        3: {"morocco": ALHUCEMAS_A_KERT}
    }
}

TANGER = {
    "name": "Tánger",
    "code": "EMA-TAN",
    "entity_type": "Zona",
    "capitals": ["Tánger"],
    "spec": {
        3:{"morocco": ZONA_TANGER + FAHS_ANJRA_A_TANGER}
    }
}
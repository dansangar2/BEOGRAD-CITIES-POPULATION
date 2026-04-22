from ciudades_del_mundo.models import NuevoAdminArea

ZONA_TANGER = ["Tánger", "Laaouama", "Hjar Ennhal", "Gueznaia"]
FAHS_ANJRA_A_TANGER = ["Al Bahraoyine"]
TANGER_ASSILAH_A_YEBALA = ["Sebt Azzinate", "Dar Chaoui", "Al Manzla"]
TETOUAN_A_LOCUS = ["Bni Leit"]
ALHUCEMAS_A_KERT = ["Imzouren", "Bni Bouayach", "Nekkour"]


DIVISIONS = [
        {
            "name": "Marruecos Español",
            "code": "EMA",
            "entity_type": "Protectorado",
            "capitals": ["Tétouan"],
            "childs": [
                {
                    "name": "Yebala",
                    "code": "EMA-YEB",
                    "entity_type": "Región",
                    "capitals": ["Tétouan"],
                    "spec": {
                        2: {"morocco": ["Fahs - Anjra", "M'Diq - Fnideq", "Tétouan"]},
                        3: {"morocco": TANGER_ASSILAH_A_YEBALA},
                        "restar": {3: {"morocco": FAHS_ANJRA_A_TANGER + TETOUAN_A_LOCUS}}
                    }
                },
                {
                    "name": "Locus",
                    "code": "EMA-LOC",
                    "entity_type": "Región",
                    "capitals": ["Larache"],
                    "spec": {
                        2: {"morocco": ["Tanger - Assilah", "Larache"]},
                        3: {"morocco": TETOUAN_A_LOCUS},
                        "restar": {3: {"morocco": ZONA_TANGER + TANGER_ASSILAH_A_YEBALA}}
                    }
                },
                {
                    "name": "Chauen",
                    "code": "EMA-CHA",
                    "entity_type": "Región",
                    "capitals": ["Chefchaouen"],
                    "spec": {
                        2:{"morocco": "Chefchaouen"}
                    }
                },
                {
                    "name": "Rif",
                    "code": "EMA-RIF",
                    "entity_type": "Región",
                    "capitals": ["Al Hoceïma"],
                    "spec": {
                        2:{"morocco": "Al Hoceïma"},
                        "restar": {3: {"morocco": ALHUCEMAS_A_KERT}}
                    }
                },
                {
                    "name": "Kert",
                    "code": "EMA-KER",
                    "entity_type": "Región",
                    "capitals": ["Nador"],
                    "spec": {
                        2: {"morocco": ["Driouch", "Nador"]},
                        3: {"morocco": ALHUCEMAS_A_KERT}
                    }
                }, 
                {
                    "name": "Tánger",
                    "code": "EMA-TAN",
                    "entity_type": "Zona",
                    "capitals": ["Tánger"],
                    "spec": {
                        3:{"morocco": ZONA_TANGER + FAHS_ANJRA_A_TANGER}
                    }
                }, 
            ]
        },
    ]

ESCANHOS = {
        "nivel": NuevoAdminArea.Level.ADMIN2,  # ejemplo: nivel de las provincias
        "escanhos": 100,
        "min": 1,
    },
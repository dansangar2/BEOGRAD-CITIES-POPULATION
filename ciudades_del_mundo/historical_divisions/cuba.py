A_CUBA_CENTRAL = ["Villa Clara", "Cienfuegos", "Sancti Spíritus"]
SANCTI_SPIRITUS_A_ORIENTAL = LAS_VILLAS_A_PUERTO_PRINCIPE = ["Jatibonico"]

A_CUBA_ORIENTAL = ["Guantánamo", "Santiago de Cuba", "Holguín", "Granma", "Las Tunas"]
LAS_TUNAS_A_OCCIDENTAL = LAS_TUNAS_A_CENTRO = LAS_TUNAS_A_PUERTO_PRINCIPE = ["Colombia", "Amancio"]

A_CUBA_OCCIDENTAL = ["Pinar del Río", "Artemisa", "Ciudad de la Habana", "Mayabeque", "Isla de la Juventud", "Matanzas"]
MATANZAS_A_CENTRAL = MATANZAS_A_SANTA_CLARA = ["Ciénaga de Zapata"]


A_PINAR_DEL_RIO = ["Pinar del Río", "Artemisa"]
A_LA_HABANA = ["Ciudad de la Habana", "Mayabeque", "Isla de la Juventud"]
A_MATANZAS = ["Matanzas"]
A_SANTA_CLARA = ["Villa Clara", "Cienfuegos", "Sancti Spíritus"]
A_PUERTO_PRINCIPE = ["Ciego de Ávila", "Camagüey"]
A_SANTIAGO_DE_CUBA = ["Las Tunas", "Holguín", "Granma", "Santiago de Cuba", "Guantánamo"]
ARTEMISA_A_LA_HABANA = ["Caimito", "Alquízar", "Bauta", "an Antonio de los Baños", "Güira de Melena"]



DIVISIONS_XV = [
    {
        "name": "Cuba",
        "code": "CUB",
        "capitals": ["Santiago de Cuba"],
        "year_start": 1510,
        "year_end": 1606,
        "entity_type": "Provincia",
        "spec": {0: {"cuba": "Cuba"}}
    },
]

DIVISIONS_XVII = [
    {
        "name": "Occidente",
        "code": "OCC",
        "capitals": ["La Habana"],
        "year_start": 1607,
        "year_end": 1773,
        "entity_type": "Provincia",
        "spec": {
            1: {"cuba": A_CUBA_OCCIDENTAL + A_CUBA_CENTRAL},
            "restar": {2: {"cuba": SANCTI_SPIRITUS_A_ORIENTAL}}
        }
    },
    {
        "name": "Oriente",
        "code": "ORI",
        "capitals": ["Santiago de Cuba"],
        "year_start": 1607,
        "year_end": 1773,
        "entity_type": "Provincia",
        "spec": {
            0: {"cuba": "Cuba"},
            2: {"cuba": SANCTI_SPIRITUS_A_ORIENTAL},
            "restar": {1: {"cuba": A_CUBA_OCCIDENTAL + A_CUBA_CENTRAL}}
        }
    },
]

DIVISIONS_XVIII = [
    {
        "name": "Occidente",
        "code": "OCC",
        "capitals": ["La Habana"],
        "year_start": [1774, 1853],
        "year_end": [1826, 1877],
        "entity_type": "Provincia",
        "spec": {
            0: {"cuba": "Cuba"},
            2: {"cuba": LAS_TUNAS_A_OCCIDENTAL},
            "restar": {1: {"cuba": A_CUBA_ORIENTAL}}
        }
    },
    {
        "name": "Oriente",
        "code": "ORI",
        "capitals": ["Santiago de Cuba"],
        "year_start": [1774, 1853],
        "year_end": [1826, 1877],
        "entity_type": "Provincia",
        "spec": {
            1: {"cuba": A_CUBA_ORIENTAL},
            "restar": {2: {"cuba": LAS_TUNAS_A_OCCIDENTAL}}
        }
    },
]
DIVISIONS_XVIII_2 = [
    {
        "name": "Occidente",
        "code": "OCC",
        "capitals": ["La Habana"],
        "year_start": 1850,
        "year_end": 1852,
        "entity_type": "Provincia",
        "spec": {
            1: {"cuba": A_CUBA_OCCIDENTAL},
            "restar": {1: {"cuba": MATANZAS_A_CENTRAL}}
        }
    },
    {
        "name": "Central",
        "code": "CEN",
        "capitals": ["Camagüey"],
        "year_start": 1850,
        "year_end": 1852,
        "entity_type": "Provincia",
        "spec": {
            0: {"cuba": "Cuba"},
            2: {"cuba": MATANZAS_A_CENTRAL + LAS_TUNAS_A_CENTRO},
            "restar": {2: {"cuba": A_CUBA_OCCIDENTAL + A_CUBA_ORIENTAL}}
        }
    },
    {
        "name": "Oriente",
        "code": "ORI",
        "capitals": ["Santiago de Cuba"],
        "year_start": 1850,
        "year_end": 1852,
        "entity_type": "Provincia",
        "spec": {
            1: {"cuba": A_CUBA_ORIENTAL},
            "restar": {2: {"cuba": LAS_TUNAS_A_CENTRO}}
        }
    },
]
DIVISIONS_XIX = [
    {
        "name": "Pinar del Río",
        "code": "PIR",
        "capitals": ["Pinar del Río"],
        "year_start": 1878,
        "entity_type": "Provincia",
        "spec": {
            1: {"cuba": A_PINAR_DEL_RIO},
            "restar": {2: {"cuba": ARTEMISA_A_LA_HABANA}}
        }
    },
    {
        "name": "La Habana",
        "code": "HAB",
        "capitals": ["La Habana"],
        "year_start": 1878,
        "entity_type": "Provincia",
        "spec": {
            1: {"cuba": A_LA_HABANA},
            2: {"cuba": ARTEMISA_A_LA_HABANA},
        }
    },
    {
        "name": "Matanzas",
        "code": "MAT",
        "capitals": ["Matanzas"],
        "year_start": 1878,
        "entity_type": "Provincia",
        "spec": {
            1: {"cuba": A_MATANZAS},
            "restar": {2: {"cuba": MATANZAS_A_SANTA_CLARA}}
        }
    },
    {
        "name": "Santa Clara",
        "code": "SCL",
        "capitals": ["Santa Clara"],
        "year_start": 1878,
        "entity_type": "Provincia",
        "spec": {
            1: {"cuba": A_SANTA_CLARA},
            2: {"cuba": MATANZAS_A_SANTA_CLARA},
            "restar": {2: {"cuba": LAS_VILLAS_A_PUERTO_PRINCIPE}}
        }
    },
    {
        "name": "Puerto Príncipe",
        "code": "PPR",
        "capitals": ["Camagüey"],
        "year_start": 1878,
        "entity_type": "Provincia",
        "spec": {
            1: {"cuba": A_PUERTO_PRINCIPE},
            2: {"cuba": LAS_TUNAS_A_PUERTO_PRINCIPE + LAS_VILLAS_A_PUERTO_PRINCIPE},
        }
    },
    {
        "name": "Santiago de Cuba",
        "code": "SCU",
        "capitals": ["Santiago de Cuba"],
        "year_start": 1878,
        "entity_type": "Provincia",
        "spec": {
            1: {"cuba": A_SANTIAGO_DE_CUBA},
            "restar": {2: {"cuba": LAS_TUNAS_A_PUERTO_PRINCIPE}}
        }
    },
]

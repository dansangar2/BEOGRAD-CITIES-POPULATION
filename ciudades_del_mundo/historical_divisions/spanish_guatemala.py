from ciudades_del_mundo.historical_divisions.spanish_mexico import CHIAPAS_A_GUATEMALA, CHIAPAS_A_EXCLUIR_A_GUATEMALA, CAMPECHE_A_GUATEMALA

GUATEMALA_A_CHIAPAS = ["La Blanca", "Ocós", "Ayutla"]
BELIZE_A_GUATEMALA = ["Stann Creek", "Cayo", "Toledo"]

EL_SALVADOR_A_GUATEMALA = ["Sonsonate", "Ahuachapán"]

COSTA_RICA_DISTR_A_NICARAGUA = ["Cóbano", "Paquera", "Lepanto", "Chira", "Los Chiles", "Caño Negro", "Isla del Coco"]
COSTA_RICA_A_NICARAGUA = ["Nandayure", "Hojancha", "Nicoya", "Santa Cruz", "Carrillo", "Liberia", "La Cruz", "Upala"]
COSTA_RICA_EXCLUIR_DISTR_A_NICARAGUA = ["Bijagua de Upala"]

COSTA_RICA_DISTR_A_VERAGUAS = ["Pavón", "Laurel", "La Cuesta", "Canoas", "Corredor", "Aguabuena",
                               "Sabalito", "Pittier", "San Vito", "Guaycará", "Cahuita", "Sixaola", "Bratsi"]

GUATEMALA = {
    "name": "Guatemala",
    "code": "GAT",
    "entity_type": "Provincia",
    "capitals": ["Guatemala"],
    "spec": {
        0: {"guatemala": ["Guatemala"]},
        1: {"elsalvador": EL_SALVADOR_A_GUATEMALA, "belize": BELIZE_A_GUATEMALA},
        2: {"mexico": CAMPECHE_A_GUATEMALA + CHIAPAS_A_GUATEMALA },
        "restar": {
            2: {"guatemala": GUATEMALA_A_CHIAPAS},
            3: {"mexico": CHIAPAS_A_EXCLUIR_A_GUATEMALA}
        },
    }
}

SAN_SALVADOR = {
    "name": "San Salvador",
    "code": "ESA",
    "entity_type": "Provincia",
    "capitals": ["San Salvador"],
    "spec": {
        0: {"elsalvador": ["El Salvador"]},
        "restar": {
            1: {"elsalvador": EL_SALVADOR_A_GUATEMALA},
        },
    }
}

COMAYAGUA = {
    "name": "Comayagua",
    "code": "COM",
    "entity_type": "Provincia",
    "capitals": ["Comayagua"],
    "spec": {
        0: {"honduras": ["Honduras"]},
    }
}

NICARAGUA = {
    "name": "Nicaragua",
    "code": "NIC",
    "entity_type": "Provincia",
    "capitals": ["León"],
    "spec": {
        0: {"nicaragua": ["Nicaragua"]},
        2: {"costarica": COSTA_RICA_A_NICARAGUA},
        3: {"costarica": COSTA_RICA_DISTR_A_NICARAGUA},
        "restar": {
            3: {"costarica": COSTA_RICA_EXCLUIR_DISTR_A_NICARAGUA},
        },
    }
}

COSTA_RICA = {
    "name": "Costa Rica",
    "code": "CRI",
    "entity_type": "Provincia",
    "capitals": ["Cartago"],
    "spec": {
        0: {"costarica": ["Costa Rica"]},
        3: {"costarica": COSTA_RICA_EXCLUIR_DISTR_A_NICARAGUA},
        "restar": {
            2: {"costarica": COSTA_RICA_A_NICARAGUA},
            3: {"costarica": COSTA_RICA_DISTR_A_NICARAGUA + COSTA_RICA_DISTR_A_VERAGUAS},
        },
    }
}

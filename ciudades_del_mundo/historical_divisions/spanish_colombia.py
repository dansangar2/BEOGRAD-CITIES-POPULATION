from ciudades_del_mundo.historical_divisions.spanish_guatemala import COSTA_RICA_DISTR_A_VERAGUAS

A_VERAGUAS = ["Bocas del Toro", "Chiriquí", "Ngäbe-Buglé", "Veraguas"]
VERAGUAS_DIS_A_PANAMA = ["Calobre"]
VERAGUAS_MUN_A_EXCLUIR_PANAMA = ["Chitra"]
VERAGUAS_MUN_A_PANAMA = ["Los Canelos", "Chupampa", "Los Llanos"]

A_PANAMA = ["Colón", "Los Santos", "Herrera", "Coclé", "Panamá Oeste", "Panamá"]

COLON_DIS_A_PORTOBELO = ["Santa Isabel", "Portobelo"]
COLON_MUN_A_PORTOBELO = ["Salamanca", "María Chiquita", "Puerto Pilón", "Sabanitas", "Nueva Providencia", "Limón"]
COLON_MUN_EXC_A_DARIEN = ["Santa Isabel", "Palmira"]

PANAMA_MUN_A_DARIEN = ["Cañita", "Madungandí", "Brujas", "Gonzalo Vásquez"]

A_DARIEN = ["Guna Yala", "Darién", "Emberá-Wounaan"]


#Antioquia
#Boyacá
#Caldas
#Cauca
#Chocó
#Cundinamarca
#Huila
#Norte de Santander
#Risaralda
#Santander
#Tolima

BOLIVAR_A_ANTIOQUIA = ["Cantagallo"]
RIOHACHA_A_SANTA_MARTA = ["Dibulla", "San Juan del Cesar", "Villanueva", "La Jagua del Pilar", "Urumita", "El Molino",
                          "Fonseca", "Distracción", "Barrancas"]
META_A_POPAYAN = ["La Macarena"]

CARTAGENA = {
    "name": "Cartagena",
    "code": "CAR",
    "entity_type": "Provincia",
    "capitals": ["Cartagena"],
    "spec": {
        1: {"colombia": ["Bolívar", "Sucre", "Córdoba", "Atlántico", "San Andrés y Providencia"]},
        2: {"colombia": COSTA_RICA_DISTR_A_VERAGUAS, "panama": VERAGUAS_MUN_A_EXCLUIR_PANAMA},
        "restar": {
            2: {"colombia": BOLIVAR_A_ANTIOQUIA},
        },
    }
}

SANTA_MARTA = {
    "name": "Santa Marta",
    "code": "SMA",
    "entity_type": "Provincia",
    "capitals": ["Santa Marta"],
    "spec": {
        1: {"colombia": ["Magdalena", "Cesar"]},
        2: {"colombia": RIOHACHA_A_SANTA_MARTA}
    }
}

RIOHACHA = {
    "name": "Riohacha",
    "code": "RIO",
    "entity_type": "Provincia",
    "capitals": ["Riohacha"],
    "spec": {
        1: {"colombia": ["La Guajira"]},
        "restar": {
            2 : {"colombia": RIOHACHA_A_SANTA_MARTA}
        }
    }
}

SANTAFE = {
    "name": "Santafe",
    "code": "STF",
    "entity_type": "Provincia",
    "capitals": ["Bogotá"],
    "spec": {
        1: {"colombia": ["Bogotá, D.C.", "Meta", "Vichada"]},
        "restar": {
            2 : {"colombia": META_A_POPAYAN}
        }
    }
}

CASANARE = {
    "name": "Casanare",
    "code": "CAS",
    "entity_type": "Provincia",
    "capitals": ["Casanare"],
    "spec": {
        1: {"colombia": ["Casanare", "Arauca"]},
    }
}

POPAYAN = {
    "name": "Popayán",
    "code": "POP",
    "entity_type": "Provincia",
    "capitals": ["Popayán"],
    "spec": {
        1: {"colombia": ["Guainía", "Amazonas", "Putumayo", "Guaviare", "Caquetá", "Vaupés", "Nariño", "Valle del Cauca",
                         "Quindío"]},
        2 : {"colombia": META_A_POPAYAN}
    }
}

#ANTIOQUIA = {
#    "name": "Cartagena",
#    "code": "VER",
#    "entity_type": "Provincia",
#    "capitals": ["Cartagena"],
#    "spec": {
#        1: {"colombia": []},
#        2: {"colombia": BOLIVAR_A_ANTIOQUIA},
#    }
#}


VERAGUAS = {
    "name": "Veraguas",
    "code": "VER",
    "entity_type": "Provincia",
    "capitals": ["Santiago"],
    "spec": {
        1: {"panama": A_VERAGUAS},
        3: {"costarica": COSTA_RICA_DISTR_A_VERAGUAS, "panama": VERAGUAS_MUN_A_EXCLUIR_PANAMA},
        "restar": {
            2: {"panama": VERAGUAS_DIS_A_PANAMA},
            3: {"panama": VERAGUAS_MUN_A_PANAMA},
        },
    }
}

PANAMA = {
    "name": "Panamá",
    "code": "PAN",
    "entity_type": "Provincia",
    "capitals": ["Panamá"],
    "spec": {
        1: {"panama": A_PANAMA},
        2: {"panama": VERAGUAS_DIS_A_PANAMA},
        3: {"panama": VERAGUAS_MUN_A_PANAMA},
        "restar": {
            2: {"panama": COLON_DIS_A_PORTOBELO},
            3: {"panama": VERAGUAS_MUN_A_EXCLUIR_PANAMA + COLON_MUN_A_PORTOBELO + PANAMA_MUN_A_DARIEN },
        },
    }
}

PORTOBELO = {
    "name": "Portobelo",
    "code": "POR",
    "entity_type": "Provincia",
    "capitals": ["Portobelo"],
    "spec": {
        2: {"panama": COLON_DIS_A_PORTOBELO},
        3: {"panama": COLON_MUN_A_PORTOBELO},
        "restar": {
            3: {"panama": COLON_MUN_EXC_A_DARIEN},
        },
    }
}

DARIEN = {
    "name": "Darién",
    "code": "DAR",
    "entity_type": "Provincia",
    "capitals": ["La Palma"],
    "spec": {
        1: {"panama": A_DARIEN},
        #2: {"panama": },
        3: {"panama": COLON_MUN_EXC_A_DARIEN + PANAMA_MUN_A_DARIEN},
    }
}

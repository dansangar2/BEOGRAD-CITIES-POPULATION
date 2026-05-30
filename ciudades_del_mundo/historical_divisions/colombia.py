from ciudades_del_mundo.historical_divisions.centroamerica import COSTA_RICA_DISTR_A_VERAGUAS

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


#Amazonas
#Antioquia
#Arauca
#Atlántico
#Bogotá, Distrito Capital
#Bolívar
#Boyacá
#Caldas
#Caquetá
#Casanare
#Cauca
#Cesar
#Chocó
#Córdoba
#Cundinamarca
#Guainía
#Guaviare
#Huila
#La Guajira
#Magdalena
#Meta
#Nariño
#Norte de Santander
#Putumayo
#Quindío
#Risaralda
#San Andrés y Providencia
#Santander
#Sucre
#Tolima
#Valle del Cauca
#Vaupés
#Vichada


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

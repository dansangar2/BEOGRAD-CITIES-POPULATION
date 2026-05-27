from ciudades_del_mundo.historical_divisions.eeuu import NUEVO_MEXICO_A_NUEVA_VIZCAYA, ARIZONA_A_NUEVA_NAVARRA, TEXAS_A_NUEVA_EXTREMADURA, TEXAS_A_NUEVO_SANTANDER, TEXAS_A_NUEVA_VIZCAYA

#Chiapas
#Guerrero
#Michoacán de Ocampo
#Morelos
#Puebla
#Tlaxcala

COAHUILA_A_NUEVA_VIZCAYA = ["Ocampo", "Acuña", "Sierra Mojada"]
COAHUILA_EXCLUYE_MUN_A_NUEVA_VIZCAYA = ["Ciudad Acuña"]

TABASCO_A_VERACRUZ = ["Cárdenas", "Huimanguillo"]
CAMPECHE_A_GUATEMALA = ["Calakmul", "Escárcega", "Candelaria"]
CAMPECHE_A_CARMEN = ["Carmen", "Palizada"]
QUERETARO_A_GUANAJUATO = ["Peñamiller"]

VIEJA_CALIFORNIA = {
            "name": "Vieja California",
            "code": "VCA",
            "entity_type": "Provincia",
            "capitals": ["Loreto"],
            "depends_of": "NES-NCA",
            "spec": {
                1: {"mexico": ["Baja California", "Baja California Sur"]}
            }
}

NUEVO_LEON = {
            "name": "Nuevo León",
            "code": "NLE",
            "entity_type": "Provincia",
            "capitals": ["Monterrey"],
            "spec": {
                1: {"mexico": ["Nuevo León"]}
            }
}

NUEVA_NAVARRA = {
            "name": "Nueva Navarra",
            "code": "NNA",
            "entity_type": "Provincia",
            "capitals": ["Arizpe"],
            "spec": {
                1: {"mexico": ["Sinaloa", "Sonora"]},
                2: {"usa": ARIZONA_A_NUEVA_NAVARRA}
            }
}

NUEVA_VIZCAYA = {
            "name": "Nueva Vizcaya",
            "code": "NVI",
            "entity_type": "Provincia",
            "capitals": ["Durango"],
            "spec": {
                1: {"mexico": ["Durango", "Chihuahua"]},
                2: {"usa": TEXAS_A_NUEVA_VIZCAYA + NUEVO_MEXICO_A_NUEVA_VIZCAYA, "mexico": COAHUILA_A_NUEVA_VIZCAYA},
                "restar": {3: {"mexico": COAHUILA_EXCLUYE_MUN_A_NUEVA_VIZCAYA}}
            }
}


NUEVA_EXTREMADURA = {
            "name": "Nueva Extremadura",
            "code": "NEX",
            "entity_type": "Provincia",
            "capitals": ["Saltillo"],
            "spec": {
                1: {"mexico": ["Coahuila de Zaragoza"]},
                2: {"usa": TEXAS_A_NUEVA_EXTREMADURA},
                3: {"mexico": COAHUILA_EXCLUYE_MUN_A_NUEVA_VIZCAYA},
                "restar": {2: {"mexico": COAHUILA_A_NUEVA_VIZCAYA}}
            }
}

NUEVO_SANTANDER = {
            "name": "Nuevo Santander",
            "code": "NSA",
            "entity_type": "Provincia",
            "capitals": ["Santander"],
            "spec": {
                1: {"mexico": ["Tamaulipas"]},
                2: {"usa": TEXAS_A_NUEVO_SANTANDER}
            }
}

OAXACA = {
            "name": "Oaxaca",
            "code": "OAX",
            "entity_type": "Provincia",
            "capitals": ["Oaxaca de Juárez"],
            "spec": {
                1: {"mexico": ["Oaxaca"]}
            }
}

ZACATECAS = {
            "name": "Zacatecas",
            "code": "ZAC",
            "entity_type": "Provincia",
            "capitals": ["Zacatecas"],
            "spec": {
                1: {"mexico": ["Zacatecas", "Aguascalientes"]}
            }
}

SAN_LUIS_POTOSI = {
            "name": "San Luis Potosí",
            "code": "SLP",
            "entity_type": "Provincia",
            "capitals": ["San Luis Potosí"],
            "spec": {
                1: {"mexico": ["San Luis Potosí"]}
            }
}

GUANAJUATO = {
            "name": "Guanajuato",
            "code": "GUA",
            "entity_type": "Provincia",
            "capitals": ["Guanajuato"],
            "spec": {
                1: {"mexico": ["Guanajuato"]},
                #2: {"mexico": QUERETARO_A_GUANAJUATO}
            }
}

GUADALAJARA_DE_JALISCO = {
            "name": "Guadalajara de Jalisco",
            "code": "GJA",
            "entity_type": "Provincia",
            "capitals": ["Guadalajara"],
            "spec": {
                1: {"mexico": ["Jalisco", "Colima", "Nayarit"]}
            }
}

TABASCO = {
            "name": "Tabasco",
            "code": "TAB",
            "entity_type": "Provincia",
            "capitals": ["Villahermosa"],
            "spec": {
                1: {"mexico": ["Tabasco"]},
                "restar": {2: {"mexico": TABASCO_A_VERACRUZ}},
            }
}

CARMEN = {
            "name": "Carmen",
            "code": "CAR",
            "entity_type": "Provincia",
            "capitals": ["Carmen"],
            "spec": {
                2: {"mexico": CAMPECHE_A_CARMEN},
            }
}

MERIDA_DE_YUCATAN = {
            "name": "Mérida de Yucatán",
            "code": "MYU",
            "entity_type": "Provincia",
            "capitals": ["Mérida"],
            "spec": {
                1: {"mexico": ["Quintana Roo", "Yucatán", "Campeche"]},
                "restar": {2: {"mexico": CAMPECHE_A_GUATEMALA + CAMPECHE_A_CARMEN}},
            }
}

MEXICO = {
            "name": "México",
            "code": "MEX",
            "entity_type": "Provincia",
            "capitals": ["Ciudad de México"],
            "spec": {
                1: {"mexico": ["México", "Ciudad de México", "Hidalgo", "Querétaro de Arteaga"]},
                #2: {"mexico":}
                #"restar": {2: {"mexico": QUERETARO_A_GUANAJUATO}},
            }
}

VERACRUZ = {
            "name": "Veracruz",
            "code": "VER",
            "entity_type": "Provincia",
            "capitals": ["Veracruz"],
            "spec": {
                1: {"mexico": [""]},
                2: {"mexico": TABASCO_A_VERACRUZ}
            }
}
from ciudades_del_mundo.historical_divisions.france import ARTOIS, FLANDES_Y_HENAO, FLANDES_FRANCES, HENAO_FRANCES, FLANDES_FRANCES_MUN, FLANDES_FRANCES_MUN_RES, HENAO_FRANCES_MUN, HENAO_FRANCES_MUN_RES, HENAO_A_FLANDES_PB, HENAO_A_HENAO_PB, LUXEMBURGO_FRANCES

	#Condado de Holanda	Territorio integrado en las Provincias Unidas en 1581.
	#Condado de Zelanda	Vinculado al Condado de Holanda. Territorio integrado en las Provincias Unidas en 1581.
	#Condado de Artois	Cedido definitivamente a Francia en 1659 por el Tratado de los Pirineos. Salvo Aire y Saint-Omer, cedidas Tratados de Nimega.
	#Condado de Namur	
	#Condado de Zutphen	Vinculado al Ducado de Güeldres. Territorio integrado en las Provincias Unidas en 1581, y reintegrado en 1591.
	#Ducado de Brabante	Parte del territorio pasó a las Provincias Unidas.
	#Ducado de Luxemburgo	
	#Ducado de Limburgo	Vinculado al ducado de Brabante.
	#Ducado de Güeldres	Territorio integrado en las Provincias Unidas en 1581; excepto una parte.
	#Señorío de Overijssel	En latín, Transisulania. Incluía Drente (mapa de 1658). Territorio integrado en las Provincias Unidas en 1591.[15]​
	#Señorío de Groninga	Territorio integrado completamente en las Provincias Unidas en 1594.[16]​
	#Señorío de Frisia	Territorio integrado en las Provincias Unidas en 1581.
	#Señorío de Utrecht	Territorio integrado en las Provincias Unidas en 1581.
	#Señorío de Malinas	Vinculado al Ducado de Brabante. Territorio de las Provincias Unidas entre 1581-1585[17]​
	#Marquesado de Amberes	Vinculado al ducado de Brabante. Perdida por las Provincias Unidas en 1585[17]​

SPEC_CAMBRAI = FLANDES_Y_HENAO["childs"][2]["spec"]
SPEC_ARTOIS = ARTOIS["childs"][0]

FRISIA_A_HOLANDA = ["Terschelling", "Vlieland"]
HOLANDA_SUR_A_ZELANDA = ["Goeree-Overflakkee"]
ZELANDA_A_FLANDES = ["Sluis", "Terneuzen", "Hulst"]

HOLANDA_NO = ["Hollands Kroon"]
UTRETCH_A_GUELDRES = ["Veenendaal"]
UTRETCH_A_HOLANDA = ["Vijfheerenlanden", "Lopik", "Oudewater"]
HOLANDA_A_UTRETCH = ["Wijdemeren"]

TOURNAI_MOUSCRON_A_FLANDES = ["Comines-Warneto", "Mouscron", "Estaimpuis", "Tournai", "Rumes",
                              "Brunehaut", "Antoing", "Pecq"]

ARTOIS = {
    "name": SPEC_ARTOIS["name"],
    "code": SPEC_ARTOIS["code"],
    "entity_type": SPEC_ARTOIS["entity_type"],
    "capitals": SPEC_ARTOIS["capitals"],
    "spec": SPEC_ARTOIS["spec"],
}

FLANDES = {
    "name": "Flandes",
    "code": "FLA",
    "entity_type": "Condado",
    "capitals": ["Lille"],
    "spec": {
        1: {"belgium": ["Oost-Vlaanderen", "West-Vlaanderen"]},
        3: {"belgium": TOURNAI_MOUSCRON_A_FLANDES, "netherlands" : ZELANDA_A_FLANDES, "france": FLANDES_FRANCES },
        4: {"france": FLANDES_FRANCES_MUN + HENAO_A_FLANDES_PB},
        "restar": {4: {"france": FLANDES_FRANCES_MUN_RES}},
    }
}
 
UTRIQUE = {
    "name": "Utrique",
    "code": "UTR",
    "entity_type": "Señorío",
    "capitals": ["Utrecht"],
    "spec": {
        2: {"netherlands" : "Utrecht"},
        3: {"netherlands" : UTRETCH_A_HOLANDA + UTRETCH_A_GUELDRES },
        "restar": {3: {"netherlands" : HOLANDA_A_UTRETCH },}
    }
}

HENAO = {
    "name": "Henao",
    "code": "HEN",
    "entity_type": "Condado",
    "capitals": ["Valenciennes"],
    "spec": {
        3: {"france": HENAO_FRANCES},
        4: {"france": HENAO_FRANCES_MUN + HENAO_A_HENAO_PB},
        "restar": {
            3: {"belgium": TOURNAI_MOUSCRON_A_FLANDES},
            4: {"france": HENAO_FRANCES_MUN_RES}
        },
    }
}

CAMBRAI = {
    "name": "Cambrai",
    "code": "CAM",
    "entity_type": "Condado",
    "capitals": ["Cambrai"],
    "spec": SPEC_CAMBRAI
}

DRENTE = {
    "name": "Drente",
    "code": "DRE",
    "entity_type": "Condado",
    "capitals": ["Assen"],
    "spec": {2: {"netherlands" : "Drenthe"}}
}

GRONINGA = {
    "name": "Groninga",
    "code": "GRO",
    "entity_type": "Señorío",
    "capitals": ["Groningen"],
    "spec": {2: {"netherlands" : "Groningen"}}
}

FRISIA = {
    "name": "Frisia",
    "code": "FRI",
    "entity_type": "Señorío",
    "capitals": ["Leeuwarden"],
    "spec": {
        2: {"netherlands" : "Friesland"},
        "restar": {3: {"netherlands" : FRISIA_A_HOLANDA}}
    }
}
 
HOLANDA = {
    "name": "Holanda",
    "code": "HOL",
    "entity_type": "Condado",
    "capitals": ["'s-Gravenhage"],
    "spec": {
        2: {"netherlands" : ["Noord-Holland", "Zuid-Holland"]},
        3: {"netherlands" : UTRETCH_A_HOLANDA + FRISIA_A_HOLANDA + ["Urk"]},
        "restar": {3: {"netherlands" : HOLANDA_A_UTRETCH + HOLANDA_NO + HOLANDA_SUR_A_ZELANDA}},
    }
}

ZELANDA = {
    "name": "Zelanda",
    "code": "ZEL",
    "entity_type": "Condado",
    "capitals": ["Middelburg"],
    "spec": {
        2: {"netherlands" : ["Zeeland"]},
        3: {"netherlands" : HOLANDA_SUR_A_ZELANDA},
        "restar": {3: {"netherlands" : ZELANDA_A_FLANDES}},
    }
}
LUXEMBURGO = {
    "name": "Luxemburgo",
    "code": "LUX",
    "entity_type": "Condado",
    "capitals": ["Luxembourg"],
    "spec": {
        0: {"luxembourg": "luxembourg"},
        4: {"france": LUXEMBURGO_FRANCES},
    }
}
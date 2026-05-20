from copy import deepcopy

from ciudades_del_mundo.new_subdivisions.austria_empire import DIVISIONS as DIVA
from ciudades_del_mundo.historical_divisions.espanha import *
from ciudades_del_mundo.historical_divisions.italy import *
from ciudades_del_mundo.historical_divisions.portugal import *

def create_join() -> dict:
    result = deepcopy(DIVA)
    spain = [
        {
            "name": "Castilla",
            "code": "CAS",
            "entity_type": "Corona",
            "capitals": ["Madrid"],
            "childs": [
                CASTILLA,
                NAVARRA_A,
            ]
        },
        {
            "name": "Aragón",
            "code": "ARA",
            "entity_type": "Corona",
            "capitals": ["Zaragoza", "Barcelona"],
            "childs": [
                ARAGON, 
                CATALUNHA_A,
                VALENCIA_A, 
                MALLORCA,
                CERDENHA,
                SICILIA,
            ]
        },
        {
            "name": "Portugal",
            "code": "POR",
            "entity_type": "Reino",
            "capitals": ["Lisboa"],
            "childs": [
                PORTUGAL
            ]
        }
    ]
    result.extend(spain)
    return result

DIVISIONS = create_join()

ESCANHOS = {
        "nivel": 2,  # provincias del imperio
        "escanhos": 200,                        # total de escaños a repartir
        "min": 1,                              # mínimo por provincia
    }

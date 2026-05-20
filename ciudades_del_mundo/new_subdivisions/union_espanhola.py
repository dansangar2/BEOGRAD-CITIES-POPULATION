from ciudades_del_mundo.historical_divisions.espanha import *
from ciudades_del_mundo.historical_divisions.italy import *
from ciudades_del_mundo.historical_divisions.portugal import *
from ciudades_del_mundo.historical_divisions.netherlands import *
#from ciudades_del_mundo.historical_divisions.france import ARTOIS

DIVISIONS = [
    {
        "name": "Castilla",
        "code": "CAS",
        "entity_type": "Corona",
        "capitals": ["Toledo", "Burgos"],
        "childs": [
            SALAMANCA_A,
            LEON_A,
            ZAMORA_A,
            TORO_A,
            VIZCAYA,
            ALAVA_A,
            GUIPUZCOA,
            BURGOS_A, 
            SORIA_A,
            AVILA_A,
            SEGOVIA_A,
            VALLADOLID_A,
            GUADALAJARA_A,
            TOLEDO_A,
            MADRID_A,
            CUENCA_A,
            MURCIA_A,
            SEVILLA_A,
            CORDOBA_A,
            JAEN_A,
            CANARIAS,
            GRANADA_A,
            MELILLA,
            ORAN,
        ],
    },
    {
        "name": "Países Bajos",
        "code": "PBA",
        "entity_type": "Círculo",
        #"capitals": ["Brussels"],
        "childs": [
            ARTOIS,
            FLANDES,
            HENAO,
            CAMBRAI,
            FRISIA,
            HOLANDA,
            DRENTE,
            ZELANDA,
            GRONINGA,
            LUXEMBURGO,
        ],
    },
    {
        "name": "Aragón",
        "code": "ARA",
        "entity_type": "Reino",
        "capitals": ["Zaragoza"],
        "childs": [
            ARAGON
        ]
    },
    {
        "name": "Cataluña",
        "code": "CAT",
        "entity_type": "Principado",
        "capitals": ["Barcelona"],
        "childs": [
            CATALUNHA_A
        ]
    },
    {
        "name": "Valencia",
        "code": "VAL",
        "entity_type": "Reino",
        "capitals": ["València"],
        "childs": [
            VALENCIA_A
        ]
    },
    {
        "name": "Mallorca",
        "code": "MAL",
        "entity_type": "Reino",
        "capitals": ["Palma"],
        "childs": [
            MALLORCA
        ]
    },
    {
        "name": "Cerdeña",
        "code": "CER",
        "entity_type": "Reino",
        "capitals": ["Cagliari"],
        "childs": [
            CERDENHA
        ]
    },
    {
        "name": "Sicilia",
        "code": "SIC",
        "entity_type": "Reino",
        "capitals": ["Palermo"],
        "childs": [
            SICILIA
        ]
    },
    {
        "name": "Nápoles",
        "code": "NAP",
        "entity_type": "Reino",
        "capitals": ["Napoli"],
        "childs": [
            CALABRIA_CITRA,
            CALABRIA_ULTRA,
            BASILICATA,
            TERRA_DE_OTRANTO,
            TERRA_DE_BARI,
            CAPITANIA,
            PRINCIPADO_CITRA,
            PRINCIPADO_ULTRA,
            TIERRA_DE_TRABAJO,
            MOLISE,
            ABRUZOS_ULTRA,
            ABRUZOS_CITRA,
        ]
    },
    {
        "name": "Portugal",
        "code": "POR",
        "entity_type": "Reino",
        "capitals": ["Lisboa"],
        "childs": [
            ENTRE_EL_DUERO_Y_MINHO,
            DETRAS_DE_LOS_MONTES_Y_ALTO_DUERO,
            ALENTEJO,
            ESTREMADURA,
            BEIRA,
            ALGARVE,
            CEUTA,
            TANGER,
            CASABLANCA,
            ALCAZARSEGUIR,
            ARCILA,
            MAZAGAN,
        ],
    },
]

ESCANHOS = {
        "nivel": 1,
        "escanhos": 150,                       
        "min": 1,                              
    }
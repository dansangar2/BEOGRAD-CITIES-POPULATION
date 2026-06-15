from ciudades_del_mundo.models import NuevoAdminArea
from ciudades_del_mundo.historical_divisions.spanish_old_provinces import *


DIVISIONS = [
        {
            "name": "España",
            "code": "ESP",
            "entity_type": "Reino",
            "capitals": ["Madrid"],
            "childs": [
                {
                    "name": "Vascongadas",
                    "code": "VAS",
                    "entity_type": "Provincias Forales",
                    "capitals": ["Bilbao", "Vitoria-Gasteiz", "Donostia"],
                    "childs": [ALAVA_B, VIZCAYA, GUIPUZCOA]
                },
                {
                    "name": "Andalucía",
                    "code": "AND",
                    "entity_type": "Reinos Andaluces",
                    "capitals": ["Sevilla"],
                    "childs": [SEVILLA_B, CORDOBA_B, JAEN_B, NUEVAS_POBLACIONES]
                },
                {
                    "name": "Asturias",
                    "code": "AST",
                    "entity_type": "Principado",
                    "capitals": ["Oviedo"],
                    "childs": [ASTURIAS]
                },
                {
                    "name": "Galicia",
                    "code": "GAL",
                    "entity_type": "Reino",
                    "capitals": ["Santiago de Compostela"],
                    "childs": [GALICIA]
                },
                {
                    "name": "León",
                    "code": "LEO",
                    "entity_type": "Reino",
                    "capitals": ["León"],
                    "childs": [LEON_B, ZAMORA_B, TORO_B, SALAMANCA_B, EXTREMADURA]
                },
                {
                    "name": "Navarra",
                    "code": "NAV",
                    "entity_type": "Reino",
                    "capitals": ["Pamplona"],
                    "childs": [NAVARRA_B]
                },
                {
                    "name": "Castilla",
                    "code": "CAS",
                    "entity_type": "Reino",
                    "capitals": ["Burgos"],
                    "childs": [BURGOS_B, SORIA_B, SEGOVIA_B, AVILA_B, PALENCIA, VALLADOLID_B ]
                },
                {
                    "name": "Toledo",
                    "code": "TOL",
                    "entity_type": "Reino",
                    "capitals": ["Toledo"],
                    "childs": [MADRID_B, GUADALAJARA_B, TOLEDO_B, LA_MANCHA, CUENCA_B]
                },
                {
                    "name": "Murcia",
                    "code": "MUR",
                    "entity_type": "Reino",
                    "capitals": ["Murcia"],
                    "childs": [MURCIA_B]
                },
                {
                    "name": "Granada",
                    "code": "GRA",
                    "entity_type": "Reino",
                    "capitals": ["Granada"],
                    "childs": [GRANADA_B]
                },
                {
                    "name": "Aragón",
                    "code": "ARA",
                    "entity_type": "Reino",
                    "capitals": ["Zaragoza"],
                    "childs": [ARAGON ]
                },
                { 
                    "name": "Cataluña",
                    "code": "CAT",
                    "entity_type": "Principado",
                    "capitals": ["Barcelona"],
                    "childs": [CATALUNHA_B]
                },
                {
                    "name": "Valencia",
                    "code": "VAL",
                    "entity_type": "Reino",
                    "capitals": ["València"],
                    "childs": [VALENCIA_B ]
                },
                {
                    "name": "Mallorca",
                    "code": "MAL",
                    "entity_type": "Reino",
                    "capitals": ["Palma"],
                    "childs": [ MALLORCA ]
                },
                {
                    "name": "África",
                    "code": "AFR",
                    "entity_type": "Capitanía General",
                    "capitals": ["Orán"],
                    "childs": [CEUTA, MELILLA, ORAN ]
                },
                {
                    "name": "Canarias",
                    "code": "CAN",
                    "entity_type": "Capitanía General",
                    "capitals": ["Santa Cruz de Tenerife"],
                    "childs": [CANARIAS]
                },
            ]
        }
    ]

ESCANHOS = {
        "nivel": NuevoAdminArea.Level.ADMIN3,  # provincias del imperio
        "escanhos": 300,                        # total de escaños a repartir
        "min": 1,                              # mínimo por provincia
    }

from ciudades_del_mundo.historical_divisions.spanish_morocco import YEBALA
from ciudades_del_mundo.models import NuevoAdminArea
from spanish_morocco import *

DIVISIONS = [
        {
            "name": "Marruecos Español",
            "code": "EMA",
            "entity_type": "Protectorado",
            "capitals": ["Tétouan"],
            "childs": [
                YEBALA,
                LOCUS,
                CHAUEN,
                RIF,
                KERT,
                TANGER,
            ]
        },
    ]

ESCANHOS = {
        "nivel": NuevoAdminArea.Level.ADMIN2,  # ejemplo: nivel de las provincias
        "escanhos": 100,
        "min": 1,
    },
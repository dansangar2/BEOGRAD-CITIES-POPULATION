from ciudades_del_mundo.domain import DivisionSourceType


DIVISIONS = [
    {"type": DivisionSourceType.INFOSECTION, "urls": "cities", "level": 0},
    {"type": DivisionSourceType.ADMIN, "urls": "admin", "level": 0},
    {
        "type": DivisionSourceType.DOUBLE,
        "urls": [
            "pinardelrio",
            "artemisa",
            "lahabana",
            "mayabeque",
            "matanzas",
            "cienfuegos",
            "villaclara",
            "sanctispiritus",
            "ciegodeavila",
            "camaguey",
            "lastunas",
            "holguin",
            "granma",
            "santiagodecuba",
            "guantanamo",
            "isladelajuventud",
        ],
        "level": 2,
    },
]

LEGAL_SUBDIVISIONS = 1

REPRESENTATION = {}

CITIES = [
    {
        "city": "La Habana",
        "id": "857",
        "district_types": ["Municipality"],
        "level": 1,
        "type": "Municipality",
        "from": {1: ["Ciudad de la Habana"]},
    },
]

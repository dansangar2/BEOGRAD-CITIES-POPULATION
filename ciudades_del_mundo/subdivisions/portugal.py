from ciudades_del_mundo.domain import DivisionSourceType


DIVISIONS = [
    {"type": DivisionSourceType.INFOSECTION, "urls": "cities", "level": 0},
    {"type": DivisionSourceType.ADMIN, "urls": "admin", "level": 0},
    {
        "type": DivisionSourceType.ADMIN,
        "urls": [
            "acores/admin",
            "aveiro/admin",
            "beja/admin",
            "braga/admin",
            "braganca/admin",
            "castelobranco/admin",
            "coimbra/admin",
            "evora/admin",
            "faro/admin",
            "guarda/admin",
            "leiria/admin",
            "lisboa/admin",
            "madeira/admin",
            "portalegre/admin",
            "porto/admin",
            "santarem/admin",
            "setubal/admin",
            "vianadocastelo/admin",
            "vilareal/admin",
            "viseu/admin",
        ],
        "level": 2,
    },
]

LEGAL_SUBDIVISIONS = 3

REPRESENTATION = {}

from ciudades_del_mundo.domain import DivisionSourceType


DIVISIONS = [
    {"type": DivisionSourceType.INFOSECTION, "urls": "cities", "level": 0},
    {"type": DivisionSourceType.ADMIN, "urls": "admin", "level": 0},
    {
        "type": DivisionSourceType.DOUBLE,
        "urls": [
            "adrar",
            "chlef",
            "laghouat",
            "oumelbouaghi",
            "batna",
            "bejaia",
            "biskra",
            "bechar",
            "blida",
            "bouira",
            "tamanrasset",
            "tebessa",
            "tlemcen",
            "tiaret",
            "tiziouzou",
            "eldjazair",
            "djelfa",
            "jijel",
            "setif",
            "saida",
            "skikda",
            "sidibelabbes",
            "annaba",
            "guelma",
            "constantine",
            "medea",
            "mostaganem",
            "msila",
            "mascara",
            "ouargla",
            "oran",
            "elbayadh",
            "illizi",
            "bordjbouarreridj",
            "boumerdes",
            "eltarf",
            "tindouf",
            "tissemsilt",
            "eloued",
            "khenchela",
            "soukahras",
            "tipaza",
            "mila",
            "aindefla",
            "naama",
            "aintemouchent",
            "ghardaia",
            "relizane",
        ],
        "level": 2,
    },
]

LEGAL_SUBDIVISIONS = 2

REPRESENTATION = {}

CITIES = [
        {
            "city": "El Djazaïr",
            "id": "algr2005",
            "district_types": [],
            "type": "Province Capital",
            "from": {1: ["El Djazaïr"]},
            "communes": ["Bologhine", "Bab El Oued", "Casbah", "Oued Koriche", "El Djazaïr",
                         "El Biar", "Sidi M'Hamed", "El Mouradia", "Hydra", "Mohamed Belouizdad (Hamma Annassers)",
                         "El Madania", "Bir Mourad Raïs", "Kouba", "Hussein Dey", "El Magharia", "Bachdjerrah",
                         "Mohammadia", "Bab Ezzouar", "Bordj El Kiffan", "Bordj El Bahri", "El Marsa"],
        },
    ]
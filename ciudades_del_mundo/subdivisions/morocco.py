from ciudades_del_mundo.domain import DivisionSourceType


DIVISIONS = [
    {"type": DivisionSourceType.ADMIN, "urls": "admin", "level": 0},
    {
        "type": DivisionSourceType.ADMIN,
        "urls": [
            "benimellalkhenifra/admin",
            "draatafilalet/admin",
            "fesmeknes/admin",
            "grandcasablancasettat/admin",
            "guelmimouednoun/admin",
            "marrakechsafi/admin",
            "oriental/admin",
            "soussmassa/admin",
            "tangertetouanalhoceima/admin",
            "rabatsalekenitra/admin",
        ],
        "level": 2,
    },
]

LEGAL_SUBDIVISIONS = 2

REPRESENTATION = {}

CITIES = [
    {
        "city": "Tanger",
        "id": "51101000",
        "district_types": ["Arrondissement"],
        "level": 3,
        "type": "City",
        "from": {2: ["Tanger - Assilah"]},
        "communes": [],
    },
    {
        "city": "Casablanca",
        "id": "14101400",
        "district_types": ["Arrondissement"],
        "level": 3,
        "type": "City",
        "from": {2: ["Casablanca"]},
    },
    {
        "city": "Rabat",
        "id": "42101000",
        "district_types": ["Arrondissement"],
        "level": 3,
        "type": "City",
        "from": {2: ["Rabat"]},
    },
    {
        "city": "Salé",
        "id": "44101000",
        "district_types": ["Arrondissement"],
        "level": 3,
        "type": "City",
        "from": {2: ["Salé"]},
    },
    {
        "city": "Fès",
        "id": "23101000",
        "district_types": ["Arrondissement"],
        "level": 3,
        "type": "City",
        "from": {2: ["Fès"]},
    },
    {
        "city": "Marrakech",
        "id": "35101000",
        "district_types": ["Arrondissement"],
        "level": 3,
        "type": "City",
        "from": {2: ["Marrakech"]},
    },
]

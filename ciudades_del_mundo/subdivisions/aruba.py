from ciudades_del_mundo.domain import DivisionSourceType


DIVISIONS = [
    {"type": DivisionSourceType.CITIES, "urls": "cities", "level": 0},
    {"type": DivisionSourceType.ADMIN, "urls": "admin", "level": 1},
]

CITIES = [
    {
        "city": "Oranjestad",
        "id": "3C",
        "district_types": ["Zone"],
        "level": 2,
        "type": "Zone",
        "from": {1: ["Oranjestad West", "Oranjestad Oost"]},
    },
]
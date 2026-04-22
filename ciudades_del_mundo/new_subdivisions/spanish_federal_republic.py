from ciudades_del_mundo.domain import RepresentationSystem

SOURCE_COUNTRY = "spain"

DIVISIONS = [
    {
        "name": "Andalucía Alta",
        "code": "AAN",
        "entity_type": "Estado",
        "capitals": ["Granada"],
        "childs": [
            {
                "name": "Almería",
                "code": "ALM",
                "entity_type": "Provincia",
                "capitals": ["Almería"],
                "dat": {2: ["Almería"]}
            },
            {
                "name": "Granada",
                "code": "GRA",
                "entity_type": "Provincia",
                "capitals": ["Granada"],
                "dat": {2: ["Granada"]}
            },
            {
                "name": "Jaén",
                "code": "JAE",
                "entity_type": "Provincia",
                "capitals": ["Jaén"],
                "dat": {2: ["Jaén"]}
            },
            {
                "name": "Málaga",
                "code": "MAL",
                "entity_type": "Provincia",
                "capitals": ["Málaga"],
                "dat": {2: ["Málaga", "Melilla"]}
            },
        ],
    },
    {
        "name": "Andalucía Baja",
        "code": "ABN",
        "entity_type": "Estado",
        "capitals": ["Sevilla"],
        "childs": [
            {
                "name": "Cádiz",
                "code": "CAD",
                "entity_type": "Provincia",
                "capitals": ["Cádiz"],
                "dat": {2: ["Cádiz", "Ceuta"]}
            },
            {
                "name": "Córdoba",
                "code": "COR",
                "entity_type": "Provincia",
                "capitals": ["Córdoba"],
                "dat": {2: ["Córdoba"]}
            },
            {
                "name": "Huelva",
                "code": "HUV",
                "entity_type": "Provincia",
                "capitals": ["Huelva"],
                "dat": {2: ["Huelva"]}
            },
            {
                "name": "Sevilla",
                "code": "SEV",
                "entity_type": "Provincia",
                "capitals": ["Sevilla"],
                "dat": {2: ["Sevilla"]}
            },
        ],
    },
    {
        "name": "Aragón",
        "code": "ARA",
        "entity_type": "Estado",
        "capitals": ["Zaragoza"],
        "childs": [
            {
                "name": "Huesca",
                "code": "HUE",
                "entity_type": "Provincia",
                "capitals": ["Huesca"],
                "dat": {2: ["Huesca"]}
            },
            {
                "name": "Teruel",
                "code": "TER",
                "entity_type": "Provincia",
                "capitals": ["Teruel"],
                "dat": {2: ["Teruel"]}
            },
            {
                "name": "Zaragoza",
                "code": "ZAR",
                "entity_type": "Provincia",
                "capitals": ["Zaragoza"],
                "dat": {2: ["Zaragoza"]}
            },
        ],
    },
    {
        "name": "Asturias",
        "code": "AST",
        "entity_type": "Estado",
        "capitals": ["Oviedo"],
        "childs": [
            {
                "name": "Asturias",
                "code": "ASTP",
                "entity_type": "Provincia",
                "capitals": ["Oviedo"],
                "dat": {2: ["Asturias"]}
            },
        ],
    },
    {
        "name": "Baleares",
        "code": "BAL",
        "entity_type": "Estado",
        "capitals": ["Palma"],
        "childs": [
            {
                "name": "Baleares",
                "code": "BALP",
                "entity_type": "Provincia",
                "capitals": ["Palma"],
                "dat": {2: ["Illes Balears"]}
            },
        ],
    },
    {
        "name": "Canarias",
        "code": "CAN",
        "entity_type": "Estado",
        "capitals": ["Santa Cruz de Tenerife"],
        "childs": [
            {
                "name": "Las Palmas",
                "code": "LPA",
                "entity_type": "Provincia",
                "capitals": ["Las Palmas de Gran Canaria"],
                "dat": {2: ["Las Palmas"]}
            },
            {
                "name": "Santa Cruz de Tenerife",
                "code": "SCT",
                "entity_type": "Provincia",
                "capitals": ["Santa Cruz de Tenerife"],
                "dat": {2: ["Santa Cruz de Tenerife"]}
            },
        ],
    },
    {
        "name": "Castilla la Nueva",
        "code": "CLN",
        "entity_type": "Estado",
        "capitals": ["Madrid"],
        "childs": [
            {
                "name": "Ciudad Real",
                "code": "CRI",
                "entity_type": "Provincia",
                "capitals": ["Ciudad Real"],
                "dat": {2: ["Ciudad Real"]}
            },
            {
                "name": "Cuenca",
                "code": "CUE",
                "entity_type": "Provincia",
                "capitals": ["Cuenca"],
                "dat": {2: ["Cuenca"]}
            },
            {
                "name": "Guadalajara",
                "code": "GUA",
                "entity_type": "Provincia",
                "capitals": ["Guadalajara"],
                "dat": {2: ["Guadalajara"]}
            },
            {
                "name": "Madrid",
                "code": "MAD",
                "entity_type": "Provincia",
                "capitals": ["Madrid"],
                "dat": {2: ["Madrid"]}
            },
            {
                "name": "Toledo",
                "code": "TOL",
                "entity_type": "Provincia",
                "capitals": ["Toledo"],
                "dat": {2: ["Toledo"]}
            },
        ],
    },
    {
        "name": "Castilla la Vieja",
        "code": "CLV",
        "entity_type": "Estado",
        "capitals": ["Valladolid"],
        "childs": [
            {
                "name": "Ávila",
                "code": "AVI",
                "entity_type": "Provincia",
                "capitals": ["Ávila"],
                "dat": {2: ["Ávila"]}
            },
            {
                "name": "Burgos",
                "code": "BUR",
                "entity_type": "Provincia",
                "capitals": ["Burgos"],
                "dat": {2: ["Burgos"]}
            },
            {
                "name": "Cantabria",
                "code": "CANP",
                "entity_type": "Provincia",
                "capitals": ["Santander"],
                "dat": {2: ["Cantabria"]}
            },
            {
                "name": "La Rioja",
                "code": "RIO",
                "entity_type": "Provincia",
                "capitals": ["Logroño"],
                "dat": {2: ["La Rioja"]}
            },
            {
                "name": "León",
                "code": "LEO",
                "entity_type": "Provincia",
                "capitals": ["León"],
                "dat": {2: ["León"]}
            },
            {
                "name": "Palencia",
                "code": "PAL",
                "entity_type": "Provincia",
                "capitals": ["Palencia"],
                "dat": {2: ["Palencia"]}
            },
            {
                "name": "Salamanca",
                "code": "SAL",
                "entity_type": "Provincia",
                "capitals": ["Salamanca"],
                "dat": {2: ["Salamanca"]}
            },
            {
                "name": "Segovia",
                "code": "SEG",
                "entity_type": "Provincia",
                "capitals": ["Segovia"],
                "dat": {2: ["Segovia"]}
            },
            {
                "name": "Soria",
                "code": "SOR",
                "entity_type": "Provincia",
                "capitals": ["Soria"],
                "dat": {2: ["Soria"]}
            },
            {
                "name": "Valladolid",
                "code": "VLL",
                "entity_type": "Provincia",
                "capitals": ["Valladolid"],
                "dat": {2: ["Valladolid"]}
            },
            {
                "name": "Zamora",
                "code": "ZAM",
                "entity_type": "Provincia",
                "capitals": ["Zamora"],
                "dat": {2: ["Zamora"]}
            },
        ],
    },
    {
        "name": "Cataluña",
        "code": "CAT",
        "entity_type": "Estado",
        "capitals": ["Barcelona"],
        "childs": [
            {
                "name": "Barcelona",
                "code": "BCN",
                "entity_type": "Provincia",
                "capitals": ["Barcelona"],
                "dat": {2: ["Barcelona"]}
            },
            {
                "name": "Girona",
                "code": "GIR",
                "entity_type": "Provincia",
                "capitals": ["Girona"],
                "dat": {2: ["Girona"]}
            },
            {
                "name": "Lleida",
                "code": "LLE",
                "entity_type": "Provincia",
                "capitals": ["Lleida"],
                "dat": {2: ["Lleida"]}
            },
            {
                "name": "Tarragona",
                "code": "TAR",
                "entity_type": "Provincia",
                "capitals": ["Tarragona"],
                "dat": {2: ["Tarragona"]}
            },
        ],
    },
    {
        "name": "Extremadura",
        "code": "EXT",
        "entity_type": "Estado",
        "capitals": ["Badajoz"],
        "childs": [
            {
                "name": "Badajoz",
                "code": "BAD",
                "entity_type": "Provincia",
                "capitals": ["Badajoz"],
                "dat": {2: ["Badajoz"]}
            },
            {
                "name": "Cáceres",
                "code": "CAC",
                "entity_type": "Provincia",
                "capitals": ["Cáceres"],
                "dat": {2: ["Cáceres"]}
            },
        ],
    },
    {
        "name": "Galicia",
        "code": "GAL",
        "entity_type": "Estado",
        "capitals": ["Santiago de Compostela"],
        "childs": [
            {
                "name": "A Coruña",
                "code": "CORU",
                "entity_type": "Provincia",
                "capitals": ["A Coruña"],
                "dat": {2: ["A Coruña"]}
            },
            {
                "name": "Lugo",
                "code": "LUG",
                "entity_type": "Provincia",
                "capitals": ["Lugo"],
                "dat": {2: ["Lugo"]}
            },
            {
                "name": "Ourense",
                "code": "OUR",
                "entity_type": "Provincia",
                "capitals": ["Ourense"],
                "dat": {2: ["Ourense"]}
            },
            {
                "name": "Pontevedra",
                "code": "PON",
                "entity_type": "Provincia",
                "capitals": ["Pontevedra"],
                "dat": {2: ["Pontevedra"]}
            },
        ],
    },
    {
        "name": "Murcia",
        "code": "MUR",
        "entity_type": "Estado",
        "capitals": ["Murcia"],
        "childs": [
            {
                "name": "Albacete",
                "code": "ALB",
                "entity_type": "Provincia",
                "capitals": ["Albacete"],
                "dat": {2: ["Albacete"]}
            },
            {
                "name": "Murcia",
                "code": "MRC",
                "entity_type": "Provincia",
                "capitals": ["Murcia"],
                "dat": {2: ["Murcia"]}
            },
        ],
    },
    {
        "name": "Navarra",
        "code": "NAV",
        "entity_type": "Estado",
        "capitals": ["Pamplona"],
        "childs": [
            {
                "name": "Navarra",
                "code": "NAVP",
                "entity_type": "Provincia",
                "capitals": ["Pamplona"],
                "dat": {2: ["Navarra"]}
            },
        ],
    },
    {
        "name": "Valencia",
        "code": "VAL",
        "entity_type": "Estado",
        "capitals": ["València"],
        "childs": [
            {
                "name": "Alicante",
                "code": "ALI",
                "entity_type": "Provincia",
                "capitals": ["Alicante"],
                "dat": {2: ["Alicante"]}
            },
            {
                "name": "Castellón",
                "code": "CAS",
                "entity_type": "Provincia",
                "capitals": ["Castelló de la Plana"],
                "dat": {2: ["Castellón"]}
            },
            {
                "name": "Valencia",
                "code": "VLC",
                "entity_type": "Provincia",
                "capitals": ["València"],
                "dat": {2: ["València"]}
            },
        ],
    },
    {
        "name": "Regiones Vascongadas",
        "code": "VAS",
        "entity_type": "Estado",
        "capitals": ["Vitoria-Gasteiz"],
        "childs": [
            {
                "name": "Álava",
                "code": "ALA",
                "entity_type": "Provincia",
                "capitals": ["Vitoria-Gasteiz"],
                "dat": {2: ["Araba"]}
            },
            {
                "name": "Gipuzkoa",
                "code": "GIP",
                "entity_type": "Provincia",
                "capitals": ["Donostia"],
                "dat": {2: ["Gipuzkoa"]}
            },
            {
                "name": "Bizkaia",
                "code": "BIZ",
                "entity_type": "Provincia",
                "capitals": ["Bilbao"],
                "dat": {2: ["Bizkaia"]}
            },
        ],
    },
]

REPRESENTATION = {
    "level": 2,
    "total": 350,
    "min": 2,
    #"min_exceptions": {"spain_51": 1, "spain_52": 1},
    #"max_exceptions": {"spain_51": 1, "spain_52": 1},
    "system": RepresentationSystem.DHONDT,
}

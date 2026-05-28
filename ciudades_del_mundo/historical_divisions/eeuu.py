from ciudades_del_mundo.historical_divisions.canada import YUKON_CIUDADES_A_NUTKA, COLUMBIA_BRITANICA_A_TERRITORIO_DEL_NORTE

#Alabama
#Alaska
#Connecticut
#Delaware
#District of Columbia
#Georgia
#Hawaii
#Illinois
#Indiana
#Kentucky
#Maine
#Maryland
#Massachusetts
#Michigan
#Mississippi
#New Hampshire
#New Jersey
#New York
#North Carolina
#Ohio
#Pennsylvania
#Rhode Island
#South Carolina
#Tennessee
#Vermont
#Virginia
#West Virginia
#Wisconsin


#=============ESPAÑA===================

def _in_parent(parent: str, names: list[str]) -> list[str]:
    return [f"{parent}|{name}" for name in names]


FLORIDA_A_FLORIDA_OCCIDENTAL = _in_parent("Florida", [
    "Franklin", "Liberty", "Jackson", "Calhoun", "Gulf", "Bay",
    "Washington", "Holmes", "Walton", "Okaloosa", "Santa Rosa", "Escambia",
])
ALABAMA_A_FLORIDA_OCCIDENTAL = _in_parent("Alabama", [
    "Russell", "Bullock", "Montgomery", "Lowndes", "Wilcox", "Marengo", "Choctaw",
    "Washington", "Clarke", "Monroe", "Conecuh", "Butler", "Crenshaw", "Pike",
    "Barbour", "Henry", "Houston", "Dale", "Geneva", "Coffee", "Covington",
    "Escambia", "Baldwin", "Mobile",
])
MISSISSIPI_A_FLORIDA_OCCIDENTAL = _in_parent("Mississippi", [
    "Lauderdale", "Clarke", "Jasper", "Smith", "Scott", "Rankin", "Simpson",
    "Copiah", "Hinds", "Warren", "Claiborne", "Jefferson", "Adams", "Franklin",
    "Lincoln", "Lawrence", "Jefferson Davis", "Covington", "Jones", "Wayne",
    "Greene", "Perry", "Forrest", "Lamar", "Marion", "Walthall", "Pike",
    "Amite", "Wilkinson", "George", "Stone", "Pearl River", "Hancock",
    "Harrison", "Jackson",
])
LUISIANA_A_FLORIDA_OCCIDENTAL = _in_parent("Louisiana", [
    "West Feliciana", "East Feliciana", "St. Helena", "Tangipahoa", "Washington",
    "St. Tammany", "Livingston", "East Baton Rouge",
])

ALABAMA_A_FLORIDA_OCCIDENTAL_2 = _in_parent("Alabama", [
    "Baldwin", "Mobile",
])
MISSISSIPI_A_FLORIDA_OCCIDENTAL_2 = _in_parent("Mississippi", [
    "George", "Stone", "Pearl River", "Hancock", "Harrison", "Jackson",
])

LUISIANA_A_TEXAS = _in_parent("Louisiana", [
    "Sabine", "Vernon", "Beauregard", "Allen", "Evangeline", "Acadia",
    "Jefferson Davis", "Calcasieu", "Cameron", "Vermilion", "Lafayette",
])

MINNESOTA_A_CANADA = _in_parent("Minnesota", [
    "Washington", "Ramsey", "Anoka", "Chisago", "Isanti", "Sherburne", "Benton", "Morrison", "Mille Lacs",
    "Kanabec", "Pine", "Aitkin", "Carlton", "Itasca", "Koochiching", "St. Louis", "Lake", "Cook", "Lake of the Woods",
    "Beltrami", "Roseau", "Kittson", "Marshall", "Polk", "Red Lake", "Pennington", "Mahnomen", "Norman",
    "Clay", "Otter Tail", "Wilkin", "Traverse"
])

DAKOTA_DEL_NORTE_A_CANADA = _in_parent("North Dakota", [
    "Richland", "Sargent", "Ransom", "Cass", "Barnes", "Traill", "Steele", "Griggs", "Grand Forks", "Nelson",
    "Walsh", "Pembina", "Cavalier", "Ramsey", "Towner", "Benson", "Pierce", "Rolette", "Bottineau", "McHenry",
    "Renville", "Ward", "Burke"
])

MONTANA_A_NUTKA = _in_parent("Montana", [
    "Ravalli", "Granite", "Powell", "Missoula", "Mineral", "Sanders", "Lake", "Flathead", "Lincoln"
])

IDAHO_A_CALIFORNIA = _in_parent("Idaho", [
    "Bear Lake", "Caribou", "Franklin", "Oneida"
])

WYOMING_A_NUTKA = _in_parent("Wyoming", [
    "Teton",
])

WYOMING_A_CALIFORNIA = _in_parent("Wyoming", [
    "Sublette", "Lincoln", "Uinta", "Sweetwater"
])

WYOMING_A_CALIFORNIA_2 = _in_parent("Wyoming", [
    "Carbon", "Uinta", "Sweetwater"
])

COLORADO_A_CALIFORNIA = _in_parent("Colorado", [
    "Moffat", "Routt", "Grand", "Rio Blanco", "Garfield"
])

COLORADO_A_CALIFORNIA_2 = _in_parent("Colorado", [
    "Moffat", "Routt", "Grand", "Rio Blanco", "Garfield", "Jackson"
])

COLORADO_A_NUEVO_MEXICO = _in_parent("Colorado", [
    "Eagle", "Summit", "Pitkin", "Mesa", "Delta", "Gunnison", "Saguache",
    "Hinsdale", "San Juan", "Dolores", "Montezuma", "La Plata", "Archuleta",
    "Mineral", "Rio Grande", "Alamosa", "Costilla", "Conejos"
])

COLORADO_A_NUEVO_MEXICO_2 = _in_parent("Colorado", [
    "Eagle", "Summit", "Pitkin", "Mesa", "Delta", "Gunnison", "Saguache",
    "Hinsdale", "San Juan", "Dolores", "Montezuma", "La Plata", "Archuleta",
    "Mineral", "Rio Grande", "Alamosa", "Costilla", "Conejos", "Lake", "Chaffee", "Custer",
    "Huerfano", "Las Animas", "Pueblo", "Otero", "Bent", "Prowers", "Baca"
])

KANSAS_A_NUEVO_MEXICO = _in_parent("Kansas", [
    "Stanton", "Grant", "Haskell", "Morton", "Stevens", "Seward", "Meade"
])

OKLAHOMA_A_NUEVO_MEXICO = _in_parent("Oklahoma", [
    "Cimarron", "Texas", "Beaver"
])

CALIFORNIA_A_NUTKA = _in_parent("California", [
    "Del Norte"
])

OREGON_A_CALIFORNIA = _in_parent("Oregon", [
    "Klamath", "Lake"
])

ARIZONA_A_NUEVA_NAVARRA = _in_parent("Arizona", [
    "Pinal", "Yuma", "Pima", "Santa Cruz", "Cochise", "Greenlee"
])

NUEVO_MEXICO_A_NUEVA_VIZCAYA = _in_parent("New Mexico", [
    "Hidalgo", "Luna"
])

NUEVO_MEXICO_A_LUISIANA = _in_parent("New Mexico", [
    "Union", "Quay", "San Miguel", "Harding", "Mora", "Colfax"
])

TEXAS_A_NUEVO_MEXICO = _in_parent("Texas", [
    "Parmer", "Bailey", "Lamb", "Hale", "Cochran", "Hockley", "Lubbock", "Crosby", "Kent",
    "Garza", "Lynn", "Terry", "Yoakum", "Gaines", "Dawson", "Borden", "Scurry", "Howard",
    "Martin", "Andrews"
])

TEXAS_A_NUEVO_MEXICO_2 = _in_parent("Texas", [
    "Parmer", "Bailey", "Lamb", "Hale", "Cochran", "Hockley", "Lubbock", "Crosby", "Kent",
    "Garza", "Lynn", "Terry", "Yoakum", "Gaines", "Dawson", "Borden", "Scurry", "Howard",
    "Martin", "Andrews", "Dallam", "Sherman", "Hansford", "Ochiltree", "Lipscomb", "Hemphill", "Roberts", "Hutchinson",
    "Moore", "Hartley", "Oldham", "Potter", "Carson", "Gray", "Wheeler", "Collingsworth", "Donley",
    "Armstrong", "Randall", "Deaf Smith", "Castro", "Swisher", "Briscoe", "Hall", "Childress",
    "Floyd", "Motley", "Dickens", "Cottle", "King", "Knox", "Foard", "Hardeman", "Wilbarger", "Baylor"
])

TEXAS_A_LUISIANA = _in_parent("Texas", [
    "Dallam", "Sherman", "Hansford", "Ochiltree", "Lipscomb", "Hemphill", "Roberts", "Hutchinson",
    "Moore", "Hartley", "Oldham", "Potter", "Carson", "Gray", "Wheeler", "Collingsworth", "Donley",
    "Armstrong", "Randall", "Deaf Smith", "Castro", "Swisher", "Briscoe", "Hall", "Childress",
    "Floyd", "Motley", "Dickens", "Cottle", "King", "Knox", "Foard", "Hardeman", "Wilbarger", "Baylor",
    "Wichita", "Archer", "Clay", "Montague", "Grayson", "Fannin", "Lamar", "Delta", "Hopkins", "Red River",
    "Franklin", "Titus", "Morris", "Camp", "Upshur", "Bowie", "Cass", "Marion"
])

TEXAS_A_NUEVA_VIZCAYA = _in_parent("Texas", [
    "El Paso", "Hudspeth", "Culberson", "Jeff Davis", "Presidio", "Brewster", "Terrell", "Pecos",
    "Reeves", "Loving", "Winkler", "Ector", "Ward", "Crane", "Upton", "Reagan", "Crockett", "Val Verde",
    "Edwards"
])

TEXAS_A_NUEVA_EXTREMADURA = _in_parent("Texas", [
    "Kinney", "Maverick", "Uvalde", "Zavala"
])

TEXAS_A_NUEVO_SANTANDER = _in_parent("Texas", [
    "Dimmit", "Webb", "McMullen", "Live Oak", "Nueces", "Jim Wells", "Duval", "Zapata", "Jim Hogg",
    "Brooks", "Kenedy", "Willacy", "Hidalgo", "Starr", "Cameron", "Kleberg"
])

ALASKA_A_NUTCA = _in_parent("Alaska", [
    "Ketchikan Gateway", "Prince of Wales-Hyder", "Wrangell", "Petersburg", "Hoonah-Angoon",
    "Sitka", "Juneau", "Haines", "Skagway", "Yakutat", "Chugach", "Copper River"
])

#==================== ESTADOS ==============================

FLORIDA_ORIENTAL = {
    "name": "Florida Oriental",
    "code": "FLR",
    "entity_type": "Provincia",
    "capitals": ["St. Augustine"],
    "spec": {
        1:{"usa": ["Florida"]},
        "restar": {2: {"usa": FLORIDA_A_FLORIDA_OCCIDENTAL}},
    }
}

FLORIDA_OCCIDENTAL = {
    "name": "Florida Occidental",
    "code": "FLC",
    "entity_type": "Provincia",
    "capitals": ["Pensacola"],
    "spec": {
        2: {"usa": FLORIDA_A_FLORIDA_OCCIDENTAL + MISSISSIPI_A_FLORIDA_OCCIDENTAL + ALABAMA_A_FLORIDA_OCCIDENTAL + LUISIANA_A_FLORIDA_OCCIDENTAL},
    }
}

FLORIDA_OCCIDENTAL_2 = {
    "name": "Florida Occidental",
    "code": "FLC",
    "entity_type": "Provincia",
    "capitals": ["Pensacola"],
    "spec": {
        2: {"usa": FLORIDA_A_FLORIDA_OCCIDENTAL + MISSISSIPI_A_FLORIDA_OCCIDENTAL_2 + ALABAMA_A_FLORIDA_OCCIDENTAL_2 + LUISIANA_A_FLORIDA_OCCIDENTAL},
    }
}

LUISIANA = {
    "name": "Luisiana",
    "code": "LUI",
    "entity_type": "Provincia",
    "capitals": ["New Orleans"],
    "spec": {
        1:{"usa": [
            "Louisiana", "Oklahoma", "Arkansas", "Missouri", "Iowa", "Kansas", "Nebraska",
            "Minnesota", "South Dakota", "North Dakota", "Montana", "Wyoming", "Colorado"
        ]},
        2: {"usa": TEXAS_A_LUISIANA},
        "restar": {
            2: {
                "usa": LUISIANA_A_FLORIDA_OCCIDENTAL + LUISIANA_A_TEXAS + COLORADO_A_CALIFORNIA +
                       COLORADO_A_NUEVO_MEXICO + MINNESOTA_A_CANADA + WYOMING_A_CALIFORNIA +
                       WYOMING_A_NUTKA + NUEVO_MEXICO_A_LUISIANA
                }
        },
    }
}

TEXAS = {
    "name": "Nueva Filipinas",
    "code": "TEX",
    "entity_type": "Provincia",
    "capitals": ["San Antonio"],
    "spec": {
        1: {"usa": ["Texas"]},
        2: {"usa": LUISIANA_A_TEXAS},
        "restar": {2: {"usa": TEXAS_A_LUISIANA + TEXAS_A_NUEVO_MEXICO + TEXAS_A_NUEVO_SANTANDER + TEXAS_A_NUEVA_VIZCAYA + TEXAS_A_NUEVA_EXTREMADURA}},
    }
}

NUEVA_CALIFORNIA = {
    "name": "Nueva California",
    "code": "NCA",
    "entity_type": "Provincia",
    "capitals": ["Monterey"],
    "spec": {
        1: {"usa": ["Utah", "Nevada", "California", "Arizona"]},
        2: {"usa":
                OREGON_A_CALIFORNIA + IDAHO_A_CALIFORNIA +
                WYOMING_A_CALIFORNIA + COLORADO_A_CALIFORNIA
            },
        "restar": { 2: {"usa": ARIZONA_A_NUEVA_NAVARRA + CALIFORNIA_A_NUTKA}}
    }
}

NUEVA_CALIFORNIA_2 = {
    "name": "Nueva California",
    "code": "NCA",
    "entity_type": "Provincia",
    "capitals": ["Monterey"],
    "spec": {
        1: {"usa": ["Utah", "Nevada", "California", "Arizona"]},
        2: {"usa":
                WYOMING_A_CALIFORNIA + COLORADO_A_CALIFORNIA
            },
        "restar": {2: {"usa": ARIZONA_A_NUEVA_NAVARRA}}
    }
}

NUEVO_MEXICO = {
    "name": "Santa Fe de Nuevo México",
    "code": "NME",
    "entity_type": "Provincia",
    "capitals": ["Santa Fe"],
    "spec": {
        1: {"usa": ["New Mexico"]},
        2: {"usa": COLORADO_A_NUEVO_MEXICO + TEXAS_A_NUEVO_MEXICO  },
        "restar": {2: {"usa": NUEVO_MEXICO_A_NUEVA_VIZCAYA + NUEVO_MEXICO_A_LUISIANA }},
    }
}

NUEVO_MEXICO_2 = {
    "name": "Santa Fe de Nuevo México",
    "code": "NME",
    "entity_type": "Provincia",
    "capitals": ["Santa Fe"],
    "spec": {
        1: {"usa": ["New Mexico"]},
        2: {"usa": COLORADO_A_NUEVO_MEXICO_2 + KANSAS_A_NUEVO_MEXICO + OKLAHOMA_A_NUEVO_MEXICO + TEXAS_A_NUEVO_MEXICO_2  },
        "restar": {2: {"usa": NUEVO_MEXICO_A_NUEVA_VIZCAYA }},
    }
}

NUTKA = {
    "name": "Nutka",
    "code": "NUT",
    "entity_type": "Territorio",
    "capitals": ["Victoria"],
    "province_status": "territorio",
    "spec": {
        1: {"usa": ["Washington", "Idaho", "Oregon"], "canada": ["British Columbia"]},
        2: {"usa": MONTANA_A_NUTKA + WYOMING_A_NUTKA + CALIFORNIA_A_NUTKA + ALASKA_A_NUTCA },
        3: {"canada": YUKON_CIUDADES_A_NUTKA },
        "restar": {
            2: {"usa": OREGON_A_CALIFORNIA },
            3: {"canada": COLUMBIA_BRITANICA_A_TERRITORIO_DEL_NORTE}
        },
    }
}

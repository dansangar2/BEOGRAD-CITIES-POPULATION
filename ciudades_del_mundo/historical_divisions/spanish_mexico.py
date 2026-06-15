##===============CANADA========

COLUMBIA_BRITANICA_A_TERRITORIO_DEL_NORTE = ["Tumbler Ridge", "Pouce Coupe", "Dawson Creek",
                                             "Chetwynd", "East Moberly Lake 169", "West Moberly Lake 168A",
                                             "Hudson's Hope", "Peace River C", "Taylor", "Fort St. John",
                                             "Halfway River 168", "Doig River 206", "Blueberry River 205",
                                             "Fort Nelson 2", "Prophet River 4"]
YUKON_CIUDADES_A_NUTKA = ["Beaver Creek", "Faro", "Carmacks", "Ross River", "Burwash Landing",
                          "Destruction Bay", "Haines Junction", "Champagne Landing 10",
                          "Macpherson - Grizzly Valley", "Lake Laberge 1", "Whitehorse, Unorganized",
                          "Ibex Valley", "Whitehorse", "Mt. Lorne", "Marsh Lake", "Carcross",
                          "Carcross 4", "Tagish", "Johnsons Crossing", "Teslin Post 13",
                          "Teslin", "Swift River", "Watson Lake", "Upper Liard"]

##================EEUU===========

def _in_parent(parent: str, names: list[str]) -> list[str]:
    return [f"{parent}|{name}" for name in names]


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

##==========MEXICO=================

COAHUILA_A_NUEVA_VIZCAYA = ["Ocampo", "Acuña", "Sierra Mojada"]
COAHUILA_EXCLUYE_MUN_A_NUEVA_VIZCAYA = ["Ciudad Acuña"]

TABASCO_A_VERACRUZ = ["Cárdenas", "Huimanguillo"]
CAMPECHE_A_GUATEMALA = ["Calakmul", "Escárcega", "Candelaria"]
CAMPECHE_A_CARMEN = ["Carmen", "Palizada"]
QUERETARO_A_GUANAJUATO = ["Peñamiller"]

CHIAPAS_A_GUATEMALA = ["Benemérito de las Américas", "Marqués de Comillas", "Ocosingo", "Maravilla Tenejapa", "Las Margaritas",
                       "Altamirano", "La Independencia",
                       "Motozintla", "Mazapa de Madero", "El Porvenir"]
CHIAPAS_A_EXCLUIR_A_GUATEMALA = ["Ocosingo"]
GUATEMALA_A_CHIAPAS = ["La Blanca", "Ocós", "Ayutla"]

MINCHOACAN_A_GUADALAJARA = ["Marcos Castellanos"]
COLIMA_A_MINCHOACAN = ["Ixtlahuacán"]

MORELOS_A_PUEBLA =["Zacualtipán de Ángeles", "Agua Blanca de Iturbide", "San Bartolo Tutotepec", "Tenango de Doria", "Huehuetla"]

VERACRUZ_A_PUEBLA = ["Huayacocotla", "Zacualpan", "Texcatepec", "Zontecomatlán de López y Fuentes", "Tlachichilco",
                     "Benito Juárez", "Ilamatlán", "Ixhuatlán de Madero",
                     "Tihuatlán", "Cazones de Herrera", "Tuxpan", "Castillo de Teayo", "Álamo Temapache",
                     "Tamiahua", "Chinampa de Gorostiza", "Naranjos Amatlán", "Tancoco", "Cerro Azul",
                     "Tepetzintla"]
VERACRUZ_A_OAXACA = ["San Juan Evangelista"]

GUERRERO_A_PUEBLA = ["Olinalá", "Xochihuehuetlán", "Huamuxtitlán", "Cualác", "Alpoyeca", "Tlapa de Comonfort", "Tlalixtaquilla de Maldonado",
                     "Alcozauca de Guerrero", "Xalpatláhuac", "Metlatónoc", "Cochoapa el Grande", "Tlacoachistlahuaca", "Xochistlahuaca",
                     "Ometepec", "Cuajinicuilapa", "Marquelia", "Juchitán", "Azoyú", "Igualapa", "Copala", "Florencio Villarreal", "Cuautepec",
                     "San Luis Acatlán", "Ayutla de los Libres", "Acatepec", "Malinaltepec", "Iliatenco", "Tlacoapa",
                     "Zapotitlán Tablas", "Atlamajalcingo del Monte", "Copanatoyac"]
GUERRERO_A_VALLADOLID = ["Zirándaro", "Coyuca de Catalán", "Ajuchitlán del Progreso", "Tlapehuala", "Pungarabato"]

VIEJA_CALIFORNIA = {
    "name": "Vieja California",
    "code": "VCA",
    "entity_type": "Provincia",
    "capitals": ["Loreto"],
    "depends_of": "NES-NCA",
    "spec": {
        1: {"mexico": ["Baja California", "Baja California Sur"]}
    }
}

NUEVO_LEON = {
    "name": "Nuevo León",
    "code": "NLE",
    "entity_type": "Provincia",
    "capitals": ["Monterrey"],
    "spec": {
        1: {"mexico": ["Nuevo León"]}
    }
}

NUEVA_NAVARRA = {
    "name": "Nueva Navarra",
    "code": "NNA",
    "entity_type": "Provincia",
    "capitals": ["Arizpe"],
    "spec": {
        1: {"mexico": ["Sinaloa", "Sonora"]},
        2: {"usa": ARIZONA_A_NUEVA_NAVARRA}
    }
}

NUEVA_VIZCAYA = {
    "name": "Nueva Vizcaya",
    "code": "NVI",
    "entity_type": "Provincia",
    "capitals": ["Durango"],
    "spec": {
        1: {"mexico": ["Durango", "Chihuahua"]},
        2: {"usa": TEXAS_A_NUEVA_VIZCAYA + NUEVO_MEXICO_A_NUEVA_VIZCAYA, "mexico": COAHUILA_A_NUEVA_VIZCAYA},
        "restar": {3: {"mexico": COAHUILA_EXCLUYE_MUN_A_NUEVA_VIZCAYA}}
    }
}


NUEVA_EXTREMADURA = {
    "name": "Nueva Extremadura",
    "code": "NEX",
    "entity_type": "Provincia",
    "capitals": ["Saltillo"],
    "spec": {
        1: {"mexico": ["Coahuila de Zaragoza"]},
        2: {"usa": TEXAS_A_NUEVA_EXTREMADURA},
        3: {"mexico": COAHUILA_EXCLUYE_MUN_A_NUEVA_VIZCAYA},
        "restar": {2: {"mexico": COAHUILA_A_NUEVA_VIZCAYA}}
    }
}

NUEVO_SANTANDER = {
    "name": "Nuevo Santander",
    "code": "NSA",
    "entity_type": "Provincia",
    "capitals": ["Santander"],
    "spec": {
        1: {"mexico": ["Tamaulipas"]},
        2: {"usa": TEXAS_A_NUEVO_SANTANDER}
    }
}

OAXACA = {
    "name": "Oaxaca",
    "code": "OAX",
    "entity_type": "Provincia",
    "capitals": ["Oaxaca de Juárez"],
    "spec": {
        1: {"mexico": ["Oaxaca"]},
        "restar": {2: { "mexico": VERACRUZ_A_OAXACA}},
    }
}

ZACATECAS = {
    "name": "Zacatecas",
    "code": "ZAC",
    "entity_type": "Provincia",
    "capitals": ["Zacatecas"],
    "spec": {
        1: {"mexico": ["Zacatecas", "Aguascalientes"]}
    }
}

SAN_LUIS_POTOSI = {
    "name": "San Luis Potosí",
    "code": "SLP",
    "entity_type": "Provincia",
    "capitals": ["San Luis Potosí"],
    "spec": {
        1: {"mexico": ["San Luis Potosí"]}
    }
}

GUANAJUATO = {
    "name": "Guanajuato",
    "code": "GUA",
    "entity_type": "Provincia",
    "capitals": ["Guanajuato"],
    "spec": {
        1: {"mexico": ["Guanajuato"]},
        #2: {"mexico": QUERETARO_A_GUANAJUATO}
    }
}

GUADALAJARA_DE_JALISCO = {
    "name": "Guadalajara de Jalisco",
    "code": "GJA",
    "entity_type": "Provincia",
    "capitals": ["Guadalajara"],
    "spec": {
        1: {"mexico": ["Jalisco", "Colima", "Nayarit"]},
        2: {"mexico": MINCHOACAN_A_GUADALAJARA},
        "restar": {2: {"mexico": COLIMA_A_MINCHOACAN}}
    }
}

TABASCO = {
    "name": "Tabasco",
    "code": "TAB",
    "entity_type": "Provincia",
    "capitals": ["Villahermosa"],
    "spec": {
        1: {"mexico": ["Tabasco"]},
        "restar": {2: {"mexico": TABASCO_A_VERACRUZ}},
    }
}

CARMEN = {
    "name": "Carmen",
    "code": "CAR",
    "entity_type": "Provincia",
    "capitals": ["Carmen"],
    "spec": {
        2: {"mexico": CAMPECHE_A_CARMEN},
    }
}

MERIDA_DE_YUCATAN = {
    "name": "Mérida de Yucatán",
    "code": "MYU",
    "entity_type": "Provincia",
    "capitals": ["Mérida"],
    "spec": {
        1: {"mexico": ["Quintana Roo", "Yucatán", "Campeche"]},
        "restar": {2: {"mexico": CAMPECHE_A_GUATEMALA + CAMPECHE_A_CARMEN}},
    }
}

MEXICO = {
    "name": "México",
    "code": "MEX",
    "entity_type": "Provincia",
    "capitals": ["Ciudad de México"],
    "spec": {
        1: {"mexico": ["México", "Ciudad de México", "Hidalgo", "Querétaro de Arteaga", "Morelos", "Guerrero"]},
        #2: {"mexico":}
        "restar": {2: {"mexico": GUERRERO_A_PUEBLA + GUERRERO_A_VALLADOLID + MORELOS_A_PUEBLA
                           #QUERETARO_A_GUANAJUATO
                       }},
    }
}

VERACRUZ = {
    "name": "Veracruz",
    "code": "VER",
    "entity_type": "Provincia",
    "capitals": ["Veracruz"],
    "spec": {
        1: {"mexico": ["Veracruz"]},
        2: {"mexico": TABASCO_A_VERACRUZ },
        "restar": {2: {"mexico": VERACRUZ_A_OAXACA + VERACRUZ_A_PUEBLA}},
    }
}

CHIAPAS = {
    "name": "Chiapas",
    "code": "CHI",
    "entity_type": "Provincia",
    "capitals": ["San Cristóbal de Las Casas"],
    "spec": {
        1: {"mexico": ["Chiapas"]},
        2: {"guatemala": GUATEMALA_A_CHIAPAS},
        3: {"mexico": CHIAPAS_A_EXCLUIR_A_GUATEMALA},
        "restar": {2: {"mexico": CHIAPAS_A_GUATEMALA}},
    }
}

MINCHOACAN = {
    "name": "Valladolid de Michoacán",
    "code": "VMI",
    "entity_type": "Provincia",
    "capitals": ["Morelia"],
    "spec": {
        1: {"mexico": ["Michoacán de Ocampo"]},
        2: {"mexico": GUERRERO_A_VALLADOLID + COLIMA_A_MINCHOACAN},
        "restar": {2: {"mexico": MINCHOACAN_A_GUADALAJARA}},
    }
}

PUEBLA = {
    "name": "Puebla",
    "code": "PUE",
    "entity_type": "Provincia",
    "capitals": ["Puebla"],
    "spec": {
        1: {"mexico": ["Puebla"]},
        2: {"mexico": GUERRERO_A_PUEBLA + MORELOS_A_PUEBLA + VERACRUZ_A_PUEBLA},
        #"restar": {2: {"mexico": }},
    }
}

TAXACLA = {
    "name": "Tlaxcala",
    "code": "TXC",
    "entity_type": "Provincia",
    "capitals": ["Tlaxcala"],
    "spec": {
        1: {"mexico": ["Tlaxcala"]},
    }
}

#==================== EEUU ==============================

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
from copy import deepcopy

from ciudades_del_mundo.models import NuevoAdminArea
from ciudades_del_mundo.historical_divisions.espanha import *
from ciudades_del_mundo.historical_divisions.portugal import *
from ciudades_del_mundo.historical_divisions.cuba import *
from ciudades_del_mundo.historical_divisions.italy import *
from ciudades_del_mundo.historical_divisions.eeuu import *
from ciudades_del_mundo.historical_divisions.mexico import *
from ciudades_del_mundo.historical_divisions.netherlands import *
from ciudades_del_mundo.historical_divisions.colombia import *
from ciudades_del_mundo.historical_divisions.ecuatorialguinea import *
from ciudades_del_mundo.historical_divisions.centroamerica import *


CAPITAL_NAMES_ES = {
    "Vitoria-Gasteiz": "Vitoria",
    "Donostia": "San Sebastián",
    "València": "Valencia",
    "Angra do Heroísmo": "Angra del Heroísmo",
    "Cagliari": "Cáller",
    "Napoli": "Nápoles",
    "Tanger": "Tánger",
    "El Jadida": "Mazagán",
    "Assilah": "Arcila",
    "Monterey": "Monterrey",
}


def _with_code(division: dict, code: str) -> dict:
    result = deepcopy(division)
    result["code"] = code
    return result


def _depends_on(division: dict, target_code: str) -> dict:
    result = deepcopy(division)
    result["depends_on"] = target_code
    return result


def _capital_label(capital) -> str:
    if isinstance(capital, dict):
        for key in ("label", "name", "id", "code"):
            value = capital.get(key)
            if value not in (None, ""):
                return str(value)
    return str(capital)


def _capital_es(capital):
    label = _capital_label(capital)
    translation = CAPITAL_NAMES_ES.get(label)
    if translation is None and not isinstance(capital, dict):
        return capital

    result = deepcopy(capital) if isinstance(capital, dict) else {"label": label}
    names = dict(result.get("names_by_language") or result.get("names") or {})
    if translation is not None:
        names["es"] = translation
    if names:
        result["names_by_language"] = names
    return result


def _with_spanish_capitals(value):
    if isinstance(value, list):
        return [_with_spanish_capitals(item) for item in value]
    if not isinstance(value, dict):
        return value

    result = deepcopy(value)
    if "capitals" in result:
        result["capitals"] = [_capital_es(capital) for capital in result.get("capitals") or []]
    if "childs" in result:
        result["childs"] = _with_spanish_capitals(result.get("childs") or [])
    return result


CASTILLA_PROVINCIAS = _with_spanish_capitals([
    ALAVA_B,
    VIZCAYA,
    GUIPUZCOA,
    SEVILLA_B,
    CORDOBA_B,
    JAEN_B,
    NUEVAS_POBLACIONES,
    ASTURIAS,
    GALICIA,
    LEON_B,
    ZAMORA_B,
    TORO_B,
    SALAMANCA_B,
    EXTREMADURA,
    NAVARRA_B,
    BURGOS_B,
    SORIA_B,
    SEGOVIA_B,
    AVILA_B,
    PALENCIA,
    _with_code(VALLADOLID_B, "VLL"),
    _with_code(MADRID_B, "MDR"),
    GUADALAJARA_B,
    TOLEDO_B,
    LA_MANCHA,
    CUENCA_B,
    MURCIA_B,
    GRANADA_B,
    CANARIAS,
])

ARAGON_PROVINCIAS = _with_spanish_capitals([
    ARAGON,
    CATALUNHA_A,
    VALENCIA_B,
    MALLORCA,
    CERDENHA,
])

PORTUGAL_PROVINCIAS = _with_spanish_capitals([
    ENTRE_EL_DUERO_Y_MINHO,
    DETRAS_DE_LOS_MONTES_Y_ALTO_DUERO,
    BEIRA,
    ESTREMADURA,
    ALENTEJO,
    ALGARVE,
])

BORGONHA_PROVINCIAS = _with_spanish_capitals([
    ARTOIS,
    FLANDES,
    UTRIQUE,
    HENAO,
    CAMBRAI,
    DRENTE,
    GRONINGA,
    FRISIA,
    HOLANDA,
    ZELANDA,
    LUXEMBURGO,
])

DIVISIONS = _with_spanish_capitals([
    {
        "name": "Corona de Castilla",
        "code": "CAS",
        "entity_type": "Corona",
        "capitals": ["Madrid"],
        "childs": CASTILLA_PROVINCIAS,
    },
    {
        "name": "Corona de Aragón",
        "code": "ARA",
        "entity_type": "Corona",
        "capitals": ["Zaragoza", "Barcelona"],
        "childs": ARAGON_PROVINCIAS,
    },
    {
        "name": "Corona de Portugal",
        "code": "POR",
        "entity_type": "Corona",
        "capitals": ["Lisboa"],
        "childs": PORTUGAL_PROVINCIAS,
    },
    {
        "name": "Nueva España",
        "code": "NES",
        "entity_type": "Virreinato",
        "capitals": ["Ciudad de México"],
        "childs": [
            NUEVO_MEXICO,
            VIEJA_CALIFORNIA,
            NUEVA_CALIFORNIA,
            NUTKA,
            TEXAS,
            NUEVA_EXTREMADURA,
            NUEVO_SANTANDER,
            NUEVA_VIZCAYA,
            NUEVO_LEON,
            NUEVA_NAVARRA,
            OAXACA,
            ZACATECAS,
            SAN_LUIS_POTOSI,
            GUANAJUATO,
            GUADALAJARA_DE_JALISCO,
            MEXICO,
            VERACRUZ,
            CHIAPAS,
            MINCHOACAN,
            PUEBLA,
            TAXACLA
        ],
    },
    {
        "name": "Italia",
        "code": "ITA",
        "entity_type": "Virreinato",
        "capitals": ["Napoli"],
        "childs": [
            SICILIA,
            REINO_NAPOLES,
            MILAN,
            _depends_on(PIOMBINO, "ITA-NAP"),
            _depends_on(PRESIDIOS_DE_TOSCANA, "ITA-NAP"),
        ],
    },
    {
        "name": "Estado Borgoñón",
        "code": "BOR",
        "entity_type": "Estado",
        "childs": BORGONHA_PROVINCIAS,
    },
    {
        "name": "África",
        "code": "AFR",
        "entity_type": "Provincia imperial",
        "capitals": ["Tanger"],
        "province_status": "territorio",
        "childs": [MAZAGAN, CASABLANCA, ARCILA, TANGER, CEUTA, MELILLA, ORAN],
    },
    {
        "name": "Perú",
        "code": "PER",
        "entity_type": "Virreinato",
        "childs": [],
    },
    {
        "name": "Nueva Granada",
        "code": "NGR",
        "entity_type": "Virreinato",
        "childs": [
            PANAMA,
            DARIEN,
            VERAGUAS,
            PORTOBELO
        ],
    },
    {
        "name": "Brasil",
        "code": "BRA",
        "entity_type": "Estado",
        "childs": [],
    },
    {
        "name": "Rio de la Plata",
        "code": "RPL",
        "entity_type": "Virreinato",
        "childs": [
            FERNANDO_POO_Y_ANNOBON
        ],
    },
    {
        "name": "Chile",
        "code": "CHI",
        "entity_type": "Capitanía General",
        "childs": [],
    },
    {
        "name": "Marañao",
        "code": "MAR",
        "entity_type": "Estado",
        "childs": [],
    },
    {
        "name": "Guatemala",
        "code": "GUA",
        "entity_type": "Capitanía General",
        "capitals": ["Guatemala"],
        "childs": [
            CHIAPAS,
            GUATEMALA,
            SAN_SALVADOR,
            NICARAGUA,
            COSTA_RICA,
        ],
    },
    {
        "name": "Cuba",
        "code": "CUB",
        "entity_type": "Capitanía General",
        "capitals": ["La Habana"],
        "childs": [
            DIVISIONS_XVII,
            FLORIDA_ORIENTAL,
            FLORIDA_OCCIDENTAL,
            LUISIANA
        ],
    },
    {
        "name": "Puerto Rico",
        "code": "PRI",
        "entity_type": "Capitanía General",
        "capitals": ["San Juan"],
        "childs": [
            {
                "name": "Puerto Rico",
                "code": "PRI",
                "entity_type": "Capitanía General",
                "capitals": ["San Juan"],
                "spec": {
                    0: {"puertorico": ["Puerto Rico"]},
                },
            }
        ]
    },
    {
        "name": "Santo Domingo",
        "code": "SDO",
        "entity_type": "Capitanía General",
        "childs": [],
    },
    {
        "name": "Yucatán",
        "code": "YUC",
        "entity_type": "Capitanía General",
        "childs": [
            TABASCO,
            CARMEN,
            MERIDA_DE_YUCATAN,
        ],
    },
    {
        "name": "Filipinas",
        "code": "FIL",
        "entity_type": "Capitanía General",
        "childs": [],
    },
    {
        "name": "Venezuela",
        "code": "VEN",
        "entity_type": "Capitanía General",
        "childs": [],
    },
])

ESCANHOS = {
    "nivel": NuevoAdminArea.Level.ADMIN2,
    "escanhos": 800,
    "min": 1,
}

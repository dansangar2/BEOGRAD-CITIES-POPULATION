from ciudades_del_mundo.historical_divisions.france_provinces import *


DIVISIONS = [
    {
        "name": "Francia",
        "code": "FRA",
        "entity_type": "Reino",
        "capitals": ["Paris"],
        "childs": [
            ISLA_DE_FRANCIA,
        #    BERRY,
        #    ORLEANESADO,
            NORMANDIA,
            LANGUEDOC,
        #    LIONESADO,
            DELFINADO,
            CAMPANHA,
            AUNIS,
            SAINTIONGE,
            POITOU,
            AQUITANIA,
        #    BORGONHA,
            PICARDIA,
            ANJOU,
            PROVENZA,
            ANGOUMOIS,
        #    BORBONES,
            MARCHE,
            BRETANHA,
        #    MAINE_Y_PERCHE,
            TURENA,
            LIMOSIN,
            FOIX,
        #    AUVERNIA,
            BEARNE,
        #    ALSACIA,
            ARTOIS,
            ROSELLON,
            FLANDES_Y_HENAO,
        #    FRANCO_CONDADO,
        #    LORENA_Y_BARROIS,
            CORCEGA,
        #    NIERVES,
        #    CONDADO_VENESINO,
        #    MULHOUSE,
        #    SABOYA,
        #    NIZA,
        #    MONTBELIARD,
        #    MENTON,
        #    ROCABRUNA,
        #    TENDE
        ]
    }
]

ESCANHOS = {
        "nivel": 2,  # provincias del imperio
        "escanhos": 100,                        # total de escaños a repartir
        "min": 1,                              # mínimo por provincia
    }
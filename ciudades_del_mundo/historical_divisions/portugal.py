from ciudades_del_mundo.historical_divisions.espanha  import OLIVENZA

AVEIRO_A_DUERO_LITORAL = ["Arouca", "Castelo de Paiva", "Espinho", "Santa Maria da Feira"]
VISEU_A_DUERO_LITORAL = ["Cinfães", "Resende"]

VISEU_A_TRAS_LOS_MONTES = ["Armamar", "Lamego", "São João da Pesqueira", "Tabuaço"]
GUARDA_A_TRAS_LOS_MONTES = ["Vila Nova de Foz Côa"]
SANTAREM_A_BEIRA = ["Ourém", "Mação"]
SETUBAL_A_ALENTEJO = ["Alcácer do Sal", "Grândola", "Santiago do Cacém", "Sines"]
PORTALEGRE_A_ESTREMADURA = ["Ponte de Sor"]
LEIRIA_A_ESTREMADURA = ["Alcobaça", "Bombarral", "Caldas da Rainha", "Marinha Grande", "Nazaré", "Óbidos", "Peniche", "Porto de Mós"]


VISEU_A_VILA_REAL = ["Armamar", "Lamego", "São João da Pesqueira", "Tabuaço"]
GUARDA_A_VILA_REAL = ["Vila Nova de Foz Côa"]
COIMBRA_A_BEIRA_ALTA = ["Oliveira do Hospital", "Tábua"]
COIMBRA_A_BEIRA_BAJA = ["Pampilhosa da Serra"]
SANTAREM_A_BEIRA_LITORAL = ["Ourém"]
SANTAREM_A_CASTELO_BRANCO = ["Mação"]
LEIRIA_A_ESTREMADURA_2 = ["Alcobaça", "Bombarral", "Caldas da Rainha", "Marinha Grande", "Nazaré", "Óbidos", "Peniche", "Porto de Mós"]
LISBOA_A_RIBATEJO = ["Arruda dos Vinhos", "Azambuja", "Vila Franca de Xira"]
SETUBAL_A_BAJO_ALENTEJO = ["Alcácer do Sal", "Grândola", "Santiago do Cacém", "Sines"]
PORTALEGRE_A_RIBATEJO = ["Ponte de Sor"]

### ISLAS

# -------------------------
# AZORES (Açores) - por islas
# -------------------------

SAO_MIGUEL = [
    "Lagoa",
    "Nordeste",
    "Ponta Delgada",
    "Povoação",
    "Ribeira Grande",
    "Vila Franca do Campo",
]

TERCEIRA = [
    "Angra do Heroísmo",
    "Vila da Praia da Vitória",
]

PICO = [
    "Lajes do Pico",
    "Madalena",
    "São Roque do Pico",
]

FAIAL = [
    "Horta",
]

SAO_JORGE = [
    "Calheta",
    "Velas",
]

GRACIOSA = [
    "Santa Cruz da Graciosa",
]

FLORES = [
    "Lajes das Flores",
    "Santa Cruz das Flores",
]

CORVO = [
    "Corvo",
]

SANTA_MARIA = [
    "Vila do Porto",
]


# -------------------------
# MADEIRA - por islas
# -------------------------

ILHA_DA_MADEIRA = [
    "Calheta",
    "Câmara de Lobos",
    "Funchal",
    "Machico",
    "Ponta do Sol",
    "Porto Moniz",
    "Ribeira Brava",
    "Santa Cruz",
    "Santana",
    "São Vicente",
]

PORTO_SANTO = [
    "Porto Santo",
]

### FULL =========================================

PORTUGAL = {
    "name": "Portugal",
    "code": "POR",
    "entity_type": "Reino",
    "capitals": ["Lisboa"],
    "spec": {
        0: {"portugal": ["Portugal"]},
        2:{"spain": ["Ceuta"]},
        3: {"spain": OLIVENZA, "morocco": ["Tanger", "Ksar Sghir", "Assilah", "Casablanca", "El Jadida"]}
    }
}

### ALENTEJO =====================================

ALENTEJO = {
        "name": "Alentejo",
        "code": "ALE",
        "entity_type": "Provincia",
        "capitals": ["Beja"],
        "spec": {
            1: {"portugal": ["Beja", "Évora", "Portalegre"]},
            2: {"portugal": SETUBAL_A_ALENTEJO}, 
            3: {"spain": OLIVENZA},
            "restar": {2:{"portugal": PORTALEGRE_A_ESTREMADURA}}
        }
    }

BAJO_ALENTEJO = {
        "name": "Beja",
        "code": "BAL",
        "entity_type": "Provincia",
        "capitals": ["Beja"],
        "spec": {
            1: {"portugal": ["Beja"]},
            2: {"portugal": SETUBAL_A_BAJO_ALENTEJO},
        }
    }

ALTO_ALENTEJO = {
        "name": "Évora",
        "code": "AAL",
        "entity_type": "Provincia",
        "capitals": ["Évora"],
        "spec": {
            1: {"portugal": ["Évora", "Portalegre"]},
            "restar": {2:{"portugal": PORTALEGRE_A_RIBATEJO}}
        }
    }

ALTO_ALENTEJO_C_OLIVENZA = {
        "name": "Évora",
        "code": "AAL",
        "entity_type": "Provincia",
        "capitals": ["Évora"],
        "spec": {
            1: {"portugal": ["Évora", "Portalegre"]},
            3: {"spain": OLIVENZA},
            "restar": {2:{"portugal": PORTALEGRE_A_RIBATEJO}}
        }
    }

### BEIRA ========================================

BEIRA = {
        "name": "Beira",
        "code": "BEI",
        "entity_type": "Provincia",
        "capitals": ["Coímbra"],
        "spec": {
            1: {"portugal": ["Coimbra", "Aveiro", "Leiría", "Castelo Branco", "Viseu", "Guarda"]},
            2: {"portugal": (SANTAREM_A_BEIRA)},
            "restar": {2: {"portugal": ( AVEIRO_A_DUERO_LITORAL + LEIRIA_A_ESTREMADURA + GUARDA_A_TRAS_LOS_MONTES + VISEU_A_TRAS_LOS_MONTES + VISEU_A_DUERO_LITORAL)}},
        },
    }

BEIRA_ALTA = {
        "name": "Beira Alta",
        "code": "BEA",
        "entity_type": "Provincia",
        "capitals": ["Viseu"],
        "spec": {
            1: {"portugal": ["Viseu", "Guarda"]},
            2: {"portugal": COIMBRA_A_BEIRA_ALTA },
            "restar": {2: {"portugal": GUARDA_A_VILA_REAL + VISEU_A_VILA_REAL + VISEU_A_DUERO_LITORAL}},
        }
    }

BEIRA_BAJA = {
        "name": "Beira Baja",
        "code": "BEB",
        "entity_type": "Provincia",
        "capitals": ["Castelo Branco"],
        "spec": {
            1: {"portugal": ["Castelo Branco"]},
            2: {"portugal": COIMBRA_A_BEIRA_BAJA + SANTAREM_A_CASTELO_BRANCO},
        }
    }
                
BEIRA_LITORAL = {
        "name": "Beira Litoral",
        "code": "BEL",
        "entity_type": "Provincia",
        "capitals": ["Coímbra"],
        "spec": {
            1: {"portugal": ["Coimbra", "Aveiro", "Leiría"]},
            2: {"portugal": SANTAREM_A_BEIRA_LITORAL},
            "restar": {2: {"portugal": (COIMBRA_A_BEIRA_BAJA + COIMBRA_A_BEIRA_ALTA + AVEIRO_A_DUERO_LITORAL + LEIRIA_A_ESTREMADURA_2)}},
        }
    }

### ALGARVE ============================================

ALGARVE = {
        "name": "Algarbe",
        "code": "ALG",
        "entity_type": "Reino",
        "capitals": ["Faro"],
        "spec": {
            1: {"portugal": ["Faro"]},
        },
    }

### ESTREMADURA =======================================

ESTREMADURA = {
        "name": "Extremadura",
        "code": "EXT",
        "entity_type": "Provincia",
        "capitals": ["Lisboa"],
        "spec": {
            1: {"portugal": ["Lisboa", "Setúbal", "Santarém"]},
            2: {"portugal": LEIRIA_A_ESTREMADURA + PORTALEGRE_A_ESTREMADURA},
            "restar": {2: {"portugal": SETUBAL_A_ALENTEJO + SANTAREM_A_BEIRA}}
        },
    }

ESTREMADURA_2 = {
        "name": "Lisboa",
        "code": "EXT",
        "entity_type": "Provincia",
        "capitals": ["Lisboa"],
        "spec": {
            1: {"portugal": ["Lisboa", "Setúbal"]},
            2: {"portugal": LEIRIA_A_ESTREMADURA_2},
            "restar": {2: {"portugal": LISBOA_A_RIBATEJO + SETUBAL_A_BAJO_ALENTEJO}}
        }
    }

RIBATEJO = {
        "name": "Ribatejo",
        "code": "RIB",
        "entity_type": "Provincia",
        "capitals": ["Santarém"],
        "spec": {
            1: {"portugal": "Santarém"},
            2: {"portugal": LISBOA_A_RIBATEJO + PORTALEGRE_A_RIBATEJO},
            "restar": {2: {"portugal": SANTAREM_A_CASTELO_BRANCO + SANTAREM_A_BEIRA_LITORAL}},
        }
    }

### ENTRE EL DUERO Y MIÑO =============================

ENTRE_EL_DUERO_Y_MINHO = {
        "name": "Entre Duero y Miño",
        "code": "EDM",
        "entity_type": "Provincia",
        "capitals": ["Braga"],
        "spec": {
            1: {"portugal": ["Braga", "Porto", "Viana do Castelo"]},
            2: {"portugal": AVEIRO_A_DUERO_LITORAL + VISEU_A_DUERO_LITORAL },
        },
    }

MINHO = {
        "name": "Miño",
        "code": "MIN",
        "entity_type": "Provincia",
        "capitals": ["Braga"],
        "spec": {
            1: {"portugal": ["Braga", "Viana do Castelo"]},
        },
    }

DUERO_LITORAL = {
        "name": "Duero Litoral",
        "code": "DUL",
        "entity_type": "Provincia",
        "capitals": ["Porto"],
        "spec": {
            1: {"portugal": ["Porto"]},
            2: {"portugal": AVEIRO_A_DUERO_LITORAL + VISEU_A_DUERO_LITORAL},
        },
    }

### ENTRE LO MONTES Y ALTO DUERO ===========================

DETRAS_DE_LOS_MONTES_Y_ALTO_DUERO = {
        "name": "Detrás de los Montes y Alto Duero",
        "code": "DMD",
        "entity_type": "Provincia",
        "capitals": ["Vila Real"],
        "spec": {
            1: {"portugal": ["Vila Real", "Bragança"]},
            2: {"portugal": VISEU_A_TRAS_LOS_MONTES + GUARDA_A_TRAS_LOS_MONTES},
        },
    }

### ÁFRICA =================================================

AZORES = {
        "name": "Azores",
        "code": "AZO",
        "entity_type": "Capitanía General",
        "capitals": ["Angra do Heroísmo"],
        "spec": {1: {"portugal": ["Açores"]},},
    }

MADEIRA = {
        "name": "Madeira",
        "code": "MAD",
        "entity_type": "Capitanía General",
        "capitals": ["Funchal"],
        "spec": {
            1: {"portugal": ["Madeira"]},
        },
    }

TANGER = {
        "name": "Tanger",
        "code": "TAN",
        "entity_type": "Presidio",
        "capitals": ["Tanger"],
        "spec": {
            3: {"morocco": "Tanger"}
        }
    }

ALCAZARSEGUIR = {
        "name": "Alcazarseguir",
        "code": "ALC",
        "entity_type": "Presidio",
        "capitals": ["Ksar Sghir"],
        "spec": {
            3: {"morocco": "Ksar Sghir"}
        }
    }

ARCILA = {
        "name": "Arcila",
        "code": "ARC",
        "entity_type": "Presidio",
        "capitals": ["Assilah"],
        "spec": {
            3: {"morocco": "Assilah"}
        }
    }

CASABLANCA = {
        "name": "Casablanca",
        "code": "CAS",
        "entity_type": "Presidio",
        "capitals": ["Casablanca"],
        "spec": {
            3: {"morocco": "Casablanca"}
        }
    }

MAZAGAN = {
        "name": "Mazagán",
        "code": "MAZ",
        "entity_type": "Presidio",
        "capitals": ["El Jadida"],
        "spec": {
            3: {"morocco": "El Jadida"}
        }
    }
                
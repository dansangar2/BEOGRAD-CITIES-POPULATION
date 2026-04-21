BRINDISI_A_TIERRA_DE_BARI = ["Cisternino", "Fasano"]
ANDRIA_TRANI_A_CAPITANIA = ["San Ferdinando di Puglia", "Trinitapoli", "Margherita di Savoia"]
FOGGIA_A_PRINCIPATO_ULTRA = ["Rocchetta Sant'Antonio"]
NAPOLES_A_PRINCIPADO_CITRA = ["Gragnano", "Lettere", "Pimonte", "Agerola", "Casola di Napoli"]
AVELLINO_A_PRINCIPADO_CITRA = ["Calabritto", "Senerchia", "Sant'Andrea di Conza", 
                               "Solofra", "Montoro", "Contrada", "Forino", "Quindici","Contrada"]
AVELLINO_A_TIERRA_DE_TRABAJO = ["Lauro","Domicella","Marzano di Nola", 
                                "Baiano","Mugnano del Cardinale", ]
AVELLINO_A_CAPITANIA = ["Montaguto", "Greci"]
BENEVENTO = ["Benevento"]
BENEVENTO_A_CAPITANIA = ["San Bartolomeo in Galdo", "Castelfranco in Miscano", "Ginestra degli Schiavoni", "Montefalcone di Val Fortore"]
BENEVENTO_A_MOLISE = ["Morcone", "Sassinoro", "Pietraroja", "Pontelandolfo", "San Lupo"]
BENEVENTO_A_TIERRA_DE_TRABAJO = ["Cusano Mutri", "Cerreto Sannita", "San Lorenzo Maggiore", "Guardia Sanframondi",
                                 "San Lorenzello", "Castelvenere", "Faicchio", "San Salvatore Telesino",
                                 "Puglianello", "Telese Terme", "Amorosi", "Solopaca", "Melizzano", "Frasso Telesino",
                                 "Dugenta", "Limatola", "Forchia", "Arpaia", "Paolisi"]
ISERNIA_A_TIERRA_DE_TRABAJO = [
    "Sesto Campano", "Venafro", "Conca Casale", "Pozzilli", "Montaquila","Filignano", "Scapoli", "Colli a Volturno",
    "Rocchetta a Volturno", "Cerro al Volturno", "Castel San Vincenzo", "Pizzone", "Montenero Val Cocchiara"
]
CHIETI_A_MOLISE = ["San Giovanni Lipioni"]
PESCARA_A_ABRUZOS_ULTRA_II = ["Sant'Eufemia a Maiella","Popoli", "Bussi sul Tirino"]
PESCARA_A_ABRUZOS_CITRA = ["Caramanico Terme", "Salle", "Tocco da Casauria", "Castiglione a Casauria",
                             "Pescosansonesco", "Corvara", "Pietranico", "Torre de' Passeri", "Bolognano",
                             "Alanno", "Scafa", "San Valentino in Abruzzo Citeriore", "Abbateggio",
                             "Roccamorice", "Lettomanoppello", "Turrivalignani", "Manoppello", "Serramonacesca",
                             "Rosciano", "Cepagatti", "Spoltore", "Pescara"]
AGUILA_A_ABRUZOS_CITRA = ["Ateleta"]
AGUILA_A_TIERRA_DE_TRABAJO = ["Castel di Sangro", "Scontrone", "Alfedena", "Barrea", "Villetta Barrea",
                              "Civitella Alfedena", "Opi", "Pescasseroli", "Villavallelonga", "Balsorano",
                              "Collelongo", "San Vincenzo Valle Roveto", "Civita d'Antino", "Morino"]
RIETI_A_ABRUZOS_ULTRA_II = [
    "Borgorose", "Pescorocchiano", "Fiamignano", "Petrella Salto", "Cittaducale", "Castel Sant'Angelo", "Borgo Velino",
    "Antrodoco", "Micigliano", "Posta", "Borbona", "Leonessa", "Cittareale", "Amatrice", "Accumoli"
]

LATINA_A_TIERRA_DE_TRABAJO = ["Castelforte", "Santi Cosma e Damiano", "Minturno", "Spigno Saturnia",
                              "Formia", "Gaeta", "Itri", "Campodimele", "Lenola", "Fondi", "Sperlonga",
                              "Monte San Biagio", "Ponza", "Ventotene"]
FROSIONE_A_ESTADOS_PONTIFICIOS = ["Filettino", "Trevi nel Lazio", "Guarcino", "Vico nel Lazio", "Collepardo",
                                  "Alatri", "Fumone", "Trivigliano", "Torre Cajetani", "Ferentino", "Anagni",
                                  "Acuto", "Piglio", "Serrone", "Paliano", "Sgurgola", "Morolo", "Supino",
                                  "Patrica", "Giuliano di Roma", "Villa Santo Stefano", "Amaseno", "Ceccano",
                                  "Frosinone"]
PONTECORVO = ["Pontecorvo"]

CALABRIA_ULTRA = {
            "name": "Calabria Ultra",
            "code": "CAU",
            "entity_type": "Magistrado",
            "capitals": [
                "Reggio di Calabria", 
                #"Catanzaro"
            ],
            "spec": {
                2:{"italy": ["Reggio di Calabria", "Catanzaro", "Crotone", "Vibo Valentia"]},
            }
        }

CALABRIA_CITRA = {
            "name": "Calabria Citra",
            "code": "CAC",
            "entity_type": "Magistrado",
            "capitals": ["Cosenza"],
            "spec": {
                2:{"italy": ["Cosenza"]},
            }
        }

BASILICATA = {
            "name": "Basilicata",
            "code": "BAS",
            "entity_type": "Magistrado",
            "capitals": [
                "Lauria",
                #"Lagonegro",
                #"Potenza",
                #"Stigliano",
                #"Tolve",
                #"Tursi",
                #"Pignola",
                #"Matera", # <---
                #"Irsina"
            ],
            "spec": {
                1:{"italy": ["Basilicata"]},
            }
        }

TERRA_DE_OTRANTO = {
            "name": "Tierra de Otranto",
            "code": "TOT",
            "entity_type": "Magistrado",
            "capitals": [
                "Lecce",
                #"Otranto",
            ],
            "spec": {
                2:{"italy": ["Lecce", "Brindisi", "Taranto"]},
                "restar": {3: {"italy": BRINDISI_A_TIERRA_DE_BARI}},
            }
}

TERRA_DE_BARI = {
            "name": "Tierra de Bari",
            "code": "TBA",
            "entity_type": "Magistrado",
            "capitals": [
                "Bari",
                #"Trani",
            ],
            "spec": {
                2: {"italy": ["Bari", "Barletta-Andria-Trani"]},
                3: {"italy": BRINDISI_A_TIERRA_DE_BARI},
                "restar": {3: {"italy": ANDRIA_TRANI_A_CAPITANIA }},
            }
}

CAPITANIA = {
            "name": "Capitania",
            "code": "CAP",
            "entity_type": "Magistrado",
            "capitals": [
                #"Lucera",
                #"San Severo", 
                "Foggia",
            ],
            "spec": {
                2: {"italy": ["Foggia"]},
                3: {"italy": BENEVENTO_A_CAPITANIA + ANDRIA_TRANI_A_CAPITANIA + ANDRIA_TRANI_A_CAPITANIA},
                "restar": {3: {"italy": FOGGIA_A_PRINCIPATO_ULTRA }},
            }
}

PRINCIPADO_CITRA = {
            "name": "Principado Citra",
            "code": "PCI",
            "entity_type": "Magistrado",
            "capitals": [
                "Salerno",
            ],
            "spec": {
                2: {"italy": ["Salerno"]},
                3: {"italy": NAPOLES_A_PRINCIPADO_CITRA + AVELLINO_A_PRINCIPADO_CITRA},
            }
}

PRINCIPADO_ULTRA = {
            "name": "Principado Ultra",
            "code": "PUL",
            "entity_type": "Magistrado",
            "capitals": [
                "Montefusco", 
                #"Avellino",
            ],
            "spec": {
                2: {"italy": ["Avellino", "Benevento"]},
                3: {"italy": FOGGIA_A_PRINCIPATO_ULTRA},
                "restar": {3: {"italy": BENEVENTO_A_CAPITANIA+ BENEVENTO_A_MOLISE + BENEVENTO_A_TIERRA_DE_TRABAJO + BENEVENTO + AVELLINO_A_CAPITANIA + AVELLINO_A_PRINCIPADO_CITRA + AVELLINO_A_TIERRA_DE_TRABAJO }},
            }
}

TIERRA_DE_TRABAJO = {
            "name": "Tierra de Trabajo",
            "code": "TIT",
            "entity_type": "Magistrado",
            "capitals": [
                #"Santa Maria", 
                "Capua",
                #"Caserta",
            ],
            "spec": {
                2: {"italy": ["Caserta", "Napoli", "Frosinone"]},
                3: {"italy": PONTECORVO + LATINA_A_TIERRA_DE_TRABAJO + AGUILA_A_TIERRA_DE_TRABAJO + ISERNIA_A_TIERRA_DE_TRABAJO + BENEVENTO_A_TIERRA_DE_TRABAJO + AVELLINO_A_TIERRA_DE_TRABAJO},
                "restar": {3: {"italy": FROSIONE_A_ESTADOS_PONTIFICIOS + NAPOLES_A_PRINCIPADO_CITRA }},
            }
}

MOLISE = {
            "name": "Molise",
            "code": "MOL",
            "entity_type": "Condado",
            "capitals": [
                "Bojano",
                #"Campobasso",
            ],
            "spec": {
                2: {"italy": ["Campobasso", "Isernia"]},
                3: {"italy": CHIETI_A_MOLISE + BENEVENTO_A_MOLISE},
                "restar": {3: {"italy": ISERNIA_A_TIERRA_DE_TRABAJO }},
            }
}

ABRUZOS_CITRA = {
            "name": "Abruzos Citra",
            "code": "ACI",
            "entity_type": "Magistrado",
            "capitals": [
                "Chieti",
            ],
            "spec": {
                2: {"italy": ["Chieti"]},
                3: {"italy": AGUILA_A_ABRUZOS_CITRA + PESCARA_A_ABRUZOS_CITRA},
                "restar": {3: {"italy": CHIETI_A_MOLISE }},
            }
}

ABRUZOS_ULTRA = {
            "name": "Abruzos Ultra",
            "code": "AUL",
            "entity_type": "Magistrado",
            "capitals": [
                "L'Aquila",
            ],
            "spec": {
                2: {"italy": ["Pescara", "Teramo", "L'Aquila"]},
                3: {"italy":  PESCARA_A_ABRUZOS_CITRA + PESCARA_A_ABRUZOS_ULTRA_II},
                "restar": {3: {"italy": AGUILA_A_ABRUZOS_CITRA + AGUILA_A_TIERRA_DE_TRABAJO }},
            }
}

CERDENHA = {
            "name": "Cerdeña",
            "code": "CER",
            "entity_type": "Reino",
            "capitals": ["Cagliari"],
            "spec": {
                1:{"italy": "Sardegna"},
            }
        }

SICILIA = {
            "name": "Sicilia",
            "code": "SIL",
            "entity_type": "Reino",
            "capitals": ["Palermo"],
            "spec": {
                0: {"malta": "malta"},
                1: {"italy": "Sicilia"},
            }
        }
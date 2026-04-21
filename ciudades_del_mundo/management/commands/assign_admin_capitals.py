# ciudades_del_mundo/management/commands/assign_admin_capitals.py

from django.core.management.base import BaseCommand
from ciudades_del_mundo.services.adminarea_capitals import (
    assign_capitals_and_biggest_city_from_map,
)

# Ejemplo: importa tu config desde un módulo aparte (recomendado)
# from ciudades_del_mundo.configs.es_capitales import CAPITAL_MAP

CAPITAL_MAP = {
    "spain": {
    # ESPAÑA CCAA
    "spain_AND": "spain_41091",
    "spain_ARA": "spain_50297",
    "spain_AST": "spain_33044",
    "spain_CAN": ["spain_35016", "spain_38038"],
    "spain_CAR": "spain_39075",
    "spain_CLM": "spain_45168",
    "spain_CLE": "spain_47186",
    "spain_CAT": "spain_08019",
    "spain_CEU": "spain_51001",
    "spain_VAL": "spain_46250",
    "spain_EXT": "spain_06083",
    "spain_GAL": "spain_15078",
    "spain_BAL": "spain_07040",
    "spain_LAR": "spain_26089",
    "spain_MAD": "spain_28079",
    "spain_MEL": "spain_52001",
    "spain_MUR": "spain_30030",
    "spain_NAV": "spain_31201",
    "spain_PAI": "spain_01059",
    # ESPAÑA PROV
    "spain_04": "spain_04013",# Almería
    "spain_11": "spain_11012", # Cádiz
    "spain_14": "spain_14021", # Córdoba
    "spain_18": "spain_18087", # Granada
    "spain_21": "spain_21041", # Huelva
    "spain_23": "spain_23050", # Jaén
    "spain_29": "spain_29067", # Málaga
    "spain_41": "spain_41091", # Sevilla
    "spain_22": "spain_22125", # Huesca
    "spain_44": "spain_44216", # Teruel
    "spain_50": "spain_50297", # Zaragoza
    "spain_33": "spain_33044", # Oviedo (Asturias)
    "spain_35": "spain_35016", # Las Palmas de Gran Canaria
    "spain_38": "spain_38038", # Santa Cruz de Tenerife
    "spain_39": "spain_39075", # Santander
    "spain_02": "spain_02003", # Albacete
    "spain_13": "spain_13034", # Ciudad Real
    "spain_16": "spain_16078", # Cuenca
    "spain_19": "spain_19130", # Guadalajara
    "spain_45": "spain_45168", # Toledo
    "spain_05": "spain_05019", # Ávila
    "spain_09": "spain_09059", # Burgos
    "spain_24": "spain_24089", # León
    "spain_34": "spain_34120", # Palencia
    "spain_37": "spain_37274", # Salamanca
    "spain_40": "spain_40194", # Segovia
    "spain_42": "spain_42173", # Soria
    "spain_47": "spain_47186", # Valladolid
    "spain_49": "spain_49275", # Zamora
    "spain_08": "spain_08019", # Barcelona
    "spain_17": "spain_17079", # Girona
    "spain_25": "spain_25120", # Lleida
    "spain_43": "spain_43148", # Tarragona
    "spain_51": "spain_51001", # Ceuta
    "spain_03": "spain_03014", # Alicante
    "spain_12": "spain_12040", # Castelló de la Plana
    "spain_46": "spain_46250", # València
    "spain_06": "spain_06015", # Badajoz
    "spain_10": "spain_10037", # Cáceres
    "spain_15": "spain_15030", # A Coruña
    "spain_27": "spain_27028", # Lugo
    "spain_32": "spain_32054", # Ourense
    "spain_36": "spain_36038", # Pontevedra
    "spain_07": "spain_07040", # Palma (Illes Balears)
    "spain_26": "spain_26089", # Logroño
    "spain_28": "spain_28079", # Madrid
    "spain_52": "spain_52001", # Melilla
    "spain_30": "spain_30030", # Murcia
    "spain_31": "spain_31201", # Pamplona/Iruña
    "spain_01": "spain_01059", # Vitoria-Gasteiz
    "spain_48": "spain_48020", # Bilbao
    "spain_20": "spain_20069", # Donostia / San Sebastián
    "spain_51": "spain_51001",
    "spain_52": "spain_52001",
    }
}

class Command(BaseCommand):
    help = "Asigna capital(es) y ciudad más poblada basándose ÚNICAMENTE en CAPITAL_MAP."

    def handle(self, *args, **opts):
        cap_changes, biggest_changes = assign_capitals_and_biggest_city_from_map()
        self.stdout.write(self.style.SUCCESS(
            f"Capitals actualizadas en {cap_changes} área(s); "
            f"most_populate_city actualizado en {biggest_changes} área(s)."
        ))
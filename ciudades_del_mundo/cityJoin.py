import os
import django
from selenium import webdriver
from bs4 import BeautifulSoup
import sqlite3
import time
import sys
from django.db import transaction
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ciudades_del_mundo.settings')

django.setup()

from ciudades_del_mundo.models import Municipality
from django.db.models import Sum

def set_0_all():
    Municipality.objects.exclude(type=2).update(type=0)

@transaction.atomic
def unir_municipios(localityCode: str, localityName: str, provinceCode: str, codeCountry:str, province: str = "", exclude: list = [], include = []):
    """
    Crea un nuevo municipio en base a la suma de size y population de otros municipios
    de una provincia concreta, excluyendo e incluyendo los indicados.

    Args:
        localityCode (str): Código del nuevo municipio (PK).
        countryCode (str): Código del país.
        province (str): Provincia a la que deben pertenecer los municipios base.
        exclude (list): Lista de IDs de municipios a excluir.
        include (list): Lista de IDs de municipios adicionales a incluir.
    """

    # Filtrar municipios base en la provincia y país, excluyendo los indicados
    queryset = Municipality.objects.filter(
        codeCountry=codeCountry,
        province=province,
    ).exclude(id__in=exclude).exclude(type=2)

    # Añadir manualmente los municipios indicados en include
    included_municipalities = Municipality.objects.filter(id__in=include, codeCountry=codeCountry).exclude(type=2)

    included_municipalities.update(type=1)
    lastDate = ""
    if included_municipalities:
        lastDate = included_municipalities[0].lastDate
    elif queryset:
        lastDate = queryset[0].lastDate

    queryset.update(type=1)

    # Combinar los queryset sin duplicados
    municipios_finales = queryset.union(included_municipalities)

    # Calcular tamaño y población total
    agg1 = queryset.aggregate(total_size=Sum("size"), total_population=Sum("population"))
    agg2 = included_municipalities.aggregate(total_size=Sum("size"), total_population=Sum("population"))

    total_size = (agg1["total_size"] or 0) + (agg2["total_size"] or 0)
    total_population = (agg1["total_population"] or 0) + (agg2["total_population"] or 0)
    
    # Crear nuevo municipio
    new_municipality = Municipality.objects.update_or_create(
        id=localityCode,
        defaults={
            "id": localityCode,
            "codeCountry": codeCountry,
            "province": provinceCode,
            "size": round(total_size, 3),
            "population": total_population,
            "nombre": localityName,
            "lastDate": lastDate,
            "type" : 2
        }
    )

    return new_municipality
    

def unir_argel():
    print(unir_municipios("algeria-i1600", "El Djazaïr[Algiers]", "El Djazaïr[Algiers]", "algeria", "", [], 
                          [
                              "algeria-i1606",
                              "algeria-i1605",
                              "algeria-i1608",
                              "algeria-i1640",
                              "algeria-i1639",
                              "algeria-i1630",
                              "algeria-i1621",
                              "algeria-i1629",
                              "algeria-i1619",
                              "algeria-i1631",
                              "algeria-i1617",
                              "algeria-i1618",
                              "algeria-i1603",
                              "algeria-i1604",
                              "algeria-i1609",
                              "algeria-i1628",
                              "algeria-i1627",
                              "algeria-i1602",
                              "algeria-i1610",
                              "algeria-i1601",
                              "algeria-i1607",
                              ]))

def unir_laHabana():
    print(unir_municipios("cuba-i2300", "La Habana", "Ciudad de la Habana[Havana]", "cuba", "Ciudad de la Habana[Havana]", [], []))
    
def unir_moroco():
    print(unir_municipios("moroco-i1410100", "Casablanca", "Casablanca", "moroco", "Casablanca", [], []))
    print(unir_municipios("moroco-i4210100", "Rabat", "Rabat", "moroco", "Rabat", [], []))
    print(unir_municipios("moroco-i2310100", "Fès", "Fès", "moroco", "Fès", 
                          [
                              "moroco-i2310115", 
                              "moroco-i2318103", 
                              "moroco-i2318105"
                              ], 
                              [
                                  
                              ]
                            ))
    print(unir_municipios("moroco-i5110100", "Tanger[Tangier]", "Tanger - Assilah[Tangier]", "moroco", "", 
                          [], 
                          [
                              "moroco-i5110107", 
                              "moroco-i5110106", 
                              "moroco-i5110103", 
                              "moroco-i5110105"
                            ]
                        ))
    print(unir_municipios("moroco-i3510100", "Marrakech[Marrakesh]", "Marrakech[Marrakesh]", "moroco", "", [],
                          [
                              "moroco-i3510101", 
                              "moroco-i3510103", 
                              "moroco-i3510105", 
                              "moroco-i3510107",
                              "moroco-i3510109", 
                              "moroco-i3510111"
                              ],
                            ))
    print(unir_municipios("moroco-i4410100", "Salé", "Salé", "moroco", "Salé", 
                          [
                                "moroco-i4410108",
                                "moroco-i4410111",
                                "moroco-i4410113",
                              ],
                              [], 
                            ))

if __name__ == "__main__":
    set_0_all()
    print("Iniciando unión...")
    unir_argel()
    unir_laHabana()
    unir_moroco()
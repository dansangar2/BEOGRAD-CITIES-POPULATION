import os
import django
import sys
from django.db import transaction

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ciudades_del_mundo.settings')

django.setup()

from ciudades_del_mundo.models import Municipality, Subdivision, Country
from django.db.models import Sum
from django.core.exceptions import ValidationError

@transaction.atomic
def create_province(provinceCode: str, country_name: str, provinceName: str, capital: str, typeSub: str, level:int, highSubdv = "" , countries: list = [], provinces: list = [], include: list = [], exclude: list = []):
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

    if(not isinstance(provinces, list)):
        provinces = [provinces]

    
    ids = []
    for e in exclude:
        ids = Municipality.objects.filter(nombre__in=exclude).exclude(type=2).values_list('id', flat=True)
        ids = list(ids)
    exclude = list(set(exclude) | set(ids))
    
    ids = []
    for i in include:
        ids = Municipality.objects.filter(nombre__in=include).exclude(type=2).values_list('id', flat=True)
        ids = list(ids)
    include = list(set(include) | set(ids))
    
    try:
        capitalByName = Municipality.objects.get(nombre=capital)
        capital = capitalByName
    except:
        capital = Municipality.objects.get(id=capital)

    # Filtrar municipios base en la provincia y país, excluyendo los indicados
    included_municipalities = Municipality.objects.filter(id__in=include,).exclude(type=2)
    agg2 = included_municipalities.aggregate(total_size=Sum("size"), total_population=Sum("population"))
    
    if(len(countries) != 0 or len(provinces) != 0):
        queryset = Municipality.objects

    if(len(countries) != 0):
        if(not isinstance(countries, list)):
            countries = [countries]

        queryset = queryset.filter(
            codeCountry__in=countries,
        )

    if(len(provinces) != 0):
        queryset = queryset.filter(
            province__in=provinces,
        )

    if(len(countries) != 0 or len(provinces) != 0):
        queryset = queryset.exclude(id__in=exclude).exclude(type=2)
        agg1 = queryset.aggregate(total_size=Sum("size"), total_population=Sum("population"))
        total_size = (agg1["total_size"] or 0) + (agg2["total_size"] or 0)
        # Añadir manualmente los municipios indicados en include
        total_population = (agg1["total_population"] or 0) + (agg2["total_population"] or 0)
        municipios_finales = queryset.union(included_municipalities)
    else:
        total_size = (agg2["total_size"] or 0)
        total_population = (agg2["total_population"] or 0)    
        municipios_finales = included_municipalities
    
    try:
        parent_subdivision = Subdivision.objects.get(id=highSubdv)
    except Subdivision.DoesNotExist:
        parent_subdivision = None 
    
    new_province, created = Subdivision.objects.update_or_create(
        id = provinceCode,
        defaults= {
            "id": provinceCode,
            "name" : provinceName,
            "type" : typeSub,
            "level" : level,
            "country" : Country.objects.get(id=country_name),
            "parent" : parent_subdivision,
            "size": round(total_size, 2),
            "population": total_population,
            "capital": capital,
            "mostPopulate": municipios_finales.order_by('-population').first(),
        }
    )

    # Luego asignar el ManyToManyField
    #new_province.municipalities.set(municipios_finales)
    print(str(new_province))
    return new_province

def get_cities_ids(country_code: str, province: str, cities_name: list):
    
    # Filtrar los municipios por nombre, país y provincia
    try:
        
        if(not isinstance(cities_name, list)):
            cities_name = [cities_name]

        if(province == None):
            municipios = Municipality.objects.filter(
                nombre__in=cities_name,
                codeCountry=country_code
            )
        else:    
            if(not isinstance(province, list)):
                province = [province]
            # Filtrar los municipios
            municipios = Municipality.objects.filter(
                nombre__in=cities_name,
                province__in=province,
                codeCountry=country_code
            )
        
        # Verificar que el número de municipios encontrados sea igual al número de nombres dados
        if municipios.count() != len(cities_name):
            raise ValidationError(f"Se esperaban {len(cities_name)} municipios, pero se encontraron {municipios.count()}.")
        
        # Obtener los ids de los municipios
        municipio_ids = municipios.values_list('id', flat=True)
        
        # Mostrar el resultado
        print(f"IDs de municipios: {list(municipio_ids)}")

        return municipio_ids

    except Country.DoesNotExist:
        print("El país " + country_code + " no fue encontrado.")
    except Subdivision.DoesNotExist:
        print("La provincia " + province +" no fue encontrada.")
    except ValidationError as e:
        print(f"Error: {e}")
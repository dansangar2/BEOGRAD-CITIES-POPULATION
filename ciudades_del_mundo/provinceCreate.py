import os
import sys

import django

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ciudades_del_mundo.settings')

django.setup()

#from subdivisions.spain.castille import *
#from subdivisions.spain.new_spanish_empire import *
#from subdivisions.spain.spanish_empire import *
from subdivisions.spain.espanha_moderna import *

if __name__ == "__main__":
    print("Iniciando creación de Provincias...")
    create_country()
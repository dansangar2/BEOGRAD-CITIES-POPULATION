from copy import deepcopy

from ciudades_del_mundo.new_subdivisions.bourbon_spanish_empire import DIVISIONS as DIVB
from ciudades_del_mundo.historical_divisions.espanha import *


def _find_child(node: dict, name: str) -> dict | None:
    for child in node.get("childs") or []:
        if isinstance(child, dict) and child.get("name") == name:
            return child
    return None


def create_join() -> dict:
    result = deepcopy(DIVB)
    espanha = next((item for item in result if item.get("name") == "España"), None)
    if espanha is None:
        return result

    catalunha = _find_child(espanha, "Cataluña")
    if catalunha is not None:
        catalunha["childs"] = [CATALUNHA_A]
    return result

DIVISIONS = create_join()

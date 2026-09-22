"""Datos de la encuesta Origen-Destino de Lima Expresa para que el tótem los muestre.

El Excel crudo (8 652 encuestas) se resume UNA vez a sparky/data/od.json:
    python -m sparky.od_data build "D:/.../Origen -Destino-V01.xlsx"
En ejecución solo se lee ese JSON (pequeño, sin datos personales).
Autoprueba:  python -m sparky.od_data
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

OD_FILE = Path(__file__).parent / "data" / "od.json"
FUENTE = "Encuesta Origen-Destino en plazas de peaje · nov-dic 2018"

# tema → (título en pantalla, columna del Excel, valores a ignorar)
TEMAS = {
    "vehiculos":  ("Tipo de vehículo", "Tipo Veh", ()),
    "motivo":     ("Motivo del viaje", "Motivo de Viaje", ("No Aplica",)),
    "frecuencia": ("Frecuencia de viaje", "Frecuencia de Viaje", ()),
    "paga":       ("¿Quién paga el peaje?", "¿Quién Paga?", ()),
    "uso_via":    ("Tramo que usan", "Tipo de Usuario", ()),
    "horas":      ("Hora de paso", "HORA_ENTERA", ()),
    "ocupantes":  ("Ocupantes por auto", "Ocupa Veh", ("No Aplica",)),
    "ejes":       ("Ejes de vehículos de carga", "Número de Ejes", ("No Aplica",)),
    "origen":     ("Zona de origen", "ORIGEN CONO", ()),
    "destino":    ("Zona de destino", "DESTINO CONO", ()),
    "flujos":     ("Principales flujos (origen → destino)", None, ()),
    "distritos_origen":  ("Distritos de donde vienen", "ORIGEN DISTRITO", ()),
    "distritos_destino": ("Distritos a donde van", "DESTINO DISTRITO", ()),
}
TOP = 8


def _clean(v):
    return " ".join(str(v).split()) if v is not None else None


def _plaza(v):
    return _clean(v).replace("Plaza ", "").replace("Pte.", "Puente").replace("Sta.", "Santa")


def _label(tema, v):
    if tema == "horas":
        return f"{int(v):02d}:00"
    if tema in ("origen", "destino", "distritos_origen", "distritos_destino"):
        return _title(v)
    return v


def _title(v):
    return v.title().replace(" De ", " de ").replace(" Del ", " del ").replace(" La ", " la ")


def build(xlsx_path):
    import openpyxl  # solo para construir; en el tótem no hace falta
    ws = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)["Origen-Destino"]
    rows = ws.iter_rows(values_only=True)
    head = next(rows)
    col = {h: i for i, h in enumerate(head)}
    data = [r for r in rows if r[col["ID"]] is not None]

    stats = defaultdict(lambda: defaultdict(Counter))   # plaza → tema → Counter
    distritos = defaultdict(lambda: {"n": 0, "destinos": Counter(), "plazas": Counter(),
                                     "motivo": Counter()})
    for r in data:
        plaza = _plaza(r[col["LUGAR"]])
        for scope in ("Todas", plaza):
            for tema, (_, c, skip) in TEMAS.items():
                if c is None:
                    o, d = _clean(r[col["ORIGEN CONO"]]), _clean(r[col["DESTINO CONO"]])
                    v = f"{o.title()} → {d.title()}" if o and d else None
                else:
                    v = _clean(r[col[c]])
                if v is None or v in skip:
                    continue
                stats[scope][tema][_label(tema, v)] += 1
        od = _clean(r[col["ORIGEN DISTRITO"]])
        if od:
            d = distritos[od.upper()]
            d["n"] += 1
            if r[col["DESTINO DISTRITO"]]:
                d["destinos"][_title(_clean(r[col["DESTINO DISTRITO"]]))] += 1
            d["plazas"][plaza] += 1
            m = _clean(r[col["Motivo de Viaje"]])
            if m and m != "No Aplica":
                d["motivo"][m] += 1

    def top(counter, tema=None):
        items = counter.most_common() if tema != "horas" else sorted(counter.items())
        return items if tema == "horas" else items[:TOP]

    out = {
        "fuente": FUENTE,
        "total": len(data),
        "plazas": sorted(p for p in stats if p != "Todas"),
        # total = todas las respuestas del tema (los % se calculan sobre él, no sobre el top)
        "stats": {p: {t: {"total": sum(c.values()), "top": top(c, t)} for t, c in temas.items()}
                  for p, temas in stats.items()},
        "distritos": {k: {"n": v["n"], "destinos": top(v["destinos"]), "plazas": top(v["plazas"]),
                          "motivo": top(v["motivo"])}
                      for k, v in distritos.items() if v["n"] >= 15},
    }
    OD_FILE.parent.mkdir(exist_ok=True)
    OD_FILE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


_DATA = None


def _load():
    global _DATA
    if _DATA is None:
        if not OD_FILE.exists():   # no se versiona (dato del cliente): hay que generarlo en el tótem
            raise FileNotFoundError('Falta sparky/data/od.json: python -m sparky.od_data build "<Excel OD>"')
        _DATA = json.loads(OD_FILE.read_text(encoding="utf-8"))
    return _DATA


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.upper().replace("PLAZA", "").split())


def _bars(items, total):
    total = total or 1
    return [{"label": k, "n": n, "pct": round(100 * n / total, 1)} for k, n in items]


def consultar_encuesta(tema="vehiculos", plaza="", distrito=""):
    """Tarjeta lista para pantalla + narración. Tema, plaza o distrito de origen."""
    d = _load()
    if distrito:
        key = next((k for k in d["distritos"] if _norm(k) == _norm(distrito)), None)
        if not key:
            return {"ok": False, "error": f"Sin datos suficientes de {distrito}.",
                    "distritos_disponibles": sorted(_title(k) for k in d["distritos"])}
        x = d["distritos"][key]
        bars = _bars(x["destinos"], x["n"])
        return {"ok": True, "titulo": f"Quienes salen de {_title(key)} van a…",
                "subtitulo": FUENTE, "total": x["n"], "unidad": "viajeros encuestados",
                "barras": bars,
                "plaza_mas_usada": x["plazas"][0][0] if x["plazas"] else "",
                "motivo_principal": x["motivo"][0][0] if x["motivo"] else "",
                "dato_clave": f"{bars[0]['pct']}% va a {bars[0]['label']}" if bars else ""}

    if tema not in TEMAS:
        return {"ok": False, "error": f"Tema no válido. Usa: {', '.join(TEMAS)}"}
    scope = "Todas"
    if plaza:
        scope = next((p for p in d["plazas"] if _norm(plaza) in _norm(p) or _norm(p) in _norm(plaza)),
                     None)
        if not scope:
            return {"ok": False, "error": "Plaza no encontrada.", "plazas": d["plazas"]}
    st = d["stats"][scope].get(tema, {"total": 0, "top": []})
    bars, total = _bars(st["top"], st["total"]), st["total"]
    lead = max(bars, key=lambda b: b["n"]) if bars else None
    return {"ok": True, "titulo": TEMAS[tema][0],
            "subtitulo": ("Plaza " + scope if scope != "Todas" else "Todas las plazas") + " · " + FUENTE,
            "total": total, "unidad": "respuestas", "barras": bars,
            "dato_clave": f"{lead['pct']}% · {lead['label']}" if lead else ""}


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "build":
        o = build(sys.argv[2])
        print(f"OK {o['total']} encuestas, {len(o['plazas'])} plazas, "
              f"{len(o['distritos'])} distritos → {OD_FILE}")
        sys.exit()
    # autoprueba sobre el JSON generado
    r = consultar_encuesta("vehiculos")
    assert r["ok"] and r["total"] == _load()["total"], r
    assert abs(sum(b["pct"] for b in r["barras"]) - 100) < 1   # 3 categorías: suman 100
    assert sum(b["pct"] for b in consultar_encuesta("distritos_origen")["barras"]) < 100  # top 8 < total
    assert consultar_encuesta("horas")["barras"][0]["label"] < consultar_encuesta("horas")["barras"][-1]["label"]
    assert consultar_encuesta("motivo", plaza="monterrico")["subtitulo"].startswith("Plaza Monterrico")
    assert consultar_encuesta(distrito="ate")["ok"]
    assert not consultar_encuesta(distrito="Narnia")["ok"]
    assert not consultar_encuesta("xx")["ok"]
    print("od_data OK")

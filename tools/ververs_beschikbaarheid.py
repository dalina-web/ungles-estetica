"""Haalt per vestiging de eerstvolgende vrije momenten op bij Treatwell en schrijft
data/beschikbaarheid.json. De website toont daarmee "proxima cita libre" naast de boekknop.

Bron: dezelfde JSON-aanroep die de tijdkiezer van de Treatwell-widget zelf doet (zonder inlog):
  https://widget.treatwell.es/datetime-ui/api/availability?venueId=<id>&proposedServices=<json>
Het antwoord geeft 90 dagen met per dag de vrije starttijden. Er wordt niets geboekt, niets
ingevuld en niets verstuurd: alleen een GET.

Per vestiging worden een paar gangbare manicurebehandelingen opgevraagd (uit data/prijzen.json) en
samengevoegd: een dag met minstens een vrije starttijd geldt als dag met plek. Van de eerste
DAGEN dagen met plek worden hoogstens TIJDEN starttijden bewaard, zodat de pagina zelf de eerste
tijd kan kiezen die nog niet voorbij is.

Gebruik:  python tools/ververs_beschikbaarheid.py           ophalen, controleren, schrijven als er iets is gewijzigd
          python tools/ververs_beschikbaarheid.py --check   ophalen en controleren, nooit schrijven

Veiligheid: lukt het ophalen niet of is het antwoord onverwacht (minder dan MIN_DAGEN dagen in de
kalender), dan blijft het oude bestand ongemoeid en eindigt het script met exitcode 1 en een
melding op stderr. "gecontroleerd" verandert alleen als de beschikbaarheid zelf is gewijzigd.
Alleen de standaardbibliotheek.
"""
import json, os, sys, unicodedata, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

MAP = Path(__file__).resolve().parent.parent / "data"
IN = MAP / "prijzen.json"
UIT = MAP / "beschikbaarheid.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36"
API = "https://widget.treatwell.es/datetime-ui/api/availability?venueId={id}&proposedServices={svc}"
VESTIGINGEN = ("sant-just", "cornella")
# Behandelingen die we opvragen, op volgorde van voorkeur; hoogstens MAX_DIENSTEN per vestiging.
ZOEKTERMEN = ("manicura semipermanente completa", "manicura completa", "manicura expres",
              "manicura semipermanente expres")
MAX_DIENSTEN, DAGEN, TIJDEN, MIN_DAGEN = 3, 5, 8, 30
TOELICHTING = ("Vrije starttijden volgens de agenda van Treatwell, opgevraagd met dezelfde "
               "aanroep als de tijdkiezer van de boekwidget. Per vestiging samengevoegd over de "
               "opgevraagde behandelingen; alleen de eerste dagen met plek.")


class Fout(Exception):
    """Ophalen of controleren mislukt: het oude bestand blijft staan."""


def plat(s):
    """Kleine letters zonder accenten, om namen te kunnen vergelijken."""
    return "".join(c for c in unicodedata.normalize("NFD", str(s).lower())
                   if unicodedata.category(c) != "Mn")


def kies_diensten(vestiging):
    """(id, optie-id, naam) van de behandelingen die we opvragen."""
    diensten = [d for g in vestiging.get("groepen") or [] for d in g.get("diensten") or []
                if d.get("opties")]
    gekozen, gezien = [], set()
    for term in ZOEKTERMEN:
        for d in diensten:
            if term in plat(d["naam"]) and d["id"] not in gezien:
                gezien.add(d["id"])
                gekozen.append((d["id"], d["opties"][0]["id"], d["naam"]))
                break
    for d in diensten:  # niets gevonden: dan de eerste behandelingen uit de kaart
        if len(gekozen) >= MAX_DIENSTEN:
            break
        if d["id"] not in gezien:
            gezien.add(d["id"])
            gekozen.append((d["id"], d["opties"][0]["id"], d["naam"]))
    return gekozen[:MAX_DIENSTEN]


def haal_dagen(venue_id, item_id, optie_id):
    svc = json.dumps([{"menuItemId": str(item_id), "optionIds": [str(optie_id)]}], separators=(",", ":"))
    url = API.format(id=venue_id, svc=urllib.parse.quote(svc))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise Fout(f"ophalen mislukt voor venue {venue_id}, dienst {item_id} ({e})")
    dagen = data.get("days")
    if not isinstance(dagen, dict) or len(dagen) < MIN_DAGEN:
        raise Fout(f"onverwacht antwoord voor venue {venue_id}, dienst {item_id}: "
                   f"{len(dagen) if isinstance(dagen, dict) else type(dagen).__name__} dagen")
    uit = {}
    for dag, info in dagen.items():
        if not isinstance(info, dict):
            raise Fout(f"venue {venue_id}: dag {dag} heeft een onverwachte opbouw")
        tijden = [t.get("startTime") for t in info.get("times") or [] if isinstance(t, dict) and t.get("startTime")]
        if tijden:
            uit.setdefault(dag, set()).update(tijden)
    return uit


def vestiging_beschikbaarheid(venue_id, keuzes):
    samen, namen = {}, []
    for item_id, optie_id, naam in keuzes:
        namen.append(naam)
        for dag, tijden in haal_dagen(venue_id, item_id, optie_id).items():
            samen.setdefault(dag, set()).update(tijden)
    dagen = [{"dag": dag, "tijden": sorted(samen[dag])[:TIJDEN]} for dag in sorted(samen)][:DAGEN]
    return {"diensten": namen, "dagen": dagen}


def schrijf(pad, data):
    """Atomair: eerst naar een tijdelijk bestand in dezelfde map, dan vervangen."""
    tmp = pad.with_name(pad.name + ".tmp")
    tmp.write_bytes((json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    os.replace(tmp, pad)


def main(argv=None, nieuw=None, uit=UIT, bron=IN):
    """nieuw meegeven slaat het ophalen over (voor tests)."""
    argv = sys.argv[1:] if argv is None else argv
    check = "--check" in argv
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        if nieuw is None:
            try:
                prijzen = json.loads(bron.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise Fout(f"{bron.name} niet te lezen ({e}); eerst ververs_prijzen.py draaien.")
            nieuw = {}
            for k in VESTIGINGEN:
                v = prijzen.get(k) or {}
                keuzes = kies_diensten(v)
                if not v.get("venue_id") or not keuzes:
                    raise Fout(f"vestiging {k}: geen venue_id of geen behandeling met opties in {bron.name}")
                nieuw[k] = vestiging_beschikbaarheid(v["venue_id"], keuzes)
        try:
            oud = json.loads(uit.read_text(encoding="utf-8")) if uit.exists() else {}
        except ValueError as e:
            raise Fout(f"bestaand bestand {uit.name} is geen geldige JSON ({e}); niet overschreven.")
    except Fout as e:
        print(f"STOP: {e}\n{uit.name} is NIET gewijzigd.", file=sys.stderr)
        return 1

    inhoud = {"toelichting": TOELICHTING, "bron": "widget.treatwell.es/datetime-ui/api/availability", **nieuw}
    anders = {k: v for k, v in oud.items() if k != "gecontroleerd"} != inhoud
    if check:
        actie = "alleen gecontroleerd (--check), niets geschreven"
    elif anders:
        schrijf(uit, {"gecontroleerd": datetime.now(timezone.utc).isoformat(timespec="minutes"), **inhoud})
        actie = f"opgeslagen in {uit.name}"
    else:
        actie = f"{uit.name} ongewijzigd, niet herschreven"
    for k in VESTIGINGEN:
        d = nieuw[k]["dagen"]
        eerste = f"{d[0]['dag']} {d[0]['tijden'][0]}" if d else "geen plek in de kalender"
        print(f"{k}: {len(d)} dagen met plek, eerste {eerste} ({len(nieuw[k]['diensten'])} behandelingen opgevraagd)")
    print(actie + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())

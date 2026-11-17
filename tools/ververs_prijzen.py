"""Haalt diensten, prijzen, opties, openingstijden, beoordeling, adres en coordinaten van beide
vestigingen op bij Treatwell en schrijft data/prijzen.json. Toont wat er sinds de vorige keer is gewijzigd.

Gebruik:  python tools/ververs_prijzen.py           ophalen, controleren, schrijven als er iets is gewijzigd
          python tools/ververs_prijzen.py --check   ophalen en controleren, nooit schrijven

Veiligheid: lukt het ophalen niet, ontbreken de gegevens, heeft een vestiging geen diensten of is het
resultaat veel kleiner dan het bestaande bestand (minder dan 70% van de prijsregels van een vestiging),
dan blijft het oude bestand ongemoeid en eindigt het script met exitcode 1 en een melding op stderr.
Exitcode 0 = gelukt, met of zonder wijzigingen. Alleen de standaardbibliotheek.
"""
import html, json, os, re, sys, urllib.request
from pathlib import Path

VESTIGINGEN = {
    "sant-just": "ungles-i-estetica-sant-just-desvern",
    "cornella": "ungles-i-estetica",
}
UIT = Path(__file__).resolve().parent.parent / "data" / "prijzen.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36"
WIDGET = "https://widget.treatwell.es/establecimiento/{slug}/"
PUBLIEK = "https://www.treatwell.es/establecimiento/{slug}/"
MIN_AANDEEL = 0.70  # nieuw resultaat moet per vestiging minstens 70% van de oude prijsregels hebben


class Fout(Exception):
    """Ophalen of controleren mislukt: het oude bestand blijft staan."""


def haal_state(slug):
    url = WIDGET.format(slug=slug)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8")
    except Exception as e:
        raise Fout(f"ophalen mislukt voor {slug} ({url}): {e}")
    m = re.search(r"window\.__state__ = (\{.*?\});?\s*</script>", html, re.S)
    if not m:
        raise Fout(f"geen gegevens (window.__state__) gevonden voor {slug}; Treatwell heeft de pagina mogelijk gewijzigd.")
    try:
        return json.loads(m.group(1))
    except ValueError as e:
        raise Fout(f"gegevens van {slug} zijn geen geldige JSON: {e}")


def adres(loc):
    """straat/postcode/plaats uit venue.location; alleen wat Treatwell zelf levert."""
    a = loc.get("address") or {}
    regels = [r.strip() for r in a.get("addressLines") or [] if r and r.strip()]
    plaats = ((loc.get("tree") or {}).get("name") or "").split(",")[0].strip()
    straat = regels[0] if regels else ""
    # Sant Just heeft alles in een regel ("Carrer Bonavista, 75, Sant Just Desvern, Barcelona"): plaats eraf.
    if plaats and plaats in straat:
        straat = straat.split(plaats)[0].rstrip(" ,")
    uit = {"straat": straat, "postcode": a.get("postalCode"), "plaats": plaats}
    return {k: v for k, v in uit.items() if v}


VET = re.compile(r"'''(.+?)'''", re.S)


def omschrijving(ruw):
    """Zet de omschrijving van Treatwell om in blokken: alinea, kop of lijst.

    Treatwell levert losse tekst met HTML-entiteiten (u&ntilde;as), vette koppen
    tussen drie apostrofs ('''Incluye:''') en opsommingen met een sterretje.
    De tekst zelf wordt niet herschreven of ingekort.
    """
    if not ruw or not str(ruw).strip():
        return None
    tekst = re.sub(r"<[^>]+>", "", html.unescape(str(ruw)).replace("\r", ""))
    blokken = []
    for regel in (r.strip() for r in tekst.split("\n")):
        if not regel:
            continue
        if regel.startswith("*"):
            punt = VET.sub(lambda m: m.group(1), regel.lstrip("* ").strip())
            if not punt:
                continue
            if blokken and blokken[-1]["type"] == "lijst":
                blokken[-1]["punten"].append(punt)
            else:
                blokken.append({"type": "lijst", "punten": [punt]})
            continue
        m = VET.match(regel)
        if m:
            kop = m.group(1).strip().rstrip(":").strip()
            rest = VET.sub(lambda x: x.group(1), regel[m.end():].strip())
            blokken.append({"type": "alinea", "kop": kop, "tekst": rest} if rest
                           else {"type": "kop", "tekst": kop})
            continue
        blokken.append({"type": "alinea", "tekst": VET.sub(lambda m2: m2.group(1), regel)})
    return blokken or None


def vestiging(state, slug):
    try:
        v = state["venue"]["venue"]
        groepen = []
        for g in v["menu"]["menuGroups"]:
            items = []
            for it in g["menuItems"]:
                d = it["data"]
                opties = []
                for og in d.get("optionGroups") or []:
                    for o in og.get("options") or []:
                        optie = {"id": o["id"], "naam": o["name"],
                                 "prijs": o["priceRange"]["minSalePriceAmount"],
                                 "was": o["priceRange"]["minFullPriceAmount"]}
                        if o.get("durationMinutes") is not None:
                            optie["minuten"] = o["durationMinutes"]
                        opties.append(optie)
                item = {
                    "id": d["id"], "naam": d["name"],
                    "min_minuten": d["durationRange"]["minDurationMinutes"],
                    "max_minuten": d["durationRange"]["maxDurationMinutes"],
                    "prijs_vanaf": d["priceRange"]["minSalePriceAmount"],
                    "prijs_tot": d["priceRange"]["maxSalePriceAmount"],
                    "opties": opties,
                }
                blokken = omschrijving(d.get("description"))
                if blokken:
                    item["omschrijving"] = blokken
                items.append(item)
            groepen.append({"naam": g["name"], "diensten": items})
        uit = {"venue_id": v["id"], "naam": v["name"], "beoordeling": v.get("rating"),
               "openingstijden": v.get("openingHours"), "groepen": groepen}
        # Toegevoegde velden (alleen als Treatwell ze levert); bestaande sleutels hierboven blijven gelijk.
        loc = v.get("location") or {}
        a = adres(loc)
        if a:
            uit["adres"] = a
        p = loc.get("point") or {}
        if p.get("lat") is not None and p.get("lon") is not None:
            uit["geo"] = {"lat": p["lat"], "lon": p["lon"]}
        naam = v.get("normalisedName")
        if naam and v.get("listedOnMarketplace"):
            uit["url"] = PUBLIEK.format(slug=naam)
            uit["reviews_url"] = uit["url"] + "#reviews"
        return uit
    except (KeyError, TypeError, AttributeError) as e:
        raise Fout(f"onverwachte opbouw van de gegevens voor {slug}: {e!r}")


def plat(data):
    return {f"{k} | {d['naam']} | {o['naam']}": o["prijs"]
            for k, v in data.items() for g in v["groepen"] for d in g["diensten"] for o in d["opties"]}


def tel(v):
    """(aantal diensten, aantal prijsregels) van een vestiging."""
    diensten = [d for g in v.get("groepen") or [] for d in g.get("diensten") or []]
    return len(diensten), sum(len(d.get("opties") or []) for d in diensten)


def valideer(oud, nieuw):
    """Geeft een lijst met problemen; leeg = nieuw resultaat mag geschreven worden."""
    if not nieuw:
        return ["nieuw resultaat is leeg"]
    fouten = []
    for k in list(VESTIGINGEN) + [k for k in oud if k not in VESTIGINGEN]:
        if k not in nieuw:
            fouten.append(f"vestiging {k} ontbreekt in het nieuwe resultaat")
            continue
        diensten, regels = tel(nieuw[k])
        if diensten == 0 or regels == 0:
            fouten.append(f"vestiging {k} heeft {diensten} diensten en {regels} prijsregels")
            continue
        if k in oud:
            oud_regels = tel(oud[k])[1]
            if regels < MIN_AANDEEL * oud_regels:
                fouten.append(f"vestiging {k}: {regels} prijsregels, was {oud_regels} "
                              f"(minder dan {int(MIN_AANDEEL * 100)}%)")
            if oud[k].get("venue_id") != nieuw[k].get("venue_id"):
                fouten.append(f"vestiging {k}: venue_id {nieuw[k].get('venue_id')} wijkt af van {oud[k].get('venue_id')}")
    return fouten


def schrijf(pad, data):
    """Atomair: eerst naar een tijdelijk bestand in dezelfde map, dan vervangen."""
    tmp = pad.with_name(pad.name + ".tmp")
    tmp.write_bytes((json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    os.replace(tmp, pad)


def main(argv=None, nieuw=None, uit=UIT):
    """nieuw meegeven slaat het ophalen over (voor tests)."""
    argv = sys.argv[1:] if argv is None else argv
    check = "--check" in argv
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        if nieuw is None:
            nieuw = {k: vestiging(haal_state(s), s) for k, s in VESTIGINGEN.items()}
        try:
            oud = json.loads(uit.read_text(encoding="utf-8")) if uit.exists() else {}
        except ValueError as e:
            raise Fout(f"bestaand bestand {uit.name} is geen geldige JSON ({e}); niet overschreven.")
        fouten = valideer(oud, nieuw)
        if fouten:
            raise Fout("controle mislukt: " + "; ".join(fouten))
    except Fout as e:
        print(f"STOP: {e}\n{uit.name} is NIET gewijzigd.", file=sys.stderr)
        return 1

    a, b = plat(oud), plat(nieuw)
    wijz = [f"GEWIJZIGD {k}: {a[k]} -> {b[k]}" for k in b if k in a and a[k] != b[k]]
    wijz += [f"NIEUW {k}: {b[k]}" for k in b if k not in a]
    wijz += [f"VERVALLEN {k}" for k in a if k not in b]
    anders = oud != nieuw
    if check:
        actie = "alleen gecontroleerd (--check), niets geschreven"
    elif anders:
        schrijf(uit, nieuw)
        actie = f"opgeslagen in {uit.name}"
    else:
        actie = f"{uit.name} ongewijzigd, niet herschreven"
    print(f"{len(b)} prijsregels; {len(wijz)} prijswijzigingen; {actie}.")
    if anders and not wijz:
        print("Wel andere gegevens gewijzigd (beoordeling, openingstijden, adres of duur).")
    print("\n".join(wijz[:60]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

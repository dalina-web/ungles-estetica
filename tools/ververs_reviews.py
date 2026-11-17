"""Haalt per vestiging de recentste geverifieerde Treatwell-reviews op en schrijft data/reviews.json.

Bron: de JSON-API die de Treatwell-widget zelf aanroept (zonder inlog):
  https://widget.treatwell.es/api/v1/venue/<venue_id>/review?page=N&size=20
Reviewtekst wordt letterlijk overgenomen (niet ingekort, niet vertaald, niet aangepast).
"geverifieerd" = het veld "verified" dat Treatwell per review meegeeft (review na een boeking via Treatwell).

Gebruik:  python tools/ververs_reviews.py           ophalen, controleren, schrijven als er iets is gewijzigd
          python tools/ververs_reviews.py --check   ophalen en controleren, nooit schrijven

Veiligheid: lukt het ophalen niet, is het antwoord onverwacht (ook: geen pagination.totalPages), levert een
vestiging geen enkele review op of krimpt een vestiging van 4 of meer naar minder dan 3 reviews, dan blijft het oude bestand ongemoeid en eindigt het script met exitcode 1 en een melding op stderr.
"bijgewerkt" verandert alleen als de reviews zelf zijn gewijzigd. Alleen de standaardbibliotheek.
"""
import json, os, re, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path

VESTIGINGEN = {  # sleutel: (venue_id, slug)
    "sant-just": (504791, "ungles-i-estetica-sant-just-desvern"),
    "cornella": (431611, "ungles-i-estetica"),
}
UIT = Path(__file__).resolve().parent.parent / "data" / "reviews.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36"
API = ("https://widget.treatwell.es/api/v1/venue/{id}/review"
       "?page={page}&size=20&includeRatingHistogram=false&excludeUnverifiedReviews=false")
REVIEWS_URL = "https://www.treatwell.es/establecimiento/{slug}/#reviews"
AANTAL, MIN_TEKENS, MIN_STERREN, MAX_PAGINAS = 6, 40, 4, 10
KRIMP_VAN, KRIMP_NAAR = 4, 3  # had een vestiging >= 4 reviews en nu < 3: oude bestand houden
# Treatwell vervangt de salonnaam in de reviewtekst soms door een plaatshouder ("Recomiendo <UE>."):
# zulke reviews slaan we over, want de plaatshouder komt letterlijk op de pagina.
PLAATSHOUDER = re.compile(r"<[^<>\n]{1,60}>")
SELECTIE = (f"De {AANTAL} recentste reviews (op plaatsingsdatum) per vestiging die Treatwell als geverifieerd "
            f"markeert (verified=true), met {MIN_STERREN} of 5 sterren en een door de klant zelf geschreven tekst "
            f"van minstens {MIN_TEKENS} tekens, zonder plaatshouder zoals <UE> in de tekst, en hoogstens een "
            f"review per voornaam. Geen selectie op inhoud; tekst letterlijk overgenomen.")


class Fout(Exception):
    """Ophalen of controleren mislukt: het oude bestand blijft staan."""


def haal_pagina(venue_id, page):
    url = API.format(id=venue_id, page=page)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise Fout(f"ophalen mislukt ({url}): {e}")
    if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
        raise Fout(f"onverwacht antwoord van {url}: geen lijst 'reviews'")
    return data


def geschikt(r):
    tekst = (r.get("content") or {}).get("content") or ""
    return (r.get("verified") is True and not (r.get("content") or {}).get("generated")
            and r.get("rating") in (4, 5) and r.get("rating") >= MIN_STERREN
            and len(tekst.strip()) >= MIN_TEKENS
            and not PLAATSHOUDER.search(tekst))


def omzetten(r):
    wie = r.get("reviewer") or {}
    naam = "" if wie.get("anonymous") else (wie.get("name") or "").split()
    return {
        "id": r["id"],
        "voornaam": naam[0] if naam else "",
        "datum": r["createdAt"][:10],
        "sterren": r["rating"],
        "tekst": r["content"]["content"],
        "dienst": ", ".join(r.get("treatmentNames") or []),
        "geverifieerd": r.get("verified") is True,
    }


def kies(venue_id, haal=haal_pagina):
    """Loopt de pagina's af (nieuwste eerst) tot er AANTAL geschikte reviews zijn.
    Hoogstens een review per voornaam, zodat er niet drie keer dezelfde klant op de pagina staat."""
    gekozen, vorige, namen = [], None, set()
    for page in range(MAX_PAGINAS):
        data = haal(venue_id, page)
        for r in data["reviews"]:
            try:
                if vorige and r["createdAt"] > vorige:
                    raise Fout(f"venue {venue_id}: reviews staan niet op datum (nieuwste eerst); volgorde niet te vertrouwen")
                vorige = r["createdAt"]
                if geschikt(r) and len(gekozen) < AANTAL:
                    rij = omzetten(r)
                    naam = rij["voornaam"].strip().lower()
                    if naam and naam in namen:
                        continue
                    if naam:
                        namen.add(naam)
                    gekozen.append(rij)
            except (KeyError, TypeError) as e:
                raise Fout(f"venue {venue_id}: onverwachte opbouw van een review: {e!r}")
        pag = data.get("pagination")
        if not isinstance(pag, dict) or type(pag.get("totalPages")) is not int:
            raise Fout(f"venue {venue_id}: geen pagination.totalPages in het antwoord")
        totaal = pag["totalPages"]
        if len(gekozen) >= AANTAL or not data["reviews"] or page + 1 >= totaal:
            break
    return gekozen


def valideer(nieuw):
    """Geeft een lijst met problemen; leeg = mag geschreven worden."""
    fouten = []
    for k in VESTIGINGEN:
        reviews = (nieuw.get(k) or {}).get("reviews") or []
        if not reviews:
            fouten.append(f"vestiging {k}: geen enkele geschikte review gevonden")
        for r in reviews:
            if not (r.get("tekst") and r.get("datum") and r.get("geverifieerd") is True):
                fouten.append(f"vestiging {k}: review {r.get('id')} is onvolledig of niet geverifieerd")
    return fouten


def gekrompen(oud, nieuw):
    """Had een vestiging er KRIMP_VAN of meer en nu minder dan KRIMP_NAAR, dan is het antwoord niet te vertrouwen."""
    fouten = []
    for k in VESTIGINGEN:
        o = oud.get(k) if isinstance(oud, dict) else None
        had = len((o.get("reviews") if isinstance(o, dict) else None) or [])
        nu = len((nieuw.get(k) or {}).get("reviews") or [])
        if had >= KRIMP_VAN and nu < KRIMP_NAAR:
            fouten.append(f"vestiging {k}: van {had} naar {nu} reviews gekrompen")
    return fouten


def schrijf(pad, data):
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
            nieuw = {k: {"reviews_url": REVIEWS_URL.format(slug=slug), "reviews": kies(vid)}
                     for k, (vid, slug) in VESTIGINGEN.items()}
        try:
            oud = json.loads(uit.read_text(encoding="utf-8")) if uit.exists() else {}
        except ValueError as e:
            raise Fout(f"bestaand bestand {uit.name} is geen geldige JSON ({e}); niet overschreven.")
        fouten = valideer(nieuw) + gekrompen(oud, nieuw)
        if fouten:
            raise Fout("controle mislukt: " + "; ".join(fouten))
    except Fout as e:
        print(f"STOP: {e}\n{uit.name} is NIET gewijzigd.", file=sys.stderr)
        return 1

    inhoud = {"selectie": SELECTIE, **nieuw}
    anders = {k: v for k, v in oud.items() if k != "bijgewerkt"} != inhoud
    aantallen = ", ".join(f"{k} {len(nieuw[k]['reviews'])}" for k in VESTIGINGEN)
    for k in VESTIGINGEN:
        if len(nieuw[k]["reviews"]) < AANTAL:
            print(f"LET OP: {k} heeft maar {len(nieuw[k]['reviews'])} geschikte reviews (gevraagd {AANTAL}).")
    if check:
        actie = "alleen gecontroleerd (--check), niets geschreven"
    elif anders:
        schrijf(uit, {"bijgewerkt": datetime.now(timezone.utc).date().isoformat(), **inhoud})
        actie = f"opgeslagen in {uit.name}"
    else:
        actie = f"{uit.name} ongewijzigd, niet herschreven"
    print(f"Reviews: {aantallen}; {actie}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Normalizzazione encoding dei testi provenienti dalle fonti PA.

Molte fonti pubblicano CSV formalmente UTF-8 validi che contengono testo
gia corrotto a monte: byte UTF-8 decodificati come latin-1/cp1252 e poi
ri-codificati in UTF-8 (doppio encoding). La corruzione puo essere annidata
piu volte (osservati fino a 3 giri sulla fonte PUN).

Esempi reali:
    "Via della LibertÃ\\xa0, 3"   -> "Via della Libertà, 3"
    "Strada CuorgnÃ¨, 79"        -> "Strada Cuorgnè, 79"
    "JaufenstraÃƒÅ¸e 44"          -> "Jaufenstraße 44"   (triplo giro)

ORDINE OBBLIGATORIO: fix_mojibake() va applicato PRIMA di strip().
Il carattere "a con accento grave" corrotto e la coppia "A tilde" + NBSP
(U+00A0): strip() considera NBSP uno spazio e lo rimuove, mutilando il
mojibake. clean_text() impone l ordine corretto.
"""

import html as _html
import re

# Caratteri che in italiano non compaiono mai isolati e segnalano mojibake
_SEGNALE = re.compile("[\u00c2\u00c3\u00c5\u0152\u017d]")

# "A tilde" o "A circonflesso" seguiti da fine stringa o punteggiatura:
# il secondo byte NBSP e stato rimosso da uno strip() a monte.
# NB: la classe del lookahead NON include NBSP, altrimenti la funzione
# non sarebbe idempotente e accumulerebbe NBSP a ogni giro.
_CODA_MUTILA = re.compile("([\u00c2\u00c3])(?=[ \t\r\n,.;:/-]|$)")


def ripristina_nbsp_troncato(s: str) -> str:
    """Reinserisce l NBSP rimosso da uno strip() applicato a monte.

    La regola e deterministica: strip() rimuove solo whitespace, e fra le
    vocali accentate italiane solo "a grave" ha NBSP come secondo byte
    (0xC3 0xA0). Le altre (0xC3 0x80 = A, 0xC3 0x88 = E, 0xC3 0x8C = I)
    hanno come secondo byte un control char, che strip() non tocca.
    Analogamente "A circonflesso" isolato era un NBSP legittimo (0xC2 0xA0).

    Non decodifica nulla da sola: restituisce una stringa che fix_mojibake()
    e di nuovo in grado di invertire.

    LIMITE NOTO: un mojibake nato da una catena diversa che si esaurisce in
    "A tilde" finale viene ricostruito come "a grave" anche se l originale
    era un altro carattere. Osservato 1 caso su circa 350 (VIA MARASA,
    ANNCSU).
    """
    if not s:
        return s
    return _CODA_MUTILA.sub(lambda m: m.group(1) + "\u00a0", s)


def _un_giro(s: str) -> str:
    """Inverte un singolo giro di doppio encoding. Invariato se non applicabile."""
    for enc in ("latin-1", "cp1252"):
        try:
            return s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return s


def fix_mojibake(s: str, max_giri: int = 3) -> str:
    """Ricostruisce il testo originale, srotolando gli encoding annidati.

    Conservativo: senza il segnale di corruzione non tocca nulla, e se un
    giro non produce una decodifica valida si ferma scartando il ripristino
    dell NBSP, restituendo l ultimo stato valido.
    """
    if not s or not _SEGNALE.search(s):
        return s
    out = s
    for _ in range(max_giri):
        cand = ripristina_nbsp_troncato(out)
        t = _un_giro(cand)
        if t == cand:
            break
        out = t
        if not _SEGNALE.search(out):
            break
    return out


_Q = "\u00bf"
_Q_TRATTINO = re.compile(" " + _Q + " ")
_Q_GRADO_CIFRA = re.compile("(?<=[0-9])" + _Q)
_Q_GRADO_NUM = re.compile("(?<=[A-Za-z])" + _Q + "(?=[0-9])")
_Q_APOSTROFO = re.compile("(?<=[A-Za-z\u00c0-\u017f])" + _Q)


def normalizza_sostituzioni(s: str) -> str:
    """Reinterpreta il carattere di sostituzione U+00BF lasciato dalla fonte.

    Il Ministero della Salute pubblica alcuni nomi in cui apostrofi tipografici,
    virgolette, trattini lunghi e simbolo di grado sono stati rimpiazzati da
    "punto interrogativo rovesciato". Il carattere originale non e recuperabile
    per inversione: si ricostruisce per contesto.

    ATTENZIONE - QUESTA E UNA EURISTICA, NON UN RECUPERO.
    A differenza di fix_mojibake(), che inverte una trasformazione
    deterministica e verificabile, qui si sceglie il carattere piu probabile.
    Esempio: "TRINITA?" diventa "TRINITA-apostrofo" seguendo la convenzione dei
    dataset PA italiani, ma l originale poteva essere "TRINITA-accentata".

    Verificata sui 21 casi presenti nel dataset Farmacie/Parafarmacie:
    20 ricostruzioni corrette, 1 imperfetta ma leggibile (una virgoletta di
    chiusura fra due parole diventa apostrofo).

    Non applicata alle altre fonti: e tarata su questo dataset.
    """
    if not s or _Q not in s:
        return s
    s = _Q_TRATTINO.sub(" - ", s)
    s = _Q_GRADO_CIFRA.sub("\u00b0", s)
    s = _Q_GRADO_NUM.sub("\u00b0", s)
    s = _Q_APOSTROFO.sub("'", s)
    s = s.replace(_Q, "")
    return re.sub(r"\s+", " ", s).strip()


# Entity HTML lasciate dalla fonte: "e commerciale" nei nomi delle farmacie (MdS,
# 152 occorrenze su 140 comuni) e spazio unificatore nelle descrizioni dei beni ArCo
# (766 occorrenze su 73 comuni). Il frontend le maschera, perche inserisce via
# innerHTML e il browser le decodifica; il chatbot invece fa escape HTML per
# sicurezza XSS, quindi l utente le legge alla lettera. Ma il difetto e nel DATO:
# questi shard sono pubblicati come open data (cfr. dcat_catalog) e riusati da terzi.
_ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,31}|#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6});")


def decodifica_entity(s: str, max_giri: int = 3) -> str:
    """Decodifica le entity HTML e normalizza lo spazio unificatore che ne deriva.

    Deterministica e verificabile, a differenza di normalizza_sostituzioni(): una
    entity ha un solo significato possibile. Una "e commerciale" isolata non e una
    entity e resta intatta ("Via Garibaldi & Co").

    Lo spazio unificatore prodotto dalla decodifica diventa spazio normale: in un
    campo testuale di un open data e rumore invisibile. Il collasso degli spazi
    multipli agisce solo sulle stringhe che contenevano entity.

    ORDINE: va applicata DOPO fix_mojibake(), che usa lo spazio unificatore come
    segnale per ricostruire i byte corrotti; anticiparla ne falserebbe il lavoro.
    """
    if not s or "&" not in s:
        return s
    if not _ENTITY.search(s):
        return s
    # La codifica puo essere ANNIDATA piu volte, esattamente come il mojibake:
    # osservato "e commerciale" codificata tre volte nel dataset Farmacie del MdS
    # (15 comuni). Un solo giro lascerebbe una entity ancora da sciogliere, quindi
    # si itera finche il testo ne contiene, con lo stesso tetto di fix_mojibake().
    out = s
    for _ in range(max_giri):
        nuovo = _html.unescape(out)
        if nuovo == out:
            break
        out = nuovo
        if not _ENTITY.search(out):
            break
    out = out.replace("\u00a0", " ")
    return re.sub(r"[ \t]{2,}", " ", out)


def clean_text(v, limite: int | None = None,
               sostituzioni: bool = False) -> str:
    """Pipeline standard per un campo testuale di una fonte: fix, strip, taglio.

    sostituzioni=True abilita anche normalizza_sostituzioni(), che e una
    EURISTICA tarata sul dataset Farmacie/Parafarmacie del Ministero della
    Salute. Va attivata solo da chi ha verificato i casi della propria fonte:
    per questo e opt-in e non fa parte del comportamento predefinito.
    """
    s = fix_mojibake(v or "")
    s = decodifica_entity(s)
    if sostituzioni:
        s = normalizza_sostituzioni(s)
    s = s.strip()
    return s[:limite] if limite else s

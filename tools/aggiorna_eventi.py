#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuonaRoma · tools/aggiorna_eventi.py
────────────────────────────────────
Aggiorna data/eventi.json collegandosi ai siti ufficiali dei locali
(vedi data/locali.json). Solo librerie standard di Python: nessuna
dipendenza da installare, funziona in GitHub Actions così com'è.

Strategie di estrazione (provate nell'ordine configurato per ogni
locale, la prima che dà risultati vince):
  · ical       — feed calendario iCal (.ics) dei siti WordPress con
                 "The Events Calendar" e di qualsiasi altro sito che
                 pubblica un link ?ical=1 / .ics
  · wp_json    — API REST di The Events Calendar (/wp-json/tribe/...)
  · jsonld     — dati strutturati schema.org (Event / MusicEvent)
  · html_apm   — parser dedicato alle card di auditorium.com
  · html_cdj   — parser dedicato agli articoli di casadeljazz.com
  · html_wix   — parser per i siti Wix (eventi in JSON dentro <script>)
  · html_testo — scansione generica del testo: riga-data italiana
                 seguita dalla riga col nome dell'artista
  · html_zero  — zero.eu, pagina "luogo" del locale (JSON-LD con
                 l'elenco degli eventi in programma)
  · html_songkick — pagina venue di songkick.com (JSON-LD MusicEvent)
  · html_romatoday — pagina "location" di romatoday.it (data nello
                 slug dell'articolo, titolo nel blocco <a>)
  · html_atlantico — parser dedicato a atlanticoliveroma.it ('EVENTI
                 IN PROGRAMMA': titolo in <strong>, data completa
                 con anno)
  · html_vinile — parser dedicato a vinileroma.it (serate 'Gio 5 nov'
                 con titolo e link di dettaglio; live dalle 22.30)

Integrazione con l'archivio (modalità "solo futuro"):
l'archivio contiene ESCLUSIVAMENTE eventi da oggi in poi. Per ogni
locale estratto con successo gli eventi futuri vengono rimpiazzati
dai dati freschi; i locali la cui fonte non risponde mantengono i
propri eventi futuri; gli eventi che diventano passati vengono
eliminati automaticamente ad ogni esecuzione (nessuno storico).

Diagnostica: quando un sito non risponde, il rapporto indica LA CAUSA
(DNS inesistente, timeout, HTTP 403 anti-bot, errore SSL...), non solo
"sito non raggiungibile".

Output:
  · data/eventi.json           — dati aggiornati
  · data/eventi.js             — specchio per apertura via file://
  · tools/report.json / .md    — rapporto dell'esecuzione

Uso:  python3 tools/aggiorna_eventi.py [--giorni=365] [--solo-report]
"""
import json, re, socket, ssl, sys, threading, time, gzip, html as htmllib
import http.cookiejar as cj
import urllib.request, urllib.error, urllib.parse
import concurrent.futures as cf
from datetime import date, datetime, timedelta
from pathlib import Path

BASE   = Path(__file__).resolve().parent.parent   # cartella suonaroma/
CFG    = json.loads((BASE / 'tools' / 'config_scraping.json').read_text(encoding='utf-8'))
SETT   = CFG.get('impostazioni', {})
# UA da browser: molti hosting italiani bloccano gli user-agent da bot
# con 403 prima ancora di guardare la pagina. Si può cambiare in config.
UA     = SETT.get('user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                'AppleWebKit/537.36 (KHTML, like Gecko) '
                                'Chrome/131.0.0.0 Safari/537.36')
TIMEO  = SETT.get('timeout_s', 15)
TENT   = SETT.get('tentativi', 1)
PAUSA  = SETT.get('pausa_s', 0.4)
GG_MAX = SETT.get('giorni_avanzo', 120)
CAP    = SETT.get('max_eventi_locale', 60)
PAR    = SETT.get('parallelo', 8)
SOLO_REPORT = '--solo-report' in sys.argv
for a in sys.argv:
    if a.startswith('--giorni='): GG_MAX = int(a.split('=')[1])

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

HEADER_HTTP = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'it-IT,it;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'sec-ch-ua': '"Chromium";v="131", "Not_A Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
}

# Sessione per-thread con cookie: alcuni server li usano per la prima
# risposta (e talvolta la sfida "ricarica la pagina" passa al secondo colpo).
_TLS = threading.local()

def opener():
    if getattr(_TLS, 'op', None) is None:
        _TLS.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj.CookieJar()),
            urllib.request.HTTPSHandler(context=CTX))
    return _TLS.op

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo('Europe/Rome')
except Exception:
    TZ = None

def oggi():
    return datetime.now(TZ).date() if TZ else date.today()

# ── rete ─────────────────────────────────────────────────────
ERR_ULTIMO = {}          # url → causa dell'ultimo errore (per il rapporto)

def spiega(ex):
    """Traduce un'eccezione di rete in una breve causa leggibile."""
    if isinstance(ex, urllib.error.HTTPError):
        c = ex.code
        if c in (403, 429):     return f'HTTP {c} (blocco anti-bot o limite richieste)'
        if c == 404:            return 'HTTP 404 (pagina non trovata)'
        if 500 <= c < 600:      return f'HTTP {c} (errore interno del server)'
        return f'HTTP {c}'
    causa = getattr(ex, 'reason', ex)
    if isinstance(causa, socket.gaierror):  return 'DNS: il dominio non risolve (inesistente o rimosso)'
    if isinstance(causa, ssl.SSLError):     return 'errore certificato SSL'
    s = str(causa).lower()
    if isinstance(causa, TimeoutError) or 'timed out' in s: return f'timeout (nessuna risposta entro {TIMEO}s)'
    if 'unreachable' in s:                  return 'rete non raggiungibile'
    if 'refused' in s:                      return 'connessione rifiutata'
    if 'reset' in s:                        return 'connessione interrotta dal server'
    return type(causa).__name__

def get(url, t=TIMEO):
    """GET con header da browser, cookie di sessione e gestione gzip;
    ritorna bytes ('' se fallisce) e registra in ERR_ULTIMO la causa."""
    for i in range(TENT):
        try:
            req = urllib.request.Request(url, headers=HEADER_HTTP)
            with opener().open(req, timeout=t) as r:
                data = r.read()
                enc = (r.headers.get('Content-Encoding') or '').lower()
            if enc == 'gzip':
                data = gzip.decompress(data)
            elif enc == 'deflate':
                import zlib
                try:    data = zlib.decompress(data)
                except Exception: data = zlib.decompress(data, -zlib.MAX_WBITS)
            time.sleep(PAUSA)
            return data
        except Exception as ex:
            ERR_ULTIMO[url] = spiega(ex)
            if i == TENT - 1: return b''
            time.sleep(PAUSA * 2)
    return b''

def get_text(url, t=TIMEO):
    return get(url, t).decode('utf-8', 'replace')

def base_url(url):
    m = re.match(r'(https?://[^/]+)', url)
    return m.group(1) if m else url

# ── generi ───────────────────────────────────────────────────
GENERI = [
    ('Jazz / Blues',        r'jazz|blues|swing|soul|funk|bossa'),
    ('Rock / Metal',        r'rock|metal|punk|grunge'),
    ('Indie / Alternative', r'indie|post[- ]?rock|shoegaze|dream pop'),
    ('Pop / Cantautorato',  r'pop|cantaut|songwriter|acoustic|acustic'),
    ('Elettronica / DJ',    r'dj set|djset|electro|techno|house music|edm|elettronica|disco music'),
    ('Hip Hop / Rap',       r'hip ?hop|rap|trap'),
    ('Classica / Opera',    r'classica|sinfon|orchestra|opera|pianoforte solo'),
    ('Folk / Country',      r'folk|country|bluegrass|celtic'),
    ('Latino / World',      r'latino|salsa|bachata|reggae|afrobeat|world'),
    ('Cover / Tribute',     r'tribute|cover band|omaggio a|tributo'),
]
def classifica(t):
    t = (t or '').lower()
    for nome, pat in GENERI:
        if re.search(pat, t): return nome
    return 'Live'

def pulisci(s):
    s = htmllib.unescape(str(s or '')).replace('\u00a0', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:180]

# ── parser iCal ──────────────────────────────────────────────
def unfold_ics(text):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    out = []
    for line in text.split('\n'):
        if line[:1] in (' ', '\t') and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out

def dec_ics(s):
    s = re.sub(r'\\n', '\n', s, flags=re.I)
    s = s.replace('\\,', ',').replace('\\;', ';').replace('\\\\', '\\')
    if 'QUOTED-PRINTABLE' in s.upper():
        s = re.sub(r'=([0-9A-F]{2})', lambda m: chr(int(m.group(1), 16)), s, flags=re.I).replace('=\n', '')
    return s

def parse_dt_ics(val):
    """DTSTART → datetime (Europe/Rome se possibile)."""
    val = val.strip()
    utc = val.endswith('Z')
    tzid = None
    m = re.search(r'TZID=([^:;]+)', val)
    if m: tzid = m.group(1)
    body = val.split(':')[-1].rstrip('Zz').strip()
    try:
        if 'T' in body:
            dt = datetime.strptime(body[:15], '%Y%m%dT%H%M%S')
        else:
            d = datetime.strptime(body[:8], '%Y%m%d')
            dt = datetime(d.year, d.month, d.day, 0, 0)
    except ValueError:
        return None
    try:
        from zoneinfo import ZoneInfo
        if utc:
            dt = dt.replace(tzinfo=ZoneInfo('UTC')).astimezone(ZoneInfo('Europe/Rome'))
        elif tzid:
            try: dt = dt.replace(tzinfo=ZoneInfo(tzid)).astimezone(ZoneInfo('Europe/Rome'))
            except Exception: pass
        elif TZ:
            dt = dt.replace(tzinfo=TZ)
    except Exception:
        pass
    return dt

def parse_ics(text, limite=200):
    """Estrae {dt, sum, desc, url} dai VEVENT."""
    lines = unfold_ics(text)
    evs, cur = [], None
    for ln in lines:
        u = ln.strip().upper()
        if u.startswith('BEGIN:VEVENT'):
            cur = {'dt': None, 'sum': '', 'desc': '', 'url': '', 'loc': ''}
        elif u.startswith('END:VEVENT'):
            if cur and cur['dt']: evs.append(cur)
            cur = None
        elif cur is not None:
            m = re.match(r'([A-Z\-]+)(?:;([^:]*))?:(.*)', ln)
            if not m: continue
            prop, params, val = m.group(1).upper(), m.group(2) or '', m.group(3).strip()
            if prop == 'DTSTART':
                dt = parse_dt_ics(params + ':' + val if 'TZID' in params else val)
                if dt: cur['dt'] = dt
            elif prop == 'SUMMARY':     cur['sum']  = dec_ics(val)
            elif prop == 'DESCRIPTION': cur['desc'] = dec_ics(val)
            elif prop == 'URL':         cur['url']  = val
            elif prop == 'LOCATION':    cur['loc']  = dec_ics(val)
        if len(evs) >= limite: break
    return evs

# ── strategie di estrazione ──────────────────────────────────
def estrai_ical(base, html0, scadenza):
    """Feed iCal: prima i link ?ical=1/.ics presenti nella pagina,
    poi i percorsi standard di The Events Calendar (solo siti WP)."""
    candidati = []
    for m in re.finditer(r'href="([^"]*(?:\?ical=1|\.ics)[^"]*)"', html0, re.I):
        u = m.group(1).replace('&amp;', '&')
        candidati.append(urllib.parse.urljoin(base, u))
    if re.search(r'wp-json|wp-content|tribe', html0, re.I):
        candidati += [base + p for p in ('/events/?ical=1', '/eventi/?ical=1', '/?ical=1')]
    visti, eventi = set(), []
    for u in candidati[:5]:
        if time.monotonic() > scadenza: break
        if u in visti: continue
        visti.add(u)
        ics = get_text(u)
        if 'BEGIN:VEVENT' not in ics: continue
        eventi = parse_ics(ics)
        if eventi: break
    return eventi

def estrai_wp_json(base, scadenza):
    out, p = [], 1
    while p <= 3 and time.monotonic() < scadenza:
        u = f"{base}/wp-json/tribe/events/v1/events?per_page=40&paged={p}&start_date={oggi().isoformat()}"
        try:
            d = json.loads(get_text(u) or '{}')
        except Exception:
            break
        evs = d.get('events') or []
        for e in evs:
            try: dt = datetime.fromisoformat(str(e.get('start_date', '')))
            except Exception: continue
            out.append({'dt': dt, 'sum': e.get('title', ''),
                        'desc': e.get('excerpt', '') or e.get('description', '') or '',
                        'url': e.get('url', ''), 'loc': ''})
        if len(evs) < 40: break
        p += 1
    return out

def estrai_jsonld(url, html0, scadenza):
    eventi = []
    def da_pagina(html):
        for blocco in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
            try:
                data = json.loads(blocco.strip().rstrip(';') or 'null')
            except Exception:
                continue
            if isinstance(data, dict):
                candidati = data.get('@graph', [data])
            elif isinstance(data, list):
                candidati = data
            else:
                candidati = []
            for item in candidati:
                if not isinstance(item, dict): continue
                if 'Event' not in str(item.get('@type', '')): continue
                sd = str(item.get('startDate', '') or item.get('start_date', ''))
                if not sd: continue
                try: dt = datetime.fromisoformat(sd.replace('Z', '+00:00'))
                except Exception: continue
                loc = item.get('location') or {}
                eventi.append({'dt': dt, 'sum': item.get('name', ''),
                               'desc': str(item.get('description', '') or ''),
                               'url': str((item.get('offers') or {}).get('url', '') or item.get('url', '') or ''),
                               'loc': str(loc.get('name', '') if isinstance(loc, dict) else loc)})
    da_pagina(html0)
    if not eventi and time.monotonic() < scadenza:
        pg = urllib.parse.urljoin(url, 'eventi/')
        da_pagina(get_text(pg))
    return eventi

# ── parser dedicati ──────────────────────────────────────────
MESI_IT = {'gennaio': 1, 'febbraio': 2, 'marzo': 3, 'aprile': 4, 'maggio': 5,
           'giugno': 6, 'luglio': 7, 'agosto': 8, 'settembre': 9,
           'ottobre': 10, 'novembre': 11, 'dicembre': 12}

def estrai_apm_card(url, html0, scadenza, pagine=3):
    """Parser per le schede 'event-card' di auditorium.com: le card
    contengono titolo, data testuale italiana ('Mercoledì 04 Novembre
    2026'), orario ('h. 20:00') ed etichetta del genere. Vengono
    tenuti solo gli eventi ospitati sul dominio dell'Auditorium.
    Segue le prime N pagine dell'elenco (/it/eventi/page/2/...)."""
    evs = []
    def da_html(html):
        for m in re.finditer(r'<div class="event-card[^"]*">(.*?)(?=<div class="event-card|$)', html, re.S | re.I):
            if time.monotonic() > scadenza: return
            blocco = m.group(1)
            def campo(cls):
                x = re.search(r'<span class="' + cls + r'[^"]*">(.*?)</span>', blocco, re.S | re.I)
                return pulisci(re.sub(r'<[^>]+>', ' ', x.group(1))) if x else ''
            link = re.search(r'<a class="apm-card-link" href="([^"]+)"', blocco, re.I)
            titolo = campo('text-card-title')
            sub    = campo('text-subtitle').lower()
            intro  = campo('text-intro').lower()
            label  = campo('text-label')
            if not (titolo and sub): continue
            if link and 'auditorium.com' not in link.group(1): continue   # eventi esterni (es. Santa Cecilia)
            dm = re.search(r'(\d{1,2})\s+([a-zàèéìòù]+)\s+(\d{4})', sub)
            if not dm or dm.group(2) not in MESI_IT: continue
            try:
                dt = datetime(int(dm.group(3)), MESI_IT[dm.group(2)], int(dm.group(1)))
            except ValueError:
                continue
            om = re.search(r'(\d{1,2})[.:](\d{2})', intro)
            if om: dt = dt.replace(hour=int(om.group(1)) % 24, minute=int(om.group(2)))
            biglietti = bool(re.search(r'event-ticket-link', blocco, re.I))
            evs.append({'dt': dt, 'sum': titolo, 'desc': label, 'url': '',
                        'loc': '', 'ticket': biglietti})
    da_html(html0)
    for p in range(2, pagine + 1):
        if time.monotonic() > scadenza or not evs: break
        pg = urllib.parse.urljoin(url, f'page/{p}/')
        da_html(get_text(pg))
    return evs

def estrai_cdj_article(url, html0, scadenza):
    """Parser per casadeljazz.com: WordPress con elenco server-side di
    <article class="loop-element ... evento ..."> contenenti il titolo
    (<a title="...">), la data in <div class="event-date"> ('7 Novembre
    2026, ore 19:30') e il luogo in <div class="event-location">."""
    evs = []
    for m in re.finditer(r'<article[^>]*class="[^"]*\bevento\b[^"]*"[^>]*>(.*?)(?=<article\b|$)', html0, re.S | re.I):
        if time.monotonic() > scadenza: break
        blocco = m.group(1)
        t = re.search(r'<a[^>]+title="([^"]+)"', blocco, re.I)
        titolo = pulisci(t.group(1)) if t else ''
        d = re.search(r'class="event-date".*?(\d{1,2})\s+([a-zàèéìòù]+)\s+(\d{4})(?:,?\s*ore\s+(\d{1,2})[.:](\d{2}))?', blocco, re.S | re.I) \
            or re.search(r'(\d{1,2})\s+([a-zàèéìòù]+)\s+(\d{4})(?:,?\s*ore\s+(\d{1,2})[.:](\d{2}))?', blocco, re.I)
        if not (titolo and d) or d.group(2).lower() not in MESI_IT: continue
        try:
            dt = datetime(int(d.group(3)), MESI_IT[d.group(2).lower()], int(d.group(1)))
        except ValueError:
            continue
        if d.group(4): dt = dt.replace(hour=int(d.group(4)) % 24, minute=int(d.group(5)))
        loc = re.search(r'class="event-location"[^>]*>\s*<a[^>]*>(.*?)</a>', blocco, re.S | re.I)
        biglietti = bool(re.search(r'bigliett|ticket|dice\.fm|vivaout', blocco, re.I))
        evs.append({'dt': dt, 'sum': titolo, 'desc': '', 'url': '',
                    'loc': pulisci(re.sub(r'<[^>]+>', ' ', loc.group(1))) if loc else '',
                    'ticket': biglietti})
    return evs

MESI_BREVI = {'gen': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'mag': 5, 'giu': 6,
              'lug': 7, 'ago': 8, 'set': 9, 'ott': 10, 'nov': 11, 'dic': 12}

def estrai_wix_json(url, html0, scadenza):
    """Parser per i siti Wix con widget Eventi (es. alexanderplatzjazz.com).
    1) Card HTML: <span data-hook="ev-list-item-title">TITOLO</span> seguita
       da data-hook="date" ('03 nov 2026, 20:00 – 04 nov 2026, 00:00'),
       data-hook="location" e link RSVP all'evento.
    2) Fallback: blob JSON 'events' dentro <script> con startDate ISO."""
    evs = []
    for m in re.finditer(r'<span[^>]*data-hook="ev-list-item-title"[^>]*>(.*?)</span>', html0, re.S | re.I):
        if time.monotonic() > scadenza: break
        titolo = pulisci(re.sub(r'<[^>]+>', ' ', m.group(1)))
        chunk = html0[m.end():m.end() + 3000]
        dd = re.search(r'data-hook="date"[^>]*>([^<]+)<', chunk, re.I)
        if not (titolo and dd): continue
        dm = re.search(r'(\d{1,2})\s+([a-zàèéìòù]{3})\.?\s+(\d{4})(?:,\s*(\d{1,2})[.:](\d{2}))?', htmllib.unescape(dd.group(1)), re.I)
        if not dm or dm.group(2).lower() not in MESI_BREVI: continue
        try:
            dt = datetime(int(dm.group(3)), MESI_BREVI[dm.group(2).lower()], int(dm.group(1)))
        except ValueError:
            continue
        if dm.group(4): dt = dt.replace(hour=int(dm.group(4)) % 24, minute=int(dm.group(5)))
        lu = re.search(r'data-hook="ev-rsvp-button"[^>]*href="([^"]+)"', chunk, re.I) \
             or re.search(r'href="(https?://[^" ]*event-details[^"]*)"', chunk, re.I)
        lo = re.search(r'data-hook="location"[^>]*>([^<]+)<', chunk, re.I)
        evs.append({'dt': dt, 'sum': titolo, 'desc': '',
                    'url': lu.group(1) if lu else '',
                    'loc': pulisci(lo.group(1)) if lo else '', 'ticket': bool(lu)})
    if evs: return evs
    # fallback: blob JSON dentro <script>
    def cerca(o, prof=0):
        if prof > 14 or time.monotonic() > scadenza: return
        if isinstance(o, dict):
            cfg  = o.get('config') if isinstance(o.get('config'), dict) else {}
            data = o.get('date')   if isinstance(o.get('date'), dict)   else {}
            dcfg = data.get('config') if isinstance(data.get('config'), dict) else {}
            sd = cfg.get('startDate') or o.get('startDate') or dcfg.get('startDate')
            if sd and o.get('title'):
                evs.append((o, str(sd)))
            for v in o.values(): cerca(v, prof + 1)
        elif isinstance(o, list):
            for v in o: cerca(v, prof + 1)
    for m in re.finditer(r'<script[^>]*>(.*?)</script>', html0, re.S | re.I):
        if time.monotonic() > scadenza: break
        corpo = m.group(1)
        if len(corpo) < 2000 or ('startDate' not in corpo and '"events"' not in corpo):
            continue
        dec = json.JSONDecoder()
        idx = corpo.find('"events"')
        idx = corpo.find('[', idx) if idx >= 0 else corpo.find('{')
        if idx < 0: continue
        try:
            obj, _ = dec.raw_decode(corpo, idx)
        except Exception:
            continue
        cerca(obj)
        if evs: break
    out = []
    for o, sd in evs[:CAP * 3]:
        try: dt = datetime.fromisoformat(sd.replace('Z', '+00:00'))
        except Exception: continue
        out.append({'dt': dt, 'sum': pulisci(o.get('title')),
                    'desc': pulisci(o.get('description') or o.get('about') or ''),
                    'url': '', 'loc': '',
                    'ticket': bool(re.search(r'ticket', json.dumps(o)[:3000], re.I))})
    return out

def estrai_testo(url, html0, scadenza, cfgl=None):
    """Scansione generica del testo per siti che elencano gli eventi
    come testo semplice: una riga con la data italiana ('5 novembre
    2026', 'Mar 03 Novembre', '27 Ottobre 2026, ore 21:00') seguita a
    breve dalla riga col nome dell'artista. Config per locale:
      · nome_venue  — regex delle righe da ignorare (nome del locale,
                      indirizzi, 'ACQUISTA IL BIGLIETTO', ...)"""
    vcfg = cfgl or {}
    salta = [re.compile(p, re.I) for p in
             (vcfg.get('salta_righe') or
              [r'^(?:per )?info\b', r'clicca', r'acquist', r'bigliett',
               r'^ore\s+\d', r'^(?:dalle|alle|h\.)\s*\d', r'\b(?:via|viale|piazza)\s+\w',
               r'^\+?\d[\d\s.]{7,}$', r'ingresso', r'\beur\b'])]
    if vcfg.get('nome_venue'):
        salta.append(re.compile(vcfg['nome_venue'], re.I))
    txt = re.sub(r'(?is)<(script|style|noscript|header|footer).*?</\1>', ' ', html0)
    txt = htmllib.unescape(re.sub(r'(?s)<[^>]+>', '\n', txt)).replace('\u00a0', ' ')
    righe = [r.strip() for r in txt.split('\n') if r.strip()]
    evs = []
    i = 0
    while i < len(righe):
        dm = re.match(r'^(?:(?:lun|mar|mer|gio|ven|sab|dom)\.?\s+)?(\d{1,2})\s+([a-zàèéìòù]+)(?:\s+(\d{4}))?(?:\s*,?\s*ore\s+(\d{1,2})[.:](\d{2}))?', righe[i], re.I)
        if not dm or dm.group(2).lower() not in MESI_IT:
            i += 1
            continue
        anno = int(dm.group(3) or oggi().year)
        if not dm.group(3):                        # senza anno: se già passata, è del prossimo anno
            tent = date(anno, MESI_IT[dm.group(2).lower()], int(dm.group(1)))
            if tent < oggi(): anno += 1
        try:
            dt = datetime(anno, MESI_IT[dm.group(2).lower()], int(dm.group(1)))
        except ValueError:
            i += 1
            continue
        if dm.group(4): dt = dt.replace(hour=int(dm.group(4)) % 24, minute=int(dm.group(5)))
        artista, bigl, j = '', False, i + 1
        while j < min(i + 5, len(righe)):
            nx = righe[j]
            if re.match(r'^(?:(?:lun|mar|mer|gio|ven|sab|dom)\.?\s+)?\d{1,2}\s+[a-zàèéìòù]+', nx, re.I) \
               and nx.lower() not in ('ore',) and not re.match(r'^ore\s+\d', nx, re.I):
                break                              # è iniziato l'evento successivo
            if re.search(r'ore\s+(\d{1,2})[.:](\d{2})', nx, re.I) and not dt.hour:
                om = re.search(r'ore\s+(\d{1,2})[.:](\d{2})', nx, re.I)
                dt = dt.replace(hour=int(om.group(1)) % 24, minute=int(om.group(2)))
            if re.search(r'bigliett|acquist', nx, re.I): bigl = True
            if not artista and 2 <= len(nx) <= 90 and not any(p.search(nx) for p in salta):
                artista = nx
            j += 1
        if artista:
            evs.append({'dt': dt, 'sum': artista, 'desc': '', 'url': '', 'loc': '', 'ticket': bigl})
        i = j
    return evs

def normalizza(raw, venue):
    dt = raw['dt']
    if dt.tzinfo is not None and TZ: dt = dt.astimezone(TZ)
    if dt.date() < oggi() or dt.date() > oggi() + timedelta(days=GG_MAX): return None
    artista = pulisci(re.sub(r'\s*[-–|]\s*' + re.escape(venue['nome']) + r'\s*$', '', raw.get('sum', ''), flags=re.I)) or 'Live in programma'
    txt = (artista + ' ' + raw.get('desc', '')).lower()
    biglietti = bool(raw.get('url')) or bool(raw.get('ticket')) or bool(re.search(r'bigliett|ticket|presale|abbonament', txt))
    descr = pulisci(raw.get('desc', ''))
    loc = pulisci(raw.get('loc', ''))
    note = ''
    if descr and descr.lower() != artista.lower(): note = descr[:140]
    elif loc and loc.lower() not in venue['nome'].lower(): note = loc
    ora = dt.strftime('%H:%M')
    if ora == '00:00':
        od = (CFG.get('locali', {}).get(str(venue['id']), {}) or {}).get('ora_default')
        if od: ora = str(od)
    return {'venueId': venue['id'], 'data': dt.strftime('%Y-%m-%d'),
            'ora': ora, 'artista': artista,
            'genere': classifica(txt), 'prenotazione': biglietti, 'note': note}

# ── fonti alternative (aggregatori) ─────────────────────────
RCHALLENGE = re.compile(r'(?i)just a moment|un momento, per favore|attention required|'
                        r'cdn-cgi/challenge|attendi che la tua richiesta|checking your browser')
DIRETTE = {'ical', 'wp_json', 'jsonld', 'html_apm', 'html_cdj', 'html_wix',
           'html_testo', 'html_atlantico', 'html_vinile'}

def _ldjson(html):
    """Yield dei dizionari JSON-LD di una pagina (con @graph espanso)."""
    for blocco in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(blocco.strip().rstrip(';') or 'null')
        except Exception:
            continue
        graph = data.get('@graph', [data]) if isinstance(data, dict) else data
        if isinstance(graph, list):
            for it in graph:
                if isinstance(it, dict): yield it

def estrai_zero(url, scadenza):
    """zero.eu — pagina "luogo": il JSON-LD contiene un array 'events'
    con gli eventi in programma (nome, ISO startDate, descrizione)."""
    html = get_text(url)
    if not html: return []
    out = []
    for it in _ldjson(html):
        for ev in it.get('events') or []:
            if not isinstance(ev, dict): continue
            sd = str(ev.get('startDate', '') or '')
            if not sd: continue
            try: dt = datetime.fromisoformat(sd.replace('Z', '+00:00'))
            except Exception: continue
            out.append({'dt': dt, 'sum': str(ev.get('name', '') or ''),
                        'desc': str(ev.get('description', '') or ''),
                        'url': str(ev.get('url', '') or ''), 'loc': '',
                        'ticket': bool(ev.get('url'))})
    return out

def estrai_songkick(slug, venue_nome, scadenza):
    """songkick.com — pagina venue con JSON-LD MusicEvent (futuri e
    passati: il filtro data avviene in normalizza). Il nome evento
    spesso finisce con ' @ Nome Venue': viene tolto."""
    html = get_text('https://www.songkick.com/venues/' + slug)
    if not html: return []
    out = []
    for it in _ldjson(html):
        if 'MusicEvent' not in str(it.get('@type', '')): continue
        sd = str(it.get('startDate', '') or '')
        if not sd: continue
        try: dt = datetime.fromisoformat(sd.replace('Z', '+00:00'))
        except Exception: continue
        nome = re.sub(r'\s*@\s*' + re.escape(venue_nome) + r'\s*$', '',
                      str(it.get('name', '') or ''), flags=re.I)
        out.append({'dt': dt, 'sum': nome, 'desc': '',
                    'url': str(it.get('url', '') or ''), 'loc': '',
                    'ticket': bool(it.get('url'))})
    return out

def estrai_romatoday(slug, venue_nome, scadenza):
    """romatoday.it — pagina 'location': link <a href="/eventi/<titolo>-
    <gg>-<mese>-<anno>.html"> con il titolo in un nodo di testo interno;
    la data sta nello slug dell'articolo. Il sito limita le richieste
    ripetute: un secondo tentativo dopo una pausa se la prima risposta
    è vuota."""
    url = 'https://www.romatoday.it/eventi/location/' + slug
    html = get_text(url)
    if not html and time.monotonic() < scadenza:
        time.sleep(3)
        html = get_text(url)
    if not html: return []
    out, visti = [], set()
    for m in re.finditer(r'href="(/eventi/[^"#?]*\.html)"', html):
        if time.monotonic() > scadenza: break
        href = m.group(1)
        dm = re.search(r'-(\d{1,2})-([a-zàèéìòù]+)-(\d{4})\.html$', href, re.I)
        if not dm or dm.group(2).lower() not in MESI_IT: continue
        d, mese, anno = int(dm.group(1)), dm.group(2).lower(), int(dm.group(3))
        chunk = html[m.end():m.end() + 2000]
        fine = chunk.find('</a>')
        corpo = chunk[:fine] if fine >= 0 else chunk[:800]
        candidati = [pulisci(x) for x in re.findall(r'>([^<>]{3,120})<', corpo)]
        titolo = next((c for c in candidati if len(c) >= 3
                       and not re.fullmatch(r'[\s\d.,€%]+', c)), '')
        if not titolo: continue
        chiave = (anno, mese, d, titolo.casefold())
        if chiave in visti: continue
        visti.add(chiave)
        # 'Masini in concerto al Palazzo dello Sport' → 'Masini'
        # (prova col nome completo e con quello senza la parte tra parentesi)
        m2 = re.match(r'([^()]+?)\s*\(', venue_nome)
        base_t = titolo
        for nv in [venue_nome] + ([m2.group(1).strip()] if m2 else []):
            t2 = re.sub(r'\s*(?:in concerto|dal vivo)?\s+al\s+' + re.escape(nv) + r'\s*$',
                        '', base_t, flags=re.I).strip()
            if t2: base_t = t2
        base_t = re.sub(r'\s+(?:in concerto|dal vivo)\s*$', '', base_t, flags=re.I).strip()
        try: dt = datetime(anno, MESI_IT[mese], d)
        except ValueError: continue
        out.append({'dt': dt, 'sum': base_t or titolo, 'desc': '',
                    'url': 'https://www.romatoday.it' + href, 'loc': '', 'ticket': True})
    # articoli doppi dello stesso spettacolo ('Masini…' vs 'Marco Masini tour…'):
    # a parità di data resta il titolo più completo
    filtrati = []
    for e in out:
        doppio = next((x for x in filtrati
                       if x['dt'].date() == e['dt'].date() and
                       (e['sum'].casefold() in x['sum'].casefold() or
                        x['sum'].casefold() in e['sum'].casefold())), None)
        if doppio:
            if len(e['sum']) > len(doppio['sum']): filtrati[filtrati.index(doppio)] = e
        else:
            filtrati.append(e)
    return filtrati


def estrai_atlantico(url, html0, scadenza, cfgl=None):
    """Parser per atlanticoliveroma.it: la homepage elenca i concerti
    come blocco con il titolo in grassetto (<strong>ARTISTA</strong>)
    seguito da un elenco puntato con la data COMPLETA ('7 Novembre
    2026'), il nome della location e i link info/biglietti. La data ha
    sempre l'anno: nessuna ricostruzione."""
    vcfg = cfgl or {}
    salta = [re.compile(p, re.I) for p in (vcfg.get('salta_righe') or
             [r'atlantico', r'eventi in programma', r'^(?:per )?info\b',
              r'clicca', r'acquist', r'bigliett', r'\b(?:via|viale|piazza)\s+\w'])]
    txt = re.sub(r'(?is)<(script|style|noscript|header|footer).*?</\1>', ' ', html0)
    txt = htmllib.unescape(re.sub(r'(?s)<[^>]+>', '\n', txt)).replace('\u00a0', ' ')
    righe = [r.strip() for r in txt.split('\n') if r.strip()]
    evs, prese = [], set()
    for i, r in enumerate(righe):
        dm = re.match(r'(\d{1,2})\s+([a-zàèéìòù]+)\s+(\d{4})'
                      r'(?:[·,;\s]+ore?\s+(\d{1,2})[.:](\d{2}))?', r, re.I)
        if not dm or dm.group(2).lower() not in MESI_IT:
            continue
        artista = ''
        for j in range(i - 1, max(-1, i - 4), -1):   # il titolo sta subito sopra la data
            x = righe[j]
            if re.match(r'^(?:(?:lun|mar|mer|gio|ven|sab|dom)\.?\s+)?\d{1,2}\s+[a-zàèéìòù]+\s+\d{4}', x, re.I):
                break                                # data precedente: blocco senza titolo
            if any(p.search(x) for p in salta):
                continue                             # righe di servizio: si risale
            if 2 <= len(x) <= 90:
                artista = x
                break
        if not artista:
            continue
        try:
            dt = datetime(int(dm.group(3)), MESI_IT[dm.group(2).lower()], int(dm.group(1)))
        except ValueError:
            continue
        if dm.group(4):
            dt = dt.replace(hour=int(dm.group(4)) % 24, minute=int(dm.group(5)))
        chiave = (dt.date(), artista.casefold())
        if chiave in prese:
            continue
        prese.add(chiave)
        evs.append({'dt': dt, 'sum': artista, 'desc': '', 'url': '', 'loc': '',
                    'ticket': bool(re.search(r'acquist|bigliett', html0, re.I))})
    return evs


def estrai_vinile(url, html0, scadenza, cfgl=None):
    """Parser per vinileroma.it: le serate sono elencate come blocchi
    'Gio 5 nov' + titolo della serata + ('Live ARTISTA' / 'DJ set ...')
    + link 'Vedi la serata'. La data non ha l'anno: si assume l'anno
    corrente e, se la data risulta già passata, quello successivo.
    Se la pagina iniziale non dà risultati prova anche /programma."""
    vcfg = cfgl or {}
    salta = [re.compile(p, re.I) for p in (vcfg.get('salta_righe') or
             [r'line-?up in arrivo', r'^vedi la serata', r'^aperitivo', r'^cena\b',
              r'^club\b', r'^pista', r'tutto il programma', r'^prenota', r'lista',
              r'bigliett', r'^\d+$', r'^→$'])]
    RE_DATA = re.compile(r'(?i)(lun|mar|mer|gio|ven|sab|dom)[\s,]*(\d{1,2})\s*·?\s*'
                         r'(gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic)[a-zàèéìòù]*')

    def da_html(html, base):
        out = []
        for m in RE_DATA.finditer(html):
            if time.monotonic() > scadenza:
                break
            g, mese = int(m.group(2)), MESI_BREVI[m.group(3).lower()]
            anno = oggi().year
            if date(anno, mese, g) < oggi():
                anno += 1
            try:
                dt = datetime(anno, mese, g)
            except ValueError:
                continue
            chunk = html[m.end():m.end() + 2500]
            testo = htmllib.unescape(re.sub(r'(?s)<[^>]+>', '\n', chunk)).replace('\u00a0', ' ')
            righe = [r.strip() for r in testo.split('\n') if r.strip()]
            titolo, desc = '', ''
            for x in righe[:10]:
                if re.match(r'(?i)^(?:(?:lun|mar|mer|gio|ven|sab|dom)\.?\s+)?\d{1,2}\s+'
                            r'(?:gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic)', x):
                    break                        # è iniziata la serata successiva
                if any(p.search(x) for p in salta):
                    continue
                if not titolo and 2 <= len(x) <= 60:
                    titolo = x
                    continue
                if titolo and not desc and 2 <= len(x) <= 100 and re.match(r'^(?:live|dj ?set)\b', x, re.I):
                    desc = x
                    break
            if not titolo:
                continue
            lu = re.search(r'href="([^"]+)"', chunk)
            out.append({'dt': dt, 'sum': titolo, 'desc': desc,
                        'url': urllib.parse.urljoin(base, lu.group(1)) if lu else '',
                        'loc': '', 'ticket': bool(lu)})
        return out

    base = base_url(url)
    evs = da_html(html0, base)
    if not evs and time.monotonic() < scadenza:
        pg = urllib.parse.urljoin(base, '/programma')
        evs = da_html(get_text(pg), pg)
    # gli stessi blocchi possono comparire più volte (home + widget): dedup
    out, visti = [], set()
    for e in evs:
        k = (e['dt'].date(), e['sum'].casefold())
        if k in visti:
            continue
        visti.add(k)
        out.append(e)
    return out

# ── deduplica globale (novità v9) ────────────────────────────
# Lo stesso spettacolo può comparire con due titoli diversi (fonti
# diverse, o doppi rimasti in archivio da estrazioni passate: es.
# 'Masini in concerto al Palazzo dello Sport' e 'Marco Masini tour
# 2026 al Palazzo dello Sport'). A fine run, a parità di locale e
# data, si fondono gli eventi i cui artisti condividono le stesse
# parole significative. Se entrambi hanno un orario reale (non
# mezzanotte) e diverso, restano due show distinti.
_STOPW_ART = {'in','al','alla','ai','con','di','del','della','dei','dal',
              'vivo','live','dj','set','tour','concerto','concerti',
              'speciale','serata','serate','roma','palazzo','dello',
              'delle','sala','teatro','estate'}

def _token_artista(nome):
    t = re.sub(r'[^\w\sàèéìòù]', ' ', (nome or '').lower())
    return {w for w in t.split() if len(w) > 2 and w not in _STOPW_ART and not w.isdigit()}

def _pulisci_coda_titolo(nome, venue_nome):
    """Toglie le code di servizio: 'in concerto al …', 'tour 2026 al …'."""
    t = (nome or '').strip()
    code = [venue_nome or '']
    m2 = re.match(r'([^()]+?)\s*\(', venue_nome or '')
    if m2: code.append(m2.group(1).strip())
    for nv in code:
        if not nv: continue
        t = re.sub(r'\s*(?:tour\s+\d{4}|in concerto|dal vivo|live)?\s+al\s+'
                   + re.escape(nv) + r'\s*$', '', t, flags=re.I).strip()
    t = re.sub(r'\s+(?:in concerto|dal vivo|live)\s*$', '', t, flags=re.I).strip()
    t = re.sub(r'\s+tour\s+\d{4}\s*$', '', t, flags=re.I).strip()
    return t

def fusi_doppioni(eventi, nomi_venue):
    """Fonde i doppioni (stesso locale, stessa data, stesso spettacolo).
    Ritorna (eventi_fusi, numero_doppioni_eliminati)."""
    fuori, fusi = [], 0
    gruppi = {}
    for e in eventi:
        gruppi.setdefault((e.get('venueId'), e.get('data')), []).append(e)
    for chiave, gruppo in gruppi.items():
        vn = nomi_venue.get(chiave[0], '')
        resta = []
        for e in sorted(gruppo, key=lambda x: x['ora']):
            tok = _token_artista(e.get('artista', ''))
            doppio = None
            for x in resta:
                xt = _token_artista(x.get('artista', ''))
                if not tok or not xt: continue
                unione = tok | xt
                sim = len(tok & xt) / len(unione) if unione else 0
                if sim < 0.6 and not (tok <= xt or xt <= tok):
                    continue
                if x['ora'] != '00:00' and e['ora'] != '00:00' and x['ora'] != e['ora']:
                    continue          # orari reali diversi: due show distinti
                doppio = x
                break
            if doppio is None:
                resta.append(e)
                continue
            fusi += 1
            cand = [(_pulisci_coda_titolo(doppio['artista'], vn), doppio),
                    (_pulisci_coda_titolo(e['artista'], vn), e)]
            best = max(cand, key=lambda c: len(c[0]))[0]
            doppio['artista'] = best or doppio['artista']
            if not doppio.get('note') and e.get('note'):
                doppio['note'] = e['note']
            elif e.get('note') and len(e['note']) > len(doppio.get('note') or ''):
                doppio['note'] = e['note']
            doppio['prenotazione'] = bool(doppio.get('prenotazione')) or bool(e.get('prenotazione'))
            if doppio['ora'] == '00:00' and e['ora'] != '00:00':
                doppio['ora'] = e['ora']
        fuori.extend(resta)
    return fuori, fusi

# ── per-locale ───────────────────────────────────────────────
def estrai_locale(v):
    cfgl = CFG.get('locali', {}).get(str(v['id']), {})
    if cfgl.get('attivo') is False:
        return [], 'disattivato: ' + cfgl.get('motivo', ''), None
    url = cfgl.get('url') or v.get('website') or ''
    if not url: return [], 'nessun sito', None
    base = base_url(url)
    scadenza = time.monotonic() + 65          # budget massimo per locale
    html0 = get_text(url) if time.monotonic() < scadenza else ''
    # fallback: se il dominio non risponde, prova con/senza "www."
    if not html0 and time.monotonic() < scadenza:
        alt = url.replace('://www.', '://') if '://www.' in url else url.replace('://', '://www.', 1)
        if alt != url:
            html0 = get_text(alt)
            if html0:
                url, base = alt, base_url(alt)
    # sito ufficiale bloccato (protezione anti-bot o risposta anomala):
    # si annota la causa, si azzera l'HTML e si proseguono comunque le
    # fonti alternative (zero.eu, songkick, romatoday) come ripiego
    blocco = ''
    if html0 and len(html0) < 800:
        blocco = (f'sito ufficiale: risposta anomala ({len(html0)} caratteri — '
                  'probabile protezione anti-bot)')
        html0 = ''
    elif html0 and RCHALLENGE.search(html0):
        blocco = 'sito ufficiale protetto da verifica anti-bot (challenge JavaScript)'
        html0 = ''
    wp = bool(re.search(r'wp-json|wp-content|tribe', html0, re.I))
    strat = cfgl.get('strategie', ['ical', 'wp_json', 'jsonld', 'html_testo'])
    tentato = []
    for s in strat:
        if time.monotonic() > scadenza: break
        if s in DIRETTE and not html0:
            continue                       # strategie che leggono html0: niente da leggere
        if s == 'wp_json' and not wp:
            tentato.append('wp_json: saltato (non WordPress)')
            continue
        if s == 'html_zero' and not cfgl.get('zero'): continue
        if s == 'html_songkick' and not cfgl.get('songkick'): continue
        if s == 'html_romatoday' and not cfgl.get('romatoday'): continue
        try:
            raw = {'ical':        lambda: estrai_ical(base, html0, scadenza),
                   'wp_json':     lambda: estrai_wp_json(base, scadenza),
                   'jsonld':      lambda: estrai_jsonld(url, html0, scadenza),
                   'html_apm':    lambda: estrai_apm_card(url, html0, scadenza),
                   'html_cdj':    lambda: estrai_cdj_article(url, html0, scadenza),
                   'html_wix':    lambda: estrai_wix_json(url, html0, scadenza),
                   'html_testo':  lambda: estrai_testo(url, html0, scadenza, cfgl),
                   'html_atlantico': lambda: estrai_atlantico(url, html0, scadenza, cfgl),
                   'html_vinile': lambda: estrai_vinile(url, html0, scadenza, cfgl),
                   'html_zero':   lambda: estrai_zero(cfgl['zero'], scadenza),
                   'html_songkick': lambda: estrai_songkick(cfgl['songkick'], v['nome'], scadenza),
                   'html_romatoday': lambda: estrai_romatoday(cfgl['romatoday'], v['nome'], scadenza)}[s]()
        except Exception as ex:
            tentato.append(f'{s}: errore {type(ex).__name__}')
            continue
        evs = [e for e in (normalizza(r, v) for r in raw) if e]
        if evs:
            dedup, visti = [], set()
            for e in evs:
                k = (e['data'], e['ora'], e['artista'].casefold())
                if k not in visti: visti.add(k); dedup.append(e)
            dedup.sort(key=lambda e: (e['data'], e['ora']))
            return dedup[:CAP], f'{s} · {len(dedup)} eventi' + (f' — {blocco}' if blocco else ''), s
        tentato.append(f'{s}: 0 eventi')
    if blocco and tentato:
        return [], blocco + ' · fonti alternative: ' + '; '.join(tentato), None
    if blocco:
        return [], blocco, None
    if not html0 and not tentato:
        motivo = ERR_ULTIMO.get(url, 'causa non determinata')
        return [], f'sito non raggiungibile ({motivo})', None
    return [], 'fonte non disponibile (' + '; '.join(tentato) + ')', None

# ── main ─────────────────────────────────────────────────────
def main():
    locali = json.loads((BASE / 'data' / 'locali.json').read_text(encoding='utf-8'))
    vecchi = json.loads((BASE / 'data' / 'eventi.json').read_text(encoding='utf-8'))
    og = oggi().isoformat()
    print(f"SuonaRoma · aggiornamento eventi del {og} · finestra {GG_MAX} giorni\n")

    with cf.ThreadPoolExecutor(PAR) as ex:
        risultati = dict(ex.map(lambda v: (v['id'], estrai_locale(v)), locali))

    passati_eliminati = sum(1 for e in vecchi if e.get('data', '') < og)
    nuovi, report = [], []
    for v in locali:
        evs, stato, fonte = risultati.get(v['id'], ([], 'non elaborato', None))
        presenti = [e for e in vecchi if e.get('venueId') == v['id'] and e.get('data', '') >= og]
        if evs:
            nuovi.extend(evs)
            report.append({'id': v['id'], 'nome': v['nome'], 'fonte': fonte, 'stato': 'ok',
                           'estratti': len(evs), 'rimossi_vecchi': len(presenti)})
            print(f"  ✔ {v['nome'][:34]:34} {stato}  (+{len(evs)} al posto di {len(presenti)})")
        else:
            nuovi.extend(presenti)
            report.append({'id': v['id'], 'nome': v['nome'], 'fonte': None, 'stato': stato, 'estratti': 0})
            print(f"  · {v['nome'][:34]:34} {stato[:74]} — dati attuali invariati ({len(presenti)})")

    nuovi = [e for e in nuovi if e['data'] >= og]   # doppia sicurezza: nessun evento passato
    nomi_venue = {v['id']: v['nome'] for v in locali}
    nuovi, doppioni_fusi = fusi_doppioni(nuovi, nomi_venue)
    if doppioni_fusi:
        print(f"\n  ⚗ doppioni fusi: {doppioni_fusi} (stesso locale, stessa data, stesso spettacolo)")
    nuovi.sort(key=lambda e: (e['data'], e['ora'], e['venueId']))
    ok = sum(1 for r in report if r['stato'] == 'ok')

    rep = {'eseguito_il': datetime.now(TZ).isoformat(timespec='seconds') if TZ else datetime.now().isoformat(timespec='seconds'),
           'giorni_avanzo': GG_MAX, 'locali_aggiornati': ok, 'locali_totali': len(report),
           'eventi_totali': len(nuovi), 'eventi_passati_eliminati': passati_eliminati,
           'doppioni_fusi': doppioni_fusi, 'dettaglio': report}
    (BASE / 'tools' / 'report.json').write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')

    righe = ['# Rapporto aggiornamento eventi SuonaRoma', '', f"- Eseguito il: **{rep['eseguito_il']}** (ora italiana)",
             f"- Locali aggiornati: **{ok} / {len(report)}**", f"- Eventi totali in archivio: **{len(nuovi)}** (solo da oggi in poi)",
             f"- Eventi passati eliminati: **{passati_eliminati}**", f"- Doppioni fusi: **{doppioni_fusi}**", '', '| Locale | Fonte / stato | Eventi |', '|---|---|---|']
    for r in report:
        righe.append(f"| {r['nome']} | {r['fonte'] or r['stato']} | {r['estratti']} |")
    (BASE / 'tools' / 'report.md').write_text('\n'.join(righe) + '\n', encoding='utf-8')

    if not SOLO_REPORT:
        (BASE / 'data' / 'eventi.json').write_text(json.dumps(nuovi, ensure_ascii=False), encoding='utf-8')
        js = (BASE / 'data' / 'eventi.js').read_text(encoding='utf-8')
        header = js.split('window.EVENTI_DATA', 1)[0]
        (BASE / 'data' / 'eventi.js').write_text(header + 'window.EVENTI_DATA = ' + json.dumps(nuovi, ensure_ascii=False) + ';\n', encoding='utf-8')
        # v13.1: specchio del timestamp dell'ultima estrazione, letto dal sito
        # anche in apertura diretta (file://) per la riga "Aggiornato ..."
        meta = {'aggiornato': rep['eseguito_il'], 'eventi': len(nuovi),
                'locali_attivi': sum(1 for r in report if r.get('estratti', 0) > 0)}
        (BASE / 'data' / 'meta.js').write_text(
            '// Generato da tools/aggiorna_eventi.py — ultimo aggiornamento riuscito\n'
            'window.SUONAROMA_META = ' + json.dumps(meta, ensure_ascii=False) + ';\n', encoding='utf-8')
        print(f"\nSalvati data/eventi.json, data/eventi.js e data/meta.js ({len(nuovi)} eventi).")
    else:
        print(f"\nModalità --solo-report: file dati NON modificati ({len(nuovi)} eventi risultanti).")
    print(f"Rapporto: tools/report.json · tools/report.md  |  locali aggiornati: {ok}/{len(report)}")

if __name__ == '__main__':
    main()

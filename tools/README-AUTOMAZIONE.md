════════════════════════════════════════════════════════════
 AUTOMAZIONE AGGIORNAMENTO EVENTI · SuonaRoma
════════════════════════════════════════════════════════════

Un agente automatico (GitHub Actions) aggiorna ogni giorno, al
primo run dalle 14:00 in poi (ora italiana), gli eventi di
SuonaRoma: si collega ai siti ufficiali dei locali, estrae la
programmazione e aggiorna i file data/eventi.json e data/eventi.js
con un commit automatico.

── COSA C'È NELLA CARTELLA ─────────────────────────────────

  .github/workflows/aggiorna-eventi.yml   il job giornaliero
  tools/aggiorna_eventi.py                l'estrattore (solo
                                          librerie standard
                                          Python: zero install)
  tools/config_scraping.json              configurazione per
                                          locale (URL alternativi,
                                          strategie, note)
  tools/report.json / report.md           rapporto dell'ultima
                                          esecuzione

── INSTALLAZIONE (una volta sola) ──────────────────────────

 1. Copia nella radice del repository:
      · la cartella .github/ (con dentro workflows/aggiorna-eventi.yml)
      · la cartella tools/
    (se il sito è nella radice del repo, va bene così; se è in una
    sottocartella, vedi più avanti "SITO IN SOTTOCARTELLA")

 2. Commit e push:
      git add .github tools
      git commit -m "Automazione aggiornamento eventi"
      git push

 3. Su GitHub: Settings → Actions → General →
    "Allow all actions and reusable workflows" (di solito già attivo).

 4. Prova manuale: scheda Actions → "Aggiorna eventi" → Run workflow.
    Apri l'esecuzione: nel riquadro "Summary" trovi il rapporto
    completo locale per locale.

── COME FUNZIONA ───────────────────────────────────────────

 · Il job viene controllato ogni 30 minuti e parte al primo run
   dalle 14:00 in poi (ora di Roma): GitHub a volte ritarda i run
   programmati o salta ore intere, così l'aggiornamento giornaliero
   arriva comunque. Un controllo sul rapporto evita doppi
   aggiornamenti nella stessa giornata; l'orario resta corretto
   anche al cambio ora legale/solare (il cron di GitHub usa UTC).
   L'avvio manuale (Run workflow) esegue l'estrazione a qualsiasi
   ora, proprio per poter provare.
 · Per ogni locale prova nell'ordine configurato (di default:
     1) feed iCal (.ics, tipico dei siti WordPress con The Events
        Calendar)
     2) API REST The Events Calendar (/wp-json/tribe/...)
     3) dati strutturati schema.org (JSON-LD) nelle pagine eventi
     4) scansione generica del testo (riga data + riga artista)
   Per alcuni locali sono configurati parser dedicati:
     html_apm  → auditorium.com (schede event-card)
     html_cdj  → casadeljazz.com (articoli WordPress)
     html_wix  → alexanderplatzjazz.com (widget Eventi di Wix)
   Fonti alternative quando il sito ufficiale è bloccato:
     html_zero → zero.eu, pagina "luogo" del locale (JSON-LD eventi)
     html_songkick → songkick.com, pagina venue del locale
     html_romatoday → romatoday.it, pagina "location" del luogo
 · La prima strategia che estrae eventi vince; il rapporto indica
   sempre la fonte che ha prodotto gli eventi.
 · Se il sito ufficiale è bloccato (challenge anti-bot, 403, IP
   datacenter bloccati) si tentano le fonti alternative configurate
   in config (zero.eu / songkick / romatoday); il rapporto indica
   anche il motivo del blocco del sito ufficiale.
 · Se la homepage non risponde si prova anche con/senza "www.".
 · INTEGRAZIONE ("solo futuro"): l'archivio contiene SOLO eventi
   da oggi in poi. Per i locali estratti con successo gli eventi
   futuri vengono SOSTITUITI con quelli freschi; i locali la cui
   fonte non risponde NON vengono modificati (il sito non resta
   mai vuoto); gli eventi che diventano passati vengono eliminati
   automaticamente a ogni esecuzione. L'estrazione copre 365
   giorni avanti, fino a 120 eventi per locale.
 · DEDUPLICA GLOBALE (v9): a fine run, a parità di locale e data,
   gli eventi doppi (stesso spettacolo con titoli diversi, es.
   "Masini in concerto al …" e "Marco Masini tour 2026 al …") si
   fondono in un unico evento; se entrambi hanno un orario reale e
   diverso restano due show distinti. Il numero di fusioni è nel
   rapporto ("Doppioni fusi").
 · Se i dati non cambiano, nessun commit (il repo resta pulito).
 · Il rapporto dell'ultima esecuzione è in tools/report.md e viene
   mostrato anche nella pagina dell'esecuzione di Actions.

── COME LEGGERE IL RAPPORTO (DIAGNOSI) ─────────────────────

 · ok / "html_apm · 13 eventi"            — estrazione riuscita
 · sito non raggiungibile (DNS: il
   dominio non risolve)                   — l'indirizzo è sbagliato
                                            o il dominio è stato
                                            dismesso: serve il nuovo
                                            URL ufficiale
 · sito non raggiungibile (timeout)       — il server non risponde
                                            (giù o troppo lento)
 · sito non raggiungibile (HTTP 403)      — blocco anti-bot: il
                                            sito rifiuta i client
                                            non-browser
 · pagina di verifica anti-bot
   (challenge JavaScript)                 — protezione che richiede
                                            un browser vero: non
                                            aggirabile via HTTP
 · risposta anomala del server            — risposte sospette
                                            (es. HTTP 202 con corpo
                                            quasi vuoto)
 · fonte non disponibile (...)            — il sito risponde ma non
                                            espone eventi leggibili
 · disattivato: ...                       — locale escluso in config
                                            (dominio morto, solo
                                            social, ecc.)

── LIMITI NOTI (aggiornati nov. 2026, v9) ──────────────────

 · Challenge JavaScript (Cloudflare/openresty): Fonclea e L'Asino
   che Vola restano illeggibili via HTTP e non hanno alternative;
   Trenta Formiche e Monk sono coperti da zero.eu.
 · Blocco anti-bot lato TLS/403: Geronimo's (geronimoslive.com);
   largovenue.com diretto è 403 ma è coperto da zero.eu; Vinile e
   Stazione Birra non aprono la connessione da IP datacenter.
 · Nessuna fonte leggibile: Big Mama (sito in errore 500, eventi
   solo su Facebook) e Argot (solo Instagram): disattivati in
   config con il motivo.
 · Siti su hosting italiano (Alcazar, Atlantico, Blackmarket, Lian,
   Fanfulla) irraggiungibili da alcune reti datacenter: dal runner
   GitHub potrebbero invece rispondere — il rapporto lo dirà.
 · Al Muretto e L'Asino che Vola: la pagina zero.eu esiste ma a
   nov. 2026 non elenca ancora eventi futuri.
 · Facebook e Instagram non sono leggibili senza login.
 · Verifica sempre gli orari prima di pubblicare: l'estrazione è
   automatica e può sbagliare (soprattutto i generi, che sono
   stimati dalle parole nel titolo).

── PERSONALIZZARE ──────────────────────────────────────────

 · Cambiare l'ora: modifica il numero 14 nel passaggio
   "È l'ora" del workflow.
 · Escludere un locale: in tools/config_scraping.json aggiungi
     "17": { "attivo": false, "motivo": "..." }
 · Usare un URL diverso per un locale:
     "4": { "url": "https://www.esempio.it", "attivo": true }
 · Limitare le strategie di un locale (ical, wp_json, jsonld,
   html_testo, html_apm, html_cdj, html_wix):
     "3": { "strategie": ["ical"] }
 · Orario predefinito per i siti che non pubblicano l'orario:
     "22": { "ora_default": "21:30" }

── SITO IN SOTTOCARTELLA ───────────────────────────────────

 Lo script individua la cartella del sito a partire dalla sua
 posizione (../data rispetto a tools/). Se metti tools/ dentro la
 sottocartella del sito (es. suonaroma/tools/), funziona senza
 modifiche; il workflow instead va sempre in .github/workflows/
 alla radice del repo, eventualmente con un passaggio di
 working-directory. Scrivimi e te lo adatto.

── NOTE LEGALI / BONARIENZA ────────────────────────────────

 Lo script si limita a leggere pagine pubbliche, con pausa tra le
 richieste e senza aggirare protezioni: usa uno User-Agent da
 browser solo perché molti hosting bloccano con 403 qualsiasi
 user-agent dichiaratosi bot. Per un uso intensivo chiedi sempre
 ai locali il consenso o i dati in formato aperto (feed iCal
 gratuiti, se chiesti).

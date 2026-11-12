# GUIDA CLOUDFLARE · SuonaRoma Live senza server (v16)

Architettura: **GitHub** esegue l'estrazione degli eventi ogni giorno
(gratis, al posto del VPS) e **Cloudflare Pages** ospita il sito (gratis,
HTTPS e CDN incluse). Niente VPS, niente SSH, niente FTP, niente token
API da creare: si configura tutto una volta col mouse e poi funziona solo.

```
GitHub (gratis)                              Cloudflare Pages (gratis)
┌─────────────────────────────────┐          ┌────────────────────────────┐
│ repository "suonaroma-live"     │  push    │ build: bash pages-build.sh │
│ Actions: estrazione ogni giorno │─────────▶│ https://suonaroma-live     │
│ (tools/aggiorna_eventi.py)      │  commit  │                 .pages.dev │
└─────────────────────────────────┘          └────────────────────────────┘
   controllo ogni 30 min; al primo                     │
   giro dopo le 14:00 di Roma estrae                   ▼
   e aggiorna i dati                    www.tuodominio.it (opzionale)
```

**Perché c'è GitHub?** L'estrattore è un programma Python: Cloudflare non
può eseguirlo a orari prestabiliti (i suoi Workers non girano Python
completo). GitHub Actions è gratuito, esegue Python e si avvia da solo:
fa esattamente quello che faceva il VPS, senza un server da accendere,
aggiornare e proteggere. I dati restano pubblici e il progetto non
contiene credenziali, quindi non c'è nulla da proteggere.

**Costi: 0 €** · Tempo di setup: 20-30 minuti · Ti servono due account
gratuiti: uno su [github.com](https://github.com) e uno su
[dash.cloudflare.com](https://dash.cloudflare.com).

I limiti gratuiti sono ampiamente sufficienti: Cloudflare Pages concede
~500 build al mese (qui ne servono 30-40), GitHub Actions è illimitato sui
repository pubblici (e sufficiente anche sui privati).

---

## Passo 1 · Crea il repository su GitHub

Il progetto va caricato su GitHub una sola volta; dopo ci pensano i bot.

### Strada consigliata: GitHub Desktop (tutto col mouse)

1. Installa **GitHub Desktop** ([desktop.github.com](https://desktop.github.com)),
   aprilo → **Sign in to GitHub.com**. Si apre il browser: accedi, oppure
   crea l'account con **Create an account** (gratuito).
2. Menu **File → Add local repository → Choose…**: seleziona la cartella
   `suonaroma-live` estratta dallo zip. Ti dirà che non è un repository:
   clicca **create and add a repository** (Name: `suonaroma-live`) → **Create repository**.
3. Clicca il pulsante **Publish repository**: lascia il nome
   `suonaroma-live`, scegli se spuntare "Keep this code private" (vedi
   riquadro qui sotto) → **Publish**.
4. Verifica aprendo `github.com/<tuo-utente>/suonaroma-live`: devi vedere
   `index.html`, `pages-build.sh`, le cartelle `data/`, `tools/`,
   `.github/`.

> **Pubblico o privato?** Il repository **pubblico** è consigliato: i dati
> sono comunque pubblici (è l'agenda di eventi del sito), il progetto non
> contiene segreti (nessuna password, nessun token: verificato file per
> file) e GitHub Actions è illimitato. Il **privato** funziona uguale,
> ma consuma i minuti gratuiti di Actions (~1.100 al mese sui 2.000
> gratis: va bene lo stesso, solo un po' più stretto).

### In alternativa: dal browser, senza installare nulla

1. Su github.com premi il "+" in alto a destra → **New repository** →
   nome `suonaroma-live` → scegli Public/Private → **Create repository**
   (lascialo vuoto, senza README).
2. Dentro il repository: **Add file → Upload files** e trascina prima i
   file della cartella principale (`index.html`, `pages-build.sh`,
   `.gitattributes`, `GUIDA-CLOUDFLARE.md`, `LEGGIMI-NUOVO-SITO.txt`),
   poi quelli di `data/`, poi `tools/`, poi `.github/workflows/` —
   trascinando le cartelle nella pagina i percorsi si ricreano da soli.
   → **Commit changes** (un commit per gruppo va benissimo).

## Passo 2 · Crea il progetto Cloudflare Pages

1. Accedi a [dash.cloudflare.com](https://dash.cloudflare.com) → menu
   laterale **Compute (Workers) → Workers & Pages** (nelle versioni del
   menu in cui esiste solo "Workers & Pages", usa quello) →
   **Create application** → scheda **Pages** → **Connect to Git**.
2. Scegli **GitHub**: si apre la pagina di consenso "Install Cloudflare
   Pages" → **Only select repositories** → spunta `suonaroma-live` →
   **Install** (è l'integrazione ufficiale Cloudflare↔GitHub: niente
   password né token da digitare).
3. Seleziona il repository appena tornati in dashboard → **Begin setup**:
   - **Project name**: `suonaroma-live` (se il nome è già occupato da
     qualcuno, va bene qualunque altro: diventa l'indirizzo
     `<nome-progetto>.pages.dev`)
   - **Production branch**: `main`
   - **Framework preset**: `None`
   - **Build command**: `bash pages-build.sh`
   - **Build output directory**: `/out`
   - Variabili d'ambiente: nessuna
4. **Save and Deploy**: dopo circa un minuto, nella scheda Deployments
   compare il primo deploy verde e il sito è online su
   `https://<nome-progetto>.pages.dev` con tutti gli eventi già caricati
   (sono dentro il repository).

Da questo momento ogni aggiornamento del repository (anche quello
notturno del bot) fa ricostruire e ripubblicare il sito da solo.

## Passo 3 · Prova l'aggiornamento automatico

1. Su github.com apri il repository → scheda **Actions** → a sinistra
   **Aggiorna eventi** → pulsante **Run workflow** → **Run workflow**.
2. Il run dura 3-6 minuti (estrae dai 24 siti dei locali). Alla fine
   diventa verde ✓; aprendolo, nel riquadro "Summary" trovi il rapporto
   completo dell'estrazione (locali aggiornati, eventi salvati).
3. Il run crea il commit "Aggiornamento automatico eventi …": Cloudflare
   Pages lo rileva e ripubblica (nuova voce verde in **Deployments**).
4. Apri il sito: la riga "Aggiornato …" nell'intestazione porta
   l'orario di pochi minuti fa. Tutto collegato: da qui in poi gira da solo.

## Passo 4 · Come funziona da adesso in poi

Tutti i giorni, senza che tu faccia nulla:

- GitHub controlla ogni 30 minuti; al **primo controllo dopo le 14:00 di
  Roma** esegue l'estrazione (i controlli notturni escono subito per una
  guardia interna, e l'orario resta giusto anche al cambio ora legale/solare).
- I dati aggiornati vengono salvati nel repository con un commit del bot
  ("Aggiornamento automatico eventi …"): lo storico di ogni giorno resta
  consultabile e ripristinabile per sempre.
- Cloudflare Pages ricostruisce il sito e lo pubblica: di norma è
  aggiornato tra le 14:00 e le 14:40 (GitHub a volte ritarda i job
  programmati di qualche minuto: è normale e non dipende da noi).
- Alcuni locali resteranno a 0 eventi: sono i 10 siti già noti come
  bloccati/ cessati (vedi rapporto e `tools/config_scraping.json`), non
  è un problema del sistema.

## Passo 5 · Dominio personale (opzionale)

Il sito funziona già su `https://<nome-progetto>.pages.dev`. Se vuoi il
tuo dominio (es. `www.tuodominio.it`):

1. Il dominio deve avere il DNS su Cloudflare: dashboard → **Add a
   domain** → `tuodominio.it` → piano **Free** → Cloudflare mostra due
   nameserver (es. `anna.ns.cloudflare.com`): impostali nel pannello del
   registrar dove il dominio è registrato. Il dominio resta tuo, cambia
   solo chi gestisce il DNS (attivazione: minuti, al massimo 24h).
2. ⚠️ **Se hai caselle email sul dominio** (es. info@tuodominio.it):
   prima di confermare annota i record **MX** e **A/CNAME del mail
   server** esistenti e ricontrollali dopo l'attivazione — le email
   continuano a funzionare se quei record restano corretti.
3. A dominio attivo: progetto Pages → **Custom domains → Set up a custom
   domain** → `www.tuodominio.it` → conferma. Cloudflare crea record DNS
   e HTTPS da solo; dopo pochi minuti il sito risponde anche sul dominio.
   (Opzionale: ripeti per la versione senza `www`.)

## Passo 6 · Dì ad Aruba "basta"

Quando il sito risponde su pages.dev (e, se lo vuoi, sul tuo dominio):

- Il **VPS Aruba** (80.211.42.166) non serve più: si può cancellare dal
  pannello Aruba e smettere di pagarlo. Lì dentro non c'è nulla di
  indispensabile: progetto e dati vivono su GitHub e Cloudflare, e una
  copia completa resta nello zip sul tuo PC.
- L'**hosting Aruba** non serve più: lascialo scadere o disdici.
- Il **dominio** resta registrato dove è (il rinnovo annuale va pagato
  lì); se un domani vuoi spostare anche la registrazione, Cloudflare
  Registrar lo trasferisce a costo pieno senza ricarichi.
- Le **email** del dominio, se erano sull'hosting Aruba: vanno migrate
  (es. su un servizio email dedicato) prima di disdire l'hosting.

---

## Manutenzione corrente

| Cosa | Dove | Note |
|---|---|---|
| Rapporto dell'estrazione | GitHub → Actions → ultimo run → Summary (e nel repo: `tools/report.md`) | pubblicato anche online in `/tools/report.md` |
| Aggiornamento manuale | GitHub → Actions → Aggiorna eventi → **Run workflow** | estrae e ripubblica subito, a qualsiasi ora |
| Storico e ripristino versioni | Cloudflare → progetto Pages → **Deployments** | ogni giorno una versione; "Manage deployment → Rollback" per tornare indietro |
| Modificare il sito (index.html) | sul PC con GitHub Desktop: Commit → Push (oppure matita di modifica su github.com) | il push ripubblica il sito in ~1 minuto |
| Cambiare l'orario di estrazione | file `.github/workflows/aggiorna-eventi.yml`, riga `H -ge 14` (e commento) | sono i minuti dopo le 14:00 di Roma; il commit della modifica riparte da solo |
| Copia di sicurezza | lo zip v16 sul tuo PC + ogni giorno lo storico su GitHub | il repository È il backup |

## Sicurezza

- Nel repository **non esistono credenziali**: nessun token, nessuna
  password, nessun file di configurazione con dati sensibili (verificato
  a tutto il progetto). Il deploy è gestito dall'integrazione ufficiale
  GitHub↔Cloudflare, che si revoca dal pannello GitHub → Settings →
  Applications in un click.
- `pages-build.sh` pubblica una **lista esplicita** di file: guide,
  estrattore e workflow non finiscono online (e comunque non contengono
  segreti).
- Il token API di Cloudflare della vecchia versione non serve più: in
  questa architettura non c'è nulla da rubare e nulla che scada.

## Se qualcosa non va

| Sintomo | Causa probabile | Rimedio |
|---|---|---|
| Il run di Actions è rosso | quasi sempre il sito di un locale ha cambiato struttura | apri il run, leggi lo step "Estrazione": gli errori di singoli locali sono normali e non bloccano (10 su 24 sono già noti) |
| Run verde ma il sito non cambia | il run era prima delle 14:00 e la guardia ha saltato l'estrazione | normale: usa "Run workflow" per forzarlo subito |
| Aggiornamento arriva alle 14:20 e non alle 14:00 | GitHub ritarda i job programmati | normale, non dipende da noi |
| Build Pages rossa | build command o cartella output scritti male | devono essere esattamente `bash pages-build.sh` e `/out` |
| `pages.dev` ok ma il dominio no | DNS/nameserver non ancora attivi | Passo 5.1: controlla i nameserver al registrar, attendi, poi Custom domains |
| Il workflow non parte più da solo | GitHub disattiva i cron dopo 60 giorni di repository fermo | qui non accade (il bot committa ogni giorno); se succede: Actions → Aggiorna eventi → abilita |
| Sito mostra eventi vecchi | il giorno prima l'estrazione è uscita con guardia attiva | fai un "Run workflow" manuale: si riallinea |
| Estrazione con molti locali a 0 | siti dei locali protetti/cessati | vedi rapporto: comportamento noto, non è un guasto |

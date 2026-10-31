# Progetto Rebecca — Nuovo sito statico

## Obiettivo
Ricostruire https://www.progettorebecca.org/ come sito statico moderno, conservando testi, foto e video storici. Deve restare disponibile come memoria dell'associazione e consentire aggiornamenti manuali saltuari.

## Decisioni approvate dall'utente
- Architettura: **multi-pagina con home landing + archivio eventi**.
- Design: **rosa/crema/prugna moderno**, più leggero dell'originale, con richiami ai colori del logo e concetti dei progetti di riferimento.
- Gallerie Picasa: **eliminate** perché non più recuperabili.
- Video YouTube: **recuperati in modo coerente** (23 unici, 22 disponibili, 1 rimosso).
- Commenti: **eliminati**.
- Hosting: **Netlify / Cloudflare Pages** con deploy automatico da repository Git.
- Dominio: mantenere `progettorebecca.org` e reindirizzarlo al nuovo sito quando pronto.
- L'utente è proprietario di Verbania Notizie e ha il permesso della famiglia.

## Riferimenti
- Sito originale: https://www.progettorebecca.org/
- Progetto riferimento 1: https://github.com/Dario-Fe/piantinedibambu
- Progetto riferimento 2: https://github.com/Dario-Fe/rosariogiordano
- Stack di riferimento: HTML/CSS/JS puri, nessun build, CSS variables, IntersectionObserver, responsive.

## Analisi completata (dati chiave)
- 151 pagine totali: 142 articoli + 8 pagine statiche + 1 pagina gallery.
- Periodo: giugno 2013 → settembre 2018.
- Testo: ~126.500 caratteri (~20.000 parole).
- Immagini: 364 riferimenti inline negli articoli + 7 immagini statiche = 371 WebP locali; 142 cover; 513 miniature.
- Video: 23 ID YouTube unici, 22 disponibili, 1 rimosso (`ivhlINGtbHY`).
- Gallery: 120 foto selezionate, una rappresentativa per articolo, preferendo il formato orizzontale; tutte le immagini restano nei singoli articoli.
- Tag spurii tipo "Aggiungi il tuo": rimossi.
- Gallerie Picasa: 8 album non più raggiungibili.
- Commenti: 3 (omessi).
- Palette originale: rosa `#D04070` / `#D6417A`, cipria `#F0C0D0`; blu nav `#0097D8` era del CMS.
- Logo: `logo.png` 960×327 con foto di Rebecca.
- Sfondo originale: carta da parati rosa con cuori.

## Architettura realizzata
```
/
├── index.html
├── chi-siamo.html
├── progetti.html
├── eventi.html
├── eventi/{id}-{slug}.html      (142 pagine)
├── gallery.html                 (120 foto + 23 video)
├── donare.html
├── contatti.html
├── privacy.html
├── ulmi.html
├── 404.html
├── assets/
│   ├── css/style.css
│   ├── js/main.js
│   ├── js/archive.js
│   ├── img/logo.png, rebecca.png, favicon.svg, pattern.svg
│   ├── img/news/{id}/...
│   ├── img/thumbs/...
│   ├── img/video/...
│   └── data/news.json, videos.json
├── content/articles.json
├── tools/build.py
├── tools/check_links.py
├── tools/templates/...
├── _redirects
├── sitemap.xml
├── robots.txt
├── netlify.toml
└── README-MANUTENZIONE.md
```

## Contatti e dati utili
- Email: info@progettorebecca.org
- Telefono: 347 5024855 (Giovanni Bonaffini)
- Sede legale: Via Indipendenza 16, Oltrefiume di Baveno, presso famiglia Bonaffini.
- Sede operativa: Via San Remigio 20/B, presso Casa Nazareth in Villa Chaminade.
- IBAN: IT47 S 03359 67684 51070 0157904 (Banca Prossima / Intesa Sanpaolo).
- Facebook: https://www.facebook.com/ProgettoRebecca
- Codice fiscale 5×1000: **non disponibile**, non inventare.

## Stato del lavoro
- [x] Analisi sito originale e riferimenti.
- [x] Report e proposta approvati.
- [x] Creazione struttura progetto e download media.
- [x] Estrazione e normalizzazione 142 articoli.
- [x] Design system CSS/JS.
- [x] Home e pagine istituzionali.
- [x] Generazione pagine articolo.
- [x] Gallery curata e sezione video.
- [x] Redirect Netlify, sitemap, robots, privacy.
- [x] Test locale, responsive e QA di base.
- [x] Configurazione repository Git e deploy Netlify: sito `progettorebecca`, deploy automatico da `main`, URL https://progettorebecca.netlify.app/.
- [ ] Inserimento codice fiscale 5×1000, se disponibile.
- [ ] Cutover del dominio `progettorebecca.org`.

## Comandi
```powershell
$env:PYTHONUTF8='1'
python tools/build.py --no-media   # rigenera le pagine senza riscaricare
python tools/check_links.py        # verifica link locali
python -m http.server 8765         # anteprima su http://127.0.0.1:8765/
```

## Note operative
- Cartella di analisi temporanea: `C:\Users\Archivio\AppData\Local\Temp\opencode\rebecca-analysis` (HTML scaricati, CSS, logo, screenshot, inventario CSV).
- Non usare framework né build: solo HTML/CSS/JS.
- Rimuovere sidebar Verbania Notizie, login, PDF, feed, calendario, commenti, banner cookie CMS.
- Conservare i testi storici senza riscritture; pulire solo encoding/impaginazione.
- Usare `youtube-nocookie` con caricamento lazy.
- Le immagini vanno scaricate da `https://www.progettorebecca.org/image.php?id={id}&num={n}`.
- I vecchi URL `/n{id}-{slug}.htm` vanno reindirizzati ai nuovi `/eventi/{id}-{slug}.html`.
- La gallery usa una selezione automatica: non è l'elenco completo delle foto, che resta nei singoli articoli.
- Le cover quasi uniformi vengono sostituite automaticamente con un'immagine dell'articolo o un poster video.
- Il logo usa un ritaglio del ritratto originale (`rebecca.png`), non un'immagine separata.

## QA eseguita
- `python tools/check_links.py`: 152 file HTML, nessun link locale mancante.
- Playwright desktop/mobile: nessun overflow orizzontale, nessun errore console, nessuna immagine locale rotta.
- Lightbox gallery: apertura, immagine caricata, chiusura con Escape.
- Video modal: iframe `youtube-nocookie`, apertura e chiusura.
- Archivio: filtri per anno, ricerca e "Mostra altre storie" funzionanti.
- Menu mobile: apertura/chiusura e `aria-expanded` corretti.
- Screenshot verificati: home, articolo, archivio, gallery, progetti, donare, contatti, chi-siamo.

## Prossimi passi
1. Verificare `_redirects` con i vecchi URL sulla preview Netlify.
2. Recuperare il codice fiscale 5×1000, se l'associazione lo fornisce.
3. Pianificare il cutover DNS di `progettorebecca.org`.

## Come riprendere
1. Leggere questo file.
2. Controllare la sezione "Stato del lavoro".
3. Continuare dal primo punto non completato.
4. Aggiornare questo file a ogni milestone.

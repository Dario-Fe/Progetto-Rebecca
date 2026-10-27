# Manutenzione del sito

Il sito è statico: non c'è un database e non serve un CMS. Le pagine sono file HTML nella cartella principale e in `eventi/`.

## Rigenerare le pagine

I contenuti storici vengono letti dagli HTML originali e rigenerati con:

```powershell
python tools/build.py
```

Per rigenerare solo le pagine senza riscaricare le immagini:

```powershell
python tools/build.py --no-media
```

## Aggiungere una nuova storia

1. Crea una cartella `assets/img/news/NUOVO-ID/` e inserisci le immagini in WebP (versione grande) e una copertina.
2. Crea la miniatura in `assets/img/thumbs/NUOVO-ID-1.webp`.
3. Copia un file esistente in `eventi/` e aggiorna titolo, data, testo e immagini.
4. Aggiungi una scheda in `eventi.html` copiando un blocco `<article class="news-card">` e aggiornando link, data, titolo e testo.
5. Aggiungi la nuova URL in `sitemap.xml`.

## Deploy

Il sito può essere pubblicato su Netlify o Cloudflare Pages collegando il repository Git. Non serve un comando di build: la cartella pubblicata è la radice del progetto. I redirect del vecchio sito sono in `_redirects`.

## Contatti e dati

- Email: info@progettorebecca.org
- Telefono: 347 5024855 (Giovanni Bonaffini)
- IBAN: IT47 S 03359 67684 51070 0157904

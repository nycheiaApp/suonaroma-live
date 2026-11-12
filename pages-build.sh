#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  SuonaRoma Live v19 · prepara la cartella "out/" che Cloudflare
#  Pages pubblica.
#
#  Viene eseguito automaticamente da Cloudflare Pages a ogni build:
#    Build command:        bash pages-build.sh
#    Build output directory: out
#
#  Whitelist esplicita: online vanno SOLO i file del sito. Guide,
#  estrattore, configurazioni e workflow non vengono pubblicati
#  (e comunque nel repository non esistono credenziali).
# ─────────────────────────────────────────────────────────────
set -eu

rm -rf out
mkdir -p out/data out/css out/js out/tools

cp -f index.html locali.html mappa.html aggiungi-locale.html out/
cp -f css/leaflet.css out/css/
cp -f js/leaflet.min.js out/js/
cp -f data/locali.js data/eventi.js data/meta.js out/data/
cp -f data/locali.json data/eventi.json out/data/
cp -f tools/report.md out/tools/

echo "out/ pronto per la pubblicazione:"
find out -type f | sort

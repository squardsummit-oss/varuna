#!/bin/sh
# Nominatim lookups (1 req/s policy). Output: query | lat | lon | osm_type/osm_id | display_name
while IFS= read -r q; do
  [ -z "$q" ] && continue
  enc=$(printf '%s' "$q" | sed 's/ /+/g; s/,/%2C/g; s/'"'"'/%27/g')
  res=$(curl -s -A "VARUNA-SIH2026-research/0.1 (dhrishta2025@gmail.com)" "https://nominatim.openstreetmap.org/search?q=${enc}&format=json&limit=1&viewbox=72.78,19.20,72.95,18.95&bounded=0")
  echo "$q | $(printf '%s' "$res" | python -c 'import sys,json
try:
  r=json.load(sys.stdin)
  if r: print(r[0]["lat"],"|",r[0]["lon"],"|",r[0]["osm_type"]+"/"+str(r[0]["osm_id"]),"|",r[0]["display_name"])
  else: print("NO RESULT")
except Exception as e: print("ERR",e)')"
  sleep 1.2
done

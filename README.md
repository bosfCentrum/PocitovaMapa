# Pocitova mapa Hustopece

Webova aplikace nad OpenStreetMap (Leaflet) s podporou vice vrstev dat.

Typy pinu:
- `Dobre` (zeleny)
- `Spatne` (cerveny)
- `Tady to chce zmenu` (zluty)

## Co aplikace umi
- prepinat jednu nebo vice vrstev mapy najednou
- vrstva `Pocitova mapa` (interaktivni piny uzivatelu)
- vrstva `Mestske budovy` (staticka data majetku mesta)
- prihlaseni bez hesla (`email + jmeno`)
- role: `admin`, `moderator`, `user`
- neprihlaseny uzivatel muze mapu jen prohlizet
- prihlaseny uzivatel muze pridat pin
- komentar lze upravit jen s opravnenim (autor pinu nebo admin)
- smazani pinu: autor sveho pinu nebo admin (admin muze smazat libovolny pin)
- filtrovat kategorie pres checkboxy
- zobrazit pocty u kategorii
- sdilet data mezi zarizenimi pres backend API

## Lokalni spusteni

```powershell
py server.py
```

Aplikace pobezi na:
- `http://localhost:8080`
- v lokalni siti i na `http://<IP_PC>:8080`

## Deploy (Render)

Repo je pripraveny pro Render pres `render.yaml`.

`render.yaml` aktualne pocita s free planem:
- bez persistentniho disku
- bez `DB_PATH` override
- start command: `python server.py`

Dulezite:
- na free planu je filesystem docasny
- SQLite data se po restartu/redeployi mohou ztratit

## Datovy model

Data jsou v SQLite rozdelena obecne:
- `layers` (definice vrstev)
- `layer_points` (body jednotlivych vrstev)

API pro piny (`/api/pins`) zustava kvuli kompatibilite, ale uklada body do `layer_points` pod vrstvou `feelings`.

## Seed testovacich dat

Soubor `seed.json` obsahuje:
- definice vrstev (`layers`)
- testovaci piny (`pins`, vrstva `feelings`)
- vzorove body `city_buildings` (`points.city_buildings`)

Pri startu serveru:
- kdyz je DB prazdna a `SEED_IF_EMPTY=1` (default), seed se naimportuje
- kdyz DB data obsahuje, seed se znovu nespousti

To je vhodne pro testovani po deployi na Render free.

## Konfigurace (ENV)
- `HOST` (default `0.0.0.0`)
- `PORT` (default `8080`)
- `DB_PATH` (default `./pins.db`)
- `SEED_IF_EMPTY` (default `1`)
- `SEED_FILE` (default `seed.json`)

Poznamka k rolim:
- prvni uzivatel, ktery se kdy prihlasi do prazdne DB, dostane roli `admin`

## API
- `GET /healthz`
- `GET /api/auth/me`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/layers`
- `GET /api/layers/{layerKey}/points`
- `POST /api/layers/{layerKey}/points` (jen vrstvy s `allow_user_points=true`)
- `GET /api/pins`
- `POST /api/pins`
- `PUT /api/pins/{id}`
- `DELETE /api/pins/{id}` (admin)
- `DELETE /api/pins`

# Pocitova mapa Hustopece

Jednoducha webova aplikace nad OpenStreetMap (Leaflet), kde muzes:
- pridat pin podle toho, jak se na miste citis (`Dobre` / `Spatne`),
- ulozit komentar ke kazdemu pinu,
- sdilet data mezi zarizenimi pres backend.

Data se ukladaji do SQLite databaze `pins.db` na serveru.

## Spusteni
1. Otevri terminal ve slozce projektu.
2. Spust backend i web:

```powershell
py server.py
```

3. Otevri v prohlizeci:
- na PC: `http://localhost:8080`
- na telefonu ve stejne Wi-Fi: `http://<IP_PC>:8080`

Priklad: `http://192.168.88.177:8080`

## Konfigurace (env)
- `HOST` (default `0.0.0.0`)
- `PORT` (default `8080`)
- `DB_PATH` (default `./pins.db`)

Test healthcheck:

```text
GET /healthz
```

## Deploy se SQLite (1 instance)
Pro online provoz se SQLite je dulezite mit persistentni disk/volume.

### Varianta A: Docker
Build image:

```bash
docker build -t pocitova-mapa .
```

Run s persistentnim volume:

```bash
docker run -p 8080:8080 -e PORT=8080 -e DB_PATH=/data/pins.db -v pocitova_data:/data pocitova-mapa
```

### Varianta B: Render / Railway / Fly
1. Nasad kod z repozitare.
2. Spusteci prikaz: `python server.py`.
3. Nastav env:
   - `PORT` podle platformy (nektere ji nastavi samy),
   - `DB_PATH` na cestu v persistentnim disku (napr. `/data/pins.db`).
4. Pripoj persistentni disk/volume.
5. Healthcheck endpoint: `/healthz`.

Bez persistentniho disku by se data po redeployi nebo restartu ztratila.

### Render (doporuceny postup)
Projekt obsahuje `render.yaml`, ktery vytvori:
- Web Service (`python server.py`)
- Persistent Disk (`/var/data`)
- `DB_PATH=/var/data/pins.db`

Postup:
1. Nahraj projekt na GitHub.
2. V Renderu zvol `New` -> `Blueprint` a pripoj repozitar.
3. Render nacte `render.yaml` a vytvori sluzbu i disk.
4. Po deployi over:
   - `https://<tvoje-sluzba>.onrender.com/healthz`
   - `https://<tvoje-sluzba>.onrender.com`

Poznamka:
- Na `free` planu muze sluzba usinat pri neaktivite.
- SQLite je zde vhodna pro 1 instanci; horizontalni scaling by chtel Postgres.

## Pozdejsi migrace na Postgres
Aktualni API vrstva uz oddeluje frontend od uloziste, takze migrace je realisticka i pozdeji:
1. pridat datovou vrstvu (repository) do `server.py`,
2. implementovat SQLite + Postgres variantu stejneho rozhrani,
3. zvolit backend podle env prepinace.

Frontend (`app.js`) se pri teto migraci menit nemusi.

## API endpointy
- `GET /api/pins` - seznam pinu
- `POST /api/pins` - vytvoreni pinu
- `PUT /api/pins/{id}` - uprava komentare
- `DELETE /api/pins` - smazani vsech pinu

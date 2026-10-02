# The table in AR / VR

`/xr` puts every seat's 3D avatar around the table: the same Meshy models, animation clips and
game-state acting as the stage (`/stage`), each holding their hand of cards, with name and life
floating above them and a ring under whoever's turn it is. The phase and **NEXT** ride along as an
overlay, so a turn can be stepped through without leaving AR.

| Device | How |
|---|---|
| **Quest 3 / 3S, Android Chrome** | WebXR **AR**: open `https://<this Mac>:8443/xr`, tap *Start AR*, point at the table, tap to place it (tap again to move it). Avatars stand about 25 cm tall around your real table. |
| **Quest (VR), PC VR** | WebXR **VR**: *Enter VR* — the table life-size in front of you. |
| **iPhone / iPad / Vision Pro** | Safari has no WebXR AR, so `/xr` shows each avatar as an **AR Quick Look** link (USDZ, idling): tap one to stand it in your room. |
| **Desktop** | A slowly orbiting preview of the table. |

**Start it:** `TLS=1 … table/play.sh …` adds an HTTPS listener on `:8443` — WebXR only runs on secure
pages, and phones reach the Mac over the LAN. The certificate is self-signed and made once per LAN
address (`table/.cache/tls/`, key 0600); each device accepts it once. Nothing leaves the LAN.

**Models:** `table/meshy_avatars.py` — `--v2` (concept → Meshy 7, 4K), `--more` (extra clips),
`--usdz` (Quick Look files). They live in the gitignored `table/.cache/avatars/` and are chosen by the
seat's commander, so a renamed seat keeps its avatar.

**Not yet:** shared anchors across headsets (each device places its own table); hand tracking to press
NEXT with a pinch; the AI's speech as spatial audio from its avatar.

## The whole board in 3D

Every card in play lies on the table in front of its owner's avatar, drawn by `table/assets/board3d.js`.
Rows run from the centre out: creatures, then other permanents, then lands (identical lands stack with a ×N
badge). Tapped cards turn sideways. Badges mark counters, tokens and suspended cards. A commander still
in the command zone sits to its owner's right and the graveyard to the left, top card face up. Hover a
card (desktop or phone) for its name, cost, type and rules text; **T** looks straight down (`?view=top`).

AI boards come from the engine; face-down cards arrive with no name or text. Human boards are physical
cards, so the brain records them from photos and what people announce
(`tablectl public-board Sam "Swamp" "Mountain:tapped" "Centaur:token:3/3" "Nihilith:suspended:5" --graveyard "Hunted Horror" --commander-out`).
They're public information only and survive a restore. Data: `GET /api/board3d` (phones and remote
visitors can read it); recording a human board is `POST /api/public-board` with the brain token.

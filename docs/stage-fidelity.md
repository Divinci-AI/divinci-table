# Stage fidelity — what to do next, ranked (research 2026-10-02)

The stage (`table/stage.html`) renders Meshy-generated, rigged GLBs in three.js r185. Ranked by
payoff per hour, cheapest first. Nothing here needs Blender.

| # | Change | Why | Cost |
|---|---|---|---|
| 1 | **Real HDRI instead of `RoomEnvironment`** — one small studio `.hdr` (CC0, e.g. Poly Haven), vendored, PMREM-prefiltered, `scene.environment` only (keep our gradient background). | PBR materials are lit mostly by the environment; an HDRI gives believable reflections on armour, monocle, buckles. Faster than extra lights. | ~1 MB file, minutes |
| 2 | **Exposure + three-point light per commander** — `toneMappingExposure ≈ 0.8` under ACES; key + coloured rim (the commander's accent) + soft fill; shadow-casting only on the key. | ACES at exposure 1.0 blows out pale skin and white robes; a coloured rim separates the character from the dark background. | an hour |
| 3 | **Contact shadow under the feet** (`ContactShadows`-style render of a depth pass, or a baked blob). | Grounds the character; the current shadow is faint and the avatar floats. | an hour |
| 4 | **Post-processing:** `EffectComposer` → N8AO / GTAO (half resolution) → selective bloom on emissive bits (crown, visor, eyes) → SMAA → OutputPass. Hardware MSAA does not mix with screen-space AO; use SMAA. | Depth in folds of coats and hair; glow sells the "magic". | an evening; watch frame rate on the laptop |
| 5 | **Cards in the hand:** parent the fan to the `RightHand` bone (Meshy's rig names it) via a socket `Object3D`, offset per avatar, and play a looping "holding" pose on the arm only (or IK the arm toward the fan). | Bone-parented items follow every animation; today the fan floats beside the body. | an evening |
| 6 | **Better Meshy source:** Meshy 7 **image-to-3D** from an approved concept image (generate the concept first with Meshy's text-to-image), `texture_resolution: "4k"`, PBR on. Text-to-3D can't use Meshy 7. | Most visible artefacts (flat faces, smeared hands) come from the source mesh, not the renderer. | ~35 credits/avatar + rig/animate |
| 7 | **Skin and hair:** set `MeshPhysicalMaterial` sheen on cloth, clearcoat on metal/visors after load; alpha-to-coverage on hair cards if Meshy emits any. three.js has no glTF subsurface support, so fake skin softness with a warm rim rather than SSS. | Cheap material wins on known parts. | an hour |
| 8 | **Blender only for a specific defect** (fused fingers, a bad weight paint, adding a hand socket bone), scripted headless with `bpy` so it stays reproducible. | Hand fixes don't scale; do them only when a model is otherwise right. | per model |

Performance guardrails: one avatar on screen at a time; cap `devicePixelRatio` at 2 (done); AO at
half resolution; preload the next seat's GLB in the background; compress textures to KTX2 if the
5–6 MB GLBs ever feel slow to swap.

Sources:
- [Three.js Journey — Realistic render](https://threejs-journey.com/lessons/realistic-render)
- [sbcode — Environment maps](https://sbcode.net/threejs/environment-maps/)
- [three.js docs — MeshStandardMaterial](https://threejs.org/docs/pages/MeshStandardMaterial.html)
- [Realistic rendering experiments (PBR, HDR, shadows)](https://github.com/davidllona/Threejs-realistic-rendering)
- [N8AO-style SSAO for three.js](https://github.com/study-game-engines/three.js-ssao)
- [SMAA vs MSAA with AO — pmndrs/postprocessing](https://github.com/pmndrs/postprocessing/discussions/557)
- [Subsurface scattering with glTF — three.js forum](https://discourse.threejs.org/t/subsurface-scattering-with-gltf-model/23629)
- [Putting a weapon in a character's hand — three.js forum](https://discourse.threejs.org/t/how-to-put-a-weapon-in-a-characters-hand/22121)
- [Meshy rigging & animation API](https://docs.meshy.ai/en/api/rigging-and-animation)

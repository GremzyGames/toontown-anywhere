# DRAFT ONLY — DO NOT MERGE / NO PULL REQUEST

This is a local draft snapshot of the browser client work. It has **not** been
opened as a pull request. Resources/packs are built locally and are not part of
this repository.

## Client / code pack

- Restored the shared localizer fix for `QuestScriptTutorialMickey_4` and removed
  the stale compiled `TTLocalizerEnglish` bytecode that was masking it. This
  fixes the tutorial movie aborting and the toon staying frozen.
- Kept browser input recovery on tab/window return, reconnect, and trusted game
  pointer presses.
- Added quiet-log FPS samples (one every 30 s, with scene context), one-shot
  task-cost profiling, and the active texture-mode line so ordinary devices can
  be diagnosed without `?log=1`.
- Cut-out shader: smooth fractional alpha, with an A/B `?cut=hard`. Street signs
  now skip depth write so their face cannot be depth-rejected by its backing
  when transparent-bin sort order flips with camera distance (`?signdw=1`
  restores the old behavior).
- Web Settings gained an Original / Remastered texture picker.

## Resources / packs (built locally, not committed here)

- Remastered textures were rebuilt with Upscayl Standard and capped at 512 px on
  the long side; SGI alpha masks kept the verified RLE pipeline.
- Fixed 33 Upscayl outputs that came out black/flat on tiny sources, including
  the sky textures.
- Restored the upstream Toontown Anywhere repainted art for Disney-character
  playgrounds, street signs, statues and matching DNA/audio (330 files) under
  the original filenames, so no code or DNA links break. Other Remastered art
  was left in place.
- Original packs ~357 MB, Remastered ~437 MB; pack version bumped and deployed
  with script key `tta-ui.js?v=1795000056`.

## Tab-return connection recovery (draft v59)

- Hiding the PC tab closed its WebSocket; the server then booted the session with code 345 ("timed out while waiting for heartbeat"), leaving the game in a lost-connection state where keyboard movement was ignored.
- Returning to the tab or window now forces a clean reload when `webnet.state` is `closed`, instead of leaving a dead session. The event is logged as `JS| auto reload after connection loss (...)`.
- `webui.py` documents that boot code 345 is transient (not a deliberate logout) and remains retryable. New client bundle: `game_ot_reconnect.apk`; script key `tta-ui.js?v=1795000059`.
- Verified the earlier fixes are still present after the upstream merge: tutorial localizer + removed stale `.pyc`, quiet FPS sampling, sign depth-write handling, focus/key recovery, and restored TTA pack art.

## Reworked couch/rug textures (draft v60)

- Replaced seven Remastered textures with the user's reworked 512px versions (`couch.jpg`, `couch2.jpg`, `couch3.jpg`, `carpet.jpg`, `rug_string.jpg`, `phase_3.5_palette_3cmla_1.jpg`, `toon_interior_palette_3cmla_1.jpg`), written back as JPEG q93 4:4:4.
- Kept the existing `rug_string_a.rgb` mask and created verified opaque SGI RLE `_a.rgb` masks for the other six targets that had none. Original packs untouched.
- Remastered packs rebuilt at 449.6 MB; `HD_PACK_V=10`, script key `tta-ui.js?v=1795000060`.

## Known items

- PC/iPad/PC rendering and FPS still need a final cross-device pass; the latest
  logs show no texture-load or shader errors.
- No pull request was opened.

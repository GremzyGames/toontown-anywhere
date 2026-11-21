# DRAFT — Dynamic Gag Track Progression (Codex Luna 6)

Complete game-wide implementation of player-driven gag track selection and progression.

## Data model
- New persisted field `setChosenTracks(uint8[] = [])` on `DistributedToon` (client + server dclass). Ordered list of chosen track IDs: the first two are starting tracks, later entries are unlock choices. This is the authoritative record of what the player picked; `setTrackAccess` is derived from it.
- Backward compatible: existing characters with an empty chosen list are seeded from their saved `trackAccess` on first login, so all existing tracks/experience are preserved and they never see the starting picker.

## Server (authority)
- `DistributedToonAI`: new `setChosenTracks`/`b_setChosenTracks`/`d_setChosenTracks`/`getChosenTracks`; new RPCs `chooseStartingTracks(uint8[])` and `chooseUnlockedTrack(uint8)`; `addTrackAccess`/`removeTrackAccess` now maintain the chosen list. Validation rejects duplicate/out-of-range starting picks.
- `fixTrackAccess()` rewritten to derive access from the chosen list only — the old tier-based forced arrays (which reset custom picks on every login) are gone.
- `QuestRewardCounter`: empty baseline and no longer overwrites track access for characters with a chosen list.
- `TutorialManagerAI`: starting inventory now uses the chosen two tracks instead of hardcoded Throw + Squirt.
- `chooseTrackChoiceQuest()` now selects generically from the tier's reward-400 pool, valid for any unlock order.
- `QuestManagerAI.completeQuest`: track-choice quests now offer all still-locked tracks, not a fixed pair.

## Client
- New reusable `TrackSelectionGui` (choose K of N, reuses existing gag artwork, Confirm/Cancel, timer). Used for the tutorial (2 of 7) and future unlocks (1 of remaining).
- `QuestParser`: new `SHOW_TRACK_SELECTION`, `CHAT_TRACK_REACTION`, `STARTING_GAG_MAP` commands; starting-gag inventory display remapped to the chosen tracks.
- `TTLocalizerEnglish`: new prompt/title/button strings and `getTrackChoiceReaction()`, which substitutes the chosen track names or delivers the special Lure+Trap joke.
- NPC track-choice movie now offers all locked tracks through the new GUI.

## Tutorial Tom dialogue
- After "There are lots of gags, but take these to start", Tom asks "What gag do you want to start with?" (NEXT button), the picker opens, then Tom reacts dynamically:
  - Normal: "Ahh, so you picked <A> and <B>! That's a good starting gag combination!"
  - Lure + Trap (either order): "Lure AND Trap?! Geez! You must really dislike yourself to start with those two! Hahaha!"
- The existing Toontorial continues unchanged after the reaction.

## Experience
- No change needed: `BattleExperienceAI.assignRewards` already awards XP strictly by the track of the gag used, independent of unlock order.

## Packs / deploy
- `HD_PACK_V=20`, script `tta-ui.js?v=1795000083`. Both pack sets rebuilt; `_code` size 42,486,210 bytes.

## Verification performed
- All changed client modules pass `py_compile`; server modules pass `py_compile` inside the container.
- `_code.zip` structure and regenerated Python 3.12 bytecode verified to contain the new GUI/parser symbols.
- Server restarted cleanly with zero tracebacks in astron/AI logs.
- Selection script present in both Original and Remastered phase_3 packs; HTTP 200 for page, JS and manifests.

## Fix: "Stuck on Starting Toontown"
The first deploy hung at login: the server's `toon.dc` had `chooseUnlockedTrack` but the client's did not, and the client also had a duplicated `setChosenTracks` line. Astron requires byte-identical DC files on client and server, so the mismatch broke the protocol. Fixed by copying the authoritative server `toon.dc` into `_code/etc/toon.dc` verbatim; verified byte-identical. Manifests updated (`_code` 42,482,907 bytes), `HD_PACK_V=21`, script `tta-ui.js?v=1795000084`. Devices must hard-reload to drop the cached broken pack.

## Fix 2: "Stuck on Starting Toontown" (second cause)
After the DC sync, login still hung. Two more problems were found and fixed:
- A circular import: I had added `from toontown.toonbase import ToontownBattleGlobals` at the top of `TTLocalizerEnglish.py`, but `ToontownBattleGlobals` itself imports `TTLocalizer`. Removed it; the reaction helper now imports lazily inside the function.
- Stale bytecode: a clean rebuild of all `.pyc` from `.py` was needed so the new `setChosenTracks` methods in `DistributedToon` were actually compiled. Verified the 3.12 bytecode contains the methods and the DC files remain byte-identical.
- `_code` final size 41,235,688 bytes; manifests updated. `HD_PACK_V=21`, `tta-ui.js?v=1795000084`.

## Fix 3: invisible posters + no Cancel in tutorial
In-game the picker showed only the title/timer/buttons — the 7 track posters were invisible. Rewrote `TrackSelectionGui` so each track is a labelled DirectButton using the stock `quit_button` model (the same proven pattern as `TrackChoiceGui`), with the gag icon attached only if available and a check mark when selected. In `start` mode there is now no Cancel button and no timer; on timeout it auto-picks the first two tracks so the player can never be stuck with no selection. `unlock` mode keeps cancel/timer. `_code` size 41,235,938 bytes, `HD_PACK_V=22`, `tta-ui.js?v=1795000085`.

## Not yet verified (needs in-game device testing)
- Full Toontorial flow and dialogue sequencing on a new character.
- Lure+Trap joke and all dynamic reactions in-game.
- Future unlock quests across arbitrary orders and neighborhoods.
- Multiplayer synchronization of the new field.

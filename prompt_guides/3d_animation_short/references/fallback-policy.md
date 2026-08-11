# H3 Prompt Correction Policy

## Reference-anchor drift

If a shot drifts from the defined `Reference Anchors`—for example a door frame moves to the wrong side, a character exits from the wrong edge, or the lighting direction flips—strengthen the prompt by quoting the exact `Reference Anchors` block from the shot table. Do not accept a prompt that silently mixes corrected and uncorrected spatial states.

If the shot remains unstable, shorten it to six seconds or less, split the dropped duration into an adjacent shot, and re-run the shot-table self-check so the transition and spatial handoff remain explicit.

## Storyboard correction

If a storyboard layout collapses, labels become illegible, panels merge, or character identity drifts, tighten the prompt by explicitly restating the four-quadrant layout, the `[char:…] [scene:…] [shot:…]` labels, and the per-panel content rules. The text storyboard remains authoritative; a visual storyboard must never override its identity, continuity, or timing instructions.

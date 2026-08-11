# H3 Single-Shot Prompt Preparation

## H3 characteristics

H3 is strong on visual packaging, motion graphics, text and UI clarity, multimodal context understanding, stylized design language, and dialogue-driven beats with native dual-channel audio.

## Prompt binding rules

For every shot, use exactly the matching section from the text storyboard together with the exact character references and exact scene reference named by that shot.

- Preserve the global visual style lock.
- Preserve exact character identity, costume, proportions, colors, and signature props.
- Preserve the scene's fixed continuity landmarks and screen direction.
- Keep per-second actions, camera behavior, dialogue, Foley, sound effects, and music intent explicit.
- Strip all storyboard-only labels (`[char:…]`, `[scene:…]`, `[shot:…]`, `[dur:…]`, `[hook:…]`) from the final video prompt so they cannot appear in-frame.
- The final video must contain only clean full-color animation content and no storyboard borders, arrows, notes, timing marks, or pose ghosts.
- Maintain the aspect ratio, duration, and resolution supplied by the node.

## H3 prompt shaping

For the default stylized 3D direction, emphasize: `Pixar-inspired 3D cartoon rendering, C4D + Octane look, stylized Q-version proportions, warm SSS skin, designed-with-detail hair, strong character design language, clean motion, on-brand color palette`.

H3 follows detailed timing well, so the shot table's per-second directives may be carried into the final prompt with their identity, spatial, performance, and audio anchors intact.

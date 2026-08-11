---
name: papercraft-stop-motion-explainer-prompt-guide
description: For creators explaining science, education, or general knowledge through tactile handmade papercraft visuals. The Prompt Guide extracts the learning goal and visual metaphor, proposes creative directions, designs paper characters, layered diorama sets, props, preview concepts, storyboards, camera movement, transitions, sound, and review constraints for a production-ready H3 prompt. Best for cut-paper, pop-up-book, layered diorama, and miniature stop-motion explainers; not for standard 2D cartoons, line doodles, live action, or explainers without a paper-art look.
---

# Papercraft Stop-Motion Explainer

Create a complete papercraft stop-motion explainer package from a science, education, or knowledge topic. The output is a production-ready creative plan: style rules, character and set design, asset planning, prompts, storyboard structures, motion language, sound direction, negative prompts, and review criteria.

Use this Prompt Guide when the user wants a tactile handmade explainer style: layered paper, cardboard cutouts, miniature diorama, stop-motion puppet movement, pop-up-book staging, paper props, and physical shadows. The default assumption is that the user wants a complete explainer video package. Prompts, storyboards, asset plans, and motion notes are reviewable production assets inside that complete process, not isolated deliverables unless the user says so.

When this Skill proceeds from planning/prompting into actual video generation, use MiniMax-H3 as the default video generation model. Treat MiniMax-H3 as the default unless the user explicitly selects another available model or MiniMax-H3 is unavailable.

## Visual Depth and Papercraft Motion Priority

Every visual plan, preview prompt, video prompt, and review pass must prioritize both layered image depth and unmistakable papercraft stop-motion qualities.

Required visual-depth emphasis:

- Build scenes with clear foreground, midground, background, and far-background planes.
- Use foreground occluders such as paper leaves, clouds, frames, curtains, props, or cutout silhouettes to create depth.
- Keep the main knowledge object or focal action in a readable middle plane.
- Add background parallax, small environmental motions, and layer separation instead of flat static backdrops.
- Use circular, diagonal, tunnel, pop-up, or cross-section compositions when they help the viewer read depth.

Required papercraft stop-motion emphasis:

- Make all visible objects feel physically made from paper: layered cardboard, cut edges, paper fibers, folds, seams, tabs, brads, joints, pull-tabs, sliders, rotating discs, and visible thickness.
- Motion should feel frame-by-frame and hand-manipulated: small stepped movements, tiny pauses, slight rebounds, hinged gestures, sliding paper mechanisms, page flips, pull-tab reveals, and paper pieces settling.
- Background elements should also be paper mechanisms when possible: paper clouds sliding on rails, paper moons/discs moving on tracks, layered scenery shifting in parallax, paper leaves falling, paper lamps swaying, and cardboard doors opening.
- Avoid smooth CG motion, plastic 3D surfaces, flat vector backdrops, overly static backgrounds, and large character motion that breaks the miniature stop-motion feel.

## Lightweight Request Bypass

If the user explicitly asks for only one production asset, do not force the full 18-step package workflow. Route directly to the relevant step while preserving the papercraft style rules.

Use these shortcuts:

- Single-image prompt only: run STEP 1, STEP 2, STEP 9, and STEP 17.
- Image-series prompts only: run STEP 1, STEP 2, STEP 10, and STEP 17.
- 5-second image-to-video prompt only: run STEP 1, STEP 2, STEP 11, and STEP 17.
- Storyboard only: run STEP 1, STEP 2, STEP 12, STEP 13, STEP 14, STEP 15, and STEP 16.
- Creative directions only: run STEP 1, STEP 2, and STEP 3.

## STEP 1: Understand the Input

Analyze the user's topic and production goal. Preserve the user's domain words and do not simplify the science into a different topic.

Output a concise understanding block:

- Topic or knowledge point
- Target audience: children, general audience, classroom, social media, brand education, or specialist audience
- Intended duration: 15s, 30s, 60s, or unspecified
- Platform or format when stated
- Required output type: single image, image series, 5-second image-to-video prompt, storyboard, or full package
- Core learning outcome: the one sentence the viewer should remember
- Visual metaphor: how the concept becomes a paper object, model, puppet, layer, path, or mechanism

If crucial information is missing, choose reasonable defaults instead of stopping: general audience, 30-second short, horizontal 16:9 video by default unless the user specifies another ratio, and one paper narrator plus one core paper model.

## STEP 2: Summarize the Style DNA

Before designing the video, state the style rules that must stay consistent.

Include these traits:

- Handmade papercraft stop-motion look
- Miniature paper diorama or shadow-box stage
- Layered cardboard cutouts with visible thickness
- Matte tactile paper textures, fibers, folds, seams, torn or cut edges
- Paper puppet characters built from separate overlapping parts
- Real physical drop shadows between layers
- Multi-plane foreground, midground, background, and far background
- Macro miniature photography, slight depth of field, and 2.5D parallax
- Educational labels, arrows, cards, and simple symbols made from paper
- Clear foreground / midground / background / far-background depth, with foreground occlusion and readable parallax
- Background layers with paper-mechanism motion, not static painted scenery
- Stop-motion motion language: stepped movement, tiny pauses, slight rebounds, hinged joints, pull-tabs, sliders, rotating discs, paper pieces settling

Explain the reason: the paper medium turns abstract science into touchable objects and makes layered explanation easy to understand. The scene must feel physically built and animated frame by frame, not merely illustrated in a paper-like texture.

## STEP 3: Propose 3-5 Creative Directions

Generate 3 to 5 distinct concepts. Each direction must explain the same topic through a different visual metaphor or narrative structure.

For each direction, provide:

- Title
- Core idea
- Explainer angle
- Paper visual metaphor
- Best duration
- Best audience or platform
- Signature visual moment
- Risk or limitation

Choose the creative direction that best fits the prompt and use the duration supplied by the node before defining detailed assets.

Recommended direction archetypes:

1. Pop-up-book journey: each page reveals one layer of the concept.
2. Paper scientist laboratory: a paper host demonstrates the mechanism on a table.
3. Layered cross-section model: a paper object opens like a sectional diagram.
4. Miniature nature theater: ecological, geographic, or astronomical topics unfold in a paper landscape.
5. Paper mechanism board: gears, arrows, sliders, labels, and moving parts explain cause and effect.

## STEP 4: Design Paper Characters

Design characters only when they help communication. A character may be a host, assistant, animal guide, personified molecule, immune cell, planet, machine part, or natural force.

For each character, output:

- Name and role in the explanation
- Shape language and proportions
- Paper construction: cutout face, layered hair or body, cardboard limbs, joint nodes, tabs, brads, or folded parts
- Expression system: simple paper eyes, eyebrows, mouth shapes, replaceable emotion cards
- Movement style: stop-motion nudges, hinged arm gestures, pointing, bouncing, sliding, or flip-card expressions
- Reusable identity details for image series consistency

Keep characters simple enough to remain readable in short videos.

## STEP 5: Design Paper Scenes

Treat every scene as a physical paper stage, not a flat background.

For each scene, output:

- Scene name
- Scientific purpose
- Foreground elements
- Midground subject and action
- Background environment
- Far-background board or sky
- Movable paper mechanisms
- Educational information carriers: labels, arrows, charts, measurement tags, captions, or cards

Use at least four depth planes whenever possible. Keep the main knowledge model separated from the background through spacing and shadow.

## STEP 6: Plan Layered Diorama Staging

Create a multi-plane staging table.

Required columns:

- Layer number
- Plane role
- Paper elements
- Material and edge treatment
- Motion or parallax behavior
- Shadow interaction
- Knowledge function

Rules:

- Use 4-7 layers for most shots.
- Place reading-heavy labels on stable layers.
- Let foreground elements partially occlude the stage for miniature realism.
- Keep the core concept in the midground, where attention is strongest.
- Use layer separation to explain hierarchy, sequence, anatomy, causality, or scale.

## STEP 7: Plan Prop and Asset Library

Create an asset list grouped by function.

Asset groups:

- Host and character assets
- Core scientific object assets
- Explanation props: arrows, labels, cards, charts, magnifiers, rulers, meters
- Scene props: lab bench, hills, books, shelves, clouds, stars, trees, water, tubes, gears
- Motion props: paper strips, sliders, rotating discs, pop-up tabs, pull-out layers, paper confetti
- Sound cue props when useful: page flip, scissors, paper rustle, click, pop, tape peel

For each asset, define:

- Name
- Purpose
- Paper material
- Layer placement
- Static or movable
- Appearance timing
- Consistency notes

## STEP 8: Plan 1-3 Visual Preview Images

Before writing final prompts or storyboards, plan 1 to 3 visual preview images based on the chosen creative direction, character design, scene design, and layered staging. These previews are for style and concept definition, not final production frames.

Default preview ratio follows the project video ratio: horizontal 16:9 unless the user specifies another ratio. If the user requested vertical, square, or another format, use that ratio consistently for previews and later video planning.

For each preview, define:

- Preview number and purpose
- Which approved direction, character, or scene it visualizes
- Key visual focus: overall world, character look, mechanism clarity, cultural atmosphere, or ending memory image
- What the user should judge before continuing

Recommended preview set:

1. Overall world preview: shows the full papercraft diorama, main character, core object, and atmosphere.
2. Mechanism preview: shows the science model, paper arrows, labels, and movable explanatory props.
3. Emotional or ending preview: shows the cultural connection, summary card, or final memory image.

After presenting previews, pause with a confirmation card. Offer choices such as: continue to prompt writing, revise preview 1, revise preview 2, revise preview 3, reduce to one visual direction, or switch creative direction. Do not write final single-image, image-series, or 5-second image-to-video prompts until the user confirms the visual preview direction.

## STEP 9: Write Single-Image Prompt

Create a prompt for one concept image or key visual.

The prompt must include:

- User's topic and core object
- Papercraft stop-motion style
- Miniature diorama stage
- Layered cardboard cutouts
- Visible paper fibers, folds, seams, cut edges, and thickness
- Real physical shadows between layers
- Macro miniature photography and slight depth of field
- Educational paper labels, arrows, or cards when needed

Avoid overloading the image with every knowledge detail. One image should communicate one main idea.

## STEP 10: Write Image-Series Prompts

Create a sequence of prompts when the user needs multiple keyframes or a storyboard image set.

For each image, provide:

- Image number and purpose
- What changes from the previous image
- Shared style anchors
- Character and scene consistency anchors
- Prompt

Series rules:

- Keep the same paper material language, light direction, depth planes, and character identity.
- Each image should explain one step of the knowledge point.
- Preserve domain-specific words from the user's topic in every prompt.
- Use the same aspect ratio across the series unless the user requests variations.

## STEP 11: Write 5-Second Image-to-Video Prompt

Create a short prompt that animates a single reference image while preserving the paper style.

Include:

- Preserve paper texture, cut edges, layered set, and physical shadows
- Slow push-in, gentle pan, or parallax move
- Small stop-motion-like puppet gestures
- Paper arrows, labels, sliders, or mechanisms moving slightly
- Foreground and background parallax
- Avoid smooth CG transformation, melting, plastic surfaces, or high-speed camera moves

Keep motion limited and physically plausible for paper objects.

## STEP 12: Create Storyboard for the Chosen Duration

Use the duration chosen after the creative directions phase. Do not present three full storyboard versions by default. First give a brief content overview, then provide a concise storyboard table for the chosen duration only.

The content overview should be short and clear:

- One-sentence video premise
- Target duration and ratio
- Main visual structure
- Knowledge path: hook → explanation → example or cultural connection → memory sentence

Storyboard table rules:

- Keep text compact. Avoid long paragraphs inside table cells.
- Use only the shots needed for the chosen duration.
- 15s: usually 4 shots.
- 30s: usually 5-6 shots.
- 60s: usually 7-9 shots.
- Each shot explains one knowledge beat.

Required columns:

- Time
- Knowledge beat
- Visual action
- Paper movement
- Camera / transition
- Sound cue

After the table, continue to editing rhythm and camera rules only when the storyboard, duration, and visual-preview logic are internally consistent.

## STEP 13: Define Editing Rhythm

Set the rhythm according to duration and educational clarity.

Guidelines:

- 15s: 4-6 shots, fast but readable, 2-4 seconds per shot.
- 30s: 6-8 shots, 3-5 seconds per shot.
- 60s: 8-12 shots, 4-7 seconds per shot.
- Pause briefly when a label card appears.
- Do not cut before the viewer understands the paper mechanism.
- Use rhythmic paper movements instead of aggressive digital edits.

## STEP 14: Define Camera Rules

Use camera movement that feels like filming a miniature paper stage.

Recommended moves:

- Slow push-in to enter the paper world
- Lateral pan with multi-plane parallax
- Static medium shot for host explanation
- Macro close-up for paper texture and key props
- Slight top-down angle for cross-section diagrams
- Layer pass-through when moving into internal structures

Avoid:

- High-speed flying camera
- Full 360-degree orbit unless the set is explicitly built for it
- Digital glitch motion
- Liquid morphing
- Hyper-real CG camera behavior

## STEP 15: Define Transitions

Use transitions that obey paper physics.

Recommended transitions:

- Page flip
- Pop-up-book unfold
- Pull-tab slide
- Paper label wipe
- Paper cloud pass
- Circular paper mask
- Cut-paper door opening
- Cross-section layer split
- Paper confetti burst
- Tape or sticker reveal

Avoid electronic scanlines, neon glitches, glass shatter, metallic wipes, and sci-fi particle transitions unless the user explicitly requests a contrast effect.

## STEP 16: Define Sound Design

Build a tactile handmade sound palette.

Recommended sound cues:

- Paper flip
- Scissor snip
- Cardboard slide
- Paper rustle
- Small wooden click
- Soft pop
- Tape peel
- Puppet joint tap
- Box opening
- Paper confetti scatter

Music direction:

- Match the BGM to the user's topic, culture, and emotional tone, not only to the generic papercraft style.
- For culturally specific topics, design BGM around the topic's relevant musical language. For example, a Mid-Autumn Festival explainer should lean toward restrained Chinese traditional color: guzheng, pipa, bamboo flute or xiao, light percussion, soft strings, and enough silence for narration.
- For science or classroom topics without a strong cultural identity, use light marimba, xylophone, pizzicato strings, soft percussion, and a warm educational tone.
- Keep music light under narration and apply ducking in the final mix.
- Avoid heavy cinematic bass, aggressive EDM, futuristic textures, or overdramatic scoring unless requested.

Paper-motion SFX direction:

- Design SFX from the actual video motion beats: page flip, paper door opening, paper rail slide, card flip, cardboard drawer pull, paper box opening, paper layer stack, soft pop, puppet joint tap, and paper rustle.
- Use SFX sparingly as tactile accents. They should make paper motion feel physical, not become exaggerated cartoon sounds.
- Map SFX to the storyboard timeline before final mixing so action, narration, and sound reinforce each other.

## STEP 18: Provide Negative Prompts

Always include a concise negative prompt block to protect the style.

Recommended negatives:

- smooth plastic 3D
- glossy CG render
- live-action realism
- flat vector illustration
- generic cartoon with no paper texture
- metallic sci-fi surfaces
- glass material
- cyberpunk neon
- digital glitch effects
- oil painting strokes
- realistic hair or skin pores
- no paper fibers
- no cut edges
- no layer shadows
- overly smooth edges
- high-speed camera orbit
- melting or liquid morphing

## STEP 19: Run Review Checklist

End with a checklist. Mark problems only when they matter to the user's requested output.

### Style checklist

- Paper fibers, folds, seams, and cut edges are visible.
- Cardboard thickness is visible.
- Layers cast physical shadows.
- The image feels like miniature photography, not flat illustration.
- The style avoids plastic CG and live-action realism.

### Character checklist

- Characters read as paper puppets.
- Faces and expressions are cut-paper simple.
- Joints and limbs feel assembled from separate parts.
- Motion remains stop-motion-like and physically plausible.

### Scene checklist

- Foreground, midground, background, and far background are clear.
- Props are paper-made and support the explanation.
- Labels, arrows, and cards do not block the core model.
- Each shot explains only one knowledge beat.

### Video checklist

- Camera moves match miniature filming.
- Transitions follow paper physics.
- Sound cues match paper, cardboard, and puppet movement.
- The storyboard duration fits the target length.
- The final memory sentence is clear.

### Audio checklist

- Voiceover duration fits the chosen video duration or the mismatch has been resolved with user confirmation.
- Voiceover ending is clean, with no tail noise, click, breath residue, or abrupt cutoff.
- BGM matches the topic's culture and emotional tone, not only the generic papercraft style.
- BGM stays under narration and does not compete with spoken information.
- Paper-motion SFX align with actual visual motion beats and remain subtle.

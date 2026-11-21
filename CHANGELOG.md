# Changelog

## v1.5

- 制作包分镜加载器: shots now run like 分镜提示词. The native 生成后操作 (increment / decrement / randomize / fixed, shown as 生成顺序控制) is replaced by 生成方式: 顺序生成 (default) / 手动设置, and 分镜序号 becomes a text field.
  - 分镜序号 is drawn like 分镜提示词's 镜号: the shot number and the pack total (2/5, 3/5 …; the total comes from 制作包状态检测 or the last run), arrows step by one, dragging the middle changes it, a click opens a box for a number or a list (6,9 / 3-5).
  - 顺序生成: one 运行 makes every shot from 分镜序号 to the last one. When a shot finishes, the server queues the next one at the front of the queue (a task another tab queued waits), and 分镜序号 follows it. An interrupted or failed shot queues nothing and keeps its number, so 运行 again redoes that shot. 0 or 1 starts from the first shot; when the pack is done 分镜序号 goes back to 1.
  - 手动设置: only the listed shots run, e.g. `6,9` or `3-5` (`0` = all), one after the other; the list stays as typed.
  - Keep ComfyUI's 运行次数 at 1: every queued copy would otherwise start its own pass through the pack.
  - Saved workflows open as 顺序生成 at their saved index (fixed opens as 手动设置 with that shot). Progress belongs to the prepared Shotlist page and starts over when the page changes. 预览当前分镜 shows the first listed shot.
  - 生成方式 is an optional input, so API prompts written before it (such as the 花果山画布 Remix Easy H3 Turbo / V2 workflows, which send an integer `shot_index` with `use_pack` off) validate and run unchanged; with `use_pack` off nothing is queued.

## v1.4.33

- Samplers: Motion Segment, 1st Sample Enhancer and Dual Sample 1st now default to euler, also for two-pass, and dual_clock_euler is no longer offered where ComfyUI samples H3 audio and video natively (ModelSamplingAV). On talk blocks (`@sound generated`) two-pass with dual_clock_euler gave the speech a steady music-like bed (pauses about -39 dB); the same run with euler was clean (about -55 to -61 dB). A saved dual_clock_euler opens as euler when the workflow is loaded in the browser (an API prompt that still names it is refused by ComfyUI's input check). On ComfyUI builds without native H3 sampling it is still the only choice. The bundled workflows now use euler.
- Shot Prompts: a dropped md now fills md_path when ComfyUI (on this machine) finds it without searching: a file of the same name, size and content in md_path's folder or in a folder md files were picked or read from before (remembered in ComfyUI/user/feihou_easy_h3_md_folders.json). No browser permission gives a page the path of a dropped file, and no disks are walked. Found: 刷新提示词 rereads that path after the md changes, also after a reload or restart. Not found: the node says so; the browser file handle is kept as before where the browser gives one, and 选择 md 文件 once remembers the folder for later drops.
- Shot Prompts, 镜号 in 顺序生成: 0 is no longer a separate 自动 mode. 0 or 1 runs the whole film again from block 1, and once the run is going 镜号 follows it (1/4, 2/4 …) like any number. So an interrupted run queued again with 镜号 untouched continues where it stopped, also when it was started with 0 (before, 0 kept showing 2/4（自动） but started the film over). A number the user changes (arrows, drag or typing) starts that block from its first segment, even when it is the block the interrupted run stopped in (sent to the run as `N!`; the following runs the film queues itself carry the plain number). Runs queued by an older version as `0@N` still continue at block N.
- H3动作迁移预处理: the Remake LoRA choice (`remake_lora`) is gone. The node no longer loads the Viggle Remake swap LoRA itself, on the 1st or the 2nd pass; put it in the workflow's LoRA list (bypass, strength 1) as before. Having it in both places loaded it twice (strength 2, and a second copy in VRAM). Saved workflows open with their mode and face-blur settings kept. Note that the LoRA list applies to every block, so a talk block in the same film now runs with it too.
- Adds the shot-md directive `@transition crossfade,frames=12` (`@转场 叠化,帧数=12`), written in the block that should fade in from the one before it; `@transition cut` (硬切, the default) keeps the hard cut. The previous block holds back its last `frames` frames (picture and sound) and saves that much shorter; this block is rendered the same number of frames before its `@range` (Setup reads the reference video from there too) and Motion Collect blends the two, so the joined film keeps its length and Video Combine V2 still joins the blocks without re-encoding. `frames` is 2–48 (default 12, 0.5 s at 24 fps). It cannot be used with `@continue` in the same block, is ignored on the first block and on `@passthrough` blocks, and a block run on its own without the held frames falls back to a hard cut. Motion workflow (动作迁移分段 + 收束) only; Sample Enhancer graphs keep the hard cut.
- Shot Prompts: the md's `@aspect` now also shows on Setup's 画幅比例. Picking, dropping or 刷新提示词 on an md sets Setup to its header `@aspect` (or the first block's) with a short notice, and a block with its own `@aspect` sets it when that block runs. The run already used `@aspect`; only the widget stayed on the old value.
- Motion Segment: new 叠化帧数 (`crossfade_frames`, after 上下文每段上限, greyed out outside 上下文窗口): the join of two split context segments now blends only the middle 6 / 12 (default, 0.5 s) / 18 / 24 frames of their overlap instead of the whole overlap (39 frames = 1.6 s with overlap=39), before them the previous segment and after them the new one. 全部重叠 keeps the old whole-overlap fade. With 分段方式 自动 the md sets it: `@segment context,window=192,overlap=39,chunk=30,fade=12` (6 / 12 / 18 / 24 / all).
- Motion Segment, 上下文每段上限: the later segments of a split block are now rendered on their own from the source and the pictures, no longer anchored to the previous segment's output (as keyframes, or as the Remake clean prefix). Anchoring carried the previous output's sharpening, saturation and grain into the next segment and it grew with every segment (measured on a 109 s Remake film: segment 2 about 1.7x the high-frequency energy and more grain than segment 1). Each later segment still renders the overlap with the previous one, which is cross-faded at the join. A block that continues the previous block with `@continue` still opens on that block's tail.
- H3动作迁移预处理: new 人脸识别模型 (`face_recognizer`) for `@blur_people`, greyed out with the face blur. It lists every insightface pack under models/insightface/models that has the 106-point landmark model and a recognition model, e.g. buffalo_l/w600k_r50.onnx (default) or antelopev2/glintr100.onnx. On a two-person car video both recognised the same faces at the 0.2 threshold with no mix-ups. Saved workflows keep their values.
- Shot Prompts: `start` is now 顺序生成 (default) or 手动设置; 自动续接 and 从头开始 are gone and saved workflows open as 顺序生成. In 顺序生成, 镜号 follows the film (1/4, 2/4 …): left as it is, an interrupted run continues and the film is still joined at the end; set to 1 the film starts over; set to another block the run starts there afresh (without the previous block's tail) and the blocks made before still join into the whole film; set to 0 (自动) every run starts the film over, and the display reads 0（自动）, 1/4（自动）, 2/4（自动）. A finished film sets 镜号 back to 1. 手动设置 is unchanged.
- Shot md `@aspect 9:16` (header for every block, or per block; 1:1 / 2:3 / 3:2 / 3:4 / 4:3 / 9:16 / 16:9 / 21:9): Setup uses it instead of its own aspect ratio, so a forgotten Setup setting cannot change the framing.
- H3动作迁移预处理: with 操作 自动 the node now says what happens: a block without `@mode` is not swapped or blurred (switch it on with `@mode swap` or 操作 人物替换); without 分镜提示词 connected to the node there is no preview, while Motion Segment still follows each block's `@mode`. The summary under the node is no longer cut off after two lines.
- Shot md `@blur_people` (H3动作迁移预处理, Remake 替换 with face blur): blur only the listed people's faces instead of the largest face of each frame. Each person is a moment and the face centre there, e.g. `@blur_people 30.13@0.18:0.58,47.2@0.68:0.35` (seconds@x:y, x and y as fractions of the frame), in the header or per block; a block uses only the moments inside its `@range`. The node takes those faces' identity with insightface buffalo_l (106-point alignment + w600k_r50, from models/insightface/models/buffalo_l, nothing is downloaded) and blurs a face only when it matches a listed person (cosine at least 0.2); a face recognition misses (back of the head, upside down, a hand over it) still counts when it overlaps that person's box within half a second. Other people stay sharp. The preview video on the node follows it. Checked on a two-person car video: the same person at least 0.21 even in profile, the other at most 0.13.
- Shot md fields now have English names with comma-separated values, so an md reads the same on any system language and is less easily broken by full-width marks: `@segment context,window=260,overlap=39,chunk=30` (also `chain,length=124,overlap=22` / `whole`; `chunk` is 20 / 30 / 45 / 60 seconds or `all`), `@mode swap` (or `off`), `@face_blur 3` (`auto` / `off`), `@sound source` / `generated`, `@hold 17.9`, `@audio block`, `@video none`, `@first 4`, `@passthrough`. The older Chinese keys and values (`@分段 上下文窗口 窗口=260 重叠=39 每段=30秒`, `@模式 人物替换`, `@脸部模糊 3档`, `@声音 原声`, `@定格 5秒`), space-separated values, bare numbers in field order, `;` / `、` separators, full-width `，＝：` and `name: value` are all still read, so old mds run unchanged. The context-window field 每段 is now named `chunk`; the old English name `segment=` still works. A key may also be followed by `:` (`@mode: swap`). Node tooltips and log lines use the English names.
- H3动作迁移预处理 has a new 模式 widget with three choices: 关闭 / 自动 / 人物替换. It replaces the Remake on/off switch, and old workflows open as 人物替换 or 关闭. Every option stays visible, and the mode greys out the ones it does not use: with 关闭 and 自动 all are grey; 人物替换 lights up the Remake LoRA and the face blur. 自动 follows the shot md's new `@mode swap` / `@mode off`, written in the header or per block; a block without it runs 关闭. Talk blocks (`@video none`) always run 关闭. The Remake LoRA (Viggle Remake swap) is chosen on the node (remake_lora, loaded in bypass mode at strength 1, on the 1st- and 2nd-pass model) and only 人物替换 blocks get it, so talk blocks in the same film run without it; remove it from the workflow's LoRA list. It defaults to the matching file found by name, or 无.
- Media video cards: with a md `@hold`, the card now plays the current block's part of the file. Before, it seeked to the block's film time, which includes the hold. A dance after a 17.9 s held talk block was looked for 17.9 s into the clip, past the end of the 14.9 s video, and the card sat frozen. A block that does not use the video (a talk block with `@video none`, or one lying inside the hold) plays the whole video on a loop instead of freezing on its first frame.
- Context windows: anchored windows no longer average their video over the overlap. The overlap keeps the earlier window's frames, and the later window takes over in the last 2 video tokens (about 8 frames). A dance first pass averaged two windows' takes there and froze for about 3 s (motion at 17–50 % of the reference from 8 to 11 s). The log says whether a block's windows are anchored.
- Context windows: every window after the first is now anchored on the one before it. Its first frames (their overlap) get a keyframe guide holding the previous window's prediction of those frames, refreshed at every sampling step, like the tail guide of a frame-window segment. Before, each window settled on its own framing and pose. In a dance block, the first window followed the talk block's tail and the second the reference video, so the overlap cross-faded between two different compositions and the motion there was averaged down. The first window keeps its own tail guide (or none).
- Context windows are now laid out from the start on the set overlap, instead of spread evenly at full length. Every window is the set length, the next one starts the overlap before it ends, and the last one only runs to the block's end. If that would leave the last window shorter than about 2 s (56 frames), it starts earlier instead. A 15 s dance (362 frames) with 260-frame windows and overlap 39 now samples 0–260 and 221–362, 401 frames in total. Before, it sampled 0–260 and 102–362: 520 frames with a 158-frame overlap. Talk blocks keep their pause-aligned windows.
- The window overlap options are now the real overlaps, 22 / 39 / 56 / 73 (default) / 90 / 107 frames. Two windows on the 17-frame grid always overlap by 5 + 17k frames, so the old 34 / 68 / 102 really meant 39 / 73 / 107. Old workflows and mds that write 34 / 68 / 102 (e.g. `@分段 上下文窗口 窗口=260 重叠=34`) still work and use 39 / 73 / 107.
- Motion Segment: workflows saved before 上下文每段上限 (`context_segment`) existed, such as the 11-02 口播舞蹈 workflow, open with that field set to 30 秒. Before, loading put another field's value there (e.g. the overlap "22"), and the run stopped with "context_segment 必须是整块一次、20 秒、30 秒、45 秒、60 秒之一". A valid saved choice is kept.
- Context windows: fixed "有时间位置没有被任何窗口覆盖" for some block lengths, e.g. a 17.9 s talk block. The last window's audio start was rounded down, so the clip's last audio frame lay in no window. A window that ends on the clip's last frame now takes the clip's last audio frames.
- Context windows now work for talk blocks (`@video none`) whose dialogue lines are time-tagged, e.g. `[00:09.00-00:12.60] <Subject 1> (S1) 看着镜头说：<d>…</d>`. Each window speaks only the lines it holds at least half of, so a sentence is never started by two windows. The windows are placed so their overlaps fall in the pauses between lines: a window may be shorter than the set length (down to about 5 s), and speech in the middle of an overlap counts most. The log reports any speech frames left in an overlap. When a window's first line starts more than 0.5 s into the window, or its last line ends more than 0.5 s before the end, the prompt gets a short note to keep the mouth closed there (Chinese or English, following the line). A talk block without time tags keeps the sequential frame-window segments. Untagged lines still go to every window, so camera moves meant to happen once should be tagged too.
- H3动作迁移预处理 / Motion Segment: a talk block (`@video none`) now skips Remake 替换 and the face blur, because it has no source video to edit (it used to stop with "Remake 替换模式需要参考视频 1"). The preprocess node no longer runs face detection or a preview for such a block. The log of a context-window block lists each window's length when they differ, and counts only the spoken frames left in overlaps (lip padding excluded).
- Reference videos are kept as 8-bit frames after decoding and turned into 0–1 floats only for the window being used (window encoding, color matching, 原片直出, the preprocess preview), with the same values as before. A 58 s 1080p video read at 720P now needs about 4 GB of RAM instead of about 19 GB (decoded frames are also no longer held twice while loading).
- Motion Segment: 分段方式 has a new first choice **自动** (now the default): the shot md decides with `@分段` — in the md header for every block, or in a block to override it, written with named fields, e.g. `@分段 上下文窗口 窗口=192 重叠=34 每段=30秒` (每段 can be 整块), `@分段 帧窗口 段长=124 重叠=22` or `@分段 不分段` (English names window / length / overlap / segment also work; bare numbers in that order are still read); values left out use the node's recommended ones (上下文窗口 345 / 68 / 30 秒, 帧窗口 124 / 22). Without `@分段` (or without a shot md) 自动 runs 不分段. With 自动 and 不分段 the window fields are greyed out and not used (不分段 joins `@split` segments and `@continue` blocks on 22 frames).
- Remake 替换 mode for the Viggle Remake swap LoRA, switched on in the H3动作迁移预处理 node below (`viggle_remake_swap_comfyui`, add it to the LoRA list with bypass on). Reference video 1 is the source to edit and the pictures the new look, presented the way the LoRA was trained: text-only labels (`<Video 1>:` with its 2 fps time stamps, `<Picture N>:`, then the prompt, no vision tokens), the source on the output canvas without its audio, pictures at the output's short edge, and the soundtrack held clean as the target audio (silence with 不送入模型). Sampling uses the 6-step swap schedule (steps is not used; two-pass runs its first `first_steps` steps in the 1st pass and the rest in the 2nd, second_steps is not used), keyframes are dropped below sigma 0.9 in both passes, and a continued segment opens with the previous segment's frames held clean (whole 17-frame groups of the overlap) instead of a keyframe guide. Works with shot blocks, frame windows and context windows. Written after Viggle's Apache-2.0 nodes; the Viggle node pack is not needed.
- New node **H3动作迁移预处理** (`FeiHouEasyH3MotionPreprocess`), between Setup and Motion Segment (h3_context in and out); bypass it when not needed. Its `remake` switch is the only place Remake 替换 is turned on (Motion Segment has no switch of its own). 原片脸部模糊 has its own on/off switch (off greys out the level and detector): before reference video 1 is encoded, the face in each source frame is found with a detector from `models/ultralytics/bbox` (default `face_yolov8m.pt`) and a feathered ellipse from brows to chin is Gaussian-blurred, so in half-body shots and close-ups the look comes from the pictures instead of the original face; head position, turns, blinks and the mouth's opening still show. Levels 1–5 are fixed strengths, the edge is feathered; 自动 picks the level per frame from the face's size in the frame (far small faces about 0, half-body about 2–3, face close-ups 5), averaged over neighbouring frames. Shot md blocks can set `@脸部模糊 关/自动/1–5`, which wins over the node; with the switch off nothing is blurred. Detections are cached per frame, so overlapping context windows detect once. The film's soundtrack, color matching and 原片直出 use the untouched source. When blurring is on, the node runs face detection over the whole current block with a progress bar on the node and shows the blurred block as a preview video on the node (canvas size, with a one-line summary of frames, faces found and levels used); Motion Segment then reuses those detections, also at the 2nd pass's larger size. An optional 分镜提示词 input makes the preview follow the block's `@脸部模糊`. Needs ultralytics (the library Face Refine uses).
- Motion Segment: context-window sampling is switched back on. `segmentation` (分段方式) is now 不分段 / 帧窗口 / 上下文窗口 and replaces the `frame_window` switch; `segment_frames` / `overlap_frames` are shared by the modes and replace `context_window_frames` / `context_overlap_frames`. Each mode shows its own options (帧窗口: 39–497 frames, overlap 22 / 39 / 5; 上下文窗口: 124–362 frames, overlap 34 / 68 / 102), starts from its recommended values (124 / 22, 345 / 68) and remembers what you last set in it (`properties.feihou_segment_modes`), so switching back restores those values. 不分段 greys out `segment_frames`. A context-window block marked `@continue` anchors on 22 tail frames. Saved workflows are migrated: `frame_window` off becomes 不分段, the old context fields become the 上下文窗口 values.
- Shot md: a prompt line that starts with a time range in film time (`[00:03.50-00:08.00]` or `[3.5-8]`, the `@range` clock) is an action for that time only. Every sampled window — context window, frame-window segment, whole block, and the 2nd pass that re-encodes it — keeps only the tagged lines that overlap it by at least 0.5 s (or lie inside it), without the tag; untagged lines go to every window. A whole-prompt encode just drops the tags. Fixes context-window blocks repeating the whole timeline in every window (actions early and repeated, ghosting where windows overlap).
- Shot md: the 45 s limit per block is gone; a block can be as long as the reference video (e.g. a 91 s one-take sampled with context windows). `@split` segments stay at most 15 s and 原片直出 blocks at most 20 s.
- Context windows: every window's reference media is encoded first and every window's prompt after it, so the video VAE and the text encoder are loaded once per pass instead of once per window (a 91 s block with 15 windows swapped the 25 GB text encoder in 15 times before sampling). The reference picture is VAE-encoded once per pass. Encoded prompts are cached by content (prompt plus every picture and video frame the text encoder sees), so the same window in a rerun skips the text encoder.
- Motion Segment: new `context_segment` (上下文每段上限: 整块一次 / 20 / 30 / 45 / 60 秒, default 30 秒), used with 上下文窗口 only (greyed out otherwise). A longer block is sampled in segments of at most that length, one per run, each still with context windows; a segment starts on the previous one's last `overlap_frames` (the window overlap), anchored to its decoded frames, and that overlap cross-fades from the old segment into the new one. Results show up segment by segment and an interrupted run keeps its finished segments; total compute stays about the same.
- Motion Collect: with save_segments on, each saved segment also shows in the video node fed by Collect's images output (e.g. Video Combine V2 成片), which otherwise only runs on the block's last segment.
- Motion Collect: `filename_prefix` is removed. Saved segments go to the same folder as the joined video: the `filename_prefix` of the node fed by Collect's images output (e.g. Video Combine V2), plus `_b001_seg01` (`FeiHou_H3_motion/segment` when nothing is connected). Saved workflows keep their `color_match_strength`.

## v1.4.32

- Shot Prompts in a Sample Enhancer graph (two-pass v8 style, no Motion Segment): Setup now encodes each md block itself instead of leaving a motion placeholder, so the shot list and production packs both work in that workflow. 自动续接 queues the next block on the server at the front of the queue, so a task queued from another tab waits until the film is done; a failed or interrupted run keeps its block, and 手动设置 keeps its pick list. `@continue` blocks start their window earlier by the overlap so 视频接续（合并） can join them; `@passthrough` still needs the motion workflow.
- Reference video length: a gallery video longer than the generation is now read only for the generated seconds (from its time trim or frame window start), and each reference video's soundtrack is cut to the frames actually encoded, so a 20 s video in a 6 s generation no longer sends 20 s of audio. Duration alignment still reads the whole video.
- Shot Prompts: an md file can now be dropped onto the prompt box as well, not only the title bar and menus.

## v1.4.31

- Cloud access: settings, LoRA and model lists, immediate prompt optimization, dependency checks and reading a shot md by path can now be opened from a remote browser (for example a cloud pod behind a reverse proxy) once the server sets `FEIHOU_EASY_H3_ALLOW_REMOTE=1` or has an empty `ComfyUI/user/default/ComfyUI-FeiHou-Easy-H3/allow_remote` file. Off by default; forwarded client IPs are never trusted. API keys stay masked, remote md reads are limited to ComfyUI's input and user folders, the md picker falls back to the browser, and remote dependency installs also need `FEIHOU_EASY_H3_ALLOW_REMOTE_INSTALL=1`. The 403 message now says how to switch it on.

## v1.4.30

- Motion transfer: a shot md whose blocks are all `@video none` (reference generation only, no reference video) now runs in the motion transfer workflow; each block takes its length from `@range`, and color matching is skipped when there is no source video.
- Motion transfer: shot blocks without `@split` follow Motion Segment's frame window setting: 帧窗口分段 cuts them into `segment_frames` clips, 整段一次采完 samples the whole block at once. `overlap_frames` stays editable with the frame window off, since `@split` and `@continue` still use it.
- Dual Sample: a context that skipped Motion Segment now stops with a clear message instead of a latent shape mismatch.

## v1.4.29

- Motion transfer: Motion Collect now queues the film's next segment itself, right behind the running prompt at the front of the queue. A task queued during a film waits until the film is finished instead of running in between and mixing up the run state, and switching workflow tabs no longer queues the wrong workflow. The browser only queues as a fallback, and progress updates only touch the workflow the run belongs to.
- Media node: several files dropped onto a card (or picked at once) fill that card and the ones after it in file-name order (2 before 10); files of another type are skipped and files past the last card are dropped.

## v1.4.28

- Media video cards now default to 720P: a source whose short edge is above 720 is read at 720P, a smaller one keeps its own size. This also applies to cards whose settings were never opened. A card explicitly set to 原始 keeps the original size; Media Bridge and linked videos are not limited.
- Short-edge presets now land exactly on their value (1080p at 720P gives 1280x720 instead of 1280x704), and a source at or below the preset is no longer resized.

## v1.4.27

- Removes GGUF transformer and text encoder support, so Easy H3 no longer needs ComfyUI-GGUF. Workflows that selected a `.gguf` model need a `.safetensors` model instead. Local GGUF prompt-optimizer models are unaffected.
- Motion transfer: audio cards can follow each shot block's time range.
- Fixes old video-card players that kept playing after a card was replaced or removed, which caused overlapping sound.

## v1.4.26

- The LoRA loader no longer needs rgthree-comfy: its widgets, row drawing and LoRA list are now built in, so it no longer shows up blank when rgthree-comfy is missing.
- Corrects the example workflow notes and guide: rgthree-comfy is only needed by the rgthree nodes kept in Two-Stage Sampling v8.

## v1.4.25

- Adds two example workflows: **Two-Stage Sampling v8** and **One-Click Long Video Replacement v1**. Reference media and previews are cleared; each workflow has notes for node and model downloads and for usage.
- Updates the workflow guide with the required nodes, the KJNodes version note, and the cloud drive, Bilibili and YouTube links.

## v1.4.24

- Adds motion transfer / person swap from a shot md: **FeiHou Easy H3 Shot Prompts** (drop or pick a md, `start` 自动续接 / 从头开始 / 手动设置, 镜号 widget, md path remembered), **Motion Segment** and **Motion Collect**. One queue runs every block, saves each block and joins the film; interrupted runs resume. Run state lives in `input/feihou_h3_motion` (with an in-memory fallback); a finished film drops its tails and state, and files older than 48 h are pruned (`segment_*.mp4` are never deleted).
- Shot md directives: `@range`, `@pictures`, `@continue`, `@split` (in-block segments of at most 15 s), `@passthrough` / `@原片` (source frames, no sampling), `@sound 原声/生成`, `@video none`, header `@hold` (hold the reference video's first frame, silent) and `@first N`.
- Motion Segment: one or two passes is detected from the graph (its `h3_context` output feeding a 2nd Sample Enhancer means two-pass, with `first_steps`); `frame_window` switch (off = the whole video in one run); `segment_frames` shown with seconds; `reference_soundtrack` 送入模型 / 不送入模型; seed removed (noise comes from the workflow). Context-window sampling is in the code but switched off for now.
- Motion Collect outputs the joined film audio at 48 kHz stereo and can colour-match to the source per shot.
- Media node: per-type external sockets (`image_N`, `video_N` + `video_N_audio`, `audio_N`) that grow as you connect and renumber on disconnect; a connected input overrides the card of the same number. Video cards get 媒体设置 (frame window, frame rate); image previews are top-aligned; the node no longer grows on tab switches.
- Prompt templates: the motion template is split into 小肥猴H3人物替换 (background stays the video's and is described explicitly) and 小肥猴H3动作迁移 (background from the scene picture, the user's text or the person picture, described in detail; the video's framing and camera move are copied), each in English and Chinese.
- Windows: serves files without aiohttp sendfile, which stalls on some machines (Media cards stuck at 上传中, previews and scripts never arriving).

- Adds the shot-md directive `@first N` (`@首帧 N`): picture card N becomes the block's first frame (a keyframe at frame 0, kept in the output) when the block has no previous tail, e.g. a dance block run on its own. The card is only a keyframe, not a reference picture, so it need not be in `@pictures`. A continued block with a tail keeps the tail; both passes and context windows (first window) get the anchor.
- Adds Motion Segment `segmentation` (分段方式) = 上下文窗口 with `context_window_frames` / `context_overlap_frames`: a long shot block with reference video is denoised in one sampler run; at every step overlapping 17-frame-aligned windows are predicted with their own reference-video window (pictures and prompt in every window) and blended over the overlaps, so no window continues from decoded frames. Works for single sampling and for both passes of the two-pass chain (2nd Sample Enhancer rebuilds the same windows at its canvas). Talk blocks without reference video keep 接续; the default stays 接续.
- Motion Collect decodes such a block in clip-aligned pieces (identical frames) and keeps 8-bit frames meanwhile.

## v1.4.23

- Expands automatic duration alignment to Video 1-3 and Audio 1-3, using each selected source's trimmed duration.
- Changes the Output node's audio output to emit the selected alignment source's audio: the video soundtrack when aligning to a video, or the trimmed audio when aligning to an audio reference. Off keeps the previous Audio 1 behaviour.
- Keeps legacy `audio`/`video`, Boolean and localized alignment values working in saved workflows.

## v1.4.22

- Distinguishes identically named custom rules from built-in rules so selecting RefMod keeps the intended rule binding.

- Adds the saved RefMod filmmaking prompt rule, unchanged, as the read-only built-in `refmod` template (`RefMod`). Main and Setup can select it without creating a custom rule; existing custom rules remain intact.
- Restores prompt-optimization rule names after ComfyUI's R / Refresh Node Definitions command. Custom rules such as RefMod stay selectable by name in the main node and Setup, preserving the selected rule's ID and reloading rules saved in another page.

## v1.4.21

- Adds optional upstream `H3_REF_MODS` inputs to the main node and Setup, with joint text/vision presentation, direct reference latents, strengths/copies, independent gallery slots and an appended `Output.reference_map` output. RefMods survive high-resolution/trigger-word condition rebuilds in both sampling passes; image mode asks users to switch to reference mode.
- Adds original RefMod Loader/Axis references to the main node and Setup's `@` menu, with metadata-only discovery, saved source binding, and strengths/copies/component filtering. RefMods lead each modality's numbering; ordinary uploads follow them. Removing ordinary media no longer invalidates RefMod mentions. Replace old placeholder-video mentions via the new menu; genuinely removed references still fail clearly. Both sampling passes and prompt optimization use the same ordering.
- Saves continuation tails for independent manual segments when production-pack batching is off, including the first segment in Auto mode and hard cuts. Tail saving is independent of applying a previous-segment guide, in both manual and package runs.
- Preserves zero overlap for independent segments and prefers the saved native tail when a later segment selects the previous video. Existing V7 wiring remains valid.
- Adds local-model dependency checks and an install/repair button to Standard settings, including native GGUF import, GPU offload and Qwen/MTMD interface checks.
- Adds a Manager install.py hook and Windows manual-ZIP install scripts that use ComfyUI's Python, choose matching GGUF binary wheels and retain existing Torch/NumPy versions.
- Checks dependencies before unloading H3 models for local prompt optimization, and reports missing dependencies at startup without automatically installing during node import.
- Supports the generic MTMD interface used by current official CPU wheels, and respects CPU mode for the vision projector. RH is unchanged.

## v1.4.20

- Brings the Standard GitHub package into parity with the local installation, including the previously local-only `FeiHouEasyH3ContinueOut` continuation/merge node and its registration.
- Includes the matching Setup continuation guidance, production-pack transition/context controls, continuation-video upload/selection, per-shot state, and frontend persistence.
- Connect `tail_latent` to FeiHou Toolbox Video Combine V2 to save continuation tails and join the completed segments.
- Includes the local face-refinement canvas presets from 360P through 1080P and preserves the v1.4.19 local LLM features. RH is unchanged.

## v1.4.19

- Standard local prompt optimization adds embedded bare-safetensors profiles for Qwen3 0.6B, 1.7B, 4B, 14B, 32B, and Mistral 7B Instruct v0.3; existing Qwen3-8B support remains compatible.
- Bare-safetensors detection now uses exact tensor structure instead of filename guesses, so renamed files remain detectable without confusing unrelated models with the same layer count.
- DeepSeek-V4 runtime detection now checks whether the installed Transformers package actually contains its model implementation instead of assuming every 5.x package supports it.

## v1.4.18

- Standard local prompt optimization can load a single `.safetensors` file for the built-in Qwen3-8B, DeepSeek-V4 Flash, and DeepSeek-V4 Pro profiles.
- Detects those profiles from the safetensors tensor header, so renamed files still work; complete Transformers directories and GGUF behavior remain unchanged.
- Resolves a matching tokenizer from local files or the model's Hugging Face repository. RH is unchanged.

## v1.4.17

- Scans standalone `.safetensors` files under `models/llm`, including files whose names were changed.
- Shows standalone weights in Standard settings as metadata-only entries when their `config.json` and tokenizer/model directory are missing; they are visible for diagnosis but cannot be selected for generation.
- Keeps complete Transformers directories runnable and prevents duplicate entries for their weight shards. RH is unchanged.

## v1.4.16

- Adds an optional Standard-edition local LLM/VLM prompt optimizer. It discovers Transformers and GGUF models under `ComfyUI/models/llm`, with optional GGUF `mmproj` selection, device, context, and output-token settings.
- Keeps local model controls inside the existing prompt-optimizer settings and hides the node's optimizer provider and guide controls until prompt optimization is enabled.
- Local optimization unloads ComfyUI models around the request and releases temporary caches before H3 sampling continues. The RH edition is unchanged.
- Adds optional local-model dependencies in `requirements-local-llm.txt`; API prompt optimization remains available without installing them.
- Identifies GGUF prompt models from their metadata, including Qwen-Image PE weights using the Qwen35 architecture; distinguishes the I2I vision projector from the T2I text model.

## v1.4.14

- Production-pack preview now loads the shot's images, videos and audio into the Media node connected to FeiHou Easy H3 Setup (previously only the standalone main node showed them; Setup and its Media node stayed empty).
- The pack loader also finds Setup through KJNodes Set/Get pairs, not only direct links and reroutes, so prompts, parameters and media load in such workflows.
- Front-end only; generation already used the pack media at execution. Validation: JavaScript syntax checks. Not re-tested in a live ComfyUI session.

## v1.4.15

- Removes the standalone Dual Sample 1st/2nd nodes from the node menu. Sample Enhancer 1st/2nd remain available and continue to use their shared internal sampling implementation.

## v1.4.13

- Removes the custom API host and LAN endpoint allow-lists. Custom HTTPS-compatible API URLs can be configured directly without allowed_api_hosts.json.
- Keeps URL parsing, the external HTTPS requirement, TLS verification, redirect blocking, and local-only settings routes.
- Validation: Python and JavaScript syntax checks plus static reference checks. GPU generation and live third-party API requests were not performed.

## v1.4.12

- Adds a Standard-edition official-node two-pass H3 enhancement node. It samples a coarse high-noise pass, enlarges the denoised video latent, and continues with a low-noise refinement pass.
- Adds a `Default Trigger Words` switch to both Sample Enhancer passes. It defaults on and prepends the built-in detail trigger to that pass's encoded prompt without exposing the trigger text in the node UI.
- LoRA loading is intentionally not integrated; connect externally configured first/refinement models, including any user-selected LoRA chain.
- Resizes MiniMax H3 reference/keyframe latent metadata with the video latent and preserves the nested audio latent. GPU quality and memory usage were not re-tested.

## v1.4.11

- Production-pack loader accepts shots with no reference images. Such shots run as text-to-video (image mode with no media); shots that reference audio still need at least one image or video, with a clear error.
- Resolves asset IDs through the Shotlist's inline project data, so a user image declared as `P1` (for example `../人物.jpg`) loads as P1 without being renamed. Files named after the ID (`P3.png`) still take priority; paths outside the package are ignored.
- Clearer missing/ambiguous asset messages. Folder paths for local ComfyUI are unchanged and need no ZIP. No UI or widget-order changes; RH edition unchanged. Loader validation tested with sample packs; GPU generation not re-tested.

## v1.4.10

- Removes the face-refinement 24 FPS restriction; uses the frame rate supplied by H3 context for existing timing calculations and output.
- No new controls, interpolation checks, or widget-order changes. Syntax and file synchronization verified; non-24-FPS GPU quality not re-tested.

## v1.4.9

- Checks standard linear LoRA dimensions before bypass loading. Skips incompatible layers with per-layer warnings and a partial-load summary; rejects LoRAs with no usable matched layers.
- Preserves matching adapters, strength, raw caches and multiple-LoRA composition. Other adapter types remain delegated to ComfyUI with an explicit unvalidated warning.
- No UI/widget-order changes. CPU compatibility regressions tested; full GPU quality not re-tested.

## v1.4.8

- Hides the LoRA stack bypass toggle and consistently enables additive bypass loading, including saved regular-mode workflows and legacy API inputs.
- Preserves LoRA names, strengths, enable flags, stack chaining and widget order. Does not change external official LoRA loaders or the RH edition.
- Validates saved-workflow migration, backend stack normalization and syntax. Full GPU quality and memory usage were not re-tested for this release.

## v1.4.7

- Restores explicitly authorized LAN inference APIs, including remote Ollama and HTTP OpenAI-compatible services, through the server-local allowed_api_hosts.json lan_endpoints list.
- Matches exact scheme, RFC1918 IPv4 address and port; does not automatically allow entire private networks. Existing public-host allow-lists and local Ollama remain compatible.
- Retains public HTTPS requirements, TLS verification, redirect blocking and local-only settings routes. The web UI cannot modify the allow-list.
- Documents LAN configuration and deployment requirements in both READMEs and the API Settings panel.
- Validation: allow-list regression tests, Python/frontend syntax and diff checks. No live LAN server connection was tested. RH is unchanged.

## v1.4.6

- Adds reference-video time ranges and play/stop previews; video frames and source audio are cropped together with 100 ms input normalization.
- Replaces the automatic-duration toggle with Off / Reference audio / Reference video alignment. Uses the first reference of the selected type, preserves legacy enabled toggles as audio alignment, and retains frame rounding and output-duration cropping.
- Includes clone-safe sampling handoff/cache fixes, multi-LoRA regular/bypass selection, and the standalone resolution selector with conditional custom dimensions.
- Improves production-pack multi-audio reference handling and documents the HTML integration contract.
- Adds experimental face refinement with optional original-audio conditioning, clean second-pass model selection, and automatic cropped reference-face input. Optional dependencies and limitations are documented in FACE_REFINE.md.
- Validation: Python/JavaScript syntax, JSON parsing, crop/playback simulations and saved-widget migration tests. No new GPU quality or live-cloud compatibility guarantees.

## v1.4.5

- Supports production-pack ZIP upload, inspection and preview from same-origin cloud ComfyUI browsers, including gateways that strip Origin while preserving same-origin Fetch Metadata. Adds credential-free access diagnostics.
- Resolves relative folder/ZIP paths under ComfyUI/input; retains absolute paths with remote directory restrictions and a server-local remote_roots configuration. Cloud deployments still require platform authentication.
- Leaves generation resolution and pixel dimensions under manual control: package inspection no longer validates or applies them, and old cached previews cannot override them. Aspect-ratio and FPS handling are retained.
- Documents cloud setup and folder permissions without a fixed domain allow-list. Local configuration and development tests are excluded from publication.
- Validation: seven local route/regression tests, syntax checks and user-confirmed cloud access. No GPU performance claims.

## v1.4.4

- Fixes the streamed final-layer wrapper for the updated ComfyUI 0.35 H3 calling convention, retaining compatibility fallbacks.
- Improves Production Pack Loader folder/HTML discovery, referenced-asset selection, optional voice-reference handling, generation order, preview, ZIP/path selection and responsive diagnostics.
- Adds an opt-in advanced text-encoder-only reference mode, without reference VAE latents. The built-in sparse-attention experiment is not included; use the official node for configurable sparse attention.
- Preserves saved widget positions, including migration from the brief two-switch experimental build, and adds a stable gap beneath the prompt editor.
- Preserves frontend cross-plugin isolation. Removes development test directories from the distributed package.
- Validation: targeted CPU/frontend regression checks and user-reported local testing; no new GPU benchmark claims.

## v1.4.3

- Isolates standard-edition frontend installation markers and matches exact node class IDs, preventing cross-plugin media parameter interference.
- Stops reference generation explicitly when visual media is missing or prompt references are unresolved.
- Adds the experimental Production Pack Loader: folder/ZIP Shotlist import, ordered referenced assets, per-shot prompts/audio ranges, and native queue index increment.
- Preserves existing main-node widget/output ordering and isolates preview settings between queued shots. Includes Chinese/English UI and usage documentation.
- Validation: CPU regression tests, frontend syntax and transport-order simulations, and the 36-shot sample import. Full GPU/cloud generation was not performed for this release.

## v1.4.2

- Adds a local custom-API host allow-list, validates every outbound prompt-API request, and blocks redirects to unvalidated destinations.
- Restricts Easy H3 settings, model-discovery, prompt-optimization, and LoRA-list routes to the local ComfyUI host.
- Documents the allow-list in the Settings UI and both READMEs.
- Uses English as the backend/default node-language fallback and completes native ComfyUI `locales/en` and `locales/zh` definitions for the Remix Loader and Digital Human/MV Duration Crop.

## v1.4.1

- Adds a play/stop control beside each reference-audio trim range. Preview playback starts at the normalized trim start, stops at the trim end, and never changes the source file or the audio sent to H3.

## v1.4.0

- Adds **Digital human/MV auto duration**. When enabled, Audio 1's trimmed duration controls generation and locks the manual duration field.
- Adds **FeiHou Easy H3 Digital human/MV Duration Crop** and a matching `duration_control` output on FeiHou Easy H3 Output. H3 now rounds automatic-duration sampling up to its required `5 + 17N` frame count; the crop node restores the precise trimmed-audio duration after decoding, while passing ordinary workflows through unchanged.

## v1.3.12

- Adds per-slot **reference-audio trimming** below the embedded Audio 1–3 gallery. Start and end are separate fields; ranges are saved with their audio when reordered. Times accept forgiving input and normalize to 100ms precision (`MM:SS:MMM`); trimming affects only the temporary H3 audio payload, never the uploaded source file.
- Adds **CLIP** and trimmed **Audio 1** outputs to FeiHou Easy H3 Output. They are appended after existing outputs so saved workflow links keep their original slot positions.
- Aligns the reference-image **Match generation size** rule with the official H3 node's downscale-only output-pixel-area behavior. When an I2V/FL2V workflow actually requests second-pass sampling, its shared context now uses H3's reference-to-video latent representation.
- Prevents the embedded media panel from self-expanding indefinitely in ComfyUI Modern Nodes mode.

## v1.3.11

- Fixes native ComfyUI external-input support for the embedded Easy H3 UI. Connected widgets now preserve their graph links instead of being overwritten by panel defaults during workflow serialization.
- The fix covers duration, resolution, aspect ratio, width/height, FPS, advanced settings, force offload, reference options, and prompt-optimizer controls. Embedded image/video/audio uploads and ordering are unchanged.

## v1.3.10

- Adds **FeiHou Easy H3 Remix Loader**: FL2VA and REF2VA share one Remix transformer, while first-pass and second-pass LoRA stacks remain independent. The optional second-pass model selector now uses **None** to keep the normal model path.
- Adds an Ollama-only **Disable thinking** setting. When enabled, Easy H3 sends the native Ollama `think: false` request option.
- Adds non-blocking **complete low-VRAM streamed-block conflict diagnostics**. When that experimental switch is enabled, existing H3 block, output-head, and attention patches are reported in the console before generation continues.

## v1.3.9

- **Force offload** now also runs a one-time ComfyUI-native allocator cleanup immediately after each sampler returns and before VAE decoding. It releases only unused cached memory; it does not clear memory during denoising steps.
- Replaces the attention-only experiment with optional **Complete low-VRAM streamed blocks (experimental)**. The embedded GPL-3.0-or-later-derived MAINodes implementation streams H3 QKV, attention, MLP/SwiGLU, and output-head work using exact bf16 K/V defaults. See NOTICE for attribution and license details.
- Updates the low-VRAM sample workflow to use the built-in complete block-streaming switch and removes the competing `ModelAttentionBackend / comfy kitchen attention` patch from that workflow.

## v1.3.8

- Fixes prompt-guide titles to read ComfyUI's current locale at display time rather than locking in the language during frontend module initialization.
- Adds the missing Chinese/English fallback label for **R2VA 加强版 / R2VA Enhanced**, so the guide remains correctly named while the backend scheme list is loading or when frontend cache is refreshed.

## v1.3.7

- Adds the built-in **R2VA Enhanced** prompt guide directly below “General only”. It uses the supplied six-section full-reference prompt template for complex reference-video, identity-retention, source-video, audio, and digital-human workflows.
- With **Force offload** enabled, a cached second-pass transformer from the prior workflow execution is now released before the next first-pass model is prepared, preventing the two stages from unnecessarily sharing VRAM.
- Further improves the embedded media gallery and prompt-editor responsive layout when resizing the node.

## v1.3.6

- Adds optional custom second-pass FL2VA and REF2VA model selection in the Loader, plus a dedicated second-sampling model output from the main node.
- Adds optional second-pass LoRA disabling and releases first-pass model / LoRA resources before second-pass sampling.
- Releases the text encoder and video/audio VAEs from VRAM after preprocessing and before sampling, reducing peak VRAM pressure while retaining automatic ComfyUI offload behavior.
- Adds ComfyUI-native Chinese/English node-definition translations and localizes embedded LoRA-stack and prompt-preview UI text according to the ComfyUI language setting.

## v1.3.5

- Embedded images, videos, and audio can now be dragged within their own galleries to change their reference order. The new order is saved in the workflow and used by H3 and prompt optimization.

## v1.3.4

- Fixes the “＋ Add custom API” button in ComfyUI Settings. It now creates a custom API tab directly instead of relying on a browser-native prompt dialog that some ComfyUI hosts suppress.
- Custom API cards now include an auto-saving, editable service-name field.

## v1.3.3

- Fixes FeiHou Easy H3 Loader model dropdowns retaining a stale plugin-level
  file list. Newly saved models under `models/diffusion_models` now appear the
  next time ComfyUI refreshes node definitions, without requiring a further
  server restart.

## v1.3.2

- Built-in prompt-optimization services now start with only their editable default API address; no model is preloaded or selected automatically.
- Migrates only models injected by older plugin defaults: user-added models and API keys are preserved, while an obsolete default selection is cleared.

## v1.3.1

- Fixes saved workflows failing validation while prompt optimization is disabled. Dynamic API service/model and prompt-guide selections are no longer validated against the current Settings list before the node can read the optimization switch.

## v1.3.0

- Prompt optimization now sends API-only JPEG copies of reference images, capped by the node's selected `ref_image_size` and encoded at quality 85. Original H3 media is never modified.
- Reference videos now contribute compact first, middle, and last visual keyframes to compatible prompt-optimization APIs; standalone audio and video soundtracks remain local to H3 generation.
- Adds safe prompt-API diagnostics: logs include endpoint, model, API format, and media-size summaries while redacting API keys, filenames, Base64 payloads, and media contents.

## v1.2.0

- Removes filename-based filtering from the H3 Loader. Renamed community model files are now listed from their respective ComfyUI model folders.
- Lets users manually assign the selected diffusion model, text encoder, video VAE, and audio VAE to each H3 Loader role.
- Keeps `.safetensors` and `.gguf` discovery across the relevant ComfyUI model directories.

## v1.1.0

- Fixes workflow validation when prompt optimization is disabled: the serialized service value remains valid while the optimization switch controls whether an API request is made.
- Fixes an issue where ComfyUI serializing a Boolean as the string `"false"` could still incorrectly run prompt optimization.
- Improves compatibility with saved workflows and avoids unnecessary prompt-optimization API calls when the feature is off.

## v1.0.0

- Adds a visible `modified` declaration to the main node header, including H3 source (`nkxx188/ComfyUI-MiniMaxH3-Easy`) and API source (`yawiii/ComfyUI-Prompt-Assistant`).
- Embeds up to 9 images, 3 videos, and 3 standalone audio files directly in the Easy H3 node.
- Adds the FeiHou LoRA Stack input flow for the Easy H3 Loader.
- Adds ComfyUI Settings integration for API providers, configured service/model selection, and prompt-optimization rules.
- Applies the selected prompt scheme during normal node execution, and carries the final result to H3 Context / Prompt Preview.

## Attribution

This release includes modified work derived from [nkxx188/ComfyUI-MiniMaxH3-Easy](https://github.com/nkxx188/ComfyUI-MiniMaxH3-Easy) (MIT) and adapted API/prompt-optimizer portions from [yawiii/ComfyUI-Prompt-Assistant](https://github.com/yawiii/ComfyUI-Prompt-Assistant) (GNU GPL v3). The repository as a whole is released under GNU GPL v3; see [NOTICE](NOTICE), [LICENSE](LICENSE), and [LICENSES/MIT-ComfyUI-MiniMaxH3-Easy.txt](LICENSES/MIT-ComfyUI-MiniMaxH3-Easy.txt).

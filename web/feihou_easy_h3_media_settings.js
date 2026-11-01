// 「媒体设置」 of a reference-video card: VHS_LoadVideo-style options and how
// they follow each other.  Pure functions only; feihou_easy_h3_ui.js builds the
// popup.  The frame window (skip / cap at the chosen frame rate) is what the
// backend reads; the duration is the same window in seconds.

export const VIDEO_SIZES = Object.freeze(["原始", "匹配参考图", "360P", "480P", "540P", "640P", "720P"]);
// 720P only ever shrinks: a source with a shorter short edge keeps its size.
export const DEFAULT_VIDEO_OPTS = Object.freeze({ fps: 24, size: "720P", skip: 0, cap: 0 });

function wholeNumber(value, fallback = 0) {
    const number = Math.round(Number(value));
    return Number.isFinite(number) && number >= 0 ? number : fallback;
}

export function normalizeVideoOpts(value) {
    const source = value && typeof value === "object" ? value : {};
    const fps = Number(source.fps ?? DEFAULT_VIDEO_OPTS.fps);
    return {
        fps: Number.isFinite(fps) && fps >= 0 ? Math.min(120, Math.round(fps * 1000) / 1000) : DEFAULT_VIDEO_OPTS.fps,
        size: VIDEO_SIZES.includes(source.size) ? source.size : DEFAULT_VIDEO_OPTS.size,
        skip: wholeNumber(source.skip),
        cap: wholeNumber(source.cap),
    };
}

// Frame rate the frames are counted at: the chosen one, or the file's own for 0.
export function effectiveFps(opts, probe) {
    if (Number(opts?.fps) > 0) return Number(opts.fps);
    const native = Number(probe?.fps);
    return Number.isFinite(native) && native > 0 ? native : DEFAULT_VIDEO_OPTS.fps;
}

// Frames available at that rate, when the source length is known.
export function totalFrames(opts, probe) {
    const duration = Number(probe?.duration);
    return Number.isFinite(duration) && duration > 0 ? Math.max(1, Math.floor(duration * effectiveFps(opts, probe) + 1e-6)) : null;
}

function clampWindow(opts, probe) {
    const total = totalFrames(opts, probe);
    if (total == null) return opts;
    const skip = Math.min(opts.skip, total - 1);
    return { ...opts, skip, cap: opts.cap > 0 ? Math.min(opts.cap, total - skip) : 0 };
}

// Window in seconds; end 0 = to the end of the video.
export function windowSeconds(opts, probe) {
    const fps = effectiveFps(opts, probe);
    return { start: opts.skip / fps, end: opts.cap > 0 ? (opts.skip + opts.cap) / fps : 0 };
}

// Apply one edited field and bring the linked ones along.
//   change: { fps } | { skip } | { cap } | { size } | { start, end } (seconds)
// Frame rate keeps the time window and recounts its frames; skip / cap move the
// window in frames; a duration sets both.
export function applyVideoChange(opts, change, probe) {
    let next = normalizeVideoOpts(opts);
    if ("size" in change) next.size = normalizeVideoOpts({ size: change.size }).size;
    if ("fps" in change) {
        const { start, end } = windowSeconds(next, probe);
        next.fps = normalizeVideoOpts({ fps: change.fps }).fps;
        const fps = effectiveFps(next, probe);
        next.skip = Math.round(start * fps);
        next.cap = end > 0 ? Math.max(1, Math.round(end * fps) - next.skip) : 0;
    }
    if ("skip" in change) next.skip = wholeNumber(change.skip, next.skip);
    if ("cap" in change) next.cap = wholeNumber(change.cap, next.cap);
    if ("start" in change || "end" in change) {
        const fps = effectiveFps(next, probe);
        const start = Math.max(0, Number(change.start) || 0);
        const end = Math.max(0, Number(change.end) || 0);
        next.skip = Math.round(start * fps);
        next.cap = end > start ? Math.max(1, Math.round(end * fps) - next.skip) : 0;
    }
    return clampWindow(next, probe);
}

// Clock text: mm:ss:mmm (hh:mm:ss:mmm past an hour).
export function formatClock(seconds) {
    const totalMs = Math.max(0, Math.round((Number(seconds) || 0) * 1000));
    const ms = totalMs % 1000;
    const whole = Math.floor(totalMs / 1000);
    const pad = (value, width = 2) => String(value).padStart(width, "0");
    const hours = Math.floor(whole / 3600);
    const clock = `${pad(Math.floor((whole % 3600) / 60))}:${pad(whole % 60)}:${pad(ms, 3)}`;
    return hours > 0 ? `${pad(hours)}:${clock}` : clock;
}

// Accepts mm:ss:mmm, hh:mm:ss:mmm, mm:ss, mm:ss.mmm or plain seconds; null when unreadable.
export function parseClock(value) {
    let text = String(value ?? "").trim().replaceAll("：", ":").replaceAll("．", ".").replace(/\s+/g, "");
    if (!text) return 0;
    if (!text.includes(":")) return /^\d+(?:\.\d+)?$/.test(text) ? Number(text) : null;
    let fraction = 0;
    const dot = text.match(/^(.*:\d+)\.(\d+)$/);
    if (dot) {
        text = dot[1];
        fraction = Number(`0.${dot[2]}`);
    }
    const parts = text.split(":");
    if (parts.some((part) => !/^\d+$/.test(part))) return null;
    const values = parts.map(Number);
    if (!dot && values.length >= 3) {
        const msText = parts[parts.length - 1];
        fraction = values.pop() * 10 ** (3 - Math.min(3, msText.length)) / 1000;
    }
    if (values.length === 2) return values[0] * 60 + values[1] + fraction;
    if (values.length === 3) return values[0] * 3600 + values[1] * 60 + values[2] + fraction;
    return null;
}

export function formatClockRange(window) {
    return `${formatClock(window.start)}-${formatClock(window.end)}`;
}

// "a-b" → { start, end } in seconds, or null.
export function parseClockRange(value) {
    const parts = String(value ?? "").trim().split(/\s*(?:-|~|–|—|至|to)\s*/i);
    if (parts.length > 2) return null;
    const start = parts.length === 2 ? parseClock(parts[0]) : 0;
    const end = parseClock(parts[parts.length - 1]);
    if (start == null || end == null) return null;
    return end > 0 && end < start ? { start: end, end: start } : { start, end };
}

// What the settings will read, for the popup's summary line.
export function describeWindow(opts, probe) {
    const fps = effectiveFps(opts, probe);
    const total = totalFrames(opts, probe);
    const frames = opts.cap > 0 ? opts.cap : total != null ? Math.max(0, total - opts.skip) : null;
    return { fps, frames, seconds: frames != null ? frames / fps : null };
}

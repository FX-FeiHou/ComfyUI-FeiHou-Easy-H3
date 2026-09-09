// No ComfyUI imports: independently testable against a final rendered Shotlist.
// Package scripts run in an opaque-origin sandbox, never in ComfyUI's window.
export function extractShotlist(doc) {
    const projectData = doc.querySelector('script#dh-project-data[type="application/json"], script#mv-project-data[type="application/json"]');
    if (projectData) {
        const project = JSON.parse(projectData.textContent);
        return (project.shots || []).map((shot) => {
            const resolution = shot.resolution || project.resolution || "";
            return {
                ...shot,
                package_mode: project.package_mode || "",
                refs: shot.references || shot.refs || [],
                video_refs: shot.video_refs || shot.video_references || [],
                voice_reference: shot.voice_reference || project.audio_references?.[0]?.file || "",
                audio_file: shot.audio_file || project.master_audio || "",
                aspect_ratio: shot.aspect_ratio || project.aspect_ratio || "",
                fps: shot.fps ?? project.fps,
                // Production delivery dimensions (e.g. 4K) are not H3 presets.
                resolution: /^(360|416|480|540|640|720|768|832|928|1024|1080)p$/i.test(resolution) ? resolution : "",
                import_warnings: resolution && !/^(360|416|480|540|640|720|768|832|928|1024|1080)p$/i.test(resolution)
                    ? [`制作包分辨率 ${resolution} 不是 H3 预设，保留主节点分辨率。`] : [],
            };
        });
    }
    const structured = doc.querySelector('script#feihou-shotlist[type="application/json"]');
    if (structured) return JSON.parse(structured.textContent).shots;
    return [...doc.querySelectorAll("article.unit")].map((unit) => {
        const text = (selector) => unit.querySelector(selector)?.textContent.trim() || "";
        const title = text(".unit-head h2");
        const meta = text(".meta");
        const shots = {
            id: title.match(/H3-\d+/i)?.[0] || title,
            title,
            range: text(".time-pill"),
            refs: [...unit.querySelectorAll('.ref-chip:not([data-media-type="video"]):not([data-media-type="audio"])')].map((el) => el.textContent.trim()),
            video_refs: [...unit.querySelectorAll('[data-media-type="video"]')].map((el) => el.dataset.file || el.textContent.trim()),
            audio_file: unit.dataset.audioFile || "",
            aspect_ratio: meta.match(/\b(?:16:9|9:16|1:1|2:3|3:2|4:3|3:4|21:9)\b/)?.[0] || "",
        };
        const seconds = text(".duration-pill").match(/([\d.]+)\s*(?:s|秒)/i);
        if (seconds) shots.seconds = Number(seconds[1]);
        const fps = meta.match(/([\d.]+)\s*(?:fps|帧\/秒)/i);
        if (fps) shots.fps = Number(fps[1]);
        const resolution = meta.match(/\b(360|416|480|540|640|720|768|832|928|1024|1080)p\b/i);
        if (resolution) shots.resolution = `${resolution[1]}P`;
        for (const pane of unit.querySelectorAll(".prompt-pane")) {
            const heading = pane.querySelector(".prompt-head")?.textContent || "";
            const prompt = pane.querySelector("pre")?.textContent.trim() || "";
            if (/English|英文/i.test(heading)) shots.prompt_en = prompt;
            else if (/中文|Chinese/i.test(heading)) shots.prompt_zh = prompt;
        }
        return shots;
    });
}

export async function renderShotlist(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    // Disallow active external resources, nested contexts, refresh and forms.
    doc.querySelectorAll("base,meta[http-equiv],iframe,frame,object,embed,link,form,script[src]").forEach((el) => el.remove());
    for (const el of doc.querySelectorAll("*")) {
        for (const name of ["src", "srcset", "href", "action", "formaction", "poster"]) el.removeAttribute(name);
    }
    const policy = doc.createElement("meta");
    policy.httpEquiv = "Content-Security-Policy";
    policy.content = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; media-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
    doc.head.prepend(policy);
    const token = crypto.randomUUID();
    const harness = doc.createElement("script");
    // Capture listener/timer primitives before a package replaces window APIs.
    harness.textContent = `(()=>{const reply=parent.postMessage.bind(parent); const run=${extractShotlist.toString()};
      const schedule=window.setTimeout.bind(window); const listen=window.addEventListener.bind(window);
      listen('load',()=>schedule(()=>{try{reply({feihouPack:${JSON.stringify(token)},shots:run(document)},'*');}
      catch(error){reply({feihouPack:${JSON.stringify(token)},error:String(error)},'*');}},600));})();`;
    policy.after(harness);
    const frame = document.createElement("iframe");
    frame.sandbox = "allow-scripts"; // deliberately NOT allow-same-origin
    frame.hidden = true;
    frame.referrerPolicy = "no-referrer";
    return new Promise((resolve, reject) => {
        const finish = (error, value) => {
            clearTimeout(timer);
            window.removeEventListener("message", receive);
            frame.remove();
            if (error) reject(new Error(error)); else resolve(value);
        };
        const receive = (event) => {
            if (event.source !== frame.contentWindow || event.data?.feihouPack !== token) return;
            if (!Array.isArray(event.data.shots) || !event.data.shots.length) {
                finish(event.data.error || "No supported Shotlist cards found");
            } else finish(null, event.data.shots);
        };
        const timer = setTimeout(() => finish("Shotlist rendering timed out (10s)"), 10000);
        window.addEventListener("message", receive);
        frame.srcdoc = "<!doctype html>" + doc.documentElement.outerHTML;
        document.body.append(frame);
    });
}

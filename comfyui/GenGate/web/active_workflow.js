import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

app.registerExtension({
    name: "GenGate.ActiveWorkflow",
    setup() {
        let activeAt = Date.now();
        const used = () => { if (document.visibilityState === "visible") activeAt = Date.now(); };
        window.addEventListener("focus", used);
        window.addEventListener("pointerdown", used, { passive: true });
        document.addEventListener("visibilitychange", used);
        api.addEventListener("gengate.workflow.request", async ({ detail }) => {
            if (!/^[a-f0-9]{32}$/.test(detail?.nonce ?? "")) return;
            const body = { nonce: detail.nonce, activeAt, visible: document.visibilityState === "visible" };
            try {
                // Serialize the actual active tab, including unsaved widget values.
                body.graph = (await app.graphToPrompt()).output;
            } catch (error) {
                body.error = `Cannot serialize the active ComfyUI workflow: ${error.message ?? error}`;
            }
            try {
                await api.fetchApi("/gengate/active-workflow", {
                    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
                });
            } catch (error) { console.warn("GenGate workflow snapshot failed", error); }
        });
    },
});

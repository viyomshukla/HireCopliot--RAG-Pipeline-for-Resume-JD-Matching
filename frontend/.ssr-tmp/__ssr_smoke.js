import { renderToString } from "react-dom/server";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Fragment, jsx, jsxs } from "react/jsx-runtime";
//#region src/lib/format.ts
function years(n) {
	if (!Number.isFinite(n)) return "--";
	const v = Math.round(n * 10) / 10;
	return `${v} yr${v === 1 ? "" : "s"}`;
}
var DEGREES = {
	bachelor: "Bachelor's",
	master: "Master's",
	doctorate: "Doctorate",
	none: "Not stated",
	"": "Not stated"
};
var degree = (d) => DEGREES[(d ?? "").toLowerCase()] ?? d ?? "Not stated";
function when(iso) {
	if (!iso) return "";
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return "";
	return d.toLocaleString(void 0, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit"
	});
}
var JOB_STATE_LABEL = {
	queued: "Queued",
	ingesting: "Reading files",
	extracting: "Extracting candidates",
	indexing: "Indexing",
	ready: "Ready",
	failed: "Failed"
};
var isTerminal = (s) => s === "ready" || s === "failed";
/** The pipeline in order, so progress can be shown as named stages rather than
*  a percentage. `progress` is not linear: extraction takes minutes and
*  indexing takes seconds, so a smooth bar looks broken. */
var STAGES = [
	"queued",
	"ingesting",
	"extracting",
	"indexing",
	"ready"
];
var FILE_STATUS = {
	parsed: {
		label: "Parsed",
		tone: "ok",
		note: "Text extracted and indexed. These candidates can be ranked."
	},
	empty_text: {
		label: "No text found",
		tone: "bad",
		note: "The file opened but produced no readable text, almost always a scanned or photographed resume. These people applied and cannot be ranked. Ask them for a text-based file, or review them by hand."
	},
	corrupt: {
		label: "Unreadable file",
		tone: "bad",
		note: "The file could not be opened. It may have been damaged in transit. These people applied and cannot be ranked. Request the file again."
	},
	encrypted: {
		label: "Password protected",
		tone: "bad",
		note: "The file is locked and cannot be opened. These people cannot be ranked until they send an unprotected copy."
	},
	unsupported_format: {
		label: "Unsupported format",
		tone: "warn",
		note: "Only .pdf and .docx are read. Convert these files and upload them again."
	},
	too_large: {
		label: "Too large",
		tone: "warn",
		note: "The file exceeded the size limit and was skipped."
	},
	duplicate: {
		label: "Duplicate",
		tone: "muted",
		note: "The same resume appeared earlier in the batch. Counted once."
	}
};
var fileStatusMeta = (s) => FILE_STATUS[s] ?? {
	label: s.replace(/_/g, " "),
	tone: "warn",
	note: "This file was not added to the batch."
};
/** Only `parsed` resumes reach the ranker. */
var isRankable = (s) => s === "parsed";
//#endregion
//#region src/api.ts
/** The one base URL. The backend's CORS allowlist only admits an app served
*  from port 3000, which is why vite.config.ts pins that port. */
var API_BASE = "http://localhost:8000";
/**
* An API failure carrying enough detail to act on.
*
* `offline` distinguishes "the browser could not reach the server at all"
* from "the server answered with an error". The first is nearly always the
* backend not running, or a CORS rejection, and deserves different advice.
*/
var ApiError = class extends Error {
	status;
	offline;
	constructor(message, status, offline = false) {
		super(message);
		this.name = "ApiError";
		this.status = status;
		this.offline = offline;
	}
};
/** FastAPI reports errors as {detail: string} or, for validation failures,
*  {detail: [{loc, msg, type}]}. Flatten both into one readable sentence. */
function readDetail(body, fallback) {
	if (typeof body !== "object" || body === null) return fallback;
	const detail = body.detail;
	if (typeof detail === "string" && detail.trim()) return detail;
	if (Array.isArray(detail)) {
		const parts = detail.map((d) => {
			if (typeof d !== "object" || d === null) return null;
			const e = d;
			const field = Array.isArray(e.loc) ? e.loc.slice(1).join(".") : "";
			return field ? `${field}: ${e.msg ?? ""}`.trim() : e.msg ?? null;
		}).filter(Boolean);
		if (parts.length) return parts.join("; ");
	}
	return fallback;
}
async function request(path, init) {
	let response;
	try {
		response = await fetch(`${API_BASE}${path}`, init);
	} catch {
		throw new ApiError(`Cannot reach the API at ${API_BASE}. Check the backend is running, and that this page is served from port 3000 (the backend only allows that origin).`, 0, true);
	}
	if (!response.ok) {
		let body = null;
		try {
			body = await response.json();
		} catch {}
		throw new ApiError(readDetail(body, `${response.status} ${response.statusText}`), response.status);
	}
	if (response.status === 204) return void 0;
	return await response.json();
}
function postJson(path, body) {
	return request(path, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(body)
	});
}
var listJobs = () => request("/api/jobs");
var getJob = (jobId) => request(`/api/jobs/${encodeURIComponent(jobId)}`);
var deleteJob = (jobId) => request(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
/**
* Returns 202 Accepted, NOT a finished result.
*
* The response carries a job_id and nothing else of substance. Processing runs
* in a background task on the server and takes minutes. The caller must poll
* getJob(); treating this promise resolving as "the upload is done" is the
* single easiest bug to write against this API.
*/
function uploadZip(file, jobId) {
	const form = new FormData();
	form.append("file", file);
	if (jobId) form.append("job_id", jobId);
	return request("/api/upload", {
		method: "POST",
		body: form
	});
}
var rank = (body) => postJson("/api/rank", body);
var audit = (body) => postJson("/api/audit", body);
//#endregion
//#region src/lib/useHealth.ts
var PROBE_MS = 15e3;
function useHealth() {
	const [health, setHealth] = useState("checking");
	const [jobs, setJobs] = useState(null);
	const probe = useCallback(async () => {
		try {
			const response = await fetch(`${API_BASE}/api/health`);
			if (!response.ok) throw new Error(String(response.status));
			const body = await response.json();
			setHealth("up");
			setJobs(typeof body.jobs === "number" ? body.jobs : null);
		} catch {
			setHealth("down");
			setJobs(null);
		}
	}, []);
	useEffect(() => {
		let dead = false;
		const tick = async () => {
			await probe();
			if (dead) return;
		};
		tick();
		const id = window.setInterval(() => void tick(), PROBE_MS);
		return () => {
			dead = true;
			window.clearInterval(id);
		};
	}, [probe]);
	return {
		health,
		jobs,
		recheck: probe
	};
}
//#endregion
//#region src/lib/useTheme.ts
var KEY = "hiremind.theme";
function read() {
	try {
		const stored = localStorage.getItem(KEY);
		if (stored === "light" || stored === "dark" || stored === "system") return stored;
	} catch {}
	return "system";
}
function useTheme() {
	const [theme, setTheme] = useState(read);
	useEffect(() => {
		const root = document.documentElement;
		if (theme === "system") root.removeAttribute("data-theme");
		else root.setAttribute("data-theme", theme);
		try {
			localStorage.setItem(KEY, theme);
		} catch {}
	}, [theme]);
	return {
		theme,
		setTheme
	};
}
//#endregion
//#region src/components/Brand.tsx
/**
* THE MARK
*
* Three bars of decreasing length - a ranking - with the top one checked off.
* That is the whole product in one glyph: an ordered list, and a claim that
* something was verified rather than guessed.
*
* Drawn with `currentColor` for the bars and a token for the plate, so it
* survives both themes and can be dropped into a dark sidebar or a light
* header without a second asset.
*/
function Mark({ size = 28 }) {
	return /* @__PURE__ */ jsxs("svg", {
		width: size,
		height: size,
		viewBox: "0 0 32 32",
		fill: "none",
		"aria-hidden": "true",
		focusable: "false",
		className: "shrink-0",
		children: [
			/* @__PURE__ */ jsx("rect", {
				width: "32",
				height: "32",
				rx: "8",
				fill: "var(--c-accent)"
			}),
			/* @__PURE__ */ jsx("rect", {
				x: "0.5",
				y: "0.5",
				width: "31",
				height: "31",
				rx: "7.5",
				stroke: "var(--c-accent-bright)",
				strokeOpacity: "0.45"
			}),
			/* @__PURE__ */ jsxs("g", {
				stroke: "var(--c-accent-ink)",
				strokeWidth: "2.1",
				strokeLinecap: "round",
				children: [
					/* @__PURE__ */ jsx("path", { d: "M8 11h9" }),
					/* @__PURE__ */ jsx("path", { d: "M8 16h13" }),
					/* @__PURE__ */ jsx("path", { d: "M8 21h6" })
				]
			}),
			/* @__PURE__ */ jsx("circle", {
				cx: "22.5",
				cy: "10.5",
				r: "4.4",
				fill: "var(--c-accent-ink)"
			}),
			/* @__PURE__ */ jsx("path", {
				d: "m20.6 10.5 1.5 1.5 2.8-3",
				stroke: "var(--c-accent)",
				strokeWidth: "1.7",
				strokeLinecap: "round",
				strokeLinejoin: "round"
			})
		]
	});
}
function Wordmark({ compact = false }) {
	return /* @__PURE__ */ jsxs("span", {
		className: "flex items-center gap-2.5 min-w-0",
		children: [/* @__PURE__ */ jsx(Mark, { size: compact ? 24 : 30 }), /* @__PURE__ */ jsxs("span", {
			className: "min-w-0 leading-none",
			children: [/* @__PURE__ */ jsx("span", {
				className: "block truncate text-[15.5px] font-semibold tracking-[-0.02em] text-ink",
				children: "HireMind"
			}), !compact && /* @__PURE__ */ jsx("span", {
				className: "mt-[3px] block truncate text-[11px] text-ink3",
				children: "Evidence-first screening"
			})]
		})]
	});
}
//#endregion
//#region src/components/icons.tsx
function Icon({ size = 16, children, ...rest }) {
	return /* @__PURE__ */ jsx("svg", {
		width: size,
		height: size,
		viewBox: "0 0 24 24",
		fill: "none",
		stroke: "currentColor",
		strokeWidth: 1.7,
		strokeLinecap: "round",
		strokeLinejoin: "round",
		"aria-hidden": "true",
		focusable: "false",
		className: "shrink-0",
		...rest,
		children
	});
}
var IconBatches = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [
		/* @__PURE__ */ jsx("path", { d: "M12 3 3 7.5l9 4.5 9-4.5L12 3Z" }),
		/* @__PURE__ */ jsx("path", { d: "m3 12.5 9 4.5 9-4.5" }),
		/* @__PURE__ */ jsx("path", { d: "m3 17 9 4.5 9-4.5" })
	]
});
var IconUpload = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [
		/* @__PURE__ */ jsx("path", { d: "M12 16V4" }),
		/* @__PURE__ */ jsx("path", { d: "m7.5 8.5 4.5-4.5 4.5 4.5" }),
		/* @__PURE__ */ jsx("path", { d: "M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" })
	]
});
var IconRank = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [
		/* @__PURE__ */ jsx("path", { d: "M9 6h11" }),
		/* @__PURE__ */ jsx("path", { d: "M9 12h11" }),
		/* @__PURE__ */ jsx("path", { d: "M9 18h11" }),
		/* @__PURE__ */ jsx("path", { d: "M4 5.5 5.5 5v3.5" }),
		/* @__PURE__ */ jsx("path", { d: "M4 11.2c.5-.7 2-.7 2 .4 0 .9-2 1.4-2 2.4h2.2" }),
		/* @__PURE__ */ jsx("path", { d: "M4 16.5h2l-1.2 1.4A1.1 1.1 0 1 1 4 19.6" })
	]
});
var IconFairness = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [
		/* @__PURE__ */ jsx("path", { d: "M12 3.5v17" }),
		/* @__PURE__ */ jsx("path", { d: "M7 20.5h10" }),
		/* @__PURE__ */ jsx("path", { d: "M5 7.5h14" }),
		/* @__PURE__ */ jsx("path", { d: "m5 7.5-2.5 6a2.8 2.8 0 0 0 5 0Z" }),
		/* @__PURE__ */ jsx("path", { d: "m19 7.5-2.5 6a2.8 2.8 0 0 0 5 0Z" })
	]
});
var IconCheck = (p) => /* @__PURE__ */ jsx(Icon, {
	...p,
	children: /* @__PURE__ */ jsx("path", { d: "m4.5 12.5 5 5 10-11" })
});
var IconX = (p) => /* @__PURE__ */ jsx(Icon, {
	...p,
	children: /* @__PURE__ */ jsx("path", { d: "m6 6 12 12M18 6 6 18" })
});
var IconAlert = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [
		/* @__PURE__ */ jsx("path", { d: "M10.6 4.2 2.9 17.5A1.6 1.6 0 0 0 4.3 20h15.4a1.6 1.6 0 0 0 1.4-2.5L13.4 4.2a1.6 1.6 0 0 0-2.8 0Z" }),
		/* @__PURE__ */ jsx("path", { d: "M12 9.5v4" }),
		/* @__PURE__ */ jsx("path", { d: "M12 17h.01" })
	]
});
var IconInfo = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [
		/* @__PURE__ */ jsx("circle", {
			cx: "12",
			cy: "12",
			r: "8.5"
		}),
		/* @__PURE__ */ jsx("path", { d: "M12 11v5" }),
		/* @__PURE__ */ jsx("path", { d: "M12 8h.01" })
	]
});
var IconShield = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [/* @__PURE__ */ jsx("path", { d: "M12 3.2 5 6v5.5c0 4.2 2.9 7.6 7 9.3 4.1-1.7 7-5.1 7-9.3V6l-7-2.8Z" }), /* @__PURE__ */ jsx("path", { d: "m9.2 12 2 2 3.6-3.8" })]
});
var IconFile = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [/* @__PURE__ */ jsx("path", { d: "M13.5 3H7a1.8 1.8 0 0 0-1.8 1.8v14.4A1.8 1.8 0 0 0 7 21h10a1.8 1.8 0 0 0 1.8-1.8V8.3L13.5 3Z" }), /* @__PURE__ */ jsx("path", { d: "M13.3 3.2v4.4a1 1 0 0 0 1 1h4.3" })]
});
var IconArchive = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [
		/* @__PURE__ */ jsx("rect", {
			x: "3",
			y: "4",
			width: "18",
			height: "4.5",
			rx: "1.2"
		}),
		/* @__PURE__ */ jsx("path", { d: "M4.8 8.5v10A1.5 1.5 0 0 0 6.3 20h11.4a1.5 1.5 0 0 0 1.5-1.5v-10" }),
		/* @__PURE__ */ jsx("path", { d: "M10 12.2h4" })
	]
});
var IconTrash = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [
		/* @__PURE__ */ jsx("path", { d: "M4.5 6.5h15" }),
		/* @__PURE__ */ jsx("path", { d: "M9.5 6.5V5a1.2 1.2 0 0 1 1.2-1.2h2.6A1.2 1.2 0 0 1 14.5 5v1.5" }),
		/* @__PURE__ */ jsx("path", { d: "M6.5 6.5 7.4 19a1.5 1.5 0 0 0 1.5 1.4h6.2a1.5 1.5 0 0 0 1.5-1.4l.9-12.5" }),
		/* @__PURE__ */ jsx("path", { d: "M10.5 10.5v6M13.5 10.5v6" })
	]
});
var IconQuote = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [/* @__PURE__ */ jsx("path", { d: "M9.5 6.5C6.9 7.7 5.5 9.9 5.5 13v4.5h5V13H8c0-1.9.6-3.2 2.3-4.1Z" }), /* @__PURE__ */ jsx("path", { d: "M18.5 6.5c-2.6 1.2-4 3.4-4 6.5v4.5h5V13H17c0-1.9.6-3.2 2.3-4.1Z" })]
});
var IconDatabase = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [
		/* @__PURE__ */ jsx("ellipse", {
			cx: "12",
			cy: "6",
			rx: "7.5",
			ry: "3"
		}),
		/* @__PURE__ */ jsx("path", { d: "M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6" }),
		/* @__PURE__ */ jsx("path", { d: "M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3" })
	]
});
var IconArrowRight = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [/* @__PURE__ */ jsx("path", { d: "M4.5 12h15" }), /* @__PURE__ */ jsx("path", { d: "m13.5 6 6 6-6 6" })]
});
var IconRefresh = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [
		/* @__PURE__ */ jsx("path", { d: "M20 11.5a8 8 0 0 0-13.7-5L3.5 9.2" }),
		/* @__PURE__ */ jsx("path", { d: "M3.5 4.5v4.7h4.7" }),
		/* @__PURE__ */ jsx("path", { d: "M4 12.5a8 8 0 0 0 13.7 5l2.8-2.7" }),
		/* @__PURE__ */ jsx("path", { d: "M20.5 19.5v-4.7h-4.7" })
	]
});
var IconMenu = (p) => /* @__PURE__ */ jsx(Icon, {
	...p,
	children: /* @__PURE__ */ jsx("path", { d: "M4 7h16M4 12h16M4 17h16" })
});
var IconSun = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [/* @__PURE__ */ jsx("circle", {
		cx: "12",
		cy: "12",
		r: "4.2"
	}), /* @__PURE__ */ jsx("path", { d: "M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4" })]
});
var IconMoon = (p) => /* @__PURE__ */ jsx(Icon, {
	...p,
	children: /* @__PURE__ */ jsx("path", { d: "M20 14.2A8.4 8.4 0 0 1 9.8 4a8.5 8.5 0 1 0 10.2 10.2Z" })
});
var IconMonitor = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [/* @__PURE__ */ jsx("rect", {
		x: "3",
		y: "4.5",
		width: "18",
		height: "12",
		rx: "1.6"
	}), /* @__PURE__ */ jsx("path", { d: "M9 20.5h6M12 16.5v4" })]
});
var IconSearch = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [/* @__PURE__ */ jsx("circle", {
		cx: "10.8",
		cy: "10.8",
		r: "6.3"
	}), /* @__PURE__ */ jsx("path", { d: "m15.5 15.5 4 4" })]
});
var IconSort = (p) => /* @__PURE__ */ jsxs(Icon, {
	...p,
	children: [
		/* @__PURE__ */ jsx("path", { d: "M7 4.5v15" }),
		/* @__PURE__ */ jsx("path", { d: "m3.5 16 3.5 3.5L10.5 16" }),
		/* @__PURE__ */ jsx("path", { d: "M13.5 7h7M13.5 12h5M13.5 17h3" })
	]
});
//#endregion
//#region src/components/Shell.tsx
var NAV = [
	{
		key: "batches",
		label: "Batches",
		hint: "Everything uploaded to this server",
		Icon: IconBatches
	},
	{
		key: "upload",
		label: "Upload",
		hint: "Add a zip of resumes",
		Icon: IconUpload
	},
	{
		key: "rank",
		label: "Rank",
		hint: "Job description to shortlist",
		Icon: IconRank
	},
	{
		key: "fairness",
		label: "Fairness",
		hint: "Who the shortlist selects",
		Icon: IconFairness
	}
];
var HEALTH_META = {
	checking: {
		dot: "bg-ink3",
		label: "Checking",
		title: "Contacting the API"
	},
	up: {
		dot: "bg-moss",
		label: "API online",
		title: "The backend responded"
	},
	down: {
		dot: "bg-blood",
		label: "API offline",
		title: "The backend did not respond. Start the FastAPI server on port 8000."
	}
};
function HealthDot({ health, jobs }) {
	const meta = HEALTH_META[health];
	return /* @__PURE__ */ jsxs("div", {
		className: "flex items-center gap-2 px-2 py-1.5",
		title: meta.title,
		role: "status",
		"aria-live": "polite",
		children: [
			/* @__PURE__ */ jsx("span", {
				"aria-hidden": "true",
				className: `h-[7px] w-[7px] rounded-full ${meta.dot}`,
				style: health === "checking" ? { animation: "pulse-dot 1.2s ease-in-out infinite" } : void 0
			}),
			/* @__PURE__ */ jsx("span", {
				className: "text-[11.5px] text-ink2",
				children: meta.label
			}),
			health === "up" && jobs != null && /* @__PURE__ */ jsxs("span", {
				className: "ml-auto font-mono text-[11px] text-ink3",
				children: [
					jobs,
					" ",
					jobs === 1 ? "batch" : "batches"
				]
			})
		]
	});
}
var THEMES = [
	[
		"light",
		"Light",
		IconSun
	],
	[
		"system",
		"System",
		IconMonitor
	],
	[
		"dark",
		"Dark",
		IconMoon
	]
];
function ThemeSwitch() {
	const { theme, setTheme } = useTheme();
	return /* @__PURE__ */ jsx("div", {
		className: "flex rounded-md border border-rule bg-sunk p-[2px]",
		role: "group",
		"aria-label": "Colour theme",
		children: THEMES.map(([value, label, Icon]) => /* @__PURE__ */ jsxs("button", {
			type: "button",
			onClick: () => setTheme(value),
			"aria-pressed": theme === value,
			title: `${label} theme`,
			className: `flex flex-1 items-center justify-center rounded-[4px] py-1 transition-colors ${theme === value ? "bg-surface text-ink shadow-[var(--shadow-xs)]" : "text-ink3 hover:text-ink"}`,
			children: [/* @__PURE__ */ jsx(Icon, { size: 14 }), /* @__PURE__ */ jsx("span", {
				className: "sr-only",
				children: label
			})]
		}, value))
	});
}
function BatchContext({ jobId, job, onOpen }) {
	if (!jobId) return /* @__PURE__ */ jsxs("div", {
		className: "rounded-lg border border-dashed border-rule-strong px-3 py-2.5",
		children: [/* @__PURE__ */ jsx("p", {
			className: "eyebrow",
			children: "Active batch"
		}), /* @__PURE__ */ jsx("p", {
			className: "mt-1 text-[12px] text-ink3",
			children: "None selected. Open one from Batches, or upload a zip."
		})]
	});
	const running = job != null && !isTerminal(job.state);
	const failed = job?.state === "failed";
	return /* @__PURE__ */ jsxs("button", {
		type: "button",
		onClick: onOpen,
		className: "w-full rounded-lg border border-rule bg-surface px-3 py-2.5 text-left transition-colors hover:border-rule-strong",
		children: [
			/* @__PURE__ */ jsxs("div", {
				className: "flex items-center justify-between gap-2",
				children: [/* @__PURE__ */ jsx("span", {
					className: "eyebrow",
					children: "Active batch"
				}), /* @__PURE__ */ jsx("span", {
					className: `text-[11px] font-medium ${failed ? "text-blood" : running ? "text-accent-mid" : "text-moss"}`,
					children: job ? JOB_STATE_LABEL[job.state] : "..."
				})]
			}),
			/* @__PURE__ */ jsx("p", {
				className: "mt-1 truncate font-mono text-[11.5px] text-ink",
				children: jobId
			}),
			running && /* @__PURE__ */ jsxs("div", {
				className: "mt-2",
				children: [/* @__PURE__ */ jsx("div", {
					className: "h-[3px] w-full overflow-hidden rounded-full bg-sunk",
					children: /* @__PURE__ */ jsx("div", {
						className: "h-full rounded-full bg-accent",
						style: {
							width: `${Math.round((job?.progress ?? 0) * 100)}%`,
							transition: "width 400ms linear"
						}
					})
				}), /* @__PURE__ */ jsx("p", {
					className: "mt-1 truncate text-[11px] text-ink3",
					children: job?.stage_message
				})]
			}),
			job?.state === "ready" && /* @__PURE__ */ jsxs("p", {
				className: "mt-1 text-[11px] text-ink3",
				children: [/* @__PURE__ */ jsx("span", {
					className: "font-mono text-ink2",
					children: job.n_candidates
				}), " candidates indexed"]
			})
		]
	});
}
function Sidebar({ view, onView, activeJobId, activeJob, health, jobs }) {
	return /* @__PURE__ */ jsxs("div", {
		className: "flex h-full flex-col gap-5 overflow-y-auto px-3 py-4",
		children: [
			/* @__PURE__ */ jsx("div", {
				className: "px-2 pt-1",
				children: /* @__PURE__ */ jsx(Wordmark, {})
			}),
			/* @__PURE__ */ jsx("nav", {
				"aria-label": "Main",
				className: "flex flex-col gap-0.5",
				children: NAV.map(({ key, label, hint, Icon }) => {
					const current = view === key;
					return /* @__PURE__ */ jsxs("button", {
						type: "button",
						onClick: () => onView(key),
						"aria-current": current ? "page" : void 0,
						title: hint,
						className: `group relative flex items-center gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors ${current ? "bg-surface text-ink shadow-[var(--shadow-xs)]" : "text-ink2 hover:bg-surface/60 hover:text-ink"}`,
						children: [
							/* @__PURE__ */ jsx("span", {
								"aria-hidden": "true",
								className: `absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-r-full transition-colors ${current ? "bg-accent" : "bg-transparent"}`
							}),
							/* @__PURE__ */ jsx(Icon, {
								size: 16,
								className: current ? "text-accent" : "text-ink3"
							}),
							/* @__PURE__ */ jsx("span", {
								className: `text-[13.5px] ${current ? "font-semibold" : "font-medium"}`,
								children: label
							})
						]
					}, key);
				})
			}),
			/* @__PURE__ */ jsx(BatchContext, {
				jobId: activeJobId,
				job: activeJob,
				onOpen: () => onView(activeJob?.state === "ready" ? "rank" : "upload")
			}),
			/* @__PURE__ */ jsxs("div", {
				className: "mt-auto flex flex-col gap-2 border-t border-rule pt-3",
				children: [/* @__PURE__ */ jsx(HealthDot, {
					health,
					jobs
				}), /* @__PURE__ */ jsx(ThemeSwitch, {})]
			})
		]
	});
}
function AppShell({ view, onView, title, description, actions, activeJobId, activeJob, children }) {
	const [menuOpen, setMenuOpen] = useState(false);
	const { health, jobs } = useHealth();
	useEffect(() => {
		if (!menuOpen) return;
		const onKey = (e) => {
			if (e.key === "Escape") setMenuOpen(false);
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [menuOpen]);
	const sidebar = /* @__PURE__ */ jsx(Sidebar, {
		view,
		onView: (v) => {
			onView(v);
			setMenuOpen(false);
		},
		activeJobId,
		activeJob,
		health,
		jobs
	});
	return /* @__PURE__ */ jsxs("div", {
		className: "min-h-screen bg-canvas lg:grid lg:grid-cols-[248px_minmax(0,1fr)]",
		children: [
			/* @__PURE__ */ jsx("aside", {
				className: "sticky top-0 hidden h-screen border-r border-rule bg-paper lg:block",
				children: sidebar
			}),
			menuOpen && /* @__PURE__ */ jsxs("div", {
				className: "fixed inset-0 z-40 lg:hidden",
				children: [/* @__PURE__ */ jsx("button", {
					type: "button",
					"aria-label": "Close navigation",
					onClick: () => setMenuOpen(false),
					className: "absolute inset-0 bg-ink/40"
				}), /* @__PURE__ */ jsx("div", {
					className: "absolute inset-y-0 left-0 w-[268px] border-r border-rule bg-paper shadow-[var(--shadow-lg)] animate-rise",
					children: sidebar
				})]
			}),
			/* @__PURE__ */ jsxs("div", {
				className: "flex min-w-0 flex-col",
				children: [/* @__PURE__ */ jsxs("header", {
					className: "sticky top-0 z-30 border-b border-rule bg-paper/90 backdrop-blur",
					children: [/* @__PURE__ */ jsxs("div", {
						className: "flex items-center gap-3 px-4 py-3 sm:px-6",
						children: [
							/* @__PURE__ */ jsx("button", {
								type: "button",
								className: "btn btn-icon btn-ghost lg:hidden",
								"aria-label": "Open navigation",
								"aria-expanded": menuOpen,
								onClick: () => setMenuOpen(true),
								children: menuOpen ? /* @__PURE__ */ jsx(IconX, { size: 18 }) : /* @__PURE__ */ jsx(IconMenu, { size: 18 })
							}),
							/* @__PURE__ */ jsxs("div", {
								className: "min-w-0 flex-1",
								children: [/* @__PURE__ */ jsx("h1", {
									className: "truncate text-[17px] leading-tight font-semibold",
									children: title
								}), /* @__PURE__ */ jsx("p", {
									className: "mt-0.5 truncate text-[12.5px] text-ink2",
									children: description
								})]
							}),
							actions && /* @__PURE__ */ jsx("div", {
								className: "flex shrink-0 items-center gap-2",
								children: actions
							})
						]
					}), /* @__PURE__ */ jsxs("p", {
						className: "flex items-center gap-2 border-t border-rule bg-sunk/70 px-4 py-1.5 text-[11.5px] text-ink2 sm:px-6",
						children: [/* @__PURE__ */ jsx("span", {
							"aria-hidden": "true",
							className: "h-1 w-1 rounded-full bg-accent"
						}), "This tool ranks candidates and shows its evidence. It does not decide who to hire."]
					})]
				}), /* @__PURE__ */ jsx("main", {
					className: "min-w-0 flex-1 px-4 py-6 sm:px-6 lg:py-8",
					children
				})]
			})
		]
	});
}
//#endregion
//#region src/lib/useJobPoll.ts
var POLL_MS = 2e3;
var RETRY_MS = 4e3;
/**
* Polls one job until it reaches a terminal state, then stops.
*
* THREE THINGS THIS HAS TO GET RIGHT
*
* 1. It stops. `ready` and `failed` are terminal and no further request is
*    scheduled. A leaked poll loop hammers the API forever and is invisible
*    from the browser, so the only symptom is a server log nobody reads.
* 2. It never overlaps. A setTimeout chained after each response, rather than
*    setInterval, means a slow response cannot stack up a queue of requests.
* 3. It tears down. The cleanup cancels the pending timer and marks the run
*    dead, so a late response from an unmounted view cannot set state.
*/
function useJobPoll(jobId) {
	const [job, setJob] = useState(null);
	const [error, setError] = useState(null);
	const [polling, setPolling] = useState(Boolean(jobId));
	const timer = useRef(void 0);
	const [seenJobId, setSeenJobId] = useState(jobId);
	if (jobId !== seenJobId) {
		setSeenJobId(jobId);
		setJob(null);
		setError(null);
		setPolling(Boolean(jobId));
	}
	useEffect(() => {
		if (!jobId) return;
		let dead = false;
		const stop = () => {
			if (!dead) setPolling(false);
		};
		const tick = async () => {
			try {
				const next = await getJob(jobId);
				if (dead) return;
				setJob(next);
				setError(null);
				if (isTerminal(next.state)) {
					stop();
					return;
				}
				timer.current = window.setTimeout(tick, POLL_MS);
			} catch (err) {
				if (dead) return;
				const apiErr = err;
				setError(apiErr);
				if (apiErr.status === 404) {
					stop();
					return;
				}
				timer.current = window.setTimeout(tick, RETRY_MS);
			}
		};
		tick();
		return () => {
			dead = true;
			if (timer.current) window.clearTimeout(timer.current);
		};
	}, [jobId]);
	return {
		job,
		error,
		polling
	};
}
//#endregion
//#region src/components/Chrome.tsx
function ScoreMark({ value, width = 58 }) {
	const clamped = Math.max(0, Math.min(1, value));
	return /* @__PURE__ */ jsxs("span", {
		className: "inline-flex items-center gap-2",
		title: `Score ${value.toFixed(3)} of 1.000`,
		children: [/* @__PURE__ */ jsx("span", {
			role: "img",
			"aria-label": `Score ${value.toFixed(3)} out of 1`,
			className: "relative block h-[6px] shrink-0 overflow-hidden rounded-full bg-sunk",
			style: { width },
			children: /* @__PURE__ */ jsx("span", {
				className: "absolute inset-y-0 left-0 rounded-full",
				style: {
					width: `${clamped * 100}%`,
					background: "linear-gradient(90deg, var(--c-accent), var(--c-accent-bright))"
				}
			})
		}), /* @__PURE__ */ jsx("span", {
			className: "font-mono text-[12.5px] tabular-nums text-ink",
			children: value.toFixed(3)
		})]
	});
}
function MetCount({ met, total }) {
	const short = total > 0 && met / total < .5;
	return /* @__PURE__ */ jsxs("span", {
		className: "font-mono text-[12.5px] tabular-nums",
		children: [/* @__PURE__ */ jsx("span", {
			className: short ? "text-blood" : "text-ink",
			children: met
		}), /* @__PURE__ */ jsxs("span", {
			className: "text-ink3",
			children: ["/", total]
		})]
	});
}
var CHIP_TONE = {
	ok: "border-moss/35 bg-moss-soft text-moss",
	warn: "border-amber/40 bg-amber-soft text-amber",
	bad: "border-blood/35 bg-blood-soft text-blood",
	muted: "border-rule-strong bg-sunk text-ink2"
};
var CHIP_DOT = {
	ok: "bg-moss",
	warn: "bg-amber",
	bad: "bg-blood",
	muted: "bg-ink3"
};
function Chip({ tone = "muted", dot = false, pulse = false, children }) {
	return /* @__PURE__ */ jsxs("span", {
		className: `inline-flex items-center gap-1.5 rounded-full border px-2 py-[2px] text-[11px] leading-[1.6] font-medium whitespace-nowrap ${CHIP_TONE[tone]}`,
		children: [dot && /* @__PURE__ */ jsx("span", {
			"aria-hidden": "true",
			className: `h-[5px] w-[5px] rounded-full ${CHIP_DOT[tone]}`,
			style: pulse ? { animation: "pulse-dot 1.4s ease-in-out infinite" } : void 0
		}), children]
	});
}
var CALLOUT_TONE = {
	ok: {
		box: "border-moss/30 bg-moss-soft",
		icon: "text-moss"
	},
	warn: {
		box: "border-amber/35 bg-amber-soft",
		icon: "text-amber"
	},
	bad: {
		box: "border-blood/30 bg-blood-soft",
		icon: "text-blood"
	},
	muted: {
		box: "border-rule-strong bg-sunk",
		icon: "text-ink3"
	}
};
var CALLOUT_ICON = {
	ok: IconCheck,
	warn: IconAlert,
	bad: IconAlert,
	muted: IconInfo
};
function Callout({ tone = "muted", title, children, actions }) {
	const meta = CALLOUT_TONE[tone];
	const Icon = CALLOUT_ICON[tone];
	return /* @__PURE__ */ jsxs("div", {
		className: `flex gap-3 rounded-lg border px-3.5 py-3 ${meta.box}`,
		children: [/* @__PURE__ */ jsx(Icon, {
			size: 17,
			className: `mt-[1px] ${meta.icon}`
		}), /* @__PURE__ */ jsxs("div", {
			className: "min-w-0 flex-1 text-[13px] leading-[1.55] text-ink",
			children: [
				title && /* @__PURE__ */ jsx("div", {
					className: "mb-0.5 text-[13.5px] font-semibold",
					children: title
				}),
				children,
				actions && /* @__PURE__ */ jsx("div", {
					className: "mt-2.5 flex flex-wrap gap-2",
					children: actions
				})
			]
		})]
	});
}
function ErrorNote({ error, onRetry }) {
	if (!error) return null;
	return /* @__PURE__ */ jsx(Callout, {
		tone: "bad",
		title: error.offline ? "The API is not responding" : "That request failed",
		actions: onRetry ? /* @__PURE__ */ jsx("button", {
			type: "button",
			className: "btn btn-sm",
			onClick: onRetry,
			children: "Try again"
		}) : void 0,
		children: /* @__PURE__ */ jsx("p", { children: error.message })
	});
}
function Stat({ label, value, sub, tone }) {
	return /* @__PURE__ */ jsxs("div", {
		className: "min-w-0",
		children: [
			/* @__PURE__ */ jsx("p", {
				className: "eyebrow truncate",
				children: label
			}),
			/* @__PURE__ */ jsx("p", {
				className: `mt-1 font-mono text-[24px] leading-none tabular-nums ${tone === "accent" ? "text-accent" : tone === "blood" ? "text-blood" : tone === "amber" ? "text-amber" : "text-ink"}`,
				children: value
			}),
			sub && /* @__PURE__ */ jsx("p", {
				className: "mt-1.5 text-[11.5px] text-ink3",
				children: sub
			})
		]
	});
}
function EmptyState({ icon, title, children, action }) {
	return /* @__PURE__ */ jsxs("div", {
		className: "card flex flex-col items-center px-6 py-12 text-center",
		children: [
			icon && /* @__PURE__ */ jsx("div", {
				className: "mb-4 flex h-11 w-11 items-center justify-center rounded-xl border border-rule bg-sunk text-accent",
				children: icon
			}),
			/* @__PURE__ */ jsx("h3", {
				className: "text-[17px] font-semibold",
				children: title
			}),
			/* @__PURE__ */ jsx("div", {
				className: "mx-auto mt-1.5 max-w-[58ch] text-[13px] leading-[1.6] text-ink2",
				children
			}),
			action && /* @__PURE__ */ jsx("div", {
				className: "mt-5",
				children: action
			})
		]
	});
}
function Chevron({ open }) {
	return /* @__PURE__ */ jsx("svg", {
		width: "10",
		height: "10",
		viewBox: "0 0 10 10",
		"aria-hidden": "true",
		className: "shrink-0 text-ink3",
		style: {
			transform: open ? "rotate(90deg)" : "none",
			transition: "transform 200ms cubic-bezier(.22,1,.36,1)"
		},
		children: /* @__PURE__ */ jsx("path", {
			d: "M3 1l5 4-5 4",
			fill: "none",
			stroke: "currentColor",
			strokeWidth: "1.7",
			strokeLinecap: "round",
			strokeLinejoin: "round"
		})
	});
}
function BusyBar() {
	return /* @__PURE__ */ jsx("div", {
		className: "h-[3px] w-full overflow-hidden bg-sunk",
		role: "presentation",
		children: /* @__PURE__ */ jsx("div", {
			className: "h-full w-1/3 rounded-full",
			style: {
				background: "linear-gradient(90deg, transparent, var(--c-accent-bright), transparent)",
				animation: "sweep 1.2s ease-in-out infinite"
			}
		})
	});
}
function SkeletonRows({ rows = 4 }) {
	return /* @__PURE__ */ jsx("div", {
		className: "card overflow-hidden",
		"aria-hidden": "true",
		children: Array.from({ length: rows }).map((_, i) => /* @__PURE__ */ jsxs("div", {
			className: "flex items-center gap-4 border-b border-rule px-4 py-3.5 last:border-b-0",
			children: [
				/* @__PURE__ */ jsx("div", { className: "skeleton h-3 w-[26%]" }),
				/* @__PURE__ */ jsx("div", { className: "skeleton h-3 w-[14%]" }),
				/* @__PURE__ */ jsx("div", { className: "skeleton ml-auto h-3 w-[18%]" })
			]
		}, i))
	});
}
//#endregion
//#region src/views/BatchesView.tsx
/**
* THE BATCH LEDGER
*
* A table, not a deck of cards. Batches are compared on the same four figures
* every time - how many files arrived, how many parsed, how many candidates
* came out, and when - so they belong in aligned columns where a bad batch
* shows up as a short number in a column of longer ones.
*/
var GRID$1 = "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 md:grid-cols-[minmax(0,1.5fr)_118px_repeat(3,60px)_140px_auto]";
var FILTERS = [
	["all", "All"],
	["ready", "Ready"],
	["working", "Processing"],
	["failed", "Failed"]
];
function stateTone(state) {
	if (state === "ready") return "ok";
	if (state === "failed") return "bad";
	return "muted";
}
function matchesFilter(job, filter) {
	if (filter === "all") return true;
	if (filter === "ready") return job.state === "ready";
	if (filter === "failed") return job.state === "failed";
	return !isTerminal(job.state);
}
function BatchRow({ job, active, onOpen, onDeleted }) {
	const [arming, setArming] = useState(false);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState(null);
	const running = !isTerminal(job.state);
	const figures = [
		["files", job.n_files],
		["parsed", job.n_parsed],
		["people", job.n_candidates]
	];
	return /* @__PURE__ */ jsxs("li", {
		className: `relative border-b border-rule last:border-b-0 ${active ? "bg-accent-soft/35" : ""}`,
		children: [
			active && /* @__PURE__ */ jsx("span", {
				"aria-hidden": "true",
				className: "absolute inset-y-0 left-0 w-[3px] bg-accent"
			}),
			/* @__PURE__ */ jsxs("div", {
				className: `${GRID$1} px-4 py-3`,
				children: [
					/* @__PURE__ */ jsxs("div", {
						className: "min-w-0",
						children: [
							/* @__PURE__ */ jsxs("div", {
								className: "flex min-w-0 items-center gap-2",
								children: [/* @__PURE__ */ jsx("span", {
									className: "truncate font-mono text-[12.5px] text-ink",
									children: job.job_id
								}), active && /* @__PURE__ */ jsx("span", {
									className: "shrink-0 text-[11px] font-medium text-accent",
									children: "open"
								})]
							}),
							job.state === "failed" && job.error && /* @__PURE__ */ jsx("p", {
								className: "mt-1 line-clamp-2 font-mono text-[11.5px] text-blood",
								children: job.error
							}),
							running && job.stage_message && /* @__PURE__ */ jsx("p", {
								className: "mt-1 truncate text-[11.5px] text-ink2",
								children: job.stage_message
							}),
							running && /* @__PURE__ */ jsx("div", {
								className: "mt-1.5 h-[3px] w-full max-w-[220px] overflow-hidden rounded-full bg-sunk",
								children: /* @__PURE__ */ jsx("div", {
									className: "h-full rounded-full bg-accent",
									style: {
										width: `${Math.round(job.progress * 100)}%`,
										transition: "width 400ms linear"
									}
								})
							})
						]
					}),
					/* @__PURE__ */ jsx("div", {
						className: "hidden md:block",
						children: /* @__PURE__ */ jsx(Chip, {
							tone: stateTone(job.state),
							dot: true,
							pulse: running,
							children: JOB_STATE_LABEL[job.state]
						})
					}),
					figures.map(([label, value]) => /* @__PURE__ */ jsxs("div", {
						className: "hidden text-right md:block",
						children: [/* @__PURE__ */ jsx("span", {
							className: "font-mono text-[13px] tabular-nums text-ink",
							children: value
						}), /* @__PURE__ */ jsx("span", {
							className: "ml-1 text-[11px] text-ink3",
							children: label
						})]
					}, label)),
					/* @__PURE__ */ jsx("div", {
						className: "hidden text-[11.5px] text-ink3 md:block",
						children: when(job.created_at)
					}),
					/* @__PURE__ */ jsxs("div", {
						className: "flex items-center justify-end gap-1.5",
						children: [/* @__PURE__ */ jsxs("button", {
							type: "button",
							className: "btn btn-sm",
							onClick: onOpen,
							children: [job.state === "ready" ? "Rank" : "Open", /* @__PURE__ */ jsx(IconArrowRight, { size: 14 })]
						}), /* @__PURE__ */ jsx("button", {
							type: "button",
							className: "btn btn-sm btn-ghost",
							onClick: () => setArming(true),
							"aria-label": `Delete batch ${job.job_id}`,
							title: "Delete this batch",
							children: /* @__PURE__ */ jsx(IconTrash, { size: 15 })
						})]
					}),
					/* @__PURE__ */ jsxs("div", {
						className: "col-span-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-ink3 md:hidden",
						children: [
							/* @__PURE__ */ jsx(Chip, {
								tone: stateTone(job.state),
								dot: true,
								pulse: running,
								children: JOB_STATE_LABEL[job.state]
							}),
							figures.map(([label, value]) => /* @__PURE__ */ jsxs("span", { children: [
								/* @__PURE__ */ jsx("span", {
									className: "font-mono text-ink2",
									children: value
								}),
								" ",
								label
							] }, label)),
							/* @__PURE__ */ jsx("span", { children: when(job.created_at) })
						]
					})
				]
			}),
			arming && /* @__PURE__ */ jsxs("div", {
				className: "flex flex-wrap items-center gap-3 border-t border-blood/25 bg-blood-soft px-4 py-2.5",
				children: [
					/* @__PURE__ */ jsxs("span", {
						className: "text-[12.5px] text-ink",
						children: [
							"Delete ",
							/* @__PURE__ */ jsx("span", {
								className: "font-mono",
								children: job.n_candidates
							}),
							" candidates, every file and every vector in this batch? This cannot be undone."
						]
					}),
					/* @__PURE__ */ jsxs("div", {
						className: "ml-auto flex gap-2",
						children: [/* @__PURE__ */ jsx("button", {
							type: "button",
							className: "btn btn-sm btn-danger",
							disabled: busy,
							onClick: async () => {
								setBusy(true);
								setError(null);
								try {
									await deleteJob(job.job_id);
									onDeleted();
								} catch (err) {
									setError(err.message);
									setBusy(false);
									setArming(false);
								}
							},
							children: busy ? "Deleting..." : "Yes, delete it"
						}), /* @__PURE__ */ jsx("button", {
							type: "button",
							className: "btn btn-sm",
							disabled: busy,
							onClick: () => setArming(false),
							children: "Keep it"
						})]
					}),
					error && /* @__PURE__ */ jsx("span", {
						className: "w-full text-[12px] text-blood",
						children: error
					})
				]
			})
		]
	});
}
function BatchesView({ activeJobId, onOpen, onUpload }) {
	const [jobs, setJobs] = useState(null);
	const [error, setError] = useState(null);
	const [query, setQuery] = useState("");
	const [filter, setFilter] = useState("all");
	const load = useCallback(async () => {
		try {
			const next = await listJobs();
			setJobs(next);
			setError(null);
		} catch (err) {
			setError(err);
			setJobs(null);
		}
	}, []);
	useEffect(() => {
		load();
	}, [load]);
	useEffect(() => {
		if (!jobs?.some((j) => !isTerminal(j.state))) return;
		const id = window.setInterval(() => void load(), 4e3);
		return () => window.clearInterval(id);
	}, [jobs, load]);
	const shown = useMemo(() => {
		const q = query.trim().toLowerCase();
		return (jobs ?? []).filter((j) => matchesFilter(j, filter) && (!q || j.job_id.toLowerCase().includes(q)));
	}, [
		jobs,
		filter,
		query
	]);
	const counts = useMemo(() => {
		const all = jobs ?? [];
		return {
			total: all.length,
			candidates: all.reduce((sum, j) => sum + j.n_candidates, 0),
			working: all.filter((j) => !isTerminal(j.state)).length
		};
	}, [jobs]);
	if (error) return /* @__PURE__ */ jsx("div", {
		className: "mx-auto max-w-[1080px]",
		children: /* @__PURE__ */ jsx(ErrorNote, {
			error,
			onRetry: () => void load()
		})
	});
	if (!jobs) return /* @__PURE__ */ jsx("div", {
		className: "mx-auto max-w-[1080px]",
		children: /* @__PURE__ */ jsx(SkeletonRows, { rows: 5 })
	});
	if (jobs.length === 0) return /* @__PURE__ */ jsx("div", {
		className: "mx-auto max-w-[1080px]",
		children: /* @__PURE__ */ jsx(EmptyState, {
			icon: /* @__PURE__ */ jsx(IconArchive, { size: 22 }),
			title: "Start with a zip of resumes",
			action: /* @__PURE__ */ jsxs("button", {
				type: "button",
				className: "btn btn-primary btn-lg",
				onClick: onUpload,
				children: [/* @__PURE__ */ jsx(IconUpload, { size: 16 }), "Upload resumes"]
			}),
			children: /* @__PURE__ */ jsx("p", { children: "Upload one archive and the server reads every resume into a searchable batch. Then paste a job description and rank them, with the evidence for every score shown next to the candidate it belongs to." })
		})
	});
	return /* @__PURE__ */ jsxs("div", {
		className: "mx-auto max-w-[1080px] space-y-4",
		children: [/* @__PURE__ */ jsxs("div", {
			className: "flex flex-wrap items-center gap-3",
			children: [
				/* @__PURE__ */ jsxs("div", {
					className: "relative min-w-[180px] flex-1 sm:max-w-[280px]",
					children: [/* @__PURE__ */ jsx(IconSearch, {
						size: 15,
						className: "pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink3"
					}), /* @__PURE__ */ jsx("input", {
						className: "field pl-8 font-mono text-[12.5px]",
						placeholder: "Find a batch id",
						"aria-label": "Filter batches by id",
						value: query,
						onChange: (e) => setQuery(e.target.value)
					})]
				}),
				/* @__PURE__ */ jsx("div", {
					className: "flex rounded-md border border-rule bg-sunk p-[2px]",
					role: "group",
					"aria-label": "Filter by state",
					children: FILTERS.map(([key, label]) => /* @__PURE__ */ jsx("button", {
						type: "button",
						onClick: () => setFilter(key),
						"aria-pressed": filter === key,
						className: `rounded-[4px] px-2.5 py-1 text-[12.5px] transition-colors ${filter === key ? "bg-surface font-medium text-ink shadow-[var(--shadow-xs)]" : "text-ink2 hover:text-ink"}`,
						children: label
					}, key))
				}),
				/* @__PURE__ */ jsxs("div", {
					className: "ml-auto flex items-center gap-3",
					children: [/* @__PURE__ */ jsxs("span", {
						className: "hidden text-[12px] text-ink3 sm:inline",
						children: [
							/* @__PURE__ */ jsx("span", {
								className: "font-mono text-ink2",
								children: counts.total
							}),
							" batches,",
							" ",
							/* @__PURE__ */ jsx("span", {
								className: "font-mono text-ink2",
								children: counts.candidates
							}),
							" candidates",
							counts.working > 0 && /* @__PURE__ */ jsxs(Fragment, { children: [
								", ",
								/* @__PURE__ */ jsx("span", {
									className: "font-mono text-accent-mid",
									children: counts.working
								}),
								" processing"
							] })
						]
					}), /* @__PURE__ */ jsxs("button", {
						type: "button",
						className: "btn btn-sm btn-ghost",
						onClick: () => void load(),
						title: "Refresh the list",
						children: [/* @__PURE__ */ jsx(IconRefresh, { size: 15 }), "Refresh"]
					})]
				})
			]
		}), /* @__PURE__ */ jsxs("div", {
			className: "card overflow-hidden",
			children: [/* @__PURE__ */ jsxs("div", {
				className: `${GRID$1} hidden border-b border-rule bg-sunk/60 px-4 py-2 text-[11px] font-semibold tracking-[0.04em] text-ink3 uppercase md:grid`,
				children: [
					/* @__PURE__ */ jsx("div", { children: "Batch" }),
					/* @__PURE__ */ jsx("div", { children: "Status" }),
					/* @__PURE__ */ jsx("div", {
						className: "text-right",
						children: "Files"
					}),
					/* @__PURE__ */ jsx("div", {
						className: "text-right",
						children: "Parsed"
					}),
					/* @__PURE__ */ jsx("div", {
						className: "text-right",
						children: "People"
					}),
					/* @__PURE__ */ jsx("div", { children: "Created" }),
					/* @__PURE__ */ jsx("div", {
						className: "text-right",
						children: "Actions"
					})
				]
			}), shown.length === 0 ? /* @__PURE__ */ jsx("p", {
				className: "px-4 py-10 text-center text-[13px] text-ink3",
				children: "No batch matches that filter."
			}) : /* @__PURE__ */ jsx("ul", { children: shown.map((job) => /* @__PURE__ */ jsx(BatchRow, {
				job,
				active: job.job_id === activeJobId,
				onOpen: () => onOpen(job.job_id),
				onDeleted: () => void load()
			}, job.job_id)) })]
		})]
	});
}
//#endregion
//#region src/views/FairnessView.tsx
/**
* DISPARATE IMPACT
*
* Three things this screen has to hold at once:
*
*   A flag is a prompt, not a verdict. A ratio under 0.80 says "look at this",
*   and the copy and colour say that too. Nothing here is styled as an alarm.
*
*   An unreliable number must not read as a finding. Fewer than 10 people in a
*   group makes the rate noise, and a hatched bar plus muted type keeps a 0.00
*   from looking like discovered discrimination.
*
*   A missing ratio is not a good ratio. `impact_ratio: null` means no two
*   groups were large enough to compare, and it gets its own treatment rather
*   than an empty space that reads as "fine".
*/
var RATE_BAR = 132;
/** Group values arrive as raw attribute values. Booleans come through as the
*  strings "True" and "False", which read as nonsense under a heading like
*  "career continuity", so they are phrased as an answer instead. Locale codes
*  such as en_GB are meaningful as written and left alone. */
function groupLabel(value) {
	if (value === "True") return "Yes";
	if (value === "False") return "No";
	if (/^[a-z]{2}_[A-Z]{2}$/.test(value)) return value;
	return value.replace(/_/g, " ");
}
function GroupRow({ group, best }) {
	const rate = group.selection_rate;
	const relative = best > 0 ? Math.min(rate / best, 1) : 0;
	return /* @__PURE__ */ jsxs("tr", {
		className: "border-t border-rule",
		children: [
			/* @__PURE__ */ jsxs("td", {
				className: "py-2 pr-3",
				children: [/* @__PURE__ */ jsx("span", {
					className: `text-[12.5px] ${group.reliable ? "text-ink" : "text-ink3"}`,
					children: groupLabel(group.value)
				}), !group.reliable && /* @__PURE__ */ jsx("span", {
					className: "ml-2 text-[11px] text-ink3",
					children: "too small to interpret"
				})]
			}),
			/* @__PURE__ */ jsx("td", {
				className: "py-2 pr-3 text-right font-mono text-[12.5px] tabular-nums text-ink2",
				children: group.total
			}),
			/* @__PURE__ */ jsx("td", {
				className: "py-2 pr-3 text-right font-mono text-[12.5px] tabular-nums text-ink2",
				children: group.selected
			}),
			/* @__PURE__ */ jsx("td", {
				className: "py-2 pr-3 text-right",
				children: /* @__PURE__ */ jsxs("span", {
					className: `font-mono text-[12.5px] tabular-nums ${group.reliable ? "text-ink" : "text-ink3"}`,
					children: [(rate * 100).toFixed(0), "%"]
				})
			}),
			/* @__PURE__ */ jsx("td", {
				className: "py-2",
				children: /* @__PURE__ */ jsx("span", {
					className: "block h-[8px] overflow-hidden rounded-full bg-sunk",
					style: { width: RATE_BAR },
					role: "img",
					"aria-label": `${group.selected} of ${group.total} selected, ${(rate * 100).toFixed(0)} percent${group.reliable ? "" : ", too small to interpret"}`,
					children: /* @__PURE__ */ jsx("span", {
						className: `block h-full rounded-full ${group.reliable ? "bg-accent" : "hatch border border-rule-strong"}`,
						style: { width: `${Math.max(relative, 0) * 100}%` }
					})
				})
			})
		]
	});
}
function AttributePanel({ attr }) {
	const groups = attr.groups ?? [];
	const unreliable = groups.filter((g) => !g.reliable);
	const best = Math.max(0, ...groups.map((g) => g.selection_rate));
	const ratio = attr.impact_ratio;
	return /* @__PURE__ */ jsxs("section", {
		className: "card overflow-hidden",
		children: [
			/* @__PURE__ */ jsxs("div", {
				className: "border-b border-rule px-4 py-3",
				children: [/* @__PURE__ */ jsxs("div", {
					className: "flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1",
					children: [/* @__PURE__ */ jsx("h3", {
						className: "text-[14.5px] font-semibold capitalize",
						children: attr.attribute.replace(/_/g, " ")
					}), attr.flagged ? /* @__PURE__ */ jsx(Chip, {
						tone: "warn",
						dot: true,
						children: "Worth investigating"
					}) : ratio == null ? /* @__PURE__ */ jsx(Chip, {
						tone: "muted",
						dot: true,
						children: "Not enough data to compare"
					}) : /* @__PURE__ */ jsx(Chip, {
						tone: "ok",
						dot: true,
						children: "Above the four-fifths threshold"
					})]
				}), /* @__PURE__ */ jsx("p", {
					className: "mt-1 text-[12.5px] text-ink2",
					children: attr.description
				})]
			}),
			/* @__PURE__ */ jsxs("div", {
				className: "flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-rule bg-sunk/50 px-4 py-3",
				children: [/* @__PURE__ */ jsx("span", {
					className: "eyebrow",
					children: "Impact ratio"
				}), ratio == null ? /* @__PURE__ */ jsx("span", {
					className: "text-[13px] text-ink2",
					children: "Cannot be calculated. No two groups here were large enough to compare, so this is an absence of evidence, not a clean result."
				}) : /* @__PURE__ */ jsxs(Fragment, { children: [/* @__PURE__ */ jsx("span", {
					className: `font-mono text-[22px] leading-none tabular-nums ${attr.flagged ? "text-amber" : "text-ink"}`,
					children: ratio.toFixed(2)
				}), /* @__PURE__ */ jsx("span", {
					className: "text-[11.5px] text-ink3",
					children: "lowest selection rate divided by the highest, among groups of 10 or more"
				})] })]
			}),
			attr.flagged && /* @__PURE__ */ jsx("div", {
				className: "border-b border-rule bg-amber-soft px-4 py-3 text-[12.5px] leading-[1.55] text-ink",
				children: "This is below 0.80, which is the point at which an employment screen is conventionally examined. It does not establish that anything is wrong. Look at whether a requirement is standing in for something the job does not actually need."
			}),
			/* @__PURE__ */ jsx("div", {
				className: "overflow-x-auto px-4 py-2",
				children: /* @__PURE__ */ jsxs("table", {
					className: "w-full",
					children: [/* @__PURE__ */ jsx("thead", { children: /* @__PURE__ */ jsxs("tr", {
						className: "text-[11px] tracking-[0.04em] text-ink3 uppercase",
						children: [
							/* @__PURE__ */ jsx("th", {
								className: "pb-1.5 text-left font-semibold",
								children: "Group"
							}),
							/* @__PURE__ */ jsx("th", {
								className: "pb-1.5 pr-3 text-right font-semibold",
								children: "In batch"
							}),
							/* @__PURE__ */ jsx("th", {
								className: "pb-1.5 pr-3 text-right font-semibold",
								children: "Shortlisted"
							}),
							/* @__PURE__ */ jsx("th", {
								className: "pb-1.5 pr-3 text-right font-semibold",
								children: "Rate"
							}),
							/* @__PURE__ */ jsx("th", {
								className: "pb-1.5 text-left font-semibold",
								style: { width: RATE_BAR }
							})
						]
					}) }), /* @__PURE__ */ jsx("tbody", { children: groups.map((g) => /* @__PURE__ */ jsx(GroupRow, {
						group: g,
						best
					}, g.value)) })]
				})
			}),
			unreliable.length > 0 && /* @__PURE__ */ jsxs("p", {
				className: "border-t border-rule px-4 py-2.5 text-[11.5px] leading-[1.55] text-ink3",
				children: [
					unreliable.length,
					" ",
					unreliable.length === 1 ? "group has" : "groups have",
					" fewer than 10 people and ",
					unreliable.length === 1 ? "was" : "were",
					" left out of the ratio. At that size one shortlist decision moves the rate by more than 10 points, so the figure would describe the group size rather than the outcome."
				]
			})
		]
	});
}
function FairnessView({ jobId, jdText, shortlistSize, result, onResult, onRank }) {
	const [running, setRunning] = useState(false);
	const [elapsed, setElapsed] = useState(0);
	const [error, setError] = useState(null);
	const ticker = useRef(void 0);
	useEffect(() => {
		if (!running) return;
		const started = Date.now();
		ticker.current = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1e3)), 1e3);
		return () => {
			if (ticker.current) window.clearInterval(ticker.current);
		};
	}, [running]);
	const run = async () => {
		if (!jobId) return;
		setElapsed(0);
		setRunning(true);
		setError(null);
		const body = {
			job_id: jobId,
			jd_text: jdText,
			shortlist_size: shortlistSize,
			include_excluded: true
		};
		try {
			onResult(await audit(body));
		} catch (err) {
			setError(err);
		} finally {
			setRunning(false);
		}
	};
	if (!jobId || jdText.trim().length < 50) return /* @__PURE__ */ jsx("div", {
		className: "mx-auto max-w-[880px]",
		children: /* @__PURE__ */ jsx(Callout, {
			tone: "muted",
			title: "Rank a shortlist first",
			actions: /* @__PURE__ */ jsxs("button", {
				type: "button",
				className: "btn btn-sm",
				onClick: onRank,
				children: [/* @__PURE__ */ jsx(IconRank, { size: 14 }), "Go to ranking"]
			}),
			children: /* @__PURE__ */ jsx("p", { children: "The audit measures who ends up on a shortlist, so it needs a job description and a batch to run against. Go to the ranking step, then come back." })
		})
	});
	const attributes = result?.attributes ?? [];
	const flagged = attributes.filter((a) => a.flagged);
	return /* @__PURE__ */ jsxs("div", {
		className: "mx-auto max-w-[920px] space-y-5",
		children: [
			/* @__PURE__ */ jsxs(Callout, {
				tone: "muted",
				title: "What this screen measures",
				children: [/* @__PURE__ */ jsx("p", { children: "It compares shortlist rates across groups the ranker never sees. A system can disadvantage a group without ever reading the attribute, because the things it does read correlate with it. That is why “the model does not use this field” is not a fairness claim, and why this screen exists." }), /* @__PURE__ */ jsx("p", {
					className: "mt-2",
					children: "The attributes are proxies recorded alongside the resumes, not fields on anyone’s application and not anything the ranking reads. Each one stands in for something a full audit would measure directly."
				})]
			}),
			!result && !running && /* @__PURE__ */ jsxs("div", {
				className: "card flex flex-col items-center px-6 py-10 text-center",
				children: [
					/* @__PURE__ */ jsx("div", {
						className: "mb-4 flex h-11 w-11 items-center justify-center rounded-xl border border-rule bg-sunk text-accent",
						children: /* @__PURE__ */ jsx(IconFairness, { size: 22 })
					}),
					/* @__PURE__ */ jsx("h3", {
						className: "text-[16px] font-semibold",
						children: "Run the audit on this shortlist"
					}),
					/* @__PURE__ */ jsx("p", {
						className: "mx-auto mt-1.5 max-w-[52ch] text-[13px] leading-[1.6] text-ink2",
						children: "This re-runs the full ranking on the server and then measures the shortlist, so it takes about as long as ranking did."
					}),
					/* @__PURE__ */ jsxs("button", {
						type: "button",
						className: "btn btn-primary btn-lg mt-5",
						onClick: () => void run(),
						children: [/* @__PURE__ */ jsx(IconShield, { size: 16 }), "Run the audit"]
					})
				]
			}),
			running && /* @__PURE__ */ jsxs("div", {
				className: "card overflow-hidden",
				children: [/* @__PURE__ */ jsx(BusyBar, {}), /* @__PURE__ */ jsxs("div", {
					className: "px-4 py-5",
					children: [/* @__PURE__ */ jsxs("p", {
						className: "font-mono text-[26px] leading-none tabular-nums",
						children: [elapsed, "s"]
					}), /* @__PURE__ */ jsx("p", {
						className: "mt-2 text-[12.5px] text-ink2",
						children: "Ranking the batch again and then measuring the shortlist. Expect 15 to 60 seconds."
					})]
				})]
			}),
			/* @__PURE__ */ jsx(ErrorNote, {
				error,
				onRetry: () => void run()
			}),
			result && /* @__PURE__ */ jsxs(Fragment, { children: [
				/* @__PURE__ */ jsxs("div", {
					className: "card grid gap-5 px-4 py-4 sm:grid-cols-3",
					children: [
						/* @__PURE__ */ jsx(Stat, {
							label: "Attributes flagged",
							value: /* @__PURE__ */ jsxs(Fragment, { children: [flagged.length, /* @__PURE__ */ jsxs("span", {
								className: "text-ink3",
								children: [" / ", attributes.length]
							})] }),
							tone: flagged.length > 0 ? "amber" : void 0,
							sub: flagged.length === 0 ? "None below the four-fifths threshold" : flagged.map((a) => a.attribute.replace(/_/g, " ")).join(", ")
						}),
						/* @__PURE__ */ jsx(Stat, {
							label: "Candidates",
							value: result.total_candidates,
							sub: "In the batch"
						}),
						/* @__PURE__ */ jsx(Stat, {
							label: "Shortlist size",
							value: result.shortlist_size,
							sub: "Selected by the ranker"
						})
					]
				}),
				/* @__PURE__ */ jsx("div", {
					className: "space-y-4",
					children: attributes.map((a) => /* @__PURE__ */ jsx(AttributePanel, { attr: a }, a.attribute))
				}),
				/* @__PURE__ */ jsxs("blockquote", {
					className: "rounded-lg border border-rule bg-sunk px-4 py-3.5",
					children: [/* @__PURE__ */ jsx("p", {
						className: "font-quote text-[14.5px] leading-[1.6] text-ink",
						children: result.note
					}), /* @__PURE__ */ jsx("footer", {
						className: "mt-2 text-[11px] text-ink3",
						children: "Methodology note, as recorded by the audit."
					})]
				}),
				/* @__PURE__ */ jsxs("button", {
					type: "button",
					className: "btn",
					onClick: () => void run(),
					children: [/* @__PURE__ */ jsx(IconRefresh, { size: 15 }), "Run the audit again"]
				})
			] })
		]
	});
}
//#endregion
//#region src/components/Evidence.tsx
var SOURCE = {
	database: {
		label: "Confirmed",
		gloss: "Recorded in the extracted skills table",
		chip: "border-accent/35 bg-accent-soft text-accent",
		icon: /* @__PURE__ */ jsx(IconDatabase, { size: 12 })
	},
	retrieval: {
		label: "Read from resume",
		gloss: "A judgement about the resume text, quoted below",
		chip: "border-rule-strong bg-surface text-ink2",
		icon: /* @__PURE__ */ jsx(IconQuote, { size: 12 })
	},
	missing: {
		label: "Not found",
		gloss: "Nothing in this resume matched the requirement",
		chip: "border-dashed border-rule-strong bg-transparent text-ink3",
		icon: /* @__PURE__ */ jsx(IconX, { size: 12 })
	}
};
function Quote({ text }) {
	return /* @__PURE__ */ jsx("blockquote", {
		className: "mt-2 rounded-r-md border-l-2 border-accent/50 bg-accent-soft/35 py-1.5 pr-3 pl-3",
		children: /* @__PURE__ */ jsxs("p", {
			className: "font-quote text-[15px] leading-[1.55] text-ink",
			children: [
				/* @__PURE__ */ jsx("span", {
					"aria-hidden": "true",
					className: "text-ink3",
					children: "“"
				}),
				text,
				/* @__PURE__ */ jsx("span", {
					"aria-hidden": "true",
					className: "text-ink3",
					children: "”"
				})
			]
		})
	});
}
function Row({ item }) {
	const meta = SOURCE[item.source] ?? SOURCE.retrieval;
	const hasQuote = typeof item.quote === "string" && item.quote.trim().length > 0;
	return /* @__PURE__ */ jsxs("li", {
		className: "border-t border-rule py-3 first:border-t-0",
		children: [/* @__PURE__ */ jsxs("div", {
			className: "flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5",
			children: [/* @__PURE__ */ jsx("span", {
				className: "min-w-0 flex-1 text-[13px] leading-[1.5] text-ink",
				children: item.requirement
			}), /* @__PURE__ */ jsxs("div", {
				className: "flex shrink-0 items-center gap-2.5",
				children: [
					/* @__PURE__ */ jsxs("span", {
						className: `inline-flex items-center gap-1 rounded-full border px-2 py-[1px] text-[10.5px] font-medium ${meta.chip}`,
						title: meta.gloss,
						children: [meta.icon, meta.label]
					}),
					/* @__PURE__ */ jsxs("span", {
						className: "font-mono text-[11px] text-ink3",
						title: "Requirement weight",
						children: ["w", item.weight]
					}),
					/* @__PURE__ */ jsx("span", {
						className: "w-[34px] text-right font-mono text-[12.5px] tabular-nums text-ink",
						children: item.score.toFixed(2)
					})
				]
			})]
		}), /* @__PURE__ */ jsxs("div", { children: [
			item.source === "database" && hasQuote && /* @__PURE__ */ jsxs("p", {
				className: "mt-1.5 font-mono text-[11.5px] text-ink2",
				children: [/* @__PURE__ */ jsx("span", {
					className: "text-ink3",
					children: "skills table: "
				}), item.quote]
			}),
			item.source === "retrieval" && hasQuote && /* @__PURE__ */ jsx(Quote, { text: item.quote }),
			item.source === "retrieval" && !hasQuote && /* @__PURE__ */ jsx("p", {
				className: "mt-1.5 text-[12px] text-ink3",
				children: "Matched on the resume text, but no single passage was strong enough to quote."
			}),
			item.source === "missing" && /* @__PURE__ */ jsx("p", {
				className: "mt-1.5 text-[12px] text-ink3",
				children: "No supporting evidence found in this resume."
			})
		] })]
	});
}
function EvidenceList({ evidence }) {
	const items = evidence ?? [];
	if (!items.length) return /* @__PURE__ */ jsx("p", {
		className: "py-3 text-[12.5px] text-ink3",
		children: "No requirements were scored for this candidate."
	});
	return /* @__PURE__ */ jsx("ul", {
		className: "rounded-lg border border-rule bg-surface px-3.5",
		children: items.map((item, i) => /* @__PURE__ */ jsx(Row, { item }, `${item.requirement}-${i}`))
	});
}
//#endregion
//#region src/components/Candidates.tsx
/**
* A ledger of people, not a deck of cards.
*
* Rows are kept tight so roughly twenty candidates are visible at once. The
* recruiter is comparing 45 people in an afternoon; a layout that shows six at
* a time turns one comparison into six screens of scrolling.
*/
var GRID = "grid grid-cols-[28px_minmax(0,1fr)_96px_58px_14px] md:grid-cols-[34px_112px_minmax(0,1fr)_72px_116px_62px_14px] items-center gap-x-3";
function CandidateHeader() {
	return /* @__PURE__ */ jsxs("div", {
		className: `${GRID} border-b border-rule bg-sunk/60 px-4 py-2 text-[11px] font-semibold tracking-[0.04em] text-ink3 uppercase`,
		children: [
			/* @__PURE__ */ jsx("div", {
				className: "text-right",
				children: "#"
			}),
			/* @__PURE__ */ jsx("div", {
				className: "hidden md:block",
				children: "Score"
			}),
			/* @__PURE__ */ jsx("div", { children: "Candidate" }),
			/* @__PURE__ */ jsx("div", {
				className: "hidden text-right md:block",
				children: "Years"
			}),
			/* @__PURE__ */ jsx("div", {
				className: "hidden md:block",
				children: "Degree"
			}),
			/* @__PURE__ */ jsx("div", {
				className: "text-right md:hidden",
				children: "Score"
			}),
			/* @__PURE__ */ jsx("div", {
				className: "text-right",
				children: "Met"
			}),
			/* @__PURE__ */ jsx("div", {})
		]
	});
}
function CandidateRow({ candidate, open, onToggle }) {
	const panelId = `evidence-${candidate.resume_id}`;
	const top = (candidate.rank ?? 99) <= 3;
	return /* @__PURE__ */ jsxs("li", {
		className: "border-b border-rule last:border-b-0",
		children: [/* @__PURE__ */ jsxs("button", {
			type: "button",
			onClick: onToggle,
			"aria-expanded": open,
			"aria-controls": panelId,
			className: `${GRID} w-full px-4 py-2.5 text-left transition-colors hover:bg-sunk/60 ${open ? "bg-sunk/60" : ""}`,
			children: [
				/* @__PURE__ */ jsx("span", {
					className: `text-right font-mono text-[12.5px] tabular-nums ${top ? "font-semibold text-accent" : "text-ink3"}`,
					children: candidate.rank ?? "--"
				}),
				/* @__PURE__ */ jsx("span", {
					className: "hidden md:block",
					children: /* @__PURE__ */ jsx(ScoreMark, { value: candidate.score })
				}),
				/* @__PURE__ */ jsxs("span", {
					className: "min-w-0",
					children: [/* @__PURE__ */ jsx("span", {
						className: "block truncate text-[14px] leading-[1.35] font-medium text-ink",
						children: candidate.name
					}), candidate.headline && /* @__PURE__ */ jsx("span", {
						className: "block truncate text-[11.5px] text-ink3",
						children: candidate.headline
					})]
				}),
				/* @__PURE__ */ jsx("span", {
					className: "hidden text-right font-mono text-[12.5px] text-ink2 md:block",
					children: years(candidate.years)
				}),
				/* @__PURE__ */ jsx("span", {
					className: "hidden truncate text-[12.5px] text-ink2 md:block",
					children: degree(candidate.highest_degree)
				}),
				/* @__PURE__ */ jsx("span", {
					className: "text-right font-mono text-[12.5px] tabular-nums text-ink md:hidden",
					children: candidate.score.toFixed(3)
				}),
				/* @__PURE__ */ jsx("span", {
					className: "text-right",
					children: /* @__PURE__ */ jsx(MetCount, {
						met: candidate.requirements_met,
						total: candidate.requirements_total
					})
				}),
				/* @__PURE__ */ jsx("span", {
					className: "flex justify-end",
					children: /* @__PURE__ */ jsx(Chevron, { open })
				})
			]
		}), /* @__PURE__ */ jsx("div", {
			className: "drawer",
			"data-open": open,
			id: panelId,
			children: /* @__PURE__ */ jsx("div", {
				inert: !open,
				children: /* @__PURE__ */ jsxs("div", {
					className: "border-t border-rule bg-paper px-4 pb-4 pl-10",
					children: [/* @__PURE__ */ jsxs("p", {
						className: "flex flex-wrap items-baseline gap-x-2 pt-3 pb-1 text-[11.5px] text-ink3",
						children: [
							"Why this candidate scored",
							/* @__PURE__ */ jsx("span", {
								className: "font-mono text-ink2",
								children: candidate.score.toFixed(3)
							}),
							", strongest evidence first.",
							/* @__PURE__ */ jsx("span", {
								className: "font-mono",
								children: candidate.resume_id
							})
						]
					}), open && /* @__PURE__ */ jsx(EvidenceList, { evidence: candidate.evidence })]
				})
			})
		})]
	});
}
function CandidateTable({ candidates }) {
	const [openId, setOpenId] = useState(null);
	return /* @__PURE__ */ jsxs("div", { children: [/* @__PURE__ */ jsx(CandidateHeader, {}), /* @__PURE__ */ jsx("ul", { children: candidates.map((c) => /* @__PURE__ */ jsx(CandidateRow, {
		candidate: c,
		open: openId === c.resume_id,
		onToggle: () => setOpenId(openId === c.resume_id ? null : c.resume_id)
	}, c.resume_id)) })] });
}
function ExcludedList({ excluded }) {
	const [open, setOpen] = useState(false);
	const [openId, setOpenId] = useState(null);
	if (!excluded.length) return null;
	return /* @__PURE__ */ jsxs("div", {
		className: "card overflow-hidden",
		children: [/* @__PURE__ */ jsxs("button", {
			type: "button",
			onClick: () => setOpen(!open),
			"aria-expanded": open,
			"aria-controls": "excluded-panel",
			className: "flex w-full items-center gap-2.5 px-4 py-3 text-left transition-colors hover:bg-sunk/60",
			children: [
				/* @__PURE__ */ jsx(Chevron, { open }),
				/* @__PURE__ */ jsxs("span", {
					className: "text-[13.5px] font-semibold",
					children: [
						excluded.length,
						" ",
						excluded.length === 1 ? "candidate was" : "candidates were",
						" removed by the hard requirements"
					]
				}),
				/* @__PURE__ */ jsx("span", {
					className: "ml-auto hidden text-[11.5px] text-ink3 sm:inline",
					children: open ? "Hide" : "Review who was removed and why"
				})
			]
		}), /* @__PURE__ */ jsx("div", {
			className: "drawer",
			"data-open": open,
			id: "excluded-panel",
			children: /* @__PURE__ */ jsx("div", {
				inert: !open,
				children: /* @__PURE__ */ jsxs("div", {
					className: "border-t border-rule bg-paper px-4 pt-3 pb-4",
					children: [/* @__PURE__ */ jsx("p", {
						className: "pb-3 text-[12.5px] text-ink2",
						children: "These people were filtered out before scoring. If someone here should have made it through, the hard requirement that removed them is wrong, not the candidate."
					}), /* @__PURE__ */ jsx("ul", {
						className: "border-t border-rule",
						children: excluded.map((c) => {
							const isOpen = openId === c.resume_id;
							const panelId = `excluded-${c.resume_id}`;
							return /* @__PURE__ */ jsxs("li", {
								className: "border-b border-rule",
								children: [/* @__PURE__ */ jsxs("button", {
									type: "button",
									onClick: () => setOpenId(isOpen ? null : c.resume_id),
									"aria-expanded": isOpen,
									"aria-controls": panelId,
									className: "grid w-full grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-1.5 py-2.5 text-left",
									children: [
										/* @__PURE__ */ jsxs("span", {
											className: "flex min-w-0 flex-wrap items-baseline gap-x-2.5",
											children: [
												/* @__PURE__ */ jsx("span", {
													className: "text-[13.5px] font-medium text-ink",
													children: c.name
												}),
												/* @__PURE__ */ jsx("span", {
													className: "font-mono text-[11px] text-ink3",
													children: years(c.years)
												}),
												/* @__PURE__ */ jsx("span", {
													className: "text-[11px] text-ink3",
													children: degree(c.highest_degree)
												})
											]
										}),
										/* @__PURE__ */ jsxs("span", {
											className: "flex items-center gap-2",
											children: [/* @__PURE__ */ jsx("span", {
												className: "text-[11px] text-ink3",
												children: c.evidence?.length ? "Evidence" : ""
											}), /* @__PURE__ */ jsx(Chevron, { open: isOpen })]
										}),
										/* @__PURE__ */ jsx("span", {
											className: "col-span-2 flex flex-wrap gap-1.5",
											children: c.exclusion_reasons?.length ? c.exclusion_reasons.map((reason, i) => /* @__PURE__ */ jsx("span", {
												className: "rounded-full border border-blood/30 bg-blood-soft px-2 py-[1px] text-[11px] text-blood",
												children: reason
											}, i)) : /* @__PURE__ */ jsx("span", {
												className: "text-[11px] text-ink3",
												children: "No reason recorded."
											})
										})
									]
								}), (c.evidence?.length ?? 0) > 0 && /* @__PURE__ */ jsx("div", {
									className: "drawer",
									"data-open": isOpen,
									id: panelId,
									children: /* @__PURE__ */ jsx("div", {
										inert: !isOpen,
										children: /* @__PURE__ */ jsx("div", {
											className: "pb-3 pl-4",
											children: isOpen && /* @__PURE__ */ jsx(EvidenceList, { evidence: c.evidence })
										})
									})
								})]
							}, c.resume_id);
						})
					})]
				})
			})
		})]
	});
}
//#endregion
//#region src/components/Requirements.tsx
/**
* THE RUBRIC THE SYSTEM INFERRED
*
* This is the screen where a wrong inference gets caught. A hard requirement
* removes people before anyone is scored, so a mis-read line in the job
* description quietly deletes qualified candidates and nothing downstream ever
* shows it. Hard and soft are therefore separated by consequence, not styled as
* two flavours of the same list.
*/
function Constraint({ req }) {
	const parts = [];
	if (req.min_years != null) parts.push(`at least ${req.min_years} years of experience`);
	if (req.degree_level) parts.push(`a ${degree(req.degree_level).toLowerCase()} or higher`);
	if (req.skill) parts.push(`${req.skill} recorded as a skill`);
	if (!parts.length) return null;
	return /* @__PURE__ */ jsxs("span", {
		className: "text-ink3",
		children: [
			" Enforced as ",
			parts.join(", "),
			"."
		]
	});
}
function RequirementRow({ req, hard }) {
	return /* @__PURE__ */ jsxs("li", {
		className: "flex items-start gap-3 border-t border-rule py-2.5 first:border-t-0",
		children: [/* @__PURE__ */ jsxs("span", {
			className: `mt-[2px] shrink-0 rounded border px-1.5 py-[1px] font-mono text-[10.5px] ${hard ? "border-blood/30 bg-blood-soft text-blood" : "border-rule bg-sunk text-ink3"}`,
			title: `Weight ${req.weight}`,
			children: ["w", req.weight]
		}), /* @__PURE__ */ jsxs("span", {
			className: "text-[13px] leading-[1.5]",
			children: [req.text, /* @__PURE__ */ jsx(Constraint, { req })]
		})]
	});
}
/** The filter_spec is the hard requirements collapsed into the query that
*  actually runs against the database. Showing it spelled out means the
*  recruiter checks the filter, not a paraphrase of it. */
function FilterSpecStatement({ spec }) {
	const clauses = [];
	if (spec.min_years != null) clauses.push(`have at least ${spec.min_years} years of experience`);
	if (spec.min_degree) clauses.push(`hold a ${degree(spec.min_degree).toLowerCase()} or higher`);
	if (spec.required_skills?.length) clauses.push(`list ${spec.required_skills.length === 1 ? "the skill" : "every skill"} ${spec.required_skills.join(", ")}`);
	const empty = clauses.length === 0;
	return /* @__PURE__ */ jsxs("div", {
		className: `flex gap-3 rounded-lg border px-4 py-3.5 ${empty ? "border-rule-strong bg-sunk" : "border-blood/30 bg-blood-soft"}`,
		children: [empty ? /* @__PURE__ */ jsx(IconCheck, {
			size: 17,
			className: "mt-[1px] text-ink3"
		}) : /* @__PURE__ */ jsx(IconAlert, {
			size: 17,
			className: "mt-[1px] text-blood"
		}), /* @__PURE__ */ jsxs("div", {
			className: "min-w-0",
			children: [/* @__PURE__ */ jsx("h4", {
				className: `text-[13.5px] font-semibold ${empty ? "text-ink" : "text-blood"}`,
				children: empty ? "No filter will run" : "The filter that will run"
			}), !empty ? /* @__PURE__ */ jsxs(Fragment, { children: [/* @__PURE__ */ jsxs("p", {
				className: "mt-1 text-[13px] leading-[1.55] text-ink",
				children: [
					"A candidate is removed before scoring unless they",
					" ",
					clauses.map((c, i) => /* @__PURE__ */ jsxs("span", { children: [i > 0 && (i === clauses.length - 1 ? ", and " : ", "), /* @__PURE__ */ jsx("span", {
						className: "font-semibold",
						children: c
					})] }, i)),
					"."
				]
			}), /* @__PURE__ */ jsx("p", {
				className: "mt-2 text-[12px] text-ink2",
				children: "Anyone this removes is still listed, with the reason. If a line here is wrong, edit the job description and run it again."
			})] }) : /* @__PURE__ */ jsx("p", {
				className: "mt-1 text-[13px] leading-[1.55] text-ink",
				children: "No hard requirement was inferred, so nobody is removed before scoring. Every candidate in the batch is scored and ranked. If the job description does have a genuine must-have, say it plainly and run it again."
			})]
		})]
	});
}
function RequirementsReview({ requirements, filterSpec, title }) {
	const hard = requirements.filter((r) => r.kind === "hard");
	const soft = requirements.filter((r) => r.kind === "soft");
	return /* @__PURE__ */ jsxs("div", {
		className: "space-y-4",
		children: [
			title && /* @__PURE__ */ jsxs("p", {
				className: "text-[12.5px] text-ink2",
				children: [
					"Read as a job description for",
					" ",
					/* @__PURE__ */ jsx("span", {
						className: "text-[14px] font-semibold text-ink",
						children: title
					}),
					"."
				]
			}),
			/* @__PURE__ */ jsxs("div", {
				className: "grid items-start gap-4 lg:grid-cols-2",
				children: [/* @__PURE__ */ jsxs("div", {
					className: "card overflow-hidden",
					children: [
						/* @__PURE__ */ jsxs("div", {
							className: "card-head",
							children: [/* @__PURE__ */ jsxs("h3", {
								className: "flex items-center gap-2 text-[13.5px] font-semibold",
								children: [/* @__PURE__ */ jsx(IconAlert, {
									size: 15,
									className: "text-blood"
								}), "Hard requirements"]
							}), /* @__PURE__ */ jsx("span", {
								className: "font-mono text-[12px] text-ink3",
								children: hard.length
							})]
						}),
						hard.length > 0 ? /* @__PURE__ */ jsx("p", {
							className: "border-b border-rule bg-blood-soft/70 px-4 py-2 text-[11.5px] text-blood",
							children: "These eliminate candidates. Nobody who fails one is scored."
						}) : /* @__PURE__ */ jsx("p", {
							className: "border-b border-rule bg-sunk px-4 py-2 text-[11.5px] text-ink2",
							children: "Nothing here, so nobody is eliminated before scoring."
						}),
						/* @__PURE__ */ jsx("div", {
							className: "px-4 py-1",
							children: hard.length ? /* @__PURE__ */ jsx("ul", { children: hard.map((r, i) => /* @__PURE__ */ jsx(RequirementRow, {
								req: r,
								hard: true
							}, i)) }) : /* @__PURE__ */ jsx("p", {
								className: "py-4 text-[12.5px] text-ink3",
								children: "None were inferred. Every candidate will be scored."
							})
						})
					]
				}), /* @__PURE__ */ jsxs("div", {
					className: "card overflow-hidden",
					children: [
						/* @__PURE__ */ jsxs("div", {
							className: "card-head",
							children: [/* @__PURE__ */ jsxs("h3", {
								className: "flex items-center gap-2 text-[13.5px] font-semibold",
								children: [/* @__PURE__ */ jsx(IconSort, {
									size: 15,
									className: "text-accent"
								}), "Soft requirements"]
							}), /* @__PURE__ */ jsx("span", {
								className: "font-mono text-[12px] text-ink3",
								children: soft.length
							})]
						}),
						/* @__PURE__ */ jsx("p", {
							className: "border-b border-rule bg-sunk px-4 py-2 text-[11.5px] text-ink2",
							children: "These move a candidate up or down the ranking. They never remove anyone."
						}),
						/* @__PURE__ */ jsx("div", {
							className: "px-4 py-1",
							children: soft.length ? /* @__PURE__ */ jsx("ul", { children: soft.map((r, i) => /* @__PURE__ */ jsx(RequirementRow, {
								req: r,
								hard: false
							}, i)) }) : /* @__PURE__ */ jsx("p", {
								className: "py-4 text-[12.5px] text-ink3",
								children: "None were inferred."
							})
						})
					]
				})]
			}),
			/* @__PURE__ */ jsx(FilterSpecStatement, { spec: filterSpec })
		]
	});
}
//#endregion
//#region src/views/RankView.tsx
var STEP_LABELS = [
	[
		"jd",
		"Job description",
		"Paste the posting"
	],
	[
		"review",
		"Requirements",
		"Check the rubric"
	],
	[
		"results",
		"Ranking",
		"Read the evidence"
	]
];
function Stepper({ step, onGo }) {
	const index = STEP_LABELS.findIndex(([s]) => s === step);
	return /* @__PURE__ */ jsx("ol", {
		className: "card mb-6 flex flex-wrap items-stretch overflow-hidden p-0",
		children: STEP_LABELS.map(([s, label, hint], i) => {
			const done = i < index;
			const current = i === index;
			return /* @__PURE__ */ jsx("li", {
				className: "min-w-[180px] flex-1 border-r border-rule last:border-r-0",
				children: /* @__PURE__ */ jsxs("button", {
					type: "button",
					disabled: !done,
					onClick: () => onGo(s),
					"aria-current": current ? "step" : void 0,
					className: `flex w-full items-center gap-3 px-4 py-3 text-left transition-colors ${current ? "bg-accent-soft/60" : done ? "hover:bg-sunk cursor-pointer" : ""}`,
					children: [/* @__PURE__ */ jsx("span", {
						"aria-hidden": "true",
						className: `flex h-6 w-6 shrink-0 items-center justify-center rounded-full border font-mono text-[11.5px] ${current ? "border-accent bg-accent text-accent-ink" : done ? "border-moss/40 bg-moss-soft text-moss" : "border-rule-strong bg-surface text-ink3"}`,
						children: done ? /* @__PURE__ */ jsx(IconCheck, { size: 12 }) : i + 1
					}), /* @__PURE__ */ jsxs("span", {
						className: "min-w-0",
						children: [/* @__PURE__ */ jsx("span", {
							className: `block truncate text-[13.5px] ${current ? "font-semibold text-ink" : done ? "text-ink2" : "text-ink3"}`,
							children: label
						}), /* @__PURE__ */ jsx("span", {
							className: "block truncate text-[11.5px] text-ink3",
							children: hint
						})]
					})]
				})
			}, s);
		})
	});
}
var RANK_STAGES = [
	{
		label: "Reading the job description",
		hint: "A language model turns it into requirements."
	},
	{
		label: "Applying the hard requirements",
		hint: "Candidates who fail one are set aside."
	},
	{
		label: "Scoring the rest against each requirement",
		hint: "The long stage. Searches and re-reads every resume."
	}
];
function RankingProgress({ elapsed, count }) {
	const likely = elapsed < 4 ? 0 : elapsed < 9 ? 1 : 2;
	return /* @__PURE__ */ jsxs("div", {
		className: "card overflow-hidden",
		children: [/* @__PURE__ */ jsx(BusyBar, {}), /* @__PURE__ */ jsxs("div", {
			className: "space-y-4 px-4 py-4",
			children: [
				/* @__PURE__ */ jsxs("div", {
					className: "flex items-baseline gap-3",
					children: [/* @__PURE__ */ jsxs("span", {
						className: "font-mono text-[26px] leading-none tabular-nums text-ink",
						children: [elapsed, "s"]
					}), /* @__PURE__ */ jsxs("span", {
						className: "text-[12.5px] text-ink2",
						children: [
							"Ranking ",
							count != null ? `${count} candidates` : "the batch",
							". This usually takes between 15 and 60 seconds."
						]
					})]
				}),
				/* @__PURE__ */ jsx("ol", {
					className: "space-y-2",
					children: RANK_STAGES.map((stage, i) => /* @__PURE__ */ jsxs("li", {
						className: "flex items-start gap-2.5",
						children: [/* @__PURE__ */ jsx("span", {
							"aria-hidden": "true",
							className: `mt-[6px] h-[7px] w-[7px] shrink-0 rounded-full border ${i < likely ? "border-moss bg-moss" : i === likely ? "border-accent bg-accent" : "border-rule-strong bg-surface"}`,
							style: i === likely ? { animation: "pulse-dot 1.4s ease-in-out infinite" } : void 0
						}), /* @__PURE__ */ jsxs("span", {
							className: "min-w-0",
							children: [/* @__PURE__ */ jsx("span", {
								className: `text-[13px] ${i === likely ? "font-semibold text-ink" : "text-ink2"}`,
								children: stage.label
							}), /* @__PURE__ */ jsx("span", {
								className: "ml-1.5 text-[11.5px] text-ink3",
								children: stage.hint
							})]
						})]
					}, stage.label))
				}),
				/* @__PURE__ */ jsx("p", {
					className: "border-t border-rule pt-3 text-[11.5px] text-ink3",
					children: "The server runs this as one request and does not report its position, so the highlighted stage is an estimate from elapsed time. The count of seconds is measured."
				})
			]
		})]
	});
}
function Summary({ result, onReview }) {
	const { passed_filter, total_candidates, shortlist, seconds } = result;
	const share = total_candidates > 0 ? passed_filter / total_candidates : 0;
	const thin = total_candidates > 0 && share < .25;
	return /* @__PURE__ */ jsxs("div", {
		className: "space-y-3",
		children: [/* @__PURE__ */ jsx("div", {
			className: "card px-4 py-4",
			children: /* @__PURE__ */ jsxs("div", {
				className: "grid gap-5 sm:grid-cols-[minmax(0,1.6fr)_repeat(2,minmax(0,1fr))]",
				children: [
					/* @__PURE__ */ jsxs("div", { children: [/* @__PURE__ */ jsx(Stat, {
						label: "Passed the hard requirements",
						value: /* @__PURE__ */ jsxs(Fragment, { children: [passed_filter, /* @__PURE__ */ jsxs("span", {
							className: "text-ink3",
							children: [" / ", total_candidates]
						})] }),
						tone: thin ? "amber" : void 0
					}), /* @__PURE__ */ jsx("div", {
						className: "mt-3 h-[6px] w-full overflow-hidden rounded-full bg-sunk",
						role: "img",
						"aria-label": `${passed_filter} of ${total_candidates} candidates passed the filter`,
						children: /* @__PURE__ */ jsx("div", {
							className: "h-full rounded-full",
							style: {
								width: `${share * 100}%`,
								background: thin ? "var(--c-amber)" : "linear-gradient(90deg, var(--c-accent), var(--c-accent-bright))"
							}
						})
					})] }),
					/* @__PURE__ */ jsx(Stat, {
						label: "Shortlisted",
						value: shortlist?.length ?? 0,
						sub: "Ranked into the list"
					}),
					/* @__PURE__ */ jsx(Stat, {
						label: "Ranked in",
						value: `${seconds}s`,
						sub: "Server time for this request"
					})
				]
			})
		}), thin && /* @__PURE__ */ jsx(Callout, {
			tone: "warn",
			title: "Very few candidates got through the filter",
			actions: /* @__PURE__ */ jsx("button", {
				type: "button",
				className: "btn btn-sm",
				onClick: onReview,
				children: "Review the requirements"
			}),
			children: /* @__PURE__ */ jsxs("p", { children: [
				passed_filter,
				" of ",
				total_candidates,
				" passed. When the filter removes this many people the usual cause is a hard requirement that was inferred too strictly, not a weak applicant pool. Check the rubric before you work from this shortlist."
			] })
		})]
	});
}
/** passed_filter can legitimately be 0. That is a real result, not an error,
*  and it points at the requirements. */
function NobodyPassed({ result, onReview }) {
	return /* @__PURE__ */ jsxs("div", {
		className: "rounded-lg border border-blood/30 bg-blood-soft px-5 py-6",
		children: [
			/* @__PURE__ */ jsx("h3", {
				className: "text-[18px] font-semibold text-ink",
				children: "No candidate met every hard requirement"
			}),
			/* @__PURE__ */ jsxs("p", {
				className: "mt-2 max-w-[62ch] text-[13px] leading-[1.6] text-ink2",
				children: [
					"All ",
					result.total_candidates,
					" candidates in this batch failed at least one hard requirement, so there is nothing to rank. This is almost always the rubric rather than the applicants: a minimum years figure read too high, or a skill marked as required that the job description only preferred."
				]
			}),
			/* @__PURE__ */ jsx("button", {
				type: "button",
				className: "btn btn-primary mt-4",
				onClick: onReview,
				children: "Review the requirements"
			}),
			/* @__PURE__ */ jsx("p", {
				className: "mt-3 text-[12px] text-ink2",
				children: "Everyone who was removed is listed below with the reason, so you can see which requirement did it."
			})
		]
	});
}
function RankView({ jobId, jdText, onJdText, shortlistSize, onShortlistSize, result, onResult, onAudit, onPickBatch }) {
	const [step, setStep] = useState(result ? "results" : "jd");
	const [running, setRunning] = useState(false);
	const [elapsed, setElapsed] = useState(0);
	const [error, setError] = useState(null);
	const ticker = useRef(void 0);
	useEffect(() => {
		if (!running) return;
		const started = Date.now();
		ticker.current = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1e3)), 1e3);
		return () => {
			if (ticker.current) window.clearInterval(ticker.current);
		};
	}, [running]);
	const tooShort = jdText.trim().length < 50;
	const run = async () => {
		if (!jobId || tooShort) return;
		setElapsed(0);
		setRunning(true);
		setError(null);
		const body = {
			job_id: jobId,
			jd_text: jdText,
			shortlist_size: shortlistSize,
			include_excluded: true
		};
		try {
			onResult(await rank(body));
			setStep("review");
		} catch (err) {
			setError(err);
		} finally {
			setRunning(false);
		}
	};
	if (!jobId) return /* @__PURE__ */ jsx("div", {
		className: "mx-auto max-w-[880px]",
		children: /* @__PURE__ */ jsx(Callout, {
			tone: "muted",
			title: "Pick a batch first",
			actions: /* @__PURE__ */ jsxs("button", {
				type: "button",
				className: "btn btn-sm",
				onClick: onPickBatch,
				children: [/* @__PURE__ */ jsx(IconBatches, { size: 14 }), "Go to batches"]
			}),
			children: /* @__PURE__ */ jsx("p", { children: "Ranking runs against one uploaded batch of resumes. Upload a zip, or open an existing batch, and come back." })
		})
	});
	const filterSpec = result?.filter_spec ?? {};
	return /* @__PURE__ */ jsxs("div", {
		className: "mx-auto max-w-[1080px]",
		children: [
			/* @__PURE__ */ jsx(Stepper, {
				step,
				onGo: setStep
			}),
			step === "jd" && /* @__PURE__ */ jsxs("div", {
				className: "max-w-[780px] space-y-4",
				children: [
					/* @__PURE__ */ jsxs("div", {
						className: "card p-5",
						children: [
							/* @__PURE__ */ jsx("label", {
								htmlFor: "jd",
								className: "label",
								children: "Paste the job description"
							}),
							/* @__PURE__ */ jsx("textarea", {
								id: "jd",
								className: "field font-quote text-[14.5px] leading-[1.6]",
								rows: 16,
								placeholder: "Paste the full posting, including the responsibilities and the requirements.",
								value: jdText,
								onChange: (e) => onJdText(e.target.value)
							}),
							/* @__PURE__ */ jsxs("div", {
								className: "mt-2 flex flex-wrap items-baseline justify-between gap-3 text-[11.5px] text-ink3",
								children: [/* @__PURE__ */ jsx("span", {
									className: "max-w-[54ch]",
									children: "The system reads this into a list of requirements. You review that list before anyone is ranked."
								}), /* @__PURE__ */ jsxs("span", {
									className: `font-mono ${tooShort ? "text-blood" : "text-moss"}`,
									children: [
										jdText.trim().length,
										" / ",
										50,
										" min"
									]
								})]
							}),
							/* @__PURE__ */ jsxs("div", {
								className: "mt-5 border-t border-rule pt-4",
								children: [
									/* @__PURE__ */ jsx("label", {
										htmlFor: "size",
										className: "label",
										children: "Shortlist size"
									}),
									/* @__PURE__ */ jsx("input", {
										id: "size",
										type: "number",
										min: 1,
										max: 500,
										className: "field w-28 font-mono",
										value: shortlistSize,
										onChange: (e) => onShortlistSize(Number(e.target.value) || 1)
									}),
									/* @__PURE__ */ jsx("p", {
										className: "hint",
										children: "How many candidates to rank into the list."
									})
								]
							})
						]
					}),
					/* @__PURE__ */ jsx(ErrorNote, {
						error,
						onRetry: () => void run()
					}),
					/* @__PURE__ */ jsxs("div", {
						className: "flex flex-wrap items-center gap-3",
						children: [/* @__PURE__ */ jsxs("button", {
							type: "button",
							className: "btn btn-primary btn-lg",
							disabled: tooShort || running,
							onClick: () => void run(),
							children: ["Read the job description", /* @__PURE__ */ jsx(IconArrowRight, { size: 15 })]
						}), /* @__PURE__ */ jsx("span", {
							className: "text-[12px] text-ink3",
							children: tooShort ? `Paste at least 50 characters.` : "You will see the requirements before you see any candidate."
						})]
					}),
					running && /* @__PURE__ */ jsx(RankingProgress, {
						elapsed,
						count: null
					})
				]
			}),
			step === "review" && result && /* @__PURE__ */ jsxs("div", {
				className: "space-y-5",
				children: [
					/* @__PURE__ */ jsxs("div", { children: [/* @__PURE__ */ jsx("h2", {
						className: "text-[16px] font-semibold",
						children: "Check what the system decided you asked for"
					}), /* @__PURE__ */ jsx("p", {
						className: "mt-1 max-w-[78ch] text-[13px] leading-[1.6] text-ink2",
						children: "This is the rubric it inferred from your job description. A hard requirement that was read wrongly removes qualified people, and this is where that gets caught. Nothing is shown about any individual until you confirm."
					})] }),
					/* @__PURE__ */ jsx(RequirementsReview, {
						requirements: result.requirements,
						filterSpec,
						title: result.title
					}),
					/* @__PURE__ */ jsxs("div", {
						className: "flex flex-wrap items-center gap-3 border-t border-rule pt-4",
						children: [
							/* @__PURE__ */ jsxs("button", {
								type: "button",
								className: "btn btn-primary",
								onClick: () => setStep("results"),
								children: [result.passed_filter > 0 ? `Show the ${result.shortlist?.length ?? 0} ranked candidates` : "Show what the filter did", /* @__PURE__ */ jsx(IconArrowRight, { size: 15 })]
							}),
							/* @__PURE__ */ jsx("button", {
								type: "button",
								className: "btn",
								onClick: () => setStep("jd"),
								children: "Edit the job description"
							}),
							/* @__PURE__ */ jsxs("span", {
								className: "text-[12px] text-ink3",
								children: [
									result.passed_filter,
									" of ",
									result.total_candidates,
									" candidates passed this filter."
								]
							})
						]
					})
				]
			}),
			step === "results" && result && /* @__PURE__ */ jsxs("div", {
				className: "space-y-5",
				children: [
					/* @__PURE__ */ jsxs("div", {
						className: "flex flex-wrap items-center justify-between gap-3",
						children: [/* @__PURE__ */ jsxs("div", {
							className: "flex min-w-0 items-center gap-3",
							children: [/* @__PURE__ */ jsx("h2", {
								className: "truncate text-[19px] font-semibold",
								children: result.title || "Ranked candidates"
							}), /* @__PURE__ */ jsxs(Chip, {
								tone: "muted",
								children: [result.shortlist?.length ?? 0, " shortlisted"]
							})]
						}), /* @__PURE__ */ jsxs("div", {
							className: "flex gap-2",
							children: [/* @__PURE__ */ jsx("button", {
								type: "button",
								className: "btn btn-sm",
								onClick: () => setStep("review"),
								children: "Requirements"
							}), /* @__PURE__ */ jsxs("button", {
								type: "button",
								className: "btn btn-sm",
								onClick: onAudit,
								children: [/* @__PURE__ */ jsx(IconFairness, { size: 14 }), "Check for bias"]
							})]
						})]
					}),
					/* @__PURE__ */ jsx(Summary, {
						result,
						onReview: () => setStep("review")
					}),
					result.passed_filter === 0 ? /* @__PURE__ */ jsx(NobodyPassed, {
						result,
						onReview: () => setStep("review")
					}) : /* @__PURE__ */ jsxs("div", {
						className: "card overflow-hidden",
						children: [/* @__PURE__ */ jsxs("div", {
							className: "card-head",
							children: [/* @__PURE__ */ jsx("p", {
								className: "text-[12.5px] text-ink2",
								children: "Ranked by how well the evidence matches the requirements. Open a row to read the evidence behind the score."
							}), /* @__PURE__ */ jsx("span", {
								className: "hidden shrink-0 text-[11.5px] text-ink3 lg:inline",
								children: "The tool ranks and cites. The decision is yours."
							})]
						}), /* @__PURE__ */ jsx(CandidateTable, { candidates: result.shortlist ?? [] })]
					}),
					/* @__PURE__ */ jsx(ExcludedList, { excluded: result.excluded ?? [] })
				]
			})
		]
	});
}
//#endregion
//#region src/components/FileReport.tsx
/**
* WHAT ARRIVED, AND WHAT DID NOT
*
* Grouped by outcome rather than listed by filename, because the only question
* worth answering here is "whose resume failed to make it in". A scanned PDF
* that yielded no text means a real applicant cannot be ranked at all, and that
* has to be visible and explained rather than buried as a status code in a list
* of fifty rows.
*/
function Group({ status, rows, total, defaultOpen }) {
	const [open, setOpen] = useState(defaultOpen);
	const meta = fileStatusMeta(status);
	const panelId = `files-${status}`;
	const bar = meta.tone === "bad" ? "bg-blood" : meta.tone === "warn" ? "bg-amber" : meta.tone === "ok" ? "bg-moss" : "bg-rule-strong";
	const share = total > 0 ? rows.length / total * 100 : 0;
	return /* @__PURE__ */ jsxs("div", {
		className: "border-b border-rule last:border-b-0",
		children: [/* @__PURE__ */ jsxs("button", {
			type: "button",
			onClick: () => setOpen(!open),
			"aria-expanded": open,
			"aria-controls": panelId,
			className: "flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-sunk/60",
			children: [
				/* @__PURE__ */ jsx(Chevron, { open }),
				/* @__PURE__ */ jsx("span", {
					"aria-hidden": "true",
					className: `h-3.5 w-[3px] shrink-0 rounded-full ${bar}`
				}),
				/* @__PURE__ */ jsx("span", {
					className: "text-[13px] font-medium",
					children: meta.label
				}),
				/* @__PURE__ */ jsx("span", {
					className: "font-mono text-[12px] text-ink3",
					children: rows.length
				}),
				/* @__PURE__ */ jsx("span", {
					"aria-hidden": "true",
					className: "ml-auto hidden h-[5px] w-24 overflow-hidden rounded-full bg-sunk sm:block",
					children: /* @__PURE__ */ jsx("span", {
						className: `block h-full rounded-full ${bar}`,
						style: { width: `${share}%` }
					})
				}),
				!isRankable(status) && /* @__PURE__ */ jsx("span", {
					className: "shrink-0 text-[11.5px] text-ink3",
					children: "not ranked"
				})
			]
		}), /* @__PURE__ */ jsx("div", {
			className: "drawer",
			"data-open": open,
			id: panelId,
			children: /* @__PURE__ */ jsx("div", {
				inert: !open,
				children: /* @__PURE__ */ jsxs("div", {
					className: "bg-paper px-4 pb-3 pl-[42px]",
					children: [/* @__PURE__ */ jsx("p", {
						className: "py-2.5 text-[12.5px] leading-[1.55] text-ink2",
						children: meta.note
					}), /* @__PURE__ */ jsx("ul", {
						className: "border-t border-rule",
						children: rows.map((row, i) => /* @__PURE__ */ jsxs("li", {
							className: "flex items-baseline justify-between gap-3 border-b border-rule py-1.5 last:border-b-0",
							children: [/* @__PURE__ */ jsxs("span", {
								className: "flex min-w-0 items-baseline gap-2",
								children: [/* @__PURE__ */ jsx(IconFile, {
									size: 12,
									className: "shrink-0 translate-y-[1px] text-ink3"
								}), /* @__PURE__ */ jsx("span", {
									className: "truncate font-mono text-[11.5px] text-ink",
									children: row.name
								})]
							}), /* @__PURE__ */ jsx("span", {
								className: "shrink-0 text-[11px] text-ink3",
								children: row.detail ? row.detail : row.n_chunks > 0 ? `${row.n_chunks} passages indexed` : ""
							})]
						}, `${row.name}-${i}`))
					})]
				})
			})
		})]
	});
}
function FileReportPanel({ files }) {
	if (!files?.length) return null;
	const groups = /* @__PURE__ */ new Map();
	for (const f of files) {
		const list = groups.get(f.status) ?? [];
		list.push(f);
		groups.set(f.status, list);
	}
	const order = [...groups.entries()].sort((a, b) => {
		const rank = (s) => isRankable(s) ? 2 : fileStatusMeta(s).tone === "muted" ? 1 : 0;
		return rank(a[0]) - rank(b[0]);
	});
	const unusable = files.filter((f) => !isRankable(f.status)).length;
	return /* @__PURE__ */ jsxs("div", {
		className: "card overflow-hidden",
		children: [/* @__PURE__ */ jsxs("div", {
			className: "card-head",
			children: [/* @__PURE__ */ jsx("h3", {
				className: "text-[13.5px] font-semibold",
				children: "Files in this batch"
			}), /* @__PURE__ */ jsx("span", {
				className: `text-[11.5px] ${unusable > 0 ? "text-amber" : "text-ink3"}`,
				children: unusable > 0 ? `${files.length - unusable} of ${files.length} can be ranked` : `all ${files.length} can be ranked`
			})]
		}), order.map(([status, rows]) => /* @__PURE__ */ jsx(Group, {
			status,
			rows,
			total: files.length,
			defaultOpen: !isRankable(status) && fileStatusMeta(status).tone === "bad"
		}, status))]
	});
}
//#endregion
//#region src/views/UploadView.tsx
function StageTrack({ job }) {
	const failed = job.state === "failed";
	const complete = job.state === "ready";
	const current = STAGES.indexOf(job.state);
	return /* @__PURE__ */ jsx("ol", {
		className: "flex flex-wrap items-center gap-x-1 gap-y-2",
		children: STAGES.map((stage, i) => {
			const done = !failed && (complete || current > i);
			const active = !failed && !complete && current === i;
			return /* @__PURE__ */ jsxs("li", {
				className: "flex items-center gap-1",
				children: [/* @__PURE__ */ jsxs("span", {
					className: `flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] ${active ? "border-accent bg-accent-soft font-semibold text-accent" : done ? "border-rule bg-surface text-ink2" : "border-dashed border-rule bg-transparent text-ink3"}`,
					children: [done ? /* @__PURE__ */ jsx(IconCheck, {
						size: 12,
						className: "text-moss"
					}) : /* @__PURE__ */ jsx("span", {
						"aria-hidden": "true",
						className: `h-[6px] w-[6px] rounded-full ${active ? "bg-accent" : "bg-rule-strong"}`,
						style: active ? { animation: "pulse-dot 1.4s ease-in-out infinite" } : void 0
					}), JOB_STATE_LABEL[stage]]
				}), i < STAGES.length - 1 && /* @__PURE__ */ jsx("span", {
					"aria-hidden": "true",
					className: `h-px w-3 ${done ? "bg-rule-strong" : "bg-rule"}`
				})]
			}, stage);
		})
	});
}
function Counts({ job }) {
	const items = [
		["Files", job.n_files],
		["Parsed", job.n_parsed],
		["Passages", job.n_chunks],
		["Candidates", job.n_candidates]
	];
	return /* @__PURE__ */ jsx("div", {
		className: "grid grid-cols-2 gap-4 border-t border-rule pt-4 sm:grid-cols-4",
		children: items.map(([label, value]) => /* @__PURE__ */ jsx(Stat, {
			label,
			value
		}, label))
	});
}
function Progress({ job, polling, onRank }) {
	const failed = job.state === "failed";
	const ready = job.state === "ready";
	return /* @__PURE__ */ jsxs("div", {
		className: "space-y-5",
		children: [
			/* @__PURE__ */ jsxs("div", {
				className: "card overflow-hidden",
				children: [
					/* @__PURE__ */ jsxs("div", {
						className: "card-head",
						children: [/* @__PURE__ */ jsx("span", {
							className: "truncate font-mono text-[12px] text-ink2",
							children: job.job_id
						}), /* @__PURE__ */ jsxs("div", {
							className: "flex shrink-0 items-center gap-3",
							children: [/* @__PURE__ */ jsxs("span", {
								className: "hidden text-[11.5px] text-ink3 sm:inline",
								children: ["Started ", when(job.created_at)]
							}), /* @__PURE__ */ jsx(Chip, {
								tone: failed ? "bad" : ready ? "ok" : "muted",
								dot: true,
								pulse: !failed && !ready,
								children: JOB_STATE_LABEL[job.state]
							})]
						})]
					}),
					polling && /* @__PURE__ */ jsx(BusyBar, {}),
					/* @__PURE__ */ jsxs("div", {
						className: "space-y-4 px-4 py-4",
						children: [
							/* @__PURE__ */ jsx(StageTrack, { job }),
							/* @__PURE__ */ jsxs("p", {
								className: "font-quote text-[17px] leading-snug text-ink",
								children: [job.stage_message || JOB_STATE_LABEL[job.state], polling && /* @__PURE__ */ jsx("span", {
									className: "ml-1 text-ink3",
									children: "..."
								})]
							}),
							!failed && !ready && /* @__PURE__ */ jsxs("div", { children: [/* @__PURE__ */ jsxs("div", {
								className: "flex items-baseline justify-between gap-3",
								children: [/* @__PURE__ */ jsx("div", {
									className: "h-[6px] flex-1 overflow-hidden rounded-full bg-sunk",
									children: /* @__PURE__ */ jsx("div", {
										className: "h-full rounded-full",
										style: {
											width: `${Math.round(job.progress * 100)}%`,
											background: "linear-gradient(90deg, var(--c-accent), var(--c-accent-bright))",
											transition: "width 400ms linear"
										}
									})
								}), /* @__PURE__ */ jsxs("span", {
									className: "font-mono text-[12.5px] tabular-nums text-ink2",
									children: [Math.round(job.progress * 100), "%"]
								})]
							}), /* @__PURE__ */ jsx("p", {
								className: "mt-1.5 text-[11.5px] text-ink3",
								children: "Extraction is the long stage and holds here for minutes, so watch the stage above rather than the bar."
							})] }),
							/* @__PURE__ */ jsx(Counts, { job })
						]
					})
				]
			}),
			failed && /* @__PURE__ */ jsxs(Callout, {
				tone: "bad",
				title: "This batch failed",
				children: [/* @__PURE__ */ jsx("p", {
					className: "font-mono text-[12px]",
					children: job.error || "No error detail was recorded."
				}), /* @__PURE__ */ jsx("p", {
					className: "mt-2",
					children: "Nothing from this upload was indexed. Check the zip opens on your machine and contains .pdf or .docx files at the top level, then upload it again. If the message mentions an API key or a provider, the server needs configuring before any batch will process."
				})]
			}),
			ready && /* @__PURE__ */ jsxs("div", {
				className: "flex flex-wrap items-center gap-3 rounded-lg border border-moss/30 bg-moss-soft px-4 py-3",
				children: [
					/* @__PURE__ */ jsx(IconCheck, {
						size: 18,
						className: "text-moss"
					}),
					/* @__PURE__ */ jsxs("span", {
						className: "text-[13px] text-ink",
						children: [/* @__PURE__ */ jsx("span", {
							className: "font-mono",
							children: job.n_candidates
						}), " candidates are indexed and ready to rank."]
					}),
					/* @__PURE__ */ jsxs("button", {
						type: "button",
						className: "btn btn-primary ml-auto",
						onClick: onRank,
						children: ["Rank these candidates", /* @__PURE__ */ jsx(IconArrowRight, { size: 15 })]
					})
				]
			}),
			ready && /* @__PURE__ */ jsx(FileReportPanel, { files: job.files })
		]
	});
}
function DropZone({ file, onFile, disabled }) {
	const [over, setOver] = useState(false);
	const [reject, setReject] = useState(null);
	const input = useRef(null);
	const accept = (f) => {
		if (!f) return;
		if (!f.name.toLowerCase().endsWith(".zip")) {
			setReject(`${f.name} is not a .zip. Put the resumes in a zip archive and try again.`);
			return;
		}
		setReject(null);
		onFile(f);
	};
	return /* @__PURE__ */ jsxs("div", { children: [/* @__PURE__ */ jsxs("div", {
		onDragOver: (e) => {
			e.preventDefault();
			if (!disabled) setOver(true);
		},
		onDragLeave: () => setOver(false),
		onDrop: (e) => {
			e.preventDefault();
			setOver(false);
			if (!disabled) accept(e.dataTransfer.files?.[0]);
		},
		className: `rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${over ? "border-accent bg-accent-soft" : "border-rule-strong bg-surface"}`,
		children: [
			/* @__PURE__ */ jsx("div", {
				className: "mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-rule bg-sunk text-accent",
				children: /* @__PURE__ */ jsx(IconArchive, { size: 21 })
			}),
			/* @__PURE__ */ jsx("p", {
				className: "text-[16px] font-semibold text-ink",
				children: "Drop a zip of resumes here"
			}),
			/* @__PURE__ */ jsx("p", {
				className: "mt-1 text-[12.5px] text-ink2",
				children: "One archive of .pdf and .docx files, up to 200MB."
			}),
			/* @__PURE__ */ jsx("button", {
				type: "button",
				className: "btn mt-4",
				disabled,
				onClick: () => input.current?.click(),
				children: "Choose a file"
			}),
			/* @__PURE__ */ jsx("input", {
				ref: input,
				type: "file",
				accept: ".zip,application/zip",
				className: "sr-only",
				onChange: (e) => accept(e.target.files?.[0])
			}),
			file && /* @__PURE__ */ jsxs("p", {
				className: "mx-auto mt-4 flex w-fit items-center gap-2 rounded-md border border-rule bg-sunk px-3 py-1.5 font-mono text-[12px] text-ink",
				children: [
					/* @__PURE__ */ jsx(IconArchive, {
						size: 14,
						className: "text-ink3"
					}),
					file.name,
					/* @__PURE__ */ jsxs("span", {
						className: "text-ink3",
						children: [(file.size / 1e6).toFixed(1), "MB"]
					})
				]
			})
		]
	}), reject && /* @__PURE__ */ jsx("div", {
		className: "mt-3",
		children: /* @__PURE__ */ jsx(Callout, {
			tone: "bad",
			children: reject
		})
	})] });
}
function UploadView({ company, onCompany, activeJobId, job, pollError, polling, onJobStarted, onRank }) {
	const [file, setFile] = useState(null);
	const [sending, setSending] = useState(false);
	const [error, setError] = useState(null);
	const submit = async () => {
		if (!file) return;
		setSending(true);
		setError(null);
		try {
			const accepted = await uploadZip(file);
			setFile(null);
			onJobStarted(accepted.job_id);
		} catch (err) {
			setError(err);
		} finally {
			setSending(false);
		}
	};
	if (activeJobId) return /* @__PURE__ */ jsxs("div", {
		className: "mx-auto max-w-[880px] space-y-4",
		children: [
			/* @__PURE__ */ jsxs("div", {
				className: "flex flex-wrap items-baseline justify-between gap-3",
				children: [/* @__PURE__ */ jsx("h2", {
					className: "text-[15px] font-semibold",
					children: "Processing"
				}), /* @__PURE__ */ jsx("span", {
					className: "text-[12px] text-ink3",
					children: polling ? "Checking every 2 seconds" : "Finished, no longer polling"
				})]
			}),
			pollError && /* @__PURE__ */ jsx(ErrorNote, { error: pollError.status === 404 ? {
				message: `The server has no record of ${activeJobId}. Job status is held in memory, so restarting the backend loses it. Upload the zip again.`,
				status: 404
			} : pollError }),
			job ? /* @__PURE__ */ jsx(Progress, {
				job,
				polling,
				onRank
			}) : !pollError && /* @__PURE__ */ jsxs("div", {
				className: "card overflow-hidden",
				children: [/* @__PURE__ */ jsx(BusyBar, {}), /* @__PURE__ */ jsx("p", {
					className: "px-4 py-6 text-[13px] text-ink3",
					children: "Asking the server for this batch..."
				})]
			})
		]
	});
	return /* @__PURE__ */ jsxs("div", {
		className: "mx-auto max-w-[760px] space-y-5",
		children: [
			/* @__PURE__ */ jsxs("div", {
				className: "card p-5",
				children: [
					/* @__PURE__ */ jsx("label", {
						htmlFor: "company",
						className: "label",
						children: "Hiring for"
					}),
					/* @__PURE__ */ jsx("input", {
						id: "company",
						className: "field",
						placeholder: "Northwind Systems",
						value: company,
						onChange: (e) => onCompany(e.target.value)
					}),
					/* @__PURE__ */ jsx("p", {
						className: "hint",
						children: "Shown as a label while you work. Held in this browser tab only, never sent to the server or saved, and cleared when you refresh."
					})
				]
			}),
			/* @__PURE__ */ jsx(DropZone, {
				file,
				onFile: setFile,
				disabled: sending
			}),
			/* @__PURE__ */ jsx(ErrorNote, {
				error,
				onRetry: () => void submit()
			}),
			/* @__PURE__ */ jsxs("div", {
				className: "flex flex-wrap items-center gap-3",
				children: [/* @__PURE__ */ jsxs("button", {
					type: "button",
					className: "btn btn-primary btn-lg",
					disabled: !file || sending,
					onClick: () => void submit(),
					children: [/* @__PURE__ */ jsx(IconUpload, { size: 16 }), sending ? "Uploading..." : "Upload and process"]
				}), /* @__PURE__ */ jsx("span", {
					className: "text-[12px] text-ink3",
					children: "Processing runs on the server and takes several minutes. You can watch it here."
				})]
			})
		]
	});
}
//#endregion
//#region src/App.tsx
/**
* Client-side view switching, no router.
*
* Four views, no deep links, and the session state below is deliberately not
* in the URL. A router would add a dependency and a set of shareable URLs that
* this app specifically should not have.
*/
var PAGE = {
	batches: {
		title: "Batches",
		description: "Every pile of resumes this server has processed."
	},
	upload: {
		title: "Upload",
		description: "Send one zip of resumes and watch the server read them."
	},
	rank: {
		title: "Rank",
		description: "A job description becomes a rubric, and the rubric becomes a shortlist."
	},
	fairness: {
		title: "Fairness audit",
		description: "Who the shortlist selects, compared across groups the ranker never sees."
	}
};
function App() {
	const [view, setView] = useState("batches");
	const [company, setCompany] = useState("");
	const [activeJobId, setActiveJobId] = useState(null);
	const [jdText, setJdText] = useState("");
	const [shortlistSize, setShortlistSize] = useState(45);
	const [rankResult, setRankResult] = useState(null);
	const [auditResult, setAuditResult] = useState(null);
	const { job: activeJob, error: pollError, polling } = useJobPoll(activeJobId);
	const selectJob = (jobId) => {
		setActiveJobId(jobId);
		setRankResult(null);
		setAuditResult(null);
	};
	const page = PAGE[view];
	const trimmedCompany = company.trim();
	return /* @__PURE__ */ jsxs(AppShell, {
		view,
		onView: setView,
		title: page.title,
		description: trimmedCompany ? `${page.description} Hiring for ${trimmedCompany}.` : page.description,
		actions: view === "batches" ? /* @__PURE__ */ jsxs("button", {
			type: "button",
			className: "btn btn-primary",
			onClick: () => {
				selectJob(null);
				setView("upload");
			},
			children: [/* @__PURE__ */ jsx(IconUpload, { size: 15 }), "Upload resumes"]
		}) : void 0,
		activeJobId,
		activeJob,
		children: [
			view === "batches" && /* @__PURE__ */ jsx(BatchesView, {
				activeJobId,
				onOpen: (jobId) => {
					selectJob(jobId);
					setView("rank");
				},
				onUpload: () => {
					selectJob(null);
					setView("upload");
				}
			}),
			view === "upload" && /* @__PURE__ */ jsx(UploadView, {
				company,
				onCompany: setCompany,
				activeJobId,
				job: activeJob,
				pollError,
				polling,
				onJobStarted: selectJob,
				onRank: () => setView("rank")
			}),
			view === "rank" && /* @__PURE__ */ jsx(RankView, {
				jobId: activeJobId,
				jdText,
				onJdText: setJdText,
				shortlistSize,
				onShortlistSize: setShortlistSize,
				result: rankResult,
				onResult: setRankResult,
				onAudit: () => setView("fairness"),
				onPickBatch: () => setView("batches")
			}),
			view === "fairness" && /* @__PURE__ */ jsx(FairnessView, {
				jobId: activeJobId,
				jdText,
				shortlistSize,
				result: auditResult,
				onResult: setAuditResult,
				onRank: () => setView("rank")
			})
		]
	});
}
//#endregion
//#region src/__ssr_smoke.tsx
var html = renderToString(/* @__PURE__ */ jsx(App, {}));
console.log("SSR_LENGTH=" + html.length);
console.log("HAS_BRAND=" + html.includes("HireMind"));
console.log("HAS_NAV=" + (html.includes("Batches") && html.includes("Fairness")));
console.log("HAS_DISCLAIMER=" + html.includes("does not decide who to hire"));
//#endregion
export {};

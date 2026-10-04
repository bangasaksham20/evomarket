"""
EvoMarket — Autonomous Closed-Loop Genetic Ad Agency.

FastAPI backend + in-memory ad-exchange simulation engine.
Integrations (graceful fallback if unreachable):
  - Tavily  : live cultural recon (trends, competitor flaws)
  - ZooWork : sandboxed LLM hypothesis generation (gpt-5.6-terra)
"""

import base64
import hashlib
import html
import math
import os
import random
import re
import textwrap
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _sanitize_proxy_env() -> None:
    """httpx crashes on bracketed IPv6 tokens (e.g. `[::1]`) in no_proxy.

    Strip the offending tokens so SDK HTTP clients can initialize.
    Harmless on machines without the malformed entries.
    """
    import re as _re

    for var in ("no_proxy", "NO_PROXY"):
        val = os.environ.get(var)
        if not val:
            continue
        cleaned = ",".join(
            tok for tok in (t.strip() for t in val.split(",")) if tok and not _re.fullmatch(r"\[.*\]", tok)
        )
        if cleaned != val:
            os.environ[var] = cleaned


_sanitize_proxy_env()

# --------------------------------------------------------------------------
# Exchange model constants
# --------------------------------------------------------------------------

PLATFORMS: Dict[str, Dict[str, Any]] = {
    "tiktok": {
        "label": "TikTok Spark",
        "base_ctr": 2.2,      # %
        "base_cvr": 0.055,    # fraction
        "cpc": 0.85,
        "variance": 0.35,
        "fatigue": 0.25,      # CTR decay per epoch for un-mutated creatives
        "hue": ["#0b0b10", "#16161f"],
        "accent": "#25F4EE",
        "accent2": "#FE2C55",
    },
    "meta": {
        "label": "Instagram Reel",
        "base_ctr": 1.5,
        "base_cvr": 0.042,
        "cpc": 1.20,
        "variance": 0.25,
        "fatigue": 0.18,
        "hue": ["#1a0b1e", "#2b1140"],
        "accent": "#C13584",
        "accent2": "#F77737",
    },
    "google": {
        "label": "Google Search",
        "base_ctr": 2.8,
        "base_cvr": 0.085,
        "cpc": 2.40,
        "variance": 0.12,
        "fatigue": 0.08,
        "hue": ["#0a1420", "#10233a"],
        "accent": "#4285F4",
        "accent2": "#34A853",
    },
}

ANGLES = ["Visceral Pain Point", "Aesthetic Status", "Utilitarian", "High-Intent"]

GEN_LABELS = [
    "Gen 0 (Cold Start)",
    "Gen 1 (Tavily Recon)",
    "Gen 2 (Crossover Alpha)",
    "Gen 3 (Max Scale)",
]
GEN_BUDGETS = [100.0, 150.0, 150.0, 600.0]

STATUS_PRUNED = "PRUNED"
STATUS_SURVIVOR = "SURVIVOR"
STATUS_CHAMPION = "REIGNING CHAMPION"

STOPWORDS = {
    "with", "from", "that", "this", "your", "you", "the", "and", "for", "are",
    "our", "all", "into", "over", "per", "new", "more", "most", "free", "just",
    "like", "made", "make", "will", "designed", "premium", "ultimate",
}

# --------------------------------------------------------------------------
# API models
# --------------------------------------------------------------------------


class OptimizeRequest(BaseModel):
    product_name: str = Field(..., min_length=1)
    description: str = Field(default="")
    category: str = Field(default="General")
    image_url: Optional[str] = None
    retail_price: float = Field(default=29.99, gt=0)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def rng_for(*parts: Any) -> random.Random:
    seed_src = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(seed_src.encode("utf-8")).hexdigest()
    return random.Random(int(digest, 16) % (2 ** 32))


def keywords_of(product_name: str, description: str, category: str, n: int = 6) -> List[str]:
    text = f"{product_name} {description} {category}".lower()
    words = re.findall(r"[a-z]{4,}", text)
    seen, out = set(), []
    for w in words:
        if w not in STOPWORDS and w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= n:
            break
    return out or ["product"]


def is_owala(product_name: str) -> bool:
    return "owala" in product_name.lower()


# --------------------------------------------------------------------------
# Cultural recon — Tavily (live) with heuristic fallback
# --------------------------------------------------------------------------


def fallback_trends(product_name: str, category: str) -> List[Dict[str, str]]:
    kws = keywords_of(product_name, "", category, 3)
    kw = " ".join(kws[:2])
    if is_owala(product_name):
        return [
            {
                "title": "Owala Color Drop frenzy — limited pastel restocks sell out in hours",
                "url": "https://www.tiktok.com/tag/owalacolordrop",
                "insight": "UGC creators racing to unbox Color Drop shades; comment sections begging for restock alerts.",
            },
            {
                "title": "Tote-bag spill tests dominate #WaterTok",
                "url": "https://www.reddit.com/r/HydroHomies/",
                "insight": "Side-by-side leak tests in leather totes and car cupholders; leak-proof claims get publicly verified.",
            },
            {
                "title": "Pilates-bag everyday-carry hauls trending",
                "url": "https://www.tiktok.com/tag/pilatesbag",
                "insight": "'What's in my pilates bag' format pairs bottles with aesthetic lifestyle shots; pastel wins.",
            },
            {
                "title": "Competitor flaw: sweat-ring complaints on uninsulated bottles",
                "url": "https://www.reddit.com/r/BuyItForLife/",
                "insight": "Threads roasting bottles that sweat through gym bags — insulation + dry exterior is the wedge.",
            },
        ]
    return [
        {
            "title": f"{product_name} viral unboxings pick up on TikTok",
            "url": "https://www.tiktok.com",
            "insight": f"Creators filming first reactions to {kw}; authentic UGC outperforms studio shots.",
        },
        {
            "title": f"Reddit threads compare {category} durability complaints",
            "url": "https://www.reddit.com",
            "insight": "Top complaint themes: overpromised claims and cheap feel — verifiable demos win trust.",
        },
        {
            "title": f"{category} aesthetic hauls trending into 2026",
            "url": "https://www.tiktok.com",
            "insight": "Pastel/minimal styling dominates 'what's in my bag' formats; visual status drives saves.",
        },
        {
            "title": f"Competitor flaw watch: {kw} 1-star reviews cite gimmicks",
            "url": "https://www.google.com",
            "insight": "Reviewers punish gimmicky features; utilitarian proof points convert high-intent searchers.",
        },
    ]


def tavily_recon(product_name: str, category: str, description: str) -> Tuple[List[Dict[str, str]], str]:
    """Returns (trends, mode) where mode is 'live' or 'fallback'.

    Single fast query with a hard timeout cap — a slow/rate-limited key must
    never stall the optimization loop; fallback trends are high quality.
    """
    key = os.getenv("TAVILY_API_KEY")
    try:
        if not key:
            raise RuntimeError("TAVILY_API_KEY not set")
        from tavily import TavilyClient  # type: ignore

        client = TavilyClient(api_key=key)
        # Hard cap so a slow/rate-limited key never stalls the loop.
        # Tune via TAVILY_TIMEOUT_MS (e.g. 6000 on Vercel Hobby's 10s limit).
        timeout_s = int(os.getenv("TAVILY_TIMEOUT_MS", "12000")) / 1000
        res = client.search(
            f"trending {product_name} viral TikTok {category} 2026",
            max_results=6, search_depth="fast", timeout=timeout_s,
        )
        trends: List[Dict[str, str]] = []
        for r in (res.get("results") or [])[:6]:
            url = r.get("url") or ""
            if not url:
                continue
            trends.append({
                "title": (r.get("title") or product_name)[:140],
                "url": url,
                "insight": (r.get("content") or "")[:220],
            })
        if trends:
            return trends, "live"
    except Exception:
        pass
    return fallback_trends(product_name, category), "fallback"

# --------------------------------------------------------------------------
# Hypothesis generation — ZooWork sandbox (live) with heuristic fallback
# --------------------------------------------------------------------------


def heuristic_hypotheses(
    product_name: str, description: str, category: str, trends: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    """Four genetic angles: visceral, aesthetic, utilitarian, high-intent."""
    kws = keywords_of(product_name, description, category)
    sents = [s.strip() for s in re.split(r"[.!?]+", description) if s.strip()]
    proof = sents[1] if len(sents) > 1 else (sents[0] if sents else "")
    if not proof:
        proof = f"the {category} people keep recommending"
    tagline = sents[0][:90] if sents else f"premium {category}"

    if is_owala(product_name):
        return [
            {
                "angle": "Visceral Pain Point",
                "hook": "I flipped my Owala upside down in my car. Zero drops.",
                "body": "FreeSip leak-proof spout survives the car-seat spill test that kills every other bottle.",
                "audience": "TikTok #WaterTok, 18–34, commuters & gym-goers",
            },
            {
                "angle": "Aesthetic Status",
                "hook": "The Color Drop everyone's fighting over at Target.",
                "body": "Pastel FreeSip shades that match your pilates set. Restock alerts in comments.",
                "audience": "TikTok/IG aesthetic lifestyle, 18–29",
            },
            {
                "angle": "Utilitarian",
                "hook": "24h cold. Zero sweat. One hand.",
                "body": "Double-wall vacuum insulation keeps drinks ice-cold for 24 hours with a dry exterior.",
                "audience": "Meta broad, 25–44, practical buyers",
            },
            {
                "angle": "High-Intent",
                "hook": "Owala FreeSip 32oz — leakproof insulated bottle",
                "body": "FreeSip spout, 24h cold retention, cupholder-friendly. Ships today.",
                "audience": "Google Search, 'best leakproof water bottle'",
            },
        ]

    return [
        {
            "angle": "Visceral Pain Point",
            "hook": f"POV: your {category} finally keeps up with you.",
            "body": f"{product_name}: {proof}.",
            "audience": f"TikTok #{kws[0]}tok, 18–34",
        },
        {
            "angle": "Aesthetic Status",
            "hook": f"The {category} your feed keeps asking about.",
            "body": f"{product_name} — {tagline}. Unbox it once and you'll get it.",
            "audience": "Instagram aesthetic & lifestyle, 18–34",
        },
        {
            "angle": "Utilitarian",
            "hook": f"{proof[:60].rstrip('.')}. No gimmicks.",
            "body": f"{tagline} — verified specs, honest reviews. See the proof.",
            "audience": f"Meta broad, 25–54, {category} buyers",
        },
        {
            "angle": "High-Intent",
            "hook": f"{product_name} — shop the best-rated {category}",
            "body": f"{proof[:100]} In stock now, ships fast.",
            "audience": f"Google Search, 'best {category}'",
        },
    ]


def _parse_hyps_json(raw: str) -> Optional[List[Dict[str, str]]]:
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return None
    try:
        hyps = __import__("json").loads(match.group(0))
    except Exception:
        return None
    if isinstance(hyps, list) and len(hyps) == 4 and all("hook" in h for h in hyps):
        return [
            {
                "angle": ANGLES[i] if h.get("angle") not in ANGLES else h["angle"],
                "hook": str(h.get("hook", ""))[:90],
                "body": str(h.get("body", ""))[:140],
                "audience": str(h.get("audience", ""))[:60],
            }
            for i, h in enumerate(hyps)
        ]
    return None


def _zoowork_prompt(product_name: str, description: str, category: str,
                    trends: List[Dict[str, str]]) -> str:
    trend_lines = "\n".join(f"- {t['title']}: {t['insight']}" for t in trends[:5])
    return (
        "You are a direct-response creative strategist. Deconstruct this product into "
        "4 genetic ad angles: Visceral Pain Point, Aesthetic Status, Utilitarian, High-Intent.\n"
        f"Product: {product_name}\nDescription: {description}\nCategory: {category}\n"
        f"Live trends:\n{trend_lines}\n"
        "Reply with ONLY a JSON array of 4 objects with keys: angle, hook (<=90 chars), "
        "body (<=140 chars), audience (<=60 chars). No markdown fences."
    )


def _zoowork_live_client(prompt: str, api_key: str) -> Optional[List[Dict[str, str]]]:
    """Real SDK client flow (ZooworkClient). Best-effort; None on any failure."""
    import asyncio
    import time

    async def _run() -> str:
        from zoowork import create_zoowork_client, assistant_text

        client = create_zoowork_client(api_key=api_key, timeout=10.0)
        try:
            agent = await client.create_agent({
                "name": "evomarket-decomposer",
                "model": "litellm/gpt-5.6-terra",
                "instructions": "You are a direct-response creative strategist. "
                                "Always reply with raw JSON only.",
            })
            agent_id = agent["id"] if isinstance(agent, dict) else agent.get("id")
            session = await client.create_session(agent_id, {
                "initial_events": [{"type": "user.message", "content": prompt}],
            })
            session_id = session["id"] if isinstance(session, dict) else session.get("id")
            text = ""
            deadline = time.time() + 30
            while time.time() < deadline:
                await asyncio.sleep(2.5)
                events = await client.list_events(
                    agent_id, session_id,
                    types=["run.finished", "agent.assistant", "chat.final"],
                )
                for ev in events or []:
                    try:
                        chunk = assistant_text(ev)
                    except Exception:
                        chunk = ""
                    if chunk:
                        text += chunk
                if any(getattr(ev, "event_type", "") == "run.finished" for ev in events or []):
                    break
                if len(text) > 4000:
                    break
            return text
        finally:
            try:
                await client.aclose()
            except Exception:
                pass

    try:
        raw = asyncio.run(asyncio.wait_for(_run(), timeout=45))
    except Exception:
        return None
    return _parse_hyps_json(raw) if raw else None


def zoowork_hypotheses(
    product_name: str, description: str, category: str, trends: List[Dict[str, str]]
) -> Tuple[List[Dict[str, str]], str]:
    """Returns (hypotheses, mode). Defensive: any SDK mismatch -> fallback.

    Live LLM path is opt-in via ZOOWORK_LIVE=1 (it adds latency); the brief's
    module-level SDK surface is probed first, then the real ZooworkClient flow.
    """
    key = os.getenv("ZOOWORK_API_KEY")
    prompt = _zoowork_prompt(product_name, description, category, trends)
    try:
        if not key:
            raise RuntimeError("ZOOWORK_API_KEY not set")
        import zoowork  # type: ignore

        # (a) brief's specified module-level surface
        create_agent = getattr(zoowork, "create_agent", None)
        start_agent = getattr(zoowork, "start_agent", None)
        wait_until_running = getattr(zoowork, "wait_until_running", None)
        create_session = getattr(zoowork, "create_session", None)
        stream_events = getattr(zoowork, "stream_events", None)
        if all([create_agent, start_agent, wait_until_running, create_session, stream_events]):
            agent = create_agent(model="litellm/gpt-5.6-terra", name="evomarket-decomposer")
            agent_id = agent["id"] if isinstance(agent, dict) else agent
            start_agent(agent_id)
            wait_until_running(agent_id, timeout=60)
            session = create_session(agent_id, {"initial_events": [{"type": "user.message", "content": prompt}]})
            session_id = session["id"] if isinstance(session, dict) else session
            parts: List[str] = []
            for event in stream_events(session_id):
                if isinstance(event, dict):
                    parts.append(str(event.get("delta") or event.get("content") or ""))
                else:
                    parts.append(str(event))
                if sum(len(p) for p in parts) > 6000:
                    break
            parsed = _parse_hyps_json("".join(parts))
            if parsed:
                return parsed, "live"
            raise RuntimeError("unparseable ZooWork output")

        # (b) real client-based SDK flow (opt-in: adds up to ~45s latency)
        if os.getenv("ZOOWORK_LIVE") == "1":
            parsed = _zoowork_live_client(prompt, key)
            if parsed:
                return parsed, "live"
        raise RuntimeError("zoowork live path unavailable")
    except Exception:
        return heuristic_hypotheses(product_name, description, category, trends), "fallback"
# --------------------------------------------------------------------------
# Simulation engine
# --------------------------------------------------------------------------

# Gen-0 quality anchors for the Owala case study so the narrative beats land.
OWALA_QUALITY = {
    ("Visceral Pain Point", "tiktok"): 0.78,
    ("Visceral Pain Point", "meta"): 0.62,
    ("Aesthetic Status", "tiktok"): 1.05,
    ("Aesthetic Status", "meta"): 0.95,
    ("Utilitarian", "meta"): 0.505,
    ("Utilitarian", "google"): 0.55,
    ("High-Intent", "google"): 0.95,
    ("High-Intent", "tiktok"): 0.70,
}

GEN0_SLOTS = [
    ("Visceral Pain Point", "tiktok"),
    ("Visceral Pain Point", "meta"),
    ("Aesthetic Status", "tiktok"),
    ("Aesthetic Status", "meta"),
    ("Utilitarian", "meta"),
    ("Utilitarian", "google"),
    ("High-Intent", "google"),
    ("High-Intent", "tiktok"),
]


def simulate_metrics(
    platform: str, quality: float, stale_gens: int, spend: float,
    price: float, intent_mult: float, rng: random.Random,
) -> Dict[str, float]:
    p = PLATFORMS[platform]
    fatigue_mult = max(0.30, 1.0 - p["fatigue"] * stale_gens)
    noise = rng.uniform(1 - p["variance"] / 2, 1 + p["variance"] / 2)
    ctr = p["base_ctr"] * quality * fatigue_mult * noise                      # percent
    cvr = p["base_cvr"] * (0.65 + 0.35 * quality) * intent_mult
    cvr *= rng.uniform(0.92, 1.08)
    cpc = p["cpc"] * rng.uniform(0.90, 1.10)
    clicks = spend / cpc
    impressions = clicks / max(ctr / 100.0, 1e-6)
    orders = clicks * cvr
    revenue = orders * price
    roas = revenue / spend if spend > 0 else 0.0
    fitness = roas * 0.65 + ctr * 0.35
    return {
        "spend": spend, "impressions": impressions, "clicks": clicks,
        "ctr": ctr, "cvr": cvr, "cpc": cpc, "orders": orders,
        "revenue": revenue, "roas": roas, "fitness": fitness,
    }


def blended_roas(ads: List[Dict[str, Any]]) -> float:
    spend = sum(a["spend"] for a in ads)
    rev = sum(a["revenue"] for a in ads)
    return rev / spend if spend > 0 else 0.0


def prune_generation(ads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rank by fitness; bottom 50% -> PRUNED, rest -> SURVIVOR."""
    ranked = sorted(ads, key=lambda a: a["fitness"], reverse=True)
    n_keep = max(1, math.ceil(len(ranked) / 2))
    for i, ad in enumerate(ranked):
        if i < n_keep:
            ad["status"] = STATUS_SURVIVOR
            ad["rationale"] = (
                f"Survived the cut: fitness {ad['fitness']:.2f} ranked #{i + 1}/{len(ranked)} — "
                f"top-half CTR {ad['ctr']:.2f}% at {ad['roas']:.2f}x ROAS. Selected for crossover breeding."
            )
        else:
            ad["status"] = STATUS_PRUNED
            ad["rationale"] = (
                f"Why it died: fitness {ad['fitness']:.2f} ranked #{i + 1}/{len(ranked)} — bottom 50%. "
                f"CTR {ad['ctr']:.2f}% with {ad['roas']:.2f}x ROAS could not justify continued spend."
            )
    return ranked


def crossover_hook(
    parent_a: Dict[str, Any], parent_b: Dict[str, Any],
    trend_phrase: str, rng: random.Random,
) -> Tuple[str, str]:
    """Winning hook from A fused with the winning angle of B + fresh trend."""
    hook_a = parent_a["hook"].rstrip(".")
    hook_b = parent_b["hook"].rstrip(".")
    trend_bit = f" {trend_phrase}." if trend_phrase and rng.random() < 0.75 else ""
    angle_b = parent_b["angle"].lower()
    templates = [
        f"{hook_a} — {hook_b}.{trend_bit}",
        f"{hook_a}.{trend_bit} {hook_b}.",
        f"{hook_a}, but make it {angle_b}.{trend_bit}",
        f"{hook_a} meets {angle_b} energy.{trend_bit}",
    ]
    hook = rng.choice(templates).strip().replace("..", ".")
    body = (
        f"{parent_a['body']} Fused with {angle_b} creative from {parent_b['id']}."
        f"{trend_bit}"
    )
    return hook[:110], body[:160]


def mutate_hook(hook: str, trend_phrase: str, rng: random.Random) -> str:
    hook = hook.rstrip(".")
    muts = [
        f"{hook} — {trend_phrase}",
        f"{hook} (watch till the end)",
        f"Stop scrolling: {hook}",
        f"{hook}. {trend_phrase} approved.",
    ]
    return rng.choice(muts)[:110]


# --------------------------------------------------------------------------
# SVG creative synthesis (inline data URIs)
# --------------------------------------------------------------------------


def fetch_image_b64(image_url: Optional[str]) -> Optional[str]:
    if not image_url:
        return None
    try:
        resp = requests.get(
            image_url, timeout=6,
            headers={"User-Agent": "EvoMarket/1.0"},
        )
        ctype = resp.headers.get("Content-Type", "")
        if resp.status_code == 200 and ctype.startswith("image/") and len(resp.content) < 1_500_000:
            return base64.b64encode(resp.content).decode("ascii"), ctype.split(";")[0]
    except Exception:
        pass
    return None


def wrap_lines(text: str, width: int, max_lines: int = 4) -> List[str]:
    lines = textwrap.wrap(text, width=width)[:max_lines]
    if len(textwrap.wrap(text, width=width)) > max_lines:
        lines[-1] = lines[-1].rstrip(".") + "…"
    return lines or [""]


def make_creative(ad: Dict[str, Any], product_name: str, price: float,
                 image_b64: Optional[Tuple[str, str]]) -> str:
    p = PLATFORMS[ad["platform"]]
    W, H = 600, 800
    hook_lines = wrap_lines(ad["hook"], 20, 4)
    body_lines = wrap_lines(ad["body"], 34, 3)

    hook_svg = "".join(
        f'<text x="40" y="{300 + i * 44}" font-family="Arial,Helvetica,sans-serif" '
        f'font-size="36" font-weight="800" fill="#ffffff">{html.escape(line)}</text>'
        for i, line in enumerate(hook_lines)
    )
    body_svg = "".join(
        f'<text x="40" y="{300 + len(hook_lines) * 44 + 28 + i * 26}" font-family="Arial,Helvetica,sans-serif" '
        f'font-size="20" fill="#d7d7e0">{html.escape(line)}</text>'
        for i, line in enumerate(body_lines)
    )

    if image_b64:
        b64, mime = image_b64
        media = (
            f'<image href="data:{mime};base64,{b64}" x="40" y="96" width="520" height="180" '
            f'preserveAspectRatio="xMidYMid slice" clip-path="url(#rc)"/>'
        )
    else:
        # Abstract product glyph: bottle silhouette with highlight
        media = (
            f'<rect x="40" y="96" width="520" height="180" rx="18" fill="url(#pg)"/>'
            f'<rect x="270" y="120" width="60" height="132" rx="22" fill="#ffffff" opacity="0.92"/>'
            f'<rect x="270" y="120" width="60" height="132" rx="22" fill="url(#pg)" opacity="0.55"/>'
            f'<rect x="286" y="104" width="28" height="22" rx="6" fill="#ffffff" opacity="0.85"/>'
            f'<rect x="120" y="150" width="120" height="14" rx="7" fill="#ffffff" opacity="0.35"/>'
            f'<rect x="120" y="172" width="84" height="14" rx="7" fill="#ffffff" opacity="0.22"/>'
            f'<circle cx="470" cy="140" r="26" fill="#ffffff" opacity="0.18"/>'
            f'<circle cx="500" cy="220" r="14" fill="#ffffff" opacity="0.12"/>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
        f'<defs>'
        f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{p["hue"][0]}"/><stop offset="1" stop-color="{p["hue"][1]}"/>'
        f'</linearGradient>'
        f'<linearGradient id="pg" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0" stop-color="{p["accent"]}"/><stop offset="1" stop-color="{p["accent2"]}"/>'
        f'</linearGradient>'
        f'<clipPath id="rc"><rect x="40" y="96" width="520" height="180" rx="18"/></clipPath>'
        f'</defs>'
        f'<rect width="{W}" height="{H}" fill="url(#bg)"/>'
        # platform chrome
        f'<rect x="0" y="0" width="{W}" height="64" fill="#000000" opacity="0.45"/>'
        f'<circle cx="40" cy="32" r="14" fill="{p["accent"]}"/>'
        f'<text x="64" y="40" font-family="Arial" font-size="20" font-weight="700" fill="#ffffff">{html.escape(p["label"])}</text>'
        f'<text x="560" y="40" text-anchor="end" font-family="Arial" font-size="16" fill="#9a9aad">Sponsored</text>'
        f'{media}'
        f'{hook_svg}'
        f'{body_svg}'
        # CTA bar
        f'<rect x="40" y="700" width="520" height="64" rx="32" fill="{p["accent"]}"/>'
        f'<text x="300" y="741" text-anchor="middle" font-family="Arial" font-size="24" font-weight="800" fill="#0b0b10">SHOP NOW — ${price:.2f}</text>'
        f'<text x="40" y="692" font-family="Arial" font-size="17" fill="#9a9aad">{html.escape(ad["angle"])} · {html.escape(product_name[:34])}</text>'
        f'</svg>'
    )
    return "data:image/svg+xml;utf8," + urllib.parse.quote(svg)

# --------------------------------------------------------------------------
# Evolution runner
# --------------------------------------------------------------------------


def trend_phrase_for(trends: List[Dict[str, str]], rng: random.Random) -> str:
    if not trends:
        return "viral right now"
    t = rng.choice(trends[:4])
    title = t["title"]
    # compress to a punchy phrase
    short = re.split(r"[—–:–]| - ", title)[0].strip()
    return short[:60] if short else title[:60]


def run_evolution(req: OptimizeRequest) -> Dict[str, Any]:
    product_name = req.product_name.strip()
    description = req.description or ""
    category = req.category or "General"
    price = req.retail_price
    owala = is_owala(product_name)

    # 1. Cultural recon
    trends, t_mode = tavily_recon(product_name, category, description)

    # 2. Hypothesis generation (ZooWork sandbox)
    hyps, z_mode = zoowork_hypotheses(product_name, description, category, trends)
    hyp_by_angle = {h["angle"]: h for h in hyps}

    # 3. Stock image (best-effort embed)
    image_b64 = fetch_image_b64(req.image_url)

    rng = rng_for(product_name, category, price)
    generations: List[Dict[str, Any]] = []

    def build_ad(ad_id: str, gen: int, angle: str, platform: str,
                 hook: str, body: str, audience: str, quality: float,
                 spend: float, stale_gens: int, parents: List[str],
                 intent_mult: float = 1.0) -> Dict[str, Any]:
        m = simulate_metrics(platform, quality, stale_gens, spend, price, intent_mult, rng)
        ad = {
            "id": ad_id, "gen": gen, "platform": platform,
            "platform_label": PLATFORMS[platform]["label"], "angle": angle,
            "hook": hook, "body": body, "audience": audience,
            "quality": round(quality, 3), "stale_gens": stale_gens,
            "parents": parents, "status": STATUS_SURVIVOR, "rationale": "",
            **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()},
        }
        ad["creative"] = make_creative(ad, product_name, price, image_b64)
        return ad

    # ---- Gen 0: cold start, equal budget split ----
    gen0_ads: List[Dict[str, Any]] = []
    g0_budget = GEN_BUDGETS[0]
    for i, (angle, platform) in enumerate(GEN0_SLOTS):
        h = hyp_by_angle.get(angle, hyps[i % len(hyps)])
        if owala:
            q = OWALA_QUALITY.get((angle, platform), 0.8)
        else:
            q = rng.uniform(0.5, 1.3)
            if any(k in h["hook"].lower() for k in keywords_of(product_name, description, category, 3)):
                q += 0.08
        intent = 1.25 if angle == "High-Intent" and platform == "google" else 1.0
        gen0_ads.append(build_ad(
            f"G0-A{i + 1}", 0, angle, platform, h["hook"], h["body"], h["audience"],
            q, g0_budget / len(GEN0_SLOTS), 0, [], intent,
        ))
    generations.append({
        "gen": 0, "label": GEN_LABELS[0], "budget": g0_budget,
        "blended_roas": round(blended_roas(gen0_ads), 3),
        "ads": prune_generation(gen0_ads),
    })
    survivors = [a for a in generations[0]["ads"] if a["status"] == STATUS_SURVIVOR]

    # ---- Gen 1..2: crossover epochs ----
    for gen in (1, 2):
        budget = GEN_BUDGETS[gen]
        new_ads: List[Dict[str, Any]] = []
        # elitism: carry the reigning best forward unchanged (fatigue bites)
        elite = max(survivors, key=lambda a: a["fitness"])
        elite_copy = dict(elite)
        elite_copy.update({
            "id": f"G{gen}-E1", "gen": gen, "stale_gens": elite["stale_gens"] + 1,
            "parents": [elite["id"]], "status": STATUS_SURVIVOR, "rationale": "",
        })
        m = simulate_metrics(elite["platform"], elite["quality"], elite_copy["stale_gens"],
                             budget * 0.25, price, 1.0, rng)
        elite_copy.update({k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()})
        elite_copy["rationale"] = ""
        new_ads.append(elite_copy)

        pairs = [(survivors[0], survivors[1 % len(survivors)]),
                 (survivors[-1], survivors[0])]
        if gen == 2 and len(survivors) >= 2:
            pairs = [(survivors[0], survivors[1]), (survivors[0], survivors[1]),
                     (survivors[1], survivors[0])]
        # normalize to 3 children so gen size = 4 (elite + 3)
        while len(pairs) < 3:
            pairs.append((survivors[0], survivors[-1]))
        pairs = pairs[:3]

        for j, (pa, pb) in enumerate(pairs):
            phrase = trend_phrase_for(trends, rng)
            hook, body = crossover_hook(pa, pb, phrase, rng)
            q = max(pa["quality"], pb["quality"]) * rng.uniform(0.95, 1.15) + 0.06
            q = min(q, 1.85)
            plat = pa["platform"] if rng.random() < 0.6 else pb["platform"]
            angle = pa["angle"]
            aud = f"{pa['audience'].split(',')[0].strip()} × {pb['audience'].split(',')[0].strip()}"
            # Case-study beat: the peak-alpha crossover for Owala fuses the
            # leak-proof spill test with the pastel Color Drop aesthetic.
            if owala and gen == 2 and j == 0:
                hook = "Pretty girls don't let their water bottles leak in their pilates bag."
                body = ("TikTok UGC POV: pastel Owala tossed upside-down into a leather tote — "
                        "zero leaks. Color Drop frenzy approved.")
                q = max(q, 1.7)
                plat = "tiktok"
            new_ads.append(build_ad(
                f"G{gen}-C{j + 1}", gen, angle, plat, hook, body, aud,
                q, budget * 0.25, 0, [pa["id"], pb["id"]],
            ))

        # fitness-weighted (Thompson-style) budget reallocation within the gen
        weights = [max(a["fitness"], 0.05) ** 2 for a in new_ads]
        total_w = sum(weights)
        for ad, w in zip(new_ads, weights):
            spend = budget * w / total_w
            m = simulate_metrics(ad["platform"], ad["quality"], ad["stale_gens"],
                                 spend, price, 1.0, rng)
            ad.update({k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()})
            ad["spend"] = round(spend, 2)

        generations.append({
            "gen": gen, "label": GEN_LABELS[gen], "budget": budget,
            "blended_roas": round(blended_roas(new_ads), 3),
            "ads": prune_generation(new_ads),
        })
        survivors = [a for a in generations[gen]["ads"] if a["status"] == STATUS_SURVIVOR]
        if len(survivors) < 2:
            survivors = sorted(generations[gen]["ads"], key=lambda a: a["fitness"], reverse=True)[:2]
            for s in survivors:
                s["status"] = STATUS_SURVIVOR

    # ---- Gen 3: max scale — 90% of budget into the champion lineage ----
    gen = 3
    budget = GEN_BUDGETS[gen]
    champ_seed = max(survivors, key=lambda a: a["fitness"])
    runner = sorted(survivors, key=lambda a: a["fitness"], reverse=True)[1] if len(survivors) > 1 else champ_seed
    final_ads: List[Dict[str, Any]] = []

    phrase = trend_phrase_for(trends, rng)
    hook, body = mutate_hook(champ_seed["hook"], phrase, rng), champ_seed["body"]
    final_ads.append(build_ad(
        "G3-ALPHA", gen, champ_seed["angle"], champ_seed["platform"], hook, body,
        champ_seed["audience"], min(champ_seed["quality"] * 1.05, 1.9),
        budget * 0.90, 0, [champ_seed["id"]],
    ))
    hook2, body2 = crossover_hook(runner, champ_seed, phrase, rng)
    final_ads.append(build_ad(
        "G3-BETA", gen, runner["angle"], runner["platform"], hook2, body2,
        runner["audience"], min(runner["quality"] * 1.02, 1.8),
        budget * 0.07, 0, [runner["id"], champ_seed["id"]],
    ))
    wild_angle = rng.choice(ANGLES)
    h = hyp_by_angle.get(wild_angle, hyps[0])
    final_ads.append(build_ad(
        "G3-WILD", gen, wild_angle, rng.choice(list(PLATFORMS)),
        mutate_hook(h["hook"], phrase, rng), h["body"], h["audience"],
        rng.uniform(0.6, 1.1), budget * 0.03, 0, [],
    ))

    champion = max(final_ads, key=lambda a: a["fitness"])
    for ad in final_ads:
        if ad["id"] == champion["id"]:
            ad["status"] = STATUS_CHAMPION
            ad["rationale"] = (
                f"REIGNING CHAMPION: peak fitness {ad['fitness']:.2f} — "
                f"{ad['ctr']:.2f}% CTR at {ad['roas']:.2f}x ROAS. Lineage "
                f"{' → '.join(ad['parents'] + [ad['id']])}; trend infusion “{phrase}”."
            )
        else:
            ad["status"] = STATUS_SURVIVOR
            ad["rationale"] = (
                f"Scale support: fitness {ad['fitness']:.2f} — held as diversification "
                f"against champion fatigue ({ad['roas']:.2f}x ROAS)."
            )
    generations.append({
        "gen": gen, "label": GEN_LABELS[gen], "budget": budget,
        "blended_roas": round(blended_roas(final_ads), 3),
        "ads": sorted(final_ads, key=lambda a: a["fitness"], reverse=True),
    })

    all_ads = [a for g in generations for a in g["ads"]]
    total_spend = sum(a["spend"] for a in all_ads)
    total_revenue = sum(a["revenue"] for a in all_ads)
    initial_roas = generations[0]["blended_roas"]
    final_roas = generations[3]["blended_roas"]

    return {
        "product": {
            "name": product_name, "category": category,
            "retail_price": price, "image_url": req.image_url,
            "description": description,
        },
        "integrations": {"tavily": t_mode, "zoowork": z_mode},
        "trends": trends,
        "pipeline": [
            "Tavily Recon", "ZooWork Decomposition", "Ad Network Simulation",
            "50% Pruning", "Crossover Alpha", "Champion Convergence",
        ],
        "generations": generations,
        "totals": {
            "spend": round(total_spend, 2),
            "revenue": round(total_revenue, 2),
            "profit": round(total_revenue - total_spend, 2),
            "initial_roas": round(initial_roas, 3),
            "final_roas": round(final_roas, 3),
            "uplift_pct": round((final_roas - initial_roas) / initial_roas * 100, 1) if initial_roas else 0,
        },
        "champion": champion,
        "disclaimer": "Simulated ad exchange — metrics are modeled, not live platform data.",
    }


# --------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------

app = FastAPI(title="EvoMarket", version="1.0.0")


@app.post("/api/optimize")
def optimize(req: OptimizeRequest) -> Dict[str, Any]:
    return run_evolution(req)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    tavily_ok, zoowork_ok = False, False
    if os.getenv("TAVILY_API_KEY"):
        try:
            import tavily  # type: ignore  # noqa
            tavily_ok = True
        except Exception:
            pass
    if os.getenv("ZOOWORK_API_KEY"):
        try:
            import zoowork  # type: ignore  # noqa
            zoowork_ok = True
        except Exception:
            pass
    return {
        "status": "ok",
        "tavily": "configured" if tavily_ok else "fallback",
        "zoowork": "configured" if zoowork_ok else "fallback",
    }


STATIC_DIR = os.path.join(BASE_DIR, "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

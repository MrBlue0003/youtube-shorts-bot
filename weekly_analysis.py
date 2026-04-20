"""
weekly_analysis.py — Analiza saptamanala a tuturor canalelor YouTube.

Ruleaza automat in fiecare luni si genereaza un raport cu:
- Crestere abonati / views fata de saptamana trecuta
- Potential de monetizare
- Recomandari de actiune
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scripts.upload import get_youtube_client

BASE_DIR = Path(__file__).parent
CHANNELS_FILE = BASE_DIR / "channels.json"
ANALYSIS_LOG = BASE_DIR / "logs" / "weekly_analysis.json"


# ── Monetization thresholds ────────────────────────────────────────────────────
# YouTube Partner Program requirements
YPP_SUBS = 1000
YPP_WATCH_HOURS = 4000       # for long videos
YPP_SHORTS_VIEWS = 10_000_000  # 10M Shorts views in 90 days (alternative)

# RPM estimates by niche ($ per 1000 views)
RPM_BY_NICHE = {
    "cute animals AI shorts": 0.04,
    "relaxing sounds / ASMR": 1.5,
    "kids content": 2.0,
    "top 10 facts": 4.0,
}


def get_channel_stats(yt, channel_id: str) -> dict:
    """Fetch current stats for a channel."""
    r = yt.channels().list(
        part="snippet,statistics",
        id=channel_id
    ).execute()
    items = r.get("items", [])
    if not items:
        return {}
    ch = items[0]
    stats = ch["statistics"]
    return {
        "subscribers": int(stats.get("subscriberCount", 0)),
        "views": int(stats.get("viewCount", 0)),
        "videos": int(stats.get("videoCount", 0)),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def get_recent_videos(yt, channel_id: str, max_results: int = 5) -> list:
    """Get the most recent videos with stats."""
    # Get uploads playlist
    r = yt.channels().list(part="contentDetails", id=channel_id).execute()
    items = r.get("items", [])
    if not items:
        return []
    uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # Get recent videos
    try:
        pl = yt.playlistItems().list(
            part="snippet",
            playlistId=uploads_id,
            maxResults=max_results
        ).execute()
    except Exception:
        return []

    video_ids = [item["snippet"]["resourceId"]["videoId"] for item in pl.get("items", [])]
    if not video_ids:
        return []

    vr = yt.videos().list(
        part="snippet,statistics",
        id=",".join(video_ids)
    ).execute()

    videos = []
    for v in vr.get("items", []):
        s = v["statistics"]
        videos.append({
            "title": v["snippet"]["title"],
            "video_id": v["id"],
            "views": int(s.get("viewCount", 0)),
            "likes": int(s.get("likeCount", 0)),
            "published": v["snippet"]["publishedAt"],
        })
    return videos


def monetization_score(channel: dict, stats: dict) -> dict:
    """Calculate monetization potential and recommendations."""
    subs = stats.get("subscribers", 0)
    views = stats.get("views", 0)
    videos = stats.get("videos", 0)
    niche = channel.get("niche", "")
    rpm = RPM_BY_NICHE.get(niche, 1.0)
    cost = channel.get("cost_per_month", 0)

    # Estimated monthly earnings (rough)
    monthly_views_est = (views / max(videos, 1)) * 30 if videos > 0 else 0
    monthly_revenue_est = (monthly_views_est / 1000) * rpm

    # Progress to monetization
    subs_pct = min(100, round(subs / YPP_SUBS * 100, 1))

    # Score 0-100
    score = min(100, int(
        (subs / YPP_SUBS * 40) +
        (min(views, 100000) / 100000 * 30) +
        (rpm / 10 * 20) +
        (min(videos, 50) / 50 * 10)
    ))

    # Recommendation
    if subs >= YPP_SUBS:
        recommendation = "MONETIZAT sau aproape! Creste frecventa uploadurilor."
    elif subs >= 500:
        recommendation = "Aproape de monetizare! Posteaza zilnic si promoveaza."
    elif subs >= 100:
        recommendation = "Crestere buna. Continua consistent, optimizeaza titluri."
    elif videos == 0:
        recommendation = "Canal gol - incepe sa postezi imediat!"
    else:
        recommendation = "Inceput. Posteaza consistent si creste volumul."

    return {
        "score": score,
        "subs_to_monetization": max(0, YPP_SUBS - subs),
        "subs_progress_pct": subs_pct,
        "estimated_monthly_revenue": round(monthly_revenue_est, 2),
        "rpm_estimate": rpm,
        "monthly_cost": cost,
        "net_monthly": round(monthly_revenue_est - cost, 2),
        "recommendation": recommendation,
    }


def run_analysis():
    with open(CHANNELS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    yt = get_youtube_client()

    print("=" * 65)
    print(f"  ANALIZA SAPTAMANALA CANALE YOUTUBE")
    print(f"  {datetime.now().strftime('%d %B %Y, %H:%M')}")
    print("=" * 65)

    results = []
    for channel in data["channels"]:
        print(f"\n>>> {channel['name'].upper()}")
        stats = get_channel_stats(yt, channel["id"])
        if not stats:
            print("  Canal negasit sau privat.")
            continue

        score_data = monetization_score(channel, stats)
        videos = get_recent_videos(yt, channel["id"])

        # Compare with last week
        prev_subs = 0
        prev_views = 0
        if ANALYSIS_LOG.exists():
            with open(ANALYSIS_LOG, encoding="utf-8") as f:
                log = json.load(f)
            prev = log.get("channels", {}).get(channel["id"], {})
            if prev:
                last = prev[-1] if isinstance(prev, list) else prev
                prev_subs = last.get("subscribers", 0)
                prev_views = last.get("views", 0)

        subs_growth = stats["subscribers"] - prev_subs
        views_growth = stats["views"] - prev_views

        print(f"  Abonati:     {stats['subscribers']:,}  ({'+' if subs_growth >= 0 else ''}{subs_growth} fata de saptamana trecuta)")
        print(f"  Views total: {stats['views']:,}  ({'+' if views_growth >= 0 else ''}{views_growth})")
        print(f"  Videoclipuri: {stats['videos']}")
        print(f"  Scor potential: {score_data['score']}/100")
        print(f"  Pana la monetizare: {score_data['subs_to_monetization']} abonati ({score_data['subs_progress_pct']}%)")
        print(f"  Venit estimat/luna: ${score_data['estimated_monthly_revenue']}")
        print(f"  Cost/luna: ${score_data['monthly_cost']}")
        print(f"  Net/luna: ${score_data['net_monthly']}")
        print(f"  Recomandare: {score_data['recommendation']}")

        if videos:
            safe_title = videos[0]['title'].encode('ascii', errors='replace').decode()
            print(f"  Top video recent: \"{safe_title}\" -- {videos[0]['views']} views")

        results.append({
            "channel_id": channel["id"],
            "name": channel["name"],
            "stats": stats,
            "score": score_data,
        })

    # Save to log
    ANALYSIS_LOG.parent.mkdir(exist_ok=True)
    log_data = {}
    if ANALYSIS_LOG.exists():
        with open(ANALYSIS_LOG, encoding="utf-8") as f:
            log_data = json.load(f)

    for r in results:
        cid = r["channel_id"]
        if cid not in log_data.get("channels", {}):
            log_data.setdefault("channels", {})[cid] = []
        log_data["channels"][cid].append(r["stats"])

    log_data["last_run"] = datetime.now(timezone.utc).isoformat()
    with open(ANALYSIS_LOG, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)

    # Summary ranking
    results.sort(key=lambda x: x["score"]["score"], reverse=True)
    print("\n" + "=" * 65)
    print("  RANKING POTENTIAL VENIT PASIV:")
    print("=" * 65)
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['name']} — Scor {r['score']['score']}/100 | Net/luna: ${r['score']['net_monthly']}")

    print("\n  Canal recomandat pentru investitie: " + results[0]["name"] if results else "N/A")
    print("=" * 65)


if __name__ == "__main__":
    run_analysis()

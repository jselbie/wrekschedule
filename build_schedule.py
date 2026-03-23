#!/usr/bin/env python3
"""WREK schedule crawler.

Fetches https://www.wrek.org/schedule and generates a JSON file matching
the schedule-schema.json format.

Usage:
    python build_schedule.py [--mapping mapping.json] [--output schedule.json]

Requirements:
    pip install requests beautifulsoup4
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, time as dt_time

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEDULE_URL = "https://www.wrek.org/schedule"

# Three-letter day abbreviations used in archive MP3 URLs
DAY_ABBREV = {
    "mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu",
    "fri": "Fri", "sat": "Sat", "sun": "Sun",
}

# Python weekday numbers (Monday = 0)
DAY_WEEKDAY = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
}

# Block shows that appear exactly once in the output, in this order.
# Key is the URL slug; these are matched against slugs scraped from the page.
BLOCK_SHOW_SLUGS_ORDERED = [
    "atmospherics",
    "classics",
    "just-jazz",
    "rrr",
    "electronic",
    "overnight-alternatives",
]

WEEKEND_WINDDOWN_SLUG = "weekend-winddown"

# Shows that are scraped from the page but must never appear in the output.
EXCLUDED_SLUGS = {
    "tech-nation",
    "planetary-radio",
    "the-best-of-our-knowledge",
    "this-way-out",
    "51-percent",
    "cambridge-forum",
    "alternative-radio",
    "between-the-lines"
}

# Minimal definitions for the two live streams.  make_live_show() assembles
# each one into the same shape as the archived show dicts.
LIVE_STREAM_DEFS = [
    {
        "id": "live-air-stream",
        "title": "Live Air Stream",
        "description": "",
        "stream_base": "https://streaming.wrek.org/main",
        "logoUrl": "https://www.selbie.com/wrek/radiodial.jpg",
        "logoBlurHash": "LKO2?V%2Tw=w]~RBVZRi};RPxuwH",
        "albumCoverUrl" : "https://www.selbie.com/wrek/911fm_450.png"
    },
    {
        "id": "hd2-subchannel",
        "title": "HD2 Subchannel",
        "description": "Alternative programming",
        "stream_base": "https://streaming.wrek.org/hd2",
        "logoUrl": "https://www.selbie.com/wrek/hd2_new.jpg",
        "logoBlurHash": "LEHV6nWB2yk8pyo0adR*.7kCMdnj",
        "albumCoverUrl" : "https://www.selbie.com/wrek/hd2_450.png"
    },
]

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def slug_to_title(slug: str) -> str:
    """Prettify a URL slug as a fallback display title.

    E.g. 'rock-rhythm-and-roll' -> 'Rock Rhythm And Roll'
    """
    return " ".join(word.capitalize() for word in slug.replace("-", " ").split())


def title_to_snake(title: str) -> str:
    """Convert a display title to snake_case for use in show IDs.

    E.g. 'Rock, Rhythm, and Roll' -> 'rock_rhythm_and_roll'
    """
    lowered = title.lower()
    snaked = re.sub(r"[^a-z0-9]+", "_", lowered)
    return snaked.strip("_")


def parse_time_str(time_str: str) -> dt_time:
    """Parse a 12-hour time string like '3:00 PM' or '12:00 AM'."""
    return datetime.strptime(time_str.strip(), "%I:%M %p").time()


def infer_show_date(day_name: str, show_time: dt_time, now: datetime) -> datetime:
    """Return the most recent naive datetime for (day_name, show_time) that is <= now.

    """
    target_weekday = DAY_WEEKDAY[day_name]
    days_back = (now.weekday() - target_weekday) % 7
    candidate_date = (now - timedelta(days=days_back)).date()
    candidate_dt = datetime.combine(candidate_date, show_time)
    if candidate_dt > now:
        candidate_dt -= timedelta(weeks=1)
    return candidate_dt


def generate_playlist(day_name: str, start_dt: datetime, end_dt: datetime,
                       bitrate: int) -> list:
    """Build archive MP3 URLs for every 30-minute segment in [start_dt, end_dt).

    The day abbreviation in each URL is taken from day_name (the schedule column
    header), not from start_dt's calendar weekday.  This is intentional: the
    archive names files by day-of-week, not by calendar date.
    """
    abbrev = DAY_ABBREV[day_name]
    urls = []
    current = start_dt
    while current < end_dt:
        urls.append(
            f"https://archive.wrek.org/main/{bitrate}kb/"
            f"{abbrev}{current.strftime('%H%M')}.mp3"
        )
        current += timedelta(minutes=30)
    return urls


# ---------------------------------------------------------------------------
# Fetching the schedule page
# ---------------------------------------------------------------------------

def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# Parsing the schedule
# ---------------------------------------------------------------------------

def parse_static_slots(html: str) -> list:
    """Extract (day, time_str, slug) for every show slot from the static HTML.

    The static HTML has times and href slugs but not display titles.
    Returns entries in document order: Mon -> Sun, top -> bottom within each day.
    """
    soup = BeautifulSoup(html, "html.parser")
    slots = []
    for container in soup.find_all(class_="dayContainer"):
        header = container.find(class_="dayHeader")
        if not header:
            continue
        day_name = header.get_text(strip=True).lower()
        if day_name not in DAY_WEEKDAY:
            continue
        for anchor in container.find_all("a", href=True):
            href = anchor["href"]
            if not href.startswith("/shows/"):
                continue
            slug = href[len("/shows/"):]
            entry = anchor.find(class_="scheduleEntry")
            if not entry:
                continue
            # The time is in the first <span> inside the scheduleEntry
            span = entry.find("span")
            if not span:
                continue
            slots.append({
                "day": day_name,
                "time_str": span.get_text(strip=True),
                "slug": slug,
            })
    return slots


def extract_titles_from_rsc(html: str) -> list:
    """Extract show display names from the Next.js RSC payload.

    The RSC payload is embedded in <script> tags as self.__next_f.push() calls.
    Within that payload the JSON is double-encoded, so actual quote characters
    appear as the two-character sequence backslash + quote (\").

    Returns titles in document order, which matches the order from
    parse_static_slots() since both come from the same server render.
    """
    soup = BeautifulSoup(html, "html.parser")
    rsc_chunks = []
    for script in soup.find_all("script"):
        text = script.string or ""
        if "__next_f" in text:
            rsc_chunks.append(text)
    combined = "\n".join(rsc_chunks)

    # In the raw script text, encoded JSON looks like:
    #   \"className\":\"whitespace-pre scheduleShowName\",\"children\":\"|  Atmospherics  \"
    # The regex below matches scheduleShowName followed by the children value.
    # \\" in a raw string = regex \\" = matches the literal two-char sequence \"
    pattern = re.compile(
        r'scheduleShowName\\",\\"children\\":\\"([^"\\]+)\\"'
    )
    titles = []
    for match in pattern.finditer(combined):
        raw = match.group(1)
        # Strip the leading "| " decoration and surrounding whitespace
        title = re.sub(r"^\|\s*", "", raw).strip()
        if title:
            titles.append(title)
    return titles


def merge_slots_and_titles(slots: list, titles: list) -> list:
    """Combine slot dicts with extracted titles using positional alignment.

    Falls back to slug_to_title() for any slot whose position has no
    corresponding title (e.g. if RSC extraction yielded fewer entries).
    """
    result = []
    for i, slot in enumerate(slots):
        title = titles[i] if i < len(titles) else slug_to_title(slot["slug"])
        result.append({**slot, "title": title})
    return result


# ---------------------------------------------------------------------------
# Show assembly helpers
# ---------------------------------------------------------------------------

def compute_end_times(slots: list) -> None:
    """Mutate each slot dict to add an 'end_dt' key.

    The end time of show[i] is the start time of show[i+1] within the same
    day column.  The last show of each day ends at midnight (start of the
    following calendar day).
    """
    # Build per-day lists, preserving the document order within each day
    day_slots: dict = {}
    for slot in slots:
        day_slots.setdefault(slot["day"], []).append(slot)

    for day_list in day_slots.values():
        for i, slot in enumerate(day_list):
            if i + 1 < len(day_list):
                # Use the next slot's time-of-day on the current slot's date.
                # infer_show_date() may assign different calendar weeks to
                # shows on the same day-of-week, so we can't use the next
                # slot's full datetime directly.
                next_time = day_list[i + 1]["start_dt"].time()
                next_dt = datetime.combine(slot["start_dt"].date(), next_time)
                # If next show's time-of-day is earlier (e.g. wraps past
                # midnight), push to the following calendar day.
                if next_dt <= slot["start_dt"]:
                    next_dt += timedelta(days=1)
                slot["end_dt"] = next_dt
            else:
                # Last show of the day: ends at midnight of the next calendar day
                slot["end_dt"] = datetime.combine(
                    slot["start_dt"].date() + timedelta(days=1), dt_time(0, 0)
                )


def pick_best_block_instance(instances: list, now: datetime):
    """Return the block-show instance with the latest start_dt whose end_dt <= now.

    Falls back to the latest-started instance if none has fully aired yet
    (edge case: script runs before any airing of this show is complete).
    """
    complete = [s for s in instances if s["end_dt"] <= now]
    if complete:
        return max(complete, key=lambda s: s["start_dt"])
    return max(instances, key=lambda s: s["start_dt"]) if instances else None


def make_live_show(defn: dict) -> dict:
    """Build a live-stream show dict from a LIVE_STREAM_DEFS entry."""
    return {
        "id": defn["id"],
        "title": defn["title"],
        "description": defn["description"],
        "creationTime": None,
        "streams": [
            {
                "bitrate": bitrate,
                "playlist": [f"{defn['stream_base']}/{bitrate}kb.mp3"],
                "isLiveStream": True,
            }
            for bitrate in (128, 320)
        ],
        "logoUrl": defn["logoUrl"],
        "logoBlurHash": defn["logoBlurHash"],
        "albumCoverUrl": defn["albumCoverUrl"],
    }


def build_streams(day_name: str, start_dt: datetime, end_dt: datetime) -> list:
    return [
        {
            "bitrate": bitrate,
            "playlist": generate_playlist(day_name, start_dt, end_dt, bitrate),
            "isLiveStream": False,
        }
        for bitrate in (128, 320)
    ]


def make_show_dict(slug: str, title: str, day: str, start_dt: datetime,
                   end_dt: datetime, mapping: dict) -> dict:
    """Build a complete show dict.  id is set to None; caller assigns it later.

    Private keys prefixed with '_' are used for sorting and are stripped before
    final output.
    """
    info = mapping.get(slug, {})
    return {
        "id": None,
        "title": title,
        "description": info.get("description", ""),
        "creationTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "streams": build_streams(day, start_dt, end_dt),
        "logoUrl": info.get("logoUrl", None),
        "logoBlurHash": info.get("logoBlurHash", None),
        # Private sort helpers — removed before writing JSON
        "_day": day,
        "_start_dt": start_dt,
    }


def assign_ids(entries: list, start_seq: int = 1) -> int:
    """Assign snake_case_N IDs to entries in place.  Returns next sequence number."""
    seq = start_seq
    for entry in entries:
        entry["id"] = f"{title_to_snake(entry['title'])}_{seq}"
        seq += 1
    return seq


def strip_private_keys(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build WREK weekly schedule JSON.")
    parser.add_argument("--mapping", default="mapping.json",
                        help="Path to the show-metadata mapping file (default: mapping.json)")
    parser.add_argument("--output", default="schedule.json",
                        help="Path for the generated JSON output (default: schedule.json)")
    args = parser.parse_args()

    # Load the show-metadata mapping
    with open(args.mapping, encoding="utf-8") as f:
        mapping = json.load(f)

    # ---- Step 1: Fetch and parse the schedule page ----
    print("Fetching https://www.wrek.org/schedule ...", file=sys.stderr)
    html = fetch_html(SCHEDULE_URL)

    slots = parse_static_slots(html)
    print(f"  {len(slots)} show slots found in static HTML", file=sys.stderr)

    titles = extract_titles_from_rsc(html)
    print(f"  {len(titles)} titles extracted from RSC payload", file=sys.stderr)

    if len(titles) < len(slots):
        missing = len(slots) - len(titles)
        print(
            f"  Warning: {missing} slot(s) have no RSC title; "
            f"falling back to slug-derived names for those",
            file=sys.stderr,
        )

    slots = merge_slots_and_titles(slots, titles)

    # ---- Step 2: Compute start and end datetimes for every slot ----
    now = datetime.now()
    print(f"  Reference time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}", file=sys.stderr)

    for slot in slots:
        slot["start_dt"] = infer_show_date(
            slot["day"], parse_time_str(slot["time_str"]), now
        )

    compute_end_times(slots)

    # ---- Step 3: Partition slots into categories ----
    block_set = set(BLOCK_SHOW_SLUGS_ORDERED)
    block_instances: dict = {slug: [] for slug in BLOCK_SHOW_SLUGS_ORDERED}
    winddown_slots = []
    regular_slots = []

    for slot in slots:
        slug = slot["slug"]
        if slug in EXCLUDED_SLUGS:
            continue
        elif slug in block_set:
            block_instances[slug].append(slot)
        elif slug == WEEKEND_WINDDOWN_SLUG:
            winddown_slots.append(slot)
        else:
            regular_slots.append(slot)

    # ---- Step 4: Build block-show entries (one per show, latest complete airing) ----
    block_entries = []
    for slug in BLOCK_SHOW_SLUGS_ORDERED:
        best = pick_best_block_instance(block_instances[slug], now)
        if best is None:
            print(
                f"  Warning: no instances found for block show '{slug}' — skipping",
                file=sys.stderr,
            )
            continue
        block_entries.append(
            make_show_dict(slug, best["title"], best["day"],
                           best["start_dt"], best["end_dt"], mapping)
        )

    # ---- Step 5: Build Weekend Winddown (coalesced from all Sunday slots) ----
    winddown_entry = None
    if winddown_slots:
        winddown_slots.sort(key=lambda s: s["start_dt"])
        first = winddown_slots[0]
        info = mapping.get(WEEKEND_WINDDOWN_SLUG, {})
        winddown_entry = {
            "id": None,
            "title": first["title"],
            "description": info.get("description", ""),
            "creationTime": first["start_dt"].strftime("%Y-%m-%dT%H:%M:%S"),
            "streams": [
                {
                    "bitrate": bitrate,
                    "playlist": [
                        url
                        for inst in winddown_slots
                        for url in generate_playlist(
                            inst["day"], inst["start_dt"], inst["end_dt"], bitrate
                        )
                    ],
                    "isLiveStream": False,
                }
                for bitrate in (128, 320)
            ],
            "logoUrl": info.get("logoUrl", None),
            "logoBlurHash": info.get("logoBlurHash", None),
            "_day": first["day"],
            "_start_dt": first["start_dt"],
        }

    # ---- Step 6: Build regular show entries ----
    regular_entries = []
    for slot in regular_slots:
        regular_entries.append(
            make_show_dict(slot["slug"], slot["title"], slot["day"],
                           slot["start_dt"], slot["end_dt"], mapping)
        )

    if winddown_entry:
        regular_entries.append(winddown_entry)

    # Sort Mon -> Sun, then by time-of-day within each day.
    # We use .time() rather than the full datetime because infer_show_date()
    # may assign different calendar weeks to shows on the same day-of-week
    # (depending on whether each show has aired yet relative to "now").
    day_order = list(DAY_WEEKDAY.keys())  # preserves mon, tue, ..., sun order
    regular_entries.sort(
        key=lambda e: (day_order.index(e["_day"]), e["_start_dt"].time())
    )

    # ---- Step 7: Assign sequential IDs ----
    # Block shows are numbered first (1, 2, ...), regular shows continue the sequence.
    seq = assign_ids(block_entries, start_seq=1)
    assign_ids(regular_entries, start_seq=seq)

    # ---- Step 8: Assemble and write output ----
    live_shows = [make_live_show(d) for d in LIVE_STREAM_DEFS]

    schedule = (
        live_shows
        + [strip_private_keys(e) for e in block_entries]
        + [strip_private_keys(e) for e in regular_entries]
    )

    output = {"schedule": schedule}
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    total = len(schedule)
    print(
        f"Wrote {total} shows ({len(live_shows)} live, "
        f"{len(block_entries)} block, {len(regular_entries)} regular) "
        f"-> {args.output}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

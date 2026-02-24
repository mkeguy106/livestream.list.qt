"""Social media link fetching for chat channel banners."""

import json
import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp

from ..core.models import StreamPlatform

logger = logging.getLogger(__name__)


def _normalize_social_name(name: str) -> str:
    """Normalize social media platform name."""
    name_map = {
        "twitter": "twitter",
        "x": "twitter",
        "instagram": "instagram",
        "youtube": "youtube",
        "tiktok": "tiktok",
        "facebook": "facebook",
        "discord": "discord",
        "patreon": "patreon",
    }
    return name_map.get(name.lower(), name.lower())


def _detect_social_from_url(url: str) -> str | None:
    """Detect social media platform from URL."""
    url_lower = url.lower()
    if "twitter.com" in url_lower or "x.com" in url_lower:
        return "twitter"
    elif "instagram.com" in url_lower:
        return "instagram"
    elif "tiktok.com" in url_lower:
        return "tiktok"
    elif "discord.gg" in url_lower or "discord.com" in url_lower:
        return "discord"
    elif "facebook.com" in url_lower:
        return "facebook"
    elif "patreon.com" in url_lower:
        return "patreon"
    elif "twitch.tv" in url_lower:
        return "twitch"
    elif "kick.com" in url_lower:
        return "kick"
    elif "youtube.com" in url_lower:
        return "youtube"
    return None


def _detect_social_from_title(title: str) -> str | None:
    """Detect social media platform from link title."""
    title_lower = title.lower()
    title_map = {
        "twitter": "twitter",
        "x": "twitter",
        "instagram": "instagram",
        "tiktok": "tiktok",
        "discord": "discord",
        "facebook": "facebook",
        "patreon": "patreon",
        "kick": "kick",
        "twitch": "twitch",
        "website": "website",
        "youtube": "youtube",
        "second channel": "youtube2",
        "clips": "youtube_clips",
    }
    for keyword, platform in title_map.items():
        if keyword in title_lower:
            return platform
    return None


async def _fetch_twitch_socials(channel_id: str) -> dict[str, str]:
    """Fetch socials from Twitch GQL channel socialMedias."""
    socials: dict[str, str] = {}

    gql_url = "https://gql.twitch.tv/gql"
    headers = {
        "Client-Id": "kimne78kx3ncx6brgo4mv6wki5h1ko",
        "Content-Type": "application/json",
    }

    query = {
        "query": """
            query UserSocials($login: String!) {
                user(login: $login) {
                    channel {
                        socialMedias {
                            name
                            url
                        }
                    }
                }
            }
            """,
        "variables": {"login": channel_id},
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                gql_url,
                json=query,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return socials

                data = await resp.json()
                user = data.get("data", {}).get("user")
                if not user:
                    return socials

                channel = user.get("channel", {})
                social_medias = channel.get("socialMedias", []) if channel else []

                for social in social_medias:
                    name = (social.get("name") or "").lower()
                    url = social.get("url") or ""
                    if url:
                        standard_name = _normalize_social_name(name)
                        if standard_name not in socials:
                            socials[standard_name] = url

    except Exception as e:
        logger.debug(f"Twitch GQL socials query failed: {e}")

    return socials


async def _fetch_youtube_socials(channel_id: str) -> dict[str, str]:
    """Fetch socials from YouTube channel about page."""
    socials: dict[str, str] = {}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    # YouTube channel ID might be @handle or UCxxxx format
    # For UC IDs, we need to use /channel/UC.../about format
    channel_path = channel_id
    if channel_path.startswith("UC"):
        url = f"https://www.youtube.com/channel/{channel_path}/about"
    elif channel_path.startswith("@"):
        url = f"https://www.youtube.com/{channel_path}/about"
    else:
        url = f"https://www.youtube.com/@{channel_path}/about"

    logger.debug(f"Fetching YouTube socials from: {url}")

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                logger.debug(f"YouTube about page status: {resp.status}")
                if resp.status != 200:
                    return socials

                html = await resp.text()
                logger.debug(f"YouTube HTML length: {len(html)}")

                # Extract ytInitialData JSON
                match = re.search(r"var ytInitialData\s*=\s*({.+?});</script>", html, re.DOTALL)
                if not match:
                    logger.warning("Could not find ytInitialData in YouTube page")
                    return socials

                data = json.loads(match.group(1))
                logger.debug(f"ytInitialData keys: {list(data.keys())}")

                # Navigate to about channel links
                endpoints = data.get("onResponseReceivedEndpoints", [])
                logger.debug(f"YouTube onResponseReceivedEndpoints count: {len(endpoints)}")

                # If no endpoints, try alternative path via tabs
                if not endpoints:
                    # Try contents.twoColumnBrowseResultsRenderer.tabs
                    tabs = (
                        data.get("contents", {})
                        .get("twoColumnBrowseResultsRenderer", {})
                        .get("tabs", [])
                    )
                    logger.debug(f"Trying tabs path, found {len(tabs)} tabs")
                    for tab in tabs:
                        tab_content = (
                            tab.get("tabRenderer", {})
                            .get("content", {})
                            .get("sectionListRenderer", {})
                            .get("contents", [])
                        )
                        for section in tab_content:
                            about = (
                                section.get("itemSectionRenderer", {})
                                .get("contents", [{}])[0]
                                .get("channelAboutFullMetadataRenderer", {})
                            )
                            if about:
                                logger.debug("Found channelAboutFullMetadataRenderer")
                                links = about.get("primaryLinks", [])
                                for link in links:
                                    title = link.get("title", {}).get("simpleText", "") or link.get(
                                        "title", {}
                                    ).get("runs", [{}])[0].get("text", "")
                                    nav = link.get("navigationEndpoint", {})
                                    url_ep = nav.get("urlEndpoint", {})
                                    redirect_url = url_ep.get("url", "")
                                    if redirect_url and "q=" in redirect_url:
                                        actual_url = unquote(redirect_url.split("q=")[-1])
                                        name = _detect_social_from_url(actual_url)
                                        if not name:
                                            name = _detect_social_from_title(title)
                                        if name and name not in socials:
                                            socials[name] = actual_url
                                            logger.debug(f"Found social (tabs): {name}")

                for endpoint in endpoints:
                    panel = (
                        endpoint.get("showEngagementPanelEndpoint", {})
                        .get("engagementPanel", {})
                        .get("engagementPanelSectionListRenderer", {})
                        .get("content", {})
                        .get("sectionListRenderer", {})
                        .get("contents", [])
                    )
                    logger.debug(f"YouTube panel contents count: {len(panel)}")
                    for section in panel:
                        about = (
                            section.get("itemSectionRenderer", {})
                            .get("contents", [{}])[0]
                            .get("aboutChannelRenderer", {})
                            .get("metadata", {})
                            .get("aboutChannelViewModel", {})
                        )
                        links = about.get("links", [])
                        if about:
                            logger.debug(f"Found aboutChannelViewModel with {len(links)} links")
                        for link in links:
                            link_vm = link.get("channelExternalLinkViewModel", {})
                            title = link_vm.get("title", {}).get("content", "")
                            link_data = link_vm.get("link", {})
                            display_url = link_data.get("content", "")

                            # Get actual URL from redirect
                            actual_url = ""
                            runs = link_data.get("commandRuns", [])
                            for run in runs:
                                innertube = run.get("onTap", {}).get("innertubeCommand", {})
                                web_cmd = innertube.get("commandMetadata", {}).get(
                                    "webCommandMetadata", {}
                                )
                                redirect_url = web_cmd.get("url", "")
                                if redirect_url:
                                    parsed = urlparse(redirect_url)
                                    qs = parse_qs(parsed.query)
                                    if "q" in qs:
                                        # External link with redirect
                                        actual_url = qs["q"][0]
                                    elif parsed.path and not parsed.path.startswith("/redirect"):
                                        # Internal YouTube link (direct URL)
                                        actual_url = redirect_url
                                    break

                            # Use actual URL if available, else construct from display
                            final_url = actual_url
                            if not final_url and display_url:
                                if not display_url.startswith("http"):
                                    final_url = f"https://{display_url}"
                                else:
                                    final_url = display_url

                            if not final_url:
                                continue

                            logger.debug(f"YouTube link: {title} -> {final_url}")

                            # Detect social from URL or title
                            name = _detect_social_from_url(final_url)
                            if not name:
                                name = _detect_social_from_title(title)
                            if name and name not in socials:
                                socials[name] = final_url

    except Exception as e:
        logger.debug(f"YouTube socials fetch failed: {e}")

    return socials


async def _fetch_kick_socials(channel_id: str) -> dict[str, str]:
    """Fetch socials from Kick channel API."""
    socials: dict[str, str] = {}

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    url = f"https://kick.com/api/v2/channels/{channel_id}"

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return socials

                data = await resp.json()
                user = data.get("user", {})

                # Kick stores socials as usernames, need to construct URLs
                social_fields = {
                    "twitter": "https://twitter.com/{}",
                    "instagram": "https://instagram.com/{}",
                    "youtube": "https://youtube.com/{}",
                    "discord": "https://discord.gg/{}",
                    "tiktok": "https://tiktok.com/@{}",
                    "facebook": "https://facebook.com/{}",
                }

                for field, url_template in social_fields.items():
                    value = user.get(field)
                    if value:
                        # Clean up the value (remove trailing slashes, etc.)
                        value = value.strip().rstrip("/")
                        if value:
                            socials[field] = url_template.format(value)

    except Exception as e:
        logger.debug(f"Kick socials fetch failed: {e}")

    return socials


async def fetch_socials(channel_id: str, platform: StreamPlatform) -> dict[str, str]:
    """Fetch social media links for a channel.

    Returns a dict of {platform_name: url}.
    """
    if platform == StreamPlatform.TWITCH:
        return await _fetch_twitch_socials(channel_id)
    elif platform == StreamPlatform.YOUTUBE:
        return await _fetch_youtube_socials(channel_id)
    elif platform == StreamPlatform.KICK:
        return await _fetch_kick_socials(channel_id)
    return {}

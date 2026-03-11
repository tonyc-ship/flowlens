"""Xiaohongshu (小红书) Site Skill — state machine architecture.

Encodes XHS-specific knowledge as page states, transitions, and extraction
rules. NO pixel heuristics — all element location is delegated to grounding
models, and all page understanding to LLMs.

State machine:
    homepage → search_results → note_detail → image_carousel
                                            → comments_section
                                            → profile_page
"""

from __future__ import annotations

from .base import ExtractionRule, PageState, SiteSkill, Transition


class XiaohongshuSkill(SiteSkill):
    """Site skill for Xiaohongshu (xiaohongshu.com)."""

    name = "xiaohongshu"
    site_url = "https://www.xiaohongshu.com"

    def get_states(self) -> dict[str, PageState]:
        return {
            "homepage": PageState(
                name="homepage",
                description=(
                    "The XHS homepage/explore page. Shows a grid of recommended "
                    "note cards with images, titles, and author names. Has a left "
                    "sidebar with navigation (首页/发现/发布/通知/我) and a search "
                    "bar at the top center. The XHS red logo is in the top-left."
                ),
                transitions={
                    "search": Transition(
                        name="search",
                        description="Click the search box and type a query to search",
                        target_state="search_results",
                        grounding_query="the search input box at the top center of the page",
                        action_type="click_and_type",
                    ),
                    "open_note": Transition(
                        name="open_note",
                        description="Click on a note card to view its details",
                        target_state="note_detail",
                        grounding_query="the {target} note card in the grid",
                        action_type="click",
                    ),
                },
                extraction_rules={
                    "cards": ExtractionRule(
                        prompt=(
                            "List ALL visible note cards on this Xiaohongshu page. "
                            "For each card, extract:\n"
                            "- title: the text below the image\n"
                            "- author: the username shown\n"
                            "- likes: the like/heart count\n"
                            "- thumbnail_desc: brief description of the card image\n\n"
                            "Return valid JSON array: "
                            '[{"title": str, "author": str, "likes": str, "thumbnail_desc": str}]'
                        ),
                        schema={"cards": "list[{title, author, likes, thumbnail_desc}]"},
                    ),
                },
            ),
            "search_results": PageState(
                name="search_results",
                description=(
                    "XHS search results page. Has the search query visible in the "
                    "search bar at top. Below the search bar are filter tabs "
                    "(全部/图文/视频/用户). The main area shows a waterfall grid of "
                    "note cards matching the search query. Left sidebar with navigation."
                ),
                transitions={
                    "open_note": Transition(
                        name="open_note",
                        description="Click on a note card to view its details (opens as overlay modal)",
                        target_state="note_detail",
                        grounding_query="the {target} note card in the search results",
                        action_type="click",
                    ),
                    "scroll_more": Transition(
                        name="scroll_more",
                        description="Scroll down to load more search results",
                        target_state="search_results",
                        action_type="scroll",
                        action_params={"direction": "down", "amount": 5},
                    ),
                    "refine_search": Transition(
                        name="refine_search",
                        description="Click the search box and change the query",
                        target_state="search_results",
                        grounding_query="the search input box with the current query",
                        action_type="click_and_type",
                    ),
                    "filter_tab": Transition(
                        name="filter_tab",
                        description="Click a filter tab (图文/视频/用户) to filter results",
                        target_state="search_results",
                        grounding_query="the '{target}' filter tab below the search bar",
                        action_type="click",
                    ),
                },
                extraction_rules={
                    "cards": ExtractionRule(
                        prompt=(
                            "List ALL visible note cards in the search results. "
                            "For each card, extract:\n"
                            "- title: the text below the image\n"
                            "- author: the username with avatar\n"
                            "- likes: the heart/like count\n"
                            "- thumbnail_desc: brief description of the card image\n"
                            "- position: approximate grid position (row, column) starting from 1\n\n"
                            "Return valid JSON array: "
                            '[{"title": str, "author": str, "likes": str, '
                            '"thumbnail_desc": str, "position": [row, col]}]'
                        ),
                        schema={"cards": "list"},
                    ),
                    "search_info": ExtractionRule(
                        prompt=(
                            "Extract the search context:\n"
                            "- query: the text in the search bar\n"
                            "- active_tab: which filter tab is selected (全部/图文/视频/用户)\n"
                            "- location_tabs: any location/category tabs visible below\n\n"
                            "Return valid JSON: "
                            '{"query": str, "active_tab": str, "location_tabs": [str]}'
                        ),
                    ),
                },
            ),
            "note_detail": PageState(
                name="note_detail",
                description=(
                    "XHS note detail view. Can appear as:\n"
                    "1. Modal overlay: dark semi-transparent background with white modal. "
                    "Left panel shows the note's image/carousel, right panel shows "
                    "author info, text content, hashtags, and comments.\n"
                    "2. Full-page view: the note takes up the whole page with the image "
                    "on the left side and content + recommendations on the right.\n\n"
                    "Key elements: author avatar and name at top, image with possible "
                    "carousel arrows (left/right), text content with hashtags, "
                    "engagement bar at bottom (likes ❤️, favorites ⭐, comments 💬, share)."
                ),
                transitions={
                    "next_image": Transition(
                        name="next_image",
                        description="Press right arrow key to see the next image in the carousel (arrows only visible on hover, so use keyboard)",
                        target_state="note_detail",
                        action_type="press_key",
                        action_params={"key": "right"},
                    ),
                    "prev_image": Transition(
                        name="prev_image",
                        description="Press left arrow key to see the previous image",
                        target_state="note_detail",
                        action_type="press_key",
                        action_params={"key": "left"},
                    ),
                    "scroll_to_comments": Transition(
                        name="scroll_to_comments",
                        description="Scroll down in the right panel to see more comments",
                        target_state="note_detail",
                        action_type="scroll",
                        action_params={"direction": "down", "amount": 3},
                    ),
                    "close_note": Transition(
                        name="close_note",
                        description="Close the note detail (press Escape or click the X button)",
                        target_state="search_results",
                        grounding_query="the close button (X) in the top-right corner of the modal",
                        action_type="press_key",
                        action_params={"key": "escape"},
                    ),
                    "open_author_profile": Transition(
                        name="open_author_profile",
                        description="Click the author's name or avatar to view their profile",
                        target_state="profile_page",
                        grounding_query="the author's username or profile picture at the top of the note",
                        action_type="click",
                    ),
                    "click_follow": Transition(
                        name="click_follow",
                        description="Click the follow button next to the author name",
                        target_state="note_detail",
                        grounding_query="the red '关注' (follow) button next to the author name",
                        action_type="click",
                    ),
                },
                extraction_rules={
                    "note_content": ExtractionRule(
                        prompt=(
                            "Extract ALL content from this Xiaohongshu note detail page:\n"
                            "- title: the note title (usually bold/large text)\n"
                            "- author: the author's display name\n"
                            "- content: the FULL text body (preserve line breaks and emojis)\n"
                            "- hashtags: all hashtags (starting with #)\n"
                            "- date: the publication date\n"
                            "- image_indicator: the image carousel indicator (e.g. '2/5' meaning image 2 of 5), or null if only one image\n"
                            "- likes: the like/heart count\n"
                            "- favorites: the favorite/star count\n"
                            "- comments_count: the comment count\n"
                            "- shares: the share count (if visible)\n\n"
                            "Return valid JSON:\n"
                            '{"title": str, "author": str, "content": str, '
                            '"hashtags": [str], "date": str, "image_indicator": str|null, '
                            '"likes": str, "favorites": str, "comments_count": str, "shares": str|null}'
                        ),
                        schema={
                            "title": "str", "author": "str", "content": "str",
                            "hashtags": "list[str]", "date": "str",
                            "image_indicator": "str|null",
                            "likes": "str", "favorites": "str",
                            "comments_count": "str", "shares": "str|null",
                        },
                    ),
                    "comments": ExtractionRule(
                        prompt=(
                            "Extract ALL visible comments from this Xiaohongshu note page. "
                            "For each comment:\n"
                            "- username: the commenter's display name\n"
                            "- text: the full comment text\n"
                            "- likes: the like count on the comment\n"
                            "- is_author_reply: whether this is a reply from the post author\n"
                            "- time: the comment timestamp if visible\n\n"
                            "Return valid JSON array:\n"
                            '[{"username": str, "text": str, "likes": str, '
                            '"is_author_reply": bool, "time": str|null}]'
                        ),
                        schema={"comments": "list"},
                    ),
                    "image_description": ExtractionRule(
                        prompt=(
                            "Describe the image currently displayed in this note. "
                            "Focus on: what's shown, any text/watermarks visible in the image, "
                            "the overall mood/style. Also note the carousel position if visible "
                            "(e.g., '2/5' means image 2 of 5).\n\n"
                            "Return valid JSON:\n"
                            '{"description": str, "text_in_image": [str], '
                            '"carousel_position": str|null, "total_images": int|null}'
                        ),
                    ),
                },
            ),
            "profile_page": PageState(
                name="profile_page",
                description=(
                    "An XHS user's profile page. Shows the user's avatar, display name, "
                    "XHS ID, bio/description, follower/following counts. Below is a grid "
                    "of their published notes."
                ),
                transitions={
                    "open_note": Transition(
                        name="open_note",
                        description="Click on one of the user's notes",
                        target_state="note_detail",
                        grounding_query="the {target} note card in the user's profile grid",
                        action_type="click",
                    ),
                    "go_back": Transition(
                        name="go_back",
                        description="Go back to previous page",
                        target_state="search_results",
                        action_type="press_key",
                        action_params={"key": "browserback"},
                    ),
                },
                extraction_rules={
                    "profile_info": ExtractionRule(
                        prompt=(
                            "Extract the user's profile information:\n"
                            "- display_name: their display name\n"
                            "- xhs_id: their Xiaohongshu ID\n"
                            "- bio: their bio/description text\n"
                            "- followers: follower count\n"
                            "- following: following count\n"
                            "- likes_received: total likes received\n"
                            "- notes_count: number of notes published\n\n"
                            "Return valid JSON:\n"
                            '{"display_name": str, "xhs_id": str, "bio": str, '
                            '"followers": str, "following": str, "likes_received": str, '
                            '"notes_count": str}'
                        ),
                    ),
                },
            ),
            "unknown": PageState(
                name="unknown",
                description="Unrecognized page state — not clearly one of the known XHS page types.",
                transitions={},
                extraction_rules={
                    "general": ExtractionRule(
                        prompt=(
                            "Describe this page in detail: what website/app is this, "
                            "what content is shown, what interactive elements are visible?"
                        ),
                    ),
                },
            ),
        }

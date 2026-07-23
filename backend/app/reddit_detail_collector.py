import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from playwright.sync_api import Page


@dataclass
class PostDetailObservation:
    url: str
    origin: str
    subreddit: str
    title: str
    author: str
    flair: str
    post_type: str
    body_text: str
    body_length: int
    upvotes: int | None
    comments: int | None
    outbound_url: str = ""
    media_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LoadedCommentNode:
    comment_id: str
    comment_url: str
    author: str
    is_op: bool
    depth: int
    score: int | None
    text: str
    text_length: int
    replies: list["LoadedCommentNode"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "comment_id": self.comment_id,
            "comment_url": self.comment_url,
            "author": self.author,
            "is_op": self.is_op,
            "depth": self.depth,
            "score": self.score,
            "text": self.text,
            "text_length": self.text_length,
            "replies": [reply.to_dict() for reply in self.replies],
        }


@dataclass
class LoadedCommentTree:
    total_comment_count: int
    top_level_count: int
    loaded_comment_count: int
    max_depth: int
    comments: list[LoadedCommentNode]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_comment_count": self.total_comment_count,
            "top_level_count": self.top_level_count,
            "loaded_comment_count": self.loaded_comment_count,
            "max_depth": self.max_depth,
            "comments": [comment.to_dict() for comment in self.comments],
        }

    def to_limited_dict(self, max_comments: int | None = None) -> dict[str, Any]:
        if max_comments is None or max_comments <= 0:
            payload = self.to_dict()
            payload["included_comment_count"] = self.loaded_comment_count
            return payload

        budget = {"remaining": max_comments, "included": 0}

        def convert(node: LoadedCommentNode) -> dict[str, Any] | None:
            if budget["remaining"] <= 0:
                return None
            budget["remaining"] -= 1
            budget["included"] += 1
            payload = {
                "comment_id": node.comment_id,
                "comment_url": node.comment_url,
                "author": node.author,
                "is_op": node.is_op,
                "depth": node.depth,
                "score": node.score,
                "text": node.text,
                "text_length": node.text_length,
                "replies": [],
            }
            for reply in node.replies:
                converted = convert(reply)
                if converted:
                    payload["replies"].append(converted)
            return payload

        comments = []
        for comment in self.comments:
            converted = convert(comment)
            if converted:
                comments.append(converted)

        return {
            "total_comment_count": self.total_comment_count,
            "top_level_count": self.top_level_count,
            "loaded_comment_count": self.loaded_comment_count,
            "included_comment_count": budget["included"],
            "max_depth": self.max_depth,
            "comments": comments,
        }


class PostDetailObservationCollector:
    def collect(self, page: Page, origin: str) -> PostDetailObservation:
        raw = page.evaluate(
            r"""
            () => {
                const post = document.querySelector('shreddit-post');
                const text = (el) => el ? (el.innerText || el.textContent || "").trim() : "";
                const attr = (el, name) => el ? (el.getAttribute(name) || "") : "";
                const normalizeUrl = (value) => {
                    if (!value) return "";
                    if (/^https?:/i.test(value)) return value;
                    if (value.startsWith("//")) return `${window.location.protocol}${value}`;
                    if (value.startsWith("/")) return `${window.location.origin}${value}`;
                    return value;
                };
                const first = (root, selectors) => {
                    if (!root) return null;
                    for (const selector of selectors) {
                        const el = root.querySelector(selector);
                        if (el) return el;
                    }
                    return null;
                };
                if (!post) return {missing_post: true};

                const bodyEl = first(post, ["div[slot='text-body']", "[slot='text-body']", ".md"]);
                const flairEl = first(post, ["[slot='post-flair']", "shreddit-post-flair", "span[class*='flair']"]);
                const authorEl = first(post, ["a[href^='/user/']", "a[href*='/user/']"]);
                const outboundEl = first(post, ["a[slot='outbound-link']", "a[href^='http']:not([href*='reddit.com'])"]);
                const mediaContainer = first(post, ["[slot='post-media-container']"]);
                const contentHref = normalizeUrl(attr(post, "content-href")).replace(/&amp;/g, "&");
                const domain = attr(post, "domain").toLowerCase();
                const hasRedditImageHref = /^https?:\/\/(?:i|preview)\.redd\.it\//i.test(contentHref);
                const hasRedditVideoHref = /^https?:\/\/v\.redd\.it\//i.test(contentHref);
                const hasGallery = Boolean(post.querySelector("gallery-carousel, shreddit-gallery-carousel"));
                const hasVideo = Boolean(post.querySelector("shreddit-player, video, source[src]"));
                const hasPostImage = Boolean(
                    (mediaContainer || post).querySelector(
                        "img#post-image[src], img.preview-img[src], .lightboxed-content img[src], zoomable-img img[src]"
                    )
                );

                let postType = (attr(post, "post-type") || "unknown").toLowerCase();
                if (postType === "unknown" && hasGallery) postType = "gallery";
                else if (postType === "unknown" && (hasVideo || hasRedditVideoHref)) postType = "video";
                else if (postType === "unknown" && (hasRedditImageHref || hasPostImage || domain === "i.redd.it")) postType = "image";
                else if (postType === "unknown" && outboundEl) postType = "link";
                else if (postType === "unknown" && bodyEl) postType = "text";

                const collectMediaUrls = () => {
                    const originalUrls = [];
                    const fallbackUrls = [];
                    const seen = new Set();
                    const mediaKey = (value) => {
                        const normalized = normalizeUrl(value).replace(/&amp;/g, "&");
                        if (!normalized) return "";
                        try {
                            const parsed = new URL(normalized);
                            const host = parsed.hostname.toLowerCase();
                            if (host !== "i.redd.it" && host !== "preview.redd.it") return "";
                            const fileName = decodeURIComponent((parsed.pathname.split("/").pop() || "").toLowerCase());
                            const stem = fileName.replace(/\.[a-z0-9]+$/i, "");
                            if (host === "i.redd.it") return stem;
                            const previewMatch = stem.match(/(?:^|-)v\d+-([a-z0-9]+)$/i);
                            return previewMatch ? previewMatch[1].toLowerCase() : stem;
                        } catch {
                            return "";
                        }
                    };
                    const push = (value, bucket = originalUrls) => {
                        const normalized = normalizeUrl(value);
                        const cleaned = normalized.replace(/&amp;/g, "&");
                        if (!cleaned) return;
                        const dedupeKey = mediaKey(cleaned) || cleaned;
                        if (seen.has(dedupeKey)) return;
                        seen.add(dedupeKey);
                        bucket.push(cleaned);
                    };
                    const collectFrom = (root, selectors, bucket = originalUrls) => {
                        if (!root) return;
                        for (const selector of selectors) {
                            for (const el of root.querySelectorAll(selector)) {
                                push(el.currentSrc || el.src || attr(el, "src"), bucket);
                            }
                        }
                    };
                    const collectGalleryImages = (root) => {
                        if (!root || postType !== "gallery") return;
                        const gallery = first(root, ["gallery-carousel", "shreddit-gallery-carousel"]);
                        if (!gallery) return;
                        const selectors = [
                            "li[slot^='page-'] figure img.media-lightbox-img",
                            "li[slot^='page-'] figure img:not([role='presentation'])",
                        ];
                        const galleryImages = [];
                        const matched = new Set();
                        for (const selector of selectors) {
                            for (const image of gallery.querySelectorAll(selector)) {
                                if (matched.has(image)) continue;
                                matched.add(image);
                                galleryImages.push(image);
                            }
                        }
                        for (const image of galleryImages) {
                            push(
                                image.currentSrc
                                    || image.src
                                    || attr(image, "src")
                                    || attr(image, "data-lazy-src"),
                                fallbackUrls,
                            );
                        }
                    };
                    if (/^https?:\/\/i\.redd\.it\//i.test(contentHref)) push(contentHref, originalUrls);
                    collectFrom(mediaContainer || post, ["zoomable-img img[src]", ".lightboxed-content img[src]"], originalUrls);
                    if (/^https?:\/\/preview\.redd\.it\//i.test(contentHref)) push(contentHref, fallbackUrls);
                    collectGalleryImages(mediaContainer || post);
                    collectFrom(mediaContainer || post, ["img#post-image[src]", "img.preview-img[src]"], fallbackUrls);
                    if (postType === "gif" || postType === "video") {
                        const player = first(mediaContainer || post, ["shreddit-player[src]", "video[src]"]);
                        if (player) push(player.currentSrc || attr(player, "src"), originalUrls);
                        collectFrom(mediaContainer || post, ["source[src]"], originalUrls);
                    }
                    return originalUrls.concat(fallbackUrls);
                };

                return {
                    subreddit: attr(post, "subreddit-prefixed-name") || "",
                    title: attr(post, "post-title") || "",
                    author: text(authorEl),
                    flair: text(flairEl),
                    post_type: postType,
                    body_text: text(bodyEl),
                    outbound_url: outboundEl ? (outboundEl.href || attr(outboundEl, "href")) : "",
                    media_urls: collectMediaUrls(),
                    upvotes: attr(post, "score") || attr(post, "vote-count"),
                    comments: attr(post, "comment-count") || attr(post, "comments-count"),
                };
            }
            """
        )
        if raw.get("missing_post"):
            raw = {}
        return PostDetailObservation(
            url=page.url,
            origin=origin,
            subreddit=raw.get("subreddit", "") or "unknown",
            title=raw.get("title", "") or "unknown",
            author=raw.get("author", "") or "unknown",
            flair=raw.get("flair", ""),
            post_type=raw.get("post_type", "unknown"),
            body_text=(raw.get("body_text") or "").strip(),
            body_length=len((raw.get("body_text") or "").strip()),
            upvotes=parse_count(raw.get("upvotes")),
            comments=parse_count(raw.get("comments")),
            outbound_url=(raw.get("outbound_url") or "").strip(),
            media_urls=list(raw.get("media_urls") or []),
        )


class LoadedCommentTreeExtractor:
    def collect(self, page: Page, total_comment_count: int = 0) -> LoadedCommentTree:
        raw_tree = page.evaluate(
            """
            () => {
                const text = (el) => el ? (el.innerText || el.textContent || "").trim() : "";
                const attr = (el, name) => el ? (el.getAttribute(name) || "") : "";
                const first = (root, selectors) => {
                    if (!root) return null;
                    for (const selector of selectors) {
                        const el = root.querySelector(selector);
                        if (el) return el;
                    }
                    return null;
                };
                const parseNode = (node) => {
                    const commentId = attr(node, "thingid") || attr(node, "thing-id") || attr(node, "comment-id") || attr(node, "id") || "";
                    const metaEl = first(node, ["div[slot='commentMeta']"]);
                    const authorEl = first(node, [
                        "div[slot='commentMeta'] a[href^='/user/'][aria-label*='profile']",
                        "div[slot='commentMeta'] a[href^='/user/']",
                        "a[href^='/user/'][aria-label*='profile']",
                        "a[href^='/user/']",
                    ]);
                    const authorLabel = attr(authorEl, "aria-label");
                    const authorMatch = authorLabel.match(/^(.+?)'s profile$/);
                    const authorFromLabel = authorMatch ? authorMatch[1].trim() : "";
                    const author = attr(node, "author") || text(authorEl) || authorFromLabel || "unknown";
                    const bodyEl = first(node, [
                        "div[id$='-post-rtjson-content']",
                        "div[id$='-comment-rtjson-content']",
                        "[slot='comment']",
                    ]);
                    const bodyText = text(bodyEl);
                    const scoreEl = first(node, [
                        "button[upvote] + span faceplate-number[number]",
                        "button[upvote] + span",
                        "faceplate-number[number]",
                    ]);
                    const score = attr(node, "score") || attr(node, "vote-count") || attr(scoreEl, "number") || text(scoreEl);
                    const permalink = attr(node, "permalink");
                    const permalinkLink = first(node, ["faceplate-timeago a[href*='/comment/']", "a[href*='/comment/']"]);
                    const commentUrl = permalink
                        ? (permalink.startsWith("http") ? permalink : `${window.location.origin}${permalink}`)
                        : (permalinkLink ? (() => {
                            const href = permalinkLink.getAttribute("href") || "";
                            return href.startsWith("http") ? href : `${window.location.origin}${href}`;
                        })() : "");
                    const replies = [];
                    const descendants = Array.from(node.querySelectorAll("shreddit-comment"));
                    descendants.forEach((child) => {
                        if (child === node || !child.parentElement) return;
                        const closestParentComment = child.parentElement.closest("shreddit-comment");
                        if (closestParentComment === node) replies.push(parseNode(child));
                    });
                    return {
                        comment_id: commentId,
                        comment_url: commentUrl,
                        author,
                        is_op: Boolean(first(metaEl || node, [
                            "span.comment-op-icon-js",
                            "[aria-label='Comment distinguished as the original poster']",
                            "shreddit-comment-author-modifier-icon[op]",
                            "shreddit-comment-badges[op]",
                        ])),
                        depth: attr(node, "depth") || attr(node, "data-depth") || "0",
                        score,
                        text: bodyText,
                        replies,
                    };
                };
                const commentsSection = document.querySelector("section[aria-label='Comments']");
                const root = commentsSection || document;
                const allComments = Array.from(root.querySelectorAll("shreddit-comment"));
                const tree = [];
                allComments.forEach((comment) => {
                    if (!comment.parentElement || !comment.parentElement.closest("shreddit-comment")) tree.push(parseNode(comment));
                });
                return tree;
            }
            """
        )

        def build_node(data: dict[str, Any]) -> LoadedCommentNode:
            text = (data.get("text") or "").strip()
            replies = [build_node(reply) for reply in (data.get("replies") or [])]
            return LoadedCommentNode(
                comment_id=(data.get("comment_id") or "").strip()
                or hashlib.sha1(
                    f"{data.get('author', '')}|{text[:120]}|{data.get('depth', 0)}".encode("utf-8", errors="ignore")
                ).hexdigest()[:16],
                comment_url=(data.get("comment_url") or "").strip(),
                author=(data.get("author") or "unknown").strip() or "unknown",
                is_op=bool(data.get("is_op")),
                depth=_clamp_int(data.get("depth"), 0, 20, 0),
                score=parse_count(data.get("score")),
                text=text,
                text_length=len(text),
                replies=replies,
            )

        comments = [build_node(item) for item in raw_tree or []]
        flat_nodes: list[LoadedCommentNode] = []

        def walk(node: LoadedCommentNode) -> None:
            flat_nodes.append(node)
            for reply in node.replies:
                walk(reply)

        for comment in comments:
            walk(comment)

        return LoadedCommentTree(
            total_comment_count=total_comment_count,
            top_level_count=len(comments),
            loaded_comment_count=len(flat_nodes),
            max_depth=max((node.depth for node in flat_nodes), default=0),
            comments=comments,
        )


def parse_count(value: Any) -> int | None:
    raw = str(value or "").strip().lower().replace(",", "")
    if not raw:
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*([km])?", raw)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        number *= 1000
    elif suffix == "m":
        number *= 1000000
    return int(number)


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))

"""Reproduce the current comment prompt without making any network requests.

All product, post, comment, and model-response data below are fictional.
Run with the backend virtual environment. Outputs are saved beside this script
in comment_decision_output/. The real prompt builder and response validator run;
only the HTTP response is simulated.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / "backend" / ".env")

from app.comment_decider import generate_comment_decision
from app.schemas import QueryPlanGenerateRequest, RedditSearchResultItem


def main() -> None:
    product = QueryPlanGenerateRequest(
        productName="ClipNest",
        productDescription=(
            "A browser-based video tool for turning long recordings into short clips. "
            "It supports automatic captions, manual caption correction, and SRT export."
        ),
        targetAudience="Solo creators and small podcast teams.",
        sellingPoints="Editable automatic captions; SRT export; browser-based workflow.",
        competitors="CapCut, Descript",
        commentRequirements="Write in English. Be helpful and conversational. Do not include links.",
        forbiddenTopics="Do not invent pricing, discounts, or features that are not listed.",
        desiredQueryCount=12,
    )
    post_url = "https://www.reddit.com/r/VideoEditing/comments/demo123/caption_workflow"
    search_result = RedditSearchResultItem(
        query="podcast captions workflow",
        queryIntent="problem_solution",
        priority=1,
        timeRange="week",
        resultIndex=1,
        postUrl=post_url,
        postId="demo123",
        title="How do you handle captions for weekly podcast clips?",
        subreddit="r/VideoEditing",
        ageText="2d ago",
        votes=58,
        comments=12,
        duplicateOfQuery=None,
        matchedQueries=["podcast captions workflow", "automatic subtitle editor"],
    )
    body = (
        "I turn a 45-minute podcast into five short clips every week. "
        "The editing is manageable, but correcting auto-captions takes forever, "
        "especially names. Is there a less painful workflow? "
        "I need to edit the wording and export subtitles separately."
    )

    def comment(comment_id, author, text, score, depth=0, is_op=False, replies=None):
        return {
            "comment_id": comment_id,
            "comment_url": f"{post_url}/comment/{comment_id}",
            "author": author,
            "is_op": is_op,
            "depth": depth,
            "score": score,
            "text": text,
            "text_length": len(text),
            "replies": replies or [],
        }

    op_reply = comment(
        "c002",
        "weekly_podcaster",
        "Exactly. I need editable captions and an SRT file for a client's upload.",
        5,
        depth=1,
        is_op=True,
    )
    first_comment = comment(
        "c001",
        "timeline_tamer",
        "Auto-captions are great until your guest's name becomes a kitchen appliance. "
        "Make sure you can export SRT instead of only burning captions into the video.",
        17,
        replies=[op_reply],
    )
    second_comment = comment(
        "c003",
        "cut_first",
        "Lock your cuts before fixing captions, otherwise you get to do the same job twice.",
        9,
    )
    detail = {
        "status": "success",
        "reason": "",
        "post_url": post_url,
        "final_url": post_url,
        "title": search_result.title,
        "subreddit": "r/VideoEditing",
        "author": "weekly_podcaster",
        "flair": "Workflow",
        "post_type": "text",
        "body_text": body,
        "body_length": len(body),
        "media_urls": [],
        "outbound_url": "",
        "upvotes": 58,
        "comments": 12,
        "comment_tree": {
            "total_comment_count": 12,
            "top_level_count": 2,
            "loaded_comment_count": 3,
            "included_comment_count": 3,
            "max_depth": 1,
            "comments": [first_comment, second_comment],
        },
        "loaded_comment_count": 3,
        "included_comment_count": 3,
        "top_level_comment_count": 2,
        "max_comment_depth": 1,
    }
    simulated_response = {
        "threadForensics": (
            "Gate 1 pass: VideoEditing podcast workflow. Gate 2 pass: no ClipNest mentions or shill accusations. "
            "Gate 3 pass: OP explicitly asks for editable auto-captions + separate SRT export. Gate 4 pass: no forbidden topics."
        ),
        "shouldComment": True,
        "skipReason": None,
        "selectedPlaybook": "playbook_1_conditional_partition",
        "voiceProfile": "profile_b_crisp_partitioner",
        "commentUrl": op_reply["comment_url"],
        "commentText": (
            "ClipNest if you specifically need editable auto-captions with clean SRT export for client deliveries. "
            "CapCut is fine for quick burned-in social subs, but fixing guest names after the fact is a headache without a separate subtitle file."
        ),
    }
    response = Mock()
    response.ok = True
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(simulated_response)}}]
    }

    # A fictional credential prevents real credentials from reaching even the mock.
    # The example is a text post, so the image downloader is never called.
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "example-not-a-real-key"}):
        with patch("app.openrouter_client.requests.post", return_value=response) as send:
            result = generate_comment_decision(
                product_context=product,
                search_result=search_result,
                detail=detail,
                comment_length_style="medium",
            )
            send.assert_called_once()
            request = send.call_args.kwargs["json"]
            request_url = send.call_args.args[0]
            timeout = send.call_args.kwargs["timeout"]

    output = Path(__file__).resolve().parent / "comment_decision_output"
    output.mkdir(exist_ok=True)

    def write_json(filename, value):
        (output / filename).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    system_prompt_text = request["messages"][0]["content"]
    user_prompt_text = request["messages"][1]["content"][0]["text"]
    write_json("01-simulated-inputs.json", {
        "fictional_example": True,
        "productContext": product.model_dump(),
        "searchResult": search_result.model_dump(),
        "detail": detail,
        "selectedCommentLengthStyle": "medium",
    })
    (output / "02-system-prompt.txt").write_text(system_prompt_text, encoding="utf-8")
    (output / "02-final-prompt.txt").write_text(user_prompt_text, encoding="utf-8")
    write_json("02-final-prompt-readable.json", json.loads(user_prompt_text))
    write_json("03-openrouter-request.json", request)
    write_json("04-simulated-model-response.json", simulated_response)
    write_json("05-validated-decision.json", result)
    write_json("06-request-metadata.json", {
        "fictional_example": True,
        "network_requests_sent": 0,
        "endpoint": request_url,
        "timeout_seconds": timeout,
        "headers_saved": False,
    })
    print(json.dumps({
        "output_directory": str(output),
        "model": request["model"],
        "system_prompt_characters": len(system_prompt_text),
        "user_prompt_characters": len(user_prompt_text),
        "body_characters": len(body),
        "validated_status": result["status"],
        "validated_reason": result["reason"],
        "network_requests_sent": 0,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

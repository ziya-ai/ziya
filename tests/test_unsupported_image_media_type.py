import base64

from app.utils.image_result_compaction import (
    sanitize_unsupported_images,
    unsupported_image_to_text_blocks,
)


def _img(mt, raw=b"<svg/>"):
    return {"type": "image", "source": {
        "type": "base64", "media_type": mt,
        "data": base64.b64encode(raw).decode()}}


def test_svg_becomes_text_png_survives():
    out = unsupported_image_to_text_blocks([_img("image/svg+xml"), _img("image/png")])
    assert out[0]["type"] == "text" and "<svg/>" in out[0]["text"]
    assert out[1]["type"] == "image"


def test_sanitize_heals_tool_result_in_conversation():
    conv = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": [_img("image/svg+xml"), {"type": "text", "text": "x"}]}]}]
    assert sanitize_unsupported_images(conv) == 1
    kinds = [b["type"] for b in conv[0]["content"][0]["content"]]
    assert "image" not in kinds
    assert sanitize_unsupported_images(conv) == 0

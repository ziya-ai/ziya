"""
Tests for StreamingToolExecutor internals.
"""


class TestImageContentCompaction:
    """Verify that structured image tool results are compacted
    to text-only summaries before entering conversation history."""

    def test_image_blocks_replaced_with_text_summary(self):
        """When a tool result contains image content blocks, the
        conversation-history builder should replace them with the
        text-only summary to avoid polluting context with base64."""

        # Simulate the compaction logic from stream_with_tools
        raw_result = [
            {
                'type': 'image',
                'source': {
                    'type': 'base64',
                    'media_type': 'image/png',
                    'data': 'iVBORw0KGgo' + 'A' * 180_000,  # ~180K of base64
                },
            },
            {
                'type': 'text',
                'text': 'Rendered graphviz diagram (PNG, 133.3 KB). Definition: 2437 chars, theme: dark.',
            },
        ]

        # Apply the same compaction that stream_with_tools uses
        if isinstance(raw_result, list):
            text_parts = [
                b.get('text', '') for b in raw_result
                if isinstance(b, dict) and b.get('type') == 'text'
            ]
            compacted = ' '.join(text_parts) or '[Image result — content delivered inline above]'
        else:
            compacted = raw_result

        assert isinstance(compacted, str)
        assert 'Rendered graphviz diagram' in compacted
        # The base64 data must NOT be present
        assert 'iVBORw0KGgo' not in compacted
        # Should be small — just the text summary
        assert len(compacted) < 200

    def test_text_only_results_pass_through(self):
        """Non-image tool results should not be affected."""
        raw_result = "command output: total 42\ndrwxr-xr-x  5 user staff"

        # Same logic path — non-list results skip compaction
        if isinstance(raw_result, list):
            text_parts = [
                b.get('text', '') for b in raw_result
                if isinstance(b, dict) and b.get('type') == 'text'
            ]
            compacted = ' '.join(text_parts) or '[Image result]'
        else:
            compacted = raw_result

        assert compacted == raw_result

    def test_image_blocks_without_text_get_fallback(self):
        """Image-only results (no text block) should get a fallback label."""
        raw_result = [
            {
                'type': 'image',
                'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'AAAA'},
            },
        ]

        if isinstance(raw_result, list):
            text_parts = [
                b.get('text', '') for b in raw_result
                if isinstance(b, dict) and b.get('type') == 'text'
            ]
            compacted = ' '.join(text_parts) or '[Image result — content delivered inline above]'
        else:
            compacted = raw_result

        assert compacted == '[Image result — content delivered inline above]'
        assert len(compacted) < 100


class TestImageDataUriExtraction:
    """Verify that the tool_display event includes an image_data field
    for inline frontend rendering, extracted from structured results."""

    def _extract_image_data_uri(self, result_text):
        """Replicate the extraction logic from stream_with_tools."""
        if not isinstance(result_text, list):
            return None
        for block in result_text:
            if isinstance(block, dict) and block.get('type') == 'image':
                src = block.get('source', {})
                if src.get('type') == 'base64' and src.get('data'):
                    media = src.get('media_type', 'image/png')
                    return f"data:{media};base64,{src['data']}"
        return None

    def test_extracts_png_data_uri(self):
        """PNG image blocks should produce a data:image/png data URI."""
        result_text = [
            {
                'type': 'image',
                'source': {
                    'type': 'base64',
                    'media_type': 'image/png',
                    'data': 'iVBORw0KGgoAAAANS',
                },
            },
            {'type': 'text', 'text': 'Rendered diagram.'},
        ]
        uri = self._extract_image_data_uri(result_text)
        assert uri is not None
        assert uri.startswith('data:image/png;base64,')
        assert 'iVBORw0KGgoAAAANS' in uri

    def test_extracts_svg_data_uri(self):
        """SVG image blocks should produce a data:image/svg+xml data URI."""
        result_text = [
            {
                'type': 'image',
                'source': {
                    'type': 'base64',
                    'media_type': 'image/svg+xml',
                    'data': 'PHN2ZyB4bWxucz0iaHR0c',
                },
            },
            {'type': 'text', 'text': 'Rendered SVG diagram.'},
        ]
        uri = self._extract_image_data_uri(result_text)
        assert uri is not None
        assert uri.startswith('data:image/svg+xml;base64,')

    def test_returns_none_for_text_only_results(self):
        """String results (non-image tools) should return None."""
        assert self._extract_image_data_uri("shell output") is None

    def test_returns_none_for_no_image_blocks(self):
        """List results without image blocks should return None."""
        result_text = [
            {'type': 'text', 'text': 'Just text, no image.'},
        ]
        assert self._extract_image_data_uri(result_text) is None

    def test_returns_none_for_missing_data(self):
        """Image blocks with empty data should return None."""
        result_text = [
            {
                'type': 'image',
                'source': {'type': 'base64', 'media_type': 'image/png', 'data': ''},
            },
        ]
        assert self._extract_image_data_uri(result_text) is None

    def test_defaults_media_type_to_png(self):
        """Missing media_type should default to image/png."""
        result_text = [
            {
                'type': 'image',
                'source': {'type': 'base64', 'data': 'AAAA'},
            },
        ]
        uri = self._extract_image_data_uri(result_text)
        assert uri is not None
        assert uri.startswith('data:image/png;base64,')

    def test_first_image_wins(self):
        """When multiple image blocks exist, the first is used."""
        result_text = [
            {
                'type': 'image',
                'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'FIRST'},
            },
            {
                'type': 'image',
                'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'SECOND'},
            },
        ]
        uri = self._extract_image_data_uri(result_text)
        assert 'FIRST' in uri
        assert 'SECOND' not in uri


class TestStructuralImageDetection:
    """Verify that image content is detected structurally rather than
    via the _has_image_content flag, which gets stripped by
    strip_signature_metadata."""

    def test_image_detected_after_signature_stripping(self):
        """Image content should be detected even after all _-prefixed
        keys have been removed by strip_signature_metadata."""
        from app.mcp.signing import strip_signature_metadata

        # Simulate what render_diagram returns
        result = {
            '_has_image_content': True,
            '_signature': 'abc123',
            '_timestamp': 12345,
            '_tool_name': 'render_diagram',
            '_arguments': {},
            '_conversation_id': 'test',
            'content': [
                {
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': 'image/png',
                        'data': 'iVBORw0KGgo',
                    },
                },
                {
                    'type': 'text',
                    'text': 'Rendered diagram (PNG, 50 KB).',
                },
            ],
        }

        # This is what happens in the executor after verification
        stripped = strip_signature_metadata(result)

        # The _has_image_content flag should be gone (old bug)
        # but the structural detection should still work
        content = stripped.get('content')
        _has_image = isinstance(content, list) and any(
            isinstance(b, dict) and b.get('type') == 'image' for b in content
        )

        assert _has_image is True
        assert '_signature' not in stripped
        assert '_timestamp' not in stripped

    def test_non_image_content_not_detected(self):
        """Text-only results should not trigger image detection."""
        result = {
            'content': [
                {'type': 'text', 'text': 'Just some output.'},
            ],
        }
        content = result.get('content')
        _has_image = isinstance(content, list) and any(
            isinstance(b, dict) and b.get('type') == 'image' for b in content
        )
        assert _has_image is False

    def test_strip_signature_preserves_content_flags(self):
        """strip_signature_metadata should only remove signing keys,
        not all _-prefixed keys."""
        from app.mcp.signing import strip_signature_metadata

        result = {
            '_has_image_content': True,
            '_signature': 'sig',
            '_timestamp': 1,
            '_tool_name': 'test',
            '_arguments': {},
            '_conversation_id': 'c1',
            'content': [{'type': 'text', 'text': 'hi'}],
        }
        stripped = strip_signature_metadata(result)

        # Signing keys removed
        assert '_signature' not in stripped
        assert '_timestamp' not in stripped
        assert '_tool_name' not in stripped
        assert '_arguments' not in stripped
        assert '_conversation_id' not in stripped

        # Content flag preserved
        assert stripped.get('_has_image_content') is True
        assert 'content' in stripped

"""
Compatibility layer for tiktoken that provides a fallback for Python 3.14+
where tiktoken doesn't yet have pre-built wheels.
"""

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    
    try:
        from transformers import AutoTokenizer
        _hf_tokenizer = AutoTokenizer.from_pretrained('Xenova/claude-tokenizer')
        
        class _HFEncoding:
            """Encoding using HuggingFace claude-tokenizer"""
            def encode(self, text, **kwargs):
                return _hf_tokenizer.encode(text)
            
            def decode(self, tokens, **kwargs):
                return _hf_tokenizer.decode(tokens)
        
        class _TiktokenStub:
            """Stub using HuggingFace tokenizer"""
            @staticmethod
            def encoding_for_model(model_name):
                return _HFEncoding()
            
            @staticmethod
            def get_encoding(encoding_name):
                return _HFEncoding()
        
        tiktoken = _TiktokenStub()
        
    except Exception:
        # Final fallback: rough estimation
        class _FallbackEncoding:
            def encode(self, text, **kwargs):
                return list(range(len(text) // 4))
            
            def decode(self, tokens, **kwargs):
                return ""
        
        class _TiktokenStub:
            @staticmethod
            def encoding_for_model(model_name):
                return _FallbackEncoding()
            
            @staticmethod
            def get_encoding(encoding_name):
                return _FallbackEncoding()
        
        tiktoken = _TiktokenStub()


def get_encoding(encoding_name="cl100k_base"):
    """Get tiktoken encoding with fallback"""
    return tiktoken.get_encoding(encoding_name)


def encoding_for_model(model_name):
    """Get tiktoken encoding for model with fallback"""
    return tiktoken.encoding_for_model(model_name)

from sentence_transformers import SentenceTransformer

_model = None

def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model

def create_embedding(text: str):
    embedding = _get_model().encode(text)
    return embedding.tolist()

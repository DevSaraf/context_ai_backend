def build_context(results):

    context = "Relevant Company Knowledge:\n\n"

    for i, item in enumerate(results, 1):
        text = item["text"]
        context += f"{i}. {text}\n\n"

    return context
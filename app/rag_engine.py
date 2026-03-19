"""
RAG Answer Generation Module
Takes a user query + retrieved chunks → generates a grounded, cited answer.

This is the core intelligence layer that transforms raw search results
into useful, trustworthy answers.

Usage:
    from app.rag_engine import generate_answer, generate_ticket_response

    # For general Q&A (Pillar 1 + Extension)
    answer = await generate_answer(query, chunks)

    # For ticket resolution (Pillar 2)
    response = await generate_ticket_response(ticket_subject, ticket_body, similar_tickets)
"""

from typing import List, Dict, Optional
from dataclasses import dataclass
from app.llm_provider import llm


@dataclass
class RAGResponse:
    """Structured response from RAG generation."""
    answer: str                        # The generated answer text
    citations: List[Dict]              # Source references used
    confidence: float                  # Overall confidence (0-1)
    query: str                         # Original query
    chunks_used: int                   # How many chunks were in context
    has_answer: bool = True            # False if no relevant context found


# ============== SYSTEM PROMPTS ==============

KNOWLEDGE_QA_PROMPT = """You are a helpful company knowledge assistant. Your job is to answer questions using ONLY the provided company knowledge context.

RULES:
1. Answer ONLY from the provided context. Never make up information.
2. If the context doesn't contain enough information to answer, say so clearly.
3. When referencing information, cite the source using [Source N] format where N matches the source number.
4. Keep answers clear, concise, and actionable.
5. If multiple sources agree, mention that for higher confidence.
6. If sources conflict, note the discrepancy.

RESPONSE FORMAT:
- Start with a direct answer to the question
- Support with details from the context
- End with source citations used"""


TICKET_RESOLUTION_PROMPT = """You are a customer support assistant. Your job is to help draft responses to support tickets using proven resolutions from past tickets.

RULES:
1. Draft a professional, helpful customer response based on what worked before.
2. Use the resolution approaches from similar past tickets.
3. Adapt the tone to be friendly and empathetic — this goes directly to a customer.
4. If past tickets show a clear solution, state it confidently.
5. If the issue is ambiguous, suggest the most common resolution and offer alternatives.
6. Never reference internal systems, past ticket IDs, or CSAT scores to the customer.
7. Keep the response concise — under 200 words unless the issue is complex.

RESPONSE FORMAT:
- Greeting acknowledging the issue
- Clear solution or next steps
- Offer for further help"""


EXTENSION_CONTEXT_PROMPT = """You are a context assistant that helps users by providing relevant company knowledge while they work in AI chat tools.

RULES:
1. Provide a brief, focused summary of the most relevant knowledge.
2. Answer in 2-4 sentences — the user will see this in a small sidebar.
3. Only use information from the provided context.
4. If the context is relevant, lead with the key insight.
5. If the context isn't very relevant to the query, say "Limited context found" and share what's available.

Keep it short. The user is in the middle of working and needs a quick reference, not an essay."""


# ============== CORE RAG FUNCTIONS ==============

async def generate_answer(
    query: str,
    chunks: List[Dict],
    mode: str = "qa"
) -> RAGResponse:
    """
    Generate an answer from retrieved chunks.

    Args:
        query:  The user's question
        chunks: Retrieved knowledge chunks (from your /search or /context endpoint)
                Each chunk should have: text, source_type, source_id, confidence/similarity
        mode:   "qa" (full answer), "extension" (brief sidebar), "ticket" (customer response)
    """
    if not chunks:
        return RAGResponse(
            answer="I couldn't find any relevant information in the knowledge base for this query. Try rephrasing your question or uploading more content.",
            citations=[],
            confidence=0.0,
            query=query,
            chunks_used=0,
            has_answer=False
        )

    # Build the context from chunks
    context_parts = []
    citations = []

    for i, chunk in enumerate(chunks, 1):
        text = chunk.get("text", "")
        source_type = chunk.get("source_type", "document")
        source_id = chunk.get("source_id", "")
        confidence = chunk.get("confidence", chunk.get("similarity", 0))

        context_parts.append(f"[Source {i}] ({source_type} #{source_id}, confidence: {confidence:.0%})\n{text}")

        citations.append({
            "source_number": i,
            "source_type": source_type,
            "source_id": source_id,
            "confidence": confidence,
            "chunk_id": chunk.get("id"),
            "preview": text[:100] + "..." if len(text) > 100 else text
        })

    context_block = "\n\n---\n\n".join(context_parts)

    # Select system prompt based on mode
    system_prompts = {
        "qa": KNOWLEDGE_QA_PROMPT,
        "extension": EXTENSION_CONTEXT_PROMPT,
        "ticket": TICKET_RESOLUTION_PROMPT,
    }
    system_prompt = system_prompts.get(mode, KNOWLEDGE_QA_PROMPT)

    # Build user prompt
    user_prompt = f"""COMPANY KNOWLEDGE CONTEXT:
{context_block}

---

USER QUESTION: {query}

Provide your answer based on the context above."""

    # Generate with LLM
    max_tokens = 300 if mode == "extension" else 800
    answer_text = await llm.generate(system_prompt, user_prompt, max_tokens=max_tokens)

    # Calculate overall confidence (average of chunk confidences)
    avg_confidence = sum(c["confidence"] for c in citations) / len(citations) if citations else 0

    return RAGResponse(
        answer=answer_text,
        citations=citations,
        confidence=round(avg_confidence, 3),
        query=query,
        chunks_used=len(chunks),
        has_answer=True
    )


async def generate_ticket_response(
    ticket_subject: str,
    ticket_body: str,
    similar_tickets: List[Dict]
) -> RAGResponse:
    """
    Generate a suggested response for a support ticket.

    Args:
        ticket_subject: The new ticket's subject line
        ticket_body:    The customer's message
        similar_tickets: Retrieved similar past tickets with resolutions
    """
    if not similar_tickets:
        return RAGResponse(
            answer="No similar past tickets found. This may be a new type of issue that requires manual investigation.",
            citations=[],
            confidence=0.0,
            query=f"{ticket_subject}: {ticket_body}",
            chunks_used=0,
            has_answer=False
        )

    # Build context from similar tickets
    context_parts = []
    citations = []

    for i, ticket in enumerate(similar_tickets, 1):
        text = ticket.get("text", "")
        confidence = ticket.get("confidence", ticket.get("similarity", 0))
        resolution_score = ticket.get("resolution_score", 0.5)

        context_parts.append(
            f"[Past Ticket {i}] (match: {confidence:.0%}, customer satisfaction: {resolution_score:.0%})\n{text}"
        )

        citations.append({
            "source_number": i,
            "source_type": "past_ticket",
            "source_id": ticket.get("source_id", ticket.get("id")),
            "confidence": confidence,
            "resolution_score": resolution_score,
            "chunk_id": ticket.get("id"),
            "preview": text[:100] + "..." if len(text) > 100 else text
        })

    context_block = "\n\n---\n\n".join(context_parts)

    user_prompt = f"""SIMILAR PAST TICKETS AND THEIR RESOLUTIONS:
{context_block}

---

NEW TICKET TO RESPOND TO:
Subject: {ticket_subject}
Customer Message: {ticket_body}

Draft a professional response to this customer based on what worked in similar past tickets."""

    answer_text = await llm.generate(TICKET_RESOLUTION_PROMPT, user_prompt, max_tokens=600)

    avg_confidence = sum(c["confidence"] for c in citations) / len(citations) if citations else 0

    return RAGResponse(
        answer=answer_text,
        citations=citations,
        confidence=round(avg_confidence, 3),
        query=f"{ticket_subject}: {ticket_body}",
        chunks_used=len(similar_tickets),
        has_answer=True
    )

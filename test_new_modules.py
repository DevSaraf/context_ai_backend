"""
Test script for the new chunking and file parsing modules.
Run this to verify everything works before integrating:

    python -m app.test_chunking_and_parsing
    
Or just:
    python test_new_modules.py
"""

import sys
import os

# Add parent dir to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_chunking():
    """Test the improved chunker."""
    from app.chunking import chunk_text, chunk_text_with_metadata

    print("=" * 60)
    print("TEST 1: Basic chunking (backward compatible)")
    print("=" * 60)

    sample_text = """
    # Company API Guidelines

    Our company uses FastAPI for backend development. We prefer PostgreSQL 
    over MySQL for database needs. All API endpoints should include proper 
    authentication using JWT tokens. Our standard response time SLA is under 
    200ms for API calls.

    # Security Policies

    All passwords must be hashed using bcrypt with a minimum of 12 rounds.
    API keys should be rotated every 90 days. Two-factor authentication is 
    required for all admin accounts. Database credentials must never be 
    committed to version control.

    # Customer Support Procedures

    Customer support tickets should be responded to within 4 hours during 
    business hours. Critical issues (P0) require immediate response. All 
    escalations must be documented in Jira. The support team uses Zendesk 
    for ticket management and Slack for internal communication about 
    ongoing issues.
    """

    # Old-style call (returns list of strings)
    chunks = chunk_text(sample_text, chunk_size=50, overlap=10, min_chunk_size=10)

    print(f"\nInput: {len(sample_text.split())} words")
    print(f"Chunks created: {len(chunks)}")
    for i, chunk in enumerate(chunks):
        words = len(chunk.split())
        print(f"\n  Chunk {i}: ({words} words)")
        print(f"  Preview: {chunk[:120]}...")

    print("\n" + "=" * 60)
    print("TEST 2: Chunking with metadata")
    print("=" * 60)

    chunks_meta = chunk_text_with_metadata(sample_text, chunk_size=50, overlap=10, min_chunk_size=10)

    for chunk in chunks_meta:
        print(f"\n  Chunk {chunk.index}:")
        print(f"    Section: {chunk.section_title}")
        print(f"    Words:   {chunk.word_count}")
        print(f"    Preview: {chunk.text[:100]}...")

    print("\n" + "=" * 60)
    print("TEST 3: Edge cases")
    print("=" * 60)

    # Empty text
    assert chunk_text("") == [], "Empty text should return empty list"
    print("  ✓ Empty text handled")

    # Very short text
    short = chunk_text("Hello world", min_chunk_size=1)
    assert len(short) == 1, "Short text should return one chunk"
    print("  ✓ Short text handled")

    # Whitespace only
    assert chunk_text("   \n\n  ") == [], "Whitespace should return empty list"
    print("  ✓ Whitespace-only handled")

    # Overlap verification
    chunks = chunk_text(sample_text, chunk_size=30, overlap=10, min_chunk_size=10)
    if len(chunks) >= 2:
        # Last 10 words of chunk 0 should appear in chunk 1
        words_0 = chunks[0].split()
        words_1 = chunks[1].split()
        overlap_words = words_0[-10:]
        # Check that at least some overlap words appear in chunk 1
        found = sum(1 for w in overlap_words if w in words_1[:20])
        assert found >= 5, f"Expected overlap, only found {found} matching words"
        print(f"  ✓ Overlap working ({found}/10 words carried over)")

    print("\n  All chunking tests passed!\n")


def test_file_parser():
    """Test file parser with synthetic content."""
    from app.file_parser import parse_raw_text

    print("=" * 60)
    print("TEST 4: Raw text parsing")
    print("=" * 60)

    result = parse_raw_text("This is a test document.\n\nIt has multiple paragraphs.")
    assert result.is_valid
    assert result.word_count > 0
    print(f"  ✓ Raw text parsed: {result.word_count} words")
    print(f"    Filename: {result.filename}")
    print(f"    Type: {result.file_type}")

    # Test with messy text
    messy = "Hello\x00World  \xa0  multiple   spaces\n\n\n\n\ntoo many newlines"
    result = parse_raw_text(messy)
    assert "\x00" not in result.text, "Null bytes should be removed"
    assert "\xa0" not in result.text, "Non-breaking spaces should be normalized"
    print("  ✓ Text cleaning works (null bytes, unicode spaces, extra newlines)")

    print("\n" + "=" * 60)
    print("TEST 5: File extension detection")
    print("=" * 60)

    from app.file_parser import _get_extension
    assert _get_extension("report.pdf") == "pdf"
    assert _get_extension("document.DOCX") == "docx"
    assert _get_extension("notes.txt") == "txt"
    assert _get_extension("data.csv") == "csv"
    assert _get_extension("README.md") == "md"
    assert _get_extension("no_extension") == ""
    print("  ✓ All extensions detected correctly")

    print("\n  All file parser tests passed!\n")


def test_integration():
    """Test chunking + parsing together (the actual pipeline)."""
    from app.file_parser import parse_raw_text
    from app.chunking import chunk_text

    print("=" * 60)
    print("TEST 6: Full pipeline (parse → chunk)")
    print("=" * 60)

    # Simulate what happens when a user uploads text
    raw_content = """
    # Onboarding Process

    New employees should complete the following steps in their first week:
    Set up their development environment using the provided script.
    Request access to GitHub, Jira, and Slack from the IT team.
    Complete the security training module on the learning platform.
    
    # Development Standards
    
    All code must pass linting before merge. We use Black for Python 
    formatting and ESLint for JavaScript. Pull requests require at least 
    two approvals. The main branch is protected and requires CI to pass.
    
    Tests are mandatory for all new features. Minimum coverage is 80%.
    Integration tests should cover all API endpoints. Unit tests should
    cover all business logic functions.
    """

    # Step 1: Parse
    parsed = parse_raw_text(raw_content, source_name="onboarding_guide")
    assert parsed.is_valid
    print(f"  Parsed: {parsed.word_count} words from '{parsed.filename}'")

    # Step 2: Chunk
    chunks = chunk_text(parsed.text, chunk_size=40, overlap=8, min_chunk_size=10)
    print(f"  Chunked into {len(chunks)} pieces")

    for i, chunk in enumerate(chunks):
        print(f"    [{i}] {len(chunk.split())} words: {chunk[:80]}...")

    print("\n  Pipeline test passed!\n")


if __name__ == "__main__":
    test_chunking()
    test_file_parser()
    test_integration()
    print("=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)

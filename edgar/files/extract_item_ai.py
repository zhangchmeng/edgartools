import json
import os
from typing import List, Dict, Tuple, Any
from pydantic import BaseModel, Field

from openai import OpenAI
from llama_index.core.output_parsers import PydanticOutputParser

from edgar.company_reports import TenQ

class LinkPosition(BaseModel):
    link_id: str = Field(..., description="Link href")
    part: str = Field(..., description="part")
    item: str = Field(..., description="item")

class LinkResult(BaseModel):
    links: list[LinkPosition]


out_parser = PydanticOutputParser(output_cls=LinkResult)


def extract_items_with_ai(
    standard_modules: Dict[str, Any],
    document_toc: List[List[Dict[str, Any]]],
) -> List[Tuple[Tuple[str, str], str]]:
    """
    Use OpenAI API to classify document table of contents according to standard modules

    Args:
        standard_modules: SEC report standard module structure
        document_toc: Document table of contents structure

    Returns:
        List[Tuple[Tuple[str, str], str]]: Classification results in format [(('part', 'item'), 'link_id'), ...]
    """

    # Check if OpenAI is available
    if OpenAI is None:
        print(
            "OpenAI library not installed, AI classification feature unavailable"
        )
        print("Please run: pip install openai")
        return []

    # Initialize OpenAI client
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Please set OPENAI_API_KEY environment variable")
        return []

    try:
        client = OpenAI(api_key=api_key)
    except Exception as e:
        print(f"OpenAI client initialization failed: {e}")
        return []

    # Flatten document table of contents
    flattened_toc = []
    for page_items in document_toc:
        for item in page_items:
            if isinstance(item, dict) and "text" in item and "link" in item:
                title = item["text"][0] if item["text"] else ""
                page = item["text"][1] if len(item["text"]) > 1 else ""
                existing_classification = (
                    item["text"][2] if len(item["text"]) > 2 else ""
                )
                link_id = item["link"]

                flattened_toc.append(
                    {
                        "title": title,
                        "page": page,
                        "existing_classification": existing_classification,
                        "link_id": link_id,
                    }
                )

    # Construct prompt
    prompt = f"""
    You are a professional SEC report analyst. Your task is to classify document table of contents items into corresponding Parts and Items according to the EXACT standard structure provided.
    CRITICAL REQUIREMENT: You MUST follow the Standard module structure with ZERO deviation. Any content that does not EXACTLY match the standard structure MUST be classified as "extracted".
    
    Standard module structure (THIS IS THE ONLY VALID REFERENCE):
    '''
    {json.dumps(standard_modules, indent=2, ensure_ascii=False)}
    '''
    
    Document table of contents to classify:
    '''
    {json.dumps(flattened_toc, indent=2, ensure_ascii=False)}
    '''
    
    CLASSIFICATION PROCESS:
    
    STEP 1 - EXACT MATCHING VERIFICATION:
    For each table of contents item, perform EXACT string matching:
    - Check if the Part name exists EXACTLY in the Standard module structure (case-sensitive)
    - Check if the Item name exists EXACTLY under that Part in the Standard module structure
    - Only proceed to Part/Item classification if BOTH conditions are met
    
    STEP 2 - MANDATORY CLASSIFICATION RULES:
    
    A. STANDARD PART/ITEM CLASSIFICATION (Only when EXACT match found):
       - Part names MUST be EXACTLY: "PART I", "PART II", "PART III", or "PART IV" (uppercase, with space)
       - Item names MUST be EXACTLY as defined in the Standard module structure
       - NO variations, abbreviations, or modifications allowed
       - Examples of VALID classifications:
         * part: "PART I", item: "ITEM 1"
         * part: "PART I", item: "ITEM 2" 
         * part: "PART II", item: "ITEM 5"
    
    B. EXTRACTED CLASSIFICATION (When NO exact match found):
       - part: "extracted"
       - item: [USE ORIGINAL TITLE TEXT EXACTLY AS IT APPEARS]
       - Preserve all formatting, capitalization, and special characters
       - Examples of content that MUST be classified as "extracted":
         * Any title not matching standard items exactly
         * "OVERVIEW", "SIGNATURES", "TABLE OF CONTENTS"
         * Company-specific sections like "Citigroup's Five Reportable Business Segments"
         * Any content with different capitalization than standard structure
    
    STEP 3 - PATTERN RECOGNITION FOR GROUPING:
    - Multiple links can map to the same standard Part/Item if they represent subsections
    - Look for continuation patterns (same item split across multiple pages)
    - Group related content only if it matches the SAME standard Part/Item exactly
    
    STEP 4 - MANDATORY VALIDATION CHECKLIST:
    Before finalizing each classification, verify:
    ✓ Part name exists EXACTLY in Standard module structure
    ✓ Item name exists EXACTLY under that Part in Standard module structure  
    ✓ Case sensitivity is correct (PART I, not part i)
    ✓ Spacing is correct (PART I, not PARTI)
    ✓ If ANY verification fails → classify as "extracted"
    
    STEP 5 - COMMON CLASSIFICATION EXAMPLES:
    
    CORRECT Standard Classifications:
    - "MANAGEMENT'S DISCUSSION AND ANALYSIS" → part: "PART I", item: "ITEM 2"
    - "CONSOLIDATED FINANCIAL STATEMENTS" → part: "PART I", item: "ITEM 1"  
    - "UNREGISTERED SALES OF EQUITY SECURITIES" → part: "PART II", item: "ITEM 2"
    
    CORRECT Extracted Classifications:
    - "OVERVIEW" → part: "extracted", item: "OVERVIEW"
    - "SIGNATURES" → part: "extracted", item: "SIGNATURES"
    - "Citigroup's Five Reportable Business Segments" → part: "extracted", item: "Citigroup's Five Reportable Business Segments"
    
    FORBIDDEN ACTIONS:
    ❌ Creating new Part names (like "part i" instead of "PART I")
    ❌ Creating new Item names not in the standard structure
    ❌ Modifying standard Part/Item names in any way
    ❌ Guessing or approximating matches - use "extracted" instead

    REMEMBER: When in doubt, classify as "extracted" rather than forcing a match to the standard structure.
    
    Based on your analysis, provide the retrieval parameters in this exact format:
    {out_parser.get_format_string()}
    """

    result_text = ""
    # Call OpenAI API
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are a professional SEC report analyst skilled at classifying documents according to standard structures. Please return results in JSON format strictly as requested.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )
    
    # Parse response
    result_text = response.choices[0].message.content
    if not result_text:
        print("API returned empty response")
        return []

    result_text = result_text.strip()

    try:
        structured_output: LinkResult = out_parser.parse(result_text)

        # Convert to expected format: List[Tuple[Tuple[str, str], str]]
        results = []
        for link_pos in structured_output.links:
            part = link_pos.part.lower()
            item = link_pos.item

            results.append(((part, item), link_pos.link_id))
        return results

    except Exception as e:
        print(f"Failed to parse AI response: {e}")
        print(f"Raw response: {result_text}")
        return []


# Example usage
if __name__ == "__main__":
    # Example table of contents
    sample_toc = [
        [
            {
                "text": ["", "OVERVIEW", "4", "", "", ""],
                "link": "i3b21a042e4b24a6a8aca8d89b8dbe271_73",
            },
            {
                "text": [
                    "",
                    "Citigroup’s Five Reportable Business Segments",
                    "6",
                    "",
                    "",
                    "",
                ],
                "link": "i3b21a042e4b24a6a8aca8d89b8dbe271_79",
            },
            {
                "text": [
                    "",
                    "MANAGEMENT’S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS",
                    "7",
                    "",
                    "",
                    "",
                ],
                "link": "i3b21a042e4b24a6a8aca8d89b8dbe271_82",
            },
        ],
    ]

    # Get 10-Q standard modules
    standard_modules = TenQ.structure.structure  # Get from the template to be parsed
    # Perform classification
    result = extract_items_with_ai(standard_modules, sample_toc)

    print("Classification results:")
    for item in result:
        print(f"  {item}")

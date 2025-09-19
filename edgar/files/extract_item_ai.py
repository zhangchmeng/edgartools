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
    You are a professional SEC report analyst. Please classify the following document table of contents items into corresponding Parts and Items according to the standard structure of report.

    Standard module structure:
    {json.dumps(standard_modules, indent=2, ensure_ascii=False)}

    Document table of contents:
    {json.dumps(flattened_toc, indent=2, ensure_ascii=False)}

    Please analyze each table of contents item's title and content, mapping them to the most appropriate Part and Item. For items that cannot be clearly classified, mark them as "extracted".


    [
        {{
            "link_id": "link_id of the table of contents item",
            "part": "part i" or "part ii" or "part iii" or "part iv" or "extracted",
            "item": "Item 1" or "Item 2" etc., if extracted then use corresponding title
        }}
    ]

    Classification rule: Signatures are typically marked as part: extracted

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
            results.append(((link_pos.part, link_pos.item), link_pos.link_id))

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

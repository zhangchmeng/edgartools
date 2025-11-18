from bs4 import NavigableString
import re
from typing import List, Dict, Any
from pydantic import BaseModel, Field
from abc import ABC, abstractmethod


# Simplified Pydantic model definitions
class PageContent(BaseModel):
    """Simplified model for F-number page content"""

    page_number: int = Field(..., description="Page number")
    page_soup: Any = Field(..., description="Page's BeautifulSoup object")
    text_content: str = Field("", description="Page text content")
    page_separators: List[Any] = Field(
        default_factory=list, description="List of page separator elements"
    )

    class Config:
        arbitrary_types_allowed = True


class PageNumberExtractionResult(BaseModel):
    """Simplified model for page number extraction result"""

    page_numbers: List[int] = Field(
        default_factory=list, description="List of page numbers"
    )
    page_contents: Dict[int, PageContent] = Field(
        default_factory=dict, description="Dictionary of page contents"
    )

    class Config:
        arbitrary_types_allowed = True


class BasePageSplitter(ABC):
    """
    Base abstract class for page number splitters.
    Defines common interfaces and methods for all splitters.
    """

    @abstractmethod
    def extract_page_numbers(self, html_content: str) -> PageNumberExtractionResult:
        """
        Abstract method to extract page numbers; must be implemented by subclasses.
        
        Args:
            html_content: HTML content string
            
        Returns:
            PageNumberExtractionResult: Page number extraction result
        """
        pass

    @staticmethod
    def _extract_text_content(element):
        """
        Common method to extract text content

        Args:
            element: HTML element or NavigableString

        Returns:
            Extracted text content
        """
        if hasattr(element, "get_text"):
            return element.get_text().strip()
        elif isinstance(element, NavigableString):
            return str(element).strip()
        return ""

    @staticmethod
    def _search_f_number_pattern(text):
        """
        Common F-number pattern search method

        Args:
            text: Text to search

        Returns:
            List of found page numbers
        """

        # F- 10
        patterns = [
            r"^F-(\d+)",  # Basic F-number pattern
            r"^Page\s*F-(\d+)",  # 'Page F-<num>' pattern
            r"^\s*F-(\d+)\s*$",  # Strict F-number pattern
            r"^F-(\d+)\s*$",  # Ends with F-number
            r"^\s*F-(\d+)",  # Starts with F-number
            r"^F-\s*(\d+)",  # Handle whitespace in 'F- 10'
            r"^\s*-\s*F-(\d+)\s*-\s*$",  # Handle '- F-18 -' style
            r"^\s*F\s*-\s*(\d+)\s*$",  # Handle 'F - 10 ' style
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                return [int(match) for match in matches]
        return []


    @staticmethod
    def _create_standard_result(page_numbers, page_contents):
        """
        Create a standardized result format

        Args:
            page_numbers: List of page numbers
            page_contents: Dictionary of page contents

        Returns:
            PageNumberExtractionResult object
        """
        page_count = 0
        for pc in page_contents.values():
            if hasattr(pc, "text_content") and len(pc.text_content) > 100:
                page_count += 1
        if page_count <= 5:
            page_numbers = []
            page_contents = {}
        if not page_numbers:
            return PageNumberExtractionResult(
                page_numbers=[], page_contents={}
            )

        # Page contents are already PageContent objects; use directly and sort by key
        pydantic_page_contents = page_contents
        def _key_sort(k):
            try:
                # First sort numerically (supports numeric strings)
                if isinstance(k, int):
                    return (0, k)
                if isinstance(k, str) and k.isdigit():
                    return (0, int(k))
            except Exception:
                pass
            # Non-numeric keys sorted by lowercase string, placed after numeric keys
            return (1, str(k).lower())

        ordered_keys = sorted(list(pydantic_page_contents.keys()), key=_key_sort)
        ordered_page_contents = {k: pydantic_page_contents[k] for k in ordered_keys}

        # Create result object
        result = PageNumberExtractionResult(
            page_numbers=sorted(list(set(page_numbers))),
            page_contents=ordered_page_contents,
        )

        return result


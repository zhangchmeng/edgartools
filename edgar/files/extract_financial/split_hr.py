from bs4 import BeautifulSoup
from edgar.files.extract_financial.base import (
    BasePageSplitter,
    PageContent,
    PageNumberExtractionResult,
)


class HRPageSplitter(BasePageSplitter):
    """Page number splitter based on HR tags"""

    def extract_page_numbers(
        self, html_content: str
    ):
        """
        Use HR tags as page split markers and extract complete content
        for all F-number pages.

        Args:
            html_content: HTML content string

        Returns:
            A dictionary mapping each page number to its soup fragment
            and element content.
        """
        soup = BeautifulSoup(html_content, "html.parser")
        page_numbers = []
        page_contents = {}

        # Get all HR tags
        hr_tags = soup.find_all("hr")

        # Track the previous valid page split marker
        prev_valid_hr = None
        prev_hr = None
        prev_number = 0

        # Iterate all HR tags and extract page content
        for hr in hr_tags:
            # Find the page number before the HR tag
            page_number = self._find_page_number_before_hr(hr)
            # Guard against None page_number before comparisons
            if page_number is None:
                prev_hr = hr
                continue
            if prev_number == 0 and page_number > 3:
                prev_hr = hr
                continue

            if page_number:
                page_elements = None
                if page_number > prev_number:
                    page_elements = self._extract_page_content_between_hrs(
                        soup, prev_valid_hr or prev_hr, hr
                    )
                elif page_number == prev_number:
                    page_elements = self._extract_page_content_between_hrs(
                        soup, prev_hr, hr
                    )
                elif page_number < prev_number:
                    page_elements = self._extract_page_content_between_hrs(
                        soup, prev_valid_hr or prev_hr, hr
                    )

                if page_elements:
                    # Create page content object
                    page_content = self._create_page_content(
                        page_number, page_elements
                    )
                    page_contents[page_number] = page_content

                    # Add to page number list
                    if page_number not in page_numbers:
                        page_numbers.append(page_number)

                # Update the previous valid page split marker
                prev_valid_hr = hr
                prev_number = page_number
            prev_hr = hr

        # Process the remaining content after the last HR tag
        if prev_valid_hr:
            self._process_remaining_content(
                soup, prev_valid_hr, page_numbers, page_contents
            )

        return self._create_standard_result(page_numbers, page_contents)

    def _find_page_number_before_hr(self, hr):
        """Find page number before an HR tag — check only the first non-empty element; if another HR is encountered, return None."""
        prev_element = getattr(hr, "previous_element", None)

        # Find the first non-empty element
        while prev_element:
            # If another HR tag is encountered, return None to avoid crossing HR boundaries
            if prev_element and getattr(prev_element, "name", "") and getattr(prev_element, "name", "").lower() == "hr":
                    return None

            text = self._extract_text_content(prev_element)
            if text:  # Found the first non-empty element
                # Use the base class F-number pattern search method
                page_numbers = self._search_f_number_pattern(text)
                if page_numbers:
                    return int(page_numbers[0])
                else:
                    if getattr(prev_element, "parent", None):
                        page_numbers = self._search_f_number_pattern(self._extract_text_content(prev_element.parent)) or self._search_f_number_pattern(self._extract_text_content(prev_element.parent.parent))
                        if page_numbers:
                            return int(page_numbers[0])
                    return None  # If the first non-empty element does not match, return None
            # Use the previous element in document order (may cross parent levels and jump to the bottom of the previous parent element)
            prev_element = getattr(prev_element, "previous_element", None)

        return None

    def _extract_page_content_between_hrs(self, soup, prev_hr, current_hr):
        """Extract the complete content between the previous HR tag and the current HR tag as page content, and remove extracted elements from soup"""
        page_elements = []
        # Determine the starting element (sequence is correct; iterate and delete step by step)
        if prev_hr is None:
            start = soup.body if getattr(soup, "body", None) else soup
            current_element = getattr(start, "next_element", None)
        else:
            current_element = getattr(prev_hr, "next_element", None)

        # Extract elements until the current HR
        while current_element is not None and current_element is not current_hr:
            # Skip ancestors of current_hr (do not delete containers, traverse their subtree)
            # Compute the next element after deletion: prefer current node's next_sibling,
            # otherwise backtrack upward to find ancestors' next_sibling
            ns = getattr(current_element, "next_sibling", None)
            if ns is None:
                parent = getattr(current_element, "parent", None)
                while parent is not None:
                    ns = getattr(parent, "next_sibling", None)
                    if ns is not None:
                        break
                    parent = getattr(parent, "parent", None)

            page_elements.append(current_element.extract())

            # Move to the next candidate element (skip over the deleted subtree)
            current_element = ns
        return page_elements

    def _process_remaining_content(
        self, soup, last_hr, page_numbers, page_contents
    ):
        """Process remaining content after the last HR tag"""
        # Extract all content after the last HR tag
        page_elements = self._extract_page_content_between_hrs(
            soup, last_hr, None
        )

        if page_elements:
            # Find page numbers within the extracted elements
            remaining_page_number = self._find_page_number_in_elements(
                page_elements
            )

            # If a page number is found, create a page content object
            if remaining_page_number:
                page_content = self._create_page_content(
                    remaining_page_number, page_elements
                )
                page_contents[remaining_page_number] = page_content

                # Add to the page number list
                if remaining_page_number not in page_numbers:
                    page_numbers.append(remaining_page_number)

    def _find_page_number_in_elements(self, elements):
        """Find page numbers within a list of elements"""
        for element in elements:
            text = self._extract_text_content(element)
            if text:
                # Use the base class F-number pattern search method
                page_numbers = self._search_f_number_pattern(text)
                if page_numbers:
                    return int(page_numbers[0])
        return None

    def _create_page_content(self, page_number, elements):
        """Create a page content object"""
        # Create page soup
        page_soup = BeautifulSoup("", "html.parser")
        page_body = page_soup.new_tag("body")
        page_soup.append(page_body)

        # Batch append elements to avoid per-element extract calls
        for element in elements:
            if hasattr(element, "parent") and element.parent:
                page_body.append(element.extract())
            else:
                page_body.append(element)

        # Call page separator extraction and cleanup methods (optional)
        # separators, cleaned_soup = self._extract_page_separators(page_soup, page_number)

        # Create page content object
        return PageContent(
            page_number=page_number,
            page_soup=page_soup,
            text_content=page_soup.get_text(),
            page_separators=elements,
            # cleaned_soup=cleaned_soup,
        )

if __name__ == "__main__":
    # Test page number extraction functionality
    html_file_path = "/Users/chenghao.zhang/Documents/secfile/extract_financial/test.html"
    # html_file_path = "/Users/chenghao.zhang/Documents/secfile/edgar/000149315225017715.html"
    try:
        # Read HTML file
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        print("=== Refactored page number extraction Test ===")
        print(f"Test file: {html_file_path}")
        print(f"File size: {len(html_content):,} characters\n")

        # Test each splitter class
        print("1. Test HR splitter:")
        hr_splitter = HRPageSplitter()
        hr_results, soup = hr_splitter.extract_page_numbers(html_content)
        print(f"   Found page numbers: {hr_results.page_numbers}")
        print(f"   Total page numbers: {len(hr_results.page_numbers)}")
        print(f"   Page content count: {len(hr_results.page_contents)}")
    except FileNotFoundError:
        print(f"Error: File not found {html_file_path}")
        print("Please ensure the HTML file exists")
    except Exception as e:
        print(f"Error occurred during test: {e}")
        import traceback

        traceback.print_exc()

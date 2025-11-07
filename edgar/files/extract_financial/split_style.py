import re
from bs4 import BeautifulSoup

from edgar.files.extract_financial.base import (
    BasePageSplitter,
    PageContent,
    PageNumberExtractionResult,
)


class StylePageSplitter(BasePageSplitter):
    """Page number splitter based on CSS styles"""

    def extract_page_numbers(
        self, html_content: str
    ) -> PageNumberExtractionResult:
        """
        Find page numbers using CSS style patterns.

        Args:
            html_content: HTML content string

        Returns:
            PageNumberExtractionResult: Structured page number extraction result
        """
        soup = BeautifulSoup(html_content, "html.parser")
        page_numbers = []
        page_contents = {}

        # Find elements with specific styles
        style_elements = self._find_style_elements(soup)

        # Find elements whose class name contains 'page'
        class_elements = self._find_page_class_elements(soup)

        # Merge all candidate elements
        all_elements = style_elements + class_elements

        # Process all candidate elements
        for element in all_elements:
            text_content = self._extract_text_content(element)
            f_numbers = self._search_f_number_pattern(text_content)

            for page_num in f_numbers:
                if page_num not in page_contents:
                    page_numbers.append(page_num)

                    # Create page content object
                    page_content = self._create_page_content(
                        page_num, [element]
                    )
                    page_contents[page_num] = page_content

        return (
            self._create_standard_result(sorted(page_numbers), page_contents),
            soup,
        )

    def _find_style_elements(self, soup):
        """Find elements with specific styles"""
        style_elements = []

        # Common style patterns indicating page numbers
        style_patterns = [
            r"text-align\s*:\s*center",
            r"text-align\s*:\s*right",
            r"position\s*:\s*absolute",
            r"bottom\s*:\s*\d+",
            r"page-break",
        ]

        for pattern in style_patterns:
            elements = soup.find_all(
                attrs={"style": re.compile(pattern, re.IGNORECASE)}
            )
            style_elements.extend(elements)

        return style_elements

    def _find_page_class_elements(self, soup):
        """Find elements with class names containing 'page'"""
        return soup.find_all(
            attrs={"class": re.compile(r"page", re.IGNORECASE)}
        )

    def _create_page_content(self, page_number, elements):
        """Create a page content object"""
        # Create page soup
        page_soup = BeautifulSoup("", "html.parser")

        # Copy elements into the page soup
        for element in elements:
            if hasattr(element, "name"):
                # Copy elements rather than moving them
                element_copy = BeautifulSoup(str(element), "html.parser")
                page_soup.append(element_copy)
            else:
                # Handle text nodes
                page_soup.append(str(element))

        # Extract text content
        text_content = page_soup.get_text().strip()

        # Create page content object
        return PageContent(
            page_number=page_number,
            page_soup=page_soup,
            text_content=text_content,
            page_separators=[],
        )


if __name__ == "__main__":
    # Testpage number提取功能
    html_file_path = "/Users/chenghao.zhang/Documents/secfile/edgar/0000908311-25-000017.html"
    try:
        # 读取HTML文件
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        print("=== 重构后的page number提取Test ===")
        print(f"Test文件: {html_file_path}")
        print(f"file size: {len(html_content):,} 字符\n")

        # Test各个split器类
        print("1. Test样式split器:")
        style_splitter = StylePageSplitter()
        style_results, soup = style_splitter.extract_page_numbers(html_content)
        print(f"   找到page number: {style_results.page_numbers}")
        print(f"   总page number数: {len(style_results.page_numbers)}")
        print(f"   page内容数: {len(style_results.page_contents)}")

    except FileNotFoundError:
        print(f"Error: File not found {html_file_path}")
        print("Please ensure the HTML file exists")
    except Exception as e:
        print(f"Test过程中发生Error: {e}")
        import traceback

        traceback.print_exc()

import json
import os
from bs4 import BeautifulSoup
import re
from typing import List, Dict, Tuple, Any
from pydantic import BaseModel, Field

from openai import OpenAI
from llama_index.core.output_parsers import PydanticOutputParser
from bs4 import BeautifulSoup, Comment


class HTMLClean:
    """
    HTML Cleaner Class

    Specialized for processing HTML content of SEC documents, extracting and formatting table of contents information
    """

    def __init__(self):
        """Initialize HTML cleaner"""
        pass

    def clean_html(self, html) -> str:
        """
        Clean HTML, preserve structure but remove meaningless styles, output HTML format suitable for GPT reading

        Args:
            html: HTML string or BeautifulSoup object

        Returns:
            str: Cleaned HTML string
        """
        try:
            # Parse HTML - if input is a string, parse it first
            if isinstance(html, str):
                soup = BeautifulSoup(html, "html.parser")
            else:
                soup = html

            # Remove unwanted tags and content
            self._remove_unwanted_elements(soup)

            # Preserve important elements
            self._preserve_important_elements(soup)

            # Clean styling attributes
            self._clean_styling_attributes(soup)

            # Simplify HTML structure
            self._simplify_html_structure(soup)

            # Return cleaned HTML string
            return str(soup)

        except Exception as e:
            # If parsing fails, return original content
            return str(html)

    def _remove_unwanted_elements(self, soup):
        """
        Remove unwanted HTML elements while preserving basic structure

        Args:
            soup: BeautifulSoup object
        """
        # Remove scripts, styles, comments and other unwanted elements
        unwanted_tags = [
            "script",
            "style",
            "meta",
            "link",
            "head",
            "title",
            "noscript",
            "iframe",
            "object",
            "embed",
        ]
        for tag in unwanted_tags:
            for element in soup.find_all(tag):
                element.decompose()

        # Remove HTML comments
        for comment in soup.find_all(
            string=lambda text: isinstance(text, Comment)
        ):
            comment.extract()

        # Remove elements with display:none
        for element in soup.find_all(style=True):
            style = element.get("style", "")
            if "display:none" in style or "visibility:hidden" in style:
                element.decompose()

        # Remove completely empty elements (but preserve elements with attributes)
        for element in soup.find_all():
            if (
                not element.get_text(strip=True)
                and not element.find_all(["a", "img", "input", "button"])
                and not element.attrs
            ):
                element.decompose()

    def _clean_styling_attributes(self, soup):
        """
        Clean styling attributes, preserve necessary structural information

        Args:
            soup: BeautifulSoup object
        """
        # Style attributes to be completely removed
        style_attrs_to_remove = ["style", "class", "id"]

        # Important attributes to preserve
        important_attrs = ["href", "src", "alt", "title", "colspan", "rowspan"]

        for element in soup.find_all():
            # Get all attributes of current element
            attrs_to_keep = {}

            # Preserve important attributes
            for attr in important_attrs:
                if attr in element.attrs:
                    attrs_to_keep[attr] = element.attrs[attr]

            # Clear all attributes, then keep only important ones
            element.attrs.clear()
            element.attrs.update(attrs_to_keep)

    def _simplify_html_structure(self, soup):
        """
        Simplify HTML structure, merge unnecessary nesting

        Args:
            soup: BeautifulSoup object
        """
        # Remove redundant whitespace text nodes
        for element in soup.find_all(text=True):
            if element.strip() == "":
                element.extract()

        # Simplify nested div structures
        for div in soup.find_all("div"):
            # If div contains only one child element and has no important attributes, consider expanding
            children = [
                child
                for child in div.children
                if child.name or (hasattr(child, "strip") and child.strip())
            ]
            if (
                len(children) == 1
                and hasattr(children[0], "name")
                and children[0].name in ["div", "span"]
                and not div.attrs
            ):
                # Promote child element content to parent level
                child = children[0]
                div.replace_with(child)

    def _preserve_important_elements(self, soup):
        """
        Ensure important HTML structural elements are preserved

        Args:
            soup: BeautifulSoup object
        """
        # Preserve table structure
        for table in soup.find_all("table"):
            # Ensure basic structural attributes of tables are preserved
            if "colspan" in str(table) or "rowspan" in str(table):
                continue

        # Preserve href attributes of links
        for link in soup.find_all("a"):
            if not link.get("href"):
                # If link has no href attribute, might need to get it from elsewhere
                continue

        # Preserve meaningful text content
        for element in soup.find_all(text=True):
            if element.strip():
                continue


class LinkPosition(BaseModel):
    link_id: str = Field(..., description="Link href")
    part: str = Field(..., description="part")
    item: str = Field(..., description="item")


class LinkResult(BaseModel):
    links: list[LinkPosition]


out_parser = PydanticOutputParser(output_cls=LinkResult)


def extract_items_with_ai(
    standard_modules: Dict[str, Any],
    document_toc: Dict[str, Any],
    form_type: str = "10-Q",
) -> List[Tuple[Tuple[str, str], str]]:
    """
    Use OpenAI API to classify document table of contents according to standard modules

    Args:
        standard_modules: SEC report standard module structure
        document_toc: Document table of contents with structure:
            {
                "table of contents": [list of HTML table strings],
                "link content positions": {link_id: position_in_text, ...}
            }

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

    # Construct prompt
    prompt = f"""
    You are a professional SEC report analyst. Your task is to classify document table of contents items into corresponding Parts and Items according to the EXACT {form_type} standard structure provided.
    
    Standard module structure:
    '''
    {json.dumps(standard_modules, indent=2, ensure_ascii=False)}
    '''
    Document table of contents to classify:
    The document contains multiple table of contents sections with their corresponding link positions in the main text.
    
    Table of Contents HTML:
    '''
    {json.dumps(document_toc.get("table of contents", []), indent=2, ensure_ascii=False)}
    '''
    Link Content Positions (link_id -> position in main text):
    '''
    {json.dumps(document_toc.get("link content positions", {}), indent=2, ensure_ascii=False)}
    '''
    
    CLASSIFICATION PROCESS:
    
    STEP 1 - HIERARCHICAL STRUCTURE IDENTIFICATION:
    Identify the hierarchical structure in the HTML tables:
    - Main items (typically with "Item X." format or major section titles)
    - Sub-items (indented content under main items, often without "Item" prefix)
    - Look for HTML structure patterns like nested <td> elements or indentation
    
    STEP 2 - SUBDIRECTORY CONSOLIDATION RULE:
    CRITICAL: When a main item has multiple sub-items or subdirectories:
    - Consolidate ALL sub-items under the MAIN ITEM classification
    - Use the EARLIEST/SMALLEST position number among all related links
    - Return ONLY ONE result for the entire main item group
    - Example: If "Item 2. Management's Discussion and Analysis" has sub-items like:
      * "Liquidity and capital resources" (position 1621353)
      * "Results of operations" (position 1343388) 
      * "Critical accounting estimates" (position 1650637)
    - Result should be: (('part i', 'ITEM 2'), '#i01a950bf4ece4c298d8a169ac74ff826_196') using the earliest position
    
    STEP 3 - EXACT MATCHING VERIFICATION:
    For each MAIN item (ignoring sub-items), perform EXACT string matching:
    - Check if the Part name exists EXACTLY in the Standard module structure
    - Check if the Item name exists EXACTLY under that Part in the Standard module structure
    
    STEP 4 - MANDATORY CLASSIFICATION RULES:
    
    A. STANDARD PART/ITEM CLASSIFICATION (Only when EXACT match found):
       - Part names MUST be EXACTLY: "PART I", "PART II", "PART III", or "PART IV" 
       - Item names MUST be EXACTLY as defined in the Standard module structure
       - NO variations, abbreviations, or modifications allowed
       - For items with sub-items: Use the link with the SMALLEST position number
       - Examples of VALID classifications:
         * part: "PART I", item: "ITEM 1"
         * part: "PART I", item: "ITEM 2" 
         * part: "PART II", item: "ITEM 5"
    
    B. EXTRACTED CLASSIFICATION (When NO exact match found):
       - part: "extracted"
       - item: [USE ORIGINAL TITLE TEXT EXACTLY AS IT APPEARS]
       - Preserve all formatting, capitalization, and special characters
       - For items with sub-items: Use the link with the SMALLEST position number
       - Examples of content that MUST be classified as "extracted":
         * Any title not matching standard items exactly
         * "OVERVIEW", "SIGNATURES", "TABLE OF CONTENTS"
         * Company-specific sections
    
    C. CRITICAL LINK VALIDATION RULE:
       - ONLY use links that exist in the "Link Content Positions" data
       - NEVER create, invent, or guess link IDs
       - If an item has NO valid links in the positions data, SKIP it entirely
       - Items marked "Not applicable" or without links should be IGNORED
    
    STEP 5 - POSITION-BASED LINK SELECTION:
    When multiple links exist for the same classified item:
    - Sort all related links by their position numbers (ascending)
    - Select the link with the SMALLEST position number
    - This ensures we use the earliest occurrence in the document
    
    STEP 6 - VALIDATION CHECKLIST:
    Before finalizing each classification, verify:
    ✓ Only ONE result per main item (no separate results for sub-items)
    ✓ Link ID corresponds to the earliest position among related links
    ✓ Link ID EXISTS in the "Link Content Positions" data (CRITICAL)
    ✓ Part name exists EXACTLY in Standard module structure (if not "extracted")
    ✓ Item name exists EXACTLY under that Part (if not "extracted")
    ✓ Case sensitivity is correct (PART I, not part i)
    ✓ If ANY verification fails → classify as "extracted" OR SKIP entirely if no valid links
    
    FORBIDDEN ACTIONS:
    ❌ Creating separate results for sub-items of main items
    ❌ Using later position links when earlier ones exist for the same item
    ❌ Creating new Part names (like "part i" instead of "PART I")
    ❌ Creating new Item names not in the standard structure
    ❌ Modifying standard Part/Item names in any way
    ❌ Guessing or approximating matches - use "extracted" instead
    ❌ INVENTING or CREATING link IDs that don't exist in the positions data
    ❌ Including items that have no valid links or are marked "Not applicable"

    REMEMBER: 
    - Consolidate sub-items under their main item
    - Always use the earliest position link for each main item
    - When in doubt, classify as "extracted" rather than forcing a match
    
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

            results.append(((part, item), link_pos.link_id.lstrip("#")))
        return results

    except Exception as e:
        print(f"Failed to parse AI response: {e}")
        print(f"Raw response: {result_text}")
        return []


class HTMLCatalogExtractor:
    """
    HTML catalog extractor, used to extract table of contents from HTML documents
    """

    def __init__(self, content: str):
        """
        Initialize HTML catalog extractor

        """
        self.soup = None
        self.load_html(content)

    def load_html(self, content: str):
        """Load HTML content
        
        Args:
            content: HTML content string
        """
        try:
            if isinstance(content, str):
                self.soup = BeautifulSoup(content, "html.parser")
            else:
                self.soup = content
        except Exception as e:
            print(f"Error loading HTML file: {e}")

    def find_table_of_contents(self) -> List[BeautifulSoup]:
        """
        查找所有TABLE OF CONTENTS元素，根据get_text()方法查找当前向下2000个字符的内容
        确保标签完整闭合，判断区间内的element是否为目录

        Returns:
            包含目录的BeautifulSoup元素列表
        """
        if not self.soup:
            return []

        # 查找包含"TABLE OF CONTENTS"文本的元素
        toc_elements = self.soup.find_all(
            string=re.compile("TABLE OF CONTENTS", re.IGNORECASE)
        )

        if not toc_elements:
            return []

        toc_containers = []

        # 处理每个找到的TABLE OF CONTENTS元素
        for toc_element in toc_elements:
            found_container = self._find_toc_in_2000_chars(toc_element)

            if found_container:
                toc_containers.extend(found_container)

        # 去重，避免重复的容器，并去除被包含的子项
        unique_containers = []
        for container in toc_containers:
            # 检查当前容器是否已存在或被其他容器包含
            is_contained = False
            containers_to_remove = []

            for existing_container in unique_containers:
                # 检查当前容器是否被已存在的容器包含
                if self._is_element_contained_in(
                    container, existing_container
                ):
                    is_contained = True
                    break
                # 检查已存在的容器是否被当前容器包含
                elif self._is_element_contained_in(
                    existing_container, container
                ):
                    containers_to_remove.append(existing_container)

            # 移除被当前容器包含的已存在容器
            for container_to_remove in containers_to_remove:
                unique_containers.remove(container_to_remove)

            # 如果当前容器没有被包含，则添加到结果中
            if not is_contained:
                unique_containers.append(container)

        return unique_containers

    def _is_element_contained_in(self, child_element, parent_element) -> bool:
        """
        Check if one element is contained within another element

        Args:
            child_element: The element that might be contained
            parent_element: The element that might contain the child

        Returns:
            True if child_element is contained within parent_element, False otherwise
        """
        if not child_element or not parent_element:
            return False

        # If two elements are the same, consider it as contained
        if child_element == parent_element:
            return True

        # Check if child_element is a descendant of parent_element
        current = child_element.parent
        while current:
            if current == parent_element:
                return True
            current = current.parent

        return False

    def _find_toc_in_2000_chars(self, start_element) -> List[BeautifulSoup]:
        """
        从指定元素开始，在2000字符范围内查找目录元素
        确保标签完整闭合，将整个范围内的元素作为整体判断

        Args:
            start_element: 起始元素

        Returns:
            找到的目录容器列表
        """
        toc_containers = []
        char_count = 0
        elements_in_range = []  # 收集2000字符范围内的所有元素

        # 使用更全面的元素遍历策略
        elements_to_check = []

        # 1. 从当前元素开始，获取所有后续元素（包括兄弟元素和子元素）
        current = start_element
        while current and char_count < 2000:
            # 首先尝试获取下一个兄弟元素
            next_element = current.find_next_sibling()

            # 如果没有兄弟元素，尝试获取父元素的下一个兄弟
            if not next_element:
                parent = current.parent
                while parent and not next_element:
                    next_element = parent.find_next_sibling()
                    parent = parent.parent

            # 如果还是没有找到，尝试深度优先搜索子元素
            if not next_element:
                # 查找当前元素的所有子元素
                children = current.find_all(recursive=True)
                for child in children:
                    if child not in elements_to_check:
                        elements_to_check.append(child)
                break

            current = next_element

            # 获取当前元素的文本内容
            element_text = current.get_text(strip=True)
            element_text_len = len(element_text)

            # 检查是否会超过2000字符限制
            if char_count + element_text_len > 2000:
                # 如果是重要的完整元素，仍然包含进来
                if self._is_complete_element(current):
                    elements_in_range.append(current)
                break

            # 累加字符数并添加到范围内元素列表
            char_count += element_text_len
            elements_in_range.append(current)

            # 同时检查当前元素的直接子元素
            direct_children = current.find_all(recursive=False)
            for child in direct_children:
                child_text = child.get_text(strip=True)
                if char_count + len(child_text) <= 2000:
                    char_count += len(child_text)
                    if child not in elements_in_range:
                        elements_in_range.append(child)

        # 添加之前收集的子元素
        for element in elements_to_check:
            element_text = element.get_text(strip=True)
            if char_count + len(element_text) <= 2000:
                char_count += len(element_text)
                if element not in elements_in_range:
                    elements_in_range.append(element)

        # 第二步：将整个范围作为整体进行目录判断
        if elements_in_range:
            toc_container = self._analyze_range_as_toc(elements_in_range)
            if toc_container:
                toc_containers.extend(toc_container)

        return toc_containers

    def _is_complete_element(self, element) -> bool:
        """
        Check if element is complete (has both start and end tags)

        Args:
            element: Element to check

        Returns:
            bool: True if element is complete
        """
        # Check if element has proper structure
        if not hasattr(element, "name") or not element.name:
            return False

        # Self-closing tags are considered complete
        if element.name in ["br", "hr", "img", "input", "meta", "link"]:
            return True

        # Check if element has content or children
        return bool(element.get_text(strip=True) or element.find_all())

    def _analyze_range_as_toc(self, elements_in_range) -> List[BeautifulSoup]:
        """
        Analyze a range of elements as potential table of contents
        Based on actual HTML structure features for optimized identification

        Args:
            elements_in_range: List of all elements in the range

        Returns:
            List of found table of contents containers
        """
        toc_containers = []

        # 1. Priority search for table-based table of contents structure
        table_containers = self._find_toc_tables_in_range(elements_in_range)
        if table_containers:
            toc_containers.extend(table_containers)
            return toc_containers

        # 2. Search for div containers with many links
        div_containers = self._find_toc_divs_in_range(elements_in_range)
        if div_containers:
            toc_containers.extend(div_containers)
            return toc_containers

        # 3. Count internal links in entire range as fallback
        total_internal_links = 0
        link_elements = []

        for element in elements_in_range:
            # Find all links
            links = element.find_all("a", href=True)
            for link in links:
                href = link.get("href", "")
                # Check if it's an internal link (starts with #)
                if href.startswith("#"):
                    total_internal_links += 1
                    link_elements.append(link)

        # If there are enough internal links (≥5) in the entire range, consider it as potential TOC
        if total_internal_links >= 5:
            # Sort elements by number of links they contain, prioritize elements with most links
            element_link_counts = []
            for element in elements_in_range:
                element_links = element.find_all(
                    "a", href=lambda x: x and x.startswith("#")
                )
                element_link_counts.append((element, len(element_links)))

            # Sort by link count in descending order
            element_link_counts.sort(key=lambda x: x[1], reverse=True)

            # Return elements with most links
            for element, link_count in element_link_counts:
                if link_count >= 3:  # Elements with at least 3 internal links
                    toc_containers.append(element)

        return toc_containers

    def _find_toc_tables_in_range(
        self, elements_in_range
    ) -> List[BeautifulSoup]:
        """
        在范围内查找目录表格结构

        Args:
            elements_in_range: 范围内的所有元素列表

        Returns:
            找到的目录表格容器列表
        """
        toc_tables = []

        for element in elements_in_range:
            # 查找表格元素
            tables = (
                element.find_all("table")
                if element.name != "table"
                else [element]
            )

            for table in tables:
                # 检查表格是否符合目录特征
                if self._is_toc_table_structure(table):
                    # 找到包含此表格的最近的div容器
                    container = table.find_parent("div")
                    if container and container not in toc_tables:
                        toc_tables.append(container)
                    elif table not in toc_tables:
                        toc_tables.append(table)

        return toc_tables

    def _find_toc_divs_in_range(
        self, elements_in_range
    ) -> List[BeautifulSoup]:
        """
        在范围内查找目录div结构

        Args:
            elements_in_range: 范围内的所有元素列表

        Returns:
            找到的目录div容器列表
        """
        toc_divs = []

        for element in elements_in_range:
            # 查找div元素
            divs = (
                element.find_all("div") if element.name != "div" else [element]
            )

            for div in divs:
                # 检查div是否符合目录特征
                if self._is_toc_div_structure(div):
                    if div not in toc_divs:
                        toc_divs.append(div)

        return toc_divs

    def _is_toc_table_structure(self, table) -> bool:
        """
        Check if table has table of contents structure

        Args:
            table: Table element to check

        Returns:
            bool: True if table has TOC structure
        """
        # 1. Check internal link count in table
        internal_links = table.find_all(
            "a", href=lambda x: x and x.startswith("#")
        )
        if len(internal_links) < 3:
            return False

        # 2. Check table row count (TOC tables usually have multiple rows)
        rows = table.find_all("tr")
        if len(rows) < 3:
            return False

        # 3. Check for typical TOC keywords
        table_text = table.get_text().lower()
        toc_keywords = [
            "item",
            "part",
            "page",
            "financial",
            "information",
            "statement",
            "exhibit",
        ]
        keyword_count = sum(
            1 for keyword in toc_keywords if keyword in table_text
        )

        # 4. Check link density (ratio of links to rows)
        link_density = len(internal_links) / len(rows)

        # Comprehensive judgment: sufficient links + keyword matching + reasonable link density
        return keyword_count >= 2 and link_density >= 0.5

    def _is_toc_div_structure(self, div) -> bool:
        """
        Check if div has table of contents structure

        Args:
            div: Div element to check

        Returns:
            bool: True if div has TOC structure
        """
        # 1. Check internal link count in div
        internal_links = div.find_all(
            "a", href=lambda x: x and x.startswith("#")
        )
        if len(internal_links) < 5:
            return False

        # 2. Check for nested table structures
        nested_tables = div.find_all("table")
        if nested_tables:
            # If contains tables, check if tables have TOC characteristics
            for table in nested_tables:
                if self._is_toc_table_structure(table):
                    return True

        # 3. Check if div text content has TOC characteristics
        div_text = div.get_text().lower()
        toc_keywords = [
            "table of contents",
            "index",
            "item",
            "part",
            "page",
            "financial information",
        ]
        keyword_matches = sum(
            1 for keyword in toc_keywords if keyword in div_text
        )

        # 4. Check if link text contains page references
        page_references = 0
        for link in internal_links:
            link_text = link.get_text().strip()
            # Check if it's a number (page number)
            if link_text.isdigit():
                page_references += 1

        # Comprehensive judgment: many internal links + keyword matching + page references
        return keyword_matches >= 1 and page_references >= 2

    def extract_catalog_tables(self) -> List[Dict[str, Any]]:
        """
        Extract tables containing catalog information, search throughout the document

        Returns:
            List of table information
        """
        catalog_tables = []

        if not self.soup:
            return catalog_tables

        # Get all tables, including tables from various positions in the document
        tables = self.soup.find_all("table")

        for i, table in enumerate(tables):
            a_list = table.find_all("a", href=True)
            internal_link_count = len(
                [a for a in a_list if a.get("href", "").startswith("#")]
            )

            # If table contains more than two valid internal links, consider it as catalog table
            if internal_link_count > 1:
                catalog_tables.append(table)

        return catalog_tables
    def get_catalog_structure(self) -> Dict[str, Any]:
        """
        Get complete catalog structure, including TABLE OF CONTENTS containers and catalog tables

        Returns:
            Dictionary containing toc_containers and catalog_tables
        """
        # Find all TABLE OF CONTENTS containers
        table_containers = self.extract_catalog_tables()
        if table_containers:
            return table_containers
        toc_containers = self.find_table_of_contents()
        if toc_containers:
            return toc_containers
        return []

    def get_catalog_link_position(self, catalogs=None) -> Dict[str, int]:
        """
        Get the position of each href="#xxx" link target in HTML text

        Args:
            catalogs: List of catalog structures, if provided only process links in these catalogs

        Returns:
            Dictionary with link href as key and target element character position in HTML text as value
        """
        if not self.soup:
            return {}

        # Get original HTML text
        html_text = str(self.soup)
        link_positions = {}

        if catalogs is not None:
            # Only process links in catalogs
            catalog_links = set()

            for catalog in catalogs:
                # Convert catalog to BeautifulSoup object (if not already)
                if isinstance(catalog, str):
                    catalog_soup = BeautifulSoup(catalog, "html.parser")
                else:
                    catalog_soup = catalog

                # Find all internal links in catalog
                internal_links = catalog_soup.find_all(
                    "a", href=lambda x: x and x.startswith("#")
                )

                for link in internal_links:
                    href = link.get("href")
                    if href and href.startswith("#"):
                        catalog_links.add(href)

            # Only process links that appear in catalog
            for href in catalog_links:
                # Extract target ID (remove # prefix)
                target_id = href[1:]  # Remove leading #

                # Find target element (by id attribute)
                target_element = self.soup.find(attrs={"id": target_id})

                if target_element:
                    # Get string representation of target element
                    target_str = str(target_element)

                    # Find position of target element in HTML text
                    position = html_text.find(target_str)
                    if position != -1:
                        link_positions[href] = position
        else:
            # Original logic: find all internal links
            internal_links = self.soup.find_all(
                "a", href=lambda x: x and x.startswith("#")
            )

            for link in internal_links:
                href = link.get("href")
                if href and href.startswith("#"):
                    # Extract target ID (remove # prefix)
                    target_id = href[1:]  # Remove leading #

                    # Find target element (by id attribute)
                    target_element = self.soup.find(attrs={"id": target_id})

                    if target_element:
                        # Get string representation of target element
                        target_str = str(target_element)

                        # Find position of target element in HTML text
                        position = html_text.find(target_str)
                        if position != -1:
                            # If same href has multiple links, save position of first target found
                            if href not in link_positions:
                                link_positions[href] = position

        return link_positions

    def get_catalog_structure_from_html(self) -> Dict[str, Any]:
        """
        Get complete catalog structure and link position information

        Returns:
            Dictionary containing catalog structure and link positions
        """
        catalogs = self.get_catalog_structure()
        # Get position for each href="#xxx" link in text, only process links in catalogs
        return {
            "table of contents": [
                HTMLClean().clean_html(catalog) for catalog in catalogs
            ],  # Cleaned catalog HTML content
            "link content positions": self.get_catalog_link_position(
                catalogs
            ),  # Mapping of catalog links to content positions
        }

def extract_catalog_structure(html_content: str, standard_modules: Dict[str, Any], form_type: str = "10-Q"):
    extractor = HTMLCatalogExtractor(html_content)
    result = extractor.get_catalog_structure_from_html()
    ai_result = extract_items_with_ai(standard_modules, result, form_type)
    return ai_result



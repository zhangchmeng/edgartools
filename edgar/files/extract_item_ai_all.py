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
    HTML清理器类

    专门用于处理SEC文档的HTML内容，提取和格式化目录信息
    """

    def __init__(self):
        """初始化HTML清理器"""
        pass

    def clean_html(self, html) -> str:
        """
        清理HTML，保留结构但去除无意义样式，输出适合GPT读取的HTML格式

        Args:
            html: HTML字符串或BeautifulSoup对象

        Returns:
            str: 清理后的HTML字符串
        """
        try:
            # 解析HTML - 如果传入的是字符串，需要先解析
            if isinstance(html, str):
                soup = BeautifulSoup(html, "html.parser")
            else:
                soup = html

            # 移除不需要的标签和内容
            self._remove_unwanted_elements(soup)

            # 保留重要元素
            self._preserve_important_elements(soup)

            # 清理样式属性
            self._clean_styling_attributes(soup)

            # 简化HTML结构
            self._simplify_html_structure(soup)

            # 返回清理后的HTML字符串
            return str(soup)

        except Exception as e:
            # 如果解析失败，返回原始内容
            return str(html)

    def _remove_unwanted_elements(self, soup):
        """
        移除不需要的HTML元素，但保留基本结构

        Args:
            soup: BeautifulSoup对象
        """
        # 移除脚本、样式、注释等不需要的元素
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

        # 移除HTML注释
        for comment in soup.find_all(
            string=lambda text: isinstance(text, Comment)
        ):
            comment.extract()

        # 移除具有display:none的元素
        for element in soup.find_all(style=True):
            style = element.get("style", "")
            if "display:none" in style or "visibility:hidden" in style:
                element.decompose()

        # 移除完全空的元素（但保留有属性的元素）
        for element in soup.find_all():
            if (
                not element.get_text(strip=True)
                and not element.find_all(["a", "img", "input", "button"])
                and not element.attrs
            ):
                element.decompose()

    def _clean_styling_attributes(self, soup):
        """
        清理样式属性，保留必要的结构信息

        Args:
            soup: BeautifulSoup对象
        """
        # 需要完全移除的样式属性
        style_attrs_to_remove = ["style", "class", "id"]

        # 需要保留的重要属性
        important_attrs = ["href", "src", "alt", "title", "colspan", "rowspan"]

        for element in soup.find_all():
            # 获取当前元素的所有属性
            attrs_to_keep = {}

            # 保留重要属性
            for attr in important_attrs:
                if attr in element.attrs:
                    attrs_to_keep[attr] = element.attrs[attr]

            # 清空所有属性，然后只保留重要的
            element.attrs.clear()
            element.attrs.update(attrs_to_keep)

    def _simplify_html_structure(self, soup):
        """
        简化HTML结构，合并不必要的嵌套

        Args:
            soup: BeautifulSoup对象
        """
        # 移除多余的空白文本节点
        for element in soup.find_all(text=True):
            if element.strip() == "":
                element.extract()

        # 简化嵌套的div结构
        for div in soup.find_all("div"):
            # 如果div只包含一个子元素且没有重要属性，考虑展开
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
                # 将子元素的内容提升到父级
                child = children[0]
                div.replace_with(child)

    def _preserve_important_elements(self, soup):
        """
        确保重要的HTML结构元素得到保留

        Args:
            soup: BeautifulSoup对象
        """
        # 保留表格结构
        for table in soup.find_all("table"):
            # 确保表格的基本结构属性得到保留
            if "colspan" in str(table) or "rowspan" in str(table):
                continue

        # 保留链接的href属性
        for link in soup.find_all("a"):
            if not link.get("href"):
                # 如果链接没有href属性，可能需要从其他地方获取
                continue

        # 保留有意义的文本内容
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
    You are a professional SEC report analyst. Your task is to classify document table of contents items into corresponding Parts and Items according to the EXACT standard structure provided.
    
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
    """HTML目录提取器"""

    def __init__(self, content: str):
        """
        初始化HTML目录提取器

        """
        self.soup = None
        self.load_html(content)

    def load_html(self, content: str):
        """加载HTML文件"""
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
        检查一个元素是否被另一个元素包含

        Args:
            child_element: 可能被包含的子元素
            parent_element: 可能包含子元素的父元素

        Returns:
            如果child_element被parent_element包含则返回True，否则返回False
        """
        if not child_element or not parent_element:
            return False

        # 如果两个元素相同，不认为是包含关系
        if child_element == parent_element:
            return True

        # 检查child_element是否是parent_element的后代
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
        判断元素是否为完整的元素（用于标签完整闭合判断）

        Args:
            element: 要检查的元素

        Returns:
            是否为完整元素
        """
        if not element or not hasattr(element, "name"):
            return False

        # 检查是否为重要的块级元素
        important_tags = ["table", "div", "section", "article", "ul", "ol"]
        return element.name in important_tags

    def _analyze_range_as_toc(self, elements_in_range) -> List[BeautifulSoup]:
        """
        分析整个范围内的元素，判断是否包含目录结构
        基于实际HTML结构特征进行优化识别

        Args:
            elements_in_range: 范围内的所有元素列表

        Returns:
            找到的目录容器列表
        """
        toc_containers = []

        # 1. 优先查找包含表格的目录结构
        table_containers = self._find_toc_tables_in_range(elements_in_range)
        if table_containers:
            toc_containers.extend(table_containers)
            return toc_containers

        # 2. 查找包含大量链接的div容器
        div_containers = self._find_toc_divs_in_range(elements_in_range)
        if div_containers:
            toc_containers.extend(div_containers)
            return toc_containers

        # 3. 统计整个范围内的内部链接数量作为后备方案
        total_internal_links = 0
        link_elements = []

        for element in elements_in_range:
            # 查找所有链接
            links = element.find_all("a", href=True)
            for link in links:
                href = link.get("href", "")
                # 检查是否为内部链接（以#开头）
                if href.startswith("#"):
                    total_internal_links += 1
                    link_elements.append(link)

        # 如果整个范围内有足够的内部链接（≥5），认为可能是目录
        if total_internal_links >= 5:
            # 按照元素包含的链接数量排序，优先返回链接最多的元素
            element_link_counts = []
            for element in elements_in_range:
                element_links = element.find_all(
                    "a", href=lambda x: x and x.startswith("#")
                )
                element_link_counts.append((element, len(element_links)))

            # 按链接数量降序排序
            element_link_counts.sort(key=lambda x: x[1], reverse=True)

            # 返回链接数量最多的前几个元素
            for element, link_count in element_link_counts:
                if link_count >= 3:  # 至少包含3个内部链接的元素
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
        检查表格是否具有目录结构特征

        Args:
            table: 表格元素

        Returns:
            是否为目录表格
        """
        # 1. 检查表格内部链接数量
        internal_links = table.find_all(
            "a", href=lambda x: x and x.startswith("#")
        )
        if len(internal_links) < 3:
            return False

        # 2. 检查表格行数（目录表格通常有多行）
        rows = table.find_all("tr")
        if len(rows) < 3:
            return False

        # 3. 检查是否包含典型的目录关键词
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

        # 4. 检查链接密度（链接数量与行数的比例）
        link_density = len(internal_links) / len(rows)

        # 综合判断：链接数量足够 + 关键词匹配 + 合理的链接密度
        return keyword_count >= 2 and link_density >= 0.5

    def _is_toc_div_structure(self, div) -> bool:
        """
        检查div是否具有目录结构特征

        Args:
            div: div元素

        Returns:
            是否为目录div
        """
        # 1. 检查div内部链接数量
        internal_links = div.find_all(
            "a", href=lambda x: x and x.startswith("#")
        )
        if len(internal_links) < 5:
            return False

        # 2. 检查是否包含嵌套的表格结构
        nested_tables = div.find_all("table")
        if nested_tables:
            # 如果包含表格，检查表格是否符合目录特征
            for table in nested_tables:
                if self._is_toc_table_structure(table):
                    return True

        # 3. 检查div的文本内容是否包含目录特征
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

        # 4. 检查链接文本是否包含页码引用
        page_references = 0
        for link in internal_links:
            link_text = link.get_text().strip()
            # 检查是否为数字（页码）
            if link_text.isdigit():
                page_references += 1

        # 综合判断：大量内部链接 + 关键词匹配 + 页码引用
        return keyword_matches >= 1 and page_references >= 2

    def extract_catalog_tables(self) -> List[Dict[str, Any]]:
        """
        提取包含目录信息的表格，在整个文档中搜索

        Returns:
            表格信息列表
        """
        catalog_tables = []

        if not self.soup:
            return catalog_tables

        # 获取所有表格，包括文档各个位置的表格
        tables = self.soup.find_all("table")

        for i, table in enumerate(tables):
            a_list = table.find_all("a", href=True)
            internal_link_count = len(
                [a for a in a_list if a.get("href", "").startswith("#")]
            )

            # 如果表格包含超过两个有效的内部链接，认为是目录表格
            if internal_link_count > 1:
                catalog_tables.append(table)

        return catalog_tables

    def get_catalog_structure(self) -> Dict[str, Any]:
        """
        获取完整的目录结构，包括TABLE OF CONTENTS容器和目录表格

        Returns:
            包含toc_containers和catalog_tables的字典
        """
        # 查找所有TABLE OF CONTENTS容器
        table_containers = self.extract_catalog_tables()
        if table_containers:
            return table_containers
        toc_containers = self.find_table_of_contents()
        if toc_containers:
            return toc_containers
        return []

    def get_catalog_link_position(self, catalogs=None) -> Dict[str, int]:
        """
        获取每个href="#xxx"链接目标在HTML文本中的位置

        Args:
            catalogs: 目录结构列表，如果提供则仅处理这些目录中的链接

        Returns:
            字典，键为链接的href值，值为该链接目标元素在HTML文本中的字符位置
        """
        if not self.soup:
            return {}

        # 获取原始HTML文本
        html_text = str(self.soup)
        link_positions = {}

        if catalogs is not None:
            # 仅处理catalogs中的链接
            catalog_links = set()

            for catalog in catalogs:
                # 将catalog转换为BeautifulSoup对象（如果还不是的话）
                if isinstance(catalog, str):
                    catalog_soup = BeautifulSoup(catalog, "html.parser")
                else:
                    catalog_soup = catalog

                # 查找catalog中的所有内部链接
                internal_links = catalog_soup.find_all(
                    "a", href=lambda x: x and x.startswith("#")
                )

                for link in internal_links:
                    href = link.get("href")
                    if href and href.startswith("#"):
                        catalog_links.add(href)

            # 只处理catalog中出现的链接
            for href in catalog_links:
                # 提取目标ID（去掉#号）
                target_id = href[1:]  # 去掉开头的#

                # 查找目标元素（通过id属性）
                target_element = self.soup.find(attrs={"id": target_id})

                if target_element:
                    # 获取目标元素的字符串表示
                    target_str = str(target_element)

                    # 在HTML文本中查找目标元素的位置
                    position = html_text.find(target_str)
                    if position != -1:
                        link_positions[href] = position
        else:
            # 原有逻辑：查找所有内部链接
            internal_links = self.soup.find_all(
                "a", href=lambda x: x and x.startswith("#")
            )

            for link in internal_links:
                href = link.get("href")
                if href and href.startswith("#"):
                    # 提取目标ID（去掉#号）
                    target_id = href[1:]  # 去掉开头的#

                    # 查找目标元素（通过id属性）
                    target_element = self.soup.find(attrs={"id": target_id})

                    if target_element:
                        # 获取目标元素的字符串表示
                        target_str = str(target_element)

                        # 在HTML文本中查找目标元素的位置
                        position = html_text.find(target_str)
                        if position != -1:
                            # 如果同一个href有多个链接，保存第一个找到的目标位置
                            if href not in link_positions:
                                link_positions[href] = position

        return link_positions

    def get_catalog_structure_from_html(self) -> Dict[str, Any]:
        """
        获取完整的目录结构和链接位置信息

        Returns:
            包含目录结构和链接位置的字典
        """
        catalogs = self.get_catalog_structure()
        # 给出每个href="#xxx"的链接在文本的position，仅处理catalogs中的链接
        return {
            "table of contents": [
                HTMLClean().clean_html(catalog) for catalog in catalogs
            ],  # 清理后的目录HTML内容
            "link content positions": self.get_catalog_link_position(
                catalogs
            ),  # 目录链接对应的正文位置映射
        }


def extract_catalog_structure(html_content: str, standard_modules: Dict[str, Any]):
    extractor = HTMLCatalogExtractor(html_content)
    result = extractor.get_catalog_structure_from_html()
    ai_result = extract_items_with_ai(standard_modules, result)
    return ai_result

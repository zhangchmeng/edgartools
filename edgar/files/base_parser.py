import re
import logging
from typing import List, Dict, Optional, Any, Union

try:
    from bs4 import BeautifulSoup, Tag
except ImportError:
    # 在运行环境缺少 bs4 时，避免类型或运行时错误
    BeautifulSoup = None
    Tag = None

def replace_space(text: str):
    html_space_entities = {
        '&nbsp;': ' ',      # 不间断空格
        '&ensp;': ' ',      # 半角空格
        '&emsp;': ' ',      # 全角空格
        '&thinsp;': ' ',    # 窄空格
        '&#160;': ' ',      # 不间断空格的数字实体
        '&#8194;': ' ',     # 半角空格的数字实体
        '&#8195;': ' ',     # 全角空格的数字实体
        '&#8201;': ' ',     # 窄空格的数字实体
        '&#32;': ' ',       # 普通空格的数字实体
        '\xa0': ' ',       # 非断行空格字符
        '\u00A0': ' ',     # 非断行空格的Unicode表示
    }
    
    for entity, space in html_space_entities.items():
        text = text.replace(entity, space)
    return text
        


class BaseHtmlParser:
    """Base HTML parser class containing common methods for ParsedHtml10K and ParsedHtml10Q"""

    def _contains_page_numbers(self, text: str) -> bool:
        """
        Unified page number detection method supporting multiple page number formats.

        Examples:
        - "7-24, 82-87, 90-101" -> True
        - "156-157" -> True
        - "155" -> True
        - "Not Applicable" -> False
        - "(a), 158" -> True
        - "Pages 3 - 24" -> True
        - "Page 41" -> True
        """
        if not text:
            return False

        text = text.strip()

        # Handle "Not Applicable" cases
        if "not applicable" in text.lower():
            return False

        # Handle "Page X" or "Pages X - Y" format
        if re.match(r"^Page\s+\d{1,4}$", text) or re.match(
            r"^Pages\s+\d{1,4}\s*-\s*\d{1,4}$", text
        ):
            return True

        # Single number (1-4 digits)
        if re.match(r"^\d{1,4}$", text):
            return True

        # Simple range: "7-24", "82-87"
        if re.match(r"^\d{1,4}-\d{1,4}$", text):
            return True

        # Multiple ranges: "7-24, 82-87", "7-24, 82-87, 90-101"
        if re.match(r"^\d{1,4}-\d{1,4}(,\s*\d{1,4}-\d{1,4})+$", text):
            return True

        # Ranges with parentheses: "7-24 (Restated)", "82-87 (Revised)"
        if re.match(r"^\d{1,4}-\d{1,4}\s*\([^)]+\)$", text):
            return True

        # Multiple numbers separated by commas: "5, 10, 15"
        if re.match(r"^\d{1,4}(,\s*\d{1,4})+$", text):
            return True

        # Handle cases with parentheses like "(a), 158"
        if "(" in text and ")" in text:
            # Extract parts after parentheses
            parts = text.split(")")
            if len(parts) > 1:
                remaining = parts[-1].strip().lstrip(",").strip()
                if remaining and remaining.isdigit():
                    return True

        # Handle comma-separated page ranges like "7-24, 82-87, 90-101"
        parts = [part.strip() for part in text.split(",")]
        for part in parts:
            if part.isdigit():  # Single page number
                return True
            elif "-" in part:  # Page range
                range_parts = part.split("-")
                if len(range_parts) == 2 and all(
                    p.strip().isdigit() for p in range_parts
                ):
                    return True

        return False

    def _contains_multiple_page_ranges(self, text: str) -> bool:
        """
        Check if text contains multiple page ranges indicating split sections.

        Examples:
        - "7-24, 82-87, 90-101" -> True
        - "60-81, 101-106" -> True
        - "156-157" -> False
        - "155" -> False
        """
        if not text or "," not in text:
            return False

        # Count comma-separated parts that look like page numbers/ranges
        parts = [part.strip() for part in text.split(",")]
        valid_parts = 0

        for part in parts:
            if part.isdigit() or (
                "-" in part
                and len(part.split("-")) == 2
                and all(p.strip().isdigit() for p in part.split("-"))
            ):
                valid_parts += 1

        return valid_parts > 1

    def _is_multi_section_item(
        self, text: List[str], links: List[str]
    ) -> bool:
        """
        Unified method to determine if an item has multiple sections.

        Args:
            text: List of cell text content for this row
            links: List of links found in this row

        Returns:
            True if item appears to be split across multiple sections
        """
        if not text or not links or len(links) <= 1:
            return False

        # Check if page numbers indicate multiple sections
        for cell_text in text:
            if self._contains_multiple_page_ranges(cell_text):
                return True

        # Check if text content indicates this is a potentially multi-section item
        text_content = " ".join(text).lower()

        # Certain items are more likely to span multiple sections
        multi_section_indicators = [
            "management's discussion",
            "financial statements",
            "controls and procedures",
            "risk factors",
            "business",
        ]

        for indicator in multi_section_indicators:
            if indicator in text_content:
                return True

        return False

    def _filter_range_end_links(
        self, cell_text: str, cell_links: List[str], link_texts: List[str]
    ) -> List[str]:
        """
        Unified method to filter range end links.
        Filters out end links in page ranges, e.g. for "7-24, 82-87, 90-101" only keeps start links (7, 82, 90).

        Args:
            cell_text: The full text content of the cell
            cell_links: List of all links found in the cell
            link_texts: List of text content for each link

        Returns:
            Filtered list of links with range end links removed
        """
        if not cell_links or not cell_text:
            return cell_links

        # If only one link, keep it
        if len(cell_links) <= 1:
            return cell_links

        # Check if it contains page number ranges
        if not self._contains_page_numbers(cell_text):
            return cell_links

        # Extract all page number ranges
        ranges = []

        # Match single range: "7-24"
        simple_ranges = re.findall(r"(\d+)-(\d+)", cell_text)
        for start, end in simple_ranges:
            ranges.append((int(start), int(end)))

        if not ranges:
            return cell_links

        # Collect end page numbers of all ranges
        end_pages = set()
        for start, end in ranges:
            end_pages.add(str(end))

        # Filter links: remove those whose text content is the end page number of ranges
        filtered_links = []
        for i, link in enumerate(cell_links):
            link_text = link_texts[i] if i < len(link_texts) else ""

            # If link text is not the end page number of ranges, keep it
            if link_text not in end_pages:
                filtered_links.append(link)
            else:
                # Check if it's also the start page number of some range
                is_start_page = False
                for start, end in ranges:
                    if link_text == str(start):
                        is_start_page = True
                        break

                # If it's both an end page number and a start page number, keep it
                if is_start_page:
                    filtered_links.append(link)
                else:
                    logging.info(
                        f"Range detected in '{cell_text}': omitting end link for page {link_text}"
                    )

        # If no links remain after filtering, return original link list
        if not filtered_links:
            return cell_links

        return filtered_links

    def _parse_html_content(
        self, html_content: str
    ) -> Optional[Any]:
        """
        Unified HTML content parsing method.

        Args:
            html_content: HTML content to parse

        Returns:
            BeautifulSoup object or None if parsing fails
        """
        if not html_content:
            return None

        html_content = html_content.replace("&nbsp;", " ")

        # Guard: BeautifulSoup may be None if bs4 is unavailable
        if BeautifulSoup is None:
            logging.error("BeautifulSoup is not available; cannot parse HTML content")
            return None

        try:
            soup = BeautifulSoup(html_content, "html.parser")
        except Exception as e:
            logging.error(f"Failed to parse HTML: {e}")
            return None

        if soup is None:
            return None

        # Remove script and style tags（避免调用 soup([...]) 导致 None 被调用的问题）
        for tag in soup.find_all(["script", "style", "noscript"]):
            tag.decompose()

        return soup

    def _extract_table_links_base_no_pagenumber(
        self, soup: Any, use_part_detection: bool = False
    ) -> List[List[Dict[str, Any]]]:
        """
        Base method to extract links from HTML tables.
        Enhanced to handle item rows without links by using first sub-item's link.

        Args:
            soup: BeautifulSoup object
            use_part_detection: Whether to detect part information (for 10-Q)

        Returns:
            List of tables with link information
        """
        if soup is None:
            return []
        link_info: List[List[Dict[str, Any]]] = []
        tables = soup.find_all("table")

        part_regex = (
            re.compile(r"^\s*(Part\s+[IVXLC]+)\s*", re.IGNORECASE)
            if use_part_detection
            else None
        )
        part = None

        for table_idx, table in enumerate(tables):
            table_links: List[Dict[str, Any]] = []
            rows = table.find_all("tr")

            # Process rows and handle item rows without links
            for row_idx, row in enumerate(rows):
                if use_part_detection and part_regex:
                    row_text = row.get_text().strip()
                    part_match = part_regex.match(row_text)
                    if part_match:
                        part = re.sub(r"\s+", " ", part_match.group(1).lower())

                cells = row.find_all("td", recursive=False)

                if cells:
                    has_links = any(cell.find("a") for cell in cells)

                    text = []
                    for cell in cells:
                        cell_text = cell.get_text(
                            separator="" if not use_part_detection else "  ",
                            strip=True,
                        )
                        if use_part_detection and len(cell_text) > 500:
                            cell_text = cell_text[:500]
                        text.append(
                            " ".join(cell_text.split())
                            if not use_part_detection
                            else cell_text
                        )


                    # Handle rows with links
                    if has_links:
                        row_links = self._extract_row_links(
                            cells,
                            text,
                            (
                                "extracted"
                                if "signature" in row.get_text().lower()
                                else part
                            ),
                            use_part_detection,
                        )
                        if row_links:
                            table_links.extend(row_links)
                    elif has_links and self._is_item_header_row(text):
                        row_links = self._extract_row_links(
                            cells,
                            text,
                            (
                                "extracted"
                                if "signature" in row.get_text().lower()
                                else part
                            ),
                            use_part_detection,
                        )
                        if row_links:
                            table_links.extend(row_links)
                    # Handle item rows without links (e.g., "Item 1. Financial Statements")
                    elif self._is_item_header_row(text):
                        # Look for the first sub-item with a link in subsequent rows
                        first_subitem_link = self._find_first_subitem_link(
                            rows, row_idx + 1
                        )
                        if first_subitem_link:
                            # Create a link entry using the item text and first sub-item's link
                            item_link_data = {
                                "text": text,
                                "link": first_subitem_link,  # Add 'link' key for compatibility
                                "links": [first_subitem_link],
                                "is_multi_section": False,
                                "link_count": 1,
                                "is_item_header": True,  # Mark as item header for identification
                            }
                            if use_part_detection and part:
                                item_link_data["part"] = part
                            table_links.append(item_link_data)
                            logging.info(
                                f"Item header without link found: {text[0] if text else 'Unknown'}, using first sub-item link: {first_subitem_link}"
                            )

            if table_links:
                link_info.append(table_links)

        return link_info

    def _extract_table_links_base(
        self, soup: Any, use_part_detection: bool = False
    ) -> List[List[Dict[str, Any]]]:
        """
        Base method to extract links from HTML tables.
        Enhanced to handle item rows without links by using first sub-item's link.

        Args:
            soup: BeautifulSoup object
            use_part_detection: Whether to detect part information (for 10-Q)

        Returns:
            List of tables with link information
        """
        if soup is None:
            return []
        link_info: List[List[Dict[str, Any]]] = []
        tables = soup.find_all("table")

        part_regex = (
            re.compile(r"^\s*(Part\s+[IVXLC]+)\s*", re.IGNORECASE)
            if use_part_detection
            else None
        )
        part = None

        for table_idx, table in enumerate(tables):
            table_links: List[Dict[str, Any]] = []
            rows = table.find_all("tr")

            # Process rows and handle item rows without links
            for row_idx, row in enumerate(rows):
                # if "Item" in row.get_text():
                #     import pdb;pdb.set_trace()
                if use_part_detection and part_regex:
                    row_text = row.get_text().strip()
                    part_match = part_regex.match(row_text)
                    if part_match:
                        part = re.sub(r"\s+", " ", part_match.group(1).lower())

                cells = row.find_all("td", recursive=False)
                exist_page_num = False

                if cells:
                    has_links = any(cell.find("a") for cell in cells)

                    text = []
                    for cell in cells:
                        cell_text = cell.get_text(
                            separator="" if not use_part_detection else "  ",
                            strip=True,
                        )
                        if use_part_detection and len(cell_text) > 500:
                            cell_text = cell_text[:500]
                        text.append(
                            " ".join(cell_text.split())
                            if not use_part_detection
                            else cell_text
                        )

                    # Check if row contains page numbers
                    for cell in cells:
                        cell_text = cell.text.strip()
                        if use_part_detection and len(cell_text) > 100:
                            continue

                        if cell_text.isdigit() or self._contains_page_numbers(
                            cell_text
                        ):
                            exist_page_num = True
                            break

                    # Handle rows with links
                    if has_links and exist_page_num:
                        row_links = self._extract_row_links(
                            cells,
                            text,
                            (
                                "extracted"
                                if "signature" in row.get_text().lower()
                                else part
                            ),
                            use_part_detection,
                        )
                        if row_links:
                            table_links.extend(row_links)
                    elif has_links and self._is_item_header_row(text):
                        row_links = self._extract_row_links(
                            cells,
                            text,
                            (
                                "extracted"
                                if "signature" in row.get_text().lower()
                                else part
                            ),
                            use_part_detection,
                        )
                        if row_links:
                            table_links.extend(row_links)
                    # Handle item rows without links (e.g., "Item 1. Financial Statements")
                    elif self._is_item_header_row(text):
                        # Look for the first sub-item with a link in subsequent rows
                        first_subitem_link = self._find_first_subitem_link(
                            rows, row_idx + 1
                        )
                        if first_subitem_link:
                            # Create a link entry using the item text and first sub-item's link
                            item_link_data = {
                                "text": text,
                                "link": first_subitem_link,  # Add 'link' key for compatibility
                                "links": [first_subitem_link],
                                "is_multi_section": False,
                                "link_count": 1,
                                "is_item_header": True,  # Mark as item header for identification
                            }
                            if use_part_detection and part:
                                item_link_data["part"] = part
                            table_links.append(item_link_data)
                            logging.info(
                                f"Item header without link found: {text[0] if text else 'Unknown'}, using first sub-item link: {first_subitem_link}"
                            )

            if table_links:
                link_info.append(table_links)

        return link_info

    def _is_item_header_row(self, text: List[str]) -> bool:
        """
        Check if this row is an item header (like "Item 1. Financial Statements")
        that typically doesn't have its own link but should use the first sub-item's link.

        Args:
            text: List of cell text content for this row

        Returns:
            True if this appears to be an item header row
        """
        if not text:
            return False

        # Check first cell for item pattern
        first_cell = text[0].strip() if text else ""
        
        first_cell = replace_space(first_cell)
        
        # 将所有连续的空白字符（换行、制表符、多个空格等）替换为单个空格
        first_cell = re.sub(r'\s+', ' ', first_cell).strip()

        # Match patterns like "Item 1.", "Item 1A.", "Item 1B.", etc.
        # Match two formats:
        # 1. "Item X." format
        # 2. "PART X Item X." format
        item_pattern = re.compile(
            r"^(PART\s+[IVX]+\s+)?Item\s+\d+[A-Z]?\.\s*", re.IGNORECASE
        )
        if item_pattern.match(first_cell):
            return True

        # Also check for underlined item headers (common in HTML)
        for cell_text in text:
            cell_text = replace_space(cell_text)
            if re.search(r"Item\s+\d+[A-Z]?\s*", cell_text, re.IGNORECASE):
                return True

        return False

    def _find_first_subitem_link(
        self, rows: List[Any], start_idx: int
    ) -> Optional[str]:
        """
        Find the first sub-item link in subsequent rows.

        Args:
            rows: List of all table rows
            start_idx: Index to start searching from

        Returns:
            First link found in sub-items, or None if not found
        """
        for i in range(
            start_idx, min(start_idx + 10, len(rows))
        ):  # Look ahead max 10 rows
            row = rows[i]
            cells = row.find_all("td", recursive=False)

            if not cells:
                continue

            # Check if this row has links and appears to be a sub-item
            has_links = any(cell.find("a") for cell in cells)
            if has_links:
                # Check if it's indented or appears to be a sub-item
                row_text = row.get_text().strip()

                # Skip if this looks like another main item
                if re.match(
                    r"^Item\s+\d+[A-Z]?\.\s*", row_text, re.IGNORECASE
                ):
                    break  # Stop if we hit another main item

                # Find the first link in this row
                for cell in cells:
                    link_elem = cell.find("a")
                    if (
                        link_elem
                        and link_elem.attrs.get("href")
                        and link_elem.attrs.get("href").startswith("#")
                    ):
                        return link_elem.attrs.get("href").split("#")[-1]

        return None

    def _extract_row_links(self, cells, text, part, use_part_detection):
        """
        Extract links from a table row.

        Args:
            cells: Table cells
            text: Cell text content
            part: Part information (for 10-Q)
            use_part_detection: Whether this is for 10-Q processing

        Returns:
            List of link dictionaries
        """
        if use_part_detection:
            # 10-Q style: only first valid link
            for cell in cells:
                link_elem = cell.find("a")
                if (
                    link_elem
                    and link_elem.attrs.get("href")
                    and link_elem.attrs.get("href").startswith("#")
                ):
                    link = link_elem.attrs.get("href").split("#")[-1]
                    if part:
                        return [{"part": part, "text": text, "link": link}]
                    else:
                        return [{"text": text, "link": link}]
            return []
        else:
            # 10-K style: multiple links with filtering
            row_links = []
            for cell in cells:
                cell_text = cell.get_text(strip=True)
                link_elems = cell.find_all("a")
                cell_links = []
                link_texts = []

                for link_elem in link_elems:
                    if link_elem.attrs.get("href") and link_elem.attrs.get(
                        "href"
                    ).startswith("#"):
                        link = link_elem.attrs.get("href").split("#")[-1]
                        link_text = link_elem.get_text(strip=True)
                        cell_links.append(link)
                        link_texts.append(link_text)

                filtered_links = self._filter_range_end_links(
                    cell_text, cell_links, link_texts
                )
                row_links.extend(filtered_links)

            if row_links:
                is_multi_section = self._is_multi_section_item(text, row_links)
                result = [
                    {
                        "text": text,
                        "links": row_links,
                        "is_multi_section": is_multi_section,
                        "link_count": len(row_links),
                    }
                ]

                if is_multi_section and len(row_links) > 1:
                    logging.info(
                        f"Multi-section item detected: {text[0] if text else 'Unknown'} with {len(row_links)} links"
                    )

                return result

            return []

    def _extract_div_links2(self, soup, use_part_detection: bool = False):
        """
        查找包含多个 href=# 的元素，并将合适大小的元素处理，给出合理的判断元素边界的逻辑
        """
        if soup is None:
            return []
        # 辅助函数：判断元素是否为合适的容器边界
        def is_suitable_container(element) -> bool:
            """判断元素是否为合适的处理容器"""
            # 使用鸭式类型判断，避免 Tag 未绑定或类型错误
            if not hasattr(element, "find_all") or not hasattr(element, "get_text"):
                return False
            
            # 获取元素文本长度和链接数量
            text = element.get_text(strip=True)
            
            # 查找带有 href=# 的链接
            links = []
            for a in element.find_all("a"):
                href = a.get("href")
                if href and isinstance(href, str) and href.startswith("#"):
                    links.append(a)
            
            # 基本条件：有文本内容且不为空
            if not text or len(text) < 5:
                return False
            
            # 文本长度合理性检查：不能太短也不能太长
            text_length = len(text)
            if text_length < 20 or text_length > 2000:
                return False
            
            # 如果包含多个链接，优先处理
            if len(links) >= 2:
                return True
            return False

        # 辅助函数：判断元素边界和分组, 获取可能合适的子元素
        def find_element_boundaries(container):
            """找到元素的合理边界，将相关元素分组（返回一维列表，且不添加父元素）"""
            flat: List[Any] = []
            for child in getattr(container, "children", []):
                # 仅在子元素是合适容器时添加，并递归收集其合适的子节点
                if is_suitable_container(child):
                    # flat.append(child)
                    flat.extend(find_element_boundaries(child))

            if not flat and is_suitable_container(container):
                flat.append(container)
            return flat

        # 获取顶层容器
        body = soup.find("body") or soup
        # 首先查找包含多个链接的大容器
        multi_link_containers = []
        for container in body.children:
            # 查找带有 href=# 的链接
            if not hasattr(container, "find_all") or not hasattr(container, "get_text"):
                continue
            
            links = []
            for a in container.find_all("a"):
                href = a.get("href")
                if href and isinstance(href, str) and href.startswith("#"):
                    links.append(a)
            
            if len(links) >= 2:  # 包含多个链接的容器
                text_content = container.get_text(strip=True)
                if text_content and len(text_content) > 20:  # 有足够的文本内容
                    multi_link_containers.append(container)

        results_tables = []
        for container in multi_link_containers:
            results_tables.extend(find_element_boundaries(container))
        
        # 如果没有找到合适的容器，返回空结果
        if not results_tables:
            return []
        
        # 从 results_tables 中提取位置信息
        positioned_divs = []
        for table in results_tables:
            for div in table.children:
                if not(hasattr(div, 'get_text') and hasattr(div, 'get')):
                    continue
                if hasattr(div, 'get') and div.get('style'):
                    style = div.get('style', '')
                    # 提取 top 位置
                    top_match = re.search(r'top:(\d+(?:\.\d+)?)px', style)
                    if top_match:
                        top_pos = float(top_match.group(1))
                        positioned_divs.append((top_pos, div))
                    else:
                        # 如果没有 top 样式，使用默认位置 0
                        positioned_divs.append((0, div))
                else:
                    # 如果没有样式信息，使用默认位置 0
                    positioned_divs.append((0, div))
        
        # 按位置排序
        positioned_divs.sort(key=lambda x: x[0])
        
        # Group by rows with tolerance
        rows = []
        current_row = []
        current_top = None
        tolerance = 5

        for top_pos, div in positioned_divs:
            if current_top is None or abs(top_pos - current_top) <= tolerance:
                current_row.append(div)
                current_top = top_pos
            else:
                if current_row:
                    rows.append(current_row)
                current_row = [div]
                current_top = top_pos

        if current_row:
            rows.append(current_row)

        # Parse rows
        table_links = []
        part = None
        part_regex = (
            re.compile(r"^\s*(Part\s+[IVXLC]+)\s*", re.IGNORECASE)
            if use_part_detection
            else None
        )

        for row_divs in rows:
            has_links = any(div.find("a") for div in row_divs)

            if not has_links:
                if part_regex:
                    row_text = " ".join(
                        div.get_text(strip=True) for div in row_divs
                    )
                    part_match = part_regex.match(row_text)
                    if part_match:
                        part = re.sub(r"\s+", " ", part_match.group(1).lower())
                continue

            # # Check for page numbers
            # has_page_num = any(
            #     div.get_text(strip=True).isdigit()
            #     or self._contains_page_numbers(div.get_text(strip=True))
            #     for div in row_divs
            # )

            # if not has_page_num:
            #     continue

            # Extract text and links
            text_parts = []
            row_links = []
            page_texts = []

            # Sort by left position
            sorted_divs = []
            for div in row_divs:
                style = div.get("style", "")
                left_match = re.search(r"left:(\d+(?:\.\d+)?)px", style)
                if left_match:
                    left_pos = float(left_match.group(1))
                    sorted_divs.append((left_pos, div))

            sorted_divs.sort(key=lambda x: x[0])

            for left_pos, div in sorted_divs:
                div_text = div.get_text(strip=True)

                if div_text:
                    if div_text.isdigit() or self._contains_page_numbers(
                        div_text
                    ):
                        page_texts.append(div_text)
                    elif not self._contains_page_numbers(div_text):
                        text_parts.append(div_text)

                # Extract links
                for link in div.find_all("a"):
                    href = link.get("href")
                    if href and href.startswith("#"):
                        row_links.append(href.split("#")[-1])

            if row_links and text_parts:
                # Filter links
                if page_texts:
                    page_text = page_texts[0]
                    link_texts = [
                        link.get_text(strip=True)
                        for div in row_divs
                        for link in div.find_all("a")
                        if link.get_text(strip=True)
                    ]
                    row_links = self._filter_range_end_links(
                        page_text, row_links, link_texts
                    )

                # Create entry based on format
                if use_part_detection:  # 10-Q format
                    first_link = row_links[0] if row_links else None
                    if first_link:
                        # 统一返回结构，增加多段标识与链接计数
                        is_multi_section = self._is_multi_section_item(
                            text_parts, row_links
                        )
                        entry = {
                            "text": text_parts,
                            "link": first_link,
                            "is_multi_section": is_multi_section,
                            "link_count": len(row_links),
                        }
                        if part:
                            entry["part"] = part
                        if "signature" in "".join(text_parts).lower():
                            entry["part"] = "extracted"

                        table_links.append(entry)

                        if len(row_links) > 1:
                            logging.info(
                                f"Multi-section item detected in div (10Q): {text_parts[0] if text_parts else 'Unknown'} with {len(row_links)} links, using first link only"
                            )
                else:  # 10-K format
                    is_multi_section = self._is_multi_section_item(
                        text_parts, row_links
                    )
                    table_links.append(
                        {
                            "text": text_parts,
                            "links": row_links,
                            "is_multi_section": is_multi_section,
                            "link_count": len(row_links),
                        }
                    )

                    if is_multi_section and len(row_links) > 1:
                        logging.info(
                            f"Multi-section item detected in div: {text_parts[0] if text_parts else 'Unknown'} with {len(row_links)} links"
                        )

        return [table_links] if table_links else []

    def _extract_div_links_base(
        self, html_content: str, use_part_detection: bool = False
    ) -> List[List[Dict[str, Any]]]:
        """Extract table of contents from div structure (common logic for both 10-K and 10-Q)"""
        if not html_content:
            return []

        # html_content = html_content.replace("&nbsp;", " ")
        html_content = replace_space(html_content)

        soup = self._parse_html_content(html_content)
        if soup is None:
            return []

        # Find div containing "TABLE OF CONTENTS"
        toc_div = None
        for div in soup.find_all("div"):
            if div.get_text(strip=True) == "TABLE OF CONTENTS":
                toc_div = div.find_parent("div")
                break

        if not toc_div:
            logging.warning("TABLE OF CONTENTS div not found")
            return self._extract_div_links2(soup, use_part_detection)
        else:
            # Get positioned divs and sort by top position
            positioned_divs = []
            for div in toc_div.find_all("div"):
                style = div.get("style", "")
                if "position:absolute" in style and "top:" in style:
                    try:
                        top_match = re.search(r"top:(\d+(?:\.\d+)?)px", style)
                        if top_match:
                            top_pos = float(top_match.group(1))
                            positioned_divs.append((top_pos, div))
                    except:
                        continue
            if not positioned_divs:
                return []

        positioned_divs.sort(key=lambda x: x[0])

        # Group by rows with tolerance
        rows = []
        current_row = []
        current_top = None
        tolerance = 5

        for top_pos, div in positioned_divs:
            if current_top is None or abs(top_pos - current_top) <= tolerance:
                current_row.append(div)
                current_top = top_pos
            else:
                if current_row:
                    rows.append(current_row)
                current_row = [div]
                current_top = top_pos

        if current_row:
            rows.append(current_row)

        # Parse rows
        table_links = []
        part = None
        part_regex = (
            re.compile(r"^\s*(Part\s+[IVXLC]+)\s*", re.IGNORECASE)
            if use_part_detection
            else None
        )

        for row_divs in rows:
            has_links = any(div.find("a") for div in row_divs)

            if not has_links:
                if part_regex:
                    row_text = " ".join(
                        div.get_text(strip=True) for div in row_divs
                    )
                    part_match = part_regex.match(row_text)
                    if part_match:
                        part = re.sub(r"\s+", " ", part_match.group(1).lower())
                continue

            # Check for page numbers
            # has_page_num = any(
            #     div.get_text(strip=True).isdigit()
            #     or self._contains_page_numbers(div.get_text(strip=True))
            #     for div in row_divs
            # )

            # if not has_page_num:
            #     continue

            # Extract text and links
            text_parts = []
            row_links = []
            page_texts = []

            # Sort by left position
            sorted_divs = []
            for div in row_divs:
                style = div.get("style", "")
                left_match = re.search(r"left:(\d+(?:\.\d+)?)px", style)
                if left_match:
                    left_pos = float(left_match.group(1))
                    sorted_divs.append((left_pos, div))

            sorted_divs.sort(key=lambda x: x[0])

            for left_pos, div in sorted_divs:
                div_text = div.get_text(strip=True)

                if div_text:
                    if div_text.isdigit() or self._contains_page_numbers(
                        div_text
                    ):
                        page_texts.append(div_text)
                    elif not self._contains_page_numbers(div_text):
                        text_parts.append(div_text)

                # Extract links
                for link in div.find_all("a"):
                    href = link.get("href")
                    if href and href.startswith("#"):
                        row_links.append(href.split("#")[-1])

            if row_links and text_parts:
                # Filter links
                if page_texts:
                    page_text = page_texts[0]
                    link_texts = [
                        link.get_text(strip=True)
                        for div in row_divs
                        for link in div.find_all("a")
                        if link.get_text(strip=True)
                    ]
                    row_links = self._filter_range_end_links(
                        page_text, row_links, link_texts
                    )

                # Create entry based on format
                if use_part_detection:  # 10-Q format
                    first_link = row_links[0] if row_links else None
                    if first_link:
                        # 统一返回结构，增加多段标识与链接计数
                        is_multi_section = self._is_multi_section_item(
                            text_parts, row_links
                        )
                        entry = {
                            "text": text_parts,
                            "link": first_link,
                            "is_multi_section": is_multi_section,
                            "link_count": len(row_links),
                        }
                        if part:
                            entry["part"] = part
                        if "signature" in "".join(text_parts).lower():
                            entry["part"] = "extracted"

                        table_links.append(entry)

                        if len(row_links) > 1:
                            logging.info(
                                f"Multi-section item detected in div (10Q): {text_parts[0] if text_parts else 'Unknown'} with {len(row_links)} links, using first link only"
                            )
                else:  # 10-K format
                    is_multi_section = self._is_multi_section_item(
                        text_parts, row_links
                    )
                    table_links.append(
                        {
                            "text": text_parts,
                            "links": row_links,
                            "is_multi_section": is_multi_section,
                            "link_count": len(row_links),
                        }
                    )

                    if is_multi_section and len(row_links) > 1:
                        logging.info(
                            f"Multi-section item detected in div: {text_parts[0] if text_parts else 'Unknown'} with {len(row_links)} links"
                        )

        return [table_links] if table_links else []

    def _priority_index_table(self, tables: List[Any]) -> List[Any]:
        """
        Prioritize tables based on their index in the document.
        """
        table_count_map = {}
        for table_index, table in enumerate(tables or []):
            table_item_count = 0
            for item in (table or []):
                texts = item.get("text")
                if isinstance(texts, list):
                    text_join = " ".join([t for t in texts if isinstance(t, str)])
                else:
                    text_join = str(texts or "")
                base = text_join.lower()
                if "item" in base:
                    table_item_count += 1
                elif ("signature" in base) or ("signatures" in base):
                    table_item_count += 1
            table_count_map[table_index] = table_item_count

        # 将 table_item_count <= 1 的 table 移到末尾；若所有 table 均 <= 1 则不处理
        if table_count_map and (max(table_count_map.values()) > 0):
            greater = [t for i, t in enumerate(tables) if table_count_map.get(i, 0) > 0]
            less_eq = [t for i, t in enumerate(tables) if table_count_map.get(i, 0) < 1]
            tables = greater + less_eq
        return tables



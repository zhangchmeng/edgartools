import re
import signal
import platform
from typing import List, Dict, Optional, Set, Any, Union
from bs4 import BeautifulSoup, Tag
import time
import logging
from edgar.files.html_documents import (
    HtmlDocument,
    TableBlock,
    clean_html_root,
    decompose_page_numbers,
    extract_and_format_content,
    Block,
    LinkBlock,
)
from edgar.files.htmltools import ChunkedDocument


# Define timeout exception class
class TimeoutException(Exception):
    """Timeout exception class, thrown when processing time exceeds the threshold"""

    pass


# Timeout handler function
def timeout_handler(signum, frame):
    """Triggered when timeout occurs, throws timeout exception"""
    raise TimeoutException("Processing timeout")


# Performance monitoring decorator
def monitor_performance(func):
    """Decorator to monitor function performance and log slow operations"""

    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time
            if elapsed > 2.0:  # Log operations taking more than 2 seconds
                logging.warning(f"{func.__name__} took {elapsed:.2f} seconds")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logging.error(
                f"{func.__name__} failed after {elapsed:.2f} seconds: {e}"
            )
            raise

    return wrapper


class AssembleText:
    @staticmethod
    def assemble_block_text(
        chunks: List[Block], prefix_src: Optional[str] = None
    ):
        if prefix_src:
            for block in chunks:
                if isinstance(block, LinkBlock):
                    yield block.to_markdown(prefix_src=prefix_src)
                else:
                    yield block.get_text()
        else:
            for block in chunks:
                yield block.get_text()

    @staticmethod
    def assemble_block_markdown(
        chunks: List[Block], prefix_src: Optional[str] = None
    ):
        if prefix_src:
            for block in chunks:
                if isinstance(block, LinkBlock):
                    yield block.to_markdown(prefix_src=prefix_src)
                elif isinstance(block, TableBlock):
                    yield block.get_text()
                else:
                    yield block.to_markdown()
        else:
            for block in chunks:
                if isinstance(block, TableBlock):
                    yield block.get_text()
                else:
                    yield block.to_markdown()

    @staticmethod
    def clean_and_assemble_text(
        start_element: Tag, markdown: bool = False
    ) -> str:
        # Now find the full text
        blocks: List[Block] = extract_and_format_content(start_element)
        # Compress the blocks
        blocks: List[Block] = HtmlDocument._compress_blocks(blocks)
        if markdown:
            text_parts = [
                text
                for text in AssembleText.assemble_block_markdown(blocks)
                if text is not None
            ]
            return "".join(text_parts)
        else:
            text_parts = [
                text
                for text in AssembleText.assemble_block_text(blocks)
                if text is not None
            ]
            return "".join(text_parts)

    @staticmethod
    def assemble_html_document(tags: List[Tag], markdown: bool = False) -> str:
        return ChunkedDocument.clean_part_line(
            "".join(
                [
                    AssembleText.clean_and_assemble_text(
                        tag, markdown=markdown
                    )
                    for tag in tags
                ]
            )
        )

    @staticmethod
    def find_block_level_parent(tag, all_link_tag: List[str]):
        """
        Optimized version: uses caching and more efficient search strategy
        """
        if not tag or not all_link_tag:
            return tag

        ori_tag = tag
        # Convert link_tag to set for improved search efficiency
        link_set = set(all_link_tag)

        # Cache checked parent elements to avoid repeated calculations
        checked_parents = {}

        while tag and tag.parent is not None:
            parent = tag.parent
            parent_id = id(parent)

            # Check cache
            if parent_id in checked_parents:
                link_count = checked_parents[parent_id]
            else:
                link_count = 0
                # Optimization: get all relevant elements at once to avoid multiple searches
                elements_with_id = parent.find_all(id=True)
                elements_with_name = parent.find_all("a", attrs={"name": True})

                # Check id attributes
                for elem in elements_with_id:
                    if elem.get("id") in link_set:
                        link_count += 1
                        if link_count > 1:
                            break

                # Check name attributes (only when needed)
                if link_count <= 1:
                    for elem in elements_with_name:
                        if elem.get("name") in link_set:
                            link_count += 1
                            if link_count > 1:
                                break

                # Cache results
                checked_parents[parent_id] = link_count

            if link_count > 1:
                return tag
            tag = parent

        return tag if tag else ori_tag

    @staticmethod
    @monitor_performance
    def assemble_items(
        html_content: str,
        item_links: List[Dict[str, Any]],
        markdown: bool = False,
    ) -> Dict[str, str]:
        """
        Optimized version: supports merging multiple sections for the same item.

        Key improvements:
        1. Handles items with multiple links (multiple sections/blocks)
        2. Merges content from all sections of the same item
        3. Maintains proper ordering and separation between sections
        """
        if not item_links:
            return {}

        # Flatten and collect all item IDs for processing
        all_item_ids = []
        item_name_to_ids = {}  # item_name -> list of item_ids

        for item in item_links:
            if isinstance(item, tuple) and len(item) >= 2:
                item_name, item_id_list = item[0], item[1]

                # Handle both single ID and list of IDs
                if isinstance(item_id_list, str):
                    item_id_list = [item_id_list]
                elif not isinstance(item_id_list, list):
                    continue

                item_name_to_ids[item_name] = item_id_list
                all_item_ids.extend(item_id_list)
            else:
                continue

        # Log information about items with multiple sections
        multi_section_items = {
            name: ids for name, ids in item_name_to_ids.items() if len(ids) > 1
        }
        if multi_section_items:
            logging.info(
                f"Items with multiple sections detected: {multi_section_items}"
            )

        # Remove duplicate detection that was preventing multi-section processing
        # Instead, we now support and expect multiple sections for the same item

        # Check if SIGALRM is supported (not supported on Windows)
        supports_alarm = platform.system() != "Windows"

        # If alarm is supported, set up timeout handling
        if supports_alarm:
            # Set timeout handler function
            signal.signal(signal.SIGALRM, timeout_handler)
            # Set 15-second timeout (increased timeout duration)
            signal.alarm(15)

        try:
            start_time = time.time()

            root: Tag = HtmlDocument.get_root(html_content)
            start_element = clean_html_root(root)
            decompose_page_numbers(start_element)
            soup = start_element
            # Use the flattened list of all item IDs
            link_ids = all_item_ids

            items = {}

            # Pre-build element lookup cache for all IDs
            id_cache = {}
            name_cache = {}
            for link_id in link_ids:
                id_elem = soup.find(id=link_id)
                name_elem = soup.find("a", attrs={"name": link_id})
                id_cache[link_id] = id_elem
                name_cache[link_id] = name_elem

            # Helper method to extract content up to a specific element
            def get_intro_content(first_item_id: str) -> List[Tag]:
                intro_content = []
                current = id_cache.get(first_item_id) or name_cache.get(
                    first_item_id
                )

                if current:
                    container = AssembleText.find_block_level_parent(
                        current, link_ids
                    )

                    if container:
                        siblings = list(container.previous_siblings)
                        intro_content = [
                            s for s in siblings if isinstance(s, Tag)
                        ]

                    sibling = current.previous_sibling
                    while sibling:
                        if isinstance(sibling, Tag):
                            intro_content.append(sibling)
                        sibling = sibling.previous_sibling
                    intro_content.reverse()
                return intro_content

            # Step 1: Extract intro (from start of document to first item)
            if item_links:
                first_item = item_links[0]
                first_item_ids = (
                    first_item[1]
                    if isinstance(first_item, tuple)
                    else first_item[1]
                )
                # Get the first ID from the list
                if isinstance(first_item_ids, list) and first_item_ids:
                    first_item_id = first_item_ids[0]
                elif isinstance(first_item_ids, str):
                    first_item_id = first_item_ids
                else:
                    first_item_id = None

                if first_item_id:
                    intro_content = get_intro_content(first_item_id)
                    items["Item 0"] = AssembleText.assemble_html_document(
                        intro_content, markdown=markdown
                    )
            # Step 2: Extract items (supporting multiple sections per item)
            for item_name, item_id_list in item_name_to_ids.items():
                # Check processing time to avoid timeout
                if (
                    time.time() - start_time > 12
                ):  # Stop processing after 12 seconds
                    logging.warning(
                        "Processing time limit reached, stopping item extraction"
                    )
                    break

                merged_content_parts = []

                # Process each section/block for this item
                for section_idx, item_id in enumerate(item_id_list):
                    # Use cached lookup results
                    target = id_cache.get(item_id) or name_cache.get(item_id)
                    if not target:
                        logging.warning(
                            f"link id not found: item_name:{item_name}, item_id:{item_id}"
                        )
                        continue

                    target = AssembleText.find_block_level_parent(
                        target, link_ids
                    )
                    if target:
                        content = []
                        current = target

                        # Find the next section boundary (next item ID in any item)
                        next_boundary_targets = []
                        for other_name, other_ids in item_name_to_ids.items():
                            for other_id in other_ids:
                                if (
                                    other_id != item_id
                                ):  # Don't include current item_id
                                    next_target = id_cache.get(
                                        other_id
                                    ) or name_cache.get(other_id)
                                    if next_target:
                                        next_boundary_targets.append(
                                            next_target
                                        )

                        while current:
                            # Check if we've reached any next item boundary
                            should_break = False
                            for next_target in next_boundary_targets:
                                if current == next_target or (
                                    next_target in current.descendants
                                    if hasattr(current, "descendants")
                                    else False
                                ):
                                    should_break = True
                                    break

                            if should_break:
                                break

                            if current.name is not None or (
                                current.string and current.string.strip()
                            ):
                                content.append(current)
                            current = current.next_sibling

                        # Convert content to text/markdown
                        section_content = AssembleText.assemble_html_document(
                            content, markdown=markdown
                        )

                        if (
                            section_content.strip()
                        ):  # Only add non-empty content
                            merged_content_parts.append(
                                section_content.strip()
                            )

                # Merge all sections for this item
                if merged_content_parts:
                    if len(merged_content_parts) > 1:
                        # Add section separators for multiple sections
                        separator = "\n\n---\n\n" if markdown else "\n\n"
                        items[item_name] = separator.join(merged_content_parts)
                        logging.info(
                            f"Merged {len(merged_content_parts)} sections for {item_name}"
                        )
                    else:
                        items[item_name] = merged_content_parts[0]
            # Step 3: Handle Signatures
            if "Signature" not in items and item_links:
                last_item = item_links[-1]
                last_item_name = (
                    last_item[0]
                    if isinstance(last_item, tuple)
                    else last_item[0]
                )
                last_content = items.get(last_item_name, "")

                if last_content:
                    sig_key = ["SIGNATURES", "SIGNATURE"]
                    content_lines = last_content.split("\n")
                    signature_line_index = None

                    for i, line in enumerate(content_lines):
                        if line.strip().upper() in sig_key:
                            signature_line_index = i
                            break

                    if signature_line_index is not None:
                        before_sig = "\n".join(
                            content_lines[:signature_line_index]
                        )
                        sig_start_pos = len(before_sig) + 1
                        items["Signature"] = last_content[
                            sig_start_pos:
                        ].strip()
                        items[last_item_name] = before_sig.strip()
                    else:
                        items["Signature"] = ""
            return items

        except TimeoutException:
            # Timeout handling, return empty dictionary
            logging.error(
                "HTML processing timeout (exceeded 15 seconds), returning empty result"
            )
            return {}
        except ValueError as e:
            # Handle duplicate item detection error
            if "Item duplication detected" in str(e):
                logging.error(
                    f"Duplicate items detected, returning empty result: {e}"
                )
                return {}
            else:
                logging.error(f"ValueError in assemble_items: {e}")
                return {}
        except Exception as e:
            logging.error(f"Error in assemble_items: {e}")
            return {}
        finally:
            # If alarm is supported, cancel the alarm
            if supports_alarm:
                signal.alarm(0)


class BaseHtmlParser:
    """基础HTML解析器类，包含ParsedHtml10K和ParsedHtml10Q的共同方法"""

    # extract_element_id 方法已在基类中定义

    def _contains_page_numbers(self, text: str) -> bool:
        """
        统一的页码检测方法，支持多种页码格式。

        Examples:
        - "7-24, 82-87, 90-101" -> True
        - "156-157" -> True
        - "155" -> True
        - "Not Applicable" -> False
        - "(a), 158" -> True
        """
        if not text:
            return False

        text = text.strip()

        # Handle "Not Applicable" cases
        if "not applicable" in text.lower():
            return False

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
        检查文本是否包含多个页码范围，表示分割的部分。

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
        统一判断是否为多部分项目的方法。

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
        统一的范围结束链接过滤方法。
        过滤掉页码范围中的结束链接，如"7-24, 82-87, 90-101"中只保留起始链接(7, 82, 90)。

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

    def _parse_html_content(self, html_content: str) -> BeautifulSoup:
        """
        统一的HTML内容解析方法。

        Args:
            html_content: HTML content to parse

        Returns:
            BeautifulSoup object or None if parsing fails
        """
        if not html_content:
            return None

        html_content = html_content.replace("&nbsp;", " ")

        try:
            soup = BeautifulSoup(html_content, "html.parser")
        except Exception as e:
            logging.error(f"Failed to parse HTML: {e}")
            return None

        # Remove script and style tags
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        return soup

    def _extract_table_links_base(
        self, soup: BeautifulSoup, use_part_detection: bool = False
    ) -> List[List[Dict[str, Any]]]:
        """
        Base method to extract links from HTML tables.

        Args:
            soup: BeautifulSoup object
            use_part_detection: Whether to detect part information (for 10-Q)

        Returns:
            List of tables with link information
        """
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

            for row in rows:
                if use_part_detection and part_regex:
                    row_text = row.get_text().strip()
                    part_match = part_regex.match(row_text)
                    if part_match:
                        part = re.sub(r"\s+", " ", part_match.group(1).lower())

                cells = row.find_all("td", recursive=False)
                exist_page_num = False

                if cells:
                    has_links = any(cell.find("a") for cell in cells)
                    if not has_links:
                        continue

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
                        if use_part_detection and len(cell_text) > 10:
                            continue

                        if cell_text.isdigit() or self._contains_page_numbers(
                            cell_text
                        ):
                            exist_page_num = True
                            break

                    if exist_page_num:
                        row_links = self._extract_row_links(
                            cells, text, part, use_part_detection
                        )
                        if row_links:
                            table_links.extend(row_links)

            if table_links:
                link_info.append(table_links)

            # Limit results for 10-Q
            if use_part_detection and len(link_info) > 20:
                logging.warning(
                    "Found too many tables with links, limiting results"
                )
                break

        return link_info

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
                    break
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

    def _extract_div_links_base(
        self, html_content: str, use_part_detection: bool = False
    ) -> List[List[Dict[str, Any]]]:
        """Extract table of contents from div structure (common logic for both 10-K and 10-Q)"""
        if not html_content:
            return []

        html_content = html_content.replace("&nbsp;", " ")
        soup = self._parse_html_content(html_content)

        # Find div containing "TABLE OF CONTENTS"
        toc_div = None
        for div in soup.find_all("div"):
            if div.get_text(strip=True) == "TABLE OF CONTENTS":
                toc_div = div.find_parent("div")
                break

        if not toc_div:
            logging.warning("TABLE OF CONTENTS div not found")
            return []

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
            has_page_num = any(
                div.get_text(strip=True).isdigit()
                or self._contains_page_numbers(div.get_text(strip=True))
                for div in row_divs
            )

            if not has_page_num:
                continue

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
                        entry = {"text": text_parts, "link": first_link}
                        if part:
                            entry["part"] = part
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


class ParsedHtml10K(BaseHtmlParser):
    @staticmethod
    def extract_element_id(href: str) -> str:
        """
        Extract element ID from an XLink href.
        Args:
            href: XLink href attribute value
        Returns:
            Element ID
        """
        return href.split("#")[-1]

    @monitor_performance
    def extract_html_link_info(
        self, html_content: str
    ) -> List[List[Dict[str, Any]]]:
        """
        Enhanced version: find table rows containing links and page numbers.
        Supports multiple links per row to handle cases where items are split across multiple sections.

        Returns:
            List of tables, each containing list of row data with text and links.
            Each row can now contain multiple links for items split across sections.
        """
        soup = self._parse_html_content(html_content)
        if not soup:
            return []

        # Use base method for table extraction (10-K style)
        link_info = self._extract_table_links_base(
            soup, use_part_detection=False
        )

        # If no table is found or table is empty, try parsing from div containing TABLE OF CONTENTS
        if not link_info:
            logging.info(
                "No table-based content found, attempting to parse from TABLE OF CONTENTS div"
            )
            div_link_info = self.extract_html_link_from_div(html_content)
            if div_link_info:
                link_info.extend(div_link_info)
                logging.info(
                    f"Successfully extracted {len(div_link_info)} tables from div structure"
                )

        return link_info

    def extract_html_link_from_div(
        self, html_content: str
    ) -> List[List[Dict[str, Any]]]:
        """
        Extract table of contents information from top-level div containing TABLE OF CONTENTS and parse it.
        When traditional table structure doesn't exist, parse table of contents structure based on absolutely positioned divs.

        Returns:
            List of tables, each containing list of row data with text and links.
            Format consistent with extract_html_link_info.
        """
        return self._extract_div_links_base(
            html_content, use_part_detection=False
        )

    # _contains_page_numbers 方法已在基类中定义

    # _is_multi_section_item 方法已在基类中定义

    # _contains_multiple_page_ranges 方法已在基类中定义

    # _filter_range_end_links 方法已在基类中定义

    @staticmethod
    @monitor_performance
    def extract_item_and_split(link_info: List[List[Dict[str, Any]]]):
        """
        Optimized version: Handles same item appearing in multiple sections/blocks.

        Defines matching patterns and functions for extracting and splitting SEC filing items.
        The code provides:
        1. Multiple dictionaries containing different formats of SEC item identifiers
        2. A match_function_map tuple that pairs each dictionary with its corresponding matching function
        3. Matching functions that handle case-insensitive comparisons (startswith, equals, contains)

        Key improvement: Instead of keeping only the first match for each item, this version
        collects ALL matching links for each item to support merging multiple sections.
        """
        if not link_info:
            return []
        link_info = [item for sublist in link_info for item in sublist]
        items_match_1 = {  # Match items starting with these patterns
            "Item 1": "Item 1.",
            "Item 1A": "Item 1A.",
            "Item 1B": "Item 1B.",
            "Item 1C": "Item 1C.",
            "Item 2": "Item 2.",
            "Item 3": "Item 3.",
            "Item 4": "Item 4.",
            "Item 5": "Item 5.",
            "Item 6": "Item 6.",
            "Item 7": "Item 7.",
            "Item 7A": "Item 7A.",
            "Item 8": "Item 8.",
            "Item 9": "Item 9.",
            "Item 9A": "Item 9A.",
            "Item 9B": "Item 9B.",
            "Item 9C": "Item 9C.",
            "Item 10": "Item 10.",
            "Item 11": "Item 11.",
            "Item 12": "Item 12.",
            "Item 13": "Item 13.",
            "Item 14": "Item 14.",
            "Item 15": "Item 15.",
            "Item 16": "Item 16.",
            "Signatures": "Signature",
        }
        items_match_0 = {key: key for key in items_match_1}
        items_match_2 = {  # Exact match after stripping whitespace
            "Item 1": "Part I, Item 1",
            "Item 1A": "Part I, Item 1A",
            "Item 1B": "Part I, Item 1B",
            "Item 1C": "Part I, Item 1C",
            "Item 2": "Part I, Item 2",
            "Item 3": "Part I, Item 3",
            "Item 4": "Part I, Item 4",
            "Item 5": "Part II, Item 5",
            "Item 6": "Part II, Item 6",
            "Item 7": "Part II, Item 7",
            "Item 7A": "Part II, Item 7A",
            "Item 8": "Part II, Item 8",
            "Item 9": "Part II, Item 9",
            "Item 9A": "Part II, Item 9A",
            "Item 9B": "Part II, Item 9B",
            "Item 9C": "Part II, Item 9C",
            "Item 10": "Part III, Item 10",
            "Item 11": "Part III, Item 11",
            "Item 12": "Part III, Item 12",
            "Item 13": "Part III, Item 13",
            "Item 14": "Part III, Item 14",
            "Item 15": "Part IV, Item 15",
            "Item 16": "Part IV, Item 16",
            "Signatures": "Signature",
        }
        items_match_2_1 = {
            "Item 1": "Item No. 1",
            "Item 1A": "Item No. 1A",
            "Item 1B": "Item No. 1B",
            "Item 1C": "Item No. 1C",
            "Item 2": "Item No. 2",
            "Item 3": "Item No. 3",
            "Item 4": "Item No. 4",
            "Item 5": "Item No. 5",
            "Item 6": "Item No. 6",
            "Item 7": "Item No. 7",
            "Item 7A": "Item No. 7A",
            "Item 8": "Item No. 8",
            "Item 9": "Item No. 9",
            "Item 9A": "Item No. 9A",
            "Item 9B": "Item No. 9B",
            "Item 9C": "Item No. 9C",
            "Item 10": "Item No. 10",
            "Item 11": "Item No. 11",
            "Item 12": "Item No. 12",
            "Item 13": "Item No. 13",
            "Item 14": "Item No. 14",
            "Item 15": "Item No. 15",
            "Item 16": "Item No. 16",
        }
        items_match_2_2 = {  # Exact match after stripping whitespace
            "Item 1": "Part I. Item 1",
            "Item 1A": "Part I. Item 1A",
            "Item 1B": "Part I. Item 1B",
            "Item 1C": "Part I. Item 1C",
            "Item 2": "Part I. Item 2",
            "Item 3": "Part I. Item 3",
            "Item 4": "Part I. Item 4",
            "Item 5": "Part II. Item 5",
            "Item 6": "Part II. Item 6",
            "Item 7": "Part II. Item 7",
            "Item 7A": "Part II. Item 7A",
            "Item 8": "Part II. Item 8",
            "Item 9": "Part II. Item 9",
            "Item 9A": "Part II. Item 9A",
            "Item 9B": "Part II. Item 9B",
            "Item 9C": "Part II. Item 9C",
            "Item 10": "Part III. Item 10",
            "Item 11": "Part III. Item 11",
            "Item 12": "Part III. Item 12",
            "Item 13": "Part III. Item 13",
            "Item 14": "Part III. Item 14",
            "Item 15": "Part IV. Item 15",
            "Item 16": "Part IV. Item 16",
            "Signatures": "Signature",
        }
        items_match_2_3 = {  # Exact match after stripping whitespace
            "Item 1": "Part I. Item 1.",
            "Item 1A": "Part I. Item 1A.",
            "Item 1B": "Part I. Item 1B.",
            "Item 1C": "Part I. Item 1C.",
            "Item 2": "Part I. Item 2.",
            "Item 3": "Part I. Item 3.",
            "Item 4": "Part I. Item 4.",
            "Item 5": "Part II. Item 5.",
            "Item 6": "Part II. Item 6.",
            "Item 7": "Part II. Item 7.",
            "Item 7A": "Part II. Item 7A.",
            "Item 8": "Part II. Item 8.",
            "Item 9": "Part II. Item 9.",
            "Item 9A": "Part II. Item 9A.",
            "Item 9B": "Part II. Item 9B.",
            "Item 9C": "Part II. Item 9C.",
            "Item 10": "Part III. Item 10.",
            "Item 11": "Part III. Item 11.",
            "Item 12": "Part III. Item 12.",
            "Item 13": "Part III. Item 13.",
            "Item 14": "Part III. Item 14.",
            "Item 15": "Part IV. Item 15.",
            "Item 16": "Part IV. Item 16.",
            "Signatures": "Signature",
        }

        items_match_3 = {  # Match item names (startswith comparison)
            "Item 1": "Business",
            "Item 1A": "Risk Factors",
            "Item 1B": "Unresolved Staff Comments",
            "Item 1C": "Cybersecurity",
            "Item 2": "Properties",
            "Item 3": "Legal Proceedings",
            "Item 4": "Mine Safety Disclosures",
            "Item 5": "Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities",
            "Item 6": "[Reserved]",
            "Item 7": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
            "Item 7A": "Quantitative and Qualitative Disclosures About Market Risk",
            "Item 8": "Financial Statements and Supplementary Data",
            "Item 9": "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure",
            "Item 9A": "Controls and Procedures",
            "Item 9B": "Other Information",
            "Item 9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
            "Item 10": "Directors, Executive Officers and Corporate Governance",
            "Item 11": "Executive Compensation",
            "Item 12": "Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters",
            "Item 13": "Certain Relationships and Related Transactions, and Director Independence",
            "Item 14": "Principal Accountant Fees and Services",
            "Item 15": "Exhibit and Financial Statement Schedules",
            "Item 16": "Form 10-K Summary",
        }

        items_match_4 = {  # Match combined items (startswith comparison)
            "Item 1": "Items 1 and 2.",
            "Item 2": "Items 1 and 2.",
        }

        items_match_5 = {
            "Item 1": "1. Business",
            "Item 1A": "1A. Risk Factors",
            "Item 1B": "1B. Unresolved Staff Comments",
            "Item 1C": "1C. Cybersecurity",
            "Item 2": "2. Properties",
            "Item 3": "3. Legal Proceedings",
            "Item 4": "4. Mine Safety Disclosures",
            "Item 5": "5. Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities",
            "Item 6": "6. [Reserved]",
            "Item 7": "7. Management's Discussion and Analysis of Financial Condition and Results of Operations",
            "Item 7A": "7A. Quantitative and Qualitative Disclosures about Market Risk",
            "Item 8": "8. Financial Statements and Supplementary Data",
            "Item 9": "9. Changes in and Disagreements with Accountants on Accounting and Financial Disclosure",
            "Item 9A": "9A. Controls and Procedures",
            "Item 9B": "9B. Other Information",
            "Item 9C": "9C. Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
            "Item 10": "10. Directors, Executive Officers and Corporate Governance",
            "Item 11": "11. Executive Compensation",
            "Item 12": "12. Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters",
            "Item 13": "13. Certain Relationships and Related Transactions, and Director Independence",
            "Item 14": "14. Principal Accountant Fees and Services",
            "Item 15": "15. Exhibit and Financial Statement Schedules",
            "Item 16": "16. Form 10-K Summary",
        }

        items_match_6 = {
            "Item 1": "1 and 2. Business and Properties",
            "Item 2": "1 and 2. Business and Properties",
        }

        # Matching function types:
        # 1. equal
        # 2. startswith
        # 3. contains
        # 4. regex
        match_function_map = [  # The current page has an order
            (
                items_match_4,
                lambda x, y: x.strip().lower().startswith(y.lower()),
            ),
            (
                items_match_6,
                lambda x, y: x.strip().lower().startswith(y.lower()),
            ),
            (items_match_0, lambda x, y: x.strip().lower() == y.lower()),
            (
                items_match_1,
                lambda x, y: x.strip().lower().startswith(y.lower()),
            ),
            (items_match_2, lambda x, y: x.strip().lower() == y.lower()),
            (items_match_2_1, lambda x, y: x.strip().lower() == y.lower()),
            (items_match_2_2, lambda x, y: x.strip().lower() == y.lower()),
            (items_match_2_3, lambda x, y: x.strip().lower() == y.lower()),
            (items_match_3, lambda x, y: y.lower() in x.lower()),
            (items_match_5, lambda x, y: y.lower() in x.lower()),
        ]

        # Process matches and collect ALL links for each item (support multiple sections)
        item_links_dict = {}  # item_name -> list of links
        multi_section_items = set()  # Track items with multiple sections

        # Record items that have been processed to avoid duplicate matching
        processed_items = set()

        for match_map, match_function in match_function_map:
            for item_name, match_text in match_map.items():
                # If item has been processed, skip subsequent matching
                if item_name in processed_items:
                    continue

                for one_table_link in link_info:
                    for cell in one_table_link["text"]:
                        if match_function(cell, match_text):
                            # Handle both old format (single "link") and new format (multiple "links")
                            if "links" in one_table_link:
                                # New format: multiple links per row
                                links_to_add = one_table_link["links"]
                                is_multi_section = one_table_link.get(
                                    "is_multi_section", False
                                )

                                # Log detection of multi-section items
                                if is_multi_section and len(links_to_add) > 1:
                                    logging.info(
                                        f"Processing multi-section item: {item_name} with {len(links_to_add)} sections"
                                    )
                            else:
                                # Backward compatibility: single link format
                                links_to_add = [one_table_link["link"]]
                                is_multi_section = False

                            # Collect ALL matching links for each item
                            if item_name not in item_links_dict:
                                item_links_dict[item_name] = []

                            # Add all links, avoiding duplicates
                            for link in links_to_add:
                                if link not in item_links_dict[item_name]:
                                    item_links_dict[item_name].append(link)

                            # Track multi-section items
                            if is_multi_section:
                                multi_section_items.add(item_name)

                            # Add processed item to processed_items set
                            processed_items.add(item_name)

                            break  # Break after first match in this cell
        # Convert to list format: [(item_name, [link1, link2, ...]), ...]
        item_links = [(name, links) for name, links in item_links_dict.items()]

        # Log summary of processing results
        multi_section_count = sum(
            1 for name, links in item_links if len(links) > 1
        )
        single_section_count = len(item_links) - multi_section_count

        logging.info(
            f"Item processing summary: {single_section_count} single-section items, {multi_section_count} multi-section items"
        )

        return item_links

    def extract_html(
        self, html_content: str, structure, markdown: bool = False
    ) -> Dict[str, Any]:
        """
        Find rows in tables that:
            1. Contain links
            2. Have a separate cell storing page numbers
        """
        index_table = self.extract_html_link_info(html_content)
        item_links = self.extract_item_and_split(index_table)
        item_result = AssembleText.assemble_items(
            html_content, item_links, markdown=markdown
        )

        item_to_part = {}
        for part_name in structure.structure:
            part_items = structure.get_part(part_name)
            for item_name in part_items:
                item_to_part[item_name.lower()] = part_name.lower()

        # Step 4: Group items by part
        result = {part_name.lower(): {} for part_name in structure.structure}
        result["extracted"] = {}

        for item_name, content in item_result.items():
            item_name = item_name.lower()
            part_name = item_to_part.get(item_name)
            if part_name:
                result[part_name][item_name] = content
            else:
                result["extracted"][item_name] = content
        return result


class ParsedHtml10Q(BaseHtmlParser):
    """Parser for 10-Q HTML documents that handles same item numbers in different parts."""

    # extract_element_id 方法已在基类中定义

    @monitor_performance
    def extract_html_link_info(self, html_content: str) -> List[Any]:
        """Optimized version: find table rows containing links and page numbers"""
        soup = self._parse_html_content(html_content)
        if not soup:
            return []

        # Use base method for table extraction (10-Q style with part detection)
        link_info = self._extract_table_links_base(
            soup, use_part_detection=True
        )

        # If no table is found or table is empty, try parsing from div containing TABLE OF CONTENTS
        if not link_info:
            logging.info(
                "No table-based content found, attempting to parse from TABLE OF CONTENTS div"
            )
            div_link_info = self.extract_html_link_from_div(html_content)
            if div_link_info:
                link_info.extend(div_link_info)
                logging.info(
                    f"Successfully extracted {len(div_link_info)} tables from div structure"
                )

        return link_info

    def extract_html_link_from_div(
        self, html_content: str
    ) -> List[List[Dict[str, Any]]]:
        """
        Extract table of contents information from top-level div containing TABLE OF CONTENTS.
        When traditional table structure doesn't exist, parse table of contents structure based on absolutely positioned divs.

        Returns:
            List of tables, each containing list of row data with text and links.
            Format consistent with extract_html_link_info.
        """
        return self._extract_div_links_base(
            html_content, use_part_detection=True
        )

    # _contains_page_numbers 方法已在基类中定义

    # _is_multi_section_item 方法已在基类中定义

    # _contains_multiple_page_ranges 方法已在基类中定义

    # _filter_range_end_links 方法已在基类中定义

    @staticmethod
    def extract_item_and_split(link_info: List[List[Dict[str, Any]]]):
        """Extract and match 10-Q specific items, handling same item numbers in different parts."""
        if not link_info:
            return []

        link_info = [item for sublist in link_info for item in sublist]

        # 10-Q specific item patterns
        items_match_1 = {  # Standard 10-Q item formats
            "part i": {
                "Item 1": "Item 1.",
                "Item 2": "Item 2.",
                "Item 3": "Item 3.",
                "Item 4": "Item 4.",
            },
            "part ii": {
                "Item 1": "Item 1.",
                "Item 1A": "Item 1A.",
                "Item 2": "Item 2.",
                "Item 3": "Item 3.",
                "Item 4": "Item 4.",
                "Item 5": "Item 5.",
                "Item 6": "Item 6.",
            },
            "Extarect": {"Signatures": "Signature"},
        }

        items_match_2 = {  # Part-prefixed items
            "part i": {
                "Item 1": "part i, Item 1",
                "Item 2": "part i, Item 2",
                "Item 3": "part i, Item 3",
                "Item 4": "part i, Item 4",
            },
            "part ii": {
                "Item 1": "part ii, Item 1",
                "Item 1A": "part ii, Item 1A",
                "Item 2": "part ii, Item 2",
                "Item 3": "part ii, Item 3",
                "Item 4": "part ii, Item 4",
                "Item 5": "part ii, Item 5",
                "Item 6": "part ii, Item 6",
            },
        }

        items_match_2_1 = {  # Part-prefixed items
            "part i": {
                "Item 1": "part i. Item 1",
                "Item 2": "part i. Item 2",
                "Item 3": "part i. Item 3",
                "Item 4": "part i. Item 4",
            },
            "part ii": {
                "Item 1": "part ii. Item 1",
                "Item 1A": "part ii. Item 1A",
                "Item 2": "part ii. Item 2",
                "Item 3": "part ii. Item 3",
                "Item 4": "part ii. Item 4",
                "Item 5": "part ii. Item 5",
                "Item 6": "part ii. Item 6",
            },
        }

        items_match_3 = {  # Item descriptions
            "part i": {
                "Item 1": "Financial Statements",
                "Item 2": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
                "Item 3": "Quantitative and Qualitative Disclosures About Market Risk",
                "Item 4": "Controls and Procedures",
            },
            "part ii": {
                "Item 1": "Legal Proceedings",
                "Item 1A": "Risk Factors",
                "Item 2": "Unregistered Sales of Equity Securities and Use of Proceeds",
                "Item 3": "Defaults Upon Senior Securities",
                "Item 4": "Mine Safety Disclosures",
                "Item 5": "Other Information",
                "Item 6": "Exhibits",
            },
        }

        match_function_map = [
            (
                items_match_1,
                lambda x, y: x.strip().lower().startswith(y.lower()),
            ),
            (items_match_2, lambda x, y: x.strip().lower() == y.lower()),
            (items_match_2_1, lambda x, y: x.strip().lower() == y.lower()),
            (items_match_3, lambda x, y: y.lower() in x.lower()),
            # (items_match_4, lambda x, y: x.strip().lower().startswith(y.lower())),
        ]

        # Process matches and ensure unique items
        item_dict = {}

        for one_link in link_info:
            for match_map, match_function in match_function_map:
                for part in match_map:
                    for item_name, match_text in match_map[part].items():
                        for cell in one_link["text"]:
                            if match_function(cell, match_text):
                                link = one_link["link"]
                                if (
                                    one_link["part"] == part
                                    and item_name not in item_dict
                                ):
                                    item_dict[(part, item_name)] = link

        # Convert to list format without sorting
        item_links = [(name, link) for name, link in item_dict.items()]
        if len(item_links) > 11:
            return {}
        return item_links

    def extract_html(
        self, html_content: str, structure, markdown: bool = True
    ) -> Dict[str, Any]:
        """Extract 10-Q items from HTML content, handling same item numbers in different parts."""
        index_table = self.extract_html_link_info(html_content)
        item_links = self.extract_item_and_split(index_table)
        if isinstance(item_links, dict):
            item_links = list(item_links.items())
        elif not isinstance(item_links, list):
            item_links = []

        item_result = AssembleText.assemble_items(
            html_content, item_links, markdown=markdown
        )

        # Group items by part
        result = {"part i": {}, "part ii": {}, "extracted": {}}

        for item_name, content in item_result.items():
            if isinstance(item_name, tuple):
                part_name, item_name = item_name
                result[part_name][item_name.lower()] = content
            else:
                result["extracted"][item_name.lower()] = content

        return result


def check_item_result(result):
    # Count all items
    total_items = 0
    empty_items = 0
    for part_name, part_content in result.items():
        if part_name == "extracted":
            continue
        print(f"\nChecking content of {part_name}:")
        if isinstance(part_content, dict):
            part_items = len(part_content)
            total_items += part_items
            print(f"{part_name} contains {part_items} items")

            for item_name, item_content in part_content.items():
                content_length = len(item_content) if item_content else 0
                if content_length == 0:
                    empty_items += 1
                    print(f"Warning: {item_name} in {part_name} is empty")
                else:
                    print(f"{item_name}: {content_length} characters")
        else:
            print(f"Warning: {part_name} is not in dictionary format")
    print(f"\nSummary:")
    print(f"Total {total_items} items found")
    if empty_items > 0:
        print(f"Among them, {empty_items} items are empty")


def test():
    """Check and confirm that normal type files can be parsed successfully"""
    from edgar import set_identity, get_by_accession_number
    from edgar.company_reports import TenK

    set_identity("1334307071@qq.com")

    # Test parsing Apple's 10-K filing
    accession_number = "0000320193-24-000123"
    filing = get_by_accession_number(accession_number)
    result = ParsedHtml10K().extract_html(
        filing.html(), TenK.structure, markdown=True
    )

    # Verify Part I content length
    assert 15660 < len(result["part i"]["item 1"]) < 15760
    assert 68734 < len(result["part i"]["item 1a"]) < 68834
    assert 2688 < len(result["part i"]["item 1c"]) < 2788
    assert 436 < len(result["part i"]["item 2"]) < 536
    assert 4291 < len(result["part i"]["item 3"]) < 4391

    # Verify Part II content length
    assert 4779 < len(result["part ii"]["item 5"]) < 4879
    assert 18274 < len(result["part ii"]["item 7"]) < 18374
    assert 3217 < len(result["part ii"]["item 7a"]) < 3317
    assert 102697 < len(result["part ii"]["item 8"]) < 102797
    assert 4450 < len(result["part ii"]["item 9a"]) < 4550
    assert 1294 < len(result["part ii"]["item 9b"]) < 1394

    # Verify Part III content length
    assert 983 < len(result["part iii"]["item 10"]) < 1083
    assert 181 < len(result["part iii"]["item 12"]) < 281
    assert 160 < len(result["part iii"]["item 13"]) < 260

    # Verify Part IV content length
    assert 30483 < len(result["part iv"]["item 15"]) < 30583

    check_item_result(result)

    # Test multi-section merging case
    accession_number = "0001601712-25-000044"
    filing = get_by_accession_number(accession_number)
    result = ParsedHtml10K().extract_html(
        filing.html(), TenK.structure, markdown=True
    )

    # Verify merged content length
    assert 144591 < len(result["part i"]["item 1"]) < 144691
    assert 135437 < len(result["part i"]["item 1a"]) < 135537
    assert 6887 < len(result["part i"]["item 1c"]) < 6987
    assert 1621 < len(result["part i"]["item 2"]) < 1721
    assert 3116 < len(result["part i"]["item 3"]) < 3216

    assert 5911 < len(result["part ii"]["item 5"]) < 6011
    assert 130239 < len(result["part ii"]["item 7"]) < 130339
    assert 8240 < len(result["part ii"]["item 7a"]) < 8340
    assert 223708 < len(result["part ii"]["item 8"]) < 223808
    assert 4021 < len(result["part ii"]["item 9a"]) < 4121
    assert 3693 < len(result["part ii"]["item 9b"]) < 3793

    assert 3693 < len(result["part iii"]["item 10"]) < 3793
    assert 76668 < len(result["part iv"]["item 15"]) < 76768

    check_item_result(result)
    accession_number = "0000726601-25-000013"
    # 0000831001-25-000131 has no table in content, parse TABLE OF CONTENTS div to determine if it's a table of contents
    # TODO


def test_10_q_processing():
    """Check and confirm that normal type files can be parsed successfully"""
    from edgar import set_identity, get_by_accession_number
    from edgar.company_reports import TenQ

    set_identity("1334307071@qq.com")


if __name__ == "__main__":
    # from edgar import set_identity, get_by_accession_number
    # from edgar.company_reports import TenQ, TenK

    # set_identity("1334307071@qq.com")
    # file_id = 691553
    # accession_number = "0000070858-24-000156"
    # # accession_number = "0000726601-25-000013"
    # filing = get_by_accession_number(accession_number)
    # # print(
    # #     ParsedHtml10K().extract_html(filing.html(), TenK.structure, markdown=True)
    # # )
    # print(
    #     ParsedHtml10Q().extract_html(
    #         filing.html(), TenQ.structure, markdown=True
    #     )
    # )
    test()

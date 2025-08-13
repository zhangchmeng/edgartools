import re
import signal
import platform
from typing import List, Dict, Optional, Set, Any, Union
from bs4 import BeautifulSoup, Tag, NavigableString
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
            logging.error(f"{func.__name__} failed after {elapsed:.2f} seconds: {e}")
            raise
    return wrapper
class AssembleText:
    @staticmethod
    def assemble_block_text(chunks: List[Block], prefix_src: str = None):
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
    def assemble_block_markdown(chunks: List[Block], prefix_src: str = None):
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
            return "".join(
                [text for text in AssembleText.assemble_block_markdown(blocks)]
            )
        else:
            return "".join(
                [text for text in AssembleText.assemble_block_text(blocks)]
            )
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
                    if elem.get('id') in link_set:
                        link_count += 1
                        if link_count > 1:
                            break
                
                # Check name attributes (only when needed)
                if link_count <= 1:
                    for elem in elements_with_name:
                        if elem.get('name') in link_set:
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
        html_content: str, item_links: List, markdown: bool = False
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
        multi_section_items = {name: ids for name, ids in item_name_to_ids.items() if len(ids) > 1}
        if multi_section_items:
            logging.info(f"Items with multiple sections detected: {multi_section_items}")
        
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
                current = id_cache.get(first_item_id) or name_cache.get(first_item_id)
                
                if current:
                    container = AssembleText.find_block_level_parent(current, link_ids)
                    
                    if container:
                        siblings = list(container.previous_siblings)
                        intro_content = [s for s in siblings if isinstance(s, Tag)]
                        
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
                first_item_ids = first_item[1] if isinstance(first_item, tuple) else first_item[1]
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
                if time.time() - start_time > 12:  # Stop processing after 12 seconds
                    logging.warning("Processing time limit reached, stopping item extraction")
                    break
                
                merged_content_parts = []
                
                # Process each section/block for this item
                for section_idx, item_id in enumerate(item_id_list):
                    # Use cached lookup results
                    target = id_cache.get(item_id) or name_cache.get(item_id)
                    if not target:
                        logging.warning(f"link id not found: item_name:{item_name}, item_id:{item_id}")
                        continue
                        
                    target = AssembleText.find_block_level_parent(target, link_ids)
                    if target:
                        content = []
                        current = target
                        
                        # Find the next section boundary (next item ID in any item)
                        next_boundary_targets = []
                        for other_name, other_ids in item_name_to_ids.items():
                            for other_id in other_ids:
                                if other_id != item_id:  # Don't include current item_id
                                    next_target = id_cache.get(other_id) or name_cache.get(other_id)
                                    if next_target:
                                        next_boundary_targets.append(next_target)
                        
                        while current:
                            # Check if we've reached any next item boundary
                            should_break = False
                            for next_target in next_boundary_targets:
                                if current == next_target or (next_target in current.descendants if hasattr(current, 'descendants') else False):
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
                        
                        if section_content.strip():  # Only add non-empty content
                            merged_content_parts.append(section_content.strip())
                
                # Merge all sections for this item
                if merged_content_parts:
                    if len(merged_content_parts) > 1:
                        # Add section separators for multiple sections
                        separator = "\n\n---\n\n" if markdown else "\n\n"
                        items[item_name] = separator.join(merged_content_parts)
                        logging.info(f"Merged {len(merged_content_parts)} sections for {item_name}")
                    else:
                        items[item_name] = merged_content_parts[0]
            # Step 3: Handle Signatures
            if "Signature" not in items and item_links:
                last_item = item_links[-1]
                last_item_name = last_item[0] if isinstance(last_item, tuple) else last_item[0]
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
                        before_sig = "\n".join(content_lines[:signature_line_index])
                        sig_start_pos = len(before_sig) + 1
                        items["Signature"] = last_content[sig_start_pos:].strip()
                        items[last_item_name] = before_sig.strip()
                    else:
                        items["Signature"] = ""
            return items
            
        except TimeoutException:
            # Timeout handling, return empty dictionary
            logging.error("HTML processing timeout (exceeded 15 seconds), returning empty result")
            return {}
        except ValueError as e:
            # Handle duplicate item detection error
            if "Item duplication detected" in str(e):
                logging.error(f"Duplicate items detected, returning empty result: {e}")
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
class ParsedHtml10K:
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
    def extract_html_link_info(self, html_content: str) -> List[List[Dict[str, Any]]]:
        """
        Enhanced version: find table rows containing links and page numbers.
        Supports multiple links per row to handle cases where items are split across multiple sections.
        
        Returns:
            List of tables, each containing list of row data with text and links.
            Each row can now contain multiple links for items split across sections.
        """
        if not html_content:
            return []
        html_content = html_content.replace("&nbsp;", " ")
        
        try:
            soup = BeautifulSoup(html_content, "html.parser")
        except Exception as e:
            logging.error(f"Failed to parse HTML: {e}")
            return []
        # Remove script and style tags
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        link_info: List[List[Dict[str, Any]]] = []
        tables = soup.find_all("table")
        
        for table_idx, table in enumerate(tables):
            table_links: List[Dict[str, Any]] = []
            rows = table.find_all("tr")
            
            for row in rows:
                cells = row.find_all("td", recursive=False)
                exist_page_num = False
                
                if cells:
                    has_links = any(cell.find("a") for cell in cells)
                    if not has_links:
                        continue
                    
                    text = []
                    for cell in cells:
                        cell_text = cell.get_text(separator="", strip=True)
                        text.append(" ".join(cell_text.split()))
                    
                    # Check if row contains page numbers (indicating it's a content row)
                    for cell in cells:
                        cell_text = cell.text.strip()
                        # Enhanced page number detection to handle complex patterns like "7-24, 82-87, 90-101"
                        if (cell_text.isdigit() or 
                            self._contains_page_numbers(cell_text)):
                            exist_page_num = True
                            break
                    
                    if exist_page_num:
                        # Extract links from this row, filtering out range end links
                        row_links = []
                        for cell in cells:
                            # Get cell text to analyze page ranges
                            cell_text = cell.get_text(strip=True)
                            
                            # Find all links in this cell
                            link_elems = cell.find_all("a")
                            cell_links = []
                            link_texts = []
                            
                            for link_elem in link_elems:
                                if (link_elem.attrs.get("href") 
                                    and link_elem.attrs.get("href").startswith("#")):
                                    link = link_elem.attrs.get("href").split("#")[-1]
                                    link_text = link_elem.get_text(strip=True)
                                    cell_links.append(link)
                                    link_texts.append(link_text)
                            
                            # Filter links based on page range patterns
                            filtered_links = self._filter_range_end_links(cell_text, cell_links, link_texts)
                            row_links.extend(filtered_links)
                        
                        # Determine if this item has multiple sections
                        is_multi_section = self._is_multi_section_item(text, row_links)
                        
                        if row_links:
                            # Create entry with all links for this row
                            table_links.append({
                                "text": text, 
                                "links": row_links,  # Changed from single "link" to multiple "links"
                                "is_multi_section": is_multi_section,
                                "link_count": len(row_links)
                            })
                            
                            # Log multi-section detection for debugging
                            if is_multi_section and len(row_links) > 1:
                                logging.info(f"Multi-section item detected: {text[0] if text else 'Unknown'} with {len(row_links)} links")
                                
            if table_links:
                link_info.append(table_links)
        return link_info
    
    def _contains_page_numbers(self, text: str) -> bool:
        """
        Enhanced page number detection to handle complex patterns.
        
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
                if (len(range_parts) == 2 and 
                    all(p.strip().isdigit() for p in range_parts)):
                    return True
        
        return False
    
    def _is_multi_section_item(self, text: List[str], links: List[str]) -> bool:
        """
        Determine if an item is split across multiple sections based on text content and link count.
        
        Args:
            text: List of cell text content for this row
            links: List of links found in this row
            
        Returns:
            True if item appears to be split across multiple sections
        """
        if len(links) <= 1:
            return False
            
        # Check if page numbers indicate multiple sections
        for cell_text in text:
            if self._contains_multiple_page_ranges(cell_text):
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
                "-" in part and 
                len(part.split("-")) == 2 and 
                all(p.strip().isdigit() for p in part.split("-"))
            ):
                valid_parts += 1
                
        return valid_parts > 1

    def _filter_range_end_links(self, cell_text: str, cell_links: List[str], link_texts: List[str]) -> List[str]:
        """
        Filter out range end links from page ranges like "7-24, 82-87, 90-101".
        Only keep the starting links (7, 82, 90) and omit ending links (24, 87, 101).
        
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
            
        # Parse the cell text to identify page ranges
        # Example: "7-24, 82-87, 90-101" should keep links for 7, 82, 90
        filtered_links = []
        
        # Split by commas to handle multiple ranges
        range_parts = [part.strip() for part in cell_text.split(",")]
        
        for range_part in range_parts:
            # Check if this part contains a range (e.g., "7-24" or "90-101")
            if "-" in range_part:
                # Extract start and end numbers using regex
                import re
                numbers = re.findall(r'\d+', range_part)
                
                if len(numbers) >= 2:
                    # This is a range, keep only the first number's link
                    start_num = numbers[0]
                    
                    # Find the link that corresponds to the start number
                    for i, link_text in enumerate(link_texts):
                        if link_text == start_num and i < len(cell_links):
                            filtered_links.append(cell_links[i])
                            logging.info(f"Range detected in '{range_part}': keeping start link for page {start_num}, omitting end link")
                            break
                elif len(numbers) == 1:
                    # Single number, keep its link
                    single_num = numbers[0]
                    for i, link_text in enumerate(link_texts):
                        if link_text == single_num and i < len(cell_links):
                            filtered_links.append(cell_links[i])
                            break
            else:
                # No range, just a single number - keep its link
                import re
                numbers = re.findall(r'\d+', range_part)
                if numbers:
                    single_num = numbers[0]
                    for i, link_text in enumerate(link_texts):
                        if link_text == single_num and i < len(cell_links):
                            filtered_links.append(cell_links[i])
                            break
        
        # If no ranges were detected, return all links (fallback)
        if not filtered_links:
            return cell_links
            
        return filtered_links

    @staticmethod
    @monitor_performance
    def extract_item_and_split(link_info: List):
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

        # 记录已经处理过的item,避免重复匹配
        processed_items = set()
        
        for match_map, match_function in match_function_map:
            for item_name, match_text in match_map.items():
                # 如果item已经被处理过,跳过后续匹配
                if item_name in processed_items:
                    continue
                    
                for one_table_link in link_info:
                    for cell in one_table_link["text"]:
                        if match_function(cell, match_text):
                            # Handle both old format (single "link") and new format (multiple "links") 
                            if "links" in one_table_link:
                                # New format: multiple links per row
                                links_to_add = one_table_link["links"]
                                is_multi_section = one_table_link.get("is_multi_section", False)
                                
                                # Log detection of multi-section items
                                if is_multi_section and len(links_to_add) > 1:
                                    logging.info(f"Processing multi-section item: {item_name} with {len(links_to_add)} sections")
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
                            
                            # 将已处理的item加入到processed_items集合中
                            processed_items.add(item_name)
                            
                            break  # Break after first match in this cell
        # Convert to list format: [(item_name, [link1, link2, ...]), ...]
        item_links = [(name, links) for name, links in item_links_dict.items()]
        
        # Log summary of processing results
        multi_section_count = sum(1 for name, links in item_links if len(links) > 1)
        single_section_count = len(item_links) - multi_section_count
        
        logging.info(f"Item processing summary: {single_section_count} single-section items, {multi_section_count} multi-section items")
        
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

class ParsedHtml10Q:
    """Parser for 10-Q HTML documents that handles same item numbers in different parts."""

    @staticmethod
    def extract_element_id(href: str) -> str:
        """Extract element ID from an XLink href."""
        return href.split("#")[-1]

    @monitor_performance
    def extract_html_link_info(self, html_content: str) -> List[Any]:
        """Optimized version: find table rows containing links and page numbers"""
        if not html_content:
            return []
            
        html_content = html_content.replace("&nbsp;", " ")
        
        try:
            soup = BeautifulSoup(html_content, "html.parser")
        except Exception as e:
            logging.error(f"Failed to parse HTML: {e}")
            return []

        # Remove script and style tags
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        link_info: List[Any] = []
        tables = soup.find_all("table")
        
        part_regex = re.compile(r"^\s*(Part\s+[IVXLC]+)\s*", re.IGNORECASE)
        part = None
        
        for table_idx, table in enumerate(tables):
            table_links: List[Dict[str, Any]] = []
            rows = table.find_all("tr")
            
            for row in rows:
                row_text = row.get_text().strip()
                part_match = part_regex.match(row_text)
                if part_match:
                    part = re.sub(r'\s+', ' ', part_match.group(1).lower())
                    
                cells = row.find_all("td", recursive=False)
                exist_page_num = False
                
                if cells and len(cells) <= 10:  # 限制单元格数量
                    # 优化：预先检查是否包含链接，避免不必要的处理
                    has_links = any(cell.find("a") for cell in cells)
                    if not has_links:
                        continue
                    
                    text = []
                    for cell in cells:
                        cell_text = cell.get_text(separator="  ", strip=True)
                        # 限制单元格文本长度
                        if len(cell_text) > 500:
                            cell_text = cell_text[:500]
                        text.append(cell_text)
                    
                    # 优化：使用更高效的页码检测
                    for cell in cells:
                        cell_text = cell.text.strip()
                        if len(cell_text) <= 10:  # 页码通常很短
                            if cell_text.isdigit() or (
                                "-" in cell_text
                                and len(cell_text.split("-")) == 2
                                and all(
                                    p.strip().isdigit()
                                    for p in cell_text.split("-")
                                )
                            ):
                                exist_page_num = True
                                break

                    if exist_page_num:
                        # 优化：只查找第一个有效链接
                        for cell in cells:
                            link_elem = cell.find("a")
                            if (link_elem 
                                and link_elem.attrs.get("href") 
                                and link_elem.attrs.get("href").startswith("#")):
                                link = link_elem.attrs.get("href").split("#")[-1]
                                if part:
                                    table_links.append(
                                        {"part": part, "text": text, "link": link}
                                    )
                                break
                                
            if table_links:
                link_info.append(table_links)
                
            # 限制返回的表格数量
            if len(link_info) > 20:
                logging.warning("Found too many tables with links, limiting results")
                break

        return link_info

    @staticmethod
    def extract_item_and_split(link_info: List):
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

    def extract_html(self, html_content: str, structure, markdown: bool = True) -> Dict[str, Any]:
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


def test():
    """ 检查确认正常类型的文件能解析成功 """
    pass


if __name__ == "__main__":
    from edgar import set_identity, get_by_accession_number
    from edgar.company_reports import TenK
    set_identity("1334307071@qq.com")
    file_id = 691553
    accession_number = "0001601712-25-000044"
    
    filing = get_by_accession_number(accession_number)
    print(
    ParsedHtml10K().extract_html(filing.html(), TenK.structure, markdown=True)
    )


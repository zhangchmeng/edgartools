import time
import logging
import re
from typing import List, Dict, Tuple, Any, Optional, Set

try:
    from bs4 import BeautifulSoup, Tag  # type: ignore
    from bs4.element import NavigableString

    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    Tag = None

from edgar.files.html_documents import (
    HtmlDocument,
    TableBlock,
    clean_html_root,
    decompose_page_numbers,
    merge_empty_div_elements,
    extract_and_format_content,
    Block,
    LinkBlock,
)
from edgar.files.htmltools import ChunkedDocument
from edgar.files.timeout_utils import monitor_performance


def time_section(section_name: str):
    """Context manager for timing code sections"""

    class TimingContext:
        def __init__(self, name):
            self.name = name
            self.start_time = None

        def __enter__(self):
            self.start_time = time.perf_counter()
            logging.debug(f"[TIMING] {self.name} - start")
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            end_time = time.perf_counter()
            duration = end_time - self.start_time
            if exc_type is None:
                logging.info(f"[TIMING] {self.name} - done: {duration:.3f}s")
            else:
                logging.warning(
                    f"[TIMING] {self.name} - exited with exception: {duration:.3f}s"
                )

    return TimingContext(section_name)


class AssembleText:
    """Text assembly utilities for HTML document processing"""

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

        if isinstance(start_element, NavigableString):
            pass
        else:
            start_element = clean_html_root(start_element)
   
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
            return "".join(str(part) for part in text_parts)
        else:
            text_parts = [
                text
                for text in AssembleText.assemble_block_text(blocks)
                if text is not None
            ]
            return "".join(text_parts)

    @staticmethod
    def assemble_html_document(tags: List[Tag], markdown: bool = False) -> str:
        """Simplified text assembly utilities for HTML document processing"""
        if not tags:
            return ""
        combined_html = "".join(str(tag) for tag in tags if tag is not None)
        combined_soup = BeautifulSoup(combined_html, "html.parser")
        merged_text = AssembleText.clean_and_assemble_text(
            combined_soup, markdown=markdown
        )
        return ChunkedDocument.clean_part_line(merged_text)

    # Define a one-time set of tags to ignore
    IGNORE_TAGS: Set[str] = {
        "script",
        "style",
        "meta",
        "link",
        "head",
        "noscript",
        "iframe",
        "svg",
    }

    @staticmethod
    def is_content_element(element: Tag) -> bool:
        """Check whether the element is a valid content element"""
        return not (
            hasattr(element, "name")
            and element.name
            and element.name in AssembleText.IGNORE_TAGS
        )

    @staticmethod
    @monitor_performance
    def assemble_items(
        html_content: str,
        item_links: List[Any],
        markdown: bool = True
    ) -> Dict[Tuple[str, str], List[Tag]]:
        """
        Simplified version: finds all link points and splits content by links.

        Returns a dictionary with structure:
        {
            ('extracted', 'OVERVIEW'): [element list],
            ('extracted', 'ITEM'): [element list],
        }

        Elements with the same key are merged (extended).
        """
        if not html_content or not item_links:
            return {}
            
        # Parse HTML content - use cache to avoid repeated parsing
        root: Tag = HtmlDocument.get_root(html_content)
        start_element = clean_html_root(root)
        decompose_page_numbers(start_element)
        merge_empty_div_elements(start_element)
        soup = start_element

        if not soup:
            logging.error("Failed to parse HTML content")
            return {}

        # Pre-allocate a result dictionary of sufficient size
        content_by_link = {}

        # Preprocess item_links into a more efficient format
        with time_section("process_item_links"):
            # Use a set to remove duplicates
            link_points = set()
            for item in item_links:
                if not isinstance(item, (tuple, list)) or len(item) < 2:
                    continue

                item_name = item[0]
                item_id_list = item[1]

                # Normalize to a list
                if isinstance(item_id_list, str):
                    item_id_list = [item_id_list]
                elif not isinstance(item_id_list, list):
                    continue

                # Add each link point and its name
                for item_id in item_id_list:
                    if item_id and item_name:
                        # Use tuples to deduplicate within the set
                        link_points.add((item_name, item_id))

            # Convert back to a list for subsequent processing
            link_points = list(link_points)

        # Find all elements in one pass
        with time_section("find_all_elements"):
            # Use dictionaries to cache element lookup results
            id_elements = {}
            name_elements = {}

            # Optimization: use CSS selectors to find all ID elements in one pass
            for elem in soup.select("[id]"):
                elem_id = elem.get("id")
                if elem_id:
                    id_elements[elem_id] = elem

            # Optimization: use CSS selectors to find all elements with a name attribute in one pass
            for elem in soup.select("a[name]"):
                elem_name = elem.get("name")
                if elem_name:
                    name_elements[elem_name] = elem

        link_element_list = []
        with time_section("create_ordered_links"):
            # Create an ordered list of link points based on document position
            ordered_links = []

            for name, link_id in link_points:
                # Find elements (by ID or name)
                element = id_elements.get(link_id) or name_elements.get(
                    link_id
                )
                if element:
                    link_element_list.append(element)
                    ordered_links.append((name, link_id, element))

            # Optimization: use a more efficient position estimation method
            html_str = str(soup)
            if ordered_links:
                # Create a mapping from element to its position to avoid recalculation
                element_positions = {}

                for name, link_id, element in ordered_links:
                    # Use the position of the element's string representation in the HTML as the sort key
                    elem_str = str(element)
                    pos = html_str.find(elem_str)
                    if pos >= 0:
                        element_positions[(name, link_id, element)] = pos
                    else:
                        element_positions[(name, link_id, element)] = float(
                            "inf"
                        )

                # Sort by position
                # First by position in the document, then by name, to ensure stable and predictable ordering
                ordered_links.sort(
                    key=lambda x: (
                        element_positions.get(x, float("inf")),
                        (str(x[0][1]).lower() if isinstance(x[0], (list, tuple)) and len(x[0]) > 1 else str(x[0]).lower())
                    )
                )

            # Log the number of matched link points to help debug empty list issues
            try:
                logging.debug(
                    f"assemble_items: matched link count={len(ordered_links)}"
                )
            except Exception:
                pass

        with time_section("extract_content_by_links"):
            # Extract content between link points
            for i, (name, link_id, element) in enumerate(ordered_links):
                # Create a tuple key for content
                # key = ('extracted', name)
                if isinstance(name, (list, tuple)):
                    key = tuple(name)
                else:
                    key = name

                # if key == ('part i', 'Item 3'):
                #     import pdb;pdb.set_trace()
                # Determine the boundaries of this section
                start_element = element
                end_element = (
                    ordered_links[i + 1][2]
                    if i + 1 < len(ordered_links)
                    else None
                )

                # Collect elements between the start and end
                section_elements = []
                current = start_element

                # Optimization: use a fixed-size buffer
                section_elements = []

                # Use a more efficient element traversal and avoid duplicates
                processed_elements = set()  # Track processed element IDs
                elements_to_process = []  # Temporarily store elements to process

                # Phase 1: collect all potential content elements (excluding end_link itself)
                # The next link's parent should also not be added directly; inspect child elements individually
                jump_elements = [one for one in end_element.parents]  if hasattr(end_element, "parents") else []

                while current and current != end_element:
                    # Include only meaningful content elements
                    if current in jump_elements:
                        next_elem = current.next_element
                        if not next_elem or next_elem in link_element_list:
                            break
                        current = next_elem
                        continue
                    elif AssembleText.is_content_element(current):
                        # Get the element's text content
                        elem_content = (
                            current.get_text()
                            if hasattr(current, "get_text")
                            else str(current)
                        )
                            
                        # Only consider adding when content is not empty
                        if (
                            elem_content
                            and id(current) not in processed_elements
                        ):
                            elements_to_process.append(current)
                            processed_elements.add(id(current))
                        
                    next_elem = current.next_sibling
                    if not next_elem or next_elem in link_element_list:
                        break
                    current = next_elem

                # Phase 2: filter out elements already contained by their ancestors or the start link
                processed_elements.clear()  # Reset processed elements set

                # Order elements by their position in the document (top to bottom)
                for element in elements_to_process:
                    # Check whether the element has already been processed
                    if id(element) in processed_elements:
                        continue

                    # Check the current element's children and mark them as processed
                    # Only traverse descendants if element is a Tag
                    if hasattr(element, "descendants"):
                        for child in element.descendants:
                            processed_elements.add(id(child))
                    else:
                        processed_elements.add(id(element))
                    # Add the current element to the results
                    section_elements.append(element)
                    processed_elements.add(id(element))

                # Add or extend elements for this key
                if key in content_by_link:
                    content_by_link[key].extend(section_elements)
                else:
                    content_by_link[key] = section_elements

            # Handle intro content (before the first link)
            if ordered_links:
                first_element = ordered_links[0][2]
                intro_elements = []

                # Optimization: directly use CSS selectors to find the body
                body = soup.find("body") or soup

                if body:
                    # Use more efficient element traversal
                    current = body

                    # Use an iterator instead of recursion and avoid duplicate elements
                    processed_intro_elements = set()  # Track processed element IDs
                    elements_to_process = []  # Temporarily store elements to process

                    # Phase 1: collect all potential content elements
                    for element in body.descendants:
                        if element == first_element:
                            break

                        # Only add meaningful content elements
                        if AssembleText.is_content_element(element):
                            # Get the element's text content
                            elem_content = (
                                element.get_text()
                                if hasattr(element, "get_text")
                                else str(element)
                            )

                            # Only consider adding when content is not empty
                            if (
                                elem_content
                                and id(element) not in processed_intro_elements
                            ):
                                elements_to_process.append(element)
                                processed_intro_elements.add(id(element))

                    # Phase 2: filter out elements already contained by their ancestors
                    processed_intro_elements.clear()  # Reset processed element set

                    for element in elements_to_process:
                        # Check whether the element has already been processed
                        if id(element) in processed_intro_elements:
                            continue

                        # Check the current element's children and mark them as processed
                        if hasattr(element, "descendants"):
                            for child in element.descendants:
                                processed_intro_elements.add(id(child))
                        else:
                            processed_intro_elements.add(id(element))

                        # Add the current element to the results
                        intro_elements.append(element)
                        processed_intro_elements.add(id(element))

                if intro_elements:
                    content_by_link[("extracted", "Item 0")] = intro_elements

        results = {}
        for key, value in content_by_link.items():
            results[key] = AssembleText.assemble_html_document(value)

        # Fallback when no link points are found: return the entire document as Item 0 and exit early
        if not ordered_links:
            try:
                body = soup.find("body") or soup
                results[("extracted", "Item 0")] = AssembleText.clean_and_assemble_text(
                    body, markdown=True
                )
                logging.debug(
                    "assemble_items: no ordered_links; returned whole document as Item 0"
                )
            except Exception as e:
                logging.error(f"Fallback assemble Item 0 failed: {e}")
            return results

        if ordered_links and not any("signature" in str(key).lower() for key in content_by_link.keys()):
            last_item = ordered_links[-1]
            last_item_name = last_item[0] if isinstance(last_item, tuple) else last_item[0]
            last_content = results.get(last_item_name, "")
            if last_content:
                sig_key = ["SIGNATURES", "SIGNATURE"]
                content_lines = last_content.split("\n")
                signature_line_index = None
                
                # Optimization: limit search scope
                search_lines = content_lines
                for i, line in enumerate(search_lines):
                    if line.strip().upper() in sig_key:
                        signature_line_index = len(content_lines) - len(search_lines) + i
                        break
                
                if signature_line_index is not None:
                    before_sig = "\n".join(content_lines[:signature_line_index])
                    sig_start_pos = len(before_sig) + 1
                    # items["Signature"] = last_content[sig_start_pos:].strip()
                    # items[last_item_name] = before_sig.strip()
                    results[("extracted", "signature")] = last_content[sig_start_pos:].strip()
                    results[last_item_name] = before_sig.strip()
                else:
                    results[("extracted", "signature")] = ""
        if ('part i', 'Item 1') in [one[0] for one in item_links]:
            if ('part i', 'Item 1')  not in [one[0] for one in ordered_links]:
                ordered_links.insert(0, (('part i', 'Item 1'), "", ""))

        results = AssembleText.re_regular_content(results, ordered_links)
        return results
    
    @staticmethod
    def re_regular_content(results, ordered_links):
        """ Check and fix unreasonable item segmentation results """
        # Handle: 1) first item missing link; 2) links pointing to page numbers causing multiple short items to merge into the first item on that page
        # 1) Determine split keywords; default order: SIGNATURES (excluded)/
        # 2) Validate the first item's content and append the split part to the current item
        # 3) For each other item, inspect the last 20 non-empty lines to see if a line starts with the next item header and split accordingly
            #    Partition according to standard item order / the order of incoming links

        # If there are no link points, return directly and skip normalization
        if not ordered_links:
            logging.debug("re_regular_content: no ordered_links, skip normalization")
            return results

        first_item = ordered_links[0][0]
        item0_keys = ("extracted", "Item 0")

        item0_content = results.get(item0_keys, "")
        # Find the line starting with "Signature" and split text into two parts: the part containing Signature and the remaining text
        # Put the first part into results[item0_keys]; append the remaining text to results[first_item]
    
        # Step 1/2: handle SIGNATURES split within Item 0
        if isinstance(item0_content, str) and item0_content.strip():
            lines = item0_content.splitlines()
            sig_index = None
            for idx, ln in enumerate(lines):
                up = ln.strip().upper()
                if up.startswith("SIGNATURES") or up.startswith("SIGNATURE"):
                    sig_index = idx
                    break
            # If SIGNATURES not found and Item 0 exceeds 10000 chars, split by EXHIBITS/EXHIBIT
            if sig_index is None and len(item0_content) > 10000:
                for idx, ln in enumerate(lines):
                    up = ln.strip().upper()
                    if up.startswith("EXHIBITS") or up.startswith("EXHIBIT"):
                        sig_index = idx
                        break

            if sig_index is not None:
                # Split starting from the line after the signature; keep the signature line in Item 0
                if len(lines) > sig_index+1:
                    before = "\n".join(lines[: sig_index + 1]).strip()
                    after = "\n".join(lines[sig_index + 1 :]).strip()
                    results[item0_keys] = before
                    # Append the 'after' part to the first item's content
                    if after:
                        if first_item in results and isinstance(results[first_item], str):
                            appended = (after + "\n" + results[first_item]).strip()
                            results[first_item] = appended
                        else:
                            results[first_item] = after

        # Step 3: check the tail of each item in ordered_links and split/merge when the next item header appears
        """
        Process items in the order of ordered_links; locate a line starting with the next item name in the tail, then split and move that tail to the next item.
        Example: if the tail of ('part i','Item 1') contains a line starting with 'Item 2', move the tail to ('part i','Item 2').
        """
        # Build the ordered key sequence
        ordered_names = [nm for (nm, _lid, _el) in ordered_links]

        for idx in range(len(ordered_names) - 1):
            curr_key = ordered_names[idx]
            next_key = ordered_names[idx + 1]

            # Current content must exist and be a string
            curr_val = results.get(curr_key)
            if not isinstance(curr_val, str) or not curr_val.strip():
                continue

            # Get the next item's label text for matching at line start
            if isinstance(next_key, (list, tuple)) and len(next_key) >= 2:
                next_label = str(next_key[1]).strip()
            else:
                next_label = str(next_key).strip()

            # Only attempt matching when next_label is non-empty
            if not next_label:
                continue

            # Check the last non-empty lines to locate the next item's header
            lines = [ln for ln in curr_val.splitlines() if ln.strip()]
            tail = lines[-50:] if len(lines) > 50 else lines

            # Build a match that starts with next_label, case-insensitive, allowing punctuation or whitespace after
            match_idx = None
            pattern = rf"^{re.escape(next_label)}(?:\b|\s|[\.|:;\-–—])"
            # for i, ln in enumerate(tail):
            #     if re is not None and re.match(pattern, ln.strip(), flags=re.IGNORECASE):
            #         # curr_key, next_key, ln, pattern
            #         match_idx = len(lines) - len(tail) + i
            #         break
            if "SIGNATURE" in next_label.upper():
                for i, ln in enumerate(tail):
                    if match_idx is not None:
                        break
                    for _label in ("SIGNATURE", "SIGNATURES"):
                        # pattern_curr = rf"^{re.escape(_label)}(?:\b|\s|[\.|:;\-–—])"
                        pattern = rf"^{re.escape(_label)}(?:\b|\s|[\.|:;\-–—])"
                        if re is not None and re.match(pattern, ln.strip(), flags=re.IGNORECASE):
                            # curr_key, next_key, ln, pattern
                            match_idx = len(lines) - len(tail) + i
                            break
            else:
                pattern = rf"^{re.escape(next_label)}(?:\b|\s|[\.|:;\-–—])"
                for i, ln in enumerate(tail):
                    if re is not None and re.match(pattern, ln.strip(), flags=re.IGNORECASE):
                        # curr_key, next_key, ln, pattern
                        match_idx = len(lines) - len(tail) + i
                        break

            # If no match, skip the current item
            if match_idx is None:
                continue

            # Perform split: keep the head in the current item and move the tail to the next item
            before = "\n".join(lines[:match_idx-1]).strip()
            after = "\n".join(lines[match_idx-1:]).strip()

            results[curr_key] = before
            if next_key in results and isinstance(results[next_key], str):
                results[next_key] = (after + "\n" + results[next_key] ).strip()
            else:
                results[next_key] = after

        # Reverse give-back of item content
        # Reverse logic: inspect the first 50 lines of each item for the current header
        # If found, give back the content before the header to the previous item
        # Reverse logic: from the last item, progressively give back front-matter to previous items

        for idx in range(len(ordered_names) - 1, 0, -1):
            prev_key = ordered_names[idx - 1]
            curr_key = ordered_names[idx]
            
            curr_val = results.get(curr_key)

            if not isinstance(curr_val, str) or not curr_val.strip():
                continue

            # Parse the current item's label text (to locate the current header)
            if isinstance(curr_key, (list, tuple)) and len(curr_key) >= 2:
                curr_label = str(curr_key[1]).strip()
            else:
                curr_label = str(curr_key).strip()

            if not curr_label:
                continue

            # Check the first 50 non-empty lines to locate the current item's header
            lines = [ln for ln in curr_val.splitlines() if ln.strip()]
            head = lines[:50] if len(lines) > 50 else lines

            match_idx = None
            if "SIGNATURE" in curr_label.upper():
                for i, ln in enumerate(head):
                    if match_idx is not None:
                        break
                    for _label in ("SIGNATURE", "SIGNATURES"):
                        pattern_curr = rf"^{re.escape(_label)}(?:\b|\s|[\.|:;\-–—])"
                        if re is not None and re.match(pattern_curr, ln.strip(), flags=re.IGNORECASE):
                            match_idx = i
                            break
            else:
                pattern_curr = rf"^{re.escape(curr_label)}(?:\b|\s|[\.|:;\-–—])"
                for i, ln in enumerate(head):
                    if re is not None and re.match(pattern_curr, ln.strip(), flags=re.IGNORECASE):
                        match_idx = i
                        break
            
            # If the header is found near the top and there is content before it, give that content back to the previous item
            if match_idx is None or match_idx <= 0:
                continue

            before = "\n".join(lines[:match_idx]).strip()
            after = "\n".join(lines[match_idx:]).strip()

            if before:
                # Update the current item's content to start at the header
                results[curr_key] = after

                # Append 'before' to the end of the previous item
                prev_val = results.get(prev_key)
                if isinstance(prev_val, str) and prev_val.strip():
                    results[prev_key] = (prev_val + "\n" + before).strip()
                else:
                    results[prev_key] = before
        return results

    @staticmethod
    def splite_f_footer_page(html_content):
        """
        Extract footer pages marked with F-/d for separate processing
        Return html_content after extracting those separate parts
        """
        soup = BeautifulSoup(html_content, "html.parser")
        footer = soup.find("footer")
        if footer and footer.get_text().strip().upper() == "F-/d":
            return footer.extract(), html_content.replace(str(footer), "")
        return None, html_content


if __name__ == "__main__":
    # Set log level to show performance monitoring information
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    with time_section("main_execution"):
        html_content = open(
            "/Users/chenghao.zhang/Documents/secfile/edgar/test.html", "r"
        ).read()
        item_links = [
            (
                ("extracted", "OVERVIEW"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_73",
            ),
            (
                ("extracted", "Citigroup's Five Reportable Business Segments"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_79",
            ),
            (("part i", "ITEM 2"), "i3b21a042e4b24a6a8aca8d89b8dbe271_82"),
            (
                ("extracted", "Executive Summary"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_85",
            ),
            (
                ("extracted", "Citi's Multiyear Transformation"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_88",
            ),
            (
                ("extracted", "Summary of Selected Financial Data"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_94",
            ),
            (
                ("extracted", "Segment Revenues and Income (Loss)"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_97",
            ),
            (
                ("extracted", "Services"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_106",
            ),
            (
                ("extracted", "Markets"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_109",
            ),
            (
                ("extracted", "Banking"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_112",
            ),
            (("extracted", "Wealth"), "i3b21a042e4b24a6a8aca8d89b8dbe271_115"),
            (
                ("extracted", "U.S. Personal Banking"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_118",
            ),
            (
                (
                    "extracted",
                    "All Other—Divestiture-Related Impacts (Reconciling Items)",
                ),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_121",
            ),
            (
                ("extracted", "All Other—Managed Basis"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_127",
            ),
            (
                ("extracted", "CAPITAL RESOURCES"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_133",
            ),
            (
                ("extracted", "Managing Global Risk—Table of Contents"),
                "i3b21a042e4b24a6a8aca8d89b8dbe271_136",
            ),
        ]

        # Run performance test
        result = AssembleText.assemble_items(html_content, item_links)
        print(f"Processing complete, generated {len(result)} items")

        # result[('extracted', ('extracted', 'Executive Summary'))]
        for key, value in result.items():
            print(f"Item: {key}, content length: {len(value)} chars")
            print(AssembleText.assemble_html_document(value))


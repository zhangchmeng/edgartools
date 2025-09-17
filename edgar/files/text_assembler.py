import time
import logging
from typing import List, Dict, Optional, Any
from bs4 import Tag

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
from edgar.files.timeout_utils import TimeoutManager, monitor_performance


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
            if isinstance(item, (tuple, list)) and len(item) >= 2:
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

        # Use timeout manager for processing
        with TimeoutManager(timeout_seconds=15):
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
                    if (
                        isinstance(first_item, (tuple, list))
                        and len(first_item) >= 2
                    ):
                        first_item_ids = first_item[1]
                    else:
                        first_item_ids = None
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
                        target = id_cache.get(item_id) or name_cache.get(
                            item_id
                        )
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
                            for (
                                other_name,
                                other_ids,
                            ) in item_name_to_ids.items():
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
                            section_content = (
                                AssembleText.assemble_html_document(
                                    content, markdown=markdown
                                )
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
                            items[item_name] = separator.join(
                                merged_content_parts
                            )
                            logging.info(
                                f"Merged {len(merged_content_parts)} sections for {item_name}"
                            )
                        else:
                            items[item_name] = merged_content_parts[0]

                # Step 3: Handle Signatures
                if "Signature" not in items and item_links:
                    last_item = item_links[-1]
                    if (
                        isinstance(last_item, (tuple, list))
                        and len(last_item) >= 1
                    ):
                        last_item_name = last_item[0]
                    else:
                        last_item_name = ""
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

            except Exception as e:
                if "timeout" in str(e).lower():
                    logging.error(
                        "HTML processing timeout (exceeded 15 seconds), returning empty result"
                    )
                    return {}
                elif "Item duplication detected" in str(e):
                    logging.error(
                        f"Duplicate items detected, returning empty result: {e}"
                    )
                    return {}
                else:
                    logging.error(f"Error in assemble_items: {e}")
                    return {}

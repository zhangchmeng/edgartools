from typing import List, Dict, Optional, Any
import logging
from edgar.files.base_parser import BaseHtmlParser
from edgar.files.text_assemble import AssembleText
from edgar.files.timeout_utils import monitor_performance
# from edgar.files.extract_item_ai import extract_items_with_ai
from edgar.files.extract_item_ai_all import extract_catalog_structure

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

    @staticmethod
    def classify_items_to_parts(item_links: List[tuple[str, List[str]]], structure) -> List[tuple[tuple[str, str], str]]:
        """
        Classify items into their corresponding parts
        
        Args:
            item_links: List of item links in format [(item_name, [link1, link2, ...]), ...]
            structure: Object containing part and item structure information
            
        Returns:
            List[tuple[tuple[str, str], str]]: [
                (('part i', 'Item 4'), 'i3b21a042e4b24a6a8aca8d89b8dbe271_265'),
                (('part i', 'Item 1'), 'i3b21a042e4b24a6a8aca8d89b8dbe271_271'),
                (('part ii', 'Item 5'), 'i3b21a042e4b24a6a8aca8d89b8dbe271_649'),
                ...
            ]
        """
        # Create item to part mapping
        item_to_part = {}
        for part_name in structure.structure:
            part_items = structure.get_part(part_name)
            for item_name in part_items:
                item_to_part[item_name.lower()] = part_name.lower()
        
        # Build result list
        result = []
        
        # Classify items to corresponding parts
        for item_name, links in item_links:
            item_name_lower = item_name.lower()
            part_name = item_to_part.get(item_name_lower)
            
            # Create tuple for each link
            for link in links:
                if part_name:
                    result.append(((part_name, item_name), link))
                else:
                    result.append((('extracted', item_name), link))
        
        # Record classification statistics
        total_links = sum(len(links) for _, links in item_links)
        classified_links = len([r for r in result if r[0][0] != 'extracted'])
        extracted_links = len([r for r in result if r[0][0] == 'extracted'])
        
        logging.info(
            f"Item classification summary: {classified_links} links classified to parts, {extracted_links} links extracted, {total_links} total links"
        )
        return result

    def extract_html(
        self, html_content: str, structure, markdown: bool = False, form_type: str = "10-K"
    ) -> Dict[str, Any]:
        """
        Find rows in tables that:
            1. Contain links
            2. Have a separate cell storing page numbers
        """
        index_table = self.extract_html_link_info(html_content)
        raw_item_links = self.extract_item_and_split(index_table)
        item_links = self.classify_items_to_parts(raw_item_links, structure)
        

        if not item_links or (len(item_links) < 10 and form_type == "10-K"):
            # new_item_links = extract_items_with_ai(structure.structure, index_table)
            new_item_links = extract_catalog_structure(html_content, structure.structure, form_type)
            if new_item_links:
                item_links = new_item_links

        item_result = AssembleText.assemble_items(
            html_content, item_links, markdown=markdown
        )
        
        # Step 4: Group items by part based on new item_result structure
        result = {part_name.lower(): {} for part_name in structure.structure}
        result["extracted"] = {}

        for key, content in item_result.items():
            if isinstance(key, tuple) and len(key) == 2:
                # Handle tuple format: ('part i', 'Item 1')
                part_name, item_name = key
                part_name = part_name.lower()
                item_name = item_name.lower()
                if part_name in result:
                    result[part_name][item_name] = content
                else:
                    result["extracted"][item_name] = content
            else:
                # Handle string format: 'Item 0', 'Signature'
                item_name = str(key).lower()
                # Try to find which part this item belongs to
                item_to_part = {}
                for part_name in structure.structure:
                    part_items = structure.get_part(part_name)
                    for part_item_name in part_items:
                        item_to_part[part_item_name.lower()] = part_name.lower()
                
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
            "extracted": {"Signatures": "Signature"},
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
                                # 如果没有part信息，则根据匹配的内容推断part
                                link_part = one_link.get("part")
                                if link_part is None:
                                    part_i_indicators = [
                                        "Financial Statements", "Management's Discussion", 
                                        "Quantitative and Qualitative", "Controls and Procedures"
                                    ]
                                    part_ii_indicators = [
                                        "Legal Proceedings", "Risk Factors", "Unregistered Sales",
                                        "Defaults Upon Senior", "Mine Safety", "Other Information", "Exhibits"
                                    ]
                                    
                                    text_content = " ".join(one_link["text"]).lower()
                                    
                                    if any(indicator.lower() in text_content for indicator in part_i_indicators):
                                        link_part = "part i"
                                    elif any(indicator.lower() in text_content for indicator in part_ii_indicators):
                                        link_part = "part ii"
                                    elif item_name.lower() in ("item 1a", "item 5", "item 6"):
                                        link_part = "part ii"
                                    elif "signature" in text_content:
                                        link_part = "extracted"
                                
                                if (
                                    (link_part == part or (link_part is None and part in ["part i", "part ii", "extracted"]))
                                    and (part, item_name) not in item_dict
                                ):
                                    item_dict[(part, item_name)] = link

        # Convert to list format without sorting
        item_links = [(name, link) for name, link in item_dict.items()]
        return item_links

    def check_10q_index_table(self, index_table: List[List[Dict[str, Any]]]) -> bool:
        """
        检查传入的index_table，如果符合第一种含有part有效，否则返回False
        
        Args:
            index_table: 从extract_html_link_info返回的表格数据结构
                        List[List[Dict[str, Any]]]格式
        
        Returns:
            bool: 如果index_table中包含part信息则返回True，否则返回False
        
        Examples:
            有效格式 (ava_ml): 包含'part'字段的字典
            [{'part': 'part i', 'text': ['Item 1.', 'Financial Statements', '1'], 'link': '...'}, ...]
            
            无效格式 (inva_ml): 不包含'part'字段的字典
            [{'text': ['Item 1.', 'Financial Statements (unaudited)', ''], 'link': '...'}, ...]
        """
        # ava_ml = [[{'part': 'part i', 'text': ['Item 1.', 'Financial Statements', '1'], 'link': 'i056866be11a54295b8c21e0877b67331_13'}, {'part': 'part i', 'text': ['Item 2.', 'Management's Discussion and Analysis of Financial Condition and Results of Operations', '12'], 'link': 'i056866be11a54295b8c21e0877b67331_67'}, {'part': 'part i', 'text': ['Item 3.', 'Quantitative and Qualitative Disclosures About Market Risk', '18'], 'link': 'i056866be11a54295b8c21e0877b67331_145'}, {'part': 'part i', 'text': ['Item 4.', 'Controls and Procedures', '18'], 'link': 'i056866be11a54295b8c21e0877b67331_148'}, {'part': 'part ii', 'text': ['Item 1.', 'Legal Proceedings', '19'], 'link': 'i056866be11a54295b8c21e0877b67331_154'}, {'part': 'part ii', 'text': ['Item 1A.', 'Risk Factors', '20'], 'link': 'i056866be11a54295b8c21e0877b67331_157'}, {'part': 'part ii', 'text': ['Item 2.', 'Unregistered Sales of Equity Securities and Use of Proceeds', '22'], 'link': 'i056866be11a54295b8c21e0877b67331_160'}, {'part': 'part ii', 'text': ['Item 3.', 'Defaults Upon Senior Securities', '22'], 'link': 'i056866be11a54295b8c21e0877b67331_163'}, {'part': 'part ii', 'text': ['Item 4.', 'Mine Safety Disclosures', '22'], 'link': 'i056866be11a54295b8c21e0877b67331_166'}, {'part': 'part ii', 'text': ['Item 5.', 'Other Information', '22'], 'link': 'i056866be11a54295b8c21e0877b67331_169'}, {'part': 'part ii', 'text': ['Item 6.', 'Exhibits', '22'], 'link': 'i056866be11a54295b8c21e0877b67331_175'}]]
        # inva_ml = [[{'text': ['Item 1.', 'Financial Statements (unaudited)', ''], 'link': 'item_1___financial_statements'}, {'text': ['', 'Condensed Consolidated Statements of Financial Condition', '1'], 'link': 'condensed_consolidated_statements_financ'}, {'text': ['', 'Condensed Consolidated Statements of Income', '2'], 'link': 'statements_of_income'}, {'text': ['', 'Condensed Consolidated Statements of Comprehensive Income', '3'], 'link': 'comprehensive_income'}, {'text': ['', 'Condensed Consolidated Statements of Changes in Equity', '4'], 'link': 'changes_in_equity'}, {'text': ['', 'Condensed Consolidated Statements of Cash Flows', '6'], 'link': 'cash_flows'}, {'text': ['', 'Notes to Condensed Consolidated Financial Statements', '7'], 'link': 'notes_to_the_condensed'}, {'text': ['Item 2.', 'Management's Discussion and Analysis of Financial Condition and Results of Operations', '38'], 'link': 'item_2_management'}, {'text': ['Item 3.', 'Quantitative and Qualitative Disclosures About Market Risk', '72'], 'link': 'item_3_quantitative'}, {'text': ['Item 4.', 'Controls and Procedures', '73'], 'link': 'item_4_controls_procedures'}], [{'text': ['Item 1.', 'Legal Proceedings', '74'], 'link': 'item_1__legal_proceedings'}, {'text': ['Item 1A.', 'Risk Factors', '75'], 'link': 'item_1a_risk_factors'}, {'text': ['Item 2.', 'Unregistered Sales of Equity Securities and Use of Proceeds', '76'], 'link': 'item_2__unregistered_sales_equity_securi'}, {'text': ['Item 6.', 'Exhibits', '77'], 'link': 'exhibits'}], [{'part': 'extracted', 'text': ['', 'Signatures', '78'], 'link': 'signature_page'}]]
        
        if not index_table or not isinstance(index_table, list):
            return False
        
        # 遍历所有表格
        for table in index_table:
            if not isinstance(table, list):
                continue
                
            # 遍历表格中的每一行
            for row in table:
                if isinstance(row, dict) and 'part' in row:
                    # 如果找到包含'part'字段的行，说明是有效格式
                    part_value = row.get('part')
                    if part_value and isinstance(part_value, str):
                        # 检查part值是否为有效的part格式（如'part i', 'part ii'等）
                        part_lower = part_value.lower().strip()
                        if part_lower.startswith('part ') and len(part_lower) > 5:
                            return True
        
        # 如果没有找到任何包含有效part信息的行，返回False
        return False


    def extract_html(
        self, html_content: str, structure, markdown: bool = True, form_type: str = "10-Q"
    ) -> Dict[str, Any]:
        """Extract 10-Q items from HTML content, handling same item numbers in different parts."""
        index_table = self.extract_html_link_info(html_content)
        if self.check_10q_index_table(index_table):
            item_links = self.extract_item_and_split(index_table)
            if isinstance(item_links, dict):
                item_links = list(item_links.items())
            elif not isinstance(item_links, list):
                item_links = []

            if not item_links or (len(item_links) < 5 and form_type == "10-Q"):
                # new_item_links = extract_items_with_ai(structure.structure, index_table)
                new_item_links = extract_catalog_structure(html_content, structure.structure, form_type)
                if new_item_links:
                    item_links = new_item_links
        else:
            item_links = extract_catalog_structure(html_content, structure.structure, form_type)
            # item_links = extract_items_with_ai(structure.structure, index_table)

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

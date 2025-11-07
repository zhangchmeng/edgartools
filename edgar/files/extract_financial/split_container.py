from bs4 import BeautifulSoup
from edgar.files.extract_financial.base import BasePageSplitter, PageContent


class ContainerPageSplitter(BasePageSplitter):
    """Page number splitter based on container elements"""

    def extract_page_numbers(self, html_content: str):
        """
        Extract page numbers by finding container elements that contain F-numbers.

        Args:
            html_content: HTML content string

        Returns:
            PageNumberExtractionResult: Structured page number extraction result
        """
        """
        # Step 1: find all elements that contain page numbers
        all_page_elements = self._find_all_page_elements(soup, target_tags)
        
        # Step 2: filter out parent containers, keep the deepest child elements
        filtered_elements = self._filter_deepest_elements(all_page_elements)
        
        # Step 3: classify page numbers by tag type
        tag_page_info = self._classify_by_tag_type(filtered_elements)

        # Step 4: sort and check continuity for each tag type
        valid_tag_pages = self._validate_tag_sequences(tag_page_info)

        # Step 5: select the best tag type
        best_tag_name = self._select_best_tag_type(valid_tag_pages)

        # Step 6: process page numbers for the best tag type
        if best_tag_name and best_tag_name in valid_tag_pages:
            page_numbers, page_contents = self._process_best_tag_pages(
                soup, valid_tag_pages[best_tag_name]
            )
        
        """
        soup = BeautifulSoup(html_content, "html.parser")
        page_numbers = []
        page_contents = {}

        # Find all container elements that contain page numbers
        sorted_containers = self._find_page_containers(soup)

        if not sorted_containers:
            return self._create_standard_result([], {}), soup

        # Find the longest ordered subsequence
        # Handle duplicate page numbers by selecting the longest ordered subsequence
        # Table of contents and page numbers may coexist; HTML may be entirely table/td elements
        # Issue 2: when content is all TOC, if the body lacks footers and fewer than five pages are found, treat as invalid and discard
        sorted_containers = self._find_longest_ordered_subsequence(
            sorted_containers
        )

        if not sorted_containers:
            return self._create_standard_result([], {}), soup

        # Process containers in page-number order
        prev_container = None

        # Process each page-number container
        for page_number, container in sorted_containers:
            # container.parents
            while container.parent:
                if (
                    container.parent.get_text().strip()
                    == container.get_text().strip()
                ):
                    container = container.parent
                else:
                    break

            if prev_container is None:
                prev_container = self._find_page_start(container)

            # Determine the page content range: all elements between the previous and current containers
            # Extract page content elements
            # if page_number == 27:
            #     import pdb;pdb.set_trace()
            page_elements = self._extract_container_content(
                prev_container, container
            )

            if page_elements:
                prev_container.extract()
                # Create page content object
                page_content = self._create_page_content(
                    page_number, page_elements
                )
                page_contents[page_number] = page_content

                # Add to page number list
                if page_number not in page_numbers:
                    page_numbers.append(page_number)
            prev_container = container

        return self._create_standard_result(page_numbers, page_contents), soup

    def _find_page_containers(self, soup):
        """Find all container elements that include page numbers"""
        page_containers = []

        # Candidate tags that may contain page numbers
        target_tags = [
            "div",
            "span",
            "p",
            "td",
            "th",
            "section",
            "article",
            "header",
            "footer",
        ]

        for tag in soup.find_all(target_tags):
            text_content = self._extract_text_content(tag)
            page_numbers = self._search_f_number_pattern(text_content)

            if page_numbers:
                # Keep only the first page number found
                page_containers.append((page_numbers[0], tag))

        # Deduplicate page numbers; keep the deepest container
        return self._filter_deepest_containers(page_containers)

    def _filter_deepest_containers(self, page_containers):
        """Filter out parent containers; keep only the deepest containers"""
        filtered_containers = []

        for page_num, container in page_containers:
            # Check whether any child also contains the same page number
            has_child_with_same_page = False
            for other_page_num, other_container in page_containers:
                if (
                    other_page_num == page_num
                    and other_container != container
                    and container in other_container.parents
                ):
                    has_child_with_same_page = True
                    break

            # If no child contains the same page number, keep this container
            if not has_child_with_same_page:
                # Ensure the container does not contain an anchor tag
                if not container.find("a"):
                    filtered_containers.append((page_num, container))

        return filtered_containers

    def _extract_container_content(self, prev_container, current_container):
        """Iterate in document order and remove all nodes between prev_container and current_container.
        Follow the traversal strategy of _extract_page_content_between_hrs:
        prefer next_sibling; when missing, backtrack to ancestors' next_sibling.
        Safely skip ancestors of current_container to avoid deleting the container itself.
        """
        removed_elements = []
        if prev_container is None or current_container is None:
            return removed_elements
        if prev_container is current_container:
            return removed_elements

        # Precompute the ancestor set of current_container for quick checks
        current_ancestors = set(list(current_container.parents))

        # Choose the traversal starting point:
        # - If prev is an ancestor of current, start within its subtree (prev.next_element);
        # - Otherwise, start at the first sibling after prev's subtree or the ancestor's next sibling (skip across subtrees).
        if prev_container in current_ancestors:
            node = getattr(prev_container, "next_element", None)
        else:
            ns = getattr(prev_container, "next_sibling", None)
            if ns is None:
                parent = getattr(prev_container, "parent", None)
                while parent is not None:
                    ns = getattr(parent, "next_sibling", None)
                    if ns is not None:
                        break
                    parent = getattr(parent, "parent", None)
            node = ns

        # Linearly traverse and remove until reaching current_container
        while node is not None and node is not current_container:
            # If the current node is an ancestor of current_container, do not delete it; dive into its subtree
            if node in current_ancestors:
                node = getattr(node, "next_element", None)
                continue

            # Precompute the next candidate after removal: prefer next_sibling, otherwise backtrack to the ancestor's next_sibling
            ns = getattr(node, "next_sibling", None)
            if ns is None:
                parent = getattr(node, "parent", None)
                while parent is not None:
                    ns = getattr(parent, "next_sibling", None)
                    if ns is not None:
                        break
                    parent = getattr(parent, "parent", None)

            # Remove and collect the current node (extract returns the removed node), skipping its subtree
            removed_elements.append(node.extract())

            # Move to the next candidate node
            node = ns
        removed_elements.append(current_container)
        return removed_elements

    def _find_page_start(self, f_number_container):
        """
        Search upward to find the start position of the page.
        """
        
        # TODO: Assume a sibling element with only digits may mark the end of the previous page
        current_element = f_number_container.previous_element
        page_start = f_number_container  # Default to starting from the F-number container

        # Search backward until another F-number is found or until the start of the document
        while current_element:
            # Check whether the current element matches an F-number pattern or pure digits
            if hasattr(current_element, "get_text"):
                text_content = self._extract_text_content(current_element)
                # # Check whether it contains F-number
                # if self._search_f_number_pattern(text_content):
                #     break
                # Check if it is pure digit text (sibling element)
                if (
                    getattr(current_element, "parent", None) is not None
                    and getattr(f_number_container, "parent", None) is not None
                    and current_element.parent.name
                    == f_number_container.parent.name
                    and (
                        text_content.strip().isdigit()
                        or text_content.strip().strip("-").strip().isdigit()
                    )
                ):
                    break
            # Update page start position
            page_start = current_element
            current_element = current_element.previous_element
        return page_start

    def _create_page_content(self, page_number, elements):
        """Create page content object"""
        # Create page soup
        page_soup = BeautifulSoup("", "html.parser")
        page_body = page_soup.new_tag("body")
        page_soup.append(page_body)

        # Add elements in bulk (copy instead of move)
        for element in elements:
            if hasattr(element, 'name'):  # is a tag element
                # Create a deep copy of the element
                element_copy = BeautifulSoup(str(element), "html.parser")
                for child in element_copy.children:
                    if hasattr(child, "name"):
                        page_body.append(child)
            elif hasattr(element, 'string'):  # is a text node
                page_body.append(element.string)

        # Call page separator extraction and cleanup
        # separators, cleaned_soup = self._extract_page_separators(page_soup, page_number)

        # Create page content object
        return PageContent(
            page_number=page_number,
            page_soup=page_soup,
            text_content=page_soup.get_text(),
            page_separators=elements,
        )

    def _find_longest_ordered_subsequence(self, containers):
        """
        Find the longest ordered subsequence to handle duplicate page numbers.

        Args:
            containers: list of tuples [(page_number, container), ...]

        Returns:
            The container list of the longest ordered subsequence
        """
        if not containers:
            return []

        # 按page number分组，每个page number保留最后一个出现的container
        page_dict = {}
        for page_num, container in containers:
            page_dict[page_num] = container

        # 按page numbersort
        sorted_pages = sorted(page_dict.items())

        # 找到最长的连续有序子序列
        if not sorted_pages:
            return []

        # 从第一个page number开始找最长连续序列
        longest_sequence = []
        current_sequence = [sorted_pages[0]]

        for i in range(1, len(sorted_pages)):
            current_page, current_container = sorted_pages[i]
            prev_page, prev_container = current_sequence[-1]

            # 如果当前page number是连续的，加入当前序列
            if current_page == prev_page + 1:
                current_sequence.append((current_page, current_container))
            else:
                # 如果不连续，check当前序列是否更长
                if len(current_sequence) > len(longest_sequence):
                    longest_sequence = current_sequence[:]
                # 开始新的序列
                current_sequence = [(current_page, current_container)]

        # check最后一个序列
        if len(current_sequence) > len(longest_sequence):
            longest_sequence = current_sequence[:]

        # 如果没有找到合适的序列，Returns所有去重后的page number
        if len(longest_sequence) < 2:
            return sorted_pages

        return longest_sequence


if __name__ == "__main__":
    # TestContainerPageSplitter
    # html_file_path = "/Users/chenghao.zhang/Documents/secfile/edgar/0000908311-25-000017.html"
    html_file_path = "/Users/chenghao.zhang/Documents/secfile/extract_financial/test.html"

    try:
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        print("=== ContainerPageSplitter Test ===")
        print(f"Test file: {html_file_path}")
        print(f"file size: {len(html_content):,} 字符\n")

        # Test Container split器
        print("Test Container splitter:")
        container_splitter = ContainerPageSplitter()
        container_results, soup = container_splitter.extract_page_numbers(
            html_content
        )

        print(f"   Found page numbers: {container_results.page_numbers}")
        print(f"   Total page count: {len(container_results.page_numbers)}")
        print(f"   Page content count: {len(container_results.page_contents)}")
        # container_results.page_contents[1].text_content


    except FileNotFoundError:
        print(f"Error: File not found {html_file_path}")
        print("Please ensure the HTML file exists")
    except Exception as e:
        print(f"Error during test: {e}")
        import traceback

        traceback.print_exc()

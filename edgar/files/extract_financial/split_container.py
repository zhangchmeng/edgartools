from bs4 import BeautifulSoup
from edgar.files.extract_financial.base import BasePageSplitter, PageContent


class ContainerPageSplitter(BasePageSplitter):
    """基于容器元素的页码分割器"""

    def extract_page_numbers(self, html_content: str):
        """
        通过查找包含F-数字的容器元素来提取页码

        Args:
            html_content: HTML内容字符串

        Returns:
            PageNumberExtractionResult: 结构化的页码提取结果
        """
        """
        # 第一步：找到所有包含页码的元素
        all_page_elements = self._find_all_page_elements(soup, target_tags)
        
        # 第二步：过滤掉父容器，只保留最深层的子元素
        filtered_elements = self._filter_deepest_elements(all_page_elements)
        
        # 第三步：按标签类型分类页码信息
        tag_page_info = self._classify_by_tag_type(filtered_elements)

        # 第四步：对每种标签类型的页码进行排序和连续性检查
        valid_tag_pages = self._validate_tag_sequences(tag_page_info)

        # 第五步：选择最优的标签类型
        best_tag_name = self._select_best_tag_type(valid_tag_pages)

        # 第六步：处理最优标签类型的页码
        if best_tag_name and best_tag_name in valid_tag_pages:
            page_numbers, page_contents = self._process_best_tag_pages(
                soup, valid_tag_pages[best_tag_name]
            )
        
        """
        soup = BeautifulSoup(html_content, "html.parser")
        page_numbers = []
        page_contents = {}

        # 查找所有包含页码的容器元素
        sorted_containers = self._find_page_containers(soup)

        if not sorted_containers:
            return self._create_standard_result([], {}), soup

        # 找到最长的有序子序列
        # 存在重复页码，需要找到最长的有序子序列
        # 会出现目录与页码同时存在的情况，html 全部都是table与td元素
        # issue2 拿到的内容均为目录，如果正文不带页脚，处理将丢弃找到的内容，如果页码不足五页，认为内容不合法，丢弃内容
        sorted_containers = self._find_longest_ordered_subsequence(
            sorted_containers
        )

        if not sorted_containers:
            return self._create_standard_result([], {}), soup

        # 按页码顺序处理容器
        prev_container = None

        # 处理每个页码容器
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

            # 确定页面内容范围：从当前容器到下一个容器之间的所有元素
            # 提取页面内容元素
            # if page_number == 27:
            #     import pdb;pdb.set_trace()
            page_elements = self._extract_container_content(
                prev_container, container
            )

            if page_elements:
                # 创建页面内容对象
                page_content = self._create_page_content(
                    page_number, page_elements
                )
                page_contents[page_number] = page_content

                # 添加到页码列表
                if page_number not in page_numbers:
                    page_numbers.append(page_number)

            prev_container = container

        return self._create_standard_result(page_numbers, page_contents), soup

    def _find_page_containers(self, soup):
        """查找所有包含页码的容器元素"""
        page_containers = []

        # 查找可能包含页码的标签
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
                # 只取第一个找到的页码
                page_containers.append((page_numbers[0], tag))

        # 过滤掉重复的页码，保留最深层的容器
        return self._filter_deepest_containers(page_containers)

    def _filter_deepest_containers(self, page_containers):
        """过滤掉父容器，只保留最深层的容器"""
        filtered_containers = []

        for page_num, container in page_containers:
            # 检查是否有子元素也包含相同的页码
            has_child_with_same_page = False
            for other_page_num, other_container in page_containers:
                if (
                    other_page_num == page_num
                    and other_container != container
                    and container in other_container.parents
                ):
                    has_child_with_same_page = True
                    break

            # 如果没有子元素包含相同页码，则保留此容器
            if not has_child_with_same_page:
                # 检查容器是否包含a标签
                if not container.find("a"):
                    filtered_containers.append((page_num, container))

        return filtered_containers

    def _extract_container_content(self, prev_container, current_container):
        """按文档顺序逐个提取并删除 prev_container 与 current_container 之间的所有节点。
        参考 _extract_page_content_between_hrs 的“next_sibling 优先，缺失时向上回溯祖先的 next_sibling”的遍历策略，
        同时安全跳过 current_container 的祖先，避免误删容器本身。
        """
        removed_elements = []
        if prev_container is None or current_container is None:
            return removed_elements
        if prev_container is current_container:
            return removed_elements

        # 预计算 current_container 的祖先集合（用于快速判断）
        current_ancestors = set(list(current_container.parents))

        # 选择遍历起点：
        # - 若 prev 是 current 的祖先，则从其子树内开始（prev.next_element）；
        # - 否则，从 prev 子树结束后的第一个兄弟或祖先的下一个兄弟开始（横向越过子树）。
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

        # 线性遍历并删除，直到遇到 current_container
        while node is not None and node is not current_container:
            # 如果当前节点是 current_container 的祖先，不删除该节点，深入其子树
            if node in current_ancestors:
                node = getattr(node, "next_element", None)
                continue

            # 预先计算删除后的下一个候选节点：优先使用 next_sibling，其次向上回溯祖先的 next_sibling
            ns = getattr(node, "next_sibling", None)
            if ns is None:
                parent = getattr(node, "parent", None)
                while parent is not None:
                    ns = getattr(parent, "next_sibling", None)
                    if ns is not None:
                        break
                    parent = getattr(parent, "parent", None)

            # 删除并收集当前节点（extract 返回被移除的节点），一次性越过其子树
            removed_elements.append(node.extract())

            # 进入下一个候选节点
            node = ns
        removed_elements.append(current_container)
        return removed_elements

    def _find_page_start(self, f_number_container):
        """
        向上查找页面的起始位置
        """
        # TODO 默认认为 同级元素且仅包含数字的元素可能是上一页的结尾
        current_element = f_number_container.previous_element
        page_start = f_number_container  # 默认从F-number容器开始

        # 向前查找，直到找到另一个F-number或到达文档开始
        while current_element:
            # 检查当前元素是否包含F-number模式或纯数字
            if hasattr(current_element, "get_text"):
                text_content = self._extract_text_content(current_element)
                # # 检查是否包含F-number
                # if self._search_f_number_pattern(text_content):
                #     break
                # 检查是否为纯数字文本(同级元素)
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
            # 更新页面起始位置
            page_start = current_element
            current_element = current_element.previous_element
        return page_start

    def _create_page_content(self, page_number, elements):
        """创建页面内容对象"""
        # 创建页面soup
        page_soup = BeautifulSoup("", "html.parser")
        page_body = page_soup.new_tag("body")
        page_soup.append(page_body)

        # 批量添加元素（复制而不是移动）
        for element in elements:
            if hasattr(element, "name"):  # 是标签元素
                # 创建元素的深拷贝
                element_copy = BeautifulSoup(str(element), "html.parser")
                for child in element_copy.children:
                    if hasattr(child, "name"):
                        page_body.append(child)
            elif hasattr(element, "string"):  # 是文本节点
                page_body.append(element.string)

        # 调用页码拆分和清理方法
        # separators, cleaned_soup = self._extract_page_separators(page_soup, page_number)

        # 创建页面内容对象
        return PageContent(
            page_number=page_number,
            page_soup=page_soup,
            text_content=page_soup.get_text(),
            page_separators=elements,
        )

    def _find_longest_ordered_subsequence(self, containers):
        """
        找到最长的有序子序列，处理重复页码的情况

        Args:
            containers: [(page_number, container), ...] 的列表

        Returns:
            最长有序子序列的容器列表
        """
        if not containers:
            return []

        # 按页码分组，每个页码保留最后一个出现的容器
        page_dict = {}
        for page_num, container in containers:
            page_dict[page_num] = container

        # 按页码排序
        sorted_pages = sorted(page_dict.items())

        # 找到最长的连续有序子序列
        if not sorted_pages:
            return []

        # 从第一个页码开始找最长连续序列
        longest_sequence = []
        current_sequence = [sorted_pages[0]]

        for i in range(1, len(sorted_pages)):
            current_page, current_container = sorted_pages[i]
            prev_page, prev_container = current_sequence[-1]

            # 如果当前页码是连续的，加入当前序列
            if current_page == prev_page + 1:
                current_sequence.append((current_page, current_container))
            else:
                # 如果不连续，检查当前序列是否更长
                if len(current_sequence) > len(longest_sequence):
                    longest_sequence = current_sequence[:]
                # 开始新的序列
                current_sequence = [(current_page, current_container)]

        # 检查最后一个序列
        if len(current_sequence) > len(longest_sequence):
            longest_sequence = current_sequence[:]

        # 如果没有找到合适的序列，返回所有去重后的页码
        if len(longest_sequence) < 2:
            return sorted_pages

        return longest_sequence


if __name__ == "__main__":
    # 测试ContainerPageSplitter
    # html_file_path = "/Users/chenghao.zhang/Documents/secfile/edgar/0000908311-25-000017.html"
    html_file_path = "/Users/chenghao.zhang/Documents/secfile/extract_financial/test.html"

    try:
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        print("=== ContainerPageSplitter 测试 ===")
        print(f"测试文件: {html_file_path}")
        print(f"文件大小: {len(html_content):,} 字符\n")

        # 测试 Container 分割器
        print("测试 Container 分割器:")
        container_splitter = ContainerPageSplitter()
        container_results, soup = container_splitter.extract_page_numbers(
            html_content
        )

        print(f"   找到页码: {container_results.page_numbers}")
        print(f"   总页码数: {len(container_results.page_numbers)}")
        print(f"   页面内容数: {len(container_results.page_contents)}")
        # container_results.page_contents[1].text_content


    except FileNotFoundError:
        print(f"错误: 找不到文件 {html_file_path}")
        print("请确保HTML文件存在")
    except Exception as e:
        print(f"测试过程中发生错误: {e}")
        import traceback

        traceback.print_exc()

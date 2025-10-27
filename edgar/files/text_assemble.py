import time
import logging
import os
from typing import List, Dict, Tuple, Any, Optional, Set

try:
    from bs4 import BeautifulSoup, Tag  # type: ignore

    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    Tag = None

from edgar.files.html_documents import (
    HtmlDocument,
    clean_html_root,
    decompose_page_numbers,
)

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
from edgar.files.timeout_utils import monitor_performance


def time_section(section_name: str):
    """代码段时间测量上下文管理器"""

    class TimingContext:
        def __init__(self, name):
            self.name = name
            self.start_time = None

        def __enter__(self):
            self.start_time = time.perf_counter()
            logging.debug(f"[TIMING] {self.name} - 开始")
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            end_time = time.perf_counter()
            duration = end_time - self.start_time
            if exc_type is None:
                logging.info(f"[TIMING] {self.name} - 完成: {duration:.3f}s")
            else:
                logging.warning(
                    f"[TIMING] {self.name} - 异常退出: {duration:.3f}s"
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
    """Simplified text assembly utilities for HTML document processing"""

    # 定义一次性的忽略标签集合
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
        """检查元素是否为有效内容元素"""
        return (
            hasattr(element, "name")
            and element.name
            and element.name not in AssembleText.IGNORE_TAGS
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
            
        # Parse HTML content - 使用缓存避免重复解析
        root: Tag = HtmlDocument.get_root(html_content)
        start_element = clean_html_root(root)
        decompose_page_numbers(start_element)
        soup = start_element

        if not soup:
            logging.error("Failed to parse HTML content")
            return {}

        # 预先分配足够大小的结果字典
        content_by_link = {}

        # 预处理 item_links 为更高效的格式
        with time_section("process_item_links"):
            # 使用集合去重
            link_points = set()
            for item in item_links:
                if not isinstance(item, (tuple, list)) or len(item) < 2:
                    continue

                item_name = item[0]
                item_id_list = item[1]

                # 统一处理为列表
                if isinstance(item_id_list, str):
                    item_id_list = [item_id_list]
                elif not isinstance(item_id_list, list):
                    continue

                # 添加每个链接点及其名称
                for item_id in item_id_list:
                    if item_id and item_name:
                        # 使用元组以便在集合中去重
                        link_points.add((item_name, item_id))

            # 转换回列表以便后续处理
            link_points = list(link_points)

        # 一次性查找所有元素
        with time_section("find_all_elements"):
            # 使用字典缓存元素查找结果
            id_elements = {}
            name_elements = {}

            # 优化：使用CSS选择器一次性查找所有ID元素
            for elem in soup.select("[id]"):
                elem_id = elem.get("id")
                if elem_id:
                    id_elements[elem_id] = elem

            # 优化：使用CSS选择器一次性查找所有name属性元素
            for elem in soup.select("a[name]"):
                elem_name = elem.get("name")
                if elem_name:
                    name_elements[elem_name] = elem

        with time_section("create_ordered_links"):
            # 创建基于文档位置的有序链接点列表
            ordered_links = []

            # 预分配足够大小的列表
            ordered_links = []

            for name, link_id in link_points:
                # 查找元素（通过ID或name）
                element = id_elements.get(link_id) or name_elements.get(
                    link_id
                )
                if element:
                    ordered_links.append((name, link_id, element))

            # 优化：使用更高效的位置估算方法
            if ordered_links:
                # 创建一个元素到位置的映射，避免重复计算
                element_positions = {}
                html_str = str(soup)

                for name, link_id, element in ordered_links:
                    # 使用元素的字符串表示在HTML中的位置作为排序依据
                    elem_str = str(element)
                    pos = html_str.find(elem_str)
                    if pos >= 0:
                        element_positions[(name, link_id, element)] = pos
                    else:
                        element_positions[(name, link_id, element)] = float(
                            "inf"
                        )

                # 根据位置排序
                ordered_links.sort(
                    key=lambda x: element_positions.get(x, float("inf"))
                )

        with time_section("extract_content_by_links"):
            # 提取链接点之间的内容
            for i, (name, link_id, element) in enumerate(ordered_links):
                # 创建内容的元组键
                # key = ('extracted', name)
                if isinstance(name, (list, tuple)):
                    key = tuple(name)
                else:
                    key = name

                # 确定本节的边界
                start_element = element
                end_element = (
                    ordered_links[i + 1][2]
                    if i + 1 < len(ordered_links)
                    else None
                )

                # 提取起始和结束元素之间的元素
                section_elements = []
                current = start_element

                # 优化：使用固定大小的缓冲区
                section_elements = []

                # 使用更高效的元素遍历方法，并防止重复元素
                processed_elements = set()  # 用于跟踪已处理的元素ID
                elements_to_process = [start_element]  # 临时存储需要处理的元素

                # 第一阶段：收集所有可能的内容元素（不包含 end_link 本身）
                while current and current != end_element:
                    # 只包含有意义的内容元素
                    if AssembleText.is_content_element(current):
                        # 获取元素的文本内容
                        elem_content = (
                            current.get_text().strip()
                            if hasattr(current, "get_text")
                            else str(current).strip()
                        )

                        # 只有当内容不为空时才考虑添加
                        if (
                            elem_content
                            and id(current) not in processed_elements
                        ):
                            elements_to_process.append(current)
                            processed_elements.add(id(current))

                    # 移动到下一个元素
                    next_elem = current.next_element
                    if not next_elem or next_elem == end_element:
                        break
                    current = next_elem

                # 第二阶段：过滤掉已经被其祖先元素或起始链接包含的元素
                processed_elements.clear()  # 重置已处理元素集合

                # 按照元素在文档中的位置排序（从上到下）
                for element in elements_to_process:
                    # 检查元素是否已被处理
                    if id(element) in processed_elements:
                        continue

                    # 检查当前元素的子元素，将它们标记为已处理
                    for child in element.descendants:
                        processed_elements.add(id(child))

                    # 将当前元素添加到结果中
                    section_elements.append(element)
                    processed_elements.add(id(element))

                # 添加或扩展此键的元素
                if key in content_by_link:
                    content_by_link[key].extend(section_elements)
                else:
                    content_by_link[key] = section_elements

            # 处理介绍内容（第一个链接之前）
            if ordered_links:
                first_element = ordered_links[0][2]
                intro_elements = []

                # 优化：直接使用CSS选择器查找body
                body = soup.find("body") or soup

                if body:
                    # 使用更高效的元素遍历
                    current = body

                    # 使用迭代器而不是递归遍历，并防止重复元素
                    processed_intro_elements = set()  # 用于跟踪已处理的元素ID
                    elements_to_process = []  # 临时存储需要处理的元素

                    # 第一阶段：收集所有可能的内容元素
                    for element in body.descendants:
                        if element == first_element:
                            break

                        # 只添加有意义的内容元素
                        if AssembleText.is_content_element(element):
                            # 获取元素的文本内容
                            elem_content = (
                                element.get_text().strip()
                                if hasattr(element, "get_text")
                                else str(element).strip()
                            )

                            # 只有当内容不为空时才考虑添加
                            if (
                                elem_content
                                and id(element) not in processed_intro_elements
                            ):
                                elements_to_process.append(element)
                                processed_intro_elements.add(id(element))

                    # 第二阶段：过滤掉已经被其祖先元素包含的元素
                    processed_intro_elements.clear()  # 重置已处理元素集合

                    for element in elements_to_process:
                        # 检查元素是否已被处理
                        if id(element) in processed_intro_elements:
                            continue

                        # 检查当前元素的子元素，将它们标记为已处理
                        for child in element.descendants:
                            processed_intro_elements.add(id(child))

                        # 将当前元素添加到结果中
                        intro_elements.append(element)
                        processed_intro_elements.add(id(element))

                if intro_elements:
                    content_by_link[("extracted", "Item 0")] = intro_elements

        results = {}
        for key, value in content_by_link.items():
            results[key] = AssembleText.assemble_html_document(value)
    
        if not any("signature" in str(key).lower() for key in content_by_link.keys()):
            last_item = ordered_links[-1]
            last_item_name = last_item[0] if isinstance(last_item, tuple) else last_item[0]
            last_content = results.get(last_item_name, "")
            if last_content:
                sig_key = ["SIGNATURES", "SIGNATURE"]
                content_lines = last_content.split("\n")
                signature_line_index = None
                
                # Optimization: limit search scope
                search_lines = content_lines[-100:] if len(content_lines) > 100 else content_lines
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

        return results

    @staticmethod
    def splite_f_footer_page(html_content):
        """
        将页脚为F-/d的页面单独取出，后续进行处理
        并返回取出单独内容的后的html_content
        """
        soup = BeautifulSoup(html_content, "html.parser")
        footer = soup.find("footer")
        if footer and footer.get_text().strip().upper() == "F-/d":
            return footer.extract(), html_content.replace(str(footer), "")
        return None, html_content


if __name__ == "__main__":
    # 设置日志级别以显示性能监控信息
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

        # 执行性能测试
        result = AssembleText.assemble_items(html_content, item_links)
        print(f"处理完成，共生成 {len(result)} 个项目")

        # result[('extracted', ('extracted', 'Executive Summary'))]
        for key, value in result.items():
            print(f"项目: {key}, 内容长度: {len(value)} 字符")
            print(AssembleText.assemble_html_document(value))

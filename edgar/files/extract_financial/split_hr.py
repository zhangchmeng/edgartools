from bs4 import BeautifulSoup
from edgar.files.extract_financial.base import (
    BasePageSplitter,
    PageContent,
    PageNumberExtractionResult,
)


class HRPageSplitter(BasePageSplitter):
    """基于HR标签的页码分割器"""

    def extract_page_numbers(
        self, html_content: str
    ):
        """
        查找 hr 标签作为页码分割线，并提取所有F-number页码的完整内容

        Args:
            html_content: HTML内容字符串

        Returns:
            包含每个页码对应的soup片段和element内容的字典
        """
        soup = BeautifulSoup(html_content, "html.parser")
        page_numbers = []
        page_contents = {}

        # 获取所有HR标签
        hr_tags = soup.find_all("hr")

        # 记录前一个有效的页码分割线
        prev_valid_hr = None
        prev_hr = None
        prev_number = None

        # 遍历所有HR标签，提取页面内容
        for hr in hr_tags:
            # 查找HR标签前面的页码信息
            page_number = self._find_page_number_before_hr(hr)

            if page_number:
                if page_number > prev_number:
                    page_elements = self._extract_page_content_between_hrs(
                        soup, prev_valid_hr or prev_hr, hr
                    )
                elif page_number == prev_number:
                    page_elements = self._extract_page_content_between_hrs(
                        soup, prev_hr, hr
                    )
                elif page_number < prev_number:
                    page_elements = self._extract_page_content_between_hrs(
                        soup, prev_valid_hr or prev_hr, hr
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

                # 更新前一个有效的页码分割线
                prev_valid_hr = hr
                prev_number = page_number
            prev_hr = hr

        # 处理最后一个HR标签之后的剩余内容
        if prev_valid_hr:
            self._process_remaining_content(
                soup, prev_valid_hr, page_numbers, page_contents
            )

        return self._create_standard_result(page_numbers, page_contents), soup

    def _find_page_number_before_hr(self, hr):
        """在HR标签前查找页码 - 仅检查第一个非空元素"""
        prev_element = getattr(hr, "previous_element", None)

        # 找到第一个非空元素
        while prev_element:
            text = self._extract_text_content(prev_element)
            if text:  # 找到第一个非空元素
                # 使用基类的F-number模式搜索方法
                page_numbers = self._search_f_number_pattern(text)
                if page_numbers:
                    return str(page_numbers[0])
                else:
                    if getattr(prev_element, "parent", None):
                        page_numbers = self._search_f_number_pattern(self._extract_text_content(prev_element.parent.parent))
                        if page_numbers:
                            return str(page_numbers[0])
                    return None  # 第一个非空元素不匹配就直接返回None
            # 使用文档序的上一个元素（可跨父级，可能跳出当前元素到上一个父元素的最底部）
            prev_element = getattr(prev_element, "previous_element", None)

        return None

    def _extract_page_content_between_hrs(self, soup, prev_hr, current_hr):
        """提取前一个HR标签到当前HR标签之间的完整内容作为页面内容，并从soup中删除已提取的元素"""
        page_elements = []
        # 确定起始元素（顺序已保证正确，逐个遍历并删除）
        if prev_hr is None:
            start = soup.body if getattr(soup, "body", None) else soup
            current_element = getattr(start, "next_element", None)
        else:
            current_element = getattr(prev_hr, "next_element", None)

        # 提取元素直到当前HR
        current_element.next_element
        while current_element is not None and current_element is not current_hr:
            # 跳过 current_hr 的祖先（不删除容器，进入其子树）
            # 计算删除后的下一个元素：优先使用当前节点的 next_sibling，其次向上回溯查找祖先的 next_sibling
            ns = getattr(current_element, "next_sibling", None)
            if ns is None:
                parent = getattr(current_element, "parent", None)
                while parent is not None:
                    ns = getattr(parent, "next_sibling", None)
                    if ns is not None:
                        break
                    parent = getattr(parent, "parent", None)

            page_elements.append(current_element.extract())

            # 进入下一个候选元素（跨越已删除子树)
            current_element = ns
        return page_elements

    def _process_remaining_content(
        self, soup, last_hr, page_numbers, page_contents
    ):
        """处理最后一个HR标签之后的剩余内容"""
        # 提取最后一个HR标签之后的所有内容
        page_elements = self._extract_page_content_between_hrs(
            soup, last_hr, None
        )

        if page_elements:
            # 在提取的元素中查找页码
            remaining_page_number = self._find_page_number_in_elements(
                page_elements
            )

            # 如果找到页码，创建页面内容对象
            if remaining_page_number:
                page_content = self._create_page_content(
                    remaining_page_number, page_elements
                )
                page_contents[remaining_page_number] = page_content

                # 添加到页码列表
                if remaining_page_number not in page_numbers:
                    page_numbers.append(remaining_page_number)

    def _find_page_number_in_elements(self, elements):
        """在元素列表中查找页码"""
        for element in elements:
            text = self._extract_text_content(element)
            if text:
                # 使用基类的F-number模式搜索方法
                page_numbers = self._search_f_number_pattern(text)
                if page_numbers:
                    return str(page_numbers[0])
        return None

    def _create_page_content(self, page_number, elements):
        """创建页面内容对象"""
        # 创建页面soup
        page_soup = BeautifulSoup("", "html.parser")
        page_body = page_soup.new_tag("body")
        page_soup.append(page_body)

        # 批量添加元素，避免逐个extract调用
        for element in elements:
            if hasattr(element, "parent") and element.parent:
                page_body.append(element.extract())
            else:
                page_body.append(element)

        # 调用页码拆分和清理方法
        # separators, cleaned_soup = self._extract_page_separators(page_soup, page_number)

        # 创建页面内容对象
        return PageContent(
            page_number=page_number,
            page_soup=page_soup,
            text_content=page_soup.get_text(),
            page_separators=elements,
            # cleaned_soup=cleaned_soup,
        )

if __name__ == "__main__":
    # 测试页码提取功能
    html_file_path = "/Users/chenghao.zhang/Documents/secfile/edgar/0000908311-25-000017.html"
    # html_file_path = "/Users/chenghao.zhang/Documents/secfile/edgar/000149315225017715.html"
    try:
        # 读取HTML文件
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        print("=== 重构后的页码提取测试 ===")
        print(f"测试文件: {html_file_path}")
        print(f"文件大小: {len(html_content):,} 字符\n")

        # 测试各个分割器类
        print("1. 测试 HR 分割器:")
        hr_splitter = HRPageSplitter()
        hr_results, soup = hr_splitter.extract_page_numbers(html_content)
        print(f"   找到页码: {hr_results.page_numbers}")
        print(f"   总页码数: {len(hr_results.page_numbers)}")
        print(f"   页面内容数: {len(hr_results.page_contents)}")

    except FileNotFoundError:
        print(f"错误: 找不到文件 {html_file_path}")
        print("请确保HTML文件存在")
    except Exception as e:
        print(f"测试过程中发生错误: {e}")
        import traceback

        traceback.print_exc()

import re
from bs4 import BeautifulSoup

from edgar.files.extract_financial.base import (
    BasePageSplitter,
    PageContent,
    PageNumberExtractionResult,
)


class StylePageSplitter(BasePageSplitter):
    """基于CSS样式的页码分割器"""

    def extract_page_numbers(
        self, html_content: str
    ) -> PageNumberExtractionResult:
        """
        通过CSS样式查找页码

        Args:
            html_content: HTML内容字符串

        Returns:
            PageNumberExtractionResult: 结构化的页码提取结果
        """
        soup = BeautifulSoup(html_content, "html.parser")
        page_numbers = []
        page_contents = {}

        # 查找具有特定样式的元素
        style_elements = self._find_style_elements(soup)

        # 查找class名包含page的元素
        class_elements = self._find_page_class_elements(soup)

        # 合并所有候选元素
        all_elements = style_elements + class_elements

        # 处理所有候选元素
        for element in all_elements:
            text_content = self._extract_text_content(element)
            f_numbers = self._search_f_number_pattern(text_content)

            for page_num in f_numbers:
                if page_num not in page_contents:
                    page_numbers.append(page_num)

                    # 创建页面内容对象
                    page_content = self._create_page_content(
                        page_num, [element]
                    )
                    page_contents[page_num] = page_content

        return (
            self._create_standard_result(sorted(page_numbers), page_contents),
            soup,
        )

    def _find_style_elements(self, soup):
        """查找具有特定样式的元素"""
        style_elements = []

        # 常见的页码样式特征
        style_patterns = [
            r"text-align\s*:\s*center",
            r"text-align\s*:\s*right",
            r"position\s*:\s*absolute",
            r"bottom\s*:\s*\d+",
            r"page-break",
        ]

        for pattern in style_patterns:
            elements = soup.find_all(
                attrs={"style": re.compile(pattern, re.IGNORECASE)}
            )
            style_elements.extend(elements)

        return style_elements

    def _find_page_class_elements(self, soup):
        """查找class名包含page的元素"""
        return soup.find_all(
            attrs={"class": re.compile(r"page", re.IGNORECASE)}
        )

    def _create_page_content(self, page_number, elements):
        """创建页面内容对象"""
        # 创建页面soup
        page_soup = BeautifulSoup("", "html.parser")

        # 复制元素到页面soup中
        for element in elements:
            if hasattr(element, "name"):
                # 复制元素而不是移动
                element_copy = BeautifulSoup(str(element), "html.parser")
                page_soup.append(element_copy)
            else:
                # 处理文本节点
                page_soup.append(str(element))

        # 提取文本内容
        text_content = page_soup.get_text().strip()

        # 创建页面内容对象
        return PageContent(
            page_number=page_number,
            page_soup=page_soup,
            text_content=text_content,
            page_separators=[],
        )


if __name__ == "__main__":
    # 测试页码提取功能
    html_file_path = "/Users/chenghao.zhang/Documents/secfile/edgar/0000908311-25-000017.html"
    try:
        # 读取HTML文件
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        print("=== 重构后的页码提取测试 ===")
        print(f"测试文件: {html_file_path}")
        print(f"文件大小: {len(html_content):,} 字符\n")

        # 测试各个分割器类
        print("1. 测试样式分割器:")
        style_splitter = StylePageSplitter()
        style_results, soup = style_splitter.extract_page_numbers(html_content)
        print(f"   找到页码: {style_results.page_numbers}")
        print(f"   总页码数: {len(style_results.page_numbers)}")
        print(f"   页面内容数: {len(style_results.page_contents)}")

    except FileNotFoundError:
        print(f"错误: 找不到文件 {html_file_path}")
        print("请确保HTML文件存在")
    except Exception as e:
        print(f"测试过程中发生错误: {e}")
        import traceback

        traceback.print_exc()

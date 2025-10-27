from bs4 import BeautifulSoup, NavigableString
import re
from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, Field
from abc import ABC, abstractmethod


# 简化的Pydantic模型定义
class PageContent(BaseModel):
    """F-number页面内容的简化模型"""

    page_number: int = Field(..., description="页码数字")
    page_soup: Any = Field(..., description="页面的BeautifulSoup对象")
    text_content: str = Field("", description="页面文本内容")
    page_separators: List[Any] = Field(
        default_factory=list, description="页面分隔符元素列表"
    )

    class Config:
        arbitrary_types_allowed = True


class PageNumberExtractionResult(BaseModel):
    """页码提取结果的简化模型"""

    page_numbers: List[int] = Field(
        default_factory=list, description="页码列表"
    )
    page_contents: Dict[int, PageContent] = Field(
        default_factory=dict, description="页面内容字典"
    )

    class Config:
        arbitrary_types_allowed = True


class BasePageSplitter(ABC):
    """
    页码分割器基础抽象类
    定义所有页码分割器的通用接口和方法
    """

    @abstractmethod
    def extract_page_numbers(self, html_content: str):
        """
        提取页码的抽象方法，子类必须实现
        
        Args:
            html_content: HTML内容字符串
            
        Returns:
            PageNumberExtractionResult: 页码提取结果
        """
        pass

    @staticmethod
    def _extract_text_content(element):
        """
        通用的文本内容提取方法

        Args:
            element: HTML元素或NavigableString

        Returns:
            提取的文本内容
        """
        if hasattr(element, "get_text"):
            return element.get_text().strip()
        elif isinstance(element, NavigableString):
            return str(element).strip()
        return ""

    @staticmethod
    def _search_f_number_pattern(text):
        """
        通用的F-number模式搜索方法

        Args:
            text: 要搜索的文本

        Returns:
            找到的页码数字列表
        """

        # F- 10
        patterns = [
            r"^F-(\d+)",  # 基本F-数字模式
            r"^Page\s*F-(\d+)",  # Page F-数字模式
            r"^\s*F-(\d+)\s*$",  # 严格的F-数字模式
            r"^F-(\d+)\s*$",  # 以F-数字结尾
            r"^\s*F-(\d+)",  # 以F-数字开头
            r"^F-\s*(\d+)",  # 处理F- 10等中间有空格的情况
            r"^\s*-\s*F-(\d+)\s*-\s*$",  # 处理- F-18 -样式
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                return [int(match) for match in matches]
        return []


    @staticmethod
    def _create_standard_result(page_numbers, page_contents):
        """
        创建标准化的结果格式

        Args:
            page_numbers: 页码列表
            page_contents: 页面内容字典

        Returns:
            PageNumberExtractionResult对象
        """
        page_count = 0
        for pc in page_contents.values():
            if hasattr(pc, "text_content") and len(pc.text_content) > 100:
                page_count += 1
        if page_count <= 5:
            page_numbers = []
            page_contents = {}
        if not page_numbers:
            return PageNumberExtractionResult(
                page_numbers=[], page_contents={}
            )

        # 页面内容已经是PageContent对象，直接使用
        pydantic_page_contents = page_contents

        # 创建结果对象
        result = PageNumberExtractionResult(
            page_numbers=sorted(list(set(page_numbers))),
            page_contents=pydantic_page_contents,
        )

        return result


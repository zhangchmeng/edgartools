from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from edgar.files.extract_financial.split_container import ContainerPageSplitter
from edgar.files.extract_financial.split_hr import HRPageSplitter
from edgar.files.extract_financial.split_style import StylePageSplitter
from edgar.files.extract_financial.base import PageContent


class FinancialStatementExtractionResult(BaseModel):
    """财务报表提取结果模型"""

    success: bool = Field(False, description="是否成功提取")
    method: Optional[str] = Field(None, description="使用的分割方法")
    page_numbers: List[int] = Field(default_factory=list, description="页码列表")
    page_contents: Dict[int, PageContent] = Field(
        default_factory=dict, description="页面内容字典"
    )
    page_contents_elements: List[BeautifulSoup] = Field(
        default_factory=list, description="页面内容元素列表"
    )
    total_pages: int = Field(0, description="总页数")
    soup: Optional[BeautifulSoup] = Field(None, description="BeautifulSoup对象")
    error: Optional[str] = Field(None, description="错误信息")

    class Config:
        arbitrary_types_allowed = True



def extract_financial_statement(html_content: str) -> FinancialStatementExtractionResult:
    """
    从HTML内容中提取财务报表, 先后尝试HRPageSplitter ContainerPageSplitter StylePageSplitter 方法
    
    Args:
        html_content (str): HTML内容
        
    Returns:
        FinancialStatementExtractionResult: 结构化的财务报表提取结果
    """
    # 输入验证
    if not html_content or not isinstance(html_content, str):
        return _create_error_result("无效的HTML内容输入")
    
    # 定义分割器列表，按优先级排序
    splitters = [
        ("HRPageSplitter", HRPageSplitter()),
        ("ContainerPageSplitter", ContainerPageSplitter()),
        ("StylePageSplitter", StylePageSplitter())
    ]
    
    # 记录每个分割器的结果
    results = {}
    
    for splitter_name, splitter in splitters:
        try:
            # 尝试使用当前分割器提取页码
            extraction_result, soup = splitter.extract_page_numbers(html_content)
            
            # 检查提取结果的质量
            page_count = len(extraction_result.page_numbers)
            # 记录结果
            results[splitter_name] = {
                "success": page_count > 0,
                "page_count": page_count,
                "page_numbers": extraction_result.page_numbers,
                "page_contents": extraction_result.page_contents,
                "soup": soup
            }
            
            # 如果找到了页码，返回结果
            if page_count > 0:
                return _create_success_result(
                    method=splitter_name,
                    page_numbers=extraction_result.page_numbers,
                    page_contents=extraction_result.page_contents,
                    page_contents_elements=[page_content.page_soup for page_content in extraction_result.page_contents.values()],
                    total_pages=page_count,
                    soup=soup
                )
                
        except Exception as e:
            # 记录错误但继续尝试下一个分割器
            results[splitter_name] = {
                "success": False,
                "error": str(e),
                "page_count": 0
            }
            continue
    
    # 如果所有分割器都失败了，返回失败结果
    return _create_error_result(f"所有分割器都未能成功提取页码。尝试结果: {results}")



def _create_success_result(method: str, page_numbers: List[int], page_contents: Dict[int, PageContent], page_contents_elements: List[BeautifulSoup], total_pages: int, soup: BeautifulSoup) -> FinancialStatementExtractionResult:
    """创建成功结果"""
    return FinancialStatementExtractionResult(
        success=True,
        method=method,
        page_numbers=page_numbers,
        page_contents=page_contents,
        page_contents_elements=page_contents_elements,
        total_pages=total_pages,
        soup=soup,
        error=None,
    )



def _create_error_result(error_message: str) -> FinancialStatementExtractionResult:
    """创建错误结果"""
    return FinancialStatementExtractionResult(
        success=False,
        method=None,
        page_numbers=[],
        page_contents={},
        page_contents_elements=[],
        total_pages=0,
        soup=None,
        error=error_message,
    )


# 测试函数
if __name__ == "__main__":
    # 测试extract_financial_statement函数
    # html_file_path = "/Users/chenghao.zhang/Documents/secfile/edgar/0000908311-25-000017.html"
    html_file_path = "/Users/chenghao.zhang/Documents/secfile/edgar/000164117225023248.html"
    
    try:
        # 读取HTML文件
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        print("=== extract_financial_statement 函数测试 ===")
        print(f"测试文件: {html_file_path}")
        print(f"文件大小: {len(html_content):,} 字符\n")
        
        # 测试函数
        result = extract_financial_statement(html_content)
        
        print("提取结果:")
        print(f"  成功: {result.success}")
        print(f"  使用方法: {result.method}")
        print(f"  总页数: {result.total_pages}")
        print(f"  页码列表: {result.page_numbers}")
        print(f"  错误信息: {result.error}")
        
        if result.success:
            print(f"  页面内容数量: {len(result.page_contents)}")
            # 显示前几个页面的内容摘要
            for i, (page_num, content) in enumerate(list(result.page_contents.items())[:3]):
                text_preview = content.text_content[:100] + "..." if len(content.text_content) > 100 else content.text_content
                print(f"  页面 {page_num} 内容预览: {text_preview}")
        
    except FileNotFoundError:
        print(f"错误: 找不到文件 {html_file_path}")
        print("请确保HTML文件存在")
    except Exception as e:
        print(f"测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()


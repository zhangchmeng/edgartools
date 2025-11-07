from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from edgar.files.extract_financial.split_container import ContainerPageSplitter
from edgar.files.extract_financial.split_hr import HRPageSplitter
from edgar.files.extract_financial.split_style import StylePageSplitter
from edgar.files.extract_financial.base import PageContent


class FinancialStatementExtractionResult(BaseModel):
    """Financial statement extraction result model"""

    success: bool = Field(False, description="Whether extraction succeeded")
    method: Optional[str] = Field(None, description="Splitting method used")
    page_numbers: List[int] = Field(default_factory=list, description="List of page numbers")
    page_contents: Dict[int, PageContent] = Field(
        default_factory=dict, description="Page content dictionary"
    )
    page_contents_elements: List[BeautifulSoup] = Field(
        default_factory=list, description="List of page content elements"
    )
    total_pages: int = Field(0, description="Total pages")
    soup: Optional[BeautifulSoup] = Field(None, description="BeautifulSoup object")
    error: Optional[str] = Field(None, description="Error message")

    class Config:
        arbitrary_types_allowed = True



def extract_financial_statement(html_content: str) -> FinancialStatementExtractionResult:
    """
    Extract financial statements from HTML content, trying HRPageSplitter,
    ContainerPageSplitter, then StylePageSplitter in order.

    Args:
        html_content (str): HTML content

    Returns:
        FinancialStatementExtractionResult: Structured extraction result
    """
    # Input validation
    if not html_content or not isinstance(html_content, str):
        return _create_error_result("无效的HTML内容输入")
    
    # Define splitters in priority order
    splitters = [
        ("HRPageSplitter", HRPageSplitter()),
        ("ContainerPageSplitter", ContainerPageSplitter()),
        ("StylePageSplitter", StylePageSplitter())
    ]
    
    # Record each splitter's result
    results = {}
    
    for splitter_name, splitter in splitters:
        try:
            # Try the current splitter to extract page numbers
            extraction_result, soup = splitter.extract_page_numbers(html_content)
            
            # Check result quality
            page_count = len(extraction_result.page_numbers)
            # Record the result
            results[splitter_name] = {
                "success": page_count > 0,
                "page_count": page_count,
                "page_numbers": extraction_result.page_numbers,
                "page_contents": extraction_result.page_contents,
                "soup": soup
            }
    
            # If page numbers were found, return the result
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
            # Record the error but continue to the next splitter
            results[splitter_name] = {
                "success": False,
                "error": str(e),
                "page_count": 0
            }
            continue
    
    # If all splitters fail, return an error result
    return _create_error_result(f"所有分割器都未能成功提取页码。尝试结果: {results}")



def _create_success_result(method: str, page_numbers: List[int], page_contents: Dict[int, PageContent], page_contents_elements: List[BeautifulSoup], total_pages: int, soup: BeautifulSoup) -> FinancialStatementExtractionResult:
    """Create a success result"""
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
    """Create an error result"""
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


# Test函数
if __name__ == "__main__":
    # Testextract_financial_statement函数
    # html_file_path = "/Users/chenghao.zhang/Documents/secfile/edgar/0000908311-25-000017.html"
    html_file_path = "/Users/chenghao.zhang/Documents/secfile/extract_financial/test.html"
    
    try:
        # Read HTML file
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        print("=== extract_financial_statement Function Test ===")
        print(f"Test file: {html_file_path}")
        print(f"file size: {len(html_content):,} characters\n")
        
        # Test function
        result = extract_financial_statement(html_content)
        
        print("Extraction result:")
        print(f"  success: {result.success}")
        print(f"  method used: {result.method}")
        print(f"  total pages: {result.total_pages}")
        print(f"  page number list: {result.page_numbers}")
        print(f"  error message: {result.error}")
        
        if result.success:
            print(f"  page content count: {len(result.page_contents)}")
            # Show summary of first few pages
            for i, (page_num, content) in enumerate(list(result.page_contents.items())[:3]):
                text_preview = content.text_content[:100] + "..." if len(content.text_content) > 100 else content.text_content
                print(f"  page {page_num} content preview: {text_preview}")
        
    except FileNotFoundError:
        print(f"Error: File not found {html_file_path}")
        print("Please ensure the HTML file exists")
    except Exception as e:
        print(f"Error occurred during test: {e}")
        import traceback
        traceback.print_exc()


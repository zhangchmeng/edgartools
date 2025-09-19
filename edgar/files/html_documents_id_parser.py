# HTML Documents ID Parser - Refactored
# This file has been refactored to split functionality into separate modules
# Standard library imports

# Third-party imports
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# Import refactored modules
from edgar.files.timeout_utils import TimeoutException, monitor_performance
from edgar.files.text_assembler import AssembleText
from edgar.files.base_parser import BaseHtmlParser
from edgar.files.document_parsers import ParsedHtml10K, ParsedHtml10Q

# Re-export classes for backward compatibility
__all__ = [
    "TimeoutException",
    "monitor_performance",
    "AssembleText",
    "BaseHtmlParser",
    "ParsedHtml10K",
    "ParsedHtml10Q",
    "check_item_result",
    "test_10_k_processing",
    "test_10_q_processing",
]


def check_item_result(result):
    # Count all items
    total_items = 0
    empty_items = 0
    for part_name, part_content in result.items():
        # if part_name == "extracted":
        #     continue
        print(f"\nChecking content of {part_name}:")
        if isinstance(part_content, dict):
            part_items = len(part_content)
            total_items += part_items
            print(f"{part_name} contains {part_items} items")

            for item_name, item_content in part_content.items():
                content_length = len(item_content) if item_content else 0
                if content_length == 0:
                    empty_items += 1
                    print(f"Warning: {item_name} in {part_name} is empty")
                else:
                    print(f"{item_name}: {content_length} characters")
        else:
            print(f"Warning: {part_name} is not in dictionary format")
    print(f"\nSummary:")
    print(f"Total {total_items} items found")
    if empty_items > 0:
        print(f"Among them, {empty_items} items are empty")


def test_10_k_processing():
    """Check and confirm that normal type files can be parsed successfully"""
    from edgar import set_identity, get_by_accession_number
    from edgar.company_reports import TenK

    set_identity("1334307071@qq.com")

    # Test parsing Apple's 10-K filing
    accession_number = "0000320193-24-000123"
    filing = get_by_accession_number(accession_number)
    result = ParsedHtml10K().extract_html(
        filing.html(), TenK.structure, markdown=True
    )

    # Verify Part I content length
    assert 15660 < len(result["part i"]["item 1"]) < 15760
    assert 68734 < len(result["part i"]["item 1a"]) < 68834
    assert 2688 < len(result["part i"]["item 1c"]) < 2788
    assert 436 < len(result["part i"]["item 2"]) < 536
    assert 4291 < len(result["part i"]["item 3"]) < 4391

    # Verify Part II content length
    assert 4779 < len(result["part ii"]["item 5"]) < 4879
    assert 18274 < len(result["part ii"]["item 7"]) < 18374
    assert 3217 < len(result["part ii"]["item 7a"]) < 3317
    assert 102697 < len(result["part ii"]["item 8"]) < 102797
    assert 4450 < len(result["part ii"]["item 9a"]) < 4550
    assert 1294 < len(result["part ii"]["item 9b"]) < 1394

    # Verify Part III content length
    assert 983 < len(result["part iii"]["item 10"]) < 1083
    assert 181 < len(result["part iii"]["item 12"]) < 281
    assert 160 < len(result["part iii"]["item 13"]) < 260

    # Verify Part IV content length
    assert 30483 < len(result["part iv"]["item 15"]) < 30583

    check_item_result(result)

    # Test multi-section merging case
    accession_number = "0001601712-25-000044"
    filing = get_by_accession_number(accession_number)
    result = ParsedHtml10K().extract_html(
        filing.html(), TenK.structure, markdown=True
    )

    # Verify merged content length
    assert 144591 < len(result["part i"]["item 1"]) < 144691
    assert 135437 < len(result["part i"]["item 1a"]) < 135537
    assert 6887 < len(result["part i"]["item 1c"]) < 6987
    assert 1621 < len(result["part i"]["item 2"]) < 1721
    assert 3116 < len(result["part i"]["item 3"]) < 3216

    assert 5911 < len(result["part ii"]["item 5"]) < 6011
    assert 130239 < len(result["part ii"]["item 7"]) < 130339
    assert 8240 < len(result["part ii"]["item 7a"]) < 8340

    assert 223000 < len(result["part ii"]["item 8"]) < 224000
    assert 4021 < len(result["part ii"]["item 9a"]) < 4121
    assert 3693 < len(result["part ii"]["item 9b"]) < 3793

    assert 3693 < len(result["part iii"]["item 10"]) < 3793
    assert 76668 < len(result["part iv"]["item 15"]) < 76768

    check_item_result(result)
    accession_number = "0000726601-25-000013"
    # 0000831001-25-000131 has no table in content, parse TABLE OF CONTENTS div to determine if it's a table of contents
    # TODO


def test_10_q_processing():
    """Check and confirm that normal type files can be parsed successfully"""
    from edgar import set_identity, get_by_accession_number
    from edgar.company_reports import TenQ

    set_identity("1334307071@qq.com")

    accession_number = "0000320193-25-000073"
    filing = get_by_accession_number(accession_number)
    result = ParsedHtml10Q().extract_html(
        filing.html(), TenQ.structure, markdown=True
    )

    # Check Part I content
    assert (
        58525 < len(result["part i"]["item 1"]) < 58725
    ), "Item 1 length not within expected range"
    assert (
        24477 < len(result["part i"]["item 2"]) < 24677
    ), "Item 2 length not within expected range"
    assert (
        290 < len(result["part i"]["item 3"]) < 490
    ), "Item 3 length not within expected range"
    assert (
        1282 < len(result["part i"]["item 4"]) < 1482
    ), "Item 4 length not within expected range"

    # Check Part II content
    assert (
        5422 < len(result["part ii"]["item 1"]) < 5622
    ), "Part II Item 1 length not within expected range"
    assert (
        899 < len(result["part ii"]["item 1a"]) < 1099
    ), "Item 1A length not within expected range"
    assert (
        3153 < len(result["part ii"]["item 2"]) < 3353
    ), "Part II Item 2 length not within expected range"
    assert (
        0 < len(result["part ii"]["item 3"]) < 145
    ), "Part II Item 3 length not within expected range"
    assert (
        0 < len(result["part ii"]["item 4"]) < 147
    ), "Part II Item 4 length not within expected range"
    assert (
        0 < len(result["part ii"]["item 5"]) < 196
    ), "Item 5 length not within expected range"
    assert (
        2811 < len(result["part ii"]["item 6"]) < 3011
    ), "Item 6 length not within expected range"

    # Check other extracted content
    assert (
        11542 < len(result["extracted"]["item 0"]) < 11742
    ), "Item 0 length not within expected range"
    assert (
        526 < len(result["extracted"]["signature"]) < 726
    ), "Signature length not within expected range"
    check_item_result(result)

    accession_number = "0000050863-25-000109"
    filing = get_by_accession_number(accession_number)
    result = ParsedHtml10Q().extract_html(
        filing.html(), TenQ.structure, markdown=True
    )

    # Check Part I content
    assert (
        114018 < len(result["part i"]["item 1"]) < 114418
    ), "Item 1 length not within expected range"
    assert (
        22487 < len(result["part i"]["item 2"]) < 22887
    ), "Item 2 length not within expected range"
    assert (
        894 < len(result["part i"]["item 3"]) < 1294
    ), "Item 3 length not within expected range"
    assert (
        2031 < len(result["part i"]["item 4"]) < 2431
    ), "Item 4 length not within expected range"

    # Check Part II content
    assert (
        79542 < len(result["part ii"]["item 1"]) < 79942
    ), "Part II Item 1 length not within expected range"
    assert (
        9589 < len(result["part ii"]["item 1a"]) < 9989
    ), "Item 1A length not within expected range"
    assert (
        697 < len(result["part ii"]["item 2"]) < 1097
    ), "Part II Item 2 length not within expected range"
    assert (
        2737 < len(result["part ii"]["item 3"]) < 3137
    ), "Part II Item 3 length not within expected range"
    assert (
        2737 < len(result["part ii"]["item 4"]) < 3137
    ), "Part II Item 4 length not within expected range"
    assert (
        2737 < len(result["part ii"]["item 5"]) < 3137
    ), "Item 5 length not within expected range"
    assert (
        7145 < len(result["part ii"]["item 6"]) < 7545
    ), "Item 6 length not within expected range"

    # Check other extracted content
    assert (
        33119 < len(result["extracted"]["item 0"]) < 33519
    ), "Item 0 length not within expected range"
    assert (
        1151 < len(result["extracted"]["signature"]) < 1551
    ), "Signature length not within expected range"
    check_item_result(result)

    accession_number = "0000950170-25-103780"
    filing = get_by_accession_number(accession_number)
    result = ParsedHtml10Q().extract_html(
        filing.html(), TenQ.structure, markdown=True
    )
    # Check Part I content
    assert (
        202325 < len(result["part i"]["item 1"]) < 202725
    ), "Item 1 length not within expected range"
    assert (
        179455 < len(result["part i"]["item 2"]) < 179855
    ), "Item 2 length not within expected range"
    assert (
        5254 < len(result["part i"]["item 3"]) < 5654
    ), "Item 3 length not within expected range"
    assert (
        655 < len(result["part i"]["item 4"]) < 1055
    ), "Item 4 length not within expected range"

    # Check Part II content
    assert (
        33 < len(result["part ii"]["item 1"]) < 433
    ), "Part II Item 1 length not within expected range"
    assert (
        2337 < len(result["part ii"]["item 1a"]) < 2737
    ), "Item 1A length not within expected range"
    assert (
        179455 < len(result["part ii"]["item 2"]) < 179855
    ), "Part II Item 2 length not within expected range"
    assert (
        3906 < len(result["part ii"]["item 6"]) < 4306
    ), "Item 6 length not within expected range"

    # Check other extracted content
    assert (
        4576 < len(result["extracted"]["item 0"]) < 4976
    ), "Item 0 length not within expected range"
    assert (
        581 < len(result["extracted"]["signature"]) < 981
    ), "Signature length not within expected range"
    check_item_result(result)
    accession_number = "0000915389-25-000155"
    filing = get_by_accession_number(accession_number)
    result = ParsedHtml10Q().extract_html(
        filing.html(), TenQ.structure, markdown=True
    )
    # Check Part I content
    assert (
        121256 < len(result["part i"]["item 1"]) < 121856
    ), "Item 1 length not within expected range"
    assert (
        64012 < len(result["part i"]["item 2"]) < 64612
    ), "Item 2 length not within expected range"
    assert (
        2420 < len(result["part i"]["item 3"]) < 3020
    ), "Item 3 length not within expected range"
    assert (
        1815 < len(result["part i"]["item 4"]) < 2415
    ), "Item 4 length not within expected range"

    # Check Part II content
    assert (
        1948 < len(result["part ii"]["item 1"]) < 2548
    ), "Part II Item 1 length not within expected range"
    assert (
        0 < len(result["part ii"]["item 1a"]) < 590
    ), "Item 1A length not within expected range"
    assert (
        2113 < len(result["part ii"]["item 2"]) < 2713
    ), "Part II Item 2 length not within expected range"
    assert (
        977 < len(result["part ii"]["item 5"]) < 1577
    ), "Item 5 length not within expected range"
    assert (
        3999 < len(result["part ii"]["item 6"]) < 4599
    ), "Item 6 length not within expected range"

    # Check other extracted content
    assert (
        58307 < len(result["extracted"]["item 0"]) < 58907
    ), "Item 0 length not within expected range"
    assert (
        301 < len(result["extracted"]["signature"]) < 901
    ), "Signature length not within expected range"
    check_item_result(result)


if __name__ == "__main__":
    # Test both 10-K and 10-Q processing
    from edgar import set_identity
    from edgar.company_reports import TenQ, TenK

    set_identity("1334307073@qq.com")

    # Test 10-K processing
    print("Testing 10-K processing...")
    accession_number = "0000827052-25-000074"

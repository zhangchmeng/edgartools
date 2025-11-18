import re
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from edgar.core import pandas_version
from edgar.files.htmltools import (
    detect_signature,
    # detect_part,
    detect_int_items,
    detect_decimal_items,
    detect_table_of_contents,
    adjust_detected_items,
    adjust_for_empty_items,
)

__all__ = [
    "TextElement",
    "chunks2df_text",
    "chunk_text",
    "ChunkedDocumentText",
    "decimal_chunk_fn_text",
    "get_signature_and_following",
]


@dataclass
class TextElement:
    id: str
    type: str
    element: Any
    summary: Optional[str] = None
    text: Optional[str] = None


@lru_cache(maxsize=8)
def chunk_text(text: str) -> List[str]:
    """Split filing text into lines and keep order."""
    # Preserve empty lines only when meaningful; here we drop pure whitespace
    return [line for line in text.splitlines() if line.strip() != ""]

def detect_part(text: pd.Series) -> pd.Series:
    """
    Detect and extract 'Part' sections such as 'PART I', 'Part II', etc., from the given text Series.

    Handles various formats found in SEC filings, including:
        - 'PART I. Financial Information'
        - 'Part II'
        - 'PART III — Executive Overview'
        - 'This section is PART IV'

    Returns:
        pd.Series: A series containing the extracted 'Part X' values (uppercase), or NaN if not found.
    """
    # Match patterns like 'PART I', 'Part II', 'PART III.', etc.
    part_pattern = r'^\b(PART\s+[IVXLC]+)\b'
    # Extract using case-insensitive matching and convert result to uppercase
    extracted = text.str.lstrip().str.extract(part_pattern, flags=re.IGNORECASE | re.MULTILINE, expand=False)
    # Normalize to uppercase for consistency (e.g., 'Part I' → 'PART I')
    return extracted.str.upper().str.replace(r'\s+', ' ', regex=True)

def chunks2df_text(
    chunks: List[str],
    item_detector: Callable[[pd.Series], pd.Series] = detect_int_items,
    item_adjuster: Optional[Callable[[pd.DataFrame, Dict[str, Any]], pd.DataFrame]] = adjust_detected_items,
    item_structure: Optional[Any] = None,
) -> pd.DataFrame:
    """Convert plain-text chunks (lines) to a dataframe similar to htmltools.chunks2df."""
    chunk_df = (
        pd.DataFrame([{"Text": line, "Table": False} for line in chunks])
        .assign(
            Chars=lambda df: df.Text.apply(len),
            Signature=lambda df: df.Text.str.strip().apply(detect_signature).fillna(""),
            TocLink=lambda df: df.Text.str.match(
                "^Table of Contents$", flags=re.IGNORECASE | re.MULTILINE
            ),
            Toc=lambda df: df.Text.head(100).apply(detect_table_of_contents),
            Empty=lambda df: df.Text.str.contains("^$", na=True),
            Part=lambda df: detect_part(df.Text),
            Item=lambda df: item_detector(df.Text),
        )
    )

    # chunk_df[(chunk_df.Part.notnull())]
    # chunk_df[(chunk_df.Item.notnull())|(chunk_df.Part.notnull())]
    # Preserve initially detected items for adjusters
    # Clean newline-induced false Items
    chunk_df.loc[chunk_df.Item.str.contains("\n", na=False), "Item"] = np.nan
    raw_items = chunk_df.Item.copy()

    # 定向填充：
    has_part = chunk_df.Part.fillna("").astype(str).str.strip() != ""
    if pandas_version >= (2, 1, 0):
        item_ffill = chunk_df.Item.ffill()
        item_bfill = chunk_df.Item.bfill()
        chunk_df.Item = item_ffill.where(~has_part, item_bfill).infer_objects(copy=False)
        chunk_df.Part = chunk_df.Part.ffill().infer_objects(copy=False)
    else:
        item_ffill = chunk_df.Item.fillna(method="ffill")
        item_bfill = chunk_df.Item.fillna(method="bfill")
        chunk_df.Item = item_ffill.where(~has_part, item_bfill)
        chunk_df.Part = chunk_df.Part.fillna(method="ffill")

    # Handle signature: blank out Item from signature to next valid item
    signature_rows = chunk_df[chunk_df.Signature]
    if len(signature_rows) > 0:
        signature_loc = signature_rows.index[-1]
        try:
            next_valid_idx = raw_items.loc[signature_loc + 1 :].dropna().index[0]
        except Exception:
            next_valid_idx = None
        if next_valid_idx is not None:
            mask = (chunk_df.index >= signature_loc) & (chunk_df.index < next_valid_idx)
        else:
            mask = chunk_df.index >= signature_loc
        chunk_df.loc[mask, "Item"] = np.nan
        chunk_df.loc[mask, "Signature"] = True

    # Normalize display values
    chunk_df.Item = chunk_df.Item.fillna("").str.title()
    chunk_df.Part = chunk_df.Part.fillna("").str.title()

    # Normalize spaces
    chunk_df.Item = chunk_df.Item.apply(lambda item: re.sub(r"\s+", " ", item))
    chunk_df.Part = chunk_df.Part.apply(lambda part: re.sub(r"\s+", " ", part).strip())
    # Keep Part lower-cased as in htmltools for grouping consistency
    chunk_df.Part = chunk_df.Part.str.lower()

    # Final columns mirroring htmltools
    chunk_df = chunk_df[[
        "Text", "Table", "Chars", "Signature", "TocLink", "Toc", "Empty", "Part", "Item"
    ]]
    # chunk_df[(chunk_df.Item == "Item 3") & (chunk_df.Part.notnull())]
    return chunk_df


# Decimal items (like 8-K items e.g., 5.02) use a different detector and adjuster
decimal_chunk_fn_text = partial(
    chunks2df_text,
    item_detector=detect_decimal_items,
    item_adjuster=adjust_for_empty_items,
)


class ChunkedDocumentText:
    """Chunked plain-text filing, aligned with htmltools.ChunkedDocument interface."""

    def __init__(self,
                 text: str,
                 chunk_fn: Callable[[List[str]], pd.DataFrame] = chunks2df_text,
                 prefix_src: str = ""):
        """
        :param text: Filing text (already extracted string)
        :param chunk_fn: Function converting chunks to dataframe
        :param prefix_src: Optional source prefix
        """
        self.chunks: List[str] = chunk_text(text)
        self._chunked_data: pd.DataFrame = chunk_fn(self.chunks)
        self.chunk_fn = chunk_fn
        self.prefix_src = prefix_src
        self.document_id_parse: Dict = {}

    @lru_cache(maxsize=4)
    def as_dataframe(self) -> pd.DataFrame:
        return self.chunk_fn(self.chunks)

    def show_items(self, df_query: str, *columns) -> pd.DataFrame:
        result = self._chunked_data.query(df_query)
        if len(columns) > 0:
            columns = ["Text"] + list(columns)
            result = result.filter(columns)
        return result

    def list_items(self) -> List[str]:
        return [item for item in self._chunked_data.Item.drop_duplicates().tolist() if item]

    @staticmethod
    def clean_part_line(text: str) -> str:
        res = text.rstrip("\n")
        last_line = res.split("\n")[-1]
        if re.match(r"^\b(PART\s+[IVXLC]+)\b", last_line):
            res = res.rstrip(last_line).rstrip()
        return res

    def get_signature(self) -> str:
        sig_index = self._chunked_data[self._chunked_data.Signature].index
        res = "\n".join([self.chunks[idx] for idx in sig_index])
        return self.clean_part_line(res)

    def get_introduction(self) -> str:
        # Before first non-empty Item
        df = self._chunked_data
        try:
            first_item_idx = df[df.Item.notnull() & (df.Item != "")].index[0]
        except Exception:
            first_item_idx = None
        if first_item_idx is None:
            return ""
        res = "\n".join(df.loc[: first_item_idx - 1, "Text"].tolist())
        return self.clean_part_line(res)

    def _chunks_for(self, item_or_part: str, col: str = "Item"):
        chunk_df = self._chunked_data
        item_or_part = item_or_part.replace(".", r"\.")
        pattern = re.compile(rf"^{item_or_part}$", flags=re.IGNORECASE)
        col_mask = chunk_df[col].str.match(pattern)
        toc_mask = ~(~chunk_df.Toc.notnull() & chunk_df.Toc)
        empty_mask = ~chunk_df.Empty
        mask = col_mask & toc_mask & empty_mask
        for i in mask[mask].index:
            yield self.chunks[i]

    def _chunks_mul_for(self, part: str, item: str):
        chunk_df = self._chunked_data
        part = part.replace(".", r"\.")
        item = item.replace(".", r"\.")
        pattern_part = re.compile(rf"^{part}$", flags=re.IGNORECASE)
        pattern_item = re.compile(rf"^{item}$", flags=re.IGNORECASE)

        item_mask = chunk_df["Item"].str.match(pattern_item)
        part_mask = chunk_df["Part"].str.match(pattern_part)
        toc_mask = ~(~chunk_df.Toc.notnull() & chunk_df.Toc)
        empty_mask = ~chunk_df.Empty
        mask = part_mask & item_mask & toc_mask & empty_mask
        for i in mask[mask].index:
            yield self.chunks[i]

    def _chunk_item_split(self, part: str, item: str) -> List[List[str]]:
        chunk_df = self._chunked_data
        part = part.replace(".", r"\.")
        item = item.replace(".", r"\.")
        pattern_part = re.compile(rf"^{part}$", flags=re.IGNORECASE)
        pattern_item = re.compile(rf"^{item}$", flags=re.IGNORECASE)

        item_mask = chunk_df["Item"].str.match(pattern_item)
        part_mask = chunk_df["Part"].str.match(pattern_part)
        toc_mask = ~(~chunk_df.Toc.notnull() & chunk_df.Toc)
        empty_mask = ~chunk_df.Empty
        mask = part_mask & item_mask & toc_mask & empty_mask

        res: List[List[str]] = []
        _res: List[str] = []
        last_index: Optional[int] = None
        for cur_index in mask[mask].index:
            if last_index is None or cur_index == (last_index + 1):
                _res.append(self.chunks[cur_index])
            else:
                res.append(_res)
                _res = []
            last_index = cur_index
        if _res:
            res.append(_res)
        return res

    def part_item_res(self, markdown: bool = True, filter_part: bool = False, split: bool = False) -> Dict[str, Dict[str, Any]]:
        """Generate part->item->content mapping using text chunks.
        The content is a single concatenated string or a list of segments if split=True.
        """
        df = self._chunked_data
        if filter_part:
            filtered_df = df[(df["Part"].notna()) & (df["Item"].notna()) & (df["Part"] != "") & (df["Item"] != "")].copy()
        else:
            filtered_df = df[(df["Item"].notna()) & (df["Item"] != "")].copy()

        result: Dict[str, Dict[str, Any]] = {}
        for part in filtered_df["Part"].dropna().unique():
            part_items = filtered_df[filtered_df["Part"] == part]["Item"].dropna().unique().tolist()
            if not part_items:
                continue
            part_key = part.lower()
            result[part_key] = {}
            if split:
                for item in sorted(part_items):
                    item_key = item.lower()
                    chunks_list = self._chunk_item_split(part, item)
                    if chunks_list:
                        content_list = [self.clean_part_line("\n".join(chunks)) for chunks in chunks_list]
                        result[part_key][item_key] = content_list
                    else:
                        result[part_key][item_key] = [""]
            else:
                for item in sorted(part_items):
                    item_key = item.lower()
                    chunks = list(self._chunks_mul_for(part, item))
                    if chunks:
                        content = self.clean_part_line("\n".join(chunks))
                        result[part_key][item_key] = content
                    else:
                        result[part_key][item_key] = ""
        # filtered_df[(filtered_df.Part=='part i') & (filtered_df.Item=='Item 1')]
        # result['part i']['item 1']
        # # filtered_df[(filtered_df.Part.notnull())]
        # extracted section
        result["extracted"] = {}
        try:
            signature_content = self.get_signature()
            result["extracted"]["signature"] = signature_content if signature_content else ""
        except Exception:
            result["extracted"]["signature"] = ""

        _introduction_content = ""
        if result.get(""):
            if result[""].get(""):
                _introduction_content = result[""].get("")

        try:
            introduction_content = self.get_introduction()
            result["extracted"]["item 0"] = introduction_content or _introduction_content
        except Exception:
            result["extracted"]["item 0"] = _introduction_content

        return result


def get_signature_and_following(chunk_df: pd.DataFrame) -> pd.DataFrame:
    sig_idx = chunk_df.index[chunk_df["Signature"] == True]
    if len(sig_idx) == 0:
        return chunk_df.iloc[0:0]
    start_idx = sig_idx[0] + 1
    return chunk_df.iloc[start_idx:]
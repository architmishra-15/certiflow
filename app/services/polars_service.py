import polars as pl
from io import BytesIO

def process_file(file_bytes: bytes, filename: str, sheet: str = None):
    '''Reads CSV/Excel with Polars and guesses column roles.'''
    if filename.endswith('.csv'):
        df = pl.read_csv(file_bytes)
    elif filename.endswith(('.xlsx', '.xls')):
        df = pl.read_excel(file_bytes, sheet_name=sheet) if sheet else pl.read_excel(file_bytes)
    else:
        raise ValueError("Unsupported format. Use .csv or .xlsx")
        
    columns = []
    for col in df.columns:
        sample = df.select(col).drop_nulls().head(3).to_series().to_list()
        
        role = None
        c_lower = col.lower()
        if "name" in c_lower: role = "name"
        elif "personal email" in c_lower: role = "personal_email"
        elif "email" in c_lower: role = "email"
        elif "id" in c_lower or "roll" in c_lower: role = "student_id"
        elif "event" in c_lower: role = "event"
            
        columns.append({
            "name": col,
            "sample": [str(x) for x in sample],
            "guessed_role": role
        })
    return columns, df.height, df

def validate_batch(df: pl.DataFrame, config: dict):
    '''High-performance validation using Polars.'''
    report = {
        "total": df.height,
        "valid": 0, "invalid": 0, "duplicates": 0,
        "issues_by_type": {}, "sample_issues": []
    }
    
    # Track invalid masks
    invalid_mask = pl.Series([False] * df.height)
    
    req_cols = config.get("required_columns", [])
    for col in req_cols:
        if col in df.columns:
            missing = df[col].is_null()
            invalid_mask = invalid_mask | missing
            
    # Deduplication
    dedupe_cols = config.get("dedupe_columns", [])
    if dedupe_cols and all(c in df.columns for c in dedupe_cols):
        dup_mask = df.select(dedupe_cols).is_duplicated()
        report["duplicates"] = dup_mask.sum()
        invalid_mask = invalid_mask | dup_mask
        
    report["invalid"] = invalid_mask.sum()
    report["valid"] = df.height - report["invalid"]
    report["expected_count"] = report["valid"]
    
    return report
